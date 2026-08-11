"""
Разовый опрос Telegram — предназначен для запуска в GitHub Actions.

GitHub Actions не даёт держать процесс запущенным вечно (это не сервер),
поэтому вместо bot.infinity_polling() (см. bot.py) этот скрипт:
  1. недолго опрашивает Telegram (getUpdates) в течение POLL_SECONDS секунд;
  2. обрабатывает все пришедшие сообщения теми же хендлерами, что описаны
     в bot.py (текст/фото/видео/кружочки/голосовые/команды);
  3. подтверждает получение обработанных обновлений, чтобы Telegram не
     прислал их повторно, и завершается.

Workflow в .github/workflows/telegram-gemini-bot.yml запускает этот скрипт
каждые несколько минут по расписанию — так получается "бот без сервера",
хоть и с задержкой ответа (не мгновенно, а раз в несколько минут).
"""

import os
import time

from telebot.apihelper import ApiTelegramException

from bot import bot  # импорт bot.py регистрирует все @bot.message_handler

# Сколько секунд опрашивать Telegram за один запуск workflow.
# Должно быть заметно меньше интервала расписания и лимита job'а в Actions.
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "260"))


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
            # предыдущий запуск workflow ещё не успел завершиться).
            # Это ожидаемая ситуация при перекрытии расписаний — просто выходим,
            # следующий запуск подхватит накопившиеся сообщения.
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


if __name__ == "__main__":
    main()
