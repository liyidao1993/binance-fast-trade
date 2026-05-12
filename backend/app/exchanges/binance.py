"""Binance U 本位合约 REST 客户端（testnet/mainnet 自动切换）.

W1 覆盖：
  - exchangeInfo    预热精度
  - bookTicker      查当前价
  - klines          K 线
  - depth           深度
  - positionRisk    持仓
  - account/balance 账户
  - order (LIMIT)   最简限价单
  - cancelOrder / cancelAllOrders
  - leverage        调杠杆（交易前置）

签名：HMAC-SHA256(query)；时间同步用服务器 time。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Literal
from urllib.parse import urlencode

import httpx


class BinanceError(Exception):
    """币安 REST 统一异常."""

    def __init__(self, code: int, message: str, http_status: int = 0) -> None:
        super().__init__(f"[{code}] {message} (http={http_status})")
        self.code = code
        self.message = message
        self.http_status = http_status


class BinanceClient:
    """U 本位合约 REST 客户端.

    :param api_key:     币安 API Key；查行情等公共接口可不传。
    :param api_secret:  API Secret；签名接口必填。
    :param base_url:    REST 基址，通过 :mod:`app.config` 配置注入。
    :param timeout_sec: 单次请求超时（含连接+读）；默认 10s。
    """

    # 需要签名的 HTTP 方法
    _SIGNED_METHODS = {"POST", "PUT", "DELETE"}

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        api_secret: str = "",
        timeout_sec: float = 10.0,
        recv_window_ms: int = 5000,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window = recv_window_ms
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout_sec,
            headers={"User-Agent": "binance-fast-trade/0.1"},
        )
        self._server_time_offset_ms: int = 0

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BinanceClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _sign(self, params: dict[str, Any]) -> str:
        q = urlencode(params, doseq=True)
        sig = hmac.new(
            self._api_secret.encode("utf-8"),
            q.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._server_time_offset_ms

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        method = method.upper()
        params = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            if not self._api_key or not self._api_secret:
                raise BinanceError(-2015, "API Key / Secret 未配置，无法签名", 0)
            params.setdefault("timestamp", self._timestamp_ms())
            params.setdefault("recvWindow", self._recv_window)
            params["signature"] = self._sign(params)
            headers["X-MBX-APIKEY"] = self._api_key
        elif self._api_key:
            # 查询类接口带上 key 也没坏处
            headers["X-MBX-APIKEY"] = self._api_key

        # 币安 Futures 对 POST 和 GET 都要求参数放 query string
        try:
            resp = await self._client.request(
                method, path, params=params, headers=headers
            )
        except httpx.HTTPError as e:
            raise BinanceError(-1, f"网络错误: {e}", 0) from e

        if resp.status_code >= 400:
            try:
                body = resp.json()
                raise BinanceError(
                    int(body.get("code", resp.status_code)),
                    str(body.get("msg", resp.text)),
                    resp.status_code,
                )
            except ValueError:
                raise BinanceError(resp.status_code, resp.text[:200], resp.status_code)

        # 空响应
        if not resp.content:
            return None
        return resp.json()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    async def sync_time(self) -> int:
        """同步服务器时间，写入 offset，返回 offset_ms."""
        data = await self._request("GET", "/fapi/v1/time")
        server_ms = int(data["serverTime"])
        local_ms = int(time.time() * 1000)
        self._server_time_offset_ms = server_ms - local_ms
        return self._server_time_offset_ms

    async def ping(self) -> bool:
        await self._request("GET", "/fapi/v1/ping")
        return True

    # ------------------------------------------------------------------
    # 公开行情
    # ------------------------------------------------------------------

    async def exchange_info(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v1/exchangeInfo")

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol.upper()}
        )

    async def klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return await self._request("GET", "/fapi/v1/klines", params)

    async def depth(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        return await self._request(
            "GET", "/fapi/v1/depth", {"symbol": symbol.upper(), "limit": limit}
        )

    # ------------------------------------------------------------------
    # 账户
    # ------------------------------------------------------------------

    async def balance(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v2/balance", signed=True)

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v2/account", signed=True)

    async def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return await self._request(
            "GET", "/fapi/v2/positionRisk", params, signed=True
        )

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol.upper(), "leverage": leverage},
            signed=True,
        )

    # ------------------------------------------------------------------
    # 交易
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: Literal["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        quantity: str,
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        stop_price: str | None = None,
        position_side: str = "BOTH",
    ) -> dict[str, Any]:
        """下单.

        :param quantity: 必须已经按 stepSize 取整的 Decimal 字符串.
        :param price:    LIMIT 必填；必须按 tickSize 取整.
        :param time_in_force: LIMIT 必填，默认 "GTC"；postOnly 传 "GTX".
        """
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "reduceOnly": "true" if reduce_only else "false",
            "positionSide": position_side.upper(),
        }
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT 单必须传 price")
            params["price"] = price
            params["timeInForce"] = time_in_force or "GTC"
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if client_order_id:
            params["newClientOrderId"] = client_order_id

        return await self._request("POST", "/fapi/v1/order", params, signed=True)

    async def cancel_order(
        self,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if not order_id and not client_order_id:
            raise ValueError("order_id 与 client_order_id 至少传一个")
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return await self._request("DELETE", "/fapi/v1/order", params, signed=True)

    async def cancel_all_orders(self, symbol: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol.upper()}, signed=True
        )

    async def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return await self._request("GET", "/fapi/v1/openOrders", params, signed=True)
