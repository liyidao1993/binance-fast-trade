"""策略级止损 stoploss.py 单测."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.stoploss import (
    StopLossConfig,
    calc_stop_price,
    compute_atr,
    require_valid_stoploss,
    validate_stoploss_config,
)


# ------------------------------------------------------------------
# 校验
# ------------------------------------------------------------------


class TestValidate:
    def test_percent_ok(self):
        cfg = StopLossConfig(mode="percent", pct=Decimal("0.01"))
        assert validate_stoploss_config(cfg) == []

    def test_percent_missing(self):
        cfg = StopLossConfig(mode="percent")
        errs = validate_stoploss_config(cfg)
        assert errs and "pct" in errs[0]

    def test_percent_looks_like_percent(self):
        # 用户填 0.5 → 50%（肯定不对），应提示
        cfg = StopLossConfig(mode="percent", pct=Decimal("5"))
        errs = validate_stoploss_config(cfg)
        assert errs and "小数" in errs[0]

    def test_atr_needs_both(self):
        cfg = StopLossConfig(mode="atr", atr_period=None, atr_multiplier=None)
        errs = validate_stoploss_config(cfg)
        assert any("atr_period" in e for e in errs)
        assert any("atr_multiplier" in e for e in errs)

    def test_atr_ok(self):
        cfg = StopLossConfig(mode="atr", atr_period=14, atr_multiplier=Decimal("2"))
        assert validate_stoploss_config(cfg) == []

    def test_fixed_amount_ok(self):
        cfg = StopLossConfig(mode="fixed_amount", max_loss_usdt=Decimal("10"))
        assert validate_stoploss_config(cfg) == []

    def test_fixed_amount_trailing_rejected(self):
        cfg = StopLossConfig(mode="fixed_amount", max_loss_usdt=Decimal("10"), trailing=True)
        errs = validate_stoploss_config(cfg)
        assert any("trailing" in e for e in errs)


class TestRequireValid:
    def test_none_rejected(self):
        with pytest.raises(ValueError, match="必须配置止损"):
            require_valid_stoploss(None)

    def test_bad_dict_rejected(self):
        with pytest.raises(ValueError):
            require_valid_stoploss({"mode": "percent"})  # 缺 pct

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            require_valid_stoploss({"mode": "magic"})

    def test_ok_dict(self):
        cfg = require_valid_stoploss({"mode": "percent", "pct": "0.01"})
        assert cfg.mode == "percent"
        assert cfg.pct == Decimal("0.01")


# ------------------------------------------------------------------
# 价格计算
# ------------------------------------------------------------------


class TestCalcStopPrice:
    def test_percent_long(self):
        cfg = StopLossConfig(mode="percent", pct=Decimal("0.01"))
        p = calc_stop_price(cfg, entry_price=Decimal("100"), side="BUY")
        assert p == Decimal("99")  # 100 * (1 - 0.01)

    def test_percent_short(self):
        cfg = StopLossConfig(mode="percent", pct=Decimal("0.01"))
        p = calc_stop_price(cfg, entry_price=Decimal("100"), side="SELL")
        assert p == Decimal("101")

    def test_atr_long(self):
        cfg = StopLossConfig(mode="atr", atr_period=14, atr_multiplier=Decimal("2"))
        p = calc_stop_price(
            cfg, entry_price=Decimal("100"), side="BUY", atr=Decimal("1.5")
        )
        # 100 - 2 * 1.5 = 97
        assert p == Decimal("97")

    def test_atr_needs_value(self):
        cfg = StopLossConfig(mode="atr", atr_period=14, atr_multiplier=Decimal("2"))
        with pytest.raises(ValueError):
            calc_stop_price(cfg, entry_price=Decimal("100"), side="BUY", atr=None)

    def test_fixed_amount_long(self):
        cfg = StopLossConfig(mode="fixed_amount", max_loss_usdt=Decimal("10"))
        # qty=0.1，max_loss 10 → price_delta = 100 → stop = 100 - 100 = 0
        p = calc_stop_price(
            cfg, entry_price=Decimal("100"), side="BUY", quantity=Decimal("0.1")
        )
        assert p == Decimal("0")

    def test_fixed_amount_short(self):
        cfg = StopLossConfig(mode="fixed_amount", max_loss_usdt=Decimal("5"))
        # qty=1，delta=5 → stop = entry + 5
        p = calc_stop_price(
            cfg, entry_price=Decimal("100"), side="SELL", quantity=Decimal("1")
        )
        assert p == Decimal("105")

    def test_fixed_amount_needs_qty(self):
        cfg = StopLossConfig(mode="fixed_amount", max_loss_usdt=Decimal("10"))
        with pytest.raises(ValueError):
            calc_stop_price(cfg, entry_price=Decimal("100"), side="BUY")

    def test_entry_must_positive(self):
        cfg = StopLossConfig(mode="percent", pct=Decimal("0.01"))
        with pytest.raises(ValueError):
            calc_stop_price(cfg, entry_price=Decimal("0"), side="BUY")


# ------------------------------------------------------------------
# ATR
# ------------------------------------------------------------------


class TestATR:
    def test_insufficient_data(self):
        # 只有 3 个点，要求 period=14 → 0
        assert compute_atr(
            [Decimal(x) for x in [1, 2, 3]],
            [Decimal(x) for x in [0, 1, 2]],
            [Decimal(x) for x in [0.5, 1.5, 2.5]],
            period=14,
        ) == Decimal("0")

    def test_constant_series(self):
        # 全是同一根 K 线 → ATR = 0
        n = 30
        highs = [Decimal("110")] * n
        lows = [Decimal("90")] * n
        closes = [Decimal("100")] * n
        atr = compute_atr(highs, lows, closes, period=14)
        # 第 1 根 TR = 20；之后 TR = max(20, |110-100|, |90-100|) = 20
        assert atr == Decimal("20")

    def test_growing_range(self):
        # 波动越来越大时 ATR > 0
        n = 30
        highs = [Decimal(100 + i) for i in range(n)]
        lows = [Decimal(50 + i) for i in range(n)]
        closes = [Decimal(75 + i) for i in range(n)]
        atr = compute_atr(highs, lows, closes, period=14)
        assert atr > Decimal("0")
