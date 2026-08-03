from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def encrypt_secret(value: str, secret_key: str) -> str:
    normalized = str(value)
    if not normalized:
        raise ValueError("secret value is required")
    return _fernet(secret_key).encrypt(normalized.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, secret_key: str) -> str:
    try:
        return _fernet(secret_key).decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("stored supplier credential cannot be decrypted") from exc


def _fernet(secret_key: str) -> Fernet:
    normalized = str(secret_key or "")
    if not normalized:
        raise ValueError("application secret key is required")
    key = base64.urlsafe_b64encode(hashlib.sha256(normalized.encode("utf-8")).digest())
    return Fernet(key)
