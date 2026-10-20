"""Encrypted durable queue for personal photo albums split across Bot API batches."""
from __future__ import annotations

import json
import os
from pathlib import Path

import telegram_state_crypto

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".github" / "telegram-album-state.enc"
SETTLE_SECONDS = 600
MAGIC = b"telegram-albums-v1"


class AlbumStateError(RuntimeError):
    """Album updates could not be authenticated or persisted safely."""


def _encrypt(payload: bytes, token: str) -> bytes:
    try:
        return telegram_state_crypto.encrypt(payload, token, MAGIC)
    except telegram_state_crypto.StateCryptoError as error:
        raise AlbumStateError("Не удалось зашифровать ожидающий фотоальбом") from error


def _decrypt(blob: bytes, token: str) -> bytes:
    try:
        return telegram_state_crypto.decrypt(blob, token, MAGIC)
    except telegram_state_crypto.StateCryptoError as error:
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
    for group in state["groups"].values():
        ready_at = max(
            float(group["last_seen"]) + settle_seconds,
            float(group.get("next_attempt", 0)),
        )
        if now >= ready_at:
            ready.append(group["updates"])

    if changed:
        _save(state, token)
    return ready, changed


def remove_completed(groups: list[list[dict]], token: str) -> bool:
    """Remove albums only after their handler and delivery checkpoint succeed."""
    if not groups:
        return False
    state = _load(token)
    completed_ids = {
        str((update.get("message") or {}).get("media_group_id") or "")
        for group in groups
        for update in group
    }
    completed_ids.discard("")
    removed = False
    for group_id in completed_ids:
        if group_id in state["groups"]:
            del state["groups"][group_id]
            removed = True
    if removed:
        _save(state, token)
    return removed


def defer_failed(
    groups: list[list[dict]],
    token: str,
    now: float,
    retry_after: float = 0,
) -> bool:
    group_ids = {
        str((update.get("message") or {}).get("media_group_id") or "")
        for group in groups
        for update in group
    }
    group_ids.discard("")
    if not group_ids:
        return False
    state = _load(token)
    for group_id in group_ids:
        group = state["groups"].get(group_id)
        if group is None:
            continue
        attempts = int(group.get("attempts", 0)) + 1
        delay = min(300 * (2 ** min(attempts - 1, 10)), 21600)
        group["attempts"] = attempts
        group["next_attempt"] = now + max(delay, float(retry_after or 0))
    _save(state, token)
    return True
