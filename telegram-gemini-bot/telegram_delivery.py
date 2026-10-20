"""Persistent, privacy-preserving deduplication for automatic channel posts."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".github" / "telegram-delivery-state.json"


class DeliveryCheckpointError(RuntimeError):
    """A publication attempt could not be made durable before sending."""


def _fingerprint(token: str, chat_id: int, message_id: int) -> str:
    value = f"{chat_id}:{message_id}".encode("ascii")
    return hmac.new(token.encode("utf-8"), value, hashlib.sha256).hexdigest()


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "deliveries": {}}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeliveryCheckpointError("Не удалось прочитать журнал защиты от повторов") from error
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or not isinstance(state.get("deliveries"), dict)
    ):
        raise DeliveryCheckpointError("Журнал защиты от повторов имеет неверный формат")
    return state


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, STATE_FILE)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise DeliveryCheckpointError("Не удалось сохранить журнал защиты от повторов") from error


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _persist_checkpoint() -> None:
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    branch = os.getenv("GITHUB_REF_NAME", "").strip()
    if not branch:
        raise DeliveryCheckpointError("Не удалось определить ветку для защиты от повторной публикации")

    relative_state = STATE_FILE.relative_to(ROOT).as_posix()
    added = _git("add", "--", relative_state)
    if added.returncode:
        raise DeliveryCheckpointError("Не удалось подготовить защиту от повторной публикации")

    staged = _git("diff", "--cached", "--quiet", "--", relative_state)
    if staged.returncode == 0:
        return
    if staged.returncode != 1:
        raise DeliveryCheckpointError("Не удалось проверить сохранение защиты от повторной публикации")

    committed = _git("commit", "-m", "Checkpoint Telegram publication attempt", "--", relative_state)
    if committed.returncode:
        raise DeliveryCheckpointError("Не удалось сохранить защиту от повторной публикации")

    for _ in range(4):
        pushed = _git("push", "origin", f"HEAD:refs/heads/{branch}")
        if pushed.returncode == 0:
            return
        fetched = _git("fetch", "origin", branch)
        if fetched.returncode:
            break
        rebased = _git("rebase", "FETCH_HEAD")
        if rebased.returncode:
            _git("rebase", "--abort")
            break

    raise DeliveryCheckpointError("Не удалось сохранить защиту от повторной публикации в GitHub")


def begin_delivery(token: str, chat_id: int, message_id: int) -> tuple[str, bool]:
    """Record a send attempt and durably checkpoint it before calling Telegram."""
    key = _fingerprint(token, chat_id, message_id)
    state = _load_state()
    previous = state["deliveries"].get(key)
    if isinstance(previous, str) and previous in {"attempted", "published"}:
        return previous, False
    if previous is not None and previous != "failed":
        raise DeliveryCheckpointError("В журнале защиты от повторов найден неизвестный статус")

    state["deliveries"][key] = "attempted"
    _save_state(state)
    _persist_checkpoint()
    return "attempted", True


def finish_delivery(token: str, chat_id: int, message_id: int, status: str) -> None:
    if status not in {"published", "failed"}:
        raise ValueError("Недопустимый статус публикации")
    key = _fingerprint(token, chat_id, message_id)
    state = _load_state()
    if key not in state["deliveries"]:
        raise DeliveryCheckpointError("Попытка публикации отсутствует в журнале")
    state["deliveries"][key] = status
    _save_state(state)
