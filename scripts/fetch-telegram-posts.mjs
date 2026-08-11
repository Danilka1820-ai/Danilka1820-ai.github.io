#!/usr/bin/env node
import { mkdir, writeFile, readFile, readdir, unlink, stat, rename } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const CHANNEL = process.env.TG_CHANNEL || 'danilka2028k';
const MAX_POSTS = Number(process.env.TG_MAX_POSTS || 40);
const PAGES = Number(process.env.TG_PAGES || 3);
const MAX_MEDIA_PER_POST = 6;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_VIDEO_BYTES = Number(process.env.TG_MAX_VIDEO_MB || 45) * 1024 * 1024;

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const DATA_FILE = path.join(ROOT, 'data', 'posts.json');
const MEDIA_DIR = path.join(ROOT, 'assets', 'posts');
const MEDIA_PUBLIC = 'assets/posts';

// Меняется, когда меняются настройки сжатия: старые файлы тогда перекачиваются
// и проходят обработку заново.
const MEDIA_RECIPE = 'v5-two-qualities';
const RECIPE_FILE = path.join(MEDIA_DIR, '.recipe');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function tool(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: 'ignore' });
  return res.status === 0;
}

const FFMPEG = tool('ffmpeg', ['-version']) ? 'ffmpeg' : '';
const MAGICK = tool('magick', ['-version']) ? 'magick' : (tool('convert', ['-version']) ? 'convert' : '');

async function sizeOf(file) {
  try { return (await stat(file)).size; } catch { return 0; }
}

// Оставляем файл только если обработка реально уменьшила его.
async function keepIfSmaller(original, candidate, label) {
  const before = await sizeOf(original);
  const after = await sizeOf(candidate);
  if (after > 1024 && after < before) {
    await rename(candidate, original);
    console.log(`    ${label}: ${Math.round(before / 1024)} → ${Math.round(after / 1024)} KB`);
    return true;
  }
  if (existsSync(candidate)) await unlink(candidate);
  return false;
}

// Telegram иногда отдаёт AV1 или HEVC. Они компактнее, но не открываются на
// старых iPhone и части Android — как раз там, где сайт и должен работать.
function videoCodec(file) {
  const res = spawnSync('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', file,
  ], { encoding: 'utf8' });
  return res.status === 0 ? res.stdout.trim() : '';
}

// Видео приходит с индексом в конце файла и с битрейтом под полный экран. На
// слабом интернете это значит: браузер сначала дочитывает хвост, потом качает
// мегабайты ради кружочка размером 260 пикселей. Пережимаем под тот размер, в
// котором видео показывается, и переносим индекс в начало.
async function optimizeVideo(file, kind) {
  if (!FFMPEG) return;
  const round = kind === 'round';
  const tmp = `${file}.tmp.mp4`;

  const codec = videoCodec(file);
  // H.264 понимают все браузеры; всё остальное переводим в него обязательно,
  // даже если файл от этого немного потяжелеет.
  const mustConvert = codec && codec !== 'h264';

  const ok = tool(FFMPEG, [
    '-y', '-loglevel', 'error', '-i', file,
    '-vf', `scale='min(${round ? 540 : 1280},iw)':-2`,
    '-c:v', 'libx264', '-crf', round ? '32' : '28', '-preset', 'veryfast',
    '-profile:v', 'main', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', round ? '64k' : '96k', '-ac', '1',
    '-movflags', '+faststart', tmp,
  ]);
  if (ok && mustConvert && (await sizeOf(tmp)) > 1024) {
    const before = await sizeOf(file);
    await rename(tmp, file);
    console.log(`    перевёл ${codec} → h264: ${Math.round(before / 1024)} → ${Math.round((await sizeOf(file)) / 1024)} KB`);
    return;
  }
  if (ok && (await keepIfSmaller(file, tmp, 'сжал'))) return;

  // Пережать не вышло или стало только больше — хотя бы переносим индекс вперёд.
  const remux = `${file}.tmp2.mp4`;
  if (tool(FFMPEG, ['-y', '-loglevel', 'error', '-i', file, '-c', 'copy', '-movflags', '+faststart', remux])) {
    const after = await sizeOf(remux);
    if (after > 1024) {
      await rename(remux, file);
      console.log('    индекс перенесён в начало файла');
      return;
    }
  }
  if (existsSync(remux)) await unlink(remux);
}

// Вторая, лёгкая версия ролика. На слабом канале основной файл может просто
// не успевать подгружаться, и человеку нужен вариант, который поедет.
async function makeLight(file, kind) {
  if (!FFMPEG) return '';
  const light = file.replace(/\.mp4$/, '-low.mp4');
  if (existsSync(light)) return light;

  const round = kind === 'round';
  const ok = tool(FFMPEG, [
    '-y', '-loglevel', 'error', '-i', file,
    '-vf', `scale='min(${round ? 360 : 640},iw)':-2`,
    '-c:v', 'libx264', '-crf', round ? '34' : '32', '-preset', 'veryfast',
    '-profile:v', 'baseline', '-level', '3.0', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '48k', '-ac', '1',
    '-movflags', '+faststart', light,
  ]);
  if (!ok) { if (existsSync(light)) await unlink(light); return ''; }

  // Если «лёгкая» вышла не легче — она бессмысленна.
  const [full, small] = [await sizeOf(file), await sizeOf(light)];
  if (small < 1024 || small >= full * 0.85) { await unlink(light); return ''; }
  console.log(`    лёгкая версия: ${Math.round(full / 1024)} → ${Math.round(small / 1024)} KB`);
  return light;
}

async function optimizeImage(file) {
  const tmp = `${file}.tmp.jpg`;
  let ok = false;

  if (MAGICK) {
    ok = tool(MAGICK, [file, '-auto-orient', '-strip', '-resize', '1200x1200>', '-quality', '76', '-interlace', 'Plane', tmp]);
  } else if (FFMPEG) {
    ok = tool(FFMPEG, [
      '-y', '-loglevel', 'error', '-i', file,
      '-vf', "scale='min(1200,iw)':-2", '-q:v', '5', tmp,
    ]);
  }

  if (ok) await keepIfSmaller(file, tmp, 'ужал');
  else if (existsSync(tmp)) await unlink(tmp);
}

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
      .replace(/<\/?(p|div|blockquote|pre)\b[^>]*>/gi, '\n')
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

// The caption sits in a div that itself contains nested divs (quotes, spoilers),
// so walk the tags and stop at the matching close instead of the first one.
function extractTextHtml(block) {
  const open = block.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>/);
  if (!open) return '';
  let depth = 1;
  const start = open.index + open[0].length;
  const tagRe = /<(\/?)div\b[^>]*>/g;
  tagRe.lastIndex = start;
  let tag;
  while ((tag = tagRe.exec(block))) {
    depth += tag[1] ? -1 : 1;
    if (depth === 0) return block.slice(start, tag.index);
  }
  return block.slice(start);
}

// Photos, videos and round video notes all live in the same block. Walk the tags
// in order: <video> carries the playable source, while the background-image on a
// photo wrap is a picture and the one on a player is only that video's poster.
function collectMedia(block) {
  const items = [];
  const posters = [];
  const tagRe = /<(?:a|i|div|video)\b[^>]*>/g;
  let tag;
  while ((tag = tagRe.exec(block))) {
    const html = tag[0];

    if (html.startsWith('<video')) {
      const src = decode(html.match(/\bsrc="([^"]+)"/)?.[1] || '');
      if (src) items.push({ type: /roundvideo/.test(html) ? 'round' : 'video', src });
      continue;
    }

    const bg = html.match(/background-image:\s*url\(['"]([^'"]+)['"]\)/);
    if (!bg) continue;
    const url = decode(bg[1]);
    if (/emoji|\/i\/userpic\//.test(url)) continue;

    if (/photo_wrap|link_preview_image/.test(html)) items.push({ type: 'photo', src: url });
    else if (/video_thumb|video_player|roundvideo/.test(html)) posters.push(url);
  }

  // Posters appear alongside their players; hand them out in order.
  let next = 0;
  for (const item of items) {
    if (item.type !== 'photo' && next < posters.length) item.poster = posters[next++];
  }

  // A grouped video Telegram refuses to inline has a poster but no source —
  // keep the still so the card is not empty.
  if (!items.length) for (const poster of posters) items.push({ type: 'photo', src: poster });

  return items;
}

function parsePost(block) {
  const id = attr(block, /data-post="([^"]+)"/);
  if (!id) return null;
  if (/\bservice_message\b/.test(block)) return null;

  const text = htmlToText(extractTextHtml(block));

  const media = collectMedia(block);
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

function mediaFileName(postId, suffix, url, fallbackExt) {
  const ext = (url.match(/\.(jpe?g|png|webp|mp4)(?:\?|$)/i)?.[1] || fallbackExt).toLowerCase();
  return `${postId.replace(/[^\w]+/g, '-')}-${suffix}.${ext === 'jpeg' ? 'jpg' : ext}`;
}

// Telegram's media URLs are signed and expire, so every file is mirrored into the
// repository — that is also what keeps the site readable where Telegram is blocked.
async function mirror(postId, suffix, url, fallbackExt, maxBytes, kind) {
  const file = mediaFileName(postId, suffix, url, fallbackExt);
  const dest = path.join(MEDIA_DIR, file);
  const publicPath = `${MEDIA_PUBLIC}/${file}`;
  if (existsSync(dest)) return publicPath;

  try {
    const res = await get(url);
    const declared = Number(res.headers.get('content-length') || 0);
    if (declared > maxBytes) {
      console.warn(`  ! ${file} слишком большой (${Math.round(declared / 1048576)} МБ) — оставляю ссылку на Telegram`);
      return '';
    }
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length < 1024 || buf.length > maxBytes) return '';
    await writeFile(dest, buf);
    console.log(`  ↓ ${file} (${Math.round(buf.length / 1024)} KB)`);
    if (kind === 'photo') await optimizeImage(dest);
    else await optimizeVideo(dest, kind);
    return publicPath;
  } catch (err) {
    console.warn(`  ! не скачалось ${url.slice(0, 80)}: ${err.message}`);
    return '';
  }
}

async function downloadMedia(postId, items) {
  const saved = [];
  for (let i = 0; i < items.length && i < MAX_MEDIA_PER_POST; i++) {
    const item = items[i];
    if (item.type === 'photo') {
      const src = await mirror(postId, i, item.src, 'jpg', MAX_IMAGE_BYTES, 'photo');
      if (src) saved.push({ type: 'photo', src });
      continue;
    }
    const src = await mirror(postId, i, item.src, 'mp4', MAX_VIDEO_BYTES, item.type);
    const poster = item.poster ? await mirror(postId, `${i}p`, item.poster, 'jpg', MAX_IMAGE_BYTES, 'photo') : '';
    if (src) {
      const disk = path.join(ROOT, src);
      const lightDisk = await makeLight(disk, item.type);
      const entry = { type: item.type, src, poster, size: await sizeOf(disk) };
      if (lightDisk) {
        entry.srcLow = `${MEDIA_PUBLIC}/${path.basename(lightDisk)}`;
        entry.sizeLow = await sizeOf(lightDisk);
      }
      saved.push(entry);
    }
    else if (poster) saved.push({ type: 'photo', src: poster, unplayable: true });
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

console.log(`Обработка медиа: ffmpeg ${FFMPEG ? 'есть' : 'НЕТ'}, ImageMagick ${MAGICK ? 'есть' : 'НЕТ'}`);

// Настройки сжатия сменились — старое зеркало сбрасываем, чтобы файлы
// перекачались и прошли обработку заново.
const savedRecipe = existsSync(RECIPE_FILE) ? (await readFile(RECIPE_FILE, 'utf8')).trim() : '';
if (savedRecipe !== MEDIA_RECIPE && FFMPEG) {
  const stale = (await readdir(MEDIA_DIR)).filter((f) => !f.startsWith('.'));
  if (stale.length) console.log(`Настройки сжатия обновились — переобрабатываю ${stale.length} файлов`);
  for (const f of stale) await unlink(path.join(MEDIA_DIR, f));
}

const posts = [];
for (const post of raw) {
  const media = await downloadMedia(post.id, post.media);
  const text = post.text.trim();
  if (!text && !media.length) continue;
  posts.push({
    id: post.id,
    date: formatDate(post.date),
    datetime: post.date,
    text: text || ' ',
    link: post.link,
    tags: [...new Set((text.match(/#[\p{L}\p{N}_]{2,}/gu) || []).map((t) => t.slice(1)))],
    media,
  });
}

const counts = posts.flatMap((p) => p.media).reduce((acc, m) => ({ ...acc, [m.type]: (acc[m.type] || 0) + 1 }), {});
console.log('Медиа:', counts);

await pruneMedia(posts.flatMap((p) => p.media).flatMap((m) => [m.src, m.srcLow, m.poster].filter(Boolean)));
if (FFMPEG) await writeFile(RECIPE_FILE, `${MEDIA_RECIPE}\n`);

const totals = { фото: 0, видео: 0 };
for (const f of (await readdir(MEDIA_DIR)).filter((x) => !x.startsWith('.'))) {
  const bytes = await sizeOf(path.join(MEDIA_DIR, f));
  totals[f.endsWith('.mp4') ? 'видео' : 'фото'] += bytes;
}
console.log(`Вес медиа: фото ${(totals.фото / 1048576).toFixed(1)} МБ, видео ${(totals.видео / 1048576).toFixed(1)} МБ`);

const payload = JSON.stringify(posts, null, 2) + '\n';
const previous = existsSync(DATA_FILE) ? await readFile(DATA_FILE, 'utf8') : '';
if (previous === payload) {
  console.log('Изменений нет.');
} else {
  await writeFile(DATA_FILE, payload);
  console.log(`Записал ${posts.length} постов в data/posts.json`);
}
