"""安全模块单测：PIN hash、Fernet 密钥派生、加解密."""

from __future__ import annotations

import pytest

from app.security import (
    cipher_from_pin,
    derive_fernet_key,
    hash_pin,
    new_salt,
    verify_pin,
)


class TestPinHash:
    def test_hash_verify_ok(self):
        h = hash_pin("123456")
        assert verify_pin("123456", h) is True

    def test_hash_verify_wrong(self):
        h = hash_pin("123456")
        assert verify_pin("654321", h) is False

    def test_hash_changes_each_call(self):
        # argon2 自带 salt，两次 hash 应不同
        assert hash_pin("abc") != hash_pin("abc")

    def test_empty_pin_rejected(self):
        with pytest.raises(ValueError):
            hash_pin("")

    def test_verify_empty(self):
        assert verify_pin("", "$argon2id$xxx") is False
        assert verify_pin("abc", "") is False

    def test_verify_invalid_hash(self):
        # 不是合法 argon2 hash，应稳妥地返回 False 而非抛异常
        assert verify_pin("abc", "not-a-hash") is False


class TestKeyDerivation:
    def test_same_pin_same_salt_yields_same_key(self):
        salt = new_salt()
        k1 = derive_fernet_key("1234", salt)
        k2 = derive_fernet_key("1234", salt)
        assert k1 == k2

    def test_different_salt_yields_different_key(self):
        k1 = derive_fernet_key("1234", new_salt())
        k2 = derive_fernet_key("1234", new_salt())
        assert k1 != k2

    def test_different_pin_yields_different_key(self):
        salt = new_salt()
        k1 = derive_fernet_key("1234", salt)
        k2 = derive_fernet_key("12345", salt)
        assert k1 != k2

    def test_short_salt_rejected(self):
        with pytest.raises(ValueError):
            derive_fernet_key("1234", b"short")

    def test_empty_pin_rejected(self):
        with pytest.raises(ValueError):
            derive_fernet_key("", new_salt())


class TestCipher:
    def test_roundtrip(self):
        salt = new_salt()
        c = cipher_from_pin("1234", salt)
        token = c.encrypt("binance-secret-xyz")
        assert c.decrypt(token) == "binance-secret-xyz"

    def test_wrong_pin_fails(self):
        salt = new_salt()
        good = cipher_from_pin("1234", salt)
        bad = cipher_from_pin("9999", salt)
        token = good.encrypt("hello")
        with pytest.raises(ValueError):
            bad.decrypt(token)

    def test_tampered_token_fails(self):
        salt = new_salt()
        c = cipher_from_pin("1234", salt)
        token = c.encrypt("hello")
        # 改掉中间一个字节（避开尾部可能的 base64 padding 容忍）
        mid = len(token) // 2
        flipped = "A" if token[mid] != "A" else "B"
        tampered = token[:mid] + flipped + token[mid + 1 :]
        assert tampered != token  # sanity
        with pytest.raises(ValueError):
            c.decrypt(tampered)

    def test_truncated_token_fails(self):
        salt = new_salt()
        c = cipher_from_pin("1234", salt)
        token = c.encrypt("hello")
        with pytest.raises(ValueError):
            c.decrypt(token[:-8])  # 截掉尾部 8 字节必坏 HMAC
