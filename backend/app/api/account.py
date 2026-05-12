"""账户概览：balance + positions + open orders + running strategies 一次拉.

给前端首页用，避免并发多次 REST 调用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..db import Strategy, StrategyRun
from ..exchanges.binance import BinanceError
from .deps import Binance, DbSession, ok

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/summary")
async def summary(client: Binance, s: DbSession) -> dict:
    # 币安账户
    try:
        balances = await client.balance()
        account = await client.account()
        positions_raw = await client.position_risk()
        open_orders = await client.open_orders()
    except BinanceError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    # USDT 余额
    usdt = next(
        (b for b in balances if b.get("asset") == "USDT"), {"balance": "0", "availableBalance": "0"}
    )

    # 有仓位过滤
    open_positions = [p for p in positions_raw if float(p.get("positionAmt", "0")) != 0.0]

    # 策略 runs
    running_runs = (
        (await s.execute(select(StrategyRun).where(StrategyRun.status == "running")))
        .scalars()
        .all()
    )
    strategies_list = (await s.execute(select(Strategy))).scalars().all()
    strat_map = {x.id: x for x in strategies_list}

    return ok(
        {
            "balance": {
                "usdt_total": usdt.get("balance", "0"),
                "usdt_available": usdt.get("availableBalance", "0"),
                "total_wallet": account.get("totalWalletBalance"),
                "total_unrealized_pnl": account.get("totalUnrealizedProfit"),
                "total_margin_balance": account.get("totalMarginBalance"),
            },
            "open_positions": open_positions,
            "open_orders": open_orders,
            "running_strategies": [
                {
                    "run_id": r.id,
                    "strategy_id": r.strategy_id,
                    "strategy_name": strat_map[r.strategy_id].name
                    if r.strategy_id in strat_map
                    else "?",
                    "strategy_type": strat_map[r.strategy_id].type
                    if r.strategy_id in strat_map
                    else "?",
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "last_heartbeat": r.last_heartbeat.isoformat() if r.last_heartbeat else None,
                }
                for r in running_runs
            ],
        }
    )
