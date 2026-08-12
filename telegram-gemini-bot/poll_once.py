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
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "260"))


def trigger_next_run() -> None:
    """Запускает следующую копию этого же workflow через GitHub API.

    Работает только внутри GitHub Actions (нужны GITHUB_TOKEN и
    GITHUB_REPOSITORY, которые задаёт сама Actions). При локальном запуске
    (python poll_once.py на своём компьютере) просто ничего не делает.
    """
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return

    url = f"https://api.github.com/repos/{repo}/actions/workflows/telegram-gemini-bot.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        response = requests.post(url, headers=headers, json={"ref": "main"}, timeout=15)
        if response.status_code >= 300:
            print(f"Не удалось запустить следующий цикл: {response.status_code} {response.text}")
    except requests.RequestException as error:
        print(f"Не удалось запустить следующий цикл: {error}")


def main() -> None:
    # На случай, если на боте когда-либо был выставлен webhook — иначе
    # getUpdates() будет падать с ошибкой "can't use getUpdates while
    # webhook is active".
    bot.remove_webhook()

    deadline = time.time() + POLL_SECONDS
    offset = None
    processed = 0

    while time.time() < deadline:
        remaining = deadline - time.time()
        # Long polling: не ждём дольше, чем осталось времени до дедлайна.
        wait = max(1, min(25, int(remaining)))
        try:
            updates = bot.get_updates(offset=offset, timeout=wait, long_polling_timeout=wait)
        except ApiTelegramException as error:
            # 409 значит, что параллельно уже идёт другой опрос (например,
            # предыдущее звено цепочки ещё не успело завершиться). Не
            # запускаем следующий цикл сами — это уже сделает тот, другой,
            # активный запуск, когда закончит работу.
            print(f"Telegram API конфликт, выхожу: {error}")
            return

        if updates:
            bot.process_new_updates(updates)
            offset = updates[-1].update_id + 1
            processed += len(updates)

    if offset is not None:
        # Финальный вызов с offset подтверждает получение последней пачки
        # обновлений, чтобы Telegram не прислал их снова в следующем запуске.
        bot.get_updates(offset=offset, timeout=0)

    print(f"poll_once: обработано сообщений — {processed}")
    trigger_next_run()


if __name__ == "__main__":
    main()
