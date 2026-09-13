from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: ожидалось 1 совпадение, найдено {count}")
    return text.replace(old, new, 1)


index_path = ROOT / "index.html"
index = index_path.read_text(encoding="utf-8")
index = replace_once(
    index,
    "видео, вводных метках разделов. Тон стал темнее: 3,8:1 к бумаге —",
    "видео, вводных метках разделов. Тон стал темнее: 4,6:1 к бумаге —",
    "комментарий контраста",
)
index = replace_once(
    index,
    "  --ink-3:      #6C7682;",
    "  --ink-3:      #5F6975;",
    "цвет --ink-3",
)

index, css_removed = re.subn(
    r"\n/\* ─── Без скачивания ───[\s\S]*?\*/\nimg, video\{\n"
    r"  -webkit-user-select:none; user-select:none;\n"
    r"  -webkit-user-drag:none; user-drag:none;\n"
    r"  -webkit-touch-callout:none;\n\}\n",
    "\n",
    index,
    count=1,
)
if css_removed != 1:
    raise RuntimeError(f"CSS-псевдозащита: ожидалось 1 совпадение, найдено {css_removed}")

js_block = """  // Ни сохранить через контекстное меню, ни перетащить на рабочий стол —
  // у любой картинки и любого видео на странице, не только у этих двух.
  document.addEventListener('contextmenu', function(e){
    if (e.target.closest('img, video')) e.preventDefault();
  });
  document.addEventListener('dragstart', function(e){
    if (e.target.closest('img, video')) e.preventDefault();
  });

"""
index = replace_once(index, js_block, "", "JS-псевдозащита")
index_path.write_text(index, encoding="utf-8")

sw_path = ROOT / "sw.js"
sw = sw_path.read_text(encoding="utf-8")
sw = replace_once(sw, "const CACHE = 'sarykov-v7';", "const CACHE = 'sarykov-v8';", "версия кэша")
sw_path.write_text(sw, encoding="utf-8")

check_path = ROOT / "scripts" / "check-site.mjs"
check = check_path.read_text(encoding="utf-8")
check = replace_once(
    check,
    "for (const имя of ['fetch-telegram-posts.mjs', 'vendor-fonts.mjs', 'check-site.mjs']) {",
    "for (const имя of ['fetch-telegram-posts.mjs', 'vendor-fonts.mjs', 'generate-post-pages.mjs', 'check-site.mjs']) {",
    "список MJS в локальной проверке",
)
check_path.write_text(check, encoding="utf-8")

readme_path = ROOT / "README.md"
readme = readme_path.read_text(encoding="utf-8")
old_intro = """Статический сайт на GitHub Pages. Записи приезжают из Telegram-канала
[@danilka2028k](https://t.me/danilka2028k) сами. Расписание просит каждые
15 минут, но GitHub на нечастых репозиториях запускает его примерно раз в
час — точности он не обещает. Нужно сейчас: вкладка **Actions** →
**Telegram → сайт** → **Run workflow**.
"""
new_intro = """Статический сайт на GitHub Pages. Записи приезжают из Telegram-канала
[@danilka2028k](https://t.me/danilka2028k) автоматически. Пока цепочка бота
активна, новый или отредактированный пост канала будит workflow
**Telegram → сайт** сразу через `workflow_dispatch`; резервный cron самого
синхронизатора остаётся раз в 15 минут. После изменения данных workflow явно
запрашивает новый GitHub Pages build, поэтому commit и публикация сайта больше
не расходятся.
"""
readme = replace_once(readme, old_intro, new_intro, "вступление README")

old_flow = """Telegram-канал
      │  каждые 15 минут, .github/workflows/telegram-sync.yml
      ▼
scripts/fetch-telegram-posts.mjs
      │  читает публичную превью-страницу t.me/s/danilka2028k
      │  тексты  → data/posts.json
      │  медиа   → assets/posts/  (сжимает и переводит в H.264)
      ▼
index.html — читает data/posts.json в браузере и рисует ленту
"""
new_flow = """Telegram-канал
      │  channel_post / edited_channel_post
      ▼
@DanilkaVlogBot → telegram-gemini-bot/poll_once.py
      │  workflow_dispatch (сразу) + резервный cron
      ▼
.github/workflows/telegram-sync.yml
      │
      ▼
scripts/fetch-telegram-posts.mjs
      │  читает публичную превью-страницу t.me/s/danilka2028k
      │  тексты  → data/posts.json
      │  медиа   → assets/posts/  (сжимает и переводит в H.264)
      │  страницы записей → dnevnik/<id>/ + sitemap.xml
      ▼
GitHub Pages build → sarykov.ru
"""
readme = replace_once(readme, old_flow, new_flow, "схема Telegram → сайт")
readme = replace_once(
    readme,
    "Сервера нет, базы нет, бота нет. Всё, что видит читатель, лежит в этом\nрепозитории — поэтому сайт открывается и там, где Telegram заблокирован.",
    "У публичной части сайта нет backend-сервера и базы данных: всё, что видит\nчитатель, лежит в этом репозитории — поэтому сайт открывается и там, где\nTelegram заблокирован. Административный бот живёт отдельно в\n`telegram-gemini-bot/`: он помогает управлять каналом и ускоряет запуск\nсинхронизации, но посетителю сайта для чтения не нужен.",
    "описание архитектуры README",
)
readme_path.write_text(readme, encoding="utf-8")

(ROOT / ".nojekyll").touch(exist_ok=True)

# Одноразовый механизм не должен оставаться в production после успешной правки.
for path in [
    ROOT / "scripts" / "final-cleanup-once.py",
    ROOT / ".github" / "workflows" / "final-cleanup.yml",
]:
    path.unlink(missing_ok=True)

print("Финальная уборка применена.")
