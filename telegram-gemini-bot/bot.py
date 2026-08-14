"""
Telegram-бот на pyTelegramBotAPI + Google Gemini.

Возможности:
  - Текст -> бот просит Gemini сделать из него красивый пост.
  - Фото/видео -> бот скачивает файл, отдаёт его Gemini,
    получает описание сюжета/текста и превращает в пост.
  - Кружочки (video_note) и голосовые (voice) -> бот скачивает аудио/видео,
    Gemini расшифровывает речь и формирует готовый пост.
  - /post <текст>            — публикует пост в канал, возвращает ID сообщения.
  - /edit <ID> <новый текст> — редактирует пост в канале по ID.
  - /delete <ID>             — удаляет пост из канала по ID.

Настройка — через переменные окружения (см. README.md рядом с этим файлом):
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  GEMINI_API_KEY     — ключ Gemini API (https://aistudio.google.com/apikey)
  CHANNEL_ID         — id или @username канала, куда бот публикует посты
  ADMIN_ID           — обязательный numeric Telegram user id владельца;
                        без него бот не запускается
"""

import os
import time
import tempfile

import telebot
from google import genai
from dotenv import load_dotenv

from bot_utils import (
    TELEGRAM_MESSAGE_LIMIT,
    clip_for_telegram,
    require_admin_id,
    user_id_from_message,
    validate_media_size,
    validate_source_text,
)

# ---------------------------------------------------------------------------
# 1. Загружаем настройки из .env (если файл есть) и переменных окружения
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
GEMINI_FILE_TIMEOUT_SECONDS = int(os.getenv("GEMINI_FILE_TIMEOUT_SECONDS", "120"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN (переменная окружения)")
if not GEMINI_API_KEY:
    raise RuntimeError("Не задан GEMINI_API_KEY (переменная окружения)")
if not CHANNEL_ID:
    raise RuntimeError("Не задан CHANNEL_ID (переменная окружения)")

ADMIN_ID = require_admin_id(ADMIN_ID)

# ---------------------------------------------------------------------------
# 2. Инициализация бота и Gemini
# ---------------------------------------------------------------------------

# Текст приходит от пользователя и Gemini. Глобальный HTML parse_mode здесь
# опасен: обычные символы вроде "<" могли превращаться в битую разметку и
# полностью ронять отправку ответа или публикацию в канал.
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-flash-latest"

# Промпт, который просит Gemini всегда возвращать готовый к публикации текст
# без лишних пояснений и без markdown-разметки, которую Telegram не понимает.
POST_STYLE_HINT = (
    "Ответь только готовым текстом поста на русском языке, без вступлений "
    "вроде 'Вот пост:' и без markdown-разметки (**, ##). Можно использовать "
    "эмодзи там, где это уместно. Пост должен быть живым, лаконичным и "
    "хорошо читаться в Telegram-канале. В самом конце поста, отдельной "
    "строкой, добавь 3-5 хэштегов на русском языке, подходящих по смыслу "
    "к содержанию (например: #природа #москва #вечер)."
)


# ---------------------------------------------------------------------------
# 3. Вспомогательные функции
# ---------------------------------------------------------------------------

def is_admin(message) -> bool:
    """Разрешает работу только владельцу, указанному в ADMIN_ID."""
    return user_id_from_message(message) == ADMIN_ID


def require_admin(message) -> bool:
    """Единая защита команд и всех обращений к платному внешнему API."""
    if is_admin(message):
        return True
    bot.reply_to(message, "⛔ Этот бот доступен только владельцу.")
    return False


def reply(message, text: str):
    """Отправляет ответ, гарантированно укладывающийся в лимит Telegram."""
    return bot.reply_to(message, clip_for_telegram(text))


def edit_reply(processing_message, text: str) -> None:
    bot.edit_message_text(
        clip_for_telegram(text),
        chat_id=processing_message.chat.id,
        message_id=processing_message.message_id,
    )


def download_telegram_file(file_id: str, suffix: str) -> str:
    """Скачивает файл из Telegram по file_id и сохраняет во временный файл.

    Возвращает путь к локальному файлу.
    """
    file_info = bot.get_file(file_id)
    validate_media_size(getattr(file_info, "file_size", None))
    file_bytes = bot.download_file(file_info.file_path)
    validate_media_size(len(file_bytes))

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(file_bytes)
    tmp.close()
    return tmp.name


GEMINI_RETRY_ATTEMPTS = 4
# Google присылает эти коды и в тексте ошибки, и в теле ответа — единого
# удобного атрибута у всех версий SDK на этот случай нет, поэтому проверяем
# по тексту, благо он всегда содержит код и статус (см. скриншот с 503
# UNAVAILABLE, из-за которого этот повтор и появился).
GEMINI_RETRY_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded")


def _is_transient_gemini_error(error: Exception) -> bool:
    """503 UNAVAILABLE и 429 RESOURCE_EXHAUSTED — модель временно перегружена
    на стороне Google, не ошибка самого запроса. Обычно проходит за секунды."""
    text = str(error)
    return any(marker in text for marker in GEMINI_RETRY_MARKERS)


def _generate_content(**kwargs):
    """generate_content с повтором при временной перегрузке Gemini — иначе
    зритель видит сырой '503 UNAVAILABLE' вместо готового поста ровно в тот
    момент, когда с самим запросом всё было в порядке."""
    delay = 2
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            return gemini_client.models.generate_content(model=GEMINI_MODEL, **kwargs)
        except Exception as error:  # noqa: BLE001 - решаем здесь же, ретраить или нет
            if attempt == GEMINI_RETRY_ATTEMPTS or not _is_transient_gemini_error(error):
                raise
            print(f"Gemini временно перегружен (попытка {attempt}/{GEMINI_RETRY_ATTEMPTS}): {error}")
            time.sleep(delay)
            delay *= 2


def ask_gemini_text(user_text: str) -> str:
    """Отправляет обычный текст в Gemini и просит сделать из него пост."""
    user_text = validate_source_text(user_text)
    prompt = (
        "Перепиши следующий текст в виде красивого поста для Telegram-канала.\n"
        f"{POST_STYLE_HINT}\n\n"
        f"Исходный текст:\n{user_text}"
    )
    response = _generate_content(contents=prompt)
    return validate_source_text(getattr(response, "text", ""))


def ask_gemini_about_media(local_path: str, task_description: str) -> str:
    """Загружает медиафайл (фото/видео/аудио) в Gemini и просит сделать пост.

    task_description — что именно нужно сделать с содержимым
    (например, "опиши фото и напиши пост" или "расшифруй речь и напиши пост").
    """
    # Загружаем файл в Gemini File API. Для фото это происходит мгновенно,
    # для видео/аудио файлу нужно время на обработку на стороне Google —
    # поэтому дожидаемся, пока его состояние станет ACTIVE.
    uploaded_file = gemini_client.files.upload(file=local_path)
    try:
        deadline = time.monotonic() + GEMINI_FILE_TIMEOUT_SECONDS
        while uploaded_file.state.name == "PROCESSING":
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Gemini не обработал файл за {GEMINI_FILE_TIMEOUT_SECONDS} секунд"
                )
            time.sleep(2)
            uploaded_file = gemini_client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise RuntimeError("Gemini не смог обработать присланный файл")

        prompt = f"{task_description}\n{POST_STYLE_HINT}"
        response = _generate_content(contents=[uploaded_file, prompt])
        return validate_source_text(getattr(response, "text", ""))
    finally:
        # Файл больше не нужен на серверах Google — удаляем, чтобы не копились.
        try:
            gemini_client.files.delete(name=uploaded_file.name)
        except Exception as error:  # удаление не должно скрыть основной ответ
            print(f"Не удалось удалить временный файл Gemini: {error}")


def process_media_message(
    message,
    file_id: str,
    suffix: str,
    task_description: str,
    file_size: int | None = None,
):
    """Общий сценарий для фото/видео/кружочков/голосовых:

    скачать файл -> отдать в Gemini -> ответить готовым постом.
    """
    try:
        validate_media_size(file_size)
    except ValueError as error:
        reply(message, f"❌ {error}")
        return
    processing_msg = reply(message, "⏳ Обрабатываю файл, подождите немного...")
    local_path = None
    try:
        local_path = download_telegram_file(file_id, suffix)
        post_text = ask_gemini_about_media(local_path, task_description)
        edit_reply(processing_msg, f"✅ Готовый пост:\n\n{post_text}")
    except Exception as error:  # noqa: BLE001 - хотим сообщить пользователю о любой ошибке
        print(f"Не удалось обработать файл: {error}")
        edit_reply(processing_msg, f"❌ Не удалось обработать файл: {error}")
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)


# ---------------------------------------------------------------------------
# 4. Команды управления каналом
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if not require_admin(message):
        return
    reply(
        message,
        "Привет! Я помогу превратить текст, фото, видео, кружочки и голосовые "
        "сообщения в готовые посты через Gemini.\n\n"
        "Команды для управления каналом:\n"
        "/post <текст> — опубликовать пост в канал\n"
        "/edit <ID> <новый текст> — изменить пост по ID\n"
        "/delete <ID> — удалить пост по ID",
    )


@bot.message_handler(commands=["post"])
def handle_post(message):
    if not require_admin(message):
        return

    # message.text выглядит как "/post текст поста..."
    text = message.text.partition(" ")[2].strip()
    if not text:
        reply(message, "Использование: /post <текст поста>")
        return
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        reply(message, f"❌ Пост длиннее лимита Telegram ({TELEGRAM_MESSAGE_LIMIT} символов).")
        return

    try:
        sent = bot.send_message(CHANNEL_ID, text)
        reply(message, f"✅ Опубликовано в канале. ID сообщения: {sent.message_id}")
    except Exception as error:  # noqa: BLE001
        reply(message, f"❌ Не удалось опубликовать: {error}")


@bot.message_handler(commands=["edit"])
def handle_edit(message):
    if not require_admin(message):
        return

    # Ожидаем формат: /edit 123 новый текст поста...
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        reply(message, "Использование: /edit <ID_сообщения> <новый текст>")
        return

    message_id = int(parts[1])
    new_text = parts[2]
    if len(new_text) > TELEGRAM_MESSAGE_LIMIT:
        reply(message, f"❌ Текст длиннее лимита Telegram ({TELEGRAM_MESSAGE_LIMIT} символов).")
        return

    try:
        bot.edit_message_text(new_text, chat_id=CHANNEL_ID, message_id=message_id)
        reply(message, f"✅ Пост {message_id} обновлён.")
    except Exception as error:  # noqa: BLE001
        reply(message, f"❌ Не удалось изменить пост: {error}")


@bot.message_handler(commands=["delete"])
def handle_delete(message):
    if not require_admin(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        reply(message, "Использование: /delete <ID_сообщения>")
        return

    message_id = int(parts[1].strip())

    try:
        bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
        reply(message, f"✅ Пост {message_id} удалён.")
    except Exception as error:  # noqa: BLE001
        reply(message, f"❌ Не удалось удалить пост: {error}")


# ---------------------------------------------------------------------------
# 5. Обработка текста, фото, видео, кружочков и голосовых
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=["text"])
def handle_text(message):
    # Команды (/post, /edit, /delete, /start, /help) обрабатываются выше
    # и до этого хендлера не доходят.

    if not require_admin(message):
        return

    # Если переслали сообщение из канала/чата — просто сообщаем его точный
    # ID и username вместо запроса к Gemini. Это удобный способ узнать,
    # что писать в CHANNEL_ID (особенно для приватных каналов без @username).
    if message.forward_from_chat is not None:
        chat = message.forward_from_chat
        username_line = f"@{chat.username}" if chat.username else "нет (канал приватный)"
        reply(
            message,
            "ℹ️ Информация о канале/чате, откуда переслано сообщение:\n"
            f"ID: {chat.id}\n"
            f"Название: {chat.title}\n"
            f"Username: {username_line}\n\n"
            f"Для CHANNEL_ID используй значение: {chat.username and ('@' + chat.username) or chat.id}",
        )
        return

    try:
        source_text = validate_source_text(message.text)
    except ValueError as error:
        reply(message, f"❌ {error}")
        return

    processing_msg = reply(message, "⏳ Спрашиваю у Gemini...")
    try:
        post_text = ask_gemini_text(source_text)
        edit_reply(processing_msg, f"✅ Готовый пост:\n\n{post_text}")
    except Exception as error:  # noqa: BLE001
        print(f"Ошибка запроса к Gemini: {error}")
        edit_reply(processing_msg, f"❌ Ошибка запроса к Gemini: {error}")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if not require_admin(message):
        return
    # message.photo — список размеров одной и той же фотографии,
    # последний элемент — самое высокое разрешение.
    file_id = message.photo[-1].file_id
    task = "Посмотри на фотографию, опиши, что на ней происходит (включая любой видимый текст), и на основе этого напиши пост."
    process_media_message(message, file_id, ".jpg", task, message.photo[-1].file_size)


@bot.message_handler(content_types=["video"])
def handle_video(message):
    if not require_admin(message):
        return
    file_id = message.video.file_id
    task = "Посмотри видео, перескажи его сюжет и содержание (включая произносимую речь, если есть) и на основе этого напиши пост."
    process_media_message(message, file_id, ".mp4", task, message.video.file_size)


@bot.message_handler(content_types=["video_note"])
def handle_video_note(message):
    if not require_admin(message):
        return
    file_id = message.video_note.file_id
    task = "Это видео-кружок. Расшифруй речь, которая на нём звучит, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(message, file_id, ".mp4", task, message.video_note.file_size)


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    if not require_admin(message):
        return
    file_id = message.voice.file_id
    task = "Это голосовое сообщение. Расшифруй речь, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(message, file_id, ".ogg", task, message.voice.file_size)


# ---------------------------------------------------------------------------
# 6. Запуск бота
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling(skip_pending=True)
