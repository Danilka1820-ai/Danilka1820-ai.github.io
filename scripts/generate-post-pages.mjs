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
<meta name="theme-color" content="#E8EAED">
<script type="application/ld+json">${jsonLd}</script>
<style>
:root{color-scheme:light}html{background:#e8eaed;color:#11151b;font-family:Georgia,serif}body{margin:0}main{width:min(760px,calc(100% - 32px));margin:0 auto;padding:48px 0 72px}nav,footer,time{font:14px/1.5 ui-monospace,monospace}nav{margin-bottom:40px}a{color:#955b32}h1{font-size:clamp(32px,7vw,64px);line-height:1.02;margin:0 0 16px;font-weight:400}time{color:#5f6975}article{font-size:clamp(18px,2.5vw,22px);line-height:1.6}p{margin:1em 0}.media{display:grid;gap:12px;margin:28px 0}.media img,.media video{display:block;width:100%;height:auto;border-radius:18px;background:#11151b}footer{margin-top:48px;padding-top:20px;border-top:1px solid #cbcfd6}@media(max-width:520px){main{padding-top:28px}}
</style>
</head>
<body><main>
<nav><a href="/#/dnevnik/${encodeURIComponent(slug)}">← Открыть запись в дневнике</a></nav>
<h1>${esc(title)}</h1>
${post?.datetime ? `<time datetime="${esc(post.datetime)}">${esc(post.date || post.datetime)}</time>` : ''}
<article>
<div class="media">${mediaHtml(post)}</div>
${paragraphs}
</article>
<footer>${telegram ? `<a href="${esc(telegram)}" rel="noopener noreferrer">Оригинал в Telegram</a> · ` : ''}<a href="/">sarykov.ru</a></footer>
</main></body></html>
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
