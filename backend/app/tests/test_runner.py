"""runner + backtest 单测."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import runtime
from app.db import Base, Strategy, StrategyRun
from app.exchanges.precision import registry as precision_registry
from app.strategies import impls  # noqa: F401  触发注册
from app.strategies import runner
from app.strategies.backtest import run_backtest
from app.strategies.events import KlineEvent
from app.strategies.registry import registry as strategy_registry


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _setup_precision():
    precision_registry.clear()
    precision_registry.load(
        {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "1"},
                    ],
                }
            ]
        }
    )
    yield
    precision_registry.clear()


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal_test = async_sessionmaker(engine, expire_on_commit=False)

    # Monkey-patch runner 内引用的 SessionLocal 到本测试 session，避免 heartbeat 用到真的文件 DB
    import app.strategies.runner as runner_mod

    original = runner_mod.SessionLocal
    runner_mod.SessionLocal = SessionLocal_test  # type: ignore[assignment]

    async with SessionLocal_test() as s:
        yield s

    runner_mod.SessionLocal = original  # type: ignore[assignment]
    await engine.dispose()


# ------------------------------------------------------------------
# runner.start_strategy：强校验 SL
# ------------------------------------------------------------------


async def _make_strategy_row(
    s: AsyncSession,
    *,
    type_key: str = "grid",
    stoploss: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    symbols: list[str] | None = None,
) -> int:
    row = Strategy(
        user_id=1,
        name="t",
        type=type_key,
        params_json=json.dumps(
            params
            or {
                "symbol": "BTCUSDT",
                "upper": "10500",
                "lower": "9500",
                "grids": 3,
                "qty_per_grid": "0.001",
                "sl_per_ladder": False,
            }
        ),
        stoploss_json=json.dumps(stoploss) if stoploss is not None else "null",
        symbols_json=json.dumps(symbols or ["BTCUSDT"]),
    )
    s.add(row)
    await s.commit()
    await s.refresh(row)
    return row.id


async def test_start_rejects_null_stoploss(db_session):
    sid = await _make_strategy_row(db_session, stoploss=None)
    with pytest.raises(ValueError, match="必须配置止损"):
        await runner.start_strategy(sid, db_session)


async def test_start_rejects_invalid_stoploss_mode(db_session):
    sid = await _make_strategy_row(db_session, stoploss={"mode": "magic"})
    with pytest.raises(ValueError):
        await runner.start_strategy(sid, db_session)


async def test_start_rejects_percent_missing_pct(db_session):
    sid = await _make_strategy_row(db_session, stoploss={"mode": "percent"})
    with pytest.raises(ValueError, match="pct"):
        await runner.start_strategy(sid, db_session)


async def test_start_rejects_unknown_type(db_session):
    sid = await _make_strategy_row(
        db_session, type_key="nonexistent", stoploss={"mode": "percent", "pct": "0.01"}
    )
    with pytest.raises(ValueError, match="未知策略类型"):
        await runner.start_strategy(sid, db_session)


# ------------------------------------------------------------------
# backtest
# ------------------------------------------------------------------


def _make_klines_flat(n: int = 30, price: Decimal = Decimal("10000")) -> list[KlineEvent]:
    """生成平坦 K 线序列（测试不会触发信号）."""
    return [
        KlineEvent(
            symbol="BTCUSDT",
            interval="15m",
            open_time_ms=i * 60_000,
            close_time_ms=i * 60_000 + 59_999,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("1"),
            closed=True,
        )
        for i in range(n)
    ]


def _make_klines_trending(n: int = 60, start: Decimal = Decimal("10000")) -> list[KlineEvent]:
    """先跌 n/3 根再涨 2n/3 根的 K 线，用于触发金叉信号."""
    out: list[KlineEvent] = []
    p = start
    down_n = n // 3
    for i in range(n):
        delta = Decimal("-10") if i < down_n else Decimal("10")
        p_next = p + delta
        out.append(
            KlineEvent(
                symbol="BTCUSDT",
                interval="15m",
                open_time_ms=i * 900_000,
                close_time_ms=i * 900_000 + 899_999,
                open=p,
                high=max(p, p_next) + Decimal("1"),
                low=min(p, p_next) - Decimal("1"),
                close=p_next,
                volume=Decimal("1"),
                closed=True,
            )
        )
        p = p_next
    return out


async def test_backtest_rejects_bad_stoploss():
    klines = _make_klines_flat(10)
    with pytest.raises(ValueError):
        await run_backtest(
            strategy_type="ma_cross",
            params={"symbol": "BTCUSDT", "qty_usdt": "100"},
            stoploss={"mode": "magic"},
            symbols=["BTCUSDT"],
            klines=klines,
        )


async def test_backtest_ma_cross_flat_no_trades():
    klines = _make_klines_flat(60)
    report = await run_backtest(
        strategy_type="ma_cross",
        params={
            "symbol": "BTCUSDT",
            "interval": "15m",
            "fast_period": 5,
            "slow_period": 10,
            "qty_usdt": "100",
            "atr_period": 14,
        },
        stoploss={"mode": "percent", "pct": "0.02"},
        symbols=["BTCUSDT"],
        klines=klines,
    )
    # 平坦序列不应触发交易
    assert report.total_fills == 0
    assert report.final_balance == Decimal("10000")


async def test_backtest_ma_cross_trending_triggers_trade():
    klines = _make_klines_trending(60)
    report = await run_backtest(
        strategy_type="ma_cross",
        params={
            "symbol": "BTCUSDT",
            "interval": "15m",
            "fast_period": 3,
            "slow_period": 8,
            "qty_usdt": "100",
            "atr_period": 5,
        },
        stoploss={"mode": "percent", "pct": "0.02"},
        symbols=["BTCUSDT"],
        klines=klines,
    )
    # 趋势上升 + 快慢线会金叉 → 至少 1 次成交
    assert report.total_fills >= 1


# ------------------------------------------------------------------
# dispatch_order
# ------------------------------------------------------------------


async def test_dispatch_order_routes_to_matching_run():
    """用 inject_strategy 注入一个假策略，测试 dispatch_order 能路由到它的 queue."""

    from app.strategies.base import BaseStrategy
    from app.strategies.context import StrategyContext
    from app.strategies.events import OrderEvent as _OE
    from app.strategies.stoploss import StopLossConfig

    events_seen: list[_OE] = []

    class Probe(BaseStrategy):
        type_key = "probe-test"  # 不在全局 registry
        display_name = "probe"

        async def on_order_update(self, o):  # type: ignore[override]
            events_seen.append(o)

        @classmethod
        def params_schema(cls):
            return {}

        @classmethod
        def stoploss_schema(cls):
            return {}

    ctx = StrategyContext(
        run_id=-1,
        strategy_name="probe",
        strategy_type="probe-test",
        symbols=["BTCUSDT"],
        params={},
        stoploss=StopLossConfig(mode="percent", pct=Decimal("0.01")),
        client=None,
    )
    strat = Probe(ctx)
    rid = runner.inject_strategy(strat)
    try:
        evt = _OE(
            symbol="BTCUSDT",
            client_order_id="x",
            exchange_order_id="1",
            side="BUY",
            status="NEW",
            exec_type="NEW",
            last_price=Decimal("0"),
            last_qty=Decimal("0"),
            cum_qty=Decimal("0"),
            orig_qty=Decimal("0"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        runner.dispatch_order(evt)
        # 让 worker 跑一小段时间
        await asyncio.sleep(0.1)
        assert len(events_seen) == 1
        assert events_seen[0].client_order_id == "x"

        # 不相关的 symbol 不应投递
        evt2 = _OE(
            symbol="ETHUSDT",
            client_order_id="y",
            exchange_order_id="2",
            side="BUY",
            status="NEW",
            exec_type="NEW",
            last_price=Decimal("0"),
            last_qty=Decimal("0"),
            cum_qty=Decimal("0"),
            orig_qty=Decimal("0"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        runner.dispatch_order(evt2)
        await asyncio.sleep(0.1)
        assert len(events_seen) == 1
    finally:
        await runner.eject_strategy(rid)
