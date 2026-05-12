"""AI 信号策略.

W3 阶段：接口已就绪，AI 调用走注入的 ``signal_fn``；默认 ``signal_fn=None``
时策略处于 "待命" 状态，不做交易。W5 里程碑会把 ``ai.signal()`` 注入进来。

AI 返回的信号 schema：
    {
        "action": "open_long" | "open_short" | "close" | "hold",
        "size_usdt": "100",           # 可选，默认用 qty_usdt
        "sl_price": "49000",          # 可选，覆盖 ctx.stoploss 的计算
        "tp_price": "51000",          # 可选
        "confidence": 0.75,
        "rationale": "..."
    }
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Awaitable, Callable

from ...exchanges.precision import quantity_from_usdt, snap_price, snap_quantity
from ..base import BaseStrategy
from ..events import KlineEvent
from ..registry import register_strategy
from ..stoploss import calc_stop_price

SignalFn = Callable[[KlineEvent, dict[str, Any]], Awaitable[dict[str, Any]]]


# 模块级注入点；W5 由 ai/signal.py 设置
_signal_provider: SignalFn | None = None


def set_signal_provider(fn: SignalFn | None) -> None:
    """W5 在 ai/signal.py 里调用，把真实的 AI 生成器注入进来."""
    global _signal_provider
    _signal_provider = fn


@register_strategy
class AiSignalStrategy(BaseStrategy):
    type_key = "ai_signal"
    display_name = "AI 信号（W5 完善）"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        p = ctx.params
        self.symbol: str = str(p["symbol"]).upper()
        self.interval: str = str(p.get("interval", "15m"))
        self.qty_usdt: Decimal = Decimal(str(p.get("qty_usdt", "0")))
        # 可选 min_confidence
        self.min_confidence: float = float(p.get("min_confidence", 0.6))

        self._position_side: str = "FLAT"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["symbol", "qty_usdt"],
            "properties": {
                "symbol": {"type": "string"},
                "interval": {"type": "string", "default": "15m"},
                "qty_usdt": {"type": "string"},
                "min_confidence": {"type": "number", "default": 0.6, "minimum": 0, "maximum": 1},
            },
        }

    @classmethod
    def stoploss_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {"type": "string", "enum": ["percent", "atr", "fixed_amount"]},
                "pct": {"type": "string"},
                "atr_period": {"type": "integer"},
                "atr_multiplier": {"type": "string"},
                "max_loss_usdt": {"type": "string"},
            },
        }

    async def on_kline(self, k: KlineEvent) -> None:
        if k.symbol != self.symbol or k.interval != self.interval or not k.closed:
            return
        if _signal_provider is None:
            return  # W3 阶段无 AI provider，静默等待

        try:
            sig = await _signal_provider(k, self.ctx.params)
        except Exception as e:  # noqa: BLE001
            self.ctx.stats.last_error = f"ai signal: {e}"
            return

        action = str(sig.get("action", "hold"))
        conf = float(sig.get("confidence", 0.0))
        if conf < self.min_confidence or action == "hold":
            return

        info = self.ctx.symbol_info(self.symbol)
        size_usdt = Decimal(str(sig.get("size_usdt", self.qty_usdt)))
        qty = snap_quantity(info, quantity_from_usdt(info, k.close, size_usdt))
        if qty <= 0:
            return

        if action == "close":
            if self._position_side == "FLAT":
                return
            close_side = "SELL" if self._position_side == "LONG" else "BUY"
            await self.ctx.place_market(self.symbol, close_side, qty, reduce_only=True)
            self._position_side = "FLAT"
            return

        target = "LONG" if action == "open_long" else "SHORT"
        if target == self._position_side:
            return

        # 切仓
        if self._position_side in {"LONG", "SHORT"}:
            close_side = "SELL" if self._position_side == "LONG" else "BUY"
            await self.ctx.place_market(self.symbol, close_side, qty, reduce_only=True)

        open_side = "BUY" if target == "LONG" else "SELL"
        await self.ctx.place_market(self.symbol, open_side, qty, reduce_only=False)

        # 止损：AI 给了 sl_price 就用，否则按 ctx.stoploss 算
        sl_price_raw = sig.get("sl_price")
        if sl_price_raw is not None:
            sl_price = Decimal(str(sl_price_raw))
        else:
            try:
                sl_price = calc_stop_price(
                    self.ctx.stoploss,
                    entry_price=k.close,
                    side=open_side,
                    quantity=qty,
                )
            except ValueError as e:
                self.ctx.stats.last_error = f"sl calc: {e}"
                self._position_side = target
                return

        close_side = "SELL" if open_side == "BUY" else "BUY"
        sl_price = snap_price(info, sl_price, close_side)

        if self.ctx.client is not None:
            cid = self.ctx.new_client_id("sl-ai")
            try:
                await self.ctx.client.place_order(
                    symbol=self.symbol,
                    side=close_side,  # type: ignore[arg-type]
                    order_type="STOP_MARKET",
                    quantity=str(qty),
                    reduce_only=True,
                    stop_price=str(sl_price),
                    client_order_id=cid,
                )
            except Exception as e:  # noqa: BLE001
                self.ctx.stats.last_error = f"ai sl place: {e}"

        self._position_side = target
