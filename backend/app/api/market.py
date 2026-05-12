"""行情路由：ticker / klines / depth / symbols（全部代理币安）."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from ..exchanges.binance import BinanceError
from ..exchanges.precision import registry
from .deps import Binance, ok

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/symbols")
async def symbols_view() -> dict:
    """返回已预热的可交易 symbol 列表与精度."""
    syms = registry.all_symbols()
    details = []
    for s in syms:
        info = registry.get(s)
        details.append(
            {
                "symbol": info.symbol,
                "tickSize": str(info.tick_size),
                "stepSize": str(info.step_size),
                "minQty": str(info.min_qty),
                "minNotional": str(info.min_notional),
                "pricePrecision": info.price_precision,
                "quantityPrecision": info.quantity_precision,
            }
        )
    return ok(details)


@router.get("/ticker")
async def ticker_view(symbol: str, client: Binance) -> dict:
    try:
        data = await client.book_ticker(symbol)
    except BinanceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e
    return ok(data)


KLineInterval = Literal[
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"
]


@router.get("/klines")
async def klines_view(
    client: Binance,
    symbol: str,
    interval: KLineInterval = "1m",
    limit: int = Query(default=500, ge=1, le=1500),
) -> dict:
    try:
        data = await client.klines(symbol, interval=interval, limit=limit)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(data)


@router.get("/depth")
async def depth_view(
    client: Binance,
    symbol: str,
    limit: int = Query(default=50, ge=5, le=1000),
) -> dict:
    try:
        data = await client.depth(symbol, limit=limit)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return ok(data)


@router.get("/positions")
async def positions_view(client: Binance, symbol: str | None = None) -> dict:
    """持仓查询（放这里是因为 W1 还没建独立的 positions 路由）."""
    try:
        data = await client.position_risk(symbol)
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    # 只过滤非零仓位，前端要全量可带 all=true
    filtered = [p for p in data if float(p.get("positionAmt", "0")) != 0.0]
    return ok({"all": data, "open": filtered})


@router.get("/balance")
async def balance_view(client: Binance) -> dict:
    try:
        data = await client.balance()
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    # 只留 USDT/BUSD/USDC 等稳定币
    return ok(data)
