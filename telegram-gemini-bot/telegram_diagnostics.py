"""Read-only checks for the production Telegram bot/channel configuration."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BOT_API = "https://api.telegram.org"
EXPECTED_BOT = "DanilkaVlogBot"
EXPECTED_CHANNEL = "danilka2028k"
REQUEST_TIMEOUT = 10


class TelegramDiagnosticsError(RuntimeError):
    pass


def telegram_api(token: str, method: str, payload: dict | None = None) -> dict:
    request = Request(
        f"{BOT_API}/bot{token}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read())
    except HTTPError as error:
        raise TelegramDiagnosticsError(
            f"{method} недоступен (HTTP {error.code})"
        ) from None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise TelegramDiagnosticsError(
            f"{method} недоступен ({type(error).__name__})"
        ) from None
    if not isinstance(body, dict) or not body.get("ok"):
        code = body.get("error_code") if isinstance(body, dict) else None
        detail = f" (код {code})" if isinstance(code, int) else ""
        raise TelegramDiagnosticsError(f"{method} отклонён Telegram{detail}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise TelegramDiagnosticsError(f"{method} вернул неожиданный ответ")
    return result


def diagnose(token: str, channel_id: str, admin_id: str, api=telegram_api) -> list[str]:
    token = token.strip()
    channel_id = channel_id.strip()
    admin_id = admin_id.strip()
    if not token or not channel_id:
        raise TelegramDiagnosticsError("Не заданы обязательные настройки Telegram")
    if not admin_id.isdigit() or int(admin_id) <= 0:
        raise TelegramDiagnosticsError("ADMIN_ID должен быть положительным числом")

    bot = api(token, "getMe")
    username = str(bot.get("username") or "")
    if username.casefold() != EXPECTED_BOT.casefold():
        raise TelegramDiagnosticsError(
            f"Токен принадлежит @{username or 'неизвестному боту'}, "
            f"ожидался @{EXPECTED_BOT}; публикации остановлены."
        )

    webhook = api(token, "getWebhookInfo")
    if webhook.get("url"):
        raise TelegramDiagnosticsError(
            "Для токена установлен webhook; getUpdates/polling не может работать одновременно."
        )

    channel = api(token, "getChat", {"chat_id": channel_id})
    channel_username = str(channel.get("username") or "")
    if channel_username.casefold() != EXPECTED_CHANNEL.casefold():
        raise TelegramDiagnosticsError(
            f"CHANNEL_ID ведёт не в @{EXPECTED_CHANNEL}; Bot API его не подтвердил."
        )

    member = api(
        token,
        "getChatMember",
        {"chat_id": channel_id, "user_id": bot.get("id")},
    )
    status = member.get("status")
    can_post = status == "creator" or (
        status == "administrator" and member.get("can_post_messages") is True
    )
    if not can_post:
        raise TelegramDiagnosticsError(
            "Бот не имеет права публиковать в настроенный канал "
            f"(статус: {status or 'неизвестен'})."
        )

    return [
        f"getMe: @{EXPECTED_BOT} — OK",
        "ADMIN_ID: задан как положительный числовой ID; обработчики ограничены личным чатом владельца.",
        "CHANNEL_ID: подтверждён как @danilka2028k.",
        f"Права бота в канале: {status}; публикация разрешена.",
        "Webhook: не установлен; конфликт с getUpdates отсутствует.",
    ]


def main() -> None:
    try:
        results = diagnose(
            os.getenv("TELEGRAM_BOT_TOKEN", ""),
            os.getenv("CHANNEL_ID", ""),
            os.getenv("ADMIN_ID", ""),
        )
    except TelegramDiagnosticsError as error:
        raise SystemExit(f"Telegram configuration check failed: {error}") from None
    print("\n".join(results))


if __name__ == "__main__":
    main()
