"""API Key 管理：加密写入 DB、绑定当前 BINANCE_ENV.

W1 为了能下单，必须能配 Key。这里提供最小接口：
  - POST /keys    写入/更新 当前 env 的 Key（会立即重建 runtime.binance_client）
  - GET  /keys    列出已有 Key（仅 env/label/掩码前后 4 位）
  - DELETE /keys  删除当前 env 的 Key
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import runtime
from ..config import get_settings
from ..db import ApiKey
from ..exchanges.binance import BinanceClient
from .deps import DbSession, ok

router = APIRouter(prefix="/keys", tags=["keys"])


class ApiKeyPayload(BaseModel):
    api_key: str = Field(min_length=8)
    api_secret: str = Field(min_length=8)
    label: str = ""
    env: str | None = None  # 不传则用当前 BINANCE_ENV


def _mask(s: str) -> str:
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]}"


@router.get("")
async def list_keys(s: DbSession) -> dict:
    rows = (await s.execute(select(ApiKey))).scalars().all()

    cipher = runtime.cipher
    result = []
    for r in rows:
        try:
            key = cipher.decrypt(r.api_key_encrypted) if cipher else ""
        except Exception:
            key = ""
        result.append(
            {
                "id": r.id,
                "env": r.env,
                "label": r.label,
                "api_key_masked": _mask(key) if key else "<加密未解锁>",
                "created_at": r.created_at.isoformat(),
            }
        )
    return ok(result)


@router.post("")
async def put_key(payload: ApiKeyPayload, s: DbSession) -> dict:
    settings = get_settings()
    env = (payload.env or settings.binance_env).lower()
    if env not in {"testnet", "mainnet"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="env 必须是 testnet 或 mainnet")

    cipher = runtime.cipher
    if cipher is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="尚未解锁 Cipher，请先 /auth/login"
        )

    enc_key = cipher.encrypt(payload.api_key)
    enc_secret = cipher.encrypt(payload.api_secret)

    row = (
        await s.execute(select(ApiKey).where(ApiKey.user_id == 1, ApiKey.env == env))
    ).scalar_one_or_none()
    if row is None:
        row = ApiKey(
            user_id=1,
            env=env,
            label=payload.label,
            api_key_encrypted=enc_key,
            api_secret_encrypted=enc_secret,
        )
        s.add(row)
    else:
        row.api_key_encrypted = enc_key
        row.api_secret_encrypted = enc_secret
        row.label = payload.label or row.label
    await s.commit()
    await s.refresh(row)

    # 若写入的是当前 env，立即重建 client
    if env == settings.binance_env:
        if runtime.binance_client is not None:
            await runtime.binance_client.close()
        runtime.binance_client = BinanceClient(
            base_url=settings.rest_base,
            api_key=payload.api_key,
            api_secret=payload.api_secret,
        )
        try:
            await runtime.binance_client.sync_time()
        except Exception:
            pass

    return ok(
        {
            "id": row.id,
            "env": row.env,
            "label": row.label,
            "api_key_masked": _mask(payload.api_key),
        }
    )


@router.delete("")
async def delete_key(s: DbSession, env: str | None = None) -> dict:
    settings = get_settings()
    env = (env or settings.binance_env).lower()
    row = (
        await s.execute(select(ApiKey).where(ApiKey.user_id == 1, ApiKey.env == env))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"未找到 env={env} 的 Key")
    await s.delete(row)
    await s.commit()

    if env == settings.binance_env and runtime.binance_client is not None:
        await runtime.binance_client.close()
        runtime.binance_client = BinanceClient(base_url=settings.rest_base)

    return ok({"deleted": True, "env": env})
