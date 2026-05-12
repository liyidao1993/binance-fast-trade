"""一键全平 / 一键反手.

V2 §五.2：
  - close-all：撤所有活跃单 → 遍历每个非零持仓 → 市价平（reduceOnly）
  - reverse (symbol)：撤该 symbol 的单 → 市价平 → 按等量反向开（非 reduceOnly）
两者都是破坏性操作，API 层必须要 PIN 确认。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from ..exchanges.binance import BinanceClient, BinanceError
from ..exchanges.precision import registry, snap_quantity
from .positions import PositionView, list_positions


@dataclass
class CloseResult:
    symbol: str
    side_closed: str  # LONG / SHORT
    quantity: Decimal
    ok: bool
    error: str | None = None


@dataclass
class KillSwitchReport:
    canceled_symbols: list[str] = field(default_factory=list)
    closed: list[CloseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return not self.errors and all(c.ok for c in self.closed)


def _close_side(pos: PositionView) -> str:
    """平多用 SELL，平空用 BUY."""
    return "SELL" if pos.is_long else "BUY"


def _kill_client_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def close_all(client: BinanceClient) -> KillSwitchReport:
    """撤所有单 + 市价平所有持仓."""
    report = KillSwitchReport()

    # 1) 先取所有持仓（用于后面撤单的 symbol 收集）
    positions = await list_positions(client, only_open=True)
    symbols = sorted({p.symbol for p in positions})

    # 2) 撤每个 symbol 的所有活跃单
    for sym in symbols:
        try:
            await client.cancel_all_orders(sym)
            report.canceled_symbols.append(sym)
        except BinanceError as e:
            report.errors.append(f"cancel {sym}: {e}")

    # 3) 市价平每个仓位
    for pos in positions:
        info = registry.get(pos.symbol) if registry.has(pos.symbol) else None
        qty = abs(pos.position_amt)
        if info is not None:
            qty = snap_quantity(info, qty)
        if qty <= 0:
            report.closed.append(
                CloseResult(
                    symbol=pos.symbol,
                    side_closed="LONG" if pos.is_long else "SHORT",
                    quantity=qty,
                    ok=False,
                    error="qty<=0 after snap",
                )
            )
            continue
        try:
            await client.place_order(
                symbol=pos.symbol,
                side=_close_side(pos),  # type: ignore[arg-type]
                order_type="MARKET",
                quantity=str(qty),
                reduce_only=True,
                client_order_id=_kill_client_id("ka"),
            )
            report.closed.append(
                CloseResult(
                    symbol=pos.symbol,
                    side_closed="LONG" if pos.is_long else "SHORT",
                    quantity=qty,
                    ok=True,
                )
            )
        except BinanceError as e:
            report.closed.append(
                CloseResult(
                    symbol=pos.symbol,
                    side_closed="LONG" if pos.is_long else "SHORT",
                    quantity=qty,
                    ok=False,
                    error=str(e),
                )
            )
    return report


@dataclass
class ReverseResult:
    symbol: str
    closed: CloseResult | None
    reopened_ok: bool
    reopened_qty: Decimal
    reopened_side: str  # LONG / SHORT
    error: str | None = None


async def reverse(client: BinanceClient, symbol: str) -> ReverseResult:
    """一键反手：撤 symbol 单 → 市价平 → 按等量反向市价开."""
    symbol = symbol.upper()

    # 1) 撤单
    try:
        await client.cancel_all_orders(symbol)
    except BinanceError as e:
        return ReverseResult(
            symbol=symbol,
            closed=None,
            reopened_ok=False,
            reopened_qty=Decimal("0"),
            reopened_side="",
            error=f"cancel failed: {e}",
        )

    # 2) 查持仓
    positions = await list_positions(client, only_open=True)
    pos = next((p for p in positions if p.symbol == symbol), None)
    if pos is None:
        return ReverseResult(
            symbol=symbol,
            closed=None,
            reopened_ok=False,
            reopened_qty=Decimal("0"),
            reopened_side="",
            error="无持仓可反手",
        )

    info = registry.get(symbol) if registry.has(symbol) else None
    qty = abs(pos.position_amt)
    if info is not None:
        qty = snap_quantity(info, qty)
    if qty <= 0:
        return ReverseResult(
            symbol=symbol,
            closed=None,
            reopened_ok=False,
            reopened_qty=qty,
            reopened_side="",
            error="qty<=0 after snap",
        )

    close_side = _close_side(pos)  # 平仓方向
    open_side = "BUY" if close_side == "SELL" else "SELL"  # 反手开仓方向

    # 3) 市价平
    close_report = CloseResult(
        symbol=symbol,
        side_closed="LONG" if pos.is_long else "SHORT",
        quantity=qty,
        ok=False,
    )
    try:
        await client.place_order(
            symbol=symbol,
            side=close_side,  # type: ignore[arg-type]
            order_type="MARKET",
            quantity=str(qty),
            reduce_only=True,
            client_order_id=_kill_client_id("rv-c"),
        )
        close_report.ok = True
    except BinanceError as e:
        close_report.error = str(e)
        return ReverseResult(
            symbol=symbol,
            closed=close_report,
            reopened_ok=False,
            reopened_qty=Decimal("0"),
            reopened_side="",
            error=f"close failed: {e}",
        )

    # 4) 反手开
    try:
        await client.place_order(
            symbol=symbol,
            side=open_side,  # type: ignore[arg-type]
            order_type="MARKET",
            quantity=str(qty),
            reduce_only=False,
            client_order_id=_kill_client_id("rv-o"),
        )
        reopened_side = "SHORT" if pos.is_long else "LONG"
        return ReverseResult(
            symbol=symbol,
            closed=close_report,
            reopened_ok=True,
            reopened_qty=qty,
            reopened_side=reopened_side,
        )
    except BinanceError as e:
        return ReverseResult(
            symbol=symbol,
            closed=close_report,
            reopened_ok=False,
            reopened_qty=Decimal("0"),
            reopened_side="",
            error=f"reopen failed: {e}",
        )
