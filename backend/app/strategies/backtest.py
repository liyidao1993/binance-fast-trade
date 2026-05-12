"""简版 K 线重放回测.

从币安历史 K 线拉数据，构造一个 "本地模拟 BinanceClient"（限价单撮合、
市价成交、持仓追踪），再把 :class:`KlineEvent` 顺序投递给策略实例，
执行完后输出 PnL / 最大回撤 / 成交次数 等基础指标。

限制（V2 说明）：
  - 事件循环重放，不支持向量化（但单进程跑上 10 万根足够）
  - 只支持单 symbol 回测（策略多 symbol 时只回测第一个）
  - 不模拟 STOP_MARKET；SL 通过策略主动平仓实现（策略代码里的
    ``place_stop_loss`` 会被 SimClient 记录但不模拟触发；ma_cross 的
    stop 由下一根 K 线触达时才会成交——为保持简单省略）
  - 手续费按 taker 0.04% 双边结算（testnet 默认）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..exchanges.binance import BinanceClient
from ..exchanges.precision import registry
from .context import StrategyContext
from .events import KlineEvent
from .registry import registry as strategy_registry
from .stoploss import StopLossConfig, require_valid_stoploss

log = logging.getLogger("backtest")


FEE_RATE = Decimal("0.0004")


# ==================================================================
# 仿真客户端
# ==================================================================


@dataclass
class _SimFill:
    side: str
    price: Decimal
    quantity: Decimal
    ts_ms: int
    fee: Decimal
    reduce_only: bool


@dataclass
class _SimOrder:
    client_id: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None
    stop_price: Decimal | None
    reduce_only: bool
    filled: bool = False
    canceled: bool = False


class SimClient:
    """最小交易模拟器：只支持本次回测需要的交易操作."""

    def __init__(self, symbol: str, start_balance: Decimal = Decimal("10000")) -> None:
        self.symbol = symbol.upper()
        self.balance: Decimal = start_balance  # USDT 账户余额
        self.position_qty: Decimal = Decimal("0")  # 有符号；>0 long <0 short
        self.position_entry: Decimal = Decimal("0")

        self.open_orders: list[_SimOrder] = []
        self.fills: list[_SimFill] = []

        # 当前 K 线（由 backtest 在 step() 前设置）
        self._cur_kline: KlineEvent | None = None

    # ------------------------------------------------------------------
    # BinanceClient 接口兼容的子集
    # ------------------------------------------------------------------

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        if self._cur_kline is None:
            return {"bidPrice": "0", "askPrice": "0"}
        # 近似：用 close 价上下半个 tick 作为 bid/ask
        price = self._cur_kline.close
        return {"bidPrice": str(price), "askPrice": str(price)}

    async def set_leverage(self, *args, **kwargs) -> dict[str, Any]:
        return {"ok": True}

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        price: str | None = None,
        stop_price: str | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        **_kwargs,
    ) -> dict[str, Any]:
        cid = client_order_id or f"sim-{uuid.uuid4().hex[:8]}"
        order = _SimOrder(
            client_id=cid,
            side=side.upper(),
            order_type=order_type.upper(),
            quantity=Decimal(quantity),
            price=Decimal(price) if price else None,
            stop_price=Decimal(stop_price) if stop_price else None,
            reduce_only=reduce_only,
        )
        if order_type.upper() == "MARKET" and self._cur_kline is not None:
            # 立即以 close 价成交
            self._fill(order, self._cur_kline.close, self._cur_kline.close_time_ms)
        else:
            self.open_orders.append(order)
        return {"clientOrderId": cid, "status": "FILLED" if order.filled else "NEW"}

    async def cancel_order(self, symbol: str, *, client_order_id: str | None = None, **_k):
        for o in self.open_orders:
            if o.client_id == client_order_id and not o.canceled and not o.filled:
                o.canceled = True
        return {"status": "CANCELED"}

    async def cancel_all_orders(self, symbol: str):
        for o in self.open_orders:
            if not o.canceled and not o.filled:
                o.canceled = True
        return {"status": "CANCELED"}

    async def open_orders_rest(self, symbol: str | None = None):  # 兼容名，策略一般不会在回测调
        return [o for o in self.open_orders if not o.canceled and not o.filled]

    # ------------------------------------------------------------------
    # 撮合
    # ------------------------------------------------------------------

    def step(self, k: KlineEvent) -> None:
        """推进到下一根 K 线：先尝试撮合所有挂单，再把持仓盯市."""
        self._cur_kline = k
        # 限价单撮合：价格 touched 即成交
        for o in self.open_orders:
            if o.filled or o.canceled:
                continue
            if o.order_type == "LIMIT" and o.price is not None:
                if o.side == "BUY" and k.low <= o.price:
                    self._fill(o, min(o.price, k.open), k.close_time_ms)
                elif o.side == "SELL" and k.high >= o.price:
                    self._fill(o, max(o.price, k.open), k.close_time_ms)
            elif o.order_type == "STOP_MARKET" and o.stop_price is not None:
                # 触价即按市价成交；简化：用 close
                if o.side == "BUY" and k.high >= o.stop_price:
                    self._fill(o, o.stop_price, k.close_time_ms)
                elif o.side == "SELL" and k.low <= o.stop_price:
                    self._fill(o, o.stop_price, k.close_time_ms)

    def _fill(self, order: _SimOrder, price: Decimal, ts_ms: int) -> None:
        qty_signed = order.quantity if order.side == "BUY" else -order.quantity

        # 更新持仓：
        prev_qty = self.position_qty
        prev_entry = self.position_entry
        new_qty = prev_qty + qty_signed

        # 实现盈亏（仅在方向对冲时结算）
        realized = Decimal("0")
        if prev_qty != 0 and prev_qty * new_qty < 0:
            # 穿仓：先平掉 prev_qty 那一部分
            close_qty = min(abs(prev_qty), order.quantity)
            pnl_per = (price - prev_entry) if prev_qty > 0 else (prev_entry - price)
            realized += pnl_per * close_qty
        elif prev_qty != 0 and abs(new_qty) < abs(prev_qty):
            # 同向减仓
            close_qty = order.quantity
            pnl_per = (price - prev_entry) if prev_qty > 0 else (prev_entry - price)
            realized += pnl_per * close_qty

        # 更新 entry（加仓或反手重置）
        if new_qty == 0:
            self.position_entry = Decimal("0")
        elif prev_qty * new_qty <= 0:
            self.position_entry = price  # 反手或首开
        else:
            # 加仓：加权平均
            total_cost = prev_entry * abs(prev_qty) + price * order.quantity
            self.position_entry = total_cost / abs(new_qty)
        self.position_qty = new_qty

        fee = price * order.quantity * FEE_RATE
        self.balance = self.balance + realized - fee

        order.filled = True
        self.fills.append(
            _SimFill(
                side=order.side,
                price=price,
                quantity=order.quantity,
                ts_ms=ts_ms,
                fee=fee,
                reduce_only=order.reduce_only,
            )
        )


# ==================================================================
# 回测报告
# ==================================================================


@dataclass
class BacktestReport:
    symbol: str
    start_ts_ms: int
    end_ts_ms: int
    initial_balance: Decimal
    final_balance: Decimal
    realized_pnl: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    total_fills: int
    winning_fills: int
    params: dict[str, Any]
    stoploss: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_ts_ms": self.start_ts_ms,
            "end_ts_ms": self.end_ts_ms,
            "initial_balance": str(self.initial_balance),
            "final_balance": str(self.final_balance),
            "realized_pnl": str(self.realized_pnl),
            "max_drawdown": str(self.max_drawdown),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "total_fills": self.total_fills,
            "winning_fills": self.winning_fills,
            "params": self.params,
            "stoploss": self.stoploss,
            "errors": self.errors,
        }


# ==================================================================
# 入口
# ==================================================================


async def run_backtest(
    *,
    strategy_type: str,
    params: dict[str, Any],
    stoploss: dict[str, Any],
    symbols: list[str],
    klines: list[KlineEvent],
    initial_balance: Decimal = Decimal("10000"),
) -> BacktestReport:
    if not symbols:
        raise ValueError("symbols 不能空")
    if not klines:
        raise ValueError("klines 不能空")
    if not strategy_registry.has(strategy_type):
        raise ValueError(f"未注册的策略：{strategy_type}")

    sl_cfg: StopLossConfig = require_valid_stoploss(stoploss)

    symbol = symbols[0].upper()
    sim = SimClient(symbol=symbol, start_balance=initial_balance)

    ctx = StrategyContext(
        run_id=-1,
        strategy_name="backtest",
        strategy_type=strategy_type,
        symbols=[symbol],
        params=params,
        stoploss=sl_cfg,
        client=sim,  # 把 sim 当 BinanceClient 注入（duck typing）
    )
    strat_cls = strategy_registry.get(strategy_type)
    strategy = strat_cls(ctx)

    errors: list[str] = []

    try:
        await strategy.on_start()
    except Exception as e:  # noqa: BLE001
        errors.append(f"on_start: {e}")

    equity_curve: list[Decimal] = []
    for k in klines:
        sim.step(k)
        try:
            await strategy.on_kline(k)
        except Exception as e:  # noqa: BLE001
            errors.append(f"on_kline {k.open_time_ms}: {e}")
        # 盯市 equity：余额 + 未实现
        unrealized = Decimal("0")
        if sim.position_qty != 0 and sim.position_entry > 0:
            if sim.position_qty > 0:
                unrealized = (k.close - sim.position_entry) * sim.position_qty
            else:
                unrealized = (sim.position_entry - k.close) * abs(sim.position_qty)
        equity_curve.append(sim.balance + unrealized)

    try:
        await strategy.on_stop()
    except Exception as e:  # noqa: BLE001
        errors.append(f"on_stop: {e}")

    # 统计
    peak = equity_curve[0] if equity_curve else initial_balance
    max_dd = Decimal("0")
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = (max_dd / peak * Decimal("100")) if peak > 0 else Decimal("0")

    winning = 0
    last_entry: Decimal | None = None
    last_side: str | None = None
    for f in sim.fills:
        if f.reduce_only and last_entry is not None and last_side is not None:
            pnl = (f.price - last_entry) if last_side == "BUY" else (last_entry - f.price)
            if pnl > 0:
                winning += 1
            last_entry = None
            last_side = None
        elif not f.reduce_only:
            last_entry = f.price
            last_side = f.side

    realized_pnl = sim.balance - initial_balance

    return BacktestReport(
        symbol=symbol,
        start_ts_ms=klines[0].open_time_ms,
        end_ts_ms=klines[-1].close_time_ms,
        initial_balance=initial_balance,
        final_balance=sim.balance,
        realized_pnl=realized_pnl,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        total_fills=len(sim.fills),
        winning_fills=winning,
        params=params,
        stoploss=stoploss,
        errors=errors,
    )


# ==================================================================
# 数据加载：从币安 REST
# ==================================================================


async def fetch_klines_range(
    client: BinanceClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    page_size: int = 1500,
) -> list[KlineEvent]:
    """分页拉 K 线。币安 Futures 单次 max 1500."""
    all_rows: list[list[Any]] = []
    cursor = start_ms
    safety = 0
    while cursor < end_ms and safety < 200:
        rows = await client.klines(
            symbol=symbol,
            interval=interval,
            start_ms=cursor,
            end_ms=end_ms,
            limit=page_size,
        )
        if not rows:
            break
        all_rows.extend(rows)
        last_close = int(rows[-1][6])  # closeTime
        next_cursor = last_close + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        safety += 1
        await asyncio.sleep(0)  # 让出

    # 去重（相邻页边界可能重复一根）
    seen: set[int] = set()
    events: list[KlineEvent] = []
    for r in all_rows:
        ot = int(r[0])
        if ot in seen:
            continue
        seen.add(ot)
        events.append(KlineEvent.from_rest_row(symbol, interval, r))
    events.sort(key=lambda e: e.open_time_ms)
    return events
