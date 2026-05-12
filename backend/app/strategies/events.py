"""策略事件类型.

由 runner / backtest 触发，分发给 BaseStrategy 的 on_xxx 回调.
所有价格、数量字段都是 Decimal 字符串或 Decimal，禁用 float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TickEvent:
    """bookTicker 推送到策略."""

    symbol: str
    bid: Decimal
    ask: Decimal
    ts_ms: int

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class KlineEvent:
    """K 线 closed 或 live 事件.

    ``closed=True`` 表示这根 K 线已收盘（策略一般只在收盘做决策）。
    """

    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool = True

    @classmethod
    def from_rest_row(cls, symbol: str, interval: str, row: list[Any]) -> "KlineEvent":
        """币安 /fapi/v1/klines 一行 → KlineEvent.

        行格式: [openTime, open, high, low, close, volume, closeTime, ...]
        """
        return cls(
            symbol=symbol,
            interval=interval,
            open_time_ms=int(row[0]),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            close_time_ms=int(row[6]),
            closed=True,
        )


@dataclass(frozen=True)
class OrderEvent:
    """订单状态变更（来自 ORDER_TRADE_UPDATE）."""

    symbol: str
    client_order_id: str
    exchange_order_id: str | None
    side: str
    status: str  # NEW / PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED / REJECTED
    exec_type: str  # NEW / TRADE / CANCELED ...
    last_price: Decimal
    last_qty: Decimal
    cum_qty: Decimal
    orig_qty: Decimal
    reduce_only: bool
    order_type: str
    ts_ms: int

    @classmethod
    def from_user_stream(cls, o: dict[str, Any]) -> "OrderEvent":
        def dec(key: str) -> Decimal:
            v = o.get(key, "0")
            if v in (None, ""):
                return Decimal("0")
            return Decimal(str(v))

        return cls(
            symbol=str(o.get("s", "")).upper(),
            client_order_id=str(o.get("c", "")),
            exchange_order_id=str(o["i"]) if o.get("i") is not None else None,
            side=str(o.get("S", "")).upper(),
            status=str(o.get("X", "")),
            exec_type=str(o.get("x", "")),
            last_price=dec("L"),
            last_qty=dec("l"),
            cum_qty=dec("z"),
            orig_qty=dec("q"),
            reduce_only=bool(o.get("R", False)),
            order_type=str(o.get("o", "")),
            ts_ms=int(o.get("T", 0) or 0) or int(datetime.now(timezone.utc).timestamp() * 1000),
        )
