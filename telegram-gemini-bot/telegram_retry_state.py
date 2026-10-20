"""Encrypted retry queue for Bot API updates that could not be processed."""
from __future__ import annotations

import json
import os
from pathlib import Path

import telegram_state_crypto

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".github" / "telegram-retry-state.enc"
MAGIC = b"telegram-retry-v1"
BASE_DELAY_SECONDS = 300
MAX_DELAY_SECONDS = 21600


class RetryStateError(RuntimeError):
    pass


def _encrypt(payload: bytes, token: str) -> bytes:
    try:
        return telegram_state_crypto.encrypt(payload, token, MAGIC)
    except telegram_state_crypto.StateCryptoError as error:
        raise RetryStateError("Не удалось зашифровать ожидающие обновления Telegram") from error


def _decrypt(blob: bytes, token: str) -> bytes:
    try:
        return telegram_state_crypto.decrypt(blob, token, MAGIC)
    except telegram_state_crypto.StateCryptoError as error:
        raise RetryStateError("Не удалось расшифровать очередь Telegram") from error


class TelegramRetryState:
    def __init__(self, token: str):
        self.token = token
        self.entries = self._load()
        self.changed = False

    def _load(self) -> dict[str, dict]:
        if not STATE_FILE.exists():
            return {}
        try:
            payload = json.loads(_decrypt(STATE_FILE.read_bytes(), self.token))
        except (OSError, json.JSONDecodeError) as error:
            raise RetryStateError("Не удалось прочитать очередь Telegram") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise RetryStateError("Очередь Telegram имеет неверную структуру")
        entries = payload.get("updates")
        if not isinstance(entries, dict):
            raise RetryStateError("Очередь Telegram имеет неверную структуру")
        for key, entry in entries.items():
            if (
                not str(key).isdigit()
                or not isinstance(entry, dict)
                or entry.get("kind") not in {"admin", "channel"}
                or not isinstance(entry.get("update"), dict)
                or not isinstance(entry.get("attempts"), int)
                or not isinstance(entry.get("next_attempt"), (int, float))
            ):
                raise RetryStateError("Запись в очереди Telegram имеет неверную структуру")
        return entries

    def due(self, kind: str, now: float) -> list[dict]:
        return [
            entry["update"]
            for _, entry in sorted(
                self.entries.items(),
                key=lambda item: int(item[0]),
            )
            if entry["kind"] == kind and float(entry["next_attempt"]) <= now
        ]

    def defer(
        self,
        kind: str,
        update: dict,
        now: float,
        retry_after: int | float = 0,
    ) -> None:
        update_id = update.get("update_id")
        if kind not in {"admin", "channel"} or not isinstance(update_id, int) or update_id < 0:
            raise RetryStateError("Нельзя безопасно сохранить обновление Telegram")
        key = str(update_id)
        previous = self.entries.get(key)
        if previous and previous["kind"] != kind:
            raise RetryStateError("Идентификатор обновления Telegram конфликтует в очереди")
        attempts = int(previous["attempts"]) + 1 if previous else 1
        delay = min(BASE_DELAY_SECONDS * (2 ** min(attempts - 1, 10)), MAX_DELAY_SECONDS)
        delay = max(delay, float(retry_after or 0))
        self.entries[key] = {
            "kind": kind,
            "update": update,
            "attempts": attempts,
            "next_attempt": now + delay,
        }
        self.changed = True

    def complete(self, update_ids: list[int]) -> None:
        for update_id in update_ids:
            if self.entries.pop(str(update_id), None) is not None:
                self.changed = True

    def save(self) -> bool:
        if not self.changed:
            return False
        if not self.entries:
            STATE_FILE.unlink(missing_ok=True)
            self.changed = False
            return True
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")
        try:
            ciphertext = _encrypt(
                json.dumps(
                    {"version": 1, "updates": self.entries},
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                self.token,
            )
            temporary.write_bytes(ciphertext)
            os.replace(temporary, STATE_FILE)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise RetryStateError("Не удалось сохранить очередь Telegram") from error
        self.changed = False
        return True
