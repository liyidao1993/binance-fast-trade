"""精度单测 —— V2 §十一 明确要求必须过.

覆盖：
  - floor_to_step / round_to_tick 基本边界
  - snap_price（BUY 向下 / SELL 向上 / None=HALF_UP）
  - snap_quantity（向下 + minQty）
  - check_min_notional
  - quantity_from_usdt
  - PrecisionRegistry.load from exchangeInfo-like payload
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.exchanges.precision import (
    PrecisionRegistry,
    SymbolInfo,
    check_min_notional,
    floor_to_step,
    quantity_from_usdt,
    round_to_tick,
    snap_price,
    snap_quantity,
)


# ------------------------------------------------------------------
# floor_to_step
# ------------------------------------------------------------------


class TestFloorToStep:
    def test_exact_multiple(self):
        assert floor_to_step("0.100", Decimal("0.001")) == Decimal("0.100")

    def test_rounding_down(self):
        assert floor_to_step("0.1234", Decimal("0.001")) == Decimal("0.123")

    def test_very_small_value(self):
        assert floor_to_step("0.00009", Decimal("0.001")) == Decimal("0")

    def test_float_input_no_binary_drift(self):
        # 0.1 + 0.2 的 float 表达是 0.30000000000000004，要保证结果仍是 0.3
        assert floor_to_step(0.1 + 0.2, Decimal("0.1")) == Decimal("0.3")

    def test_large_qty(self):
        assert floor_to_step("12345.678", Decimal("0.01")) == Decimal("12345.67")

    def test_step_must_be_positive(self):
        with pytest.raises(ValueError):
            floor_to_step("1", Decimal("0"))


# ------------------------------------------------------------------
# round_to_tick
# ------------------------------------------------------------------


class TestRoundToTick:
    def test_half_up(self):
        # 0.015 with tick 0.01 → 0.02 (HALF_UP，银行家舍入会到 0.02 也可，但 Decimal HALF_UP 明确)
        assert round_to_tick("0.015", Decimal("0.01"), "HALF_UP") == Decimal("0.02")

    def test_down(self):
        assert round_to_tick("101.234", Decimal("0.1"), "DOWN") == Decimal("101.2")

    def test_up(self):
        assert round_to_tick("101.234", Decimal("0.1"), "UP") == Decimal("101.3")

    def test_exact(self):
        assert round_to_tick("100.00", Decimal("0.01"), "HALF_UP") == Decimal("100.00")

    def test_tick_larger_than_value(self):
        assert round_to_tick("0.3", Decimal("1"), "DOWN") == Decimal("0")
        assert round_to_tick("0.3", Decimal("1"), "UP") == Decimal("1")

    def test_bad_mode(self):
        with pytest.raises(ValueError):
            round_to_tick("1", Decimal("0.1"), "BOGUS")


# ------------------------------------------------------------------
# SymbolInfo + snap_price / snap_quantity
# ------------------------------------------------------------------


@pytest.fixture
def btc_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="BTCUSDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("100"),
        price_precision=2,
        quantity_precision=3,
    )


class TestSnapPrice:
    def test_buy_rounds_down(self, btc_info):
        # 买方挂更低价更保守（不容易追涨到上一 tick）
        assert snap_price(btc_info, "50000.15", side="BUY") == Decimal("50000.10")

    def test_sell_rounds_up(self, btc_info):
        assert snap_price(btc_info, "50000.15", side="SELL") == Decimal("50000.20")

    def test_none_half_up(self, btc_info):
        # 恰好落在中点时 HALF_UP 向上
        assert snap_price(btc_info, "50000.15", side=None) == Decimal("50000.20")

    def test_already_aligned(self, btc_info):
        assert snap_price(btc_info, "50000.10", side="BUY") == Decimal("50000.10")


class TestSnapQuantity:
    def test_truncates_to_step(self, btc_info):
        assert snap_quantity(btc_info, "0.0019") == Decimal("0.001")

    def test_below_min_qty_returns_zero(self, btc_info):
        assert snap_quantity(btc_info, "0.0005") == Decimal("0")

    def test_exact_min(self, btc_info):
        assert snap_quantity(btc_info, "0.001") == Decimal("0.001")


# ------------------------------------------------------------------
# notional / usdt
# ------------------------------------------------------------------


class TestMinNotional:
    def test_ok(self, btc_info):
        assert check_min_notional(btc_info, "50000", "0.01") is True

    def test_fail(self, btc_info):
        # 50000 * 0.001 = 50 < 100
        assert check_min_notional(btc_info, "50000", "0.001") is False

    def test_exact(self, btc_info):
        # 50000 * 0.002 = 100 = min
        assert check_min_notional(btc_info, "50000", "0.002") is True


class TestQuantityFromUsdt:
    def test_typical(self, btc_info):
        # 100 USDT / 50000 = 0.002 → step 0.001 → 0.002
        assert quantity_from_usdt(btc_info, "50000", "100") == Decimal("0.002")

    def test_truncates(self, btc_info):
        # 150 / 50000 = 0.003 → 0.003
        assert quantity_from_usdt(btc_info, "50000", "150") == Decimal("0.003")

    def test_below_min(self, btc_info):
        # 10 / 50000 = 0.0002 → step 0.001 → 0 （低于 min_qty 返回 0）
        assert quantity_from_usdt(btc_info, "50000", "10") == Decimal("0")

    def test_zero_price_raises(self, btc_info):
        with pytest.raises(ValueError):
            quantity_from_usdt(btc_info, "0", "100")


# ------------------------------------------------------------------
# PrecisionRegistry
# ------------------------------------------------------------------


def _exchange_info_fixture() -> dict:
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            },
            {
                "symbol": "ETHUSDT",
                "status": "TRADING",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "NOTIONAL", "minNotional": "20"},
                ],
            },
            {
                "symbol": "OFFLINEUSDT",
                "status": "BREAK",  # 应被跳过
                "filters": [],
            },
        ]
    }


class TestRegistry:
    def test_load_and_query(self):
        reg = PrecisionRegistry()
        loaded = reg.load(_exchange_info_fixture())
        assert loaded == 2
        assert reg.size() == 2
        assert reg.has("btcusdt")  # 大小写不敏感
        assert reg.has("ETHUSDT")
        assert not reg.has("OFFLINEUSDT")

        info = reg.get("btcusdt")
        assert info.symbol == "BTCUSDT"
        assert info.tick_size == Decimal("0.10")
        assert info.min_notional == Decimal("100")

    def test_unknown_symbol(self):
        reg = PrecisionRegistry()
        reg.load(_exchange_info_fixture())
        with pytest.raises(KeyError):
            reg.get("DOGEUSDT")

    def test_clear(self):
        reg = PrecisionRegistry()
        reg.load(_exchange_info_fixture())
        reg.clear()
        assert reg.size() == 0

    def test_notional_from_notional_field(self):
        reg = PrecisionRegistry()
        reg.load(_exchange_info_fixture())
        # ETHUSDT 用的是 NOTIONAL/minNotional 组合
        assert reg.get("ETHUSDT").min_notional == Decimal("20")
