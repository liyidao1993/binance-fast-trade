"""追价 asyncio task.

每 ``interval_sec`` 秒检查当前挂单 price 与最新 mid 的偏离；
若仍有效、未成交、偏离超过 ``threshold``（默认等于一次 float_pct 的距离），
则撤单、重挂新的 ``target_price = mid * (1 + float_pct)``。

终止条件：
  - 订单成交 / 撤销 / 拒单
  - max_attempts 到
  - expire_sec 到 → 按 on_expire 处理（cancel 或转市价）
  - asyncio.CancelledError（程序关闭）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .. import runtime
from ..exchanges.binance import BinanceClient, BinanceError
from ..exchanges.precision import registry, snap_price
from .floating import FloatingOrderRequest, _get_mid_price, _resolve_order_side


@dataclass
class ChaseState:
    client_order_id: str
    symbol: str
    side: str  # BUY / SELL
    float_pct: Decimal
    interval_sec: float
    max_attempts: int
    expire_sec: float | None
    on_expire: str  # cancel / market
    last_price: Decimal
    attempts: int = 0
    started_at: float = 0.0
    done: bool = False
    result: str = ""  # "filled" / "canceled" / "expired-cancel" / "expired-market" / "error:xxx"


async def _fetch_order_status(client: BinanceClient, symbol: str, client_order_id: str) -> dict:
    """查单。client_order_id 可以直接查."""
    # 使用 open_orders 拿的结果包含 status；更精确的是 /fapi/v1/order，但这里够用
    all_open = await client.open_orders(symbol)
    for o in all_open:
        if o.get("clientOrderId") == client_order_id:
            return o
    # 不在 open 里 → 要么已成交、要么已撤销；返回一个哨兵让 caller 退出
    return {"status": "CLOSED_OR_FILLED"}


async def chase_loop(
    client: BinanceClient,
    req: FloatingOrderRequest,
    client_order_id: str,
    initial_price: Decimal,
    on_done: Callable[[ChaseState], None] | None = None,
) -> ChaseState:
    """追价主循环，返回终止状态."""
    info = registry.get(req.symbol.upper())
    order_side = _resolve_order_side(req.side, req.action)

    state = ChaseState(
        client_order_id=client_order_id,
        symbol=req.symbol.upper(),
        side=order_side,
        float_pct=req.float_pct,
        interval_sec=req.chase.interval_sec,
        max_attempts=req.chase.max_attempts,
        expire_sec=req.chase.expire_sec,
        on_expire=req.chase.on_expire,
        last_price=initial_price,
        started_at=time.monotonic(),
    )

    try:
        while not state.done:
            await asyncio.sleep(state.interval_sec)

            # 1) 超时判定
            if state.expire_sec is not None:
                elapsed = time.monotonic() - state.started_at
                if elapsed >= state.expire_sec:
                    await _on_expire(client, state, req)
                    break

            # 2) 查单状态
            try:
                cur = await _fetch_order_status(client, state.symbol, client_order_id)
            except BinanceError as e:
                state.result = f"error:{e}"
                state.done = True
                break

            status = str(cur.get("status", ""))
            if status in {"FILLED", "PARTIALLY_FILLED"}:
                # 部分成交也视为追价终止（不再搬价，避免撕碎剩余量）
                state.result = "filled"
                state.done = True
                break
            if status in {"CANCELED", "EXPIRED", "REJECTED", "CLOSED_OR_FILLED"}:
                state.result = "canceled"
                state.done = True
                break

            # 3) 算新目标价
            mid = await _get_mid_price(client, state.symbol)
            target = snap_price(info, mid * (Decimal("1") + state.float_pct), side=order_side)
            current_price = Decimal(str(cur.get("price", state.last_price)))

            # 偏离阈值：半个 float_pct 的距离（保守）
            drift_threshold = abs(mid * state.float_pct) / Decimal("2")
            if abs(target - current_price) < drift_threshold:
                continue  # 不值得搬

            # 4) max_attempts
            if state.attempts >= state.max_attempts:
                # 到顶了走 on_expire 流程（与超时共用）
                await _on_expire(client, state, req)
                break

            # 5) 撤旧挂新
            try:
                await client.cancel_order(state.symbol, client_order_id=client_order_id)
            except BinanceError:
                pass

            qty = Decimal(str(cur.get("origQty", "0")))
            if qty <= 0:
                state.result = "error:origQty=0"
                state.done = True
                break

            try:
                resp = await client.place_order(
                    symbol=state.symbol,
                    side=order_side,  # type: ignore[arg-type]
                    order_type="LIMIT",
                    quantity=str(qty),
                    price=str(target),
                    time_in_force=req.time_in_force,
                    reduce_only=req.reduce_only,
                    client_order_id=client_order_id + f"-r{state.attempts + 1}",
                )
            except BinanceError as e:
                state.result = f"error:{e}"
                state.done = True
                break

            state.attempts += 1
            state.last_price = target
            # 继续循环
    except asyncio.CancelledError:
        state.result = "cancelled"
        state.done = True
        raise
    finally:
        if on_done is not None:
            try:
                on_done(state)
            except Exception:  # pragma: no cover
                pass
        runtime.chase_tasks.pop(client_order_id, None)

    return state


async def _on_expire(client: BinanceClient, state: ChaseState, req: FloatingOrderRequest) -> None:
    """到期/次数超限处理：撤单或转市价."""
    try:
        await client.cancel_order(state.symbol, client_order_id=state.client_order_id)
    except BinanceError:
        pass

    if state.on_expire == "market":
        info = registry.get(state.symbol)
        # 近似以最新 mid 计算剩余量（保守）
        qty_str = ""
        try:
            cur = await _fetch_order_status(client, state.symbol, state.client_order_id)
            qty_str = str(cur.get("origQty", "0"))
        except BinanceError:
            qty_str = ""
        if not qty_str or Decimal(qty_str) <= 0:
            state.result = "expired-cancel"
            state.done = True
            return
        try:
            await client.place_order(
                symbol=state.symbol,
                side=state.side,  # type: ignore[arg-type]
                order_type="MARKET",
                quantity=qty_str,
                reduce_only=req.reduce_only,
                client_order_id=state.client_order_id + "-mkt",
            )
            state.result = "expired-market"
        except BinanceError as e:
            state.result = f"error:{e}"
    else:
        state.result = "expired-cancel"
    state.done = True


def start_chase(
    client: BinanceClient,
    req: FloatingOrderRequest,
    client_order_id: str,
    initial_price: Decimal,
) -> asyncio.Task:
    """创建并注册一个追价 task，非阻塞返回."""
    task = asyncio.create_task(
        chase_loop(client, req, client_order_id, initial_price),
        name=f"chase-{client_order_id}",
    )
    runtime.chase_tasks[client_order_id] = task
    return task
