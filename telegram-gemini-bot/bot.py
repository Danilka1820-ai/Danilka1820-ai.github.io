"""
Telegram-бот на pyTelegramBotAPI + Google Gemini.

Возможности:
  - Текст -> Gemini делает пост, который бот автоматически публикует в канал.
  - Фото/видео -> бот скачивает файл, отдаёт его Gemini,
    получает описание сюжета/текста и превращает в пост.
  - Альбом (несколько фото одним сообщением, media_group_id) -> собирается
    в ОДИН пост по всем фото сразу, а не в N отдельных (см. process_album).
    Группировка обновлений выполняется основным bot_api_cycle.py;
    при локальном bot.infinity_polling() фото обрабатываются отдельно.
  - Кружочки (video_note) и голосовые (voice) -> бот скачивает аудио/видео,
    Gemini расшифровывает речь и формирует готовый пост.
  - /post <текст>            — публикует пост в канал, возвращает ID сообщения.
  - /ask <текст>             — задаёт вопрос Gemini без публикации в канал.
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
from contextlib import ExitStack

import telebot
from google import genai
from dotenv import load_dotenv

import gemini_resilience
import telegram_delivery
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
#
# threaded=False — не косметика. По умолчанию telebot обрабатывает апдейты
# в фоновом пуле из 2 ДЕМОН-потоков: process_new_updates() возвращается
# сразу, не дожидаясь ответа хендлера. Основной bot_api_cycle.py должен
# подтвердить Telegram offset только после завершения обработчиков. При
# threaded=False обработка остаётся в текущем потоке, поэтому цикл не
# подтвердит пачку раньше, чем закончатся ответы Gemini.
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None, threaded=False)

gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=gemini_resilience.build_retry_http_options(),
)

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

delivery_checkpoint_errors: list[telegram_delivery.DeliveryCheckpointError] = []
processing_errors: list[Exception] = []


# ---------------------------------------------------------------------------
# 3. Вспомогательные функции
# ---------------------------------------------------------------------------

def is_admin(message) -> bool:
    """Разрешает личные сообщения только владельцу, указанному в ADMIN_ID."""
    chat = getattr(message, "chat", None)
    return (
        getattr(chat, "type", None) == "private"
        and user_id_from_message(message) == ADMIN_ID
    )


def require_admin(message) -> bool:
    """Единая защита команд и всех обращений к платному внешнему API."""
    if is_admin(message):
        return True
    bot.reply_to(message, "⛔ Этот бот доступен только владельцу.")
    return False


def reply(message, text: str):
    """Отправляет ответ, гарантированно укладывающийся в лимит Telegram."""
    return bot.reply_to(message, clip_for_telegram(text))


def _error_message_for_user(error: Exception) -> str:
    """Текст ошибки, безопасный для показа в чате.

    GeminiUnavailableError/GeminiConfigurationError несут сырой ответ Gemini
    в str(error) (код, статус, тело JSON) — это годится для лога (см. print()
    рядом с каждым вызовом этой функции), но не для пользователя. Остальные
    исключения в этом боте (ValueError из validate_*, TimeoutError ожидания
    файла и т.п.) уже сформулированы по-русски и понятны как есть.
    """
    if isinstance(error, (gemini_resilience.GeminiUnavailableError, gemini_resilience.GeminiConfigurationError)):
        return gemini_resilience.friendly_error_message(error)
    return _redact_secrets(str(error))


def _redact_secrets(text: str) -> str:
    for secret in (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY):
        if secret:
            text = text.replace(secret, "[СКРЫТО]")
    return text


def edit_reply(processing_message, text: str) -> None:
    bot.edit_message_text(
        clip_for_telegram(text),
        chat_id=processing_message.chat.id,
        message_id=processing_message.message_id,
    )


class PartialChannelDeliveryError(RuntimeError):
    def __init__(self, original_error: Exception):
        super().__init__(str(original_error))
        self.original_error = original_error


def _send_channel_content(text: str, media_paths: list[str], media_type: str):
    caption = text if len(text) <= 1024 else None
    with ExitStack() as stack:
        files = [stack.enter_context(open(path, "rb")) for path in media_paths]
        if media_type == "album":
            items = [
                telebot.types.InputMediaPhoto(
                    media_file,
                    caption=caption if index == 0 else None,
                )
                for index, media_file in enumerate(files)
            ]
            sent = bot.send_media_group(CHANNEL_ID, media=items)
            first_sent = sent[0] if sent else None
        elif media_type == "photo":
            first_sent = bot.send_photo(CHANNEL_ID, files[0], caption=caption)
        elif media_type == "video":
            first_sent = bot.send_video(CHANNEL_ID, files[0], caption=caption)
        elif media_type == "voice":
            first_sent = bot.send_voice(CHANNEL_ID, files[0], caption=caption)
        elif media_type == "video_note":
            first_sent = bot.send_video_note(CHANNEL_ID, files[0])
        else:
            raise ValueError(f"Неподдерживаемый тип вложения для канала: {media_type}")

        if caption is None or media_type == "video_note":
            try:
                bot.send_message(CHANNEL_ID, text)
            except Exception as error:
                raise PartialChannelDeliveryError(error) from error
        return first_sent


def publish_to_channel(
    message,
    text: str,
    processing_message=None,
    media_paths: list[str] | None = None,
    media_type: str | None = None,
) -> bool:
    """Publish generated text and its original media once, after checkpointing."""
    try:
        text = validate_source_text(text)
        if len(text) > TELEGRAM_MESSAGE_LIMIT:
            raise ValueError(
                f"Результат Gemini длиннее лимита Telegram ({TELEGRAM_MESSAGE_LIMIT} символов); "
                "пост не опубликован."
            )
        status, should_send = telegram_delivery.begin_delivery(
            TELEGRAM_BOT_TOKEN,
            message.chat.id,
            message.message_id,
        )
    except telegram_delivery.DeliveryCheckpointError as error:
        delivery_checkpoint_errors.append(error)
        print(_redact_secrets(str(error)))
        reply(message, f"❌ Не удалось безопасно подготовить публикацию: {_error_message_for_user(error)}")
        return False
    except ValueError as error:
        reply(message, f"❌ {error}")
        return False

    if not should_send:
        if status == "published":
            notice = "Это сообщение уже было опубликовано; повторно не отправляю."
        else:
            notice = (
                "Предыдущий запуск мог успеть опубликовать пост, но не сохранил результат. "
                "Проверьте канал; повторно отправить этот материал автоматически нельзя."
            )
        if processing_message:
            edit_reply(processing_message, notice)
        else:
            reply(message, notice)
        return True

    try:
        if media_paths:
            sent = _send_channel_content(text, media_paths, media_type or "")
        else:
            sent = bot.send_message(CHANNEL_ID, text)
    except Exception as error:  # noqa: BLE001 - внешний API может завершиться неоднозначно
        processing_errors.append(error)
        partial_delivery = isinstance(error, PartialChannelDeliveryError)
        telegram_error = error.original_error if partial_delivery else error
        error_code = getattr(telegram_error, "error_code", None)
        response = getattr(telegram_error, "response", None)
        if error_code is None:
            error_code = getattr(response, "status_code", None)
        if not partial_delivery and isinstance(error_code, int) and 400 <= error_code < 500:
            try:
                telegram_delivery.finish_delivery(
                    TELEGRAM_BOT_TOKEN, message.chat.id, message.message_id, "failed"
                )
            except telegram_delivery.DeliveryCheckpointError as state_error:
                print(_redact_secrets(str(state_error)))
        detail = _error_message_for_user(telegram_error)
        print(f"Не удалось подтвердить публикацию Telegram: {_redact_secrets(str(error))}")
        notice = f"❌ Telegram не подтвердил публикацию: {detail}"
        if partial_delivery:
            notice += " Часть поста уже могла появиться; повторно автоматически не отправляю."
        elif error_code is None or error_code >= 500:
            notice += " Результат отправки неопределён; повторно этот материал не отправляю."
        if processing_message:
            edit_reply(processing_message, notice)
        else:
            reply(message, notice)
        return False

    try:
        telegram_delivery.finish_delivery(
            TELEGRAM_BOT_TOKEN, message.chat.id, message.message_id, "published"
        )
    except telegram_delivery.DeliveryCheckpointError as error:
        # The durable "attempted" checkpoint still prevents a duplicate if this
        # process dies before the workflow commits its final state.
        print(_redact_secrets(str(error)))

    notice = f"✅ Опубликовано в канале. ID сообщения: {sent.message_id}"
    if processing_message:
        edit_reply(processing_message, notice)
    else:
        reply(message, notice)
    return True


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


def _generate_content(contents):
    """generate_content с устойчивостью к временной перегрузке Gemini — см.
    gemini_resilience.py: SDK сам повторяет 503/429/5xx/сетевые сбои внутри
    одной модели, а эта обёртка ещё и переключается на резервную модель
    (GEMINI_FALLBACK_MODELS), если основная не ответила совсем."""
    response, used_model = gemini_resilience.generate_content_resilient(gemini_client, contents)
    if used_model != gemini_resilience.GEMINI_PRIMARY_MODEL:
        print(f"Gemini: использована резервная модель {used_model}")
    return response


def ask_gemini_text(user_text: str) -> str:
    """Отправляет обычный текст в Gemini и просит сделать из него пост."""
    user_text = validate_source_text(user_text)
    prompt = (
        "Перепиши следующий текст в виде красивого поста для Telegram-канала.\n"
        f"{POST_STYLE_HINT}\n\n"
        f"Исходный текст:\n{user_text}"
    )
    response = _generate_content(prompt)
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


def ask_gemini_about_album(local_paths: list, task_description: str) -> str:
    """Как ask_gemini_about_media, но с несколькими фото альбома разом: все
    файлы и один общий промпт уходят в Gemini одним запросом, чтобы получился
    один пост, а не N ответов на N частей одной подборки.
    """
    uploaded = []
    try:
        for path in local_paths:
            uploaded.append(gemini_client.files.upload(file=path))
        prompt = f"{task_description}\n{POST_STYLE_HINT}"
        response = _generate_content(contents=[*uploaded, prompt])
        return validate_source_text(getattr(response, "text", ""))
    finally:
        for uploaded_file in uploaded:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception as error:  # удаление не должно скрыть основной ответ
                print(f"Не удалось удалить временный файл Gemini: {error}")


def group_photo_messages(update):
    """Апдейт — фото из альбома (media_group_id есть)? Возвращает Message,
    иначе None. Только фото: у видео и голосовых альбомов не бывает."""
    message = getattr(update, "message", None)
    if message is None or message.content_type != "photo" or not message.media_group_id:
        return None
    return message


def process_album(messages: list) -> bool:
    """Собирает альбом (несколько фото с общим media_group_id) в ОДИН пост.

    Telegram присылает каждое фото альбома отдельным апдейтом, а подпись —
    только у одного из них. Раньше каждое фото уходило в Gemini само по
    себе, и вместо одной подборки зритель получал столько же черновиков
    поста, сколько было фото — этой функции раньше не было вовсе.
    """
    if not messages:
        return True
    first = messages[0]
    if not is_admin(first):
        bot.reply_to(first, "⛔ Этот бот доступен только владельцу.")
        return True

    caption = ""
    for message in messages:
        if getattr(message, "caption", None):
            caption = message.caption
            break

    photos = [m for m in messages if m.photo]
    total_size = sum((m.photo[-1].file_size or 0) for m in photos)
    try:
        validate_media_size(total_size or None)
    except ValueError as error:
        bot.reply_to(first, f"❌ {error}")
        return True

    processing_msg = bot.reply_to(
        first, f"⏳ Обрабатываю альбом из {len(photos)} фото, подождите немного..."
    )
    local_paths = []
    try:
        for message in photos:
            local_paths.append(download_telegram_file(message.photo[-1].file_id, ".jpg"))
        task = (
            f"Посмотри на эти {len(local_paths)} фотографий из одного альбома — "
            "это один сюжет или подборка, а не отдельные снимки. Опиши, что на "
            "них происходит в целом, и на основе этого напиши ОДИН пост."
        )
        if caption:
            task += f"\nАвтор подписал альбом так: «{caption}». Учти это в посте."
        post_text = ask_gemini_about_album(local_paths, task)
        return publish_to_channel(
            first,
            post_text,
            processing_msg,
            media_paths=local_paths,
            media_type="album",
        )
    except Exception as error:  # noqa: BLE001 - хотим сообщить пользователю о любой ошибке
        if isinstance(error, telegram_delivery.DeliveryCheckpointError):
            delivery_checkpoint_errors.append(error)
        processing_errors.append(error)
        print(f"Не удалось обработать альбом: {_redact_secrets(str(error))}")
        edit_reply(processing_msg, f"❌ Не удалось обработать альбом: {_error_message_for_user(error)}")
        return False
    finally:
        for path in local_paths:
            if os.path.exists(path):
                os.remove(path)


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
        media_type = getattr(message, "content_type", "")
        if media_type not in {"photo", "video", "voice", "video_note"}:
            media_type = "video" if suffix in {".mp4", ".mov", ".mkv"} else "photo"
        publish_to_channel(
            message,
            post_text,
            processing_msg,
            media_paths=[local_path],
            media_type=media_type,
        )
    except Exception as error:  # noqa: BLE001 - хотим сообщить пользователю о любой ошибке
        if isinstance(error, telegram_delivery.DeliveryCheckpointError):
            delivery_checkpoint_errors.append(error)
        processing_errors.append(error)
        print(f"Не удалось обработать файл: {_redact_secrets(str(error))}")
        edit_reply(processing_msg, f"❌ Не удалось обработать файл: {_error_message_for_user(error)}")
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
        "/ask <вопрос> — получить ответ Gemini без публикации\n"
        "/edit <ID> <новый текст> — изменить пост по ID\n"
        "/delete <ID> — удалить пост по ID\n"
        "/status — диагностика Gemini (модели, предохранитель, последняя ошибка)",
    )


@bot.message_handler(commands=["status"])
def handle_status(message):
    if not require_admin(message):
        return
    snapshot = gemini_resilience.diagnostics_snapshot()
    lines = ["🩺 Диагностика Gemini:"] + [f"{key}: {value}" for key, value in snapshot.items()]
    reply(message, "\n".join(lines))


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
        publish_to_channel(message, text)
    except Exception as error:  # noqa: BLE001
        if isinstance(error, telegram_delivery.DeliveryCheckpointError):
            delivery_checkpoint_errors.append(error)
        processing_errors.append(error)
        reply(message, f"❌ Не удалось опубликовать: {_error_message_for_user(error)}")


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
        processing_errors.append(error)
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
        processing_errors.append(error)
        reply(message, f"❌ Не удалось удалить пост: {error}")


@bot.message_handler(commands=["ask"])
def handle_ask(message):
    if not require_admin(message):
        return
    source_text = message.text.partition(" ")[2].strip()
    try:
        source_text = validate_source_text(source_text)
    except ValueError as error:
        reply(message, f"Использование: /ask <текст вопроса>\n\n❌ {error}")
        return

    processing_msg = reply(message, "⏳ Спрашиваю у Gemini без публикации...")
    try:
        answer = ask_gemini_question(source_text)
        edit_reply(processing_msg, f"💬 Ответ Gemini (не опубликован):\n\n{answer}")
    except Exception as error:  # noqa: BLE001
        processing_errors.append(error)
        print(f"Ошибка запроса к Gemini: {_redact_secrets(str(error))}")
        edit_reply(
            processing_msg,
            f"❌ Ошибка запроса к Gemini: {_error_message_for_user(error)}",
        )


def ask_gemini_question(question: str) -> str:
    """Answer a private question without applying the channel-post prompt."""
    question = validate_source_text(question)
    response = _generate_content(
        "Ответь по-русски на вопрос владельца. Не превращай ответ в пост "
        "для канала и не добавляй хэштеги.\n\n"
        f"Вопрос:\n{question}"
    )
    return validate_source_text(getattr(response, "text", ""))


# ---------------------------------------------------------------------------
# 5. Обработка текста, фото, видео, кружочков и голосовых
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=["text"])
def handle_text(message):
    # Registered commands are handled before this text handler.

    if not require_admin(message):
        return

    if (message.text or "").lstrip().startswith("/"):
        reply(message, "Неизвестная команда. Отправьте материал обычным сообщением или используйте /ask.")
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
        publish_to_channel(message, post_text, processing_msg)
    except Exception as error:  # noqa: BLE001
        if isinstance(error, telegram_delivery.DeliveryCheckpointError):
            delivery_checkpoint_errors.append(error)
        processing_errors.append(error)
        print(f"Ошибка запроса к Gemini: {_redact_secrets(str(error))}")
        edit_reply(processing_msg, f"❌ Ошибка запроса к Gemini: {_error_message_for_user(error)}")


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
