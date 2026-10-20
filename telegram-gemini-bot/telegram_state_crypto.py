"""Authenticated encryption shared by the small Telegram workflow state files."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import subprocess


class StateCryptoError(RuntimeError):
    pass


def encrypt(payload: bytes, token: str, magic: bytes) -> bytes:
    if not token:
        raise StateCryptoError("missing encryption key")
    derived = hmac.new(token.encode("utf-8"), magic, hashlib.sha512).digest()
    encryption_key, authentication_key = derived[:32], derived[32:]
    iv = secrets.token_bytes(16)
    try:
        result = subprocess.run(
            [
                "openssl", "enc", "-aes-256-cbc", "-K", encryption_key.hex(),
                "-iv", iv.hex(), "-nosalt",
            ],
            input=payload,
            capture_output=True,
            check=False,
        )
    except OSError:
        raise StateCryptoError("encryption failed") from None
    if result.returncode:
        raise StateCryptoError("encryption failed")
    encrypted = result.stdout
    tag = hmac.new(authentication_key, magic + iv + encrypted, hashlib.sha256).digest()
    return magic + iv + tag + encrypted


def decrypt(blob: bytes, token: str, magic: bytes) -> bytes:
    if not token:
        raise StateCryptoError("missing encryption key")
    if not blob.startswith(magic) or len(blob) < len(magic) + 48:
        raise StateCryptoError("invalid encrypted state format")
    derived = hmac.new(token.encode("utf-8"), magic, hashlib.sha512).digest()
    encryption_key, authentication_key = derived[:32], derived[32:]
    start = len(magic)
    iv, tag, encrypted = blob[start:start + 16], blob[start + 16:start + 48], blob[start + 48:]
    expected = hmac.new(authentication_key, magic + iv + encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise StateCryptoError("state authentication failed")
    try:
        result = subprocess.run(
            [
                "openssl", "enc", "-d", "-aes-256-cbc", "-K", encryption_key.hex(),
                "-iv", iv.hex(), "-nosalt",
            ],
            input=encrypted,
            capture_output=True,
            check=False,
        )
    except OSError:
        raise StateCryptoError("decryption failed") from None
    if result.returncode:
        raise StateCryptoError("decryption failed")
    return result.stdout
