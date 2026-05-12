"""StrategyContext：策略实例运行期依赖容器.

把 BinanceClient / symbols / stoploss / 指标上报 helper 打包一次传给策略。
策略代码只用 ctx.place_order / ctx.place_stop_loss / ctx.cancel 等，不直接碰 REST。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..exchanges.binance import BinanceClient, BinanceError
from ..exchanges.precision import (
    SymbolInfo,
    check_min_notional,
    registry,
    snap_price,
    snap_quantity,
)
from .stoploss import StopLossConfig, calc_stop_price


@dataclass
class StrategyStats:
    """策略运行期指标."""

    orders_placed: int = 0
    orders_filled: int = 0
    stop_loss_triggered: int = 0
    last_error: str | None = None
    custom: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "orders_placed": self.orders_placed,
            "orders_filled": self.orders_filled,
            "stop_loss_triggered": self.stop_loss_triggered,
            "last_error": self.last_error,
            "custom": self.custom,
        }


class StrategyContext:
    """策略运行上下文."""

    def __init__(
        self,
        *,
        run_id: int,
        strategy_name: str,
        strategy_type: str,
        symbols: list[str],
        params: dict[str, Any],
        stoploss: StopLossConfig,
        client: BinanceClient | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.run_id = run_id
        self.strategy_name = strategy_name
        self.strategy_type = strategy_type
        self.symbols = [s.upper() for s in symbols]
        self.params = params
        self.stoploss = stoploss
        self.client = client
        self.log = logger or logging.getLogger(f"strategy.{strategy_type}.{run_id}")
        self.stats = StrategyStats()

        # 运行控制
        self.stop_event = asyncio.Event()
        # 用来收事件（tick/kline/order）的队列，runner 投递
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    # ------------------------------------------------------------------
    # 精度
    # ------------------------------------------------------------------

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return registry.get(symbol.upper())

    # ------------------------------------------------------------------
    # 下单
    # ------------------------------------------------------------------

    def new_client_id(self, prefix: str = "st") -> str:
        """策略侧 client_order_id 规范：``st-{run_id}-{uuid8}`` .

        fills.py 里匹配的是 "fl-" 开头（浮动）；策略用 "st-" 区分。
        """
        return f"{prefix}-{self.run_id}-{uuid.uuid4().hex[:8]}"

    async def place_limit(
        self,
        symbol: str,
        side: str,  # BUY / SELL
        price: Decimal,
        quantity: Decimal,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("策略未绑定 BinanceClient（回测环境请用 backtest 专用 ctx）")
        info = self.symbol_info(symbol)
        p = snap_price(info, price, side)
        q = snap_quantity(info, quantity)
        if q <= 0:
            raise ValueError(f"quantity {quantity} 小于 step/min_qty")
        if not check_min_notional(info, p, q):
            raise ValueError(f"notional {p * q} < min_notional {info.min_notional}")

        cid = client_order_id or self.new_client_id()
        try:
            resp = await self.client.place_order(
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                order_type="LIMIT",
                quantity=str(q),
                price=str(p),
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                client_order_id=cid,
            )
            self.stats.orders_placed += 1
            return resp
        except BinanceError as e:
            self.stats.last_error = f"place_limit: {e}"
            raise

    async def place_market(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("策略未绑定 BinanceClient")
        info = self.symbol_info(symbol)
        q = snap_quantity(info, quantity)
        if q <= 0:
            raise ValueError(f"quantity {quantity} 小于 step/min_qty")
        cid = client_order_id or self.new_client_id()
        try:
            resp = await self.client.place_order(
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                order_type="MARKET",
                quantity=str(q),
                reduce_only=reduce_only,
                client_order_id=cid,
            )
            self.stats.orders_placed += 1
            return resp
        except BinanceError as e:
            self.stats.last_error = f"place_market: {e}"
            raise

    async def place_stop_loss(
        self,
        symbol: str,
        entry_side: str,  # 开仓方向 BUY/SELL
        entry_price: Decimal,
        quantity: Decimal,
        *,
        atr: Decimal | None = None,
        leverage: int | None = None,
    ) -> dict[str, Any]:
        """根据 ctx.stoploss 配置，挂一张 STOP_MARKET reduceOnly.

        成交后由用户流事件可选择触发；这里是"预挂"模式——跟 floating 的 TP/SL 路径一致。
        """
        if self.client is None:
            raise RuntimeError("策略未绑定 BinanceClient")

        info = self.symbol_info(symbol)
        stop_price = calc_stop_price(
            self.stoploss,
            entry_price=entry_price,
            side=entry_side,
            atr=atr,
            quantity=quantity,
            leverage=leverage,
        )
        # SL 方向与开仓方向相反
        close_side = "SELL" if entry_side.upper() == "BUY" else "BUY"
        stop_price = snap_price(info, stop_price, close_side)

        cid = self.new_client_id("sl")
        try:
            resp = await self.client.place_order(
                symbol=symbol,
                side=close_side,  # type: ignore[arg-type]
                order_type="STOP_MARKET",
                quantity=str(quantity),
                reduce_only=True,
                stop_price=str(stop_price),
                client_order_id=cid,
            )
            return resp
        except BinanceError as e:
            self.stats.last_error = f"place_stop_loss: {e}"
            raise

    async def cancel(self, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        if self.client is None:
            return None
        try:
            return await self.client.cancel_order(symbol, client_order_id=client_order_id)
        except BinanceError as e:
            self.stats.last_error = f"cancel: {e}"
            return None

    async def cancel_all(self, symbol: str) -> None:
        if self.client is None:
            return
        try:
            await self.client.cancel_all_orders(symbol)
        except BinanceError as e:
            self.stats.last_error = f"cancel_all: {e}"

    # ------------------------------------------------------------------
    # 行情 / 持仓辅助
    # ------------------------------------------------------------------

    async def get_mid(self, symbol: str) -> Decimal:
        """进程 tickers 有就用；没有 REST 兜底.

        导入 runtime 放在函数内避免循环 import.
        """
        from .. import runtime

        tk = runtime.tickers.get(symbol.upper())
        if tk:
            bid = Decimal(str(tk.get("bid", "0") or "0"))
            ask = Decimal(str(tk.get("ask", "0") or "0"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
        if self.client is None:
            raise RuntimeError("无 client 且 tickers 无数据")
        data = await self.client.book_ticker(symbol)
        return (Decimal(str(data["bidPrice"])) + Decimal(str(data["askPrice"]))) / 2
