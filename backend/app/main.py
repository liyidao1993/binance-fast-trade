"""FastAPI 入口 + 生命周期.

启动流程：
  1. init_db()                创建 SQLite 表 + 写默认护栏
  2. 创建只读 BinanceClient   用于预热 exchangeInfo（无需 API Key）
  3. registry.load()          把 tickSize/stepSize 缓存进内存
  4. 关闭该只读 client        登录后 auth.py 会替换为带 Key 的 client

关闭流程：撤销所有 asyncio task（W2+ 起有追价 task）、关闭 HTTPX、关闭 DB。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import runtime
from .api import account, auth, keys, market, orders, risk, strategies, ws, ws_pusher
from .api.deps import ok
from .config import get_settings
from .db import engine, init_db
from .exchanges.binance import BinanceClient, BinanceError
from .exchanges.precision import registry
from .security import ensure_session_secret
# 触发策略注册装饰器（grid / ma_cross / ai_signal）
from .strategies import impls  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    # --- startup ---
    await init_db()

    # 预热 exchangeInfo（不需要签名）
    warmup = BinanceClient(base_url=settings.rest_base)
    try:
        info = await warmup.exchange_info()
        count = registry.load(info)
        print(f"[startup] 已加载 {count} 个交易对精度")  # noqa: T201
    except BinanceError as e:
        # 启动时网络不通不致命；之后调 /market/* 会报错即可
        print(f"[startup][warn] exchangeInfo 加载失败: {e}")  # noqa: T201
    finally:
        await warmup.close()

    # 初始化一个无 key 的 client，便于行情查询在未登录时也能用
    # （但是 /market/ticker 等仍要走登录态的 deps.Binance；这里保持 None）
    runtime.binance_client = None

    # 启动 WS pusher（向前端广播 tickers / orders）
    ws_pusher.start()

    yield

    # --- shutdown ---
    from .strategies import runner as strategy_runner

    await strategy_runner.stop_all()
    await ws_pusher.stop()
    if runtime.binance_ws is not None:
        await runtime.binance_ws.stop()
    if runtime.binance_client is not None:
        await runtime.binance_client.close()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Binance Fast Trade",
        version="0.1.0",
        description="单人自用的币安合约快速交易系统（W1 骨架）",
        lifespan=lifespan,
    )

    # Session（PIN 登录用 cookie）
    app.add_middleware(
        SessionMiddleware,
        secret_key=ensure_session_secret(settings.session_secret),
        session_cookie="bft_session",
        https_only=settings.is_mainnet,  # mainnet 强制 HTTPS cookie
        same_site="lax",
        max_age=60 * 60 * 8,  # 8 小时
    )

    # 本地前端开发方便
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return ok(
            {
                "status": "ok",
                "env": settings.binance_env,
                "symbols_loaded": registry.size(),
            }
        )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(keys.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(orders.router, prefix="/api/v1")
    app.include_router(risk.router, prefix="/api/v1")
    app.include_router(strategies.router, prefix="/api/v1")
    app.include_router(account.router, prefix="/api/v1")
    # /ws 端点（前端订阅用）
    app.include_router(ws.router, prefix="/api/v1")

    return app


app = create_app()
