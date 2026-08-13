"""
Разовый опрос Telegram — предназначен для запуска в GitHub Actions.

GitHub Actions не даёт держать процесс запущенным вечно (это не сервер),
поэтому вместо bot.infinity_polling() (см. bot.py) этот скрипт:
  1. опрашивает Telegram (getUpdates) в течение POLL_SECONDS секунд;
  2. обрабатывает все пришедшие сообщения теми же хендлерами, что описаны
     в bot.py (текст/фото/видео/кружочки/голосовые/команды);
  3. подтверждает получение обработанных обновлений, чтобы Telegram не
     прислал их повторно;
  4. запускает копию самого себя через GitHub API (self-chaining) — так
     следующий цикл прослушки стартует почти сразу после этого, без
     ожидания расписания. Расписание в .github/workflows/telegram-gemini-bot.yml
     остаётся как подстраховка на случай, если цепочка где-то оборвётся.
"""

import os
import time

import requests
from telebot.apihelper import ApiTelegramException

from bot import bot  # импорт bot.py регистрирует все @bot.message_handler

# Сколько секунд опрашивать Telegram за один запуск workflow.
# Должно быть заметно меньше лимита job'а в Actions (timeout-minutes).
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "300"))
MAX_TELEGRAM_ERRORS = 3


class FatalTelegramError(RuntimeError):
    """Ошибка настроек, которую нельзя лечить бесконечным перезапуском."""


def trigger_next_run() -> bool:
    """Запускает следующую копию этого же workflow через GitHub API.

    Работает только внутри GitHub Actions (нужны GITHUB_TOKEN и
    GITHUB_REPOSITORY, которые задаёт сама Actions). При локальном запуске
    (python poll_once.py на своём компьютере) просто ничего не делает.
    """
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/telegram-gemini-bot.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = requests.post(url, headers=headers, json={"ref": "main"}, timeout=15)
        if response.status_code >= 300:
            print(f"Не удалось запустить следующий цикл: {response.status_code} {response.text}")
            return False
        print("Следующий цикл Telegram-бота поставлен в очередь.")
        return True
    except requests.RequestException as error:
        print(f"Не удалось запустить следующий цикл: {error}")
        return False


def telegram_error_code(error: ApiTelegramException) -> int | None:
    """pyTelegramBotAPI хранит код в error_code, но старые версии — в result."""
    code = getattr(error, "error_code", None)
    if code is not None:
        return code
    result = getattr(error, "result", None)
    return getattr(result, "status_code", None)


def poll() -> bool:
    """Обрабатывает одно окно long polling; False означает чужой активный poller."""
    # На случай, если на боте когда-либо был выставлен webhook — иначе
    # getUpdates() будет падать с ошибкой "can't use getUpdates while
    # webhook is active".
    bot.remove_webhook()

    deadline = time.time() + POLL_SECONDS
    offset = None
    processed = 0
    consecutive_errors = 0

    while time.time() < deadline:
        remaining = deadline - time.time()
        # Long polling: не ждём дольше, чем осталось времени до дедлайна.
        wait = max(1, min(25, int(remaining)))
        try:
            updates = bot.get_updates(offset=offset, timeout=wait, long_polling_timeout=wait)
        except ApiTelegramException as error:
            code = telegram_error_code(error)
            if code == 409:
                # Другой poller уже работает. Его цепочку дублировать нельзя.
                print(f"Telegram API конфликт 409, выхожу: {error}")
                return False
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
            print(f"Временная ошибка Telegram API ({code or 'без кода'}), повтор через {delay} с: {error}")
            time.sleep(delay)
            continue

        consecutive_errors = 0

        if updates:
            bot.process_new_updates(updates)
            offset = updates[-1].update_id + 1
            processed += len(updates)

    if offset is not None:
        # Финальный вызов с offset подтверждает получение последней пачки
        # обновлений, чтобы Telegram не прислал их снова в следующем запуске.
        for attempt in range(MAX_TELEGRAM_ERRORS):
            try:
                bot.get_updates(offset=offset, timeout=0)
                break
            except ApiTelegramException as error:
                if attempt + 1 == MAX_TELEGRAM_ERRORS:
                    raise RuntimeError("Не удалось подтвердить последнюю пачку обновлений") from error
                time.sleep(2 ** (attempt + 1))

    print(f"poll_once: обработано сообщений — {processed}")
    return True


def main() -> None:
    # Следующий запуск создаём только после корректного цикла. Конфликт 409,
    # неверный токен и программная ошибка не должны порождать бесконечную
    # очередь одинаково падающих workflow — их подхватит резервный cron.
    if poll():
        trigger_next_run()


if __name__ == "__main__":
    main()
