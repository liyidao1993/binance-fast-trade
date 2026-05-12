"""认证：PIN 登录 / 登出 / 初始化.

W1 目标：本地 PIN（argon2 hash）+ 会话 cookie。没有 JWT，没有 2FA。

流程：
  - 首次：DB 无 user → ``POST /auth/init`` 用 ``.env`` 里的 ``APP_PIN`` 初始化并立即登录
  - 常规：``POST /auth/login`` 传 pin → 校验 → 派生 Fernet 密钥写入 runtime →
          若有 Binance API Key 记录，解密 → 初始化 ``runtime.binance_client``
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import runtime
from ..config import get_settings
from ..db import ApiKey, User
from ..exchanges.binance import BinanceClient
from ..security import (
    cipher_from_pin,
    ensure_session_secret,  # noqa: F401  (导出以便 main 复用)
    hash_pin,
    new_salt,
    verify_pin,
)
from .deps import DbSession, ok

router = APIRouter(prefix="/auth", tags=["auth"])


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class PinPayload(BaseModel):
    pin: str = Field(min_length=4, max_length=64)


class InitResult(BaseModel):
    initialized: bool
    message: str


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------


async def _get_user(s: DbSession) -> User | None:
    return (await s.execute(select(User).where(User.id == 1))).scalar_one_or_none()


async def _unlock_binance(user: User, pin: str, s: DbSession) -> bool:
    """用 PIN + user.fernet_salt 派生密钥，解密当前环境的 API Key，初始化 BinanceClient.

    找不到 key 时返回 False（允许无 API Key 启动，仅查公开行情）。
    解密失败抛 HTTPException(500)。
    """
    settings = get_settings()
    salt = base64.urlsafe_b64decode(user.fernet_salt.encode("ascii"))
    cipher = cipher_from_pin(pin, salt)
    runtime.cipher = cipher

    stmt = select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.env == settings.binance_env)
    row = (await s.execute(stmt)).scalar_one_or_none()

    api_key = ""
    api_secret = ""
    if row is not None:
        try:
            api_key = cipher.decrypt(row.api_key_encrypted)
            api_secret = cipher.decrypt(row.api_secret_encrypted)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"API Key 解密失败（PIN 错误或密文损坏）：{e}",
            ) from e

    # 关闭老连接再换新
    if runtime.binance_client is not None:
        await runtime.binance_client.close()

    runtime.binance_client = BinanceClient(
        base_url=settings.rest_base,
        api_key=api_key,
        api_secret=api_secret,
    )
    # 同步时间（非阻塞关键路径；失败打日志不抛）
    try:
        await runtime.binance_client.sync_time()
    except Exception:
        pass

    # 启动 WS：行情流（允许无 key 启动）+ 用户流（若有 key）
    await _start_ws(api_key_ready=bool(api_key and api_secret))

    return bool(api_key and api_secret)


async def _start_ws(api_key_ready: bool) -> None:
    """启动/重启 BinanceWsClient.

    行情流订阅来自 guard_rules.allowed_symbols（没有则跳过）。
    用户流需要 API Key，有才启用。
    """
    from ..exchanges.binance_ws import BinanceWsClient
    from ..trading.fills import on_order_update
    import json as _json
    from sqlalchemy import select as _select
    from ..db import GuardRules, DEFAULT_GUARD_RULES, SessionLocal as _SL

    settings = get_settings()

    # 先停老 WS
    if runtime.binance_ws is not None:
        await runtime.binance_ws.stop()
        runtime.binance_ws = None

    # 读 allowed_symbols
    async with _SL() as _s:
        row = (await _s.execute(_select(GuardRules).limit(1))).scalar_one_or_none()
        rules = _json.loads(row.rules_json) if row else DEFAULT_GUARD_RULES
    symbols = list(rules.get("allowed_symbols", [])) or []

    ws = BinanceWsClient(ws_base=settings.ws_base, rest_client=runtime.binance_client)
    ws.add_order_callback(on_order_update)

    # 前端订阅转发
    from .ws_pusher import order_event_to_hub
    ws.add_order_callback(order_event_to_hub)

    # 把订单事件也转发给策略 runner
    from ..strategies import runner as _strategy_runner
    from ..strategies.events import KlineEvent as _KlineEvent
    from ..strategies.events import OrderEvent as _OrderEvent

    async def _strategy_order_cb(o: dict) -> None:
        try:
            _strategy_runner.dispatch_order(_OrderEvent.from_user_stream(o))
        except Exception:  # noqa: BLE001
            pass

    ws.add_order_callback(_strategy_order_cb)

    async def _kline_cb(k: dict) -> None:
        """币安 kline payload → KlineEvent → runner."""
        from decimal import Decimal

        try:
            evt = _KlineEvent(
                symbol=str(k.get("s", "")).upper(),
                interval=str(k.get("i", "")),
                open_time_ms=int(k.get("t", 0) or 0),
                close_time_ms=int(k.get("T", 0) or 0),
                open=Decimal(str(k.get("o", "0") or "0")),
                high=Decimal(str(k.get("h", "0") or "0")),
                low=Decimal(str(k.get("l", "0") or "0")),
                close=Decimal(str(k.get("c", "0") or "0")),
                volume=Decimal(str(k.get("v", "0") or "0")),
                closed=bool(k.get("x", False)),
            )
        except Exception:  # noqa: BLE001
            return
        _strategy_runner.dispatch_kline(evt)

    ws.add_kline_callback(_kline_cb)

    # 基于当前 running strategies 推断要订阅的 kline_specs
    kline_specs: list[tuple[str, str]] = []
    from ..db import Strategy, StrategyRun

    async with _SL() as _s:
        rows = (
            await _s.execute(
                _select(StrategyRun, Strategy)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .where(StrategyRun.status == "running")
            )
        ).all()
    for run, strat in rows:
        try:
            params = _json.loads(strat.params_json or "{}")
            iv = params.get("interval")
            if iv:
                for sym in _json.loads(run.symbols_json or "[]"):
                    kline_specs.append((str(sym).lower(), str(iv)))
        except Exception:  # noqa: BLE001
            pass

    await ws.start(symbols=symbols, with_user_stream=api_key_ready, kline_specs=kline_specs)
    runtime.binance_ws = ws


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------


@router.get("/status")
async def status_view(request: Request, s: DbSession) -> dict:
    """返回登录状态和是否已初始化 PIN."""
    user = await _get_user(s)
    return ok(
        {
            "initialized": user is not None,
            "authenticated": bool(request.session.get("user_id")),
            "env": get_settings().binance_env,
        }
    )


@router.post("/init", response_model=None)
async def init_view(request: Request, s: DbSession, payload: PinPayload | None = None) -> dict:
    """首次初始化：写入 PIN（优先用请求体，否则用 .env APP_PIN）.

    为防重复初始化，若 DB 已有 user 则拒绝（改用 /auth/login）。
    """
    existing = await _get_user(s)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="已初始化，请使用 /auth/login")

    settings = get_settings()
    pin = (payload.pin if payload else None) or settings.app_pin
    if not pin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="请传入 pin 或在 .env 设置 APP_PIN",
        )

    salt = new_salt()
    user = User(
        id=1,
        pin_hash=hash_pin(pin),
        fernet_salt=base64.urlsafe_b64encode(salt).decode("ascii"),
    )
    s.add(user)
    await s.commit()
    await s.refresh(user)

    # 顺便解锁（此时还没 API Key，runtime.binance_client 将是只读的）
    await _unlock_binance(user, pin, s)
    request.session["user_id"] = user.id

    return ok(InitResult(initialized=True, message="已初始化，下一步配置 Binance API Key").model_dump())


@router.post("/login")
async def login_view(request: Request, s: DbSession, payload: PinPayload) -> dict:
    user = await _get_user(s)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="尚未初始化，请先 POST /auth/init")

    if not verify_pin(payload.pin, user.pin_hash):
        # 不暴露具体原因，只说失败
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="PIN 错误")

    has_key = await _unlock_binance(user, payload.pin, s)

    # 刷新 last_login
    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    await s.commit()

    request.session["user_id"] = user.id
    return ok({"authenticated": True, "api_key_ready": has_key})


@router.post("/logout")
async def logout_view(request: Request) -> dict:
    request.session.clear()
    # 清理敏感运行时态
    client = runtime.binance_client
    ws = runtime.binance_ws
    runtime.reset()
    if ws is not None:
        await ws.stop()
    if client is not None:
        await client.close()
    return ok({"authenticated": False})
