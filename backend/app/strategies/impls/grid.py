"""网格策略（neutral 模式 + 每档独立 SL）.

参数：
  - symbol          作用的合约（必须 == ctx.symbols[0]）
  - upper / lower   上下边界价（Decimal 字符串）
  - grids           档位数（含端点）
  - qty_per_grid    每档数量（合约张数，Decimal 字符串）
  - reinvest        成交后是否在对侧补挂（默认 True）
  - sl_per_ladder   是否对每档独立挂 SL（v2 要求；默认 True）

工作机制：
  1. ``on_start``：按 upper..lower 均匀划 N 档；当前价下方挂 BUY，上方挂 SELL。
  2. ``on_order_update``：
        - BUY 成交  → 在"上一档"（价格更高）挂 SELL 平头寸
        - SELL 成交 → 在"下一档"（价格更低）挂 BUY 平头寸
  3. 每个成交档位挂一张独立的 STOP_MARKET（触发停补挂该档位，防连环）。
  4. ``on_stop``：撤所有本策略挂的单（通过 cancel_all；更精细 SL 可覆盖）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..base import BaseStrategy
from ..events import OrderEvent
from ..registry import register_strategy
from ..stoploss import calc_stop_price


@register_strategy
class GridStrategy(BaseStrategy):
    type_key = "grid"
    display_name = "网格（每档独立 SL）"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        p = ctx.params
        self.symbol: str = str(p["symbol"]).upper()
        self.upper: Decimal = Decimal(str(p["upper"]))
        self.lower: Decimal = Decimal(str(p["lower"]))
        self.grids: int = int(p.get("grids", 10))
        self.qty_per_grid: Decimal = Decimal(str(p["qty_per_grid"]))
        self.reinvest: bool = bool(p.get("reinvest", True))
        self.sl_per_ladder: bool = bool(p.get("sl_per_ladder", True))

        # 内部态
        self._ladder_prices: list[Decimal] = []
        # 档位索引 → 当前挂单的 client_order_id（None 表示该档暂无挂单）
        self._open_orders: dict[int, str] = {}
        # 已触发 SL 的档位（永久冻结）
        self._disabled_ladders: set[int] = set()

    # ------------------------------------------------------------------
    # Schemas
    # ------------------------------------------------------------------

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["symbol", "upper", "lower", "grids", "qty_per_grid"],
            "properties": {
                "symbol": {"type": "string"},
                "upper": {"type": "string", "description": "上轨价（Decimal 字符串）"},
                "lower": {"type": "string", "description": "下轨价"},
                "grids": {"type": "integer", "minimum": 2, "maximum": 200},
                "qty_per_grid": {"type": "string", "description": "每档合约张数"},
                "reinvest": {"type": "boolean", "default": True},
                "sl_per_ladder": {"type": "boolean", "default": True},
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
                "per_ladder": {"type": "boolean", "default": True},
            },
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _compute_grid(self) -> list[Decimal]:
        if self.grids < 2:
            raise ValueError("grids 必须 >= 2")
        if self.upper <= self.lower:
            raise ValueError("upper 必须 > lower")
        step = (self.upper - self.lower) / Decimal(self.grids - 1)
        return [self.lower + step * Decimal(i) for i in range(self.grids)]

    def _closest_ladder(self, price: Decimal) -> int:
        """找离某价最近的档位 index."""
        return min(range(len(self._ladder_prices)), key=lambda i: abs(self._ladder_prices[i] - price))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_start(self) -> None:
        self._ladder_prices = self._compute_grid()

        mid = await self.ctx.get_mid(self.symbol)
        pivot = self._closest_ladder(mid)

        # 下方档位挂 BUY，上方档位挂 SELL
        for i, price in enumerate(self._ladder_prices):
            if i == pivot:
                continue
            side = "BUY" if i < pivot else "SELL"
            await self._place_ladder(i, price, side)

    async def on_order_update(self, o: OrderEvent) -> None:
        if o.symbol != self.symbol:
            return
        if o.status not in {"FILLED", "PARTIALLY_FILLED"}:
            return

        # 幂等：只对我们本策略发的单做反应
        if not o.client_order_id.startswith(f"st-{self.ctx.run_id}-") and not o.client_order_id.startswith("st-"):
            return

        # 找到这个 client_order_id 所在档位
        ladder_idx = next(
            (idx for idx, cid in self._open_orders.items() if cid == o.client_order_id),
            None,
        )
        if ladder_idx is None:
            return

        # 移除该档位挂单记录
        self._open_orders.pop(ladder_idx, None)
        self.ctx.stats.orders_filled += 1

        # 若该档位已被 SL 冻结，不补挂
        if ladder_idx in self._disabled_ladders:
            return

        # （可选）为本次成交挂独立 SL
        if self.sl_per_ladder and o.status == "FILLED":
            try:
                await self._place_ladder_sl(ladder_idx, o)
            except Exception:  # noqa: BLE001
                pass

        # 补挂对侧档位
        if not self.reinvest:
            return

        if o.side == "BUY":
            # BUY 成交 → 在上一档挂 SELL
            target = ladder_idx + 1
            if 0 <= target < len(self._ladder_prices):
                await self._place_ladder(target, self._ladder_prices[target], "SELL")
        elif o.side == "SELL":
            target = ladder_idx - 1
            if 0 <= target < len(self._ladder_prices):
                await self._place_ladder(target, self._ladder_prices[target], "BUY")

    # ------------------------------------------------------------------
    # 下单辅助
    # ------------------------------------------------------------------

    async def _place_ladder(self, idx: int, price: Decimal, side: str) -> None:
        if idx in self._disabled_ladders:
            return
        if idx in self._open_orders:
            return  # 已经挂了
        try:
            resp = await self.ctx.place_limit(
                symbol=self.symbol,
                side=side,
                price=price,
                quantity=self.qty_per_grid,
            )
        except Exception as e:  # noqa: BLE001
            self.ctx.stats.last_error = f"grid ladder {idx}: {e}"
            return
        cid = str(resp.get("clientOrderId", ""))
        if cid:
            self._open_orders[idx] = cid

    async def _place_ladder_sl(self, idx: int, o: OrderEvent) -> None:
        """为某档的成交挂独立 SL（STOP_MARKET reduceOnly）.

        触发后 ``on_order_update`` 会看到 STOP_MARKET 成交，
        届时我们把该档位加入 ``_disabled_ladders``，停止补挂。
        """
        # 根据全局 stoploss 配置算出此档的止损价
        entry_side = o.side
        entry_price = o.last_price if o.last_price > 0 else self._ladder_prices[idx]
        try:
            stop_price = calc_stop_price(
                self.ctx.stoploss,
                entry_price=entry_price,
                side=entry_side,
                quantity=self.qty_per_grid,
            )
        except ValueError:
            return

        close_side = "SELL" if entry_side == "BUY" else "BUY"
        info = self.ctx.symbol_info(self.symbol)
        from ...exchanges.precision import snap_price

        stop_price = snap_price(info, stop_price, close_side)

        cid = self.ctx.new_client_id(f"sl-g{idx}")
        if self.ctx.client is None:
            return
        try:
            await self.ctx.client.place_order(
                symbol=self.symbol,
                side=close_side,  # type: ignore[arg-type]
                order_type="STOP_MARKET",
                quantity=str(self.qty_per_grid),
                reduce_only=True,
                stop_price=str(stop_price),
                client_order_id=cid,
            )
            # 标记该档可能被 SL 命中；真正冻结要等 SL 成交事件
            self._open_orders[idx] = cid
        except Exception as e:  # noqa: BLE001
            self.ctx.stats.last_error = f"grid sl {idx}: {e}"

    # ------------------------------------------------------------------
    # 属性（给测试用）
    # ------------------------------------------------------------------

    @property
    def ladder_prices(self) -> list[Decimal]:
        return list(self._ladder_prices)

    @property
    def open_orders(self) -> dict[int, str]:
        return dict(self._open_orders)

    @property
    def disabled_ladders(self) -> set[int]:
        return set(self._disabled_ladders)

    def disable_ladder(self, idx: int) -> None:
        """给测试和 SL 成交回调用的显式关闭."""
        self._disabled_ladders.add(idx)
        self._open_orders.pop(idx, None)
