"""Grid 策略单测（mock BinanceClient）."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app import runtime
from app.exchanges.precision import registry
from app.strategies.context import StrategyContext
from app.strategies.events import OrderEvent
from app.strategies.impls.grid import GridStrategy
from app.strategies.stoploss import StopLossConfig


class _FakeClient:
    def __init__(self) -> None:
        self.placed: list[dict[str, Any]] = []

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        return {"bidPrice": "10000", "askPrice": "10000"}

    async def set_leverage(self, *_a, **_k):
        return {"ok": True}

    async def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return {
            "orderId": 900 + len(self.placed),
            "status": "NEW",
            "clientOrderId": kwargs.get("client_order_id"),
        }

    async def cancel_order(self, *_a, **_k):
        return {"status": "CANCELED"}

    async def cancel_all_orders(self, *_a, **_k):
        return {"status": "CANCELED"}


@pytest.fixture(autouse=True)
def _reset():
    runtime.tickers.clear()
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
                        {"filterType": "MIN_NOTIONAL", "notional": "1"},
                    ],
                }
            ]
        }
    )
    yield
    runtime.tickers.clear()
    registry.clear()


def _make(client) -> GridStrategy:
    ctx = StrategyContext(
        run_id=1,
        strategy_name="grid-test",
        strategy_type="grid",
        symbols=["BTCUSDT"],
        params={
            "symbol": "BTCUSDT",
            "upper": "10500",
            "lower": "9500",
            "grids": 5,
            "qty_per_grid": "0.01",
            "sl_per_ladder": False,  # 测试基础机制先关
        },
        stoploss=StopLossConfig(mode="percent", pct=Decimal("0.01")),
        client=client,
    )
    return GridStrategy(ctx)


# ------------------------------------------------------------------
# 档位计算
# ------------------------------------------------------------------


class TestLadderCompute:
    async def test_on_start_prices_evenly(self):
        client = _FakeClient()
        g = _make(client)
        # 中间价 10000 → pivot = 档 2（10000）；下方 0,1 挂 BUY；上方 3,4 挂 SELL
        await g.on_start()

        assert g.ladder_prices == [
            Decimal("9500.00"),
            Decimal("9750.00"),
            Decimal("10000.00"),
            Decimal("10250.00"),
            Decimal("10500.00"),
        ]
        sides = [p["side"] for p in client.placed]
        prices = [Decimal(p["price"]) for p in client.placed]
        # pivot 不下单 → 4 单
        assert len(client.placed) == 4
        assert sides.count("BUY") == 2
        assert sides.count("SELL") == 2
        # BUY 单的价 < pivot，SELL > pivot
        for p in client.placed:
            if p["side"] == "BUY":
                assert Decimal(p["price"]) < Decimal("10000")
            else:
                assert Decimal(p["price"]) > Decimal("10000")
        # 每档的档位索引应四个不同（0、1、3、4）
        assert set(g.open_orders.keys()) == {0, 1, 3, 4}
        # 所有挂单价都在 ladder_prices 中
        for pr in prices:
            assert pr in g.ladder_prices


# ------------------------------------------------------------------
# 成交补挂
# ------------------------------------------------------------------


class TestRefill:
    async def test_buy_fill_places_sell_above(self):
        client = _FakeClient()
        g = _make(client)
        await g.on_start()

        # 拿到档 1 的 client_order_id
        cid_1 = g.open_orders[1]
        initial_count = len(client.placed)

        # 模拟档 1 的 BUY 成交
        evt = OrderEvent(
            symbol="BTCUSDT",
            client_order_id=cid_1,
            exchange_order_id="901",
            side="BUY",
            status="FILLED",
            exec_type="TRADE",
            last_price=Decimal("9750"),
            last_qty=Decimal("0.01"),
            cum_qty=Decimal("0.01"),
            orig_qty=Decimal("0.01"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        await g.on_order_update(evt)

        # 档 2 是 pivot（初始未挂），BUY 成交 → 应在档 2（上一档）挂 SELL
        assert len(client.placed) == initial_count + 1
        new_order = client.placed[-1]
        assert new_order["side"] == "SELL"
        assert Decimal(new_order["price"]) == Decimal("10000.00")
        assert 2 in g.open_orders

    async def test_sell_fill_places_buy_below(self):
        client = _FakeClient()
        g = _make(client)
        await g.on_start()

        cid_3 = g.open_orders[3]
        initial_count = len(client.placed)

        evt = OrderEvent(
            symbol="BTCUSDT",
            client_order_id=cid_3,
            exchange_order_id="902",
            side="SELL",
            status="FILLED",
            exec_type="TRADE",
            last_price=Decimal("10250"),
            last_qty=Decimal("0.01"),
            cum_qty=Decimal("0.01"),
            orig_qty=Decimal("0.01"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        await g.on_order_update(evt)

        assert len(client.placed) == initial_count + 1
        assert client.placed[-1]["side"] == "BUY"
        assert Decimal(client.placed[-1]["price"]) == Decimal("10000.00")

    async def test_disabled_ladder_not_refilled(self):
        client = _FakeClient()
        g = _make(client)
        await g.on_start()

        cid = g.open_orders[1]
        g.disable_ladder(2)  # 冻结对侧档

        evt = OrderEvent(
            symbol="BTCUSDT",
            client_order_id=cid,
            exchange_order_id="903",
            side="BUY",
            status="FILLED",
            exec_type="TRADE",
            last_price=Decimal("9750"),
            last_qty=Decimal("0.01"),
            cum_qty=Decimal("0.01"),
            orig_qty=Decimal("0.01"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        before = len(client.placed)
        await g.on_order_update(evt)
        # 档 2 被冻结不补挂
        assert len(client.placed) == before
        assert 2 not in g.open_orders

    async def test_fill_from_unknown_cid_ignored(self):
        client = _FakeClient()
        g = _make(client)
        await g.on_start()
        before = len(client.placed)
        evt = OrderEvent(
            symbol="BTCUSDT",
            client_order_id="st-99-ffff",
            exchange_order_id="x",
            side="BUY",
            status="FILLED",
            exec_type="TRADE",
            last_price=Decimal("9750"),
            last_qty=Decimal("0.01"),
            cum_qty=Decimal("0.01"),
            orig_qty=Decimal("0.01"),
            reduce_only=False,
            order_type="LIMIT",
            ts_ms=0,
        )
        await g.on_order_update(evt)
        assert len(client.placed) == before  # 未知 cid 不应该触发补挂
