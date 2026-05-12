"""安全模块：PIN hash + 会话 + Fernet (用 PIN 派生的密钥) 加解密 API Key."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

# ------------------------------------------------------------------
# PIN hash
# ------------------------------------------------------------------

_ph = PasswordHasher()


def hash_pin(pin: str) -> str:
    """用 argon2 hash PIN，返回字符串供落库."""
    if not pin:
        raise ValueError("PIN 不能为空")
    return _ph.hash(pin)


def verify_pin(pin: str, hashed: str) -> bool:
    """校验 PIN。任何异常一律视为失败，绝不抛给调用方."""
    if not pin or not hashed:
        return False
    try:
        return _ph.verify(hashed, pin)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


# ------------------------------------------------------------------
# Fernet 密钥派生：PIN + salt -> 32B -> base64url
# ------------------------------------------------------------------

# scrypt 参数：N=2**14 约 16MB 内存，对 PIN 这种低熵值足够抵挡离线爆破
# （mainnet 场景仍建议使用强 PIN 并开 IP 白名单）
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32


def derive_fernet_key(pin: str, salt: bytes) -> bytes:
    """从 PIN + salt 派生出 Fernet 兼容的 32B base64url 密钥."""
    if not pin:
        raise ValueError("PIN 不能为空")
    if len(salt) < 16:
        raise ValueError("salt 至少 16 字节")
    raw = hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return base64.urlsafe_b64encode(raw)


def new_salt() -> bytes:
    """产生一个 16 字节随机 salt。保存到 DB，与密文一一对应."""
    return secrets.token_bytes(16)


# ------------------------------------------------------------------
# Fernet 加解密
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Cipher:
    """一个加密器实例（运行时持有），封装 Fernet."""

    key: bytes

    def encrypt(self, plaintext: str) -> str:
        token = Fernet(self.key).encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> str:
        try:
            plain = Fernet(self.key).decrypt(token.encode("utf-8"))
        except InvalidToken as e:
            raise ValueError("Fernet 解密失败，可能 PIN 错误或密文损坏") from e
        return plain.decode("utf-8")


def cipher_from_pin(pin: str, salt: bytes) -> Cipher:
    """根据 PIN + salt 构造 Cipher."""
    return Cipher(key=derive_fernet_key(pin, salt))


# ------------------------------------------------------------------
# 会话密钥
# ------------------------------------------------------------------


def ensure_session_secret(existing: str) -> str:
    """若 .env 里没配 SESSION_SECRET 则内存生成一个."""
    if existing:
        return existing
    # 仅内存保存；重启失效会踢登录，这正是 V2 的安全取舍
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
