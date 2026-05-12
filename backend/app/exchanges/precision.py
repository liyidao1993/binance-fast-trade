"""精度处理：tickSize / stepSize / minNotional 取整.

所有计算一律用 ``Decimal``，严禁 float。下单前强制取整。

币安 ``exchangeInfo`` 返回的过滤器字段：
  - PRICE_FILTER:    tickSize  = 价格最小跳动
  - LOT_SIZE:        stepSize  = 数量最小跳动
  - MIN_NOTIONAL:    notional  = 最小下单名义金额（价 * 量）
我们把这些缓存到 :class:`SymbolInfo`，由 :class:`PrecisionRegistry` 管理。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any, Iterable


# ------------------------------------------------------------------
# SymbolInfo
# ------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolInfo:
    """单个合约的精度与规则元数据."""

    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int

    @staticmethod
    def _extract(filters: Iterable[dict[str, Any]], filter_type: str) -> dict[str, Any]:
        for f in filters:
            if f.get("filterType") == filter_type:
                return f
        raise KeyError(f"symbol 缺少 {filter_type} 过滤器")

    @classmethod
    def from_exchange_info(cls, entry: dict[str, Any]) -> "SymbolInfo":
        """从 ``/fapi/v1/exchangeInfo`` 的单条 symbol 记录构造."""
        filters = entry.get("filters", [])
        price_f = cls._extract(filters, "PRICE_FILTER")
        lot_f = cls._extract(filters, "LOT_SIZE")
        # 有的接口返回 MIN_NOTIONAL，有的返回 NOTIONAL
        try:
            notional_f = cls._extract(filters, "MIN_NOTIONAL")
            min_notional = Decimal(str(notional_f.get("notional") or notional_f.get("minNotional") or "0"))
        except KeyError:
            try:
                notional_f = cls._extract(filters, "NOTIONAL")
                min_notional = Decimal(str(notional_f.get("minNotional") or notional_f.get("notional") or "0"))
            except KeyError:
                min_notional = Decimal("0")

        return cls(
            symbol=entry["symbol"],
            tick_size=Decimal(str(price_f["tickSize"])),
            step_size=Decimal(str(lot_f["stepSize"])),
            min_qty=Decimal(str(lot_f.get("minQty", "0"))),
            min_notional=min_notional,
            price_precision=int(entry.get("pricePrecision", 8)),
            quantity_precision=int(entry.get("quantityPrecision", 8)),
        )


# ------------------------------------------------------------------
# 取整
# ------------------------------------------------------------------


def _to_decimal(x: Any) -> Decimal:
    """宽松转 Decimal。float 先转 str 避免引入二进制误差."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(str(x))
    return Decimal(str(x))


def floor_to_step(value: Any, step: Decimal) -> Decimal:
    """向下取整到 step 的整数倍，用于数量（防超额下单）."""
    v = _to_decimal(value)
    s = _to_decimal(step)
    if s <= 0:
        raise ValueError("step 必须 > 0")
    return (v / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s


def round_to_tick(value: Any, tick: Decimal, rounding: str = "HALF_UP") -> Decimal:
    """按 tick 取整。价格一般用 HALF_UP，方向敏感时可传 DOWN/UP.

    :param rounding: ``HALF_UP`` / ``DOWN`` / ``UP``
    """
    v = _to_decimal(value)
    t = _to_decimal(tick)
    if t <= 0:
        raise ValueError("tick 必须 > 0")

    mode_map = {
        "HALF_UP": ROUND_HALF_UP,
        "DOWN": ROUND_DOWN,
        "UP": ROUND_UP,
    }
    mode = mode_map.get(rounding.upper())
    if mode is None:
        raise ValueError(f"不支持的 rounding: {rounding}")

    return (v / t).quantize(Decimal("1"), rounding=mode) * t


# ------------------------------------------------------------------
# 面向 SymbolInfo 的快捷函数
# ------------------------------------------------------------------


def snap_price(info: SymbolInfo, price: Any, side: str | None = None) -> Decimal:
    """把价格吸附到 tickSize.

    :param side: 为 "BUY" 向下（保守成本），"SELL" 向上（保守成本），None=HALF_UP.
    """
    if side is None:
        rounding = "HALF_UP"
    elif side.upper() == "BUY":
        rounding = "DOWN"
    elif side.upper() == "SELL":
        rounding = "UP"
    else:
        rounding = "HALF_UP"
    return round_to_tick(price, info.tick_size, rounding)


def snap_quantity(info: SymbolInfo, quantity: Any) -> Decimal:
    """把数量吸附到 stepSize。默认向下，保证不会超量."""
    q = floor_to_step(quantity, info.step_size)
    # 并校验 min_qty
    if q < info.min_qty:
        return Decimal("0")
    return q


def check_min_notional(info: SymbolInfo, price: Any, quantity: Any) -> bool:
    """返回是否满足 minNotional."""
    return _to_decimal(price) * _to_decimal(quantity) >= info.min_notional


def quantity_from_usdt(info: SymbolInfo, price: Any, usdt: Any) -> Decimal:
    """USDT 金额 → 数量（吸附 stepSize）.

    常用于 "我想花 100U 买 BTC" 类型的下单。
    """
    p = _to_decimal(price)
    u = _to_decimal(usdt)
    if p <= 0:
        raise ValueError("price 必须 > 0")
    raw_qty = u / p
    return snap_quantity(info, raw_qty)


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


class PrecisionRegistry:
    """进程内缓存 SymbolInfo。应用启动时通过 REST 预热一次."""

    def __init__(self) -> None:
        self._infos: dict[str, SymbolInfo] = {}

    def load(self, exchange_info: dict[str, Any]) -> int:
        """从 ``/fapi/v1/exchangeInfo`` 响应批量加载. 返回本次载入条数."""
        symbols = exchange_info.get("symbols", [])
        count = 0
        for s in symbols:
            # 只关心 "TRADING" 状态的 U 本位永续
            if s.get("status") != "TRADING":
                continue
            try:
                info = SymbolInfo.from_exchange_info(s)
            except KeyError:
                continue
            self._infos[info.symbol] = info
            count += 1
        return count

    def get(self, symbol: str) -> SymbolInfo:
        try:
            return self._infos[symbol.upper()]
        except KeyError as e:
            raise KeyError(f"未知 symbol: {symbol}，请先 load exchangeInfo") from e

    def has(self, symbol: str) -> bool:
        return symbol.upper() in self._infos

    def all_symbols(self) -> list[str]:
        return sorted(self._infos.keys())

    def size(self) -> int:
        return len(self._infos)

    def clear(self) -> None:
        self._infos.clear()


# 全局单例（由 main.py 在 lifespan 里预热）
registry = PrecisionRegistry()
