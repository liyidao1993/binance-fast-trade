"""Binance U 本位合约 WebSocket 客户端.

职责：
  1. 订阅 ``<symbol>@bookTicker`` 流 → 实时写入 :data:`runtime.tickers`.
     浮动挂单 / 追价 都从这里读最新价。
  2. 订阅 **用户流** ``listenKey`` → 回调 ``on_order_update`` / ``on_account_update``.
     支持 ORDER_TRADE_UPDATE（成交、部分成交、撤销）事件驱动 TP/SL 挂钩。
  3. 自动重连 + listenKey 30min 续期.

币安 Futures WS 约定（testnet / mainnet 共用）：
  - 行情流 wss://stream.binancefuture.com/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker
  - 用户流 wss://fstream.binance.com/ws/<listenKey>  （mainnet）
  - 用户流 wss://stream.binancefuture.com/ws/<listenKey> （testnet）
  listenKey 通过 POST /fapi/v1/listenKey 拿，每 30min 调 PUT 续期。

设计备注：
  - 本模块 **不耦合** FastAPI。由 main.py lifespan 注入 BinanceClient，负责 listenKey 生命周期。
  - 订阅列表由 runtime 保存；热添加 symbol 通过 SUBSCRIBE 控制帧发送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from .. import runtime
from .binance import BinanceClient, BinanceError

log = logging.getLogger("binance_ws")


# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

_RECONNECT_BACKOFF = (1, 2, 5, 10, 20, 30)  # 秒
_LISTEN_KEY_KEEPALIVE_SEC = 25 * 60  # 25 分钟续一次（官方 60min 失效，30min 是保守值）
_USER_STREAM_PATH_TESTNET = "/ws/"
_USER_STREAM_PATH_MAINNET = "/ws/"


# ------------------------------------------------------------------
# Callback types
# ------------------------------------------------------------------

OrderUpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""ORDER_TRADE_UPDATE 回调：传入 event["o"] 子字典."""


# ------------------------------------------------------------------
# 客户端
# ------------------------------------------------------------------


class BinanceWsClient:
    """双流客户端：行情流 + 用户流（任一可关闭）."""

    def __init__(
        self,
        ws_base: str,
        rest_client: BinanceClient | None = None,
    ) -> None:
        self._ws_base = ws_base.rstrip("/")
        self._rest = rest_client

        # 订阅的 symbols（统一小写，币安 WS 流名要求）
        self._symbols: set[str] = set()
        # 订阅的 kline 流，元素形如 "btcusdt@kline_15m"
        self._kline_streams: set[str] = set()
        # kline 回调（runner 注册；event 形如 币安 kline payload）
        self._kline_callbacks: list = []

        # 任务句柄
        self._market_task: asyncio.Task | None = None
        self._user_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None

        # 用户流相关
        self._listen_key: str | None = None
        self._order_callbacks: list[OrderUpdateCallback] = []

        self._stop_evt = asyncio.Event()
        self._started = False

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def start(self, symbols: list[str] | None = None, *, with_user_stream: bool = True, kline_specs: list[tuple[str, str]] | None = None) -> None:
        """启动行情流（与可选的用户流）.

        :param symbols:        订阅 @bookTicker 流的 symbol 列表（写进程内 tickers）
        :param with_user_stream: 是否订阅用户流（需 API Key）
        :param kline_specs:    [(symbol, interval)] 列表，订阅 @kline_{interval} 流
        """
        if self._started:
            return
        self._started = True
        self._stop_evt.clear()

        if symbols:
            self._symbols.update(s.lower() for s in symbols)
        if kline_specs:
            for sym, iv in kline_specs:
                self._kline_streams.add(f"{sym.lower()}@kline_{iv}")

        if self._symbols or self._kline_streams:
            self._market_task = asyncio.create_task(self._run_market_stream(), name="ws-market")

        if with_user_stream and self._rest is not None:
            try:
                await self._init_listen_key()
            except BinanceError as e:
                log.warning("listenKey 获取失败（用户流禁用）: %s", e)
            else:
                self._user_task = asyncio.create_task(self._run_user_stream(), name="ws-user")
                self._keepalive_task = asyncio.create_task(
                    self._run_keepalive(), name="ws-keepalive"
                )

    async def stop(self) -> None:
        self._stop_evt.set()
        for t in (self._market_task, self._user_task, self._keepalive_task):
            if t is not None and not t.done():
                t.cancel()
        for t in (self._market_task, self._user_task, self._keepalive_task):
            if t is not None:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._market_task = None
        self._user_task = None
        self._keepalive_task = None
        self._started = False

    def add_order_callback(self, cb: OrderUpdateCallback) -> None:
        self._order_callbacks.append(cb)

    def add_kline_callback(self, cb) -> None:
        """注册一个 kline 回调，签名 ``async (data: dict) -> None``.

        data 是币安推过来的 "k" 对象（含 s/i/t/T/o/c/h/l/v/x 等字段）。
        """
        self._kline_callbacks.append(cb)

    async def ensure_kline(self, symbol: str, interval: str) -> None:
        """动态添加 kline 订阅；若当前未订阅就重启 market 流以生效."""
        stream = f"{symbol.lower()}@kline_{interval}"
        if stream in self._kline_streams:
            return
        self._kline_streams.add(stream)
        # 重启 market task
        if self._market_task is not None and not self._market_task.done():
            self._market_task.cancel()
            try:
                await self._market_task
            except (asyncio.CancelledError, Exception):
                pass
        self._market_task = asyncio.create_task(self._run_market_stream(), name="ws-market")

    @property
    def subscribed_symbols(self) -> list[str]:
        return sorted(self._symbols)

    # ------------------------------------------------------------------
    # 行情流
    # ------------------------------------------------------------------

    def _market_url(self) -> str:
        parts = [f"{s}@bookTicker" for s in sorted(self._symbols)]
        parts.extend(sorted(self._kline_streams))
        return f"{self._ws_base}/stream?streams={'/'.join(parts)}"

    async def _run_market_stream(self) -> None:
        attempt = 0
        while not self._stop_evt.is_set():
            url = self._market_url()
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20, close_timeout=5
                ) as ws:
                    attempt = 0  # 连上就重置
                    log.info("market stream connected: %d ticker + %d kline", len(self._symbols), len(self._kline_streams))
                    async for raw in ws:
                        if self._stop_evt.is_set():
                            break
                        await self._handle_market_msg(raw)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as e:
                log.warning("market ws closed: %s", e)
            except Exception as e:  # noqa: BLE001
                log.error("market ws error: %s", e)

            if self._stop_evt.is_set():
                break
            delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
            attempt += 1
            log.info("market ws reconnect in %.0fs", delay)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _handle_market_msg(self, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        # combined stream 格式：{"stream": "btcusdt@bookTicker", "data": {...}}
        stream = msg.get("stream", "") if isinstance(msg, dict) else ""
        data = msg.get("data") if isinstance(msg, dict) and "data" in msg else msg
        if not isinstance(data, dict):
            return

        # 分发 kline
        if "@kline_" in stream or data.get("e") == "kline":
            k = data.get("k")
            if isinstance(k, dict):
                for cb in list(self._kline_callbacks):
                    try:
                        await cb(k)
                    except Exception as e:  # noqa: BLE001
                        log.error("kline callback error: %s", e)
            return

        # bookTicker：写 tickers
        symbol = data.get("s")
        bid = data.get("b")
        ask = data.get("a")
        if not symbol or bid is None or ask is None:
            return
        runtime.tickers[str(symbol).upper()] = {
            "bid": str(bid),
            "ask": str(ask),
            "ts": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        }

    # ------------------------------------------------------------------
    # 用户流
    # ------------------------------------------------------------------

    async def _init_listen_key(self) -> None:
        assert self._rest is not None
        resp = await self._rest._request(  # noqa: SLF001
            "POST", "/fapi/v1/listenKey", signed=False
        )
        if not resp or "listenKey" not in resp:
            raise BinanceError(-1, "listenKey 响应不合法", 0)
        self._listen_key = str(resp["listenKey"])

    def _user_url(self) -> str:
        assert self._listen_key is not None
        return f"{self._ws_base}{_USER_STREAM_PATH_TESTNET}{self._listen_key}"

    async def _run_user_stream(self) -> None:
        attempt = 0
        while not self._stop_evt.is_set():
            if not self._listen_key:
                try:
                    await self._init_listen_key()
                except BinanceError as e:
                    log.warning("listenKey 取 fail: %s", e)
                    await asyncio.sleep(10)
                    continue
            url = self._user_url()
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20, close_timeout=5
                ) as ws:
                    attempt = 0
                    log.info("user stream connected")
                    async for raw in ws:
                        if self._stop_evt.is_set():
                            break
                        await self._handle_user_msg(raw)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as e:
                log.warning("user ws closed: %s", e)
                self._listen_key = None  # 重新申请
            except Exception as e:  # noqa: BLE001
                log.error("user ws error: %s", e)

            if self._stop_evt.is_set():
                break
            delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _handle_user_msg(self, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        event_type = msg.get("e")
        if event_type == "ORDER_TRADE_UPDATE":
            o = msg.get("o", {})
            for cb in list(self._order_callbacks):
                try:
                    await cb(o)
                except Exception as e:  # noqa: BLE001
                    log.error("order callback error: %s", e)
        # 其他事件类型（ACCOUNT_UPDATE 等）目前不使用；W3/W4 起可扩展

    async def _run_keepalive(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=_LISTEN_KEY_KEEPALIVE_SEC)
            except asyncio.TimeoutError:
                pass
            if self._stop_evt.is_set():
                break
            if self._rest is None or not self._listen_key:
                continue
            try:
                await self._rest._request("PUT", "/fapi/v1/listenKey", signed=False)  # noqa: SLF001
                log.debug("listenKey keepalive ok")
            except BinanceError as e:
                log.warning("listenKey keepalive failed: %s", e)
                self._listen_key = None  # 触发用户流重连里重拿
