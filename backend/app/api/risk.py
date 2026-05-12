"""护栏（guard）路由：仅管硬性护栏，不做自动熔断.

V2 §五.5 的设计：删除 daily_loss_limit_usdt 和 circuit_breaker，
只保留单笔上限、总仓上限、杠杆、白/黑名单、破坏性操作 PIN。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import DEFAULT_GUARD_RULES, GuardRules
from .deps import DbSession, ok

# V2 约定 URL 前缀：/guard/rules（不再沿用 v1 的 /risk/rules）
router = APIRouter(prefix="/guard", tags=["guard"])


class GuardRulesPayload(BaseModel):
    per_order_max_usdt: float = Field(ge=0)
    global_max_position_usdt: float = Field(ge=0)
    global_max_leverage: int = Field(ge=1, le=125)
    allowed_symbols: list[str]
    forbidden_symbols: list[str] = []
    ai_review_default: str = "off"
    require_pin_for: list[str] = Field(
        default=["close-all", "reverse", "mainnet-switch", "key-delete"]
    )


async def _get_row(s: DbSession) -> GuardRules:
    row = (await s.execute(select(GuardRules).limit(1))).scalar_one_or_none()
    if row is None:
        row = GuardRules(rules_json=json.dumps(DEFAULT_GUARD_RULES))
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


@router.get("/rules")
async def get_rules(s: DbSession) -> dict:
    row = await _get_row(s)
    try:
        rules = json.loads(row.rules_json)
    except json.JSONDecodeError:
        rules = DEFAULT_GUARD_RULES
    return ok(rules)


@router.put("/rules")
async def put_rules(payload: GuardRulesPayload, s: DbSession) -> dict:
    row = await _get_row(s)
    row.rules_json = json.dumps(payload.model_dump(), ensure_ascii=False)
    await s.commit()
    return ok(payload.model_dump())


@router.post("/reset")
async def reset_rules(s: DbSession) -> dict:
    row = await _get_row(s)
    row.rules_json = json.dumps(DEFAULT_GUARD_RULES)
    await s.commit()
    return ok(DEFAULT_GUARD_RULES, msg="已重置为默认护栏")
