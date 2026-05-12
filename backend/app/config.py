"""全局配置：读 .env + 提供币安 endpoint 的 testnet/mainnet 默认值."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根（backend/.. = binance-fast-trade/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{(DEFAULT_DATA_DIR / 'app.db').as_posix()}"


# 币安 endpoint 默认表
_BINANCE_ENDPOINTS: dict[str, dict[str, str]] = {
    "testnet": {
        "rest": "https://testnet.binancefuture.com",
        "ws": "wss://stream.binancefuture.com",
    },
    "mainnet": {
        "rest": "https://fapi.binance.com",
        "ws": "wss://fstream.binance.com",
    },
}


class Settings(BaseSettings):
    """运行时设置.

    所有字段都可从环境变量或 `.env` 读取。字段优先级：
        env var > .env > 默认值。
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 运行环境 ----
    binance_env: Literal["testnet", "mainnet"] = "testnet"

    # ---- 服务 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # ---- 会话 ----
    # 为空则在运行时自动派生一个（仅内存，重启失效会踢登录）
    session_secret: str = ""

    # ---- PIN ----
    app_pin: str = ""  # 仅首次初始化使用；设置后应清空
    app_pin_hash: str = ""  # argon2 hash，正式存 DB，这里只当冷备

    # ---- 币安 API Key (Fernet 密文，运维备份用；真源在 DB.api_keys) ----
    binance_api_key_encrypted: str = ""
    binance_api_secret_encrypted: str = ""

    # ---- 币安 endpoint 覆盖 ----
    binance_rest_base: str = ""
    binance_ws_base: str = ""

    # ---- 数据库 ----
    database_url: str = DEFAULT_DB_URL

    # ---- AI / TG （W5、W6 用，这里先占位） ----
    litellm_provider: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    tg_bot_token: str = ""
    tg_chat_id: str = ""

    # ---- mainnet 二次确认 (CLI --confirm-mainnet，由 main.py 解析后设置) ----
    confirm_mainnet: bool = Field(default=False, exclude=True)

    # ----------- 校验器 -----------

    @field_validator("database_url", mode="before")
    @classmethod
    def _empty_database_url_to_default(cls, v: str | None) -> str:
        """.env 里 ``DATABASE_URL=`` 空值视为用默认，避免 aiosqlite 解析出空路径."""
        if v is None or str(v).strip() == "":
            return DEFAULT_DB_URL
        return str(v)

    # ----------- 派生属性 -----------
    @property
    def is_mainnet(self) -> bool:
        return self.binance_env == "mainnet"

    @property
    def rest_base(self) -> str:
        if self.binance_rest_base:
            return self.binance_rest_base.rstrip("/")
        return _BINANCE_ENDPOINTS[self.binance_env]["rest"]

    @property
    def ws_base(self) -> str:
        if self.binance_ws_base:
            return self.binance_ws_base.rstrip("/")
        return _BINANCE_ENDPOINTS[self.binance_env]["ws"]

    @property
    def data_dir(self) -> Path:
        """SQLite 和日志写入目录（固定 PROJECT_ROOT/data/）."""
        return DEFAULT_DATA_DIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例."""
    return Settings()
