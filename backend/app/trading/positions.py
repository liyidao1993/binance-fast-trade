"""持仓 / 杠杆 / 可用余额 helper.

薄封装 BinanceClient 调用，方便 trading 层使用 Decimal 类型、屏蔽 API 字段名。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..exchanges.binance import BinanceClient


@dataclass
class PositionView:
    """持仓视图（从 positionRisk 映射）."""

    symbol: str
    position_amt: Decimal  # 有符号；>0 long，<0 short
    entry_price: Decimal
    mark_price: Decimal
    leverage: int
    unrealized_pnl: Decimal
    position_side: str  # BOTH / LONG / SHORT

    @property
    def is_long(self) -> bool:
        return self.position_amt > 0

    @property
    def is_short(self) -> bool:
        return self.position_amt < 0

    @property
    def is_flat(self) -> bool:
        return self.position_amt == 0

    @property
    def notional(self) -> Decimal:
        ref = self.mark_price if self.mark_price > 0 else self.entry_price
        return abs(self.position_amt) * ref

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "PositionView":
        def dec(key: str) -> Decimal:
            v = row.get(key)
            if v in (None, ""):
                return Decimal("0")
            return Decimal(str(v))

        return cls(
            symbol=str(row["symbol"]),
            position_amt=dec("positionAmt"),
            entry_price=dec("entryPrice"),
            mark_price=dec("markPrice"),
            leverage=int(row.get("leverage", 0) or 0),
            unrealized_pnl=dec("unRealizedProfit"),
            position_side=str(row.get("positionSide", "BOTH")),
        )


async def list_positions(client: BinanceClient, only_open: bool = True) -> list[PositionView]:
    rows = await client.position_risk()
    views = [PositionView.from_api(r) for r in rows]
    if only_open:
        views = [v for v in views if not v.is_flat]
    return views


async def get_position(client: BinanceClient, symbol: str) -> PositionView | None:
    """返回指定 symbol 的 **有仓位** 记录（BOTH 模式下每个 symbol 最多一条）."""
    rows = await client.position_risk(symbol)
    for r in rows:
        v = PositionView.from_api(r)
        if not v.is_flat:
            return v
    return None


async def ensure_leverage(client: BinanceClient, symbol: str, leverage: int) -> None:
    """设置 symbol 杠杆（每次下单前调一次最保险；失败不致命）."""
    await client.set_leverage(symbol, leverage)
