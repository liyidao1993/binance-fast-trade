"""成交回调：把浮动挂单成交后的 TP/SL 自动挂到币安.

由 binance_ws 的 user stream ORDER_TRADE_UPDATE 事件驱动。
事件字段参考官方文档（"o" 子对象）：
    s   symbol
    S   side (BUY/SELL)
    c   clientOrderId
    X   order status (NEW / PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED / REJECTED)
    x   execution type (NEW / TRADE / CANCELED ...)
    L   last filled price
    z   cumulative filled qty
    q   原始下单数量
    o   订单类型 (LIMIT / MARKET / STOP_MARKET ...)
    R   reduceOnly
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from ..db import Order, SessionLocal
from ..exchanges.binance import BinanceClient, BinanceError
from ..exchanges.precision import registry, snap_price, snap_quantity

log = logging.getLogger("fills")


def _opposite(side: str) -> str:
    return "SELL" if side.upper() == "BUY" else "BUY"


def _tp_sl_id(prefix: str, client_id: str) -> str:
    """衍生出 TP/SL 的 client_order_id。长度限制 36，保守截断."""
    # 币安 Futures 限制 clientOrderId ≤ 36 字符
    base = client_id if len(client_id) <= 26 else client_id[:26]
    return f"{prefix}-{base}"


async def place_tp_sl_for_fill(
    client: BinanceClient,
    order_event: dict[str, Any],
) -> dict[str, Any]:
    """根据成交事件挂 TP/SL（如 DB 记录里配置了 tp_pct / sl_pct）.

    幂等：如果 DB order 已经记录 tp_order_id / sl_order_id 则跳过。
    只对 LIMIT 且非 reduceOnly 的浮动挂单成交触发。
    """
    client_id = str(order_event.get("c", ""))
    status = str(order_event.get("X", ""))
    exec_type = str(order_event.get("x", ""))
    order_type = str(order_event.get("o", ""))
    reduce_only = bool(order_event.get("R", False))

    if not client_id.startswith("fl-"):
        return {"skipped": "not floating"}
    if status not in {"FILLED", "PARTIALLY_FILLED"}:
        return {"skipped": f"status={status}"}
    if exec_type != "TRADE":
        return {"skipped": f"exec={exec_type}"}
    if order_type != "LIMIT":
        return {"skipped": f"type={order_type}"}
    if reduce_only:
        return {"skipped": "reduceOnly entry"}

    symbol = str(order_event.get("s", "")).upper()
    fill_price = Decimal(str(order_event.get("L", "0") or "0"))
    filled_qty = Decimal(str(order_event.get("z", "0") or "0"))
    side = str(order_event.get("S", "")).upper()
    if fill_price <= 0 or filled_qty <= 0 or side not in {"BUY", "SELL"}:
        return {"skipped": "bad fill data"}

    # 从 DB 拿原请求的 sl_pct / tp_pct
    async with SessionLocal() as s:
        row = (
            await s.execute(select(Order).where(Order.client_order_id == client_id))
        ).scalar_one_or_none()
        if row is None:
            return {"skipped": "no db row"}

        # 幂等：已挂过就跳
        if row.tp_order_id and row.sl_order_id:
            return {"skipped": "already placed"}

        try:
            req = json.loads(row.raw_request or "{}").get("request", {})
        except json.JSONDecodeError:
            req = {}
        tp_pct = req.get("tp_pct")
        sl_pct = req.get("sl_pct")
        if tp_pct is None and sl_pct is None:
            return {"skipped": "no tp/sl"}

        info = registry.get(symbol) if registry.has(symbol) else None
        if info is None:
            return {"skipped": "no precision info"}

        qty = snap_quantity(info, filled_qty)
        if qty <= 0:
            return {"skipped": "qty<=0"}

        opposite = _opposite(side)

        result: dict[str, Any] = {"placed": []}

        # --- TP (TAKE_PROFIT_MARKET) ---
        if tp_pct is not None and not row.tp_order_id:
            try:
                pct = Decimal(str(tp_pct))
            except Exception:  # noqa: BLE001
                pct = None
            if pct is not None:
                # BUY 成交（多头开仓）→ TP 在更高价；SELL 成交（空头开仓）→ TP 在更低价
                direction = Decimal("1") if side == "BUY" else Decimal("-1")
                tp_price = snap_price(
                    info,
                    fill_price * (Decimal("1") + direction * pct),
                    side=opposite,
                )
                tp_cid = _tp_sl_id("tp", client_id)
                try:
                    resp = await client.place_order(
                        symbol=symbol,
                        side=opposite,  # type: ignore[arg-type]
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=str(qty),
                        reduce_only=True,
                        stop_price=str(tp_price),
                        client_order_id=tp_cid,
                    )
                    row.tp_order_id = tp_cid
                    result["placed"].append({"kind": "TP", "price": str(tp_price), "resp": resp})
                except BinanceError as e:
                    log.warning("TP 挂失败: %s", e)
                    result["placed"].append({"kind": "TP", "error": str(e)})

        # --- SL (STOP_MARKET) ---
        if sl_pct is not None and not row.sl_order_id:
            try:
                pct = Decimal(str(sl_pct))
            except Exception:  # noqa: BLE001
                pct = None
            if pct is not None:
                # sl_pct 是正数，表示亏损百分比；BUY → 止损价更低，SELL → 更高
                direction = Decimal("-1") if side == "BUY" else Decimal("1")
                sl_price = snap_price(
                    info,
                    fill_price * (Decimal("1") + direction * pct),
                    side=opposite,
                )
                sl_cid = _tp_sl_id("sl", client_id)
                try:
                    resp = await client.place_order(
                        symbol=symbol,
                        side=opposite,  # type: ignore[arg-type]
                        order_type="STOP_MARKET",
                        quantity=str(qty),
                        reduce_only=True,
                        stop_price=str(sl_price),
                        client_order_id=sl_cid,
                    )
                    row.sl_order_id = sl_cid
                    result["placed"].append({"kind": "SL", "price": str(sl_price), "resp": resp})
                except BinanceError as e:
                    log.warning("SL 挂失败: %s", e)
                    result["placed"].append({"kind": "SL", "error": str(e)})

        await s.commit()
        return result


async def on_order_update(order_event: dict[str, Any]) -> None:
    """作为 BinanceWsClient.add_order_callback 的注册入口.

    从 ``runtime.binance_client`` 取当前客户端；若未就绪则静默跳过.
    """
    from .. import runtime

    client = runtime.binance_client
    if client is None:
        return
    try:
        await place_tp_sl_for_fill(client, order_event)
    except Exception as e:  # noqa: BLE001
        log.error("fill handler error: %s", e)
