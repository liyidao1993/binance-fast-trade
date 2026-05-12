"""FastAPI 公共依赖."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime
from ..db import get_db
from ..exchanges.binance import BinanceClient


def require_login(request: Request) -> int:
    """从 session cookie 读出已登录 user_id，否则 401."""
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未登录")
    return int(uid)


CurrentUser = Annotated[int, Depends(require_login)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_binance() -> BinanceClient:
    """注入已登录态下可用的 Binance 客户端。

    未解锁（未登录 / 未配置 API Key）时抛 503.
    """
    client = runtime.binance_client
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Binance 客户端未就绪，请先登录并配置 API Key",
        )
    return client


Binance = Annotated[BinanceClient, Depends(get_binance)]


def ok(data: Any = None, msg: str = "") -> dict[str, Any]:
    """统一响应 {code, data, msg}."""
    return {"code": 0, "data": data, "msg": msg}


def fail(code: int, msg: str) -> dict[str, Any]:
    return {"code": code, "data": None, "msg": msg}
