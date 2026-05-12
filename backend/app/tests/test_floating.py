"""Floating 浮动挂单单测（mock BinanceClient + in-memory SQLite）."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import runtime
from app.db import Base, DEFAULT_GUARD_RULES, GuardRules
from app.exchanges.precision import PrecisionRegistry, registry
from app.trading.floating import (
    ChaseConfig,
    FloatingOrderRequest,
    FloatingOrderService,
    GuardBlockedError,
    LaddersConfig,
    _ladder_prices,
    _resolve_order_side,
)
from app.trading.guard import GuardService


# ------------------------------------------------------------------
# Fake BinanceClient
# ------------------------------------------------------------------


class _FakeClient:
    def __init__(self, bid: str = "50000", ask: str = "50001") -> None:
        self._bid = bid
        self._ask = ask
        self.placed: list[dict[str, Any]] = []
        self.leverage_calls: list[tuple[str, int]] = []

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        return {"bidPrice": self._bid, "askPrice": self._ask}

    async def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    async def set_leverage(self, symbol: str, lev: int) -> dict[str, Any]:
        self.leverage_calls.append((symbol, lev))
        return {"leverage": lev}

    async def place_order(self, **kwargs) -> dict[str, Any]:
        self.placed.append(kwargs)
        return {
            "orderId": 10000 + len(self.placed),
            "status": "NEW",
            "clientOrderId": kwargs.get("client_order_id"),
        }


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime.tickers.clear()
    runtime.chase_tasks.clear()
    yield
    runtime.tickers.clear()
    runtime.chase_tasks.clear()


@pytest.fixture
def btc_registry() -> PrecisionRegistry:
    # 往全局 registry 注入 BTCUSDT，供 floating 内部 registry.has/get 使用
    registry.clear()
    registry.load(
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
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
            ]
        }
    )
    yield registry
    registry.clear()


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(GuardRules(rules_json=json.dumps(DEFAULT_GUARD_RULES)))
        await s.commit()
        yield s
    await engine.dispose()


# ------------------------------------------------------------------
# 纯逻辑：价格计算 / side 映射
# ------------------------------------------------------------------


class TestResolveSide:
    def test_long_open_is_buy(self):
        assert _resolve_order_side("long", "open") == "BUY"

    def test_long_close_is_sell(self):
        assert _resolve_order_side("long", "close") == "SELL"

    def test_short_open_is_sell(self):
        assert _resolve_order_side("short", "open") == "SELL"

    def test_short_close_is_buy(self):
        assert _resolve_order_side("short", "close") == "BUY"


class TestLadderPrices:
    def test_single_ladder(self, btc_registry):
        req = FloatingOrderRequest(
            symbol="BTCUSDT",
            side="long",
            action="open",
            float_pct=Decimal("-0.001"),  # -0.1%
            qty_mode="usdt",
            qty=Decimal("100"),
        )
        info = btc_registry.get("BTCUSDT")
        prices = _ladder_prices(Decimal("50000"), req, info)
        # -0.1% of 50000 = -50  →  49950；tickSize 0.10 保守向下 = 49950.00
        assert prices == [Decimal("49950.00")]

    def test_multi_ladders_long(self, btc_registry):
        req = FloatingOrderRequest(
            symbol="BTCUSDT",
            side="long",
            action="open",
            float_pct=Decimal("-0.001"),
            qty_mode="usdt",
            qty=Decimal("300"),
            ladders=LaddersConfig(count=3, spacing_pct=Decimal("0.001")),
        )
        info = btc_registry.get("BTCUSDT")
        prices = _ladder_prices(Decimal("50000"), req, info)
        # base = 49950；下一档 -50 = 49900；再下一档 -100 = 49850
        assert prices == [Decimal("49950.00"), Decimal("49900.00"), Decimal("49850.00")]

    def test_multi_ladders_short(self, btc_registry):
        req = FloatingOrderRequest(
            symbol="BTCUSDT",
            side="short",
            action="open",
            float_pct=Decimal("0.001"),  # +0.1%（空头挂更高价）
            qty_mode="usdt",
            qty=Decimal("300"),
            ladders=LaddersConfig(count=3, spacing_pct=Decimal("0.001")),
        )
        info = btc_registry.get("BTCUSDT")
        prices = _ladder_prices(Decimal("50000"), req, info)
        # short+open 是 SELL 方向 → 每档向上；base=50050，下一档 50100，再一档 50150
        assert prices == [Decimal("50050.00"), Decimal("50100.00"), Decimal("50150.00")]


# ------------------------------------------------------------------
# 集成：submit()
# ------------------------------------------------------------------


async def test_submit_single_ladder(btc_registry, db_session):
    client = _FakeClient(bid="50000", ask="50000.2")  # mid=50000.1
    guard = GuardService(db_session, client)
    svc = FloatingOrderService(db_session, client, guard)

    req = FloatingOrderRequest(
        symbol="BTCUSDT",
        side="long",
        action="open",
        float_pct=Decimal("-0.001"),
        qty_mode="usdt",
        qty=Decimal("100"),
    )
    result = await svc.submit(req)

    assert len(result.ladders) == 1
    lad = result.ladders[0]
    assert lad.exchange_order_id is not None
    assert lad.status == "NEW"
    assert lad.client_order_id.startswith("fl-0-")
    # 下到 Fake 客户端的 payload 数量应对齐 stepSize
    assert len(client.placed) == 1
    p = client.placed[0]
    assert p["symbol"] == "BTCUSDT"
    assert p["side"] == "BUY"
    assert Decimal(p["price"]) <= Decimal("50000.1")


async def test_submit_guard_blocks_unknown_symbol(btc_registry, db_session):
    # 把白名单去掉，改为只允许 ETHUSDT
    await db_session.execute(
        GuardRules.__table__.update().values(
            rules_json=json.dumps(
                {**DEFAULT_GUARD_RULES, "allowed_symbols": ["ETHUSDT"]}
            )
        )
    )
    await db_session.commit()

    client = _FakeClient()
    guard = GuardService(db_session, client)
    svc = FloatingOrderService(db_session, client, guard)
    req = FloatingOrderRequest(
        symbol="BTCUSDT",
        side="long",
        action="open",
        float_pct=Decimal("0"),
        qty_mode="usdt",
        qty=Decimal("50"),
    )
    with pytest.raises(GuardBlockedError):
        await svc.submit(req)
    assert client.placed == []


async def test_submit_ladders_equal_distribution(btc_registry, db_session):
    client = _FakeClient(bid="50000", ask="50000")
    guard = GuardService(db_session, client)
    svc = FloatingOrderService(db_session, client, guard)

    req = FloatingOrderRequest(
        symbol="BTCUSDT",
        side="long",
        action="open",
        float_pct=Decimal("-0.001"),
        qty_mode="usdt",
        qty=Decimal("300"),
        ladders=LaddersConfig(count=3, spacing_pct=Decimal("0.001")),
    )
    result = await svc.submit(req)

    assert len(result.ladders) == 3
    assert all(lad.exchange_order_id for lad in result.ladders)
    # 价格应递减
    prices = [lad.price for lad in result.ladders]
    assert prices[0] > prices[1] > prices[2]
    # 每档 client_order_id 各不相同并带索引
    assert {lad.client_order_id.split("-")[1] for lad in result.ladders} == {"0", "1", "2"}


async def test_submit_uses_ws_ticker_first(btc_registry, db_session):
    """若 runtime.tickers 有数据，应直接用 WS 价而不 call REST."""
    runtime.tickers["BTCUSDT"] = {"bid": "60000", "ask": "60000.2"}
    client = _FakeClient(bid="1", ask="2")  # REST 假价；不应被用到
    guard = GuardService(db_session, client)
    svc = FloatingOrderService(db_session, client, guard)
    req = FloatingOrderRequest(
        symbol="BTCUSDT",
        side="long",
        action="open",
        float_pct=Decimal("0"),
        qty_mode="usdt",
        qty=Decimal("100"),
    )
    result = await svc.submit(req)
    assert result.mid_price > Decimal("50000")
    # 价格基于 60000 附近
    assert Decimal(client.placed[0]["price"]) > Decimal("50000")
