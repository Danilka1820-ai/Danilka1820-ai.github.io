"""
Разовый опрос Telegram — предназначен для запуска в GitHub Actions.

Цепочка работает так:
  1. бот принимает обычные сообщения администратора и channel_post из канала;
  2. обычные сообщения идут в те же обработчики, что в bot.py;
  3. новый/изменённый пост канала сразу запускает workflow Telegram → сайт;
  4. после корректного окна polling запускается следующая копия этого workflow.

Cron в telegram-gemini-bot.yml остаётся резервом на случай разрыва self-chain.
"""

import os
import time

import requests
from telebot.apihelper import ApiTelegramException

from bot import bot, group_photo_messages, process_album

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "300"))
MAX_TELEGRAM_ERRORS = 3
MAX_DISPATCH_ATTEMPTS = 5


class FatalTelegramError(RuntimeError):
    """Ошибка настроек, которую нельзя лечить бесконечным перезапуском."""


def dispatch_updates(updates: list) -> bool:
    """Обрабатывает обычные updates и сообщает, нужен ли немедленный sync сайта.

    channel_post/edited_channel_post не отправляются в Gemini: это уже готовый
    контент канала. Они лишь будят workflow Telegram → сайт. Фото одного
    альбома в личной переписке с ботом по-прежнему собираются в один запрос.
    """
    albums: dict = {}
    singles = []
    site_sync_needed = False

    for update in updates:
        if getattr(update, "channel_post", None) is not None or getattr(
            update, "edited_channel_post", None
        ) is not None:
            site_sync_needed = True
            continue

        grouped = group_photo_messages(update)
        if grouped is not None:
            albums.setdefault(grouped.media_group_id, []).append(grouped)
        else:
            singles.append(update)

    if singles:
        bot.process_new_updates(singles)

    for messages in albums.values():
        messages.sort(key=lambda message: message.message_id)
        process_album(messages)

    return site_sync_needed


def trigger_workflow(workflow_file: str, label: str) -> bool:
    """Надёжно запускает workflow_dispatch с retry на временных ошибках GitHub."""
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    ref = os.getenv("GITHUB_REF_NAME", "main")

    if not token or not repo:
        print(f"Не могу запустить {label}: нет GITHUB_TOKEN/GITHUB_REPOSITORY")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    retryable_statuses = {408, 425, 429}

    for attempt in range(MAX_DISPATCH_ATTEMPTS):
        try:
            response = requests.post(
                url,
                headers=headers,
                json={"ref": ref},
                timeout=15,
            )
        except requests.RequestException as error:
            if attempt + 1 == MAX_DISPATCH_ATTEMPTS:
                print(f"Не удалось запустить {label}: {error}")
                return False
        else:
            if response.status_code < 300:
                print(f"{label}: workflow поставлен в очередь.")
                return True

            retryable = (
                response.status_code in retryable_statuses
                or response.status_code >= 500
            )
            if not retryable:
                print(
                    f"Не удалось запустить {label}: "
                    f"HTTP {response.status_code} {response.text}"
                )
                return False

            if attempt + 1 == MAX_DISPATCH_ATTEMPTS:
                print(
                    f"Не удалось запустить {label} после повторов: "
                    f"HTTP {response.status_code} {response.text}"
                )
                return False

        delay = min(30, 2 ** (attempt + 1))
        print(f"Повтор запуска {label} через {delay} с.")
        time.sleep(delay)

    return False


def trigger_next_run() -> bool:
    return trigger_workflow("telegram-gemini-bot.yml", "следующий цикл Telegram-бота")


def trigger_site_sync() -> bool:
    return trigger_workflow("telegram-sync.yml", "синхронизация Telegram → сайт")


def telegram_error_code(error: ApiTelegramException) -> int | None:
    """pyTelegramBotAPI хранит код в error_code, старые версии — в result."""
    code = getattr(error, "error_code", None)
    if code is not None:
        return code
    result = getattr(error, "result", None)
    return getattr(result, "status_code", None)


def poll() -> tuple[bool, bool]:
    """Возвращает (poll_ok, site_sync_needed)."""
    bot.remove_webhook()

    deadline = time.time() + POLL_SECONDS
    offset = None
    processed = 0
    consecutive_errors = 0
    site_sync_needed = False

    while time.time() < deadline:
        remaining = deadline - time.time()
        wait = max(1, min(25, int(remaining)))

        try:
            updates = bot.get_updates(
                offset=offset,
                timeout=wait,
                long_polling_timeout=wait,
            )
        except ApiTelegramException as error:
            code = telegram_error_code(error)

            if code == 409:
                print(f"Telegram API конфликт 409, выхожу: {error}")
                return False, False

            if code in (401, 403):
                raise FatalTelegramError(
                    f"Telegram отклонил токен или доступ бота (HTTP {code}): {error}"
                ) from error

            consecutive_errors += 1
            if consecutive_errors >= MAX_TELEGRAM_ERRORS:
                raise RuntimeError(
                    f"Telegram API не ответил после {MAX_TELEGRAM_ERRORS} попыток: {error}"
                ) from error

            delay = min(10, 2 ** consecutive_errors)
            print(
                f"Временная ошибка Telegram API ({code or 'без кода'}), "
                f"повтор через {delay} с: {error}"
            )
            time.sleep(delay)
            continue

        consecutive_errors = 0

        if updates:
            site_sync_needed = dispatch_updates(updates) or site_sync_needed
            offset = updates[-1].update_id + 1
            processed += len(updates)

    if offset is not None:
        for attempt in range(MAX_TELEGRAM_ERRORS):
            try:
                bot.get_updates(offset=offset, timeout=0)
                break
            except ApiTelegramException as error:
                if attempt + 1 == MAX_TELEGRAM_ERRORS:
                    raise RuntimeError(
                        "Не удалось подтвердить последнюю пачку обновлений"
                    ) from error
                time.sleep(2 ** (attempt + 1))

    print(f"poll_once: обработано обновлений — {processed}")
    return True, site_sync_needed


def main() -> None:
    poll_ok, site_sync_needed = poll()
    if not poll_ok:
        return

    site_sync_ok = True
    if site_sync_needed:
        site_sync_ok = trigger_site_sync()

    next_run_ok = trigger_next_run()

    if not next_run_ok:
        raise RuntimeError(
            "Polling завершён, но следующий workflow Telegram-бота не удалось "
            "поставить в очередь. Резервный cron попробует восстановить цепочку."
        )

    if not site_sync_ok:
        raise RuntimeError(
            "Пост канала получен, но workflow Telegram → сайт не удалось "
            "запустить. Его резервный cron выполнит синхронизацию позже."
        )


if __name__ == "__main__":
    main()
