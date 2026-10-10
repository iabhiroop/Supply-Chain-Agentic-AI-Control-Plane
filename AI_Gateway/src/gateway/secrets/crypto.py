"""Symmetric encryption for provider API keys stored at rest."""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache


def _fernet(secret: str):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, secret: str) -> str:
    if not plaintext:
        return ""
    f = _fernet(secret)
    if f is not None:
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    nonce = os.urandom(16)
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    data = plaintext.encode("utf-8")
    cipher = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(nonce + cipher).decode("utf-8")


def decrypt_secret(token: str, secret: str) -> str:
    if not token:
        return ""
    f = _fernet(secret)
    if f is not None:
        try:
            return f.decrypt(token.encode("utf-8")).decode("utf-8")
        except Exception:
            pass
    raw = base64.urlsafe_b64decode(token.encode("utf-8"))
    nonce, cipher = raw[:16], raw[16:]
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(cipher)).decode("utf-8")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****" + value[-2:]
    return value[:4] + "…" + value[-4:]


@lru_cache
def derived_app_secret(raw: str) -> str:
    return raw or "dev-insecure-gateway-secret"
