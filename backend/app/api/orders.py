"""订单路由（W1 最简：LIMIT 单、撤单、查单；浮动/追价/一键全平见 W2）."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from ..db import Order
from ..exchanges.binance import BinanceError
from ..exchanges.precision import (
    check_min_notional,
    registry,
    snap_price,
    snap_quantity,
)
from .deps import Binance, DbSession, ok

router = APIRouter(prefix="/orders", tags=["orders"])


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


Side = Literal["BUY", "SELL"]


class LimitOrderRequest(BaseModel):
    """W1 最简限价单. 浮动挂单见 W2 的 /orders/floating."""

    symbol: str
    side: Side
    # 数量可二选一：quantity (已是整手数) 或 quote_usdt (按当前价换算)
    quantity: str | None = None
    quote_usdt: str | None = None
    price: str = Field(description="限价，Decimal 字符串")
    time_in_force: Literal["GTC", "IOC", "FOK", "GTX"] = "GTC"
    reduce_only: bool = False
    position_side: Literal["BOTH", "LONG", "SHORT"] = "BOTH"


class CancelRequest(BaseModel):
    symbol: str
    order_id: int | None = None
    client_order_id: str | None = None


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------


def _resolve_client_id(prefix: str = "m") -> str:
    """生成币安要求的 client order id（<= 36 字符且允许的字符集）."""
    # python-binance 限制 32，这里保留前缀 2~3 + uuid8 = 11 左右，足够长
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------


@router.post("", status_code=status.HTTP_200_OK)
async def place_limit(
    payload: LimitOrderRequest,
    client: Binance,
    s: DbSession,
) -> dict:
    symbol = payload.symbol.upper()
    if not registry.has(symbol):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"未知 symbol：{symbol}")

    info = registry.get(symbol)

    # 价格取整（按方向保守）
    price = snap_price(info, payload.price, payload.side)

    # 数量取整
    if payload.quantity is not None:
        qty = snap_quantity(info, payload.quantity)
    elif payload.quote_usdt is not None:
        raw_qty = Decimal(payload.quote_usdt) / price
        qty = snap_quantity(info, raw_qty)
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="quantity 与 quote_usdt 至少传一个"
        )

    if qty <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"数量小于最小下单量 {info.min_qty}",
        )
    if not check_min_notional(info, price, qty):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"名义价值低于最小 {info.min_notional}",
        )

    cid = _resolve_client_id()

    # 先落 DB（占位），再发单，再回写响应 —— W1 采用简单的"先写后发"便于幂等追踪
    db_order = Order(
        client_order_id=cid,
        symbol=symbol,
        side=payload.side,
        position_side=payload.position_side,
        type="LIMIT",
        time_in_force=payload.time_in_force,
        price=str(price),
        quantity=str(qty),
        reduce_only=payload.reduce_only,
        status="PENDING",
        origin="manual",
        raw_request=json.dumps(payload.model_dump(), ensure_ascii=False),
    )
    s.add(db_order)
    await s.commit()
    await s.refresh(db_order)

    try:
        resp = await client.place_order(
            symbol=symbol,
            side=payload.side,
            order_type="LIMIT",
            quantity=str(qty),
            price=str(price),
            time_in_force=payload.time_in_force,
            reduce_only=payload.reduce_only,
            client_order_id=cid,
            position_side=payload.position_side,
        )
    except BinanceError as e:
        db_order.status = "REJECTED"
        db_order.raw_response = json.dumps({"error": str(e), "code": e.code})
        await s.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    db_order.status = resp.get("status", "NEW")
    db_order.exchange_order_id = str(resp.get("orderId"))
    db_order.raw_response = json.dumps(resp, ensure_ascii=False)
    await s.commit()
    await s.refresh(db_order)

    return ok(
        {
            "id": db_order.id,
            "client_order_id": cid,
            "exchange_order_id": db_order.exchange_order_id,
            "status": db_order.status,
            "symbol": symbol,
            "side": payload.side,
            "price": str(price),
            "quantity": str(qty),
            "exchange_response": resp,
        }
    )


@router.delete("")
async def cancel(payload: CancelRequest, client: Binance) -> dict:
    try:
        resp = await client.cancel_order(
            symbol=payload.symbol,
            order_id=payload.order_id,
            client_order_id=payload.client_order_id,
        )
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(resp)


@router.get("/open")
async def list_open(client: Binance, symbol: str | None = None) -> dict:
    try:
        resp = await client.open_orders(symbol)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(resp)


@router.get("")
async def list_history(s: DbSession, symbol: str | None = None, limit: int = 50) -> dict:
    """本地 DB 订单流水（用于审计；真源在币安）."""
    stmt = select(Order).order_by(desc(Order.created_at)).limit(limit)
    if symbol:
        stmt = stmt.where(Order.symbol == symbol.upper())
    rows = (await s.execute(stmt)).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "client_order_id": r.client_order_id,
                "exchange_order_id": r.exchange_order_id,
                "symbol": r.symbol,
                "side": r.side,
                "type": r.type,
                "price": r.price,
                "quantity": r.quantity,
                "status": r.status,
                "origin": r.origin,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )



# ==================================================================
# W2：浮动挂单 / 一键全平 / 一键反手
# ==================================================================

from dataclasses import replace  # noqa: E402

from ..security import verify_pin  # noqa: E402
from ..trading.chase import start_chase  # noqa: E402
from ..trading.floating import (  # noqa: E402
    ChaseConfig,
    FloatingOrderRequest,
    FloatingOrderService,
    GuardBlockedError,
    LaddersConfig,
)
from ..trading.guard import GuardService  # noqa: E402
from ..trading.killswitch import close_all, reverse  # noqa: E402
from ..db import User  # noqa: E402


# ---------- schemas ----------


class _ChaseIn(BaseModel):
    enabled: bool = False
    interval_sec: float = Field(default=5.0, ge=0.5, le=300)
    max_attempts: int = Field(default=3, ge=0, le=50)
    expire_sec: float | None = Field(default=None, ge=1, le=3600)
    on_expire: Literal["cancel", "market"] = "cancel"


class _LaddersIn(BaseModel):
    count: int = Field(default=1, ge=1, le=20)
    spacing_pct: str = "0"


class FloatingOrderIn(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    action: Literal["open", "close"]
    float_pct: str = Field(description="例如 '-0.001' 表示 mid 下 0.1%")
    qty_mode: Literal["usdt", "contract"] = "usdt"
    qty: str
    chase: _ChaseIn = Field(default_factory=_ChaseIn)
    ladders: _LaddersIn = Field(default_factory=_LaddersIn)
    tp_pct: str | None = None
    sl_pct: str | None = None
    reduce_only: bool = False
    ai_review: Literal["off", "weak", "strong"] = "off"
    leverage: int | None = Field(default=None, ge=1, le=125)
    time_in_force: Literal["GTC", "IOC", "FOK", "GTX"] = "GTC"


class _PinBody(BaseModel):
    pin: str = Field(min_length=4, max_length=64)


class CloseAllIn(_PinBody):
    pass


class ReverseIn(_PinBody):
    symbol: str


# ---------- helpers ----------


async def _verify_user_pin(s: DbSession, pin: str) -> None:
    u = (await s.execute(select(User).where(User.id == 1))).scalar_one_or_none()
    if u is None or not verify_pin(pin, u.pin_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="PIN 错误")


def _to_floating_req(payload: FloatingOrderIn) -> FloatingOrderRequest:
    return FloatingOrderRequest(
        symbol=payload.symbol,
        side=payload.side,
        action=payload.action,
        float_pct=Decimal(payload.float_pct),
        qty_mode=payload.qty_mode,
        qty=Decimal(payload.qty),
        chase=ChaseConfig(
            enabled=payload.chase.enabled,
            interval_sec=payload.chase.interval_sec,
            max_attempts=payload.chase.max_attempts,
            expire_sec=payload.chase.expire_sec,
            on_expire=payload.chase.on_expire,
        ),
        ladders=LaddersConfig(
            count=payload.ladders.count,
            spacing_pct=Decimal(payload.ladders.spacing_pct),
        ),
        tp_pct=None if payload.tp_pct is None else Decimal(payload.tp_pct),
        sl_pct=None if payload.sl_pct is None else Decimal(payload.sl_pct),
        reduce_only=payload.reduce_only,
        ai_review=payload.ai_review,
        leverage=payload.leverage,
        time_in_force=payload.time_in_force,
    )


# ---------- /orders/floating ----------


@router.post("/floating")
async def post_floating(
    payload: FloatingOrderIn,
    client: Binance,
    s: DbSession,
) -> dict:
    req = _to_floating_req(payload)
    guard = GuardService(s, client)
    svc = FloatingOrderService(s, client, guard)

    try:
        result = await svc.submit(req)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except GuardBlockedError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"护栏拒绝：{e}") from e
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    # 启动追价（对每个下单成功的档位启动一个 task）
    if req.chase.enabled:
        for lad in result.ladders:
            if lad.exchange_order_id:
                # 追价 task 单独针对一个档；若该档撤单则重挂时会生成新 client_order_id
                start_chase(client, req, lad.client_order_id, lad.price)

    return ok(
        {
            "mid_price": str(result.mid_price),
            "guard_warnings": result.guard_warnings,
            "ladders": [
                {
                    "idx": i,
                    "client_order_id": lad.client_order_id,
                    "exchange_order_id": lad.exchange_order_id,
                    "price": str(lad.price),
                    "quantity": str(lad.quantity),
                    "status": lad.status,
                    "error": lad.error,
                }
                for i, lad in enumerate(result.ladders)
            ],
            "chase_started": bool(
                req.chase.enabled and any(l.exchange_order_id for l in result.ladders)
            ),
        }
    )


# ---------- /orders/close-all ----------


@router.post("/close-all")
async def post_close_all(payload: CloseAllIn, client: Binance, s: DbSession) -> dict:
    # 破坏性：强制 PIN
    await _verify_user_pin(s, payload.pin)
    try:
        report = await close_all(client)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(
        {
            "canceled_symbols": report.canceled_symbols,
            "closed": [
                {
                    "symbol": c.symbol,
                    "side_closed": c.side_closed,
                    "quantity": str(c.quantity),
                    "ok": c.ok,
                    "error": c.error,
                }
                for c in report.closed
            ],
            "errors": report.errors,
            "all_ok": report.all_ok,
        }
    )


# ---------- /orders/reverse ----------


@router.post("/reverse")
async def post_reverse(payload: ReverseIn, client: Binance, s: DbSession) -> dict:
    await _verify_user_pin(s, payload.pin)
    try:
        r = await reverse(client, payload.symbol)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(
        {
            "symbol": r.symbol,
            "closed": None
            if r.closed is None
            else {
                "symbol": r.closed.symbol,
                "side_closed": r.closed.side_closed,
                "quantity": str(r.closed.quantity),
                "ok": r.closed.ok,
                "error": r.closed.error,
            },
            "reopened_ok": r.reopened_ok,
            "reopened_qty": str(r.reopened_qty),
            "reopened_side": r.reopened_side,
            "error": r.error,
        }
    )
