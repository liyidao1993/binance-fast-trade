"""WS fan-out 管理器.

职责：维护 {topic → set[WebSocket]} 的订阅表，
把各路事件（tickers 变化 / 订单成交 / 策略心跳）推送到订阅该 topic 的前端连接.

topic 命名：
  - ``ticker:<SYMBOL>``   bookTicker 推送
  - ``orders``            ORDER_TRADE_UPDATE 推送
  - ``positions``         每次订单成交后刷新的持仓快照
  - ``strategy_runs``     策略心跳 / 状态变更
  - ``tickers``           所有已订阅 symbol 的 ticker 合并流（前端简单页面用）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

log = logging.getLogger("ws_hub")


class WsHub:
    """前端 WS 连接集中管理."""

    def __init__(self) -> None:
        # topic → set of websockets
        self._subs: dict[str, set[WebSocket]] = {}
        # 反向索引：ws → set of topics，方便断开时清理
        self._ws_topics: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._ws_topics[ws] = set()

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            topics = self._ws_topics.pop(ws, set())
            for t in topics:
                subs = self._subs.get(t)
                if subs is not None:
                    subs.discard(ws)
                    if not subs:
                        self._subs.pop(t, None)

    async def subscribe(self, ws: WebSocket, topic: str) -> None:
        async with self._lock:
            self._subs.setdefault(topic, set()).add(ws)
            self._ws_topics.setdefault(ws, set()).add(topic)

    async def unsubscribe(self, ws: WebSocket, topic: str) -> None:
        async with self._lock:
            subs = self._subs.get(topic)
            if subs is not None:
                subs.discard(ws)
                if not subs:
                    self._subs.pop(topic, None)
            wst = self._ws_topics.get(ws)
            if wst is not None:
                wst.discard(topic)

    async def publish(self, topic: str, event: str, data: Any) -> None:
        """把消息 {topic, event, data} 推给订阅 topic 的全部连接."""
        payload = json.dumps({"topic": topic, "event": event, "data": data}, default=str, ensure_ascii=False)
        dead: list[WebSocket] = []
        subs = self._subs.get(topic)
        if not subs:
            return
        # 拷贝一份遍历，避免在发送过程中被 mutate
        for ws in list(subs):
            if ws.application_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_text(payload)
            except Exception as e:  # noqa: BLE001
                log.debug("ws send failed, dropping: %s", e)
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._ws_topics)

    @property
    def topic_count(self) -> int:
        return len(self._subs)


# 全局单例
hub = WsHub()
