"""Guard 运行时校验单测（用 mock BinanceClient，不发网络）."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.db import DEFAULT_GUARD_RULES
from app.trading.guard import GuardOrderInput, GuardService


# ------------------------------------------------------------------
# Fakes
# ------------------------------------------------------------------


class _FakeSession:
    """最小 mock，只支持 GuardService.load_rules 读 guard_rules 一行."""

    def __init__(self, rules: dict | None) -> None:
        self._rules = rules

    async def execute(self, _stmt):
        rules = self._rules

        class _Row:
            rules_json = json.dumps(rules) if rules is not None else None

        class _Scalar:
            def scalar_one_or_none(self):
                return _Row() if rules is not None else None

        return _Scalar()


class _FakeClient:
    def __init__(self, positions: list[dict] | None = None) -> None:
        self._positions = positions or []

    async def position_risk(self, symbol: str | None = None) -> list[dict]:
        return self._positions


# ------------------------------------------------------------------
# 测试
# ------------------------------------------------------------------


@pytest.fixture
def default_rules() -> dict:
    return dict(DEFAULT_GUARD_RULES)


async def test_pass_within_limits(default_rules):
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    result = await guard.check_order(
        GuardOrderInput(
            symbol="BTCUSDT",
            side="BUY",
            notional_usdt=Decimal("100"),
            leverage=5,
        )
    )
    assert result.allow, result.reasons


async def test_block_not_in_whitelist(default_rules):
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    result = await guard.check_order(
        GuardOrderInput(symbol="DOGEUSDT", side="BUY", notional_usdt=Decimal("50"))
    )
    assert not result.allow
    assert any("白名单" in r for r in result.reasons)


async def test_block_in_blacklist(default_rules):
    default_rules["forbidden_symbols"] = ["BTCUSDT"]
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    result = await guard.check_order(
        GuardOrderInput(symbol="BTCUSDT", side="BUY", notional_usdt=Decimal("50"))
    )
    assert not result.allow
    assert any("黑名单" in r for r in result.reasons)


async def test_block_per_order_max(default_rules):
    default_rules["per_order_max_usdt"] = 100
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    result = await guard.check_order(
        GuardOrderInput(symbol="BTCUSDT", side="BUY", notional_usdt=Decimal("101"))
    )
    assert not result.allow
    assert any("单笔" in r for r in result.reasons)


async def test_block_leverage(default_rules):
    default_rules["global_max_leverage"] = 10
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    result = await guard.check_order(
        GuardOrderInput(
            symbol="BTCUSDT", side="BUY", notional_usdt=Decimal("50"), leverage=20
        )
    )
    assert not result.allow
    assert any("杠杆" in r for r in result.reasons)


async def test_block_global_max_position(default_rules):
    default_rules["global_max_position_usdt"] = 1000
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.02", "markPrice": "50000", "entryPrice": "50000"},
    ]  # 当前仓位 1000 USDT
    guard = GuardService(_FakeSession(default_rules), _FakeClient(positions))
    result = await guard.check_order(
        GuardOrderInput(symbol="BTCUSDT", side="BUY", notional_usdt=Decimal("100"))
    )
    assert not result.allow
    assert any("总仓" in r for r in result.reasons)


async def test_reduce_only_skips_position_check(default_rules):
    default_rules["global_max_position_usdt"] = 100  # 非常小
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.02", "markPrice": "50000", "entryPrice": "50000"},
    ]
    guard = GuardService(_FakeSession(default_rules), _FakeClient(positions))
    result = await guard.check_order(
        GuardOrderInput(
            symbol="BTCUSDT",
            side="SELL",
            notional_usdt=Decimal("500"),
            reduce_only=True,
        )
    )
    assert result.allow, result.reasons


async def test_warning_near_80pct(default_rules):
    default_rules["global_max_position_usdt"] = 1000
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.015", "markPrice": "50000", "entryPrice": "50000"},
    ]  # 750
    guard = GuardService(_FakeSession(default_rules), _FakeClient(positions))
    result = await guard.check_order(
        GuardOrderInput(symbol="BTCUSDT", side="BUY", notional_usdt=Decimal("100"))
    )
    # 750+100=850 占 85%
    assert result.allow
    assert result.warnings and "80%" in result.warnings[0]


async def test_require_pin_for(default_rules):
    guard = GuardService(_FakeSession(default_rules), _FakeClient())
    assert await guard.require_pin_for("close-all") is True
    assert await guard.require_pin_for("reverse") is True
    assert await guard.require_pin_for("nothing") is False
