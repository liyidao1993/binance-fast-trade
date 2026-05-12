"""/ws 端点：前端连接后订阅 topic，接收实时推送.

协议（JSON text frames）：
  客户端 → 服务器：
    {"op": "subscribe",   "topic": "ticker:BTCUSDT"}
    {"op": "unsubscribe", "topic": "ticker:BTCUSDT"}
    {"op": "ping"}
  服务器 → 客户端：
    {"topic": "ticker:BTCUSDT", "event": "update", "data": {"bid":"...","ask":"...","ts":...}}
    {"topic": "orders",         "event": "update", "data": {...}}
    {"topic": "__system__",     "event": "pong" | "error" | "subscribed" | "unsubscribed"}

鉴权：SessionMiddleware。未登录直接关闭。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from .ws_hub import hub

log = logging.getLogger("ws_endpoint")

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    user_id = None
    try:
        user_id = ws.session.get("user_id")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        user_id = None
    if not user_id:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="not authenticated")
        return

    await hub.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _err(ws, "非法 JSON")
                continue
            if not isinstance(msg, dict):
                await _err(ws, "消息必须是 object")
                continue

            op = str(msg.get("op", "")).lower()
            topic = str(msg.get("topic", ""))

            if op == "subscribe":
                if not topic:
                    await _err(ws, "topic 不能为空")
                    continue
                await hub.subscribe(ws, topic)
                await ws.send_text(
                    json.dumps({"topic": "__system__", "event": "subscribed", "data": topic})
                )
            elif op == "unsubscribe":
                await hub.unsubscribe(ws, topic)
                await ws.send_text(
                    json.dumps({"topic": "__system__", "event": "unsubscribed", "data": topic})
                )
            elif op == "ping":
                await ws.send_text(
                    json.dumps({"topic": "__system__", "event": "pong", "data": None})
                )
            else:
                await _err(ws, f"未知 op: {op}")
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("ws session error: %s", e)
    finally:
        await hub.disconnect(ws)


async def _err(ws: WebSocket, msg: str) -> None:
    try:
        await ws.send_text(
            json.dumps({"topic": "__system__", "event": "error", "data": msg})
        )
    except Exception:  # noqa: BLE001
        pass
