"""★ 手动浮动挂单核心.

按 V2 §五.1 的流程：
    1. 读进程内 ``tickers[symbol]`` 拿最新价（WS 没推到时 REST 兜底）
    2. 护栏校验 ``guard.check_order()``
    3. （W5 再接）AI 审查
    4. 按 ``float_pct`` 算价 + tickSize 取整（BUY 向下、SELL 向上，保守）
    5. 生成 ``client_order_id = fl-{uuid}`` 保证幂等
    6. REST 下单 → 落 DB → 推 WS
    7. ladders.count > 1 时循环挂 N 档
    8. chase.enabled 时创建追价 task
    9. TP/SL：成交回调挂对向 STOP_MARKET（依赖 user stream，W2 末补充）

关键不变式：
    - 价格、数量、百分比 **全部用 Decimal**，禁用 float
    - snap_price 用 BUY 向下 / SELL 向上，避免追价到下一 tick
    - client_order_id 形如 ``fl-<ladder_idx>-<uuid8>``，方便 WS 回调识别档位
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime
from ..db import Order
from ..exchanges.binance import BinanceClient, BinanceError
from ..exchanges.precision import (
    SymbolInfo,
    check_min_notional,
    quantity_from_usdt,
    registry,
    snap_price,
    snap_quantity,
)
from .guard import GuardCheck, GuardOrderInput, GuardService


# ==================================================================
# Schemas
# ==================================================================


Side = Literal["long", "short"]
Action = Literal["open", "close"]
QtyMode = Literal["usdt", "contract"]
OnExpire = Literal["cancel", "market"]


@dataclass
class ChaseConfig:
    enabled: bool = False
    interval_sec: float = 5.0
    max_attempts: int = 3
    expire_sec: float | None = None
    on_expire: OnExpire = "cancel"


@dataclass
class LaddersConfig:
    count: int = 1
    spacing_pct: Decimal = Decimal("0")  # 档间距，相对 mid 的比例
    distribution: Literal["equal"] = "equal"  # 预留扩展


@dataclass
class FloatingOrderRequest:
    symbol: str
    side: Side
    action: Action
    float_pct: Decimal  # 例如 -0.001 = mid 下 0.1%
    qty_mode: QtyMode
    qty: Decimal  # usdt 模式：USDT 金额；contract 模式：合约张数
    chase: ChaseConfig = field(default_factory=ChaseConfig)
    ladders: LaddersConfig = field(default_factory=LaddersConfig)
    tp_pct: Decimal | None = None
    sl_pct: Decimal | None = None
    reduce_only: bool = False
    ai_review: Literal["off", "weak", "strong"] = "off"
    leverage: int | None = None  # 非空则先调 setLeverage
    time_in_force: Literal["GTC", "IOC", "FOK", "GTX"] = "GTC"


@dataclass
class PlacedLadder:
    client_order_id: str
    exchange_order_id: str | None
    price: Decimal
    quantity: Decimal
    status: str
    exchange_response: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class FloatingOrderResult:
    request: FloatingOrderRequest
    mid_price: Decimal
    ladders: list[PlacedLadder]
    guard_warnings: list[str] = field(default_factory=list)

    @property
    def any_placed(self) -> bool:
        return any(p.exchange_order_id for p in self.ladders)


# ==================================================================
# 辅助函数
# ==================================================================


def _new_client_id(ladder_idx: int = 0) -> str:
    """生成浮动挂单 client_order_id： ``fl-<idx>-<uuid8>`` ."""
    return f"fl-{ladder_idx}-{uuid.uuid4().hex[:12]}"


async def _get_mid_price(client: BinanceClient, symbol: str) -> Decimal:
    """优先从 WS tickers 读；没有则 REST bookTicker 兜底."""
    tk = runtime.tickers.get(symbol.upper())
    if tk:
        bid = Decimal(str(tk.get("bid", "0") or "0"))
        ask = Decimal(str(tk.get("ask", "0") or "0"))
        if bid > 0 and ask > 0:
            return (bid + ask) / 2

    rest = await client.book_ticker(symbol)
    bid = Decimal(str(rest.get("bidPrice", "0")))
    ask = Decimal(str(rest.get("askPrice", "0")))
    if bid <= 0 or ask <= 0:
        raise BinanceError(-1000, f"{symbol} bookTicker 无有效买卖价", 0)
    # 顺手写回进程缓存供后续使用
    runtime.tickers[symbol.upper()] = {
        "bid": str(bid),
        "ask": str(ask),
        "ts": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }
    return (bid + ask) / 2


def _resolve_order_side(side: Side, action: Action) -> str:
    """把 (long|short, open|close) 映射到 BUY/SELL.

    - long+open  = BUY       - long+close = SELL
    - short+open = SELL      - short+close = BUY
    """
    if (side == "long" and action == "open") or (side == "short" and action == "close"):
        return "BUY"
    return "SELL"


def _ladder_prices(mid: Decimal, req: FloatingOrderRequest, info: SymbolInfo) -> list[Decimal]:
    """计算每一档的限价（已吸附 tickSize）.

    档位 0：base_price = mid * (1 + float_pct)
    档位 i (i>=1)：相对 base，按方向继续叠加 spacing_pct 向"更难成交"一侧推
        - BUY  方向 (long+open / short+close)：价格继续下移
        - SELL 方向 (short+open / long+close)：价格继续上移
    """
    order_side = _resolve_order_side(req.side, req.action)
    base = mid * (Decimal("1") + req.float_pct)

    prices: list[Decimal] = []
    for i in range(max(1, req.ladders.count)):
        if i == 0:
            p = base
        else:
            # 每档继续朝"更远"挂
            step = mid * req.ladders.spacing_pct * Decimal(i)
            if order_side == "BUY":
                p = base - step
            else:
                p = base + step
        prices.append(snap_price(info, p, side=order_side))
    return prices


def _ladder_quantity(info: SymbolInfo, price: Decimal, req: FloatingOrderRequest, ladder_count: int) -> Decimal:
    """把总量按 equal 切成每档量（吸附 stepSize）.

    usdt 模式：每档分到 qty/ladder_count USDT，再除以该档价换算张数
    contract 模式：每档分到 qty/ladder_count 张
    """
    n = Decimal(max(1, ladder_count))
    if req.qty_mode == "usdt":
        per = req.qty / n
        return quantity_from_usdt(info, price, per)
    else:
        per = req.qty / n
        return snap_quantity(info, per)


# ==================================================================
# 核心入口
# ==================================================================


class FloatingOrderService:
    """浮动挂单编排器."""

    def __init__(
        self,
        session: AsyncSession,
        client: BinanceClient,
        guard: GuardService,
    ) -> None:
        self._s = session
        self._c = client
        self._g = guard

    async def submit(self, req: FloatingOrderRequest) -> FloatingOrderResult:
        symbol = req.symbol.upper()
        if not registry.has(symbol):
            raise ValueError(f"未知 symbol：{symbol}")
        info = registry.get(symbol)

        # 1) 拿 mid
        mid = await _get_mid_price(self._c, symbol)

        # 2) （可选）调杠杆
        if req.leverage is not None:
            try:
                await self._c.set_leverage(symbol, req.leverage)
            except BinanceError:
                # 杠杆不是每次必改，失败不致命；后续护栏还会校验
                pass

        # 3) 计算每档价格 + 数量
        prices = _ladder_prices(mid, req, info)
        plan: list[tuple[Decimal, Decimal]] = []
        for p in prices:
            q = _ladder_quantity(info, p, req, len(prices))
            plan.append((p, q))

        # 4) 护栏一次性校验（按所有档总名义金额）
        total_notional = sum((p * q for p, q in plan), start=Decimal("0"))
        check: GuardCheck = await self._g.check_order(
            GuardOrderInput(
                symbol=symbol,
                side=_resolve_order_side(req.side, req.action),
                notional_usdt=total_notional,
                leverage=req.leverage,
                reduce_only=req.reduce_only,
            )
        )
        if not check.allow:
            raise GuardBlockedError(check.reason)

        # 5) 逐档下单
        placed: list[PlacedLadder] = []
        order_side = _resolve_order_side(req.side, req.action)
        reduce_only = req.reduce_only or (req.action == "close")

        for idx, (price, qty) in enumerate(plan):
            if qty <= 0:
                placed.append(
                    PlacedLadder(
                        client_order_id="",
                        exchange_order_id=None,
                        price=price,
                        quantity=qty,
                        status="SKIPPED",
                        error=f"数量为 0（min_qty={info.min_qty} / step={info.step_size}）",
                    )
                )
                continue
            if not check_min_notional(info, price, qty):
                placed.append(
                    PlacedLadder(
                        client_order_id="",
                        exchange_order_id=None,
                        price=price,
                        quantity=qty,
                        status="SKIPPED",
                        error=f"名义 {price * qty} < min_notional {info.min_notional}",
                    )
                )
                continue

            cid = _new_client_id(idx)
            db_order = Order(
                client_order_id=cid,
                symbol=symbol,
                side=order_side,
                position_side="BOTH",
                type="LIMIT",
                time_in_force=req.time_in_force,
                price=str(price),
                quantity=str(qty),
                reduce_only=reduce_only,
                status="PENDING",
                origin="floating",
                float_pct=str(req.float_pct),
                raw_request=json.dumps(
                    {"ladder_idx": idx, "request": _req_to_dict(req)},
                    ensure_ascii=False,
                ),
            )
            self._s.add(db_order)
            await self._s.commit()
            await self._s.refresh(db_order)

            try:
                resp = await self._c.place_order(
                    symbol=symbol,
                    side=order_side,  # type: ignore[arg-type]
                    order_type="LIMIT",
                    quantity=str(qty),
                    price=str(price),
                    time_in_force=req.time_in_force,
                    reduce_only=reduce_only,
                    client_order_id=cid,
                )
            except BinanceError as e:
                db_order.status = "REJECTED"
                db_order.raw_response = json.dumps({"code": e.code, "msg": e.message})
                await self._s.commit()
                placed.append(
                    PlacedLadder(
                        client_order_id=cid,
                        exchange_order_id=None,
                        price=price,
                        quantity=qty,
                        status="REJECTED",
                        error=str(e),
                    )
                )
                continue

            db_order.status = resp.get("status", "NEW")
            db_order.exchange_order_id = str(resp.get("orderId"))
            db_order.raw_response = json.dumps(resp, ensure_ascii=False, default=str)
            await self._s.commit()

            placed.append(
                PlacedLadder(
                    client_order_id=cid,
                    exchange_order_id=db_order.exchange_order_id,
                    price=price,
                    quantity=qty,
                    status=db_order.status,
                    exchange_response=resp,
                )
            )

        # 6) 追价：见 chase.py，API 层拿到结果后会启动 task
        # 7) TP/SL：见 killswitch/handlers（成交事件由 user stream 触发，W2 末补）

        return FloatingOrderResult(
            request=req, mid_price=mid, ladders=placed, guard_warnings=check.warnings
        )


# ==================================================================
# Exceptions
# ==================================================================


class GuardBlockedError(Exception):
    """护栏拦截。API 层应翻译成 400."""


# ==================================================================
# helpers
# ==================================================================


def _req_to_dict(req: FloatingOrderRequest) -> dict[str, Any]:
    """Decimal → str 序列化."""
    return {
        "symbol": req.symbol,
        "side": req.side,
        "action": req.action,
        "float_pct": str(req.float_pct),
        "qty_mode": req.qty_mode,
        "qty": str(req.qty),
        "chase": {
            "enabled": req.chase.enabled,
            "interval_sec": req.chase.interval_sec,
            "max_attempts": req.chase.max_attempts,
            "expire_sec": req.chase.expire_sec,
            "on_expire": req.chase.on_expire,
        },
        "ladders": {
            "count": req.ladders.count,
            "spacing_pct": str(req.ladders.spacing_pct),
        },
        "tp_pct": None if req.tp_pct is None else str(req.tp_pct),
        "sl_pct": None if req.sl_pct is None else str(req.sl_pct),
        "reduce_only": req.reduce_only,
        "ai_review": req.ai_review,
        "leverage": req.leverage,
        "time_in_force": req.time_in_force,
    }
