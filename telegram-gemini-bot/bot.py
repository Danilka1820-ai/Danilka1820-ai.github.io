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
  ADMIN_ID           — (необязательно) numeric Telegram user id,
                        если задан — команды /post /edit /delete работают
                        только для этого пользователя
"""

import os
import time
import tempfile

import telebot
from google import genai
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 1. Загружаем настройки из .env (если файл есть) и переменных окружения
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")  # опционально, строка -> int ниже

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN (переменная окружения)")
if not GEMINI_API_KEY:
    raise RuntimeError("Не задан GEMINI_API_KEY (переменная окружения)")
if not CHANNEL_ID:
    raise RuntimeError("Не задан CHANNEL_ID (переменная окружения)")

ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else None

# ---------------------------------------------------------------------------
# 2. Инициализация бота и Gemini
# ---------------------------------------------------------------------------

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="HTML")

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
    """Проверяет, разрешено ли пользователю управлять каналом.

    Если ADMIN_ID не задан, ограничений нет (не рекомендуется для боевого
    использования — задайте ADMIN_ID, чтобы посты мог публиковать только он).
    """
    if ADMIN_ID is None:
        return True
    return message.from_user.id == ADMIN_ID


def download_telegram_file(file_id: str, suffix: str) -> str:
    """Скачивает файл из Telegram по file_id и сохраняет во временный файл.

    Возвращает путь к локальному файлу.
    """
    file_info = bot.get_file(file_id)
    file_bytes = bot.download_file(file_info.file_path)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(file_bytes)
    tmp.close()
    return tmp.name


def ask_gemini_text(user_text: str) -> str:
    """Отправляет обычный текст в Gemini и просит сделать из него пост."""
    prompt = (
        "Перепиши следующий текст в виде красивого поста для Telegram-канала.\n"
        f"{POST_STYLE_HINT}\n\n"
        f"Исходный текст:\n{user_text}"
    )
    response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


def ask_gemini_about_media(local_path: str, task_description: str) -> str:
    """Загружает медиафайл (фото/видео/аудио) в Gemini и просит сделать пост.

    task_description — что именно нужно сделать с содержимым
    (например, "опиши фото и напиши пост" или "расшифруй речь и напиши пост").
    """
    # Загружаем файл в Gemini File API. Для фото это происходит мгновенно,
    # для видео/аудио файлу нужно время на обработку на стороне Google —
    # поэтому дожидаемся, пока его состояние станет ACTIVE.
    uploaded_file = gemini_client.files.upload(file=local_path)

    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = gemini_client.files.get(name=uploaded_file.name)

    if uploaded_file.state.name == "FAILED":
        raise RuntimeError("Gemini не смог обработать присланный файл")

    prompt = f"{task_description}\n{POST_STYLE_HINT}"

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded_file, prompt],
        )
        return response.text.strip()
    finally:
        # Файл больше не нужен на серверах Google — удаляем, чтобы не копились.
        gemini_client.files.delete(name=uploaded_file.name)


def process_media_message(message, file_id: str, suffix: str, task_description: str):
    """Общий сценарий для фото/видео/кружочков/голосовых:

    скачать файл -> отдать в Gemini -> ответить готовым постом.
    """
    processing_msg = bot.reply_to(message, "⏳ Обрабатываю файл, подождите немного...")
    local_path = None
    try:
        local_path = download_telegram_file(file_id, suffix)
        post_text = ask_gemini_about_media(local_path, task_description)
        bot.edit_message_text(
            f"✅ Готовый пост:\n\n{post_text}",
            chat_id=processing_msg.chat.id,
            message_id=processing_msg.message_id,
        )
    except Exception as error:  # noqa: BLE001 - хотим сообщить пользователю о любой ошибке
        bot.edit_message_text(
            f"❌ Не удалось обработать файл: {error}",
            chat_id=processing_msg.chat.id,
            message_id=processing_msg.message_id,
        )
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)


# ---------------------------------------------------------------------------
# 4. Команды управления каналом
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    bot.reply_to(
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
    if not is_admin(message):
        bot.reply_to(message, "⛔ У вас нет прав на публикацию в канал.")
        return

    # message.text выглядит как "/post текст поста..."
    text = message.text.partition(" ")[2].strip()
    if not text:
        bot.reply_to(message, "Использование: /post <текст поста>")
        return

    try:
        sent = bot.send_message(CHANNEL_ID, text)
        bot.reply_to(message, f"✅ Опубликовано в канале. ID сообщения: {sent.message_id}")
    except Exception as error:  # noqa: BLE001
        bot.reply_to(message, f"❌ Не удалось опубликовать: {error}")


@bot.message_handler(commands=["edit"])
def handle_edit(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ У вас нет прав на редактирование постов.")
        return

    # Ожидаем формат: /edit 123 новый текст поста...
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        bot.reply_to(message, "Использование: /edit <ID_сообщения> <новый текст>")
        return

    message_id = int(parts[1])
    new_text = parts[2]

    try:
        bot.edit_message_text(new_text, chat_id=CHANNEL_ID, message_id=message_id)
        bot.reply_to(message, f"✅ Пост {message_id} обновлён.")
    except Exception as error:  # noqa: BLE001
        bot.reply_to(message, f"❌ Не удалось изменить пост: {error}")


@bot.message_handler(commands=["delete"])
def handle_delete(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ У вас нет прав на удаление постов.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delete <ID_сообщения>")
        return

    message_id = int(parts[1].strip())

    try:
        bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
        bot.reply_to(message, f"✅ Пост {message_id} удалён.")
    except Exception as error:  # noqa: BLE001
        bot.reply_to(message, f"❌ Не удалось удалить пост: {error}")


# ---------------------------------------------------------------------------
# 5. Обработка текста, фото, видео, кружочков и голосовых
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=["text"])
def handle_text(message):
    # Команды (/post, /edit, /delete, /start, /help) обрабатываются выше
    # и до этого хендлера не доходят.
    processing_msg = bot.reply_to(message, "⏳ Спрашиваю у Gemini...")
    try:
        post_text = ask_gemini_text(message.text)
        bot.edit_message_text(
            f"✅ Готовый пост:\n\n{post_text}",
            chat_id=processing_msg.chat.id,
            message_id=processing_msg.message_id,
        )
    except Exception as error:  # noqa: BLE001
        bot.edit_message_text(
            f"❌ Ошибка запроса к Gemini: {error}",
            chat_id=processing_msg.chat.id,
            message_id=processing_msg.message_id,
        )


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    # message.photo — список размеров одной и той же фотографии,
    # последний элемент — самое высокое разрешение.
    file_id = message.photo[-1].file_id
    task = "Посмотри на фотографию, опиши, что на ней происходит (включая любой видимый текст), и на основе этого напиши пост."
    process_media_message(message, file_id, ".jpg", task)


@bot.message_handler(content_types=["video"])
def handle_video(message):
    file_id = message.video.file_id
    task = "Посмотри видео, перескажи его сюжет и содержание (включая произносимую речь, если есть) и на основе этого напиши пост."
    process_media_message(message, file_id, ".mp4", task)


@bot.message_handler(content_types=["video_note"])
def handle_video_note(message):
    file_id = message.video_note.file_id
    task = "Это видео-кружок. Расшифруй речь, которая на нём звучит, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(message, file_id, ".mp4", task)


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    file_id = message.voice.file_id
    task = "Это голосовое сообщение. Расшифруй речь, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(message, file_id, ".ogg", task)


# ---------------------------------------------------------------------------
# 6. Запуск бота
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling(skip_pending=True)
