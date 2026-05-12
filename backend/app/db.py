"""SQLite async engine + 全部 SQLAlchemy 模型.

W1 只建立必要的表：users / api_keys / orders / guard_rules.
W2+ 再加 positions / trades / strategies / strategy_runs / ai_providers / ai_logs / audit_logs.
金额一律用字符串存（DECIMAL），读出来转 Decimal，**永远不用 float**。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings

# ------------------------------------------------------------------
# Engine / Session
# ------------------------------------------------------------------

_settings = get_settings()

# SQLite 文件目录：确保存在
_settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    future=True,
    # aiosqlite 下的连接参数，避免多线程警告
    connect_args={"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {},
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用."""
    async with SessionLocal() as session:
        yield session


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """SQLAlchemy Declarative Base."""


# ------------------------------------------------------------------
# 模型
# ------------------------------------------------------------------


class User(Base):
    """单用户系统，固定 id=1."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pin_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    # Fernet 密钥派生 salt（base64url 字符串）
    fernet_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)


class ApiKey(Base):
    """Binance API Key（加密存）.

    一个用户可以同时持有 testnet 和 mainnet，两条独立记录。
    """

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "env", name="uq_apikey_user_env"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    env: Mapped[str] = mapped_column(String(16), nullable=False)  # testnet / mainnet
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Fernet 密文（base64url 文本）
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Order(Base):
    """订单流水（W1 只覆盖普通限价单，浮动相关字段先留空）."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 幂等 client order id
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), default=None)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY / SELL
    position_side: Mapped[str] = mapped_column(String(8), nullable=False, default="BOTH")
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # LIMIT / MARKET / STOP_MARKET
    time_in_force: Mapped[str | None] = mapped_column(String(8), default=None)

    price: Mapped[str | None] = mapped_column(String(32), default=None)  # Decimal 字符串
    quantity: Mapped[str] = mapped_column(String(32), nullable=False)  # Decimal 字符串
    reduce_only: Mapped[bool] = mapped_column(default=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NEW")

    # 浮动挂单元数据（W2+ 填）
    float_pct: Mapped[str | None] = mapped_column(String(16), default=None)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    strategy_run_id: Mapped[int | None] = mapped_column(Integer, default=None)

    # 止盈止损附属单（W2+ 填）
    sl_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    tp_order_id: Mapped[str | None] = mapped_column(String(64), default=None)

    # 原始 payload / 响应 （调试/审计用）
    raw_request: Mapped[str | None] = mapped_column(Text, default=None)
    raw_response: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class GuardRules(Base):
    """硬性防手抖护栏（单行 JSON，id=1）.

    与 v1 的 risk_rules 不同：这里 **没有日亏损熔断**，只有护栏。
    """

    __tablename__ = "guard_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class Strategy(Base):
    """策略定义（模板）.

    实际运行由 :class:`StrategyRun` 承载。一个 Strategy 可创建 N 个 run。
    """

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 策略类型（对应 StrategyRegistry 里注册的 key，如 "grid" / "ma_cross" / "ai_signal"）
    type: Mapped[str] = mapped_column(String(32), nullable=False)

    # 参数（JSON 字符串；和 params_schema 对齐）
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 止损配置（JSON 字符串；★ 不可为空，启动时强校验，见 runner.start）
    stoploss_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 运行 symbols（策略作用的币对列表；JSON 数组字符串）
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    is_template: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class StrategyRun(Base):
    """策略运行实例（asyncio task 生命周期承载）.

    V2 §五.3：每个 run 是一个独立 asyncio task；10s 写心跳；超 30s 标记 stale。
    """

    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # running / paused / stopped / error / stale
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="stopped")

    # 启动时的 snapshot（防运行期间策略定义被改动）
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    stoploss_json: Mapped[str] = mapped_column(Text, nullable=False)
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False)

    # asyncio task name（便于排障）
    task_name: Mapped[str | None] = mapped_column(String(64), default=None)

    started_at: Mapped[datetime | None] = mapped_column(default=None)
    stopped_at: Mapped[datetime | None] = mapped_column(default=None)
    last_heartbeat: Mapped[datetime | None] = mapped_column(default=None)

    # 运行指标 JSON（PnL / 成交数 / 触发 SL 次数 / 最后错误等）
    stats_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


# ------------------------------------------------------------------
# 默认护栏配置（V2 附录 B）
# ------------------------------------------------------------------

DEFAULT_GUARD_RULES: dict = {
    "per_order_max_usdt": 500,
    "global_max_position_usdt": 5000,
    "global_max_leverage": 10,
    "allowed_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "forbidden_symbols": [],
    "ai_review_default": "off",
    "require_pin_for": ["close-all", "reverse", "mainnet-switch", "key-delete"],
}


# ------------------------------------------------------------------
# 初始化
# ------------------------------------------------------------------


async def init_db() -> None:
    """建表 + 写入默认护栏（若尚未初始化）."""
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        row = (await s.execute(select(GuardRules).limit(1))).scalar_one_or_none()
        if row is None:
            s.add(GuardRules(rules_json=json.dumps(DEFAULT_GUARD_RULES)))
            await s.commit()
