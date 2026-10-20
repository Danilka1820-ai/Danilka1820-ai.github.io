"""Encrypted durable queue for personal photo albums split across Bot API batches."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".github" / "telegram-album-state.enc"
SETTLE_SECONDS = 600
MAGIC = b"telegram-albums-v1"


class AlbumStateError(RuntimeError):
    """Album updates could not be authenticated or persisted safely."""


def _keys(token: str) -> tuple[bytes, bytes]:
    if not token:
        raise AlbumStateError("Не задан токен для защиты состояния альбома")
    derived = hmac.new(token.encode("utf-8"), MAGIC, hashlib.sha512).digest()
    return derived[:32], derived[32:]


def _encrypt(payload: bytes, token: str) -> bytes:
    encryption_key, authentication_key = _keys(token)
    iv = secrets.token_bytes(16)
    try:
        encrypted = subprocess.run(
            [
                "openssl", "enc", "-aes-256-cbc", "-K", encryption_key.hex(),
                "-iv", iv.hex(), "-nosalt",
            ],
            input=payload,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise AlbumStateError("Не удалось зашифровать ожидающий фотоальбом") from error
    tag = hmac.new(authentication_key, MAGIC + iv + encrypted, hashlib.sha256).digest()
    return MAGIC + iv + tag + encrypted


def _decrypt(blob: bytes, token: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 48:
        raise AlbumStateError("Зашифрованное состояние фотоальбома имеет неверный формат")
    encryption_key, authentication_key = _keys(token)
    start = len(MAGIC)
    iv, tag, encrypted = blob[start:start + 16], blob[start + 16:start + 48], blob[start + 48:]
    expected = hmac.new(authentication_key, MAGIC + iv + encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise AlbumStateError("Не удалось проверить подлинность состояния фотоальбома")
    try:
        return subprocess.run(
            [
                "openssl", "enc", "-d", "-aes-256-cbc", "-K", encryption_key.hex(),
                "-iv", iv.hex(), "-nosalt",
            ],
            input=encrypted,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise AlbumStateError("Не удалось расшифровать ожидающий фотоальбом") from error


def _load(token: str) -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "groups": {}}
    try:
        state = json.loads(_decrypt(STATE_FILE.read_bytes(), token))
    except (OSError, json.JSONDecodeError) as error:
        raise AlbumStateError("Не удалось прочитать состояние фотоальбома") from error
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("groups"), dict):
        raise AlbumStateError("Состояние фотоальбома имеет неверную структуру")
    return state


def _save(state: dict, token: str) -> None:
    if not state["groups"]:
        STATE_FILE.unlink(missing_ok=True)
        return
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")
    try:
        ciphertext = _encrypt(
            json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
            token,
        )
        temporary.write_bytes(ciphertext)
        os.replace(temporary, STATE_FILE)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise AlbumStateError("Не удалось сохранить состояние фотоальбома") from error


def record_and_get_ready(
    updates: list[dict],
    token: str,
    now: float,
    settle_seconds: int = SETTLE_SECONDS,
) -> tuple[list[list[dict]], bool]:
    state = _load(token)
    changed = False
    for update in updates:
        message = update.get("message") or {}
        group_id = message.get("media_group_id")
        if not group_id or not message.get("photo"):
            continue
        key = str(group_id)
        group = state["groups"].setdefault(key, {"last_seen": now, "updates": []})
        update_id = update.get("update_id")
        if any(existing.get("update_id") == update_id for existing in group["updates"]):
            continue
        group["updates"].append(update)
        group["updates"].sort(key=lambda item: int(item["update_id"]))
        group["last_seen"] = now
        changed = True

    ready = []
    for key, group in list(state["groups"].items()):
        if now - float(group["last_seen"]) >= settle_seconds:
            ready.append(group["updates"])
            del state["groups"][key]
            changed = True

    if changed:
        _save(state, token)
    return ready, changed
