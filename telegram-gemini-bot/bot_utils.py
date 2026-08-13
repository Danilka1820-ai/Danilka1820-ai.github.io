"""Небольшие чистые функции для безопасной работы Telegram-бота."""

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_SOURCE_TEXT_CHARS = 12_000
MAX_MEDIA_BYTES = 20 * 1024 * 1024


def require_admin_id(raw_value: str | None) -> int:
    """Возвращает ADMIN_ID или останавливает небезопасный запуск.

    Управляющий каналом бот не должен переходить в режим «доступен всем»
    из-за опечатки в имени секрета или пустого значения.
    """
    if not raw_value:
        raise RuntimeError("Не задан ADMIN_ID (числовой Telegram user id владельца)")
    try:
        admin_id = int(raw_value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("ADMIN_ID должен быть целым числом") from error
    if admin_id <= 0:
        raise RuntimeError("ADMIN_ID должен быть положительным числом")
    return admin_id


def user_id_from_message(message) -> int | None:
    """Безопасно получает ID отправителя даже у неполного update."""
    sender = getattr(message, "from_user", None)
    return getattr(sender, "id", None)


def clip_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    """Обрезает ответ до лимита Telegram, не превышая его после суффикса."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    suffix = "\n\n… Ответ сокращён из-за лимита Telegram."
    return value[: max(0, limit - len(suffix))].rstrip() + suffix


def validate_source_text(text: str) -> str:
    """Проверяет, что запрос не пустой и имеет разумный размер."""
    value = str(text or "").strip()
    if not value:
        raise ValueError("Текст сообщения пуст")
    if len(value) > MAX_SOURCE_TEXT_CHARS:
        raise ValueError(
            f"Текст слишком длинный: {len(value)} символов, максимум — "
            f"{MAX_SOURCE_TEXT_CHARS}"
        )
    return value


def validate_media_size(size: int | None) -> None:
    """Не даёт скачать в память файл больше установленного лимита."""
    if size is not None and size > MAX_MEDIA_BYTES:
        limit_mb = MAX_MEDIA_BYTES // (1024 * 1024)
        raise ValueError(f"Файл слишком большой. Максимальный размер — {limit_mb} МБ")
