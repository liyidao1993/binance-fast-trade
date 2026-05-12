"""EMA 交叉策略（+ ATR 止损）.

参数：
  - symbol
  - interval           K 线周期（策略只在 ``closed=True`` 的 K 线上做决策）
  - fast_period        快 EMA 周期
  - slow_period        慢 EMA 周期
  - qty_usdt           每次开仓的 USDT 金额（按当前价换算合约张数）
  - atr_period         ATR 周期（与 stoploss.atr_period 建议一致；ATR 计算本策略自算）

止损：
  建议但不强制使用 atr 模式（stoploss.mode = "atr"）。
  runner.start 只要求 SL 合法，不限定模式。

信号：
  EMA_fast 上穿 EMA_slow → 金叉 → 开多（若持空则先平）
  EMA_fast 下穿 EMA_slow → 死叉 → 开空（若持多则先平）
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...exchanges.precision import quantity_from_usdt, snap_quantity
from ..base import BaseStrategy
from ..events import KlineEvent
from ..registry import register_strategy
from ..stoploss import calc_stop_price, compute_atr


@register_strategy
class MaCrossStrategy(BaseStrategy):
    type_key = "ma_cross"
    display_name = "EMA 交叉 + ATR 止损"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        p = ctx.params
        self.symbol: str = str(p["symbol"]).upper()
        self.interval: str = str(p.get("interval", "15m"))
        self.fast_period: int = int(p.get("fast_period", 9))
        self.slow_period: int = int(p.get("slow_period", 21))
        self.qty_usdt: Decimal = Decimal(str(p["qty_usdt"]))
        self.atr_period: int = int(p.get("atr_period", 14))

        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period 必须 < slow_period")

        # 价序列（用于 EMA / ATR）
        self._closes: list[Decimal] = []
        self._highs: list[Decimal] = []
        self._lows: list[Decimal] = []

        self._prev_fast: Decimal | None = None
        self._prev_slow: Decimal | None = None

        self._position_side: str = "FLAT"  # LONG / SHORT / FLAT

    # ------------------------------------------------------------------
    # Schemas
    # ------------------------------------------------------------------

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["symbol", "qty_usdt"],
            "properties": {
                "symbol": {"type": "string"},
                "interval": {"type": "string", "default": "15m"},
                "fast_period": {"type": "integer", "default": 9, "minimum": 2},
                "slow_period": {"type": "integer", "default": 21, "minimum": 3},
                "qty_usdt": {"type": "string"},
                "atr_period": {"type": "integer", "default": 14, "minimum": 2},
            },
        }

    @classmethod
    def stoploss_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["percent", "atr", "fixed_amount"],
                    "default": "atr",
                },
                "pct": {"type": "string"},
                "atr_period": {"type": "integer"},
                "atr_multiplier": {"type": "string", "default": "2"},
                "max_loss_usdt": {"type": "string"},
            },
        }

    # ------------------------------------------------------------------
    # EMA / signal
    # ------------------------------------------------------------------

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> Decimal | None:
        if len(values) < period:
            return None
        alpha = Decimal("2") / Decimal(period + 1)
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (Decimal("1") - alpha) * ema
        return ema

    def _detect_cross(self, fast: Decimal, slow: Decimal) -> str:
        """返回 'golden' / 'death' / 'none'."""
        if self._prev_fast is None or self._prev_slow is None:
            return "none"
        if self._prev_fast <= self._prev_slow and fast > slow:
            return "golden"
        if self._prev_fast >= self._prev_slow and fast < slow:
            return "death"
        return "none"

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    async def on_kline(self, k: KlineEvent) -> None:
        if k.symbol != self.symbol or not k.closed:
            return
        if k.interval != self.interval:
            return

        self._closes.append(k.close)
        self._highs.append(k.high)
        self._lows.append(k.low)
        # 截断内存，只留最近 500 根就够
        if len(self._closes) > 500:
            self._closes.pop(0)
            self._highs.pop(0)
            self._lows.pop(0)

        fast = self._ema(self._closes[-self.slow_period * 3 :], self.fast_period)
        slow = self._ema(self._closes[-self.slow_period * 3 :], self.slow_period)
        if fast is None or slow is None:
            return

        cross = self._detect_cross(fast, slow)
        self._prev_fast = fast
        self._prev_slow = slow

        if cross == "none":
            return

        # 交叉信号 → 切换方向
        if cross == "golden":
            await self._switch_to("LONG", k.close)
        else:
            await self._switch_to("SHORT", k.close)

    async def _switch_to(self, target: str, ref_price: Decimal) -> None:
        """切换到目标持仓方向：先平当前反向仓，再开新仓."""
        if target == self._position_side:
            return

        info = self.ctx.symbol_info(self.symbol)

        # 先平反向仓
        if self._position_side in {"LONG", "SHORT"}:
            close_side = "SELL" if self._position_side == "LONG" else "BUY"
            # 用"反向等量开"的方式；依赖币安 one-way 模式的持仓合并
            qty = quantity_from_usdt(info, ref_price, self.qty_usdt)
            qty = snap_quantity(info, qty)
            if qty > 0:
                try:
                    await self.ctx.place_market(
                        symbol=self.symbol, side=close_side, quantity=qty, reduce_only=True
                    )
                except Exception as e:  # noqa: BLE001
                    self.ctx.stats.last_error = f"close prev: {e}"

        # 开新仓
        open_side = "BUY" if target == "LONG" else "SELL"
        qty = quantity_from_usdt(info, ref_price, self.qty_usdt)
        qty = snap_quantity(info, qty)
        if qty <= 0:
            return

        try:
            await self.ctx.place_market(
                symbol=self.symbol, side=open_side, quantity=qty, reduce_only=False
            )
        except Exception as e:  # noqa: BLE001
            self.ctx.stats.last_error = f"open new: {e}"
            return

        # 挂 SL（按 ctx.stoploss 模式）
        atr = compute_atr(self._highs, self._lows, self._closes, self.atr_period)
        try:
            stop_price = calc_stop_price(
                self.ctx.stoploss,
                entry_price=ref_price,
                side=open_side,
                atr=atr if atr > 0 else None,
                quantity=qty,
            )
        except ValueError as e:
            self.ctx.stats.last_error = f"sl calc: {e}"
            self._position_side = target
            return

        close_side = "SELL" if open_side == "BUY" else "BUY"
        from ...exchanges.precision import snap_price

        stop_price = snap_price(info, stop_price, close_side)
        cid = self.ctx.new_client_id("sl-ma")
        if self.ctx.client is not None:
            try:
                await self.ctx.client.place_order(
                    symbol=self.symbol,
                    side=close_side,  # type: ignore[arg-type]
                    order_type="STOP_MARKET",
                    quantity=str(qty),
                    reduce_only=True,
                    stop_price=str(stop_price),
                    client_order_id=cid,
                )
            except Exception as e:  # noqa: BLE001
                self.ctx.stats.last_error = f"place sl: {e}"

        self._position_side = target
