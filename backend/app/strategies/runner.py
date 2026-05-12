"""策略运行时管理器.

职责：
  - 启动（强校验 SL）/ 停止 策略 asyncio task
  - 10 秒心跳写 DB.strategy_runs.last_heartbeat
  - 捕获 task 异常，标记 status=error + 写 last_error，不影响其它 task
  - 分发 tick / kline / order 事件到各运行中的策略（从 runtime.binance_ws 的回调中来）

本模块 **不直接** 订阅 WS；而是提供对外的事件注入接口 ``dispatch_*``。
main.py 在 WS 客户端回调里把消息翻译成 :mod:`events` 对象再投递进来。

事件进入流程：WS callback → StrategyRunner.dispatch_* → 按 symbol 找到相关 run →
ctx.queue.put_nowait(event) → 该 run 的 worker 协程 await queue.get() → strategy.on_xxx.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime
from ..db import SessionLocal, Strategy, StrategyRun
from .base import BaseStrategy
from .context import StrategyContext
from .events import KlineEvent, OrderEvent, TickEvent
from .registry import registry as strategy_registry
from .stoploss import StopLossConfig, require_valid_stoploss

log = logging.getLogger("strategy.runner")


HEARTBEAT_INTERVAL_SEC = 10
STALE_THRESHOLD_SEC = 30


# ==================================================================
# 运行中实例容器
# ==================================================================


class RunningInstance:
    """对应一个 strategy_run 的内存活体."""

    def __init__(
        self,
        run_id: int,
        strategy: BaseStrategy,
        ctx: StrategyContext,
        worker_task: asyncio.Task,
        heartbeat_task: asyncio.Task,
    ) -> None:
        self.run_id = run_id
        self.strategy = strategy
        self.ctx = ctx
        self.worker_task = worker_task
        self.heartbeat_task = heartbeat_task


# 全局运行表
_running: dict[int, RunningInstance] = {}


def running_count() -> int:
    return len(_running)


def is_running(run_id: int) -> bool:
    return run_id in _running


def get_instance(run_id: int) -> RunningInstance | None:
    return _running.get(run_id)


def all_running() -> list[RunningInstance]:
    return list(_running.values())


# ==================================================================
# 启动 / 停止
# ==================================================================


async def start_strategy(strategy_id: int, session: AsyncSession) -> StrategyRun:
    """启动一个策略。过程：
      1. 读 Strategy 定义
      2. **强校验 stoploss_json** → 拒绝无/非法 SL
      3. 校验 type_key 已注册
      4. 创建 StrategyRun 行
      5. 构造 StrategyContext + 策略实例
      6. 起 worker 协程（消费 ctx.queue）+ 心跳协程
    """
    strat = (
        await session.execute(select(Strategy).where(Strategy.id == strategy_id))
    ).scalar_one_or_none()
    if strat is None:
        raise ValueError(f"Strategy id={strategy_id} 不存在")

    # --- 强制 SL 校验（本版核心）---
    try:
        stoploss_raw = json.loads(strat.stoploss_json or "null")
    except json.JSONDecodeError as e:
        raise ValueError(f"stoploss_json 非合法 JSON：{e}") from e
    sl_cfg: StopLossConfig = require_valid_stoploss(stoploss_raw)

    # --- 策略类型 ---
    if not strategy_registry.has(strat.type):
        raise ValueError(f"未知策略类型：{strat.type}")
    strat_cls = strategy_registry.get(strat.type)

    # --- 参数 / symbols ---
    try:
        params = json.loads(strat.params_json or "{}")
        symbols = json.loads(strat.symbols_json or "[]")
    except json.JSONDecodeError as e:
        raise ValueError(f"params_json/symbols_json 解析失败：{e}") from e
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("symbols 至少一个")

    # --- 创建 Run 行 ---
    run = StrategyRun(
        strategy_id=strat.id,
        status="running",
        params_json=strat.params_json,
        stoploss_json=strat.stoploss_json,
        symbols_json=strat.symbols_json,
        started_at=datetime.now(timezone.utc),
        last_heartbeat=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    run.task_name = f"strategy-{strat.type}-{run.id}"
    await session.commit()

    # --- ctx + instance ---
    ctx = StrategyContext(
        run_id=run.id,
        strategy_name=strat.name,
        strategy_type=strat.type,
        symbols=[s.upper() for s in symbols],
        params=params,
        stoploss=sl_cfg,
        client=runtime.binance_client,
    )
    strategy_obj = strat_cls(ctx)

    worker_task = asyncio.create_task(_worker_loop(run.id, strategy_obj), name=run.task_name)
    heartbeat_task = asyncio.create_task(_heartbeat_loop(run.id), name=f"hb-{run.id}")

    _running[run.id] = RunningInstance(run.id, strategy_obj, ctx, worker_task, heartbeat_task)

    # 让策略先自启
    try:
        await strategy_obj.on_start()
    except Exception as e:  # noqa: BLE001
        log.exception("on_start failed: %s", e)
        await _mark_error(run.id, f"on_start: {e}")

    # 若策略参数有 interval 字段（K 线驱动型），动态订阅 kline 流
    iv = params.get("interval") if isinstance(params, dict) else None
    if iv and runtime.binance_ws is not None:
        for sym in ctx.symbols:
            try:
                await runtime.binance_ws.ensure_kline(sym, str(iv))
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_kline(%s,%s) failed: %s", sym, iv, e)

    return run


async def stop_strategy(run_id: int, session: AsyncSession) -> None:
    inst = _running.pop(run_id, None)
    if inst is None:
        # 可能已经在 DB 里但没活体，直接 DB 标记
        row = (
            await session.execute(select(StrategyRun).where(StrategyRun.id == run_id))
        ).scalar_one_or_none()
        if row is not None and row.status in {"running", "error", "stale"}:
            row.status = "stopped"
            row.stopped_at = datetime.now(timezone.utc)
            await session.commit()
        return

    # 触发退出
    inst.ctx.stop_event.set()
    for t in (inst.worker_task, inst.heartbeat_task):
        if not t.done():
            t.cancel()
    for t in (inst.worker_task, inst.heartbeat_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    # 走 on_stop 清理（撤单等）
    try:
        await inst.strategy.on_stop()
    except Exception as e:  # noqa: BLE001
        log.warning("on_stop error: %s", e)

    # DB 收尾
    row = (
        await session.execute(select(StrategyRun).where(StrategyRun.id == run_id))
    ).scalar_one_or_none()
    if row is not None:
        row.status = "stopped"
        row.stopped_at = datetime.now(timezone.utc)
        row.stats_json = json.dumps(inst.ctx.stats.to_dict(), ensure_ascii=False)
        await session.commit()


async def stop_all() -> None:
    """进程退出时调用（main.py lifespan shutdown）."""
    ids = list(_running.keys())
    for rid in ids:
        inst = _running.pop(rid, None)
        if inst is None:
            continue
        inst.ctx.stop_event.set()
        for t in (inst.worker_task, inst.heartbeat_task):
            if not t.done():
                t.cancel()
        for t in (inst.worker_task, inst.heartbeat_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


# ==================================================================
# worker + heartbeat
# ==================================================================


async def _worker_loop(run_id: int, strategy: BaseStrategy) -> None:
    """从 ctx.queue 拉事件，分发到 on_xxx.

    任何异常只影响自己；捕获并标记 status=error。
    """
    ctx = strategy.ctx
    try:
        while not ctx.stop_event.is_set():
            try:
                event = await asyncio.wait_for(ctx.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                if isinstance(event, TickEvent):
                    await strategy.on_tick(event)
                elif isinstance(event, KlineEvent):
                    await strategy.on_kline(event)
                elif isinstance(event, OrderEvent):
                    await strategy.on_order_update(event)
                else:
                    log.warning("[run %d] unknown event: %r", run_id, type(event))
            except Exception as e:  # noqa: BLE001
                # 单个事件出错不退出 worker，只记
                log.exception("[run %d] handler error: %s", run_id, e)
                ctx.stats.last_error = f"handler: {e}"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("[run %d] worker crashed: %s", run_id, e)
        await _mark_error(run_id, str(e))


async def _heartbeat_loop(run_id: int) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
            async with SessionLocal() as s:
                row = (
                    await s.execute(select(StrategyRun).where(StrategyRun.id == run_id))
                ).scalar_one_or_none()
                if row is None or row.status in {"stopped", "error"}:
                    return
                row.last_heartbeat = datetime.now(timezone.utc)
                inst = _running.get(run_id)
                if inst is not None:
                    row.stats_json = json.dumps(inst.ctx.stats.to_dict(), ensure_ascii=False)
                await s.commit()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("[run %d] heartbeat error: %s", run_id, e)


async def _mark_error(run_id: int, msg: str) -> None:
    try:
        async with SessionLocal() as s:
            row = (
                await s.execute(select(StrategyRun).where(StrategyRun.id == run_id))
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = "error"
            row.last_error = msg[:2000]
            row.stopped_at = datetime.now(timezone.utc)
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


# ==================================================================
# 事件分发（由 main.py WS 回调调用）
# ==================================================================


def dispatch_tick(event: TickEvent) -> None:
    for inst in _running.values():
        if event.symbol in inst.ctx.symbols:
            try:
                inst.ctx.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("[run %d] tick queue full", inst.run_id)


def dispatch_kline(event: KlineEvent) -> None:
    for inst in _running.values():
        if event.symbol in inst.ctx.symbols:
            try:
                inst.ctx.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("[run %d] kline queue full", inst.run_id)


def dispatch_order(event: OrderEvent) -> None:
    for inst in _running.values():
        # 策略可能对所有订单事件感兴趣（即便 symbol 不在当前持有列表）
        if event.symbol in inst.ctx.symbols:
            try:
                inst.ctx.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("[run %d] order queue full", inst.run_id)


# ==================================================================
# 状态工具
# ==================================================================


def mark_stale_runs_sync_from_db(row: StrategyRun) -> str:
    """给路由层判定某条 run 是否 stale 用."""
    if row.status != "running":
        return row.status
    if row.last_heartbeat is None:
        return "stale"
    age = (datetime.now(timezone.utc) - row.last_heartbeat).total_seconds()
    if age > STALE_THRESHOLD_SEC:
        return "stale"
    return "running"


def inject_strategy(strategy: BaseStrategy) -> int:
    """单测用：把一个已构造的 strategy 实例注入到运行表，不做 DB 写入.

    返回伪造的 run_id.
    """
    run_id = -1 * (len(_running) + 1)
    worker = asyncio.create_task(_worker_loop(run_id, strategy), name=f"t-{run_id}")
    # 心跳在测试里没 DB，给一个永不执行的空 task
    heartbeat = asyncio.create_task(asyncio.sleep(3600), name=f"hb-{run_id}")
    _running[run_id] = RunningInstance(run_id, strategy, strategy.ctx, worker, heartbeat)
    return run_id


async def eject_strategy(run_id: int) -> None:
    """单测用：从运行表移除并 cancel."""
    inst = _running.pop(run_id, None)
    if inst is None:
        return
    inst.ctx.stop_event.set()
    inst.worker_task.cancel()
    inst.heartbeat_task.cancel()
    for t in (inst.worker_task, inst.heartbeat_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
