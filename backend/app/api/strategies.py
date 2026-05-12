"""策略相关 HTTP 路由.

覆盖：
  GET  /strategies/types              列出已注册策略类型 + params_schema + stoploss_schema
  GET  /strategies                    CRUD：列表
  POST /strategies                    创建（★ 强校验 stoploss）
  GET  /strategies/{id}
  PUT  /strategies/{id}
  DELETE /strategies/{id}
  POST /strategies/{id}/start         启动一个 run（再次强校验 stoploss + 要求已注册的 type）
  POST /strategies/{id}/runs/{rid}/stop
  GET  /strategies/{id}/runs          该策略的历史/当前 run 列表
  GET  /strategies/runs               所有运行中的 run（跨策略）
  POST /strategies/backtest           临时回测（无需先建 Strategy 行）
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from ..db import Strategy, StrategyRun
from ..strategies import runner
from ..strategies.backtest import fetch_klines_range, run_backtest
from ..strategies.events import KlineEvent
from ..strategies.registry import registry as strategy_registry
from ..strategies.stoploss import require_valid_stoploss
from .deps import Binance, DbSession, ok

router = APIRouter(prefix="/strategies", tags=["strategies"])


# ==================================================================
# Schemas
# ==================================================================


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str
    params: dict[str, Any]
    stoploss: dict[str, Any]
    symbols: list[str] = Field(min_length=1)
    is_template: bool = False


class StrategyUpdate(BaseModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    stoploss: dict[str, Any] | None = None
    symbols: list[str] | None = None
    is_template: bool | None = None


class BacktestIn(BaseModel):
    strategy_type: str
    params: dict[str, Any]
    stoploss: dict[str, Any]
    symbols: list[str] = Field(min_length=1)
    interval: Literal[
        "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"
    ] = "15m"
    start_ms: int
    end_ms: int
    initial_balance: str = "10000"


# ==================================================================
# helpers
# ==================================================================


def _strategy_to_dict(s: Strategy, run_summary: dict | None = None) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "type": s.type,
        "params": json.loads(s.params_json or "{}"),
        "stoploss": json.loads(s.stoploss_json or "{}"),
        "symbols": json.loads(s.symbols_json or "[]"),
        "is_template": s.is_template,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "running_count": (run_summary or {}).get("running_count", 0),
    }


def _run_to_dict(r: StrategyRun) -> dict:
    eff_status = runner.mark_stale_runs_sync_from_db(r)
    try:
        stats = json.loads(r.stats_json or "{}")
    except json.JSONDecodeError:
        stats = {}
    return {
        "id": r.id,
        "strategy_id": r.strategy_id,
        "status": eff_status,
        "raw_status": r.status,
        "task_name": r.task_name,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
        "last_heartbeat": r.last_heartbeat.isoformat() if r.last_heartbeat else None,
        "last_error": r.last_error,
        "stats": stats,
    }


def _ensure_type_registered(type_key: str) -> None:
    if not strategy_registry.has(type_key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"未注册的策略类型：{type_key}。已注册：{[t['type_key'] for t in strategy_registry.all_types()]}",
        )


# ==================================================================
# /strategies/types
# ==================================================================


@router.get("/types")
async def list_types() -> dict:
    return ok(strategy_registry.all_types())


# ==================================================================
# 运行实例（全局列表）
# ==================================================================


@router.get("/runs")
async def list_all_runs(s: DbSession, limit: int = 100) -> dict:
    stmt = select(StrategyRun).order_by(desc(StrategyRun.id)).limit(limit)
    rows = (await s.execute(stmt)).scalars().all()
    return ok([_run_to_dict(r) for r in rows])


# ==================================================================
# CRUD
# ==================================================================


@router.get("")
async def list_strategies(s: DbSession) -> dict:
    rows = (await s.execute(select(Strategy).order_by(Strategy.id))).scalars().all()
    # 统计每个 strategy 当前 running 实例数
    running_map: dict[int, int] = {}
    for inst in runner.all_running():
        # inst.ctx 无 strategy_id；但 run_id 可反查——为了简单，从 DB 取一次
        pass  # 跳过精确计数；running_count 用 DB 快照
    stmt2 = select(StrategyRun).where(StrategyRun.status == "running")
    runs = (await s.execute(stmt2)).scalars().all()
    for r in runs:
        running_map[r.strategy_id] = running_map.get(r.strategy_id, 0) + 1
    return ok(
        [_strategy_to_dict(row, {"running_count": running_map.get(row.id, 0)}) for row in rows]
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_strategy(payload: StrategyIn, s: DbSession) -> dict:
    _ensure_type_registered(payload.type)

    # stoploss 强校验（失败即 400）
    try:
        require_valid_stoploss(payload.stoploss)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    row = Strategy(
        user_id=1,
        name=payload.name,
        type=payload.type,
        params_json=json.dumps(payload.params, ensure_ascii=False),
        stoploss_json=json.dumps(payload.stoploss, ensure_ascii=False),
        symbols_json=json.dumps([x.upper() for x in payload.symbols], ensure_ascii=False),
        is_template=payload.is_template,
    )
    s.add(row)
    await s.commit()
    await s.refresh(row)
    return ok(_strategy_to_dict(row))


@router.get("/{sid}")
async def get_strategy(sid: int, s: DbSession) -> dict:
    row = (await s.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")
    return ok(_strategy_to_dict(row))


@router.put("/{sid}")
async def update_strategy(sid: int, payload: StrategyUpdate, s: DbSession) -> dict:
    row = (await s.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")

    if payload.name is not None:
        row.name = payload.name
    if payload.params is not None:
        row.params_json = json.dumps(payload.params, ensure_ascii=False)
    if payload.stoploss is not None:
        try:
            require_valid_stoploss(payload.stoploss)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        row.stoploss_json = json.dumps(payload.stoploss, ensure_ascii=False)
    if payload.symbols is not None:
        if not payload.symbols:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="symbols 至少一个")
        row.symbols_json = json.dumps([x.upper() for x in payload.symbols], ensure_ascii=False)
    if payload.is_template is not None:
        row.is_template = payload.is_template
    await s.commit()
    await s.refresh(row)
    return ok(_strategy_to_dict(row))


@router.delete("/{sid}")
async def delete_strategy(sid: int, s: DbSession) -> dict:
    row = (await s.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")

    # 在删除前先停掉正在跑的 runs
    runs = (
        await s.execute(
            select(StrategyRun).where(
                StrategyRun.strategy_id == sid,
                StrategyRun.status == "running",
            )
        )
    ).scalars().all()
    for r in runs:
        try:
            await runner.stop_strategy(r.id, s)
        except Exception:  # noqa: BLE001
            pass

    await s.delete(row)
    await s.commit()
    return ok({"deleted": True, "id": sid})


# ==================================================================
# start / stop / runs
# ==================================================================


@router.post("/{sid}/start")
async def start_strategy_view(sid: int, s: DbSession, _client: Binance) -> dict:
    """启动策略。依赖 Binance 登录（runtime.binance_client 已就绪）."""
    try:
        run = await runner.start_strategy(sid, s)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(_run_to_dict(run))


@router.post("/{sid}/runs/{rid}/stop")
async def stop_run_view(sid: int, rid: int, s: DbSession) -> dict:
    run = (
        await s.execute(select(StrategyRun).where(StrategyRun.id == rid, StrategyRun.strategy_id == sid))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run 不存在")
    await runner.stop_strategy(rid, s)
    # 重新读
    run = (await s.execute(select(StrategyRun).where(StrategyRun.id == rid))).scalar_one_or_none()
    return ok(_run_to_dict(run) if run else {"id": rid, "status": "stopped"})


@router.get("/{sid}/runs")
async def list_strategy_runs(sid: int, s: DbSession, limit: int = 50) -> dict:
    stmt = (
        select(StrategyRun)
        .where(StrategyRun.strategy_id == sid)
        .order_by(desc(StrategyRun.id))
        .limit(limit)
    )
    rows = (await s.execute(stmt)).scalars().all()
    return ok([_run_to_dict(r) for r in rows])


# ==================================================================
# backtest
# ==================================================================


@router.post("/backtest")
async def backtest_view(payload: BacktestIn, client: Binance) -> dict:
    _ensure_type_registered(payload.strategy_type)

    # 强校验 SL
    try:
        require_valid_stoploss(payload.stoploss)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if payload.end_ms <= payload.start_ms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="end_ms 必须 > start_ms")

    symbol = payload.symbols[0].upper()
    try:
        klines = await fetch_klines_range(
            client, symbol, payload.interval, payload.start_ms, payload.end_ms
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"拉历史 K 线失败：{e}") from e

    if not klines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="指定区间无 K 线数据")

    try:
        report = await run_backtest(
            strategy_type=payload.strategy_type,
            params=payload.params,
            stoploss=payload.stoploss,
            symbols=payload.symbols,
            klines=klines,
            initial_balance=Decimal(payload.initial_balance),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return ok(report.to_dict())
