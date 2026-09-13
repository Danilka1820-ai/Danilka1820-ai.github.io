"""One-shot Telegram Bot API cycle for the production GitHub Actions job.

This replaces the old self-restarting long-polling chain. A scheduled job reads
one pending batch from Telegram, mirrors channel posts into the static site,
processes administrator messages through the existing bot handlers, then
acknowledges the batch. The public t.me scraper is only a reconciliation fallback.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
        raise TelegramApiError(f"Telegram API {method} failed: {error}") from error
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


def _make_video_variant(path: Path, width: int, crf: int, suffix: str) -> Path | None:
    dims = _video_dimensions(path)
    if dims and dims[0] <= int(width * 1.1):
        return None
    target = path.with_name(f"{path.stem}-{suffix}.mp4")
    if target.exists() and target.stat().st_size > 1024:
        return target
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
            str(target),
        ]
    )
    if not ok or not target.exists() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        return None
    if target.stat().st_size >= path.stat().st_size * 0.9:
        target.unlink(missing_ok=True)
        return None
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


def _download_media_file(token: str, file_obj: dict, post_number: int, index: str | int, default_ext: str) -> Path:
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
    if target.exists() and target.stat().st_size > 1024:
        return target
    if declared and int(declared) > MAX_BOT_DOWNLOAD_BYTES:
        raise TelegramApiError(
            f"file is {int(declared) / 1048576:.1f} MB; official Bot API limit is 20 MB"
        )
    try:
        response = requests.get(f"{BOT_API}/file/bot{token}/{file_path}", timeout=90)
        response.raise_for_status()
    except requests.RequestException as error:
        raise TelegramApiError(f"Telegram file download failed: {error}") from error
    if len(response.content) > MAX_BOT_DOWNLOAD_BYTES:
        raise TelegramApiError("download exceeded official 20 MB Bot API limit")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return target


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _download_thumbnail(token: str, thumb: dict | None, post_number: int, index: str) -> str:
    if not thumb or not thumb.get("file_id"):
        return ""
    try:
        target = _download_media_file(token, thumb, post_number, index, "jpg")
        _optimize_image(target)
        return _relative(target)
    except TelegramApiError as error:
        print(f"Thumbnail skipped: {error}")
        return ""


def _media_from_message(token: str, message: dict, post_number: int, start_index: int) -> list[dict]:
    entries: list[dict] = []
    message_id = int(message.get("message_id") or 0)

    photos = message.get("photo") or []
    if photos:
        photo = photos[-1]
        try:
            target = _download_media_file(token, photo, post_number, start_index, "jpg")
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
            print(f"Photo {message_id} skipped: {error}")
        return entries

    video = message.get("video")
    video_note = message.get("video_note")
    media_obj = video_note or video
    if media_obj:
        kind = "round" if video_note else "video"
        poster = _download_thumbnail(
            token,
            media_obj.get("thumbnail"),
            post_number,
            f"{start_index}p",
        )
        try:
            target = _download_media_file(token, media_obj, post_number, start_index, "mp4")
            _optimize_video(target, kind)
            mid = _make_video_variant(target, 720 if kind == "round" else 1280, 28, "mid")
            low = _make_video_variant(target, 360 if kind == "round" else 640, 34 if kind == "round" else 32, "low")
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
            print(f"Video {message_id} skipped in primary path: {error}")
            if poster:
                entries.append(
                    {
                        "type": "photo",
                        "src": poster,
                        "unplayable": True,
                        "telegramMessageId": message_id,
                        "telegramFileUniqueId": media_obj.get("file_unique_id"),
                    }
                )
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
    POSTS_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def ingest_channel_updates(token: str, raw_updates: list[dict], channel_username_fallback: str) -> int:
    if not raw_updates:
        return 0
    posts = _load_posts()
    changed = 0

    for group_id, messages in _group_messages(raw_updates):
        if not messages:
            continue
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
        if not text and existing:
            text = str(existing.get("text") or "").strip()
        date, iso = _format_datetime(messages[0].get("date"))

        new_media: list[dict] = []
        index = 0
        for message in messages:
            media = _media_from_message(token, message, canonical_number, index)
            new_media.extend(media)
            if media:
                index += 1

        if existing:
            old_media = list(existing.get("media") or [])
            if new_media:
                direct_metadata_present = any("telegramMessageId" in item for item in old_media)
                if direct_metadata_present:
                    updated_ids = set(message_ids)
                    old_media = [
                        item
                        for item in old_media
                        if int(item.get("telegramMessageId") or -1) not in updated_ids
                    ]
                    merged_media = old_media + new_media
                else:
                    # First direct Bot API refresh of a legacy post: replace its
                    # scraper-origin media instead of duplicating it.
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

    posts.sort(key=_post_number, reverse=True)
    posts = posts[:MAX_POSTS]
    _save_posts(posts)
    return changed


def process_admin_updates(raw_updates: list[dict]) -> int:
    admin_raw = [raw for raw in raw_updates if channel_message(raw) is None]
    if not admin_raw:
        return 0

    # Import lazily: CI unit tests can exercise the ingestion logic without
    # requiring Telegram/Gemini secrets, while the production job still reuses
    # the exact same handlers as bot.py.
    import telebot  # type: ignore
    from bot import bot, group_photo_messages, process_album  # type: ignore

    updates = [telebot.types.Update.de_json(json.dumps(raw)) for raw in admin_raw]
    albums: dict[str, list] = {}
    singles = []
    for update in updates:
        grouped = group_photo_messages(update)
        if grouped is None:
            singles.append(update)
        else:
            albums.setdefault(grouped.media_group_id, []).append(grouped)
    if singles:
        bot.process_new_updates(singles)
    for messages in albums.values():
        messages.sort(key=lambda item: item.message_id)
        process_album(messages)
    return len(updates)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    configured_channel = os.getenv("CHANNEL_ID", "").strip()
    channel_username = os.getenv("TG_CHANNEL_USERNAME", "danilka2028k").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not configured_channel:
        raise RuntimeError("CHANNEL_ID is required")

    updates = fetch_updates(token)
    if not updates:
        print("Bot API: pending updates not found.")
        return

    max_update_id = max(int(item.get("update_id") or 0) for item in updates)
    channel_updates = select_channel_updates(updates, configured_channel)
    channel_count = ingest_channel_updates(token, channel_updates, channel_username)
    admin_count = process_admin_updates(updates)

    # Acknowledge only after all local processing succeeded. Site persistence to
    # git happens in the next workflow step; the independent HTML reconciler can
    # recover a channel post if a later git push fails.
    acknowledge_updates(token, max_update_id)
    print(
        f"Bot API cycle complete: updates={len(updates)}, "
        f"channel_posts={channel_count}, admin_updates={admin_count}, ack={max_update_id}."
    )


if __name__ == "__main__":
    main()
