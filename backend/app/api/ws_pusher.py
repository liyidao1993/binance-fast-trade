"""后端事件 → WsHub 的全局泵.

启动 N 个后台 task：
  1. ticker_pusher   : 每 500ms 把 runtime.tickers 广播到 ticker:<SYM> topic
  2. order_relay     : 把 BinanceWsClient 收到的订单事件转发到 orders topic
                       （由 auth._start_ws 在创建 WS 时调 set_order_relay_active）

别的事件（strategy 心跳、positions 刷新）直接在产生事件的地方调 hub.publish 即可.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import runtime
from .ws_hub import hub

log = logging.getLogger("ws_pusher")

_ticker_task: asyncio.Task | None = None


async def _ticker_loop() -> None:
    """把 tickers 广播到所有订阅者."""
    try:
        while True:
            await asyncio.sleep(0.5)
            if hub.connection_count == 0:
                continue
            snapshot = dict(runtime.tickers)
            for sym, tk in snapshot.items():
                await hub.publish(f"ticker:{sym}", "update", tk)
            await hub.publish("tickers", "snapshot", snapshot)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.error("ticker loop crashed: %s", e)


def start() -> None:
    """main.py lifespan 启动时调用."""
    global _ticker_task
    if _ticker_task is None or _ticker_task.done():
        _ticker_task = asyncio.create_task(_ticker_loop(), name="ws-ticker")


async def stop() -> None:
    global _ticker_task
    if _ticker_task is not None and not _ticker_task.done():
        _ticker_task.cancel()
        try:
            await _ticker_task
        except (asyncio.CancelledError, Exception):
            pass
    _ticker_task = None


# ------------------------------------------------------------------
# 给 binance_ws 注册的回调：订单事件 → hub
# ------------------------------------------------------------------


async def order_event_to_hub(o: dict[str, Any]) -> None:
    """签名匹配 :class:`BinanceWsClient.OrderUpdateCallback`.

    把订单事件翻译成前端友好的结构并推到 ``orders`` topic。
    """
    try:
        payload = {
            "symbol": o.get("s"),
            "client_order_id": o.get("c"),
            "exchange_order_id": o.get("i"),
            "side": o.get("S"),
            "status": o.get("X"),
            "exec_type": o.get("x"),
            "order_type": o.get("o"),
            "last_price": str(o.get("L", "0")),
            "last_qty": str(o.get("l", "0")),
            "cum_qty": str(o.get("z", "0")),
            "orig_qty": str(o.get("q", "0")),
            "reduce_only": bool(o.get("R", False)),
            "time_ms": o.get("T") or o.get("E"),
        }
        await hub.publish("orders", "update", payload)
    except Exception as e:  # noqa: BLE001
        log.warning("order_event_to_hub: %s", e)
