import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const root = process.cwd();
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const write = (p, content) => {
  const full = path.join(root, p);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, content, 'utf8');
};
const replaceRequired = (content, from, to, label) => {
  if (!content.includes(from)) throw new Error(`Не найден ожидаемый фрагмент: ${label}`);
  return content.replace(from, to);
};

// 1) Доступность + UX без изменения архитектуры/плеера.
let index = read('index.html');
index = replaceRequired(
  index,
  '  --ink-3:      #6C7682;',
  '  --ink-3:      #5F6975;',
  'контраст --ink-3',
);
index = index.replace('Тон стал темнее: 3,8:1 к бумаге —', 'Тон стал темнее: 4,6:1 к бумаге —');
const pseudoProtection = `  // Ни сохранить через контекстное меню, ни перетащить на рабочий стол —\n  // у любой картинки и любого видео на странице, не только у этих двух.\n  document.addEventListener('contextmenu', function(e){\n    if (e.target.closest('img, video')) e.preventDefault();\n  });\n  document.addEventListener('dragstart', function(e){\n    if (e.target.closest('img, video')) e.preventDefault();\n  });\n\n`;
index = replaceRequired(index, pseudoProtection, '', 'псевдозащита contextmenu/dragstart');
write('index.html', index);

// 2) README: публичный сайт статический, но административный бот в репозитории есть.
let readme = read('README.md');
readme = replaceRequired(
  readme,
  'Сервера нет, базы нет, бота нет. Всё, что видит читатель, лежит в этом\nрепозитории — поэтому сайт открывается и там, где Telegram заблокирован.',
  'У публичного сайта нет backend-сервера и базы данных: всё, что видит читатель,\nлежит в этом репозитории — поэтому сайт открывается и там, где Telegram\nзаблокирован. Отдельно в `telegram-gemini-bot/` живёт административный бот\nвладельца; он не участвует в показе сайта посетителям.',
  'устаревшее описание README',
);
write('README.md', readme);

// 3) Локальный мусор и секреты.
let gitignore = read('.gitignore');
for (const line of [
  'telegram-gemini-bot/.venv/',
  '*.py[cod]',
  '.pytest_cache/',
  '.ruff_cache/',
  '.mypy_cache/',
  '.DS_Store',
]) {
  if (!gitignore.split(/\r?\n/).includes(line)) gitignore += `${line}\n`;
}
write('.gitignore', gitignore);

// 4) Статические индексируемые страницы записей без framework и внешних CDN.
const generator = `import fs from 'node:fs';\nimport path from 'node:path';\n\nconst ROOT = process.cwd();\nconst postsPath = path.join(ROOT, 'data', 'posts.json');\nconst outRoot = path.join(ROOT, 'dnevnik');\nconst SITE = 'https://www.sarykov.ru';\n\nfunction esc(value = '') {\n  return String(value)\n    .replaceAll('&', '&amp;')\n    .replaceAll('<', '&lt;')\n    .replaceAll('>', '&gt;')\n    .replaceAll('"', '&quot;')\n    .replaceAll("'", '&#39;');\n}\nfunction xml(value = '') { return esc(value); }\nfunction slugOf(post) {\n  const raw = String(post?.id || '').split('/').pop() || '';\n  const safe = raw.trim().replace(/[^0-9A-Za-z_-]/g, '');\n  return safe || null;\n}\nfunction titleOf(post) {\n  const first = String(post?.text || '').split(/\\r?\\n/).map((s) => s.trim()).find(Boolean);\n  return (first || ('Запись от ' + (post?.date || 'Данила Сарыкова'))).slice(0, 90);\n}\nfunction descriptionOf(post) {\n  return String(post?.text || '').replace(/\\s+/g, ' ').trim().slice(0, 180) || 'Запись из личного дневника Данила Сарыкова.';\n}\nfunction localUrl(src) {\n  if (!src) return '';\n  const clean = String(src).replace(/^\\/+/, '');\n  return '/' + clean;\n}\nfunction mediaHtml(post) {\n  const media = Array.isArray(post?.media) ? post.media : [];\n  return media.slice(0, 6).map((m) => {\n    const src = localUrl(m?.src);\n    if (!src) return '';\n    if (m.type === 'photo') return '<img loading="lazy" decoding="async" src="' + esc(src) + '" alt="">';\n    const poster = localUrl(m?.poster);\n    return '<video controls preload="metadata"' + (poster ? ' poster="' + esc(poster) + '"' : '') + ' src="' + esc(src) + '"></video>';\n  }).join('\\n');\n}\nfunction firstPreview(post) {\n  const media = Array.isArray(post?.media) ? post.media : [];\n  const first = media.find((m) => m?.type === 'photo' && m?.src) || media.find((m) => m?.poster);\n  const src = first?.type === 'photo' ? first.src : first?.poster;\n  return src ? SITE + localUrl(src) : SITE + '/og-image.png';\n}\nfunction render(post, slug) {\n  const title = titleOf(post);\n  const description = descriptionOf(post);\n  const canonical = SITE + '/dnevnik/' + encodeURIComponent(slug) + '/';\n  const tg = /^https:\\/\\/t\\.me\\/danilka2028k\\/\\d+$/.test(String(post?.link || '')) ? String(post.link) : '';\n  const paragraphs = String(post?.text || '').split(/\\r?\\n/).filter(Boolean).map((p) => '<p>' + esc(p) + '</p>').join('\\n');\n  const preview = firstPreview(post);\n  const jsonLd = JSON.stringify({\n    '@context': 'https://schema.org',\n    '@type': 'BlogPosting',\n    headline: title,\n    description,\n    datePublished: post?.datetime || undefined,\n    author: { '@type': 'Person', name: 'Данил Сарыков', url: SITE + '/' },\n    mainEntityOfPage: canonical,\n    image: preview,\n  }).replace(/</g, '\\u003c');\n  return '<!DOCTYPE html>\\n<html lang="ru">\\n<head>\\n<meta charset="utf-8">\\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\\n<meta name="robots" content="noimageindex">\\n<meta name="referrer" content="strict-origin-when-cross-origin">\\n<title>' + esc(title) + ' — Данил Сарыков</title>\\n<meta name="description" content="' + esc(description) + '">\\n<link rel="canonical" href="' + esc(canonical) + '">\\n<meta property="og:type" content="article">\\n<meta property="og:locale" content="ru_RU">\\n<meta property="og:site_name" content="Вокруг меня">\\n<meta property="og:title" content="' + esc(title) + '">\\n<meta property="og:description" content="' + esc(description) + '">\\n<meta property="og:url" content="' + esc(canonical) + '">\\n<meta property="og:image" content="' + esc(preview) + '">\\n<meta name="twitter:card" content="summary_large_image">\\n<meta name="theme-color" content="#E8EAED">\\n<script type="application/ld+json">' + jsonLd + '</script>\\n<style>html{background:#e8eaed;color:#11151b;font-family:Georgia,serif}body{margin:0}main{width:min(760px,calc(100% - 32px));margin:0 auto;padding:48px 0 72px}nav{font:14px/1.4 ui-monospace,monospace;margin-bottom:40px}a{color:#955b32}h1{font-size:clamp(32px,7vw,64px);line-height:1.02;margin:0 0 16px;font-weight:400}time{font:13px/1.4 ui-monospace,monospace;color:#5f6975}article{font-size:clamp(18px,2.5vw,22px);line-height:1.6}p{margin:1em 0}.media{display:grid;gap:12px;margin:28px 0}.media img,.media video{display:block;width:100%;height:auto;border-radius:18px;background:#11151b}footer{margin-top:48px;padding-top:20px;border-top:1px solid #cbcfd6;font:14px/1.5 ui-monospace,monospace}@media(max-width:520px){main{padding-top:28px}}</style>\\n</head>\\n<body><main>\\n<nav><a href="/#/dnevnik/' + encodeURIComponent(slug) + '">← Открыть запись в дневнике</a></nav>\\n<h1>' + esc(title) + '</h1>\\n' + (post?.datetime ? '<time datetime="' + esc(post.datetime) + '">' + esc(post.date || post.datetime) + '</time>' : '') + '\\n<article>\\n<div class="media">' + mediaHtml(post) + '</div>\\n' + paragraphs + '\\n</article>\\n<footer>' + (tg ? '<a href="' + esc(tg) + '" rel="noopener noreferrer">Оригинал в Telegram</a> · ' : '') + '<a href="/">sarykov.ru</a></footer>\\n</main></body></html>\\n';\n}\n\nconst posts = JSON.parse(fs.readFileSync(postsPath, 'utf8'));\nif (!Array.isArray(posts)) throw new Error('data/posts.json должен содержать массив');\nfs.rmSync(outRoot, { recursive: true, force: true });\nfs.mkdirSync(outRoot, { recursive: true });\nconst sitemap = [{ loc: SITE + '/', lastmod: null }];\nlet count = 0;\nfor (const post of posts) {\n  const slug = slugOf(post);\n  if (!slug) continue;\n  const dir = path.join(outRoot, slug);\n  fs.mkdirSync(dir, { recursive: true });\n  fs.writeFileSync(path.join(dir, 'index.html'), render(post, slug), 'utf8');\n  sitemap.push({ loc: SITE + '/dnevnik/' + encodeURIComponent(slug) + '/', lastmod: String(post?.datetime || '').slice(0, 10) || null });\n  count++;\n}\nconst body = sitemap.map(({ loc, lastmod }) => '  <url>\\n    <loc>' + xml(loc) + '</loc>' + (lastmod ? '\\n    <lastmod>' + xml(lastmod) + '</lastmod>' : '') + '\\n  </url>').join('\\n');\nfs.writeFileSync(path.join(ROOT, 'sitemap.xml'), '<?xml version="1.0" encoding="UTF-8"?>\\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\\n' + body + '\\n</urlset>\\n', 'utf8');\nconsole.log('SEO-страницы записей: ' + count);\n`;
write('scripts/generate-post-pages.mjs', generator);
write('.nojekyll', '');

// 5) CI: минимальные права, immutable SHA для Actions, dependency audit.
write('.github/workflows/proverka.yml', `name: Проверка сайта\n\non:\n  push:\n    branches: ['**']\n    paths-ignore:\n      - '**.md'\n  pull_request:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\njobs:\n  proverka:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7\n        with:\n          node-version: '20'\n      - name: Проверить сайт\n        run: node scripts/check-site.mjs\n      - name: Проверить синтаксис служебных скриптов\n        run: |\n          node --check sw.js\n          node --check scripts/check-site.mjs\n          node --check scripts/fetch-telegram-posts.mjs\n          node --check scripts/vendor-fonts.mjs\n          node --check scripts/generate-post-pages.mjs\n      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7\n        with:\n          python-version: '3.11'\n      - name: Проверить код и тесты Telegram-бота\n        run: |\n          python - <<'PY'\n          import ast\n          from pathlib import Path\n          for path in Path('telegram-gemini-bot').glob('*.py'):\n              ast.parse(path.read_text(encoding='utf-8'), filename=str(path))\n              print(f'Синтаксис корректен: {path}')\n          PY\n          python -m unittest discover -s telegram-gemini-bot -p 'test_*.py'\n      - name: Проверить Python-зависимости по базе уязвимостей\n        run: |\n          python -m pip install --disable-pip-version-check pip-audit==2.10.1\n          python -m pip_audit -r telegram-gemini-bot/requirements.txt\n`);

// 6) Telegram → сайт: SEO pages + явный Pages build после push от GITHUB_TOKEN.
write('.github/workflows/telegram-sync.yml', `name: Telegram → сайт\n\non:\n  schedule:\n    - cron: '*/15 * * * *'\n  workflow_dispatch:\n  push:\n    paths:\n      - 'scripts/fetch-telegram-posts.mjs'\n      - 'scripts/generate-post-pages.mjs'\n      - '.github/workflows/telegram-sync.yml'\n\nconcurrency:\n  group: telegram-sync\n  cancel-in-progress: false\n\npermissions:\n  contents: write\n  pages: write\n\njobs:\n  sync:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n\n      - name: Установить ffmpeg\n        run: sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg\n\n      - name: Забрать записи из Telegram\n        run: node scripts/fetch-telegram-posts.mjs\n        env:\n          TG_CHANNEL: danilka2028k\n\n      - name: Собрать постоянные страницы записей\n        run: node scripts/generate-post-pages.mjs\n\n      - name: Проверить данные и медиа до публикации\n        run: node scripts/check-site.mjs\n\n      - name: Commit new posts\n        id: commit\n        run: |\n          git config user.name "github-actions[bot]"\n          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"\n          git add data assets/posts dnevnik sitemap.xml\n          if git diff --cached --quiet; then\n            echo "Нет новых записей."\n            echo "changed=false" >> "$GITHUB_OUTPUT"\n            exit 0\n          fi\n          git commit -m "Sync diary posts from Telegram"\n          pushed=false\n          for delay in 0 2 4 8 16; do\n            sleep "$delay"\n            git rebase --abort 2>/dev/null || true\n            if git pull --rebase origin "${GITHUB_REF_NAME}" && git push origin "HEAD:${GITHUB_REF_NAME}"; then\n              pushed=true\n              break\n            fi\n          done\n          if [ "$pushed" != "true" ]; then\n            echo "Не удалось отправить обновление после повторных попыток." >&2\n            exit 1\n          fi\n          echo "changed=true" >> "$GITHUB_OUTPUT"\n\n      - name: Запросить публикацию GitHub Pages\n        if: steps.commit.outputs.changed == 'true'\n        env:\n          GH_TOKEN: ${{ github.token }}\n        run: |\n          if gh api --method POST "repos/${GITHUB_REPOSITORY}/pages/builds" >/dev/null 2>&1; then\n            echo "GitHub Pages build поставлен в очередь."\n          else\n            echo "::warning::Не удалось запросить branch-based Pages build. Если Pages уже публикуется отдельным Actions workflow, это нормально."\n          fi\n`);

// 7) Telegram/Gemini workflow: immutable SHA, остальная логика без изменений.
let botWorkflow = read('.github/workflows/telegram-gemini-bot.yml');
botWorkflow = botWorkflow.replaceAll('actions/checkout@v7', 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7');
botWorkflow = botWorkflow.replaceAll('actions/setup-python@v7', 'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7');
write('.github/workflows/telegram-gemini-bot.yml', botWorkflow);

// 8) Первая генерация SEO-страниц и все проверки до коммита.
execFileSync(process.execPath, ['scripts/generate-post-pages.mjs'], { stdio: 'inherit' });
execFileSync(process.execPath, ['scripts/check-site.mjs'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'scripts/generate-post-pages.mjs'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'scripts/fetch-telegram-posts.mjs'], { stdio: 'inherit' });
execFileSync('python3', ['-m', 'unittest', 'discover', '-s', 'telegram-gemini-bot', '-p', 'test_*.py'], { stdio: 'inherit' });

// Одноразовый ремонт не должен оставаться в production-дереве.
fs.rmSync(path.join(root, 'scripts', 'audit-repair-once.mjs'), { force: true });
fs.rmSync(path.join(root, '.github', 'workflows', 'audit-repair.yml'), { force: true });
console.log('Аудит-ремонт подготовлен и проверен.');
