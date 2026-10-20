"""One-shot Telegram Bot API cycle for the production GitHub Actions job.

This replaces the old self-restarting long-polling chain. A scheduled job reads
one pending batch from Telegram, mirrors channel posts into the static site,
processes administrator messages through the existing bot handlers, records
successful bot publications directly, then acknowledges only after persistence.
The public t.me scraper is only a reconciliation fallback.
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
POSTS_FILE = ROOT / "data" / "posts.json"
MEDIA_DIR = ROOT / "assets" / "posts"
BOT_API = "https://api.telegram.org"
MAX_UPDATES = 100
MAX_POSTS = int(os.getenv("TG_MAX_POSTS", "40"))
MAX_BOT_DOWNLOAD_BYTES = 20 * 1024 * 1024
MOSCOW = ZoneInfo("Europe/Moscow")
MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_FFPROBE = shutil.which("ffprobe") is not None


class TelegramApiError(RuntimeError):
    pass


def _safe_error_message(error: Exception, token: str) -> str:
    return str(error).replace(token, "[СКРЫТО]") if token else str(error)


def bot_api(token: str, method: str, payload: dict | None = None, *, timeout: int = 35):
    try:
        response = requests.post(
            f"{BOT_API}/bot{token}/{method}",
            json=payload or {},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        message = _safe_error_message(error, token)
        raise TelegramApiError(f"Telegram API {method} failed: {message}") from None
    if not body.get("ok"):
        raise TelegramApiError(
            f"Telegram API {method} returned error: {body.get('description') or body}"
        )
    return body.get("result")


def fetch_updates(token: str) -> list[dict]:
    # No offset here: fetching does not acknowledge the batch. We acknowledge only
    # after both site ingestion and bot handlers have succeeded.
    result = bot_api(
        token,
        "getUpdates",
        {
            "limit": MAX_UPDATES,
            "timeout": 20,
            "allowed_updates": [
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
            ],
        },
        timeout=30,
    )
    return result if isinstance(result, list) else []


def acknowledge_updates(token: str, max_update_id: int) -> None:
    # offset=N confirms updates with update_id < N. A newer update that arrives
    # between fetch and ack is therefore not lost and remains pending.
    bot_api(token, "getUpdates", {"offset": max_update_id + 1, "limit": 1, "timeout": 0})


def channel_message(raw_update: dict) -> dict | None:
    return raw_update.get("channel_post") or raw_update.get("edited_channel_post")


def _configured_channel_matches(message: dict, configured: str) -> bool:
    chat = message.get("chat") or {}
    configured = (configured or "").strip()
    if not configured:
        return False
    if configured.startswith("@"):
        return str(chat.get("username") or "").lower() == configured[1:].lower()
    try:
        return int(chat.get("id")) == int(configured)
    except (TypeError, ValueError):
        return False


def select_channel_updates(raw_updates: list[dict], configured_channel: str) -> list[dict]:
    selected = []
    for raw in raw_updates:
        message = channel_message(raw)
        if message and _configured_channel_matches(message, configured_channel):
            selected.append(raw)
    return selected


def _format_datetime(unix_seconds: int | float | None) -> tuple[str, str]:
    dt = datetime.fromtimestamp(float(unix_seconds or 0), tz=timezone.utc)
    local = dt.astimezone(MOSCOW)
    return (
        f"{local.day} {MONTHS[local.month]} {local.year}",
        dt.isoformat(timespec="seconds"),
    )


def _extract_tags(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"#([\w]{2,})", text, flags=re.UNICODE)))


def _post_number(post: dict) -> int:
    try:
        return int(str(post.get("id", "")).rsplit("/", 1)[-1])
    except ValueError:
        return -1


def _channel_username(message: dict, fallback: str) -> str:
    username = str((message.get("chat") or {}).get("username") or "").strip()
    if username:
        return username
    return fallback.lstrip("@").strip() or "danilka2028k"


def _video_dimensions(path: Path) -> tuple[int, int] | None:
    if not HAS_FFPROBE:
        return None
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        width, height = (int(part) for part in result.stdout.strip().split("x", 1))
    except (ValueError, TypeError):
        return None
    return (width, height) if width and height else None


def _run_ffmpeg(args: list[str]) -> bool:
    if not HAS_FFMPEG:
        return False
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _optimize_image(path: Path) -> None:
    if not HAS_FFMPEG or path.suffix.lower() not in {".jpg", ".jpeg"}:
        return
    tmp = path.with_name(f"{path.stem}.tmp.jpg")
    if _run_ffmpeg(["-i", str(path), "-vf", "scale='min(1200,iw)':-2", "-q:v", "5", str(tmp)]):
        if tmp.exists() and 1024 < tmp.stat().st_size < path.stat().st_size:
            tmp.replace(path)
            return
    tmp.unlink(missing_ok=True)


def _make_video_variant(
    path: Path,
    width: int,
    crf: int,
    suffix: str,
    *,
    force: bool = False,
) -> Path | None:
    dims = _video_dimensions(path)
    if dims and dims[0] <= int(width * 1.1):
        return None
    target = path.with_name(f"{path.stem}-{suffix}.mp4")
    if not force and target.exists() and target.stat().st_size > 1024:
        return target
    temporary = target.with_name(f"{target.stem}.tmp.mp4")
    ok = _run_ffmpeg(
        [
            "-i",
            str(path),
            "-vf",
            f"scale='min({width},iw)':-2",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k" if suffix == "low" else "96k",
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    if not ok or not temporary.exists() or temporary.stat().st_size < 1024:
        temporary.unlink(missing_ok=True)
        return None
    if temporary.stat().st_size >= path.stat().st_size * 0.9:
        temporary.unlink(missing_ok=True)
        return None
    temporary.replace(target)
    return target


def _optimize_video(path: Path, kind: str) -> None:
    if not HAS_FFMPEG:
        return
    tmp = path.with_name(f"{path.stem}.tmp.mp4")
    max_width = 1080 if kind == "round" else 1920
    crf = 32 if kind == "round" else 28
    ok = _run_ffmpeg(
        [
            "-i",
            str(path),
            "-vf",
            f"scale='min({max_width},iw)':-2",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k" if kind == "round" else "96k",
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
    )
    if ok and tmp.exists() and tmp.stat().st_size > 1024:
        # Prefer the transcoded H.264 output even if slightly larger; browser
        # compatibility matters more than a marginal size increase.
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)


def _download_media_file(
    token: str,
    file_obj: dict,
    post_number: int,
    index: str | int,
    default_ext: str,
    *,
    force_download: bool = False,
) -> Path:
    file_id = str(file_obj.get("file_id") or "")
    if not file_id:
        raise TelegramApiError("media has no file_id")
    declared = file_obj.get("file_size")
    info = bot_api(token, "getFile", {"file_id": file_id})
    file_path = str((info or {}).get("file_path") or "")
    if not file_path:
        raise TelegramApiError("getFile returned no file_path")
    suffix = Path(file_path).suffix.lower().lstrip(".")
    ext = suffix if suffix in {"jpg", "jpeg", "png", "webp", "mp4"} else default_ext
    if ext == "jpeg":
        ext = "jpg"
    target = MEDIA_DIR / f"danilka2028k-{post_number}-{index}.{ext}"
    if not force_download and target.exists() and target.stat().st_size > 1024:
        return target
    if declared and int(declared) > MAX_BOT_DOWNLOAD_BYTES:
        raise TelegramApiError(
            f"file is {int(declared) / 1048576:.1f} MB; official Bot API limit is 20 MB"
        )
    try:
        response = requests.get(f"{BOT_API}/file/bot{token}/{file_path}", timeout=90)
        response.raise_for_status()
    except requests.RequestException as error:
        message = _safe_error_message(error, token)
        raise TelegramApiError(f"Telegram file download failed: {message}") from None
    if len(response.content) > MAX_BOT_DOWNLOAD_BYTES:
        raise TelegramApiError("download exceeded official 20 MB Bot API limit")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.download")
    try:
        temporary.write_bytes(response.content)
        temporary.replace(target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise TelegramApiError("Не удалось сохранить загруженный файл") from error
    return target


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _download_thumbnail(
    token: str,
    thumb: dict | None,
    post_number: int,
    index: str,
    *,
    force_download: bool = False,
) -> str:
    if not thumb or not thumb.get("file_id"):
        return ""
    try:
        download_options = {"force_download": True} if force_download else {}
        target = _download_media_file(token, thumb, post_number, index, "jpg", **download_options)
        _optimize_image(target)
        return _relative(target)
    except TelegramApiError as error:
        print(f"Thumbnail skipped: {error}")
        return ""


def _reuse_video_entry(entry: dict, source: Path, kind: str) -> dict:
    reused = dict(entry)
    variants = (
        ("srcMid", "sizeMid", "wMid", "hMid", 720 if kind == "round" else 1280, 28, "mid"),
        (
            "srcLow",
            "sizeLow",
            "wLow",
            "hLow",
            360 if kind == "round" else 640,
            34 if kind == "round" else 32,
            "low",
        ),
    )
    for src_key, size_key, width_key, height_key, width, crf, suffix in variants:
        relative = reused.get(src_key)
        if not relative or (ROOT / str(relative)).is_file():
            continue
        variant = _make_video_variant(source, width, crf, suffix, force=True)
        if variant is None:
            for key in (src_key, size_key, width_key, height_key):
                reused.pop(key, None)
            continue
        reused[src_key] = _relative(variant)
        reused[size_key] = variant.stat().st_size
        dims = _video_dimensions(variant)
        if dims:
            reused[width_key], reused[height_key] = dims
    return reused


def _media_from_message(
    token: str,
    message: dict,
    post_number: int,
    start_index: int,
    existing_entry: dict | None = None,
    force_download: bool = False,
) -> list[dict]:
    entries: list[dict] = []
    message_id = int(message.get("message_id") or 0)

    photos = message.get("photo") or []
    if photos:
        photo = photos[-1]
        unchanged = (
            existing_entry
            and existing_entry.get("type") == "photo"
            and photo.get("file_unique_id")
            and existing_entry.get("telegramFileUniqueId") == photo.get("file_unique_id")
            and (ROOT / str(existing_entry.get("src") or "")).is_file()
        )
        if unchanged:
            return [existing_entry]
        try:
            download_options = (
                {"force_download": True}
                if existing_entry is not None or force_download
                else {}
            )
            target = _download_media_file(
                token, photo, post_number, start_index, "jpg", **download_options
            )
            _optimize_image(target)
            entries.append(
                {
                    "type": "photo",
                    "src": _relative(target),
                    "telegramMessageId": message_id,
                    "telegramFileUniqueId": photo.get("file_unique_id"),
                }
            )
        except TelegramApiError as error:
            raise TelegramApiError(f"Не удалось сохранить фото сообщения {message_id}") from error
        return entries

    video = message.get("video")
    video_note = message.get("video_note")
    media_obj = video_note or video
    if media_obj:
        kind = "round" if video_note else "video"
        unchanged = (
            existing_entry
            and existing_entry.get("type") == kind
            and media_obj.get("file_unique_id")
            and existing_entry.get("telegramFileUniqueId") == media_obj.get("file_unique_id")
            and (ROOT / str(existing_entry.get("src") or "")).is_file()
        )
        if unchanged:
            return [_reuse_video_entry(existing_entry, ROOT / existing_entry["src"], kind)]
        thumbnail_options = (
            {"force_download": True}
            if existing_entry is not None or force_download
            else {}
        )
        poster = _download_thumbnail(
            token,
            media_obj.get("thumbnail"),
            post_number,
            f"{start_index}p",
            **thumbnail_options,
        )
        try:
            download_options = (
                {"force_download": True}
                if existing_entry is not None or force_download
                else {}
            )
            target = _download_media_file(
                token, media_obj, post_number, start_index, "mp4", **download_options
            )
            _optimize_video(target, kind)
            mid = _make_video_variant(
                target,
                720 if kind == "round" else 1280,
                28,
                "mid",
                force=existing_entry is not None or force_download,
            )
            low = _make_video_variant(
                target,
                360 if kind == "round" else 640,
                34 if kind == "round" else 32,
                "low",
                force=existing_entry is not None or force_download,
            )
            entry: dict = {
                "type": kind,
                "src": _relative(target),
                "poster": poster,
                "size": target.stat().st_size,
                "telegramMessageId": message_id,
                "telegramFileUniqueId": media_obj.get("file_unique_id"),
            }
            dims = _video_dimensions(target)
            if dims:
                entry["w"], entry["h"] = dims
            if mid:
                entry["srcMid"] = _relative(mid)
                entry["sizeMid"] = mid.stat().st_size
                dims = _video_dimensions(mid)
                if dims:
                    entry["wMid"], entry["hMid"] = dims
            if low:
                entry["srcLow"] = _relative(low)
                entry["sizeLow"] = low.stat().st_size
                dims = _video_dimensions(low)
                if dims:
                    entry["wLow"], entry["hLow"] = dims
            entries.append(entry)
        except TelegramApiError as error:
            raise TelegramApiError(f"Не удалось сохранить видео сообщения {message_id}") from error
        return entries

    voice = message.get("voice") or message.get("audio")
    if voice:
        unchanged = (
            existing_entry
            and existing_entry.get("type") == "audio"
            and voice.get("file_unique_id")
            and existing_entry.get("telegramFileUniqueId") == voice.get("file_unique_id")
            and (ROOT / str(existing_entry.get("src") or "")).is_file()
        )
        if unchanged:
            return [existing_entry]
        try:
            download_options = (
                {"force_download": True}
                if existing_entry is not None or force_download
                else {}
            )
            target = _download_media_file(
                token, voice, post_number, start_index, "ogg", **download_options
            )
            entries.append(
                {
                    "type": "audio",
                    "src": _relative(target),
                    "size": target.stat().st_size,
                    "telegramMessageId": message_id,
                    "telegramFileUniqueId": voice.get("file_unique_id"),
                }
            )
        except TelegramApiError as error:
            raise TelegramApiError(f"Не удалось сохранить аудио сообщения {message_id}") from error
        return entries

    return entries


def _load_posts() -> list[dict]:
    if not POSTS_FILE.exists():
        return []
    payload = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("data/posts.json must contain an array")
    return payload


def _save_posts(posts: list[dict]) -> None:
    POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = POSTS_FILE.with_name(f"{POSTS_FILE.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(POSTS_FILE)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _sent_media(message, media_type: str) -> tuple[str, str | None] | None:
    if media_type == "album":
        kind, media = "photo", getattr(message, "photo", None)
    elif media_type == "photo":
        kind, media = "photo", getattr(message, "photo", None)
    elif media_type == "video":
        kind, media = "video", getattr(message, "video", None)
    elif media_type == "video_note":
        kind, media = "round", getattr(message, "video_note", None)
    elif media_type == "voice":
        kind, media = "audio", getattr(message, "voice", None)
    else:
        return None
    if isinstance(media, list):
        media = media[-1] if media else None
    if not media:
        return None
    return kind, getattr(media, "file_unique_id", None)


def record_bot_publication(
    text: str,
    sent_messages: list,
    media_paths: list[str],
    media_type: str | None,
) -> None:
    if not sent_messages:
        raise RuntimeError("Telegram returned no messages to save on the site")
    first = sent_messages[0]
    message_ids = [int(message.message_id) for message in sent_messages]
    canonical_number = message_ids[0]
    username = str(getattr(getattr(first, "chat", None), "username", "") or "danilka2028k")
    group_id = getattr(first, "media_group_id", None)
    posts = _load_posts()
    synthetic_messages = [
        {"message_id": message_id}
        for message_id in message_ids
    ]
    existing = _find_existing(posts, synthetic_messages, str(group_id) if group_id else None)
    if existing:
        canonical_number = _post_number(existing)

    old_media = list(existing.get("media") or []) if existing else []
    old_by_id = {
        int(item["telegramMessageId"]): item
        for item in old_media
        if str(item.get("telegramMessageId") or "").isdigit()
    }
    media_messages = [
        (message, media)
        for message in sent_messages
        if media_type and (media := _sent_media(message, media_type))
    ]
    if len(media_messages) != len(media_paths):
        raise RuntimeError("Telegram media result does not match uploaded attachments")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []
    new_media: list[dict] = []
    try:
        for index, ((message, (kind, unique_id)), source_name) in enumerate(
            zip(media_messages, media_paths, strict=True)
        ):
            message_id = int(message.message_id)
            previous = old_by_id.get(message_id)
            if previous and (ROOT / str(previous.get("src") or "")).is_file():
                new_media.append(previous)
                continue

            source = Path(source_name)
            suffix = source.suffix.lower()
            if not source.is_file() or suffix not in {
                ".jpg", ".jpeg", ".png", ".webp", ".mp4", ".ogg", ".m4a", ".mp3",
            }:
                raise RuntimeError("Published media file is missing or unsupported")
            target = MEDIA_DIR / f"danilka2028k-{canonical_number}-{index}{suffix}"
            created_target = not target.exists()
            if target.exists():
                if not filecmp.cmp(source, target, shallow=False):
                    raise RuntimeError("Published media path already contains different data")
            else:
                temporary = target.with_name(f"{target.name}.copy")
                try:
                    shutil.copyfile(source, temporary)
                    temporary.replace(target)
                except OSError:
                    temporary.unlink(missing_ok=True)
                    raise
                created_files.append(target)

            if created_target and kind == "photo":
                _optimize_image(target)
            elif created_target and kind in {"video", "round"}:
                _optimize_video(target, kind)
            entry: dict = {
                "type": kind,
                "src": _relative(target),
                "telegramMessageId": message_id,
            }
            if unique_id:
                entry["telegramFileUniqueId"] = unique_id
            if kind in {"video", "round"}:
                entry["size"] = target.stat().st_size
                dimensions = _video_dimensions(target)
                if dimensions:
                    entry["w"], entry["h"] = dimensions
                mid_target = target.with_name(f"{target.stem}-mid.mp4")
                low_target = target.with_name(f"{target.stem}-low.mp4")
                mid_existed, low_existed = mid_target.exists(), low_target.exists()
                mid = _make_video_variant(
                    target,
                    720 if kind == "round" else 1280,
                    28,
                    "mid",
                )
                if mid and not mid_existed:
                    created_files.append(mid)
                low = _make_video_variant(
                    target,
                    360 if kind == "round" else 640,
                    34 if kind == "round" else 32,
                    "low",
                )
                if low and not low_existed:
                    created_files.append(low)
                for variant, src_key, size_key, width_key, height_key in (
                    (mid, "srcMid", "sizeMid", "wMid", "hMid"),
                    (low, "srcLow", "sizeLow", "wLow", "hLow"),
                ):
                    if variant:
                        entry[src_key] = _relative(variant)
                        entry[size_key] = variant.stat().st_size
                        dimensions = _video_dimensions(variant)
                        if dimensions:
                            entry[width_key], entry[height_key] = dimensions
            elif kind == "audio":
                entry["size"] = target.stat().st_size
            new_media.append(entry)

        date, iso = _format_datetime(getattr(first, "date", None))
        text = str(text).strip()
        old_ids = (
            {
                int(message_id)
                for message_id in existing.get("telegramMessageIds", [])
                if str(message_id).isdigit()
            }
            if existing
            else set()
        )
        if existing:
            if any("telegramMessageId" in item for item in old_media):
                retained_media = [
                    item
                    for item in old_media
                    if int(item.get("telegramMessageId") or -1) not in set(message_ids)
                ]
            else:
                retained_media = []
            existing.update(
                {
                    "date": existing.get("date") or date,
                    "datetime": existing.get("datetime") or iso,
                    "text": text or " ",
                    "link": existing.get("link") or f"https://t.me/{username}/{canonical_number}",
                    "tags": _extract_tags(text),
                    "media": retained_media + new_media,
                    "telegramMessageIds": sorted(old_ids | set(message_ids)),
                }
            )
            if group_id:
                existing["telegramMediaGroupId"] = str(group_id)
        else:
            post = {
                "id": f"{username}/{canonical_number}",
                "date": date,
                "datetime": iso,
                "text": text or " ",
                "link": f"https://t.me/{username}/{canonical_number}",
                "tags": _extract_tags(text),
                "media": new_media,
                "telegramMessageIds": sorted(set(message_ids)),
            }
            if group_id:
                post["telegramMediaGroupId"] = str(group_id)
            posts.append(post)
        posts.sort(key=_post_number, reverse=True)
        _save_posts(posts[:MAX_POSTS])
    except Exception:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise


def _find_existing(posts: list[dict], messages: list[dict], group_id: str | None) -> dict | None:
    message_ids = {int(m.get("message_id") or 0) for m in messages}
    if group_id:
        for post in posts:
            if post.get("telegramMediaGroupId") == group_id:
                return post
    for post in posts:
        known = {int(x) for x in post.get("telegramMessageIds", []) if str(x).isdigit()}
        if known & message_ids:
            return post
    for post in posts:
        if _post_number(post) in message_ids:
            return post
    return None


def _group_messages(raw_updates: list[dict]) -> list[tuple[str | None, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for raw in raw_updates:
        message = channel_message(raw)
        if not message:
            continue
        media_group_id = message.get("media_group_id")
        key = f"g:{media_group_id}" if media_group_id else f"m:{message.get('message_id')}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(message)
    result = []
    for key in order:
        messages = sorted(groups[key], key=lambda m: int(m.get("message_id") or 0))
        group_id = str(messages[0].get("media_group_id")) if messages and messages[0].get("media_group_id") else None
        result.append((group_id, messages))
    return result


def select_admin_updates(raw_updates: list[dict], admin_id: int) -> list[dict]:
    selected = []
    for raw in raw_updates:
        if channel_message(raw) is not None:
            continue
        message = raw.get("message") or raw.get("edited_message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            continue
        try:
            sender_id = int(sender.get("id"))
        except (TypeError, ValueError):
            continue
        if sender_id == admin_id:
            selected.append(raw)
    return selected


def ingest_channel_updates(
    token: str,
    raw_updates: list[dict],
    channel_username_fallback: str,
    on_failure=None,
) -> int:
    if not raw_updates:
        return 0
    posts = _load_posts()
    changed = 0

    raw_by_message_id = {
        int((channel_message(raw) or {}).get("message_id") or 0): raw
        for raw in raw_updates
        if channel_message(raw)
    }
    edited_message_ids = {
        int((raw.get("edited_channel_post") or {}).get("message_id") or 0)
        for raw in raw_updates
        if raw.get("edited_channel_post")
    }

    for group_id, messages in _group_messages(raw_updates):
        if not messages:
            continue
        try:
            existing = _find_existing(posts, messages, group_id)
            canonical_number = _post_number(existing) if existing else int(messages[0].get("message_id") or 0)
            if canonical_number <= 0:
                continue
            username = _channel_username(messages[0], channel_username_fallback)
            message_ids = [int(m.get("message_id") or 0) for m in messages]
            text = next(
                (
                    str(m.get("text") or m.get("caption") or "").strip()
                    for m in messages
                    if str(m.get("text") or m.get("caption") or "").strip()
                ),
                "",
            )
            edited_ids = set(message_ids) & edited_message_ids
            if not text and existing and not edited_ids:
                text = str(existing.get("text") or "").strip()
            date, iso = _format_datetime(messages[0].get("date"))

            old_media = list(existing.get("media") or []) if existing else []
            has_direct_media_metadata = any(
                "telegramMessageId" in item for item in old_media
            )
            existing_media_by_id = {
                int(item["telegramMessageId"]): item
                for item in old_media
                if str(item.get("telegramMessageId") or "").isdigit()
            }
            new_media: list[dict] = []
            index = len(old_media) if has_direct_media_metadata else 0
            for message in messages:
                message_id = int(message.get("message_id") or 0)
                old_entry = existing_media_by_id.get(message_id)
                media_index = index
                if old_entry:
                    match = re.match(
                        rf"^danilka2028k-{canonical_number}-(\d+)\.",
                        Path(str(old_entry.get("src") or "")).name,
                    )
                    if match:
                        media_index = int(match.group(1))
                if old_entry is None:
                    if existing is None:
                        media = _media_from_message(
                            token,
                            message,
                            canonical_number,
                            media_index,
                            force_download=True,
                        )
                    else:
                        media = _media_from_message(
                            token,
                            message,
                            canonical_number,
                            media_index,
                            force_download=True,
                        )
                else:
                    media = _media_from_message(
                        token,
                        message,
                        canonical_number,
                        media_index,
                        existing_entry=old_entry,
                    )
                new_media.extend(media)
                if media:
                    index += 1

            if existing:
                if has_direct_media_metadata:
                    updated_ids = set(message_ids)
                    retained = [
                        item
                        for item in old_media
                        if int(item.get("telegramMessageId") or -1) not in updated_ids
                    ]
                    merged_media = retained + new_media
                elif new_media:
                    # Replace scraper-origin media on the first direct Bot API refresh.
                    merged_media = new_media
                else:
                    merged_media = old_media
                existing.update(
                    {
                        "date": existing.get("date") or date,
                        "datetime": existing.get("datetime") or iso,
                        "text": text or " ",
                        "link": existing.get("link") or f"https://t.me/{username}/{canonical_number}",
                        "tags": _extract_tags(text),
                        "media": merged_media,
                        "telegramMessageIds": sorted(set(existing.get("telegramMessageIds", [])) | set(message_ids)),
                    }
                )
                if group_id:
                    existing["telegramMediaGroupId"] = group_id
            else:
                post = {
                    "id": f"{username}/{canonical_number}",
                    "date": date,
                    "datetime": iso,
                    "text": text or " ",
                    "link": f"https://t.me/{username}/{canonical_number}",
                    "tags": _extract_tags(text),
                    "media": new_media,
                    "telegramMessageIds": sorted(set(message_ids)),
                }
                if group_id:
                    post["telegramMediaGroupId"] = group_id
                posts.append(post)
            changed += 1
        except Exception as error:
            failed_raw = [
                raw_by_message_id[message_id]
                for message_id in (int(m.get("message_id") or 0) for m in messages)
                if message_id in raw_by_message_id
            ]
            if on_failure is None:
                raise
            on_failure(failed_raw, error)

    posts.sort(key=_post_number, reverse=True)
    posts = posts[:MAX_POSTS]
    if changed:
        _save_posts(posts)
    return changed


def process_admin_updates(
    raw_updates: list[dict],
    retry_state=None,
    now: float | None = None,
) -> int:
    from bot_utils import require_admin_id

    admin_id = require_admin_id(os.getenv("ADMIN_ID"))
    admin_raw = select_admin_updates(raw_updates, admin_id)

    # Import lazily: CI unit tests can exercise the ingestion logic without
    # requiring Telegram/Gemini secrets, while the production job still reuses
    # the exact same handlers as bot.py.
    import telebot  # type: ignore
    import bot  # type: ignore
    from bot import group_photo_messages, process_album  # type: ignore
    import telegram_album_state
    import telegram_retry_state

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    retry_state = retry_state or telegram_retry_state.TelegramRetryState(token)
    now = time.time() if now is None else now
    pending_admin = retry_state.due("admin", now)
    admin_by_id = {
        int(update["update_id"]): update
        for update in pending_admin + select_admin_updates(raw_updates, admin_id)
        if isinstance(update.get("update_id"), int)
    }
    admin_raw = [admin_by_id[key] for key in sorted(admin_by_id)]
    bot.delivery_checkpoint_errors.clear()
    bot.processing_errors.clear()
    bot.published_post_handler = record_bot_publication
    album_updates = []
    single_updates = []
    for raw in admin_raw:
        message = raw.get("message") or {}
        if message.get("media_group_id") and message.get("photo"):
            album_updates.append(raw)
        else:
            single_updates.append(raw)

    ready_albums, _ = telegram_album_state.record_and_get_ready(
        album_updates,
        token,
        now,
    )

    completed_ids = [
        int(raw["update_id"])
        for raw in album_updates
        if isinstance(raw.get("update_id"), int)
    ]
    processed_count = 0
    for raw in single_updates:
        update_id = raw.get("update_id")
        bot.delivery_checkpoint_errors.clear()
        bot.processing_errors.clear()
        try:
            message = raw.get("message") or {}
            text = str(message.get("text") or message.get("caption") or "").strip()
            command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
            is_publish_candidate = command == "/post" or (
                not command
                and any(
                    key in message
                    for key in ("text", "photo", "video", "video_note", "voice", "audio")
                )
            )
            if is_publish_candidate:
                chat_id = int((message.get("chat") or {}).get("id"))
                message_id = int(message.get("message_id") or 0)
                status = bot.telegram_delivery.delivery_status(token, chat_id, message_id)
                if status in {"attempted", "published"}:
                    bot.notify_delivery_status(chat_id, message_id, status)
                    if isinstance(update_id, int):
                        completed_ids.append(update_id)
                    processed_count += 1
                    continue
            update = telebot.types.Update.de_json(json.dumps(raw))
            bot.bot.process_new_updates([update])
        except Exception as error:
            bot.processing_errors.append(error)
        errors = [*bot.delivery_checkpoint_errors, *bot.processing_errors]
        bot.delivery_checkpoint_errors.clear()
        bot.processing_errors.clear()
        if errors:
            retry_after = _telegram_retry_after(errors[0])
            retry_state.defer("admin", raw, now, retry_after)
            print(
                "Owner update deferred for encrypted retry "
                f"(reason={type(errors[0]).__name__}, delay={retry_after or 'backoff'}s)."
            )
        elif isinstance(update_id, int):
            completed_ids.append(update_id)
        processed_count += 1

    for raw_album in ready_albums:
        bot.delivery_checkpoint_errors.clear()
        bot.processing_errors.clear()
        try:
            updates = [telebot.types.Update.de_json(json.dumps(raw)) for raw in raw_album]
            messages = [group_photo_messages(update) for update in updates]
            messages = [message for message in messages if message is not None]
            messages.sort(key=lambda item: item.message_id)
            first_message = messages[0] if messages else None
            delivery_status = (
                bot.telegram_delivery.delivery_status(
                    token,
                    int(first_message.chat.id),
                    int(first_message.message_id),
                )
                if first_message
                else None
            )
            if delivery_status in {"attempted", "published"} and first_message:
                bot.notify_delivery_status(
                    int(first_message.chat.id),
                    int(first_message.message_id),
                    delivery_status,
                )
                completed = True
            else:
                completed = process_album(messages)
        except Exception as error:
            completed = False
            bot.processing_errors.append(error)
        errors = [*bot.delivery_checkpoint_errors, *bot.processing_errors]
        bot.delivery_checkpoint_errors.clear()
        bot.processing_errors.clear()
        if completed and not errors:
            telegram_album_state.remove_completed([raw_album], token)
        else:
            reason = type(errors[0]).__name__ if errors else "AlbumProcessingError"
            telegram_album_state.defer_failed(
                [raw_album],
                token,
                now,
                _telegram_retry_after(errors[0]) if errors else 0,
            )
            print(f"Owner photo album retained for retry (reason={reason}).")
        processed_count += len(raw_album)

    retry_state.complete(completed_ids)
    retry_state.save()
    return processed_count


def _telegram_retry_after(error: Exception) -> float:
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    result_json = getattr(error, "result_json", None)
    if isinstance(result_json, dict):
        parameters = result_json.get("parameters")
        if isinstance(parameters, dict):
            retry_after = parameters.get("retry_after")
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                return float(retry_after)
    return 0


def _write_cycle_outputs(max_update_id: int, channel_count: int) -> None:
    output_file = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_file:
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(f"pending_offset={max_update_id + 1}\n")
            output.write(f"channel_posts={channel_count}\n")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    configured_channel = os.getenv("CHANNEL_ID", "").strip()
    channel_username = os.getenv("TG_CHANNEL_USERNAME", "danilka2028k").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not configured_channel:
        raise RuntimeError("CHANNEL_ID is required")

    import telegram_diagnostics

    def read_telegram_configuration(
        api_token: str,
        method: str,
        payload: dict | None = None,
    ) -> dict:
        result = bot_api(api_token, method, payload, timeout=10)
        if not isinstance(result, dict):
            raise TelegramApiError(f"Telegram API {method} returned an unexpected response")
        return result

    try:
        telegram_diagnostics.diagnose(
            token,
            configured_channel,
            os.getenv("ADMIN_ID", ""),
            api=read_telegram_configuration,
        )
    except telegram_diagnostics.TelegramDiagnosticsError as error:
        raise RuntimeError(f"Telegram configuration check failed: {error}") from None

    import telegram_retry_state

    retry_state = telegram_retry_state.TelegramRetryState(token)
    now = time.time()
    updates = fetch_updates(token)
    max_update_id = max((int(item.get("update_id") or 0) for item in updates), default=None)

    channel_work_by_id = {
        int(update["update_id"]): update
        for update in retry_state.due("channel", now)
        if isinstance(update.get("update_id"), int)
    }
    channel_work_by_id.update(
        {
            int(update["update_id"]): update
            for update in select_channel_updates(updates, configured_channel)
            if isinstance(update.get("update_id"), int)
        }
    )
    channel_work = [channel_work_by_id[key] for key in sorted(channel_work_by_id)]
    channel_failures: list[tuple[list[dict], Exception]] = []
    channel_count = ingest_channel_updates(
        token,
        channel_work,
        channel_username,
        on_failure=lambda failed, error: channel_failures.append((failed, error)),
    )
    failed_channel_ids = set()
    for failed_updates, error in channel_failures:
        retry_after = _telegram_retry_after(error)
        for raw in failed_updates:
            update_id = raw.get("update_id")
            if isinstance(update_id, int):
                failed_channel_ids.add(update_id)
                retry_state.defer("channel", raw, now, retry_after)
        print(
            "Channel update retained for encrypted retry "
            f"(reason={type(error).__name__}, delay={retry_after or 'backoff'}s)."
        )
    retry_state.complete(
        [
            int(update["update_id"])
            for update in channel_work
            if isinstance(update.get("update_id"), int)
            and int(update["update_id"]) not in failed_channel_ids
        ]
    )
    admin_count = process_admin_updates(updates, retry_state, now)
    retry_state.save()

    if max_update_id is not None:
        _write_cycle_outputs(max_update_id, channel_count)
    print(
        f"Bot API cycle complete: updates={len(updates)}, "
        f"channel_posts={channel_count}, admin_updates={admin_count}, "
        f"pending_ack={max_update_id + 1 if max_update_id is not None else 'none'}."
    )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--ack-offset":
        if not sys.argv[2].isdigit() or int(sys.argv[2]) < 1:
            raise RuntimeError("offset должен быть положительным целым числом")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        acknowledge_updates(token, int(sys.argv[2]) - 1)
        print("Bot API updates acknowledged after repository persistence.")
    elif len(sys.argv) == 1:
        main()
    else:
        raise RuntimeError("Использование: bot_api_cycle.py [--ack-offset OFFSET]")
