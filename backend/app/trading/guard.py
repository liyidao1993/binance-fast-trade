"""硬性防手抖护栏（非熔断）.

V2 §五.5 明确：
  ✅ 保留 per_order_max_usdt / global_max_position_usdt / global_max_leverage /
     allowed_symbols / forbidden_symbols / require_pin_for
  ❌ 删除 daily_loss_limit_usdt 和 circuit_breaker 自动触发

GuardService 只做 **无状态** 的运行时校验：每次下单拉一次 DB 规则 + 查一次当前持仓
（通过 BinanceClient），做逐条硬性校验。结果给调用方决定放行/拦截。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import DEFAULT_GUARD_RULES, GuardRules
from ..exchanges.binance import BinanceClient, BinanceError


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------


@dataclass
class GuardCheck:
    """护栏校验结果."""

    allow: bool
    reasons: list[str]  # 全部被拒原因；allow=False 时至少一条
    warnings: list[str]  # 允许但有提示，如仓位接近上限

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) or ""

    @classmethod
    def pass_(cls, warnings: list[str] | None = None) -> "GuardCheck":
        return cls(allow=True, reasons=[], warnings=list(warnings or []))

    @classmethod
    def block(cls, *reasons: str) -> "GuardCheck":
        return cls(allow=False, reasons=list(reasons), warnings=[])


@dataclass
class GuardOrderInput:
    """下单前护栏校验的入参（最小必要集）."""

    symbol: str
    side: str  # BUY / SELL
    notional_usdt: Decimal  # 本单名义金额（价 * 量）
    leverage: int | None = None  # 本次或当前 symbol 杠杆（None 表示不校验）
    reduce_only: bool = False  # 仅减仓单跳过仓位上限校验


# ------------------------------------------------------------------
# GuardService
# ------------------------------------------------------------------


class GuardService:
    """运行时护栏.

    用法::

        guard = GuardService(session, client)
        result = await guard.check_order(GuardOrderInput(...))
        if not result.allow:
            raise HTTPException(400, result.reason)
    """

    def __init__(self, session: AsyncSession, client: BinanceClient | None) -> None:
        self._s = session
        self._c = client

    async def load_rules(self) -> dict[str, Any]:
        row = (await self._s.execute(select(GuardRules).limit(1))).scalar_one_or_none()
        if row is None:
            return dict(DEFAULT_GUARD_RULES)
        try:
            return json.loads(row.rules_json)
        except json.JSONDecodeError:
            return dict(DEFAULT_GUARD_RULES)

    # ------------------------------------------------------------------
    # 顶层：下单前校验
    # ------------------------------------------------------------------

    async def check_order(self, order: GuardOrderInput) -> GuardCheck:
        rules = await self.load_rules()
        reasons: list[str] = []
        warnings: list[str] = []

        symbol = order.symbol.upper()

        # 1) symbol 黑白名单
        allowed = [s.upper() for s in rules.get("allowed_symbols", [])]
        forbidden = [s.upper() for s in rules.get("forbidden_symbols", [])]
        if allowed and symbol not in allowed:
            reasons.append(f"symbol {symbol} 不在白名单 {allowed}")
        if symbol in forbidden:
            reasons.append(f"symbol {symbol} 在黑名单中")

        # 2) 单笔上限
        per_max = Decimal(str(rules.get("per_order_max_usdt", 0)))
        if per_max > 0 and order.notional_usdt > per_max:
            reasons.append(
                f"单笔名义 {order.notional_usdt} USDT 超过单笔上限 {per_max} USDT"
            )

        # 3) 杠杆
        if order.leverage is not None:
            max_lev = int(rules.get("global_max_leverage", 0))
            if max_lev > 0 and order.leverage > max_lev:
                reasons.append(f"杠杆 {order.leverage}x 超过上限 {max_lev}x")

        # 4) 总持仓（reduce_only 跳过，因为减仓总是收敛风险）
        if not order.reduce_only and self._c is not None:
            global_max = Decimal(str(rules.get("global_max_position_usdt", 0)))
            if global_max > 0:
                try:
                    current_notional = await self._current_total_notional()
                except BinanceError as e:
                    warnings.append(f"无法读取当前持仓做总仓校验（放行）：{e}")
                else:
                    projected = current_notional + order.notional_usdt
                    if projected > global_max:
                        reasons.append(
                            f"总仓 {current_notional} + 本单 {order.notional_usdt} "
                            f"= {projected} USDT 超过总仓上限 {global_max} USDT"
                        )
                    elif projected > global_max * Decimal("0.8"):
                        warnings.append(
                            f"总仓占用 {projected}/{global_max} USDT，已超过 80%"
                        )

        if reasons:
            return GuardCheck(allow=False, reasons=reasons, warnings=warnings)
        return GuardCheck(allow=True, reasons=[], warnings=warnings)

    # ------------------------------------------------------------------
    # 破坏性操作：PIN 要求
    # ------------------------------------------------------------------

    async def require_pin_for(self, action: str) -> bool:
        """判定某个动作是否在 require_pin_for 清单里."""
        rules = await self.load_rules()
        return action in rules.get("require_pin_for", [])

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _current_total_notional(self) -> Decimal:
        """当前所有持仓的名义金额（USDT，按绝对值）之和."""
        assert self._c is not None
        positions = await self._c.position_risk()
        total = Decimal("0")
        for p in positions:
            amt = Decimal(str(p.get("positionAmt", "0")))
            if amt == 0:
                continue
            # entryPrice 偶尔为 0（无仓位但有残留记录）；优先 markPrice
            mark = Decimal(str(p.get("markPrice", "0") or "0"))
            entry = Decimal(str(p.get("entryPrice", "0") or "0"))
            ref = mark if mark > 0 else entry
            total += abs(amt) * ref
        return total
