"""
Telegram-бот на pyTelegramBotAPI + xAI Grok.

Возможности:
  - Текст -> бот просит Grok сделать из него красивый пост.
  - Фото -> бот скачивает файл, отдаёт его Grok Vision,
    получает описание сюжета/текста и превращает в пост.
  - Видео -> Grok не умеет «смотреть» видео целиком через API (в отличие от
    Gemini): бот вытаскивает несколько кадров ffmpeg'ом, отдельно
    расшифровывает звук через Grok Speech-to-Text и просит Grok Vision
    написать пост по кадрам и расшифровке вместе. Похоже на просмотр, но
    не то же самое — то, что случилось между кадрами и без звука, мимо.
  - Кружочки (video_note) и голосовые (voice) -> бот расшифровывает речь
    через Grok Speech-to-Text и формирует готовый пост по тексту.
  - /post <текст>            — публикует пост в канал, возвращает ID сообщения.
  - /edit <ID> <новый текст> — редактирует пост в канале по ID.
  - /delete <ID>             — удаляет пост из канала по ID.

Настройка — через переменные окружения (см. README.md рядом с этим файлом):
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  XAI_API_KEY        — ключ xAI API (https://console.x.ai)
  CHANNEL_ID         — id или @username канала, куда бот публикует посты
  ADMIN_ID           — обязательный numeric Telegram user id владельца;
                        без него бот не запускается
"""

import base64
import mimetypes
import os
import shutil
import subprocess
import tempfile

import requests
import telebot
from dotenv import load_dotenv
from openai import OpenAI

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
XAI_API_KEY = os.getenv("XAI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
GROK_FILE_TIMEOUT_SECONDS = int(os.getenv("GROK_FILE_TIMEOUT_SECONDS", "120"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN (переменная окружения)")
if not XAI_API_KEY:
    raise RuntimeError("Не задан XAI_API_KEY (переменная окружения)")
if not CHANNEL_ID:
    raise RuntimeError("Не задан CHANNEL_ID (переменная окружения)")

ADMIN_ID = require_admin_id(ADMIN_ID)

# ---------------------------------------------------------------------------
# 2. Инициализация бота и Grok
# ---------------------------------------------------------------------------

# Текст приходит от пользователя и Grok. Глобальный HTML parse_mode здесь
# опасен: обычные символы вроде "<" могли превращаться в битую разметку и
# полностью ронять отправку ответа или публикацию в канал.
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

# У xAI обычный OpenAI-совместимый /chat/completions — используем их же SDK,
# только с другим base_url. Имена моделей у xAI меняются заметно быстрее,
# чем у Gemini, поэтому это переменные окружения с разумным умолчанием, а не
# жёстко вшитая строка: если xAI переименует модель, чинить — не в коде.
XAI_BASE_URL = "https://api.x.ai/v1"
xai_client = OpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL, timeout=GROK_FILE_TIMEOUT_SECONDS)
GROK_TEXT_MODEL = os.getenv("GROK_TEXT_MODEL", "grok-4")
GROK_VISION_MODEL = os.getenv("GROK_VISION_MODEL", "grok-2-vision-latest")

# Речь в аудио/видео расшифровывает отдельный эндпоинт Grok Speech-to-Text —
# он не часть /chat/completions и не покрыт OpenAI SDK, поэтому обычный
# HTTP-запрос.
XAI_STT_URL = f"{XAI_BASE_URL}/stt"

# ffmpeg нужен только для кадров из обычного видео (см. extract_video_frames).
# Кружочки и голосовые уходят в Speech-to-Text как есть, без него.
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

# Промпт, который просит Grok всегда возвращать готовый к публикации текст
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


def ask_grok_text(user_text: str) -> str:
    """Отправляет обычный текст в Grok и просит сделать из него пост."""
    user_text = validate_source_text(user_text)
    prompt = (
        "Перепиши следующий текст в виде красивого поста для Telegram-канала.\n"
        f"{POST_STYLE_HINT}\n\n"
        f"Исходный текст:\n{user_text}"
    )
    response = xai_client.chat.completions.create(
        model=GROK_TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return validate_source_text(response.choices[0].message.content or "")


def _image_content_part(image_path: str) -> dict:
    """Кодирует картинку в data URL для content вида image_url — так Grok
    Vision принимает изображение прямо в теле запроса, без отдельной
    загрузки файла (в отличие от Gemini File API, у Grok такого шага нет)."""
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def ask_grok_about_image(local_path: str, task_description: str) -> str:
    """Отдаёт фото в Grok Vision и просит сделать пост."""
    content = [
        _image_content_part(local_path),
        {"type": "text", "text": f"{task_description}\n{POST_STYLE_HINT}"},
    ]
    response = xai_client.chat.completions.create(
        model=GROK_VISION_MODEL,
        messages=[{"role": "user", "content": content}],
    )
    return validate_source_text(response.choices[0].message.content or "")


def transcribe_with_grok(local_path: str) -> str:
    """Расшифровывает речь в аудио- или видеофайле через Grok Speech-to-Text.

    Пустая строка, если речи не нашлось (например, немое видео) — это не
    ошибка, вызывающий код сам решает, что делать дальше.
    """
    with open(local_path, "rb") as file:
        response = requests.post(
            XAI_STT_URL,
            headers={"Authorization": f"Bearer {XAI_API_KEY}"},
            files={"file": file},
            timeout=GROK_FILE_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    return response.json().get("text", "").strip()


def ask_grok_about_speech(local_path: str, task_description: str) -> str:
    """Кружочки и голосовые: расшифровать речь и написать пост по тексту."""
    transcript = transcribe_with_grok(local_path)
    if not transcript:
        raise RuntimeError("Grok не расслышал речь в файле")
    prompt = f"{task_description}\n{POST_STYLE_HINT}\n\nРасшифровка речи:\n{transcript}"
    response = xai_client.chat.completions.create(
        model=GROK_TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return validate_source_text(response.choices[0].message.content or "")


def _ffprobe_duration(path: str) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def extract_video_frames(video_path: str, count: int = 3) -> list[str]:
    """Достаёт count кадров, равномерно распределённых по длине ролика.

    Возвращает пути к временным jpg. Пустой список, если ffmpeg недоступен
    или у файла не вышло определить длительность — вызывающий код тогда
    обходится одной расшифровкой звука.
    """
    if not FFMPEG_AVAILABLE:
        return []
    duration = _ffprobe_duration(video_path)
    if not duration or duration <= 0:
        return []

    frames = []
    for i in range(count):
        timestamp = duration * (i + 1) / (count + 1)
        out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{timestamp:.2f}",
             "-i", video_path, "-frames:v", "1", out_path],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            frames.append(out_path)
        elif os.path.exists(out_path):
            os.remove(out_path)
    return frames


def ask_grok_about_video(local_path: str, task_description: str) -> str:
    """Обычное видео: у Grok, в отличие от Gemini, нет API, понимающего видео
    целиком. Показываем ему несколько кадров плюс расшифровку звука —
    приближение, а не настоящий просмотр: то, что случилось между кадрами и
    без звука, в пост не попадёт.
    """
    frames = extract_video_frames(local_path)
    transcript = ""
    try:
        transcript = transcribe_with_grok(local_path)
    except Exception as error:  # noqa: BLE001 - звук необязателен, кадры могут заменить
        print(f"Не удалось расшифровать звук видео: {error}")

    if not frames and not transcript:
        raise RuntimeError("не удалось ни разобрать кадры, ни расслышать звук в видео")

    text_parts = [task_description]
    if transcript:
        text_parts.append(f"Расшифровка произнесённой речи:\n{transcript}")
    if not frames:
        text_parts.append("(кадры видео недоступны — пост только по звуку)")
    text_parts.append(POST_STYLE_HINT)

    content = [_image_content_part(frame) for frame in frames]
    content.append({"type": "text", "text": "\n\n".join(text_parts)})

    try:
        response = xai_client.chat.completions.create(
            model=GROK_VISION_MODEL,
            messages=[{"role": "user", "content": content}],
        )
        return validate_source_text(response.choices[0].message.content or "")
    finally:
        for frame in frames:
            if os.path.exists(frame):
                os.remove(frame)


def process_media_message(
    message,
    file_id: str,
    suffix: str,
    asker,
    file_size: int | None = None,
):
    """Общий сценарий для фото/видео/кружочков/голосовых:

    скачать файл -> asker(local_path) -> ответить готовым постом. asker —
    одна из ask_grok_about_* выше, разная для каждого типа медиа.
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
        post_text = asker(local_path)
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
        "сообщения в готовые посты через Grok.\n\n"
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
    # ID и username вместо запроса к Grok. Это удобный способ узнать,
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

    processing_msg = reply(message, "⏳ Спрашиваю у Grok...")
    try:
        post_text = ask_grok_text(source_text)
        edit_reply(processing_msg, f"✅ Готовый пост:\n\n{post_text}")
    except Exception as error:  # noqa: BLE001
        print(f"Ошибка запроса к Grok: {error}")
        edit_reply(processing_msg, f"❌ Ошибка запроса к Grok: {error}")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if not require_admin(message):
        return
    # message.photo — список размеров одной и той же фотографии,
    # последний элемент — самое высокое разрешение.
    file_id = message.photo[-1].file_id
    task = "Посмотри на фотографию, опиши, что на ней происходит (включая любой видимый текст), и на основе этого напиши пост."
    process_media_message(
        message, file_id, ".jpg",
        lambda path: ask_grok_about_image(path, task),
        message.photo[-1].file_size,
    )


@bot.message_handler(content_types=["video"])
def handle_video(message):
    if not require_admin(message):
        return
    file_id = message.video.file_id
    task = "Посмотри видео, перескажи его сюжет и содержание (включая произносимую речь, если есть) и на основе этого напиши пост."
    process_media_message(
        message, file_id, ".mp4",
        lambda path: ask_grok_about_video(path, task),
        message.video.file_size,
    )


@bot.message_handler(content_types=["video_note"])
def handle_video_note(message):
    if not require_admin(message):
        return
    file_id = message.video_note.file_id
    task = "Это видео-кружок. Расшифруй речь, которая на нём звучит, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(
        message, file_id, ".mp4",
        lambda path: ask_grok_about_speech(path, task),
        message.video_note.file_size,
    )


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    if not require_admin(message):
        return
    file_id = message.voice.file_id
    task = "Это голосовое сообщение. Расшифруй речь, переведи её в текст и на основе сказанного напиши пост."
    process_media_message(
        message, file_id, ".ogg",
        lambda path: ask_grok_about_speech(path, task),
        message.voice.file_size,
    )


# ---------------------------------------------------------------------------
# 6. Запуск бота
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling(skip_pending=True)
