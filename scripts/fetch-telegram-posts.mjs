#!/usr/bin/env node
import { mkdir, writeFile, readFile, readdir, unlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

const CHANNEL = process.env.TG_CHANNEL || 'danilka2028k';
const MAX_POSTS = Number(process.env.TG_MAX_POSTS || 40);
const PAGES = Number(process.env.TG_PAGES || 3);

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const DATA_FILE = path.join(ROOT, 'data', 'posts.json');
const MEDIA_DIR = path.join(ROOT, 'assets', 'posts');
const MEDIA_PUBLIC = 'assets/posts';

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

async function get(url) {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const res = await fetch(url, { headers: { 'user-agent': UA, 'accept-language': 'ru,en;q=0.8' } });
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
      return res;
    } catch (err) {
      if (attempt === 3) throw err;
      await new Promise((r) => setTimeout(r, 2000 * 2 ** attempt));
    }
  }
}

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', laquo: '«', raquo: '»', hellip: '…', mdash: '—', ndash: '–' };

function decode(str) {
  return str
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&([a-z]+);/gi, (m, name) => ENTITIES[name.toLowerCase()] ?? m);
}

function htmlToText(html) {
  return decode(
    html
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div)>/gi, '\n')
      .replace(/<[^>]+>/g, '')
  )
    .split('\n')
    .map((line) => line.replace(/[ \t ]+/g, ' ').trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// t.me/s/<channel> renders each post inside a .tgme_widget_message_wrap block.
function splitPosts(html) {
  const parts = html.split('<div class="tgme_widget_message_wrap');
  parts.shift();
  return parts;
}

function attr(block, re) {
  const m = block.match(re);
  return m ? decode(m[1]) : '';
}

function parsePost(block) {
  const id = attr(block, /data-post="([^"]+)"/);
  if (!id) return null;
  if (/\bservice_message\b/.test(block)) return null;

  const textMatch = block.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>\s*(?:<div class="tgme_widget_message_(?:footer|photo|video|poll|document|link_preview)|<time)/);
  let text = textMatch ? htmlToText(textMatch[1]) : '';
  if (!text) {
    const loose = block.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*)/);
    if (loose) text = htmlToText(loose[1].split('<div class="tgme_widget_message_footer')[0]);
  }

  const media = [];
  const mediaRe = /background-image:\s*url\(['"]([^'"]+)['"]\)/g;
  let m;
  while ((m = mediaRe.exec(block))) {
    const url = decode(m[1]);
    if (/emoji|\/i\/userpic\//.test(url)) continue;
    if (!media.includes(url)) media.push(url);
  }

  const date = attr(block, /<time[^>]+datetime="([^"]+)"/);
  const tags = [...new Set((text.match(/#[\p{L}\p{N}_]{2,}/gu) || []).map((t) => t.slice(1)))];

  return { id, text, date, media, link: `https://t.me/${id}` };
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Moscow' })
    .format(d)
    .replace(/\s*г\.$/, '');
}

function mediaFileName(postId, index, url) {
  const ext = (url.match(/\.(jpe?g|png|webp)(?:\?|$)/i)?.[1] || 'jpg').toLowerCase();
  return `${postId.replace(/[^\w]+/g, '-')}-${index}.${ext === 'jpeg' ? 'jpg' : ext}`;
}

async function downloadMedia(postId, urls) {
  const saved = [];
  for (let i = 0; i < urls.length && i < 4; i++) {
    const file = mediaFileName(postId, i, urls[i]);
    const dest = path.join(MEDIA_DIR, file);
    if (!existsSync(dest)) {
      try {
        const res = await get(urls[i]);
        const buf = Buffer.from(await res.arrayBuffer());
        if (buf.length < 1024) continue;
        await writeFile(dest, buf);
        console.log(`  ↓ ${file} (${Math.round(buf.length / 1024)} KB)`);
      } catch (err) {
        console.warn(`  ! не скачалось ${urls[i]}: ${err.message}`);
        continue;
      }
    }
    saved.push(`${MEDIA_PUBLIC}/${file}`);
  }
  return saved;
}

async function pruneMedia(keep) {
  if (!existsSync(MEDIA_DIR)) return;
  const kept = new Set(keep.map((p) => path.basename(p)));
  for (const file of await readdir(MEDIA_DIR)) {
    if (file.startsWith('.')) continue;
    if (!kept.has(file)) {
      await unlink(path.join(MEDIA_DIR, file));
      console.log(`  × удалил неиспользуемое ${file}`);
    }
  }
}

async function collect() {
  const seen = new Map();
  let url = `https://t.me/s/${CHANNEL}`;
  for (let page = 0; page < PAGES; page++) {
    console.log(`Читаю ${url}`);
    const html = await (await get(url)).text();
    const blocks = splitPosts(html);
    if (!blocks.length) break;

    let oldest = Infinity;
    for (const block of blocks) {
      const post = parsePost(block);
      if (!post) continue;
      if (!seen.has(post.id)) seen.set(post.id, post);
      const num = Number(post.id.split('/').pop());
      if (Number.isFinite(num)) oldest = Math.min(oldest, num);
    }
    if (seen.size >= MAX_POSTS || !Number.isFinite(oldest) || oldest <= 1) break;
    url = `https://t.me/s/${CHANNEL}?before=${oldest}`;
  }

  return [...seen.values()]
    .sort((a, b) => Number(b.id.split('/').pop()) - Number(a.id.split('/').pop()))
    .slice(0, MAX_POSTS);
}

const raw = await collect();
console.log(`Найдено постов: ${raw.length}`);

await mkdir(MEDIA_DIR, { recursive: true });
await mkdir(path.dirname(DATA_FILE), { recursive: true });

const posts = [];
for (const post of raw) {
  const photos = await downloadMedia(post.id, post.media);
  const text = post.text.trim();
  if (!text && !photos.length) continue;
  posts.push({
    id: post.id,
    date: formatDate(post.date),
    datetime: post.date,
    text: text || ' ',
    link: post.link,
    tags: [...new Set((text.match(/#[\p{L}\p{N}_]{2,}/gu) || []).map((t) => t.slice(1)))],
    photo: photos[0] || '',
    photos,
  });
}

await pruneMedia(posts.flatMap((p) => p.photos));

const payload = JSON.stringify(posts, null, 2) + '\n';
const previous = existsSync(DATA_FILE) ? await readFile(DATA_FILE, 'utf8') : '';
if (previous === payload) {
  console.log('Изменений нет.');
} else {
  await writeFile(DATA_FILE, payload);
  console.log(`Записал ${posts.length} постов в data/posts.json`);
}
