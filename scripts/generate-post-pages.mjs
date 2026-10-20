import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const POSTS_PATH = path.join(ROOT, 'data', 'posts.json');
const OUT_ROOT = path.join(ROOT, 'dnevnik');
const SITE = 'https://www.sarykov.ru';

function esc(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function slugOf(post) {
  const raw = String(post?.id ?? '').split('/').pop() ?? '';
  const safe = raw.trim().replace(/[^0-9A-Za-z_-]/g, '');
  return safe || null;
}

function titleOf(post) {
  const first = String(post?.text ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  return (first || `Запись ${post?.date || ''}` || 'Запись из дневника').slice(0, 90);
}

function descriptionOf(post) {
  return String(post?.text ?? '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180) || 'Запись из личного дневника Данила Сарыкова.';
}

function localUrl(value) {
  if (!value) return '';
  return `/${String(value).replace(/^\/+/, '')}`;
}

function previewOf(post) {
  const media = Array.isArray(post?.media) ? post.media : [];
  const photo = media.find((item) => item?.type === 'photo' && item?.src);
  const poster = media.find((item) => item?.poster);
  const src = photo?.src || poster?.poster;
  return src ? `${SITE}${localUrl(src)}` : `${SITE}/og-image.png`;
}

function mediaHtml(post) {
  const media = Array.isArray(post?.media) ? post.media : [];
  return media.slice(0, 8).map((item) => {
    const src = localUrl(item?.src);
    if (!src) return '';
    if (item.type === 'photo') {
      return `<img loading="lazy" decoding="async" src="${esc(src)}" alt="">`;
    }
    if (item.type === 'audio') {
      return `<audio controls preload="none" src="${esc(src)}" aria-label="Голосовое сообщение"></audio>`;
    }
    const poster = localUrl(item?.poster);
    return `<video controls preload="metadata"${poster ? ` poster="${esc(poster)}"` : ''} src="${esc(src)}"></video>`;
  }).filter(Boolean).join('\n');
}

function render(post, slug) {
  const title = titleOf(post);
  const description = descriptionOf(post);
  const canonical = `${SITE}/dnevnik/${encodeURIComponent(slug)}/`;
  const preview = previewOf(post);
  const telegram = /^https:\/\/t\.me\/danilka2028k\/\d+$/.test(String(post?.link ?? ''))
    ? String(post.link)
    : '';
  const paragraphs = String(post?.text ?? '')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => `<p>${esc(line)}</p>`)
    .join('\n');
  const jsonLd = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: title,
    description,
    datePublished: post?.datetime || undefined,
    author: { '@type': 'Person', name: 'Данил Сарыков', url: `${SITE}/` },
    mainEntityOfPage: canonical,
    image: preview,
  }).replace(/</g, '\\u003c');

  return `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="robots" content="noimageindex">
<title>${esc(title)} — Данил Сарыков</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${esc(canonical)}">
<meta property="og:type" content="article">
<meta property="og:locale" content="ru_RU">
<meta property="og:site_name" content="Вокруг меня">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${esc(canonical)}">
<meta property="og:image" content="${esc(preview)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0A0E13">
<link rel="stylesheet" href="/assets/css/living-diary.css">
<script type="application/ld+json">${jsonLd}</script>
</head>
<body>
<canvas id="weatherFx" aria-hidden="true"></canvas>
<main class="post-page wrap">
  <nav class="post-page__nav"><a href="/#/dnevnik/${encodeURIComponent(slug)}">← Открыть запись в дневнике</a></nav>
  <header class="post-page__head">
    <p class="label">Дневник · ${esc(post?.date || 'Запись')}</p>
    <h1>${esc(title)}</h1>
    ${post?.datetime ? `<time datetime="${esc(post.datetime)}">${esc(post.date || post.datetime)}</time>` : ''}
  </header>
  <article class="entry__body post-page__body">
    <div class="media">${mediaHtml(post)}</div>
    ${paragraphs}
  </article>
</main>
<footer class="foot">
  <div class="wrap foot__row">
    <a class="label u" href="/">www.sarykov.ru</a>
    ${telegram ? `<a class="label u" href="${esc(telegram)}" rel="noopener noreferrer">Оригинал в Telegram</a>` : ''}
    <div class="site-controls" aria-label="Настройки оформления">
      <button type="button" id="effectsToggle" aria-pressed="true">Эффекты: вкл.</button>
      <button type="button" id="transparencyToggle" aria-pressed="false">Уменьшить прозрачность</button>
    </div>
  </div>
</footer>
<script src="/assets/js/atmosphere.js" defer></script>
</body></html>
`;
}

const posts = JSON.parse(fs.readFileSync(POSTS_PATH, 'utf8'));
if (!Array.isArray(posts)) throw new Error('data/posts.json должен содержать массив');

fs.rmSync(OUT_ROOT, { recursive: true, force: true });
fs.mkdirSync(OUT_ROOT, { recursive: true });

const urls = [{ loc: `${SITE}/`, lastmod: null }];
let count = 0;

for (const post of posts) {
  const slug = slugOf(post);
  if (!slug) continue;
  const dir = path.join(OUT_ROOT, slug);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.html'), render(post, slug), 'utf8');
  urls.push({
    loc: `${SITE}/dnevnik/${encodeURIComponent(slug)}/`,
    lastmod: String(post?.datetime ?? '').slice(0, 10) || null,
  });
  count += 1;
}

const xmlBody = urls.map(({ loc, lastmod }) => {
  const mod = lastmod ? `\n    <lastmod>${esc(lastmod)}</lastmod>` : '';
  return `  <url>\n    <loc>${esc(loc)}</loc>${mod}\n  </url>`;
}).join('\n');

fs.writeFileSync(
  path.join(ROOT, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${xmlBody}\n</urlset>\n`,
  'utf8',
);

console.log(`SEO-страницы записей: ${count}`);
