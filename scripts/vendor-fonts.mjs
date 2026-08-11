#!/usr/bin/env node
// Складывает шрифты рядом с сайтом. Внешний запрос к fonts.googleapis.com —
// самое узкое место там, где интернет режут: пока ответа нет, страница стоит
// пустая. Со своими файлами сайт зависит только от самого себя.
import { mkdir, writeFile, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT_DIR = path.join(ROOT, 'assets', 'fonts');
const PAGE = path.join(ROOT, 'index.html');
// Описание шрифтов вставляется прямо в страницу между этими метками: на
// медленном канале отдельный файл стоит лишнего обращения к серверу.
const MARK_BEGIN = '/* ШРИФТЫ:НАЧАЛО */';
const MARK_END = '/* ШРИФТЫ:КОНЕЦ */';

// Только то, что реально встречается в стилях: лишнее начертание — это два
// файла (кириллица и латиница) и примерно 40 КБ на первом заходе.
const CSS_URL =
  'https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,200;0,300;0,400;1,300&family=IBM+Plex+Mono:wght@400;500&display=swap';

// woff2 отдают только современным браузерам — представляемся именно им.
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

// Кириллица и латиница; греческий и вьетнамский набор сайту не нужен.
const WANTED = ['cyrillic', 'cyrillic-ext', 'latin', 'latin-ext'];

async function get(url) {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const res = await fetch(url, { headers: { 'user-agent': UA } });
      if (!res.ok) throw new Error(`HTTP ${res.status} для ${url}`);
      return res;
    } catch (err) {
      if (attempt === 3) throw err;
      await new Promise((r) => setTimeout(r, 1500 * 2 ** attempt));
    }
  }
}

const css = await (await get(CSS_URL)).text();

// Google перечисляет наборы символов комментарием перед каждым @font-face.
const blocks = css.split('/*').slice(1).map((chunk) => {
  const subset = chunk.slice(0, chunk.indexOf('*/')).trim();
  return { subset, body: chunk.slice(chunk.indexOf('*/') + 2) };
});

await mkdir(OUT_DIR, { recursive: true });

const out = [
  '/* Шрифты лежат на самом сайте: внешних запросов при загрузке страницы нет.',
  '   Файл собирается скриптом scripts/vendor-fonts.mjs, руками не правится. */',
  '',
];
let saved = 0;
let bytes = 0;

for (const { subset, body } of blocks) {
  if (!WANTED.includes(subset)) continue;

  const family = body.match(/font-family:\s*'([^']+)'/)?.[1];
  const style = body.match(/font-style:\s*([^;]+);/)?.[1]?.trim() || 'normal';
  const weight = body.match(/font-weight:\s*([^;]+);/)?.[1]?.trim() || '400';
  const range = body.match(/unicode-range:\s*([^;]+);/)?.[1]?.trim();
  const url = body.match(/url\((https:\/\/[^)]+\.woff2)\)/)?.[1];
  if (!family || !url) continue;

  const file = `${family.toLowerCase().replace(/\s+/g, '-')}-${weight}-${style}-${subset}.woff2`;
  const buf = Buffer.from(await (await get(url)).arrayBuffer());
  await writeFile(path.join(OUT_DIR, file), buf);
  saved++;
  bytes += buf.length;

  out.push('@font-face{');
  out.push(`  font-family:'${family}';`);
  out.push(`  font-style:${style};`);
  out.push(`  font-weight:${weight};`);
  out.push('  font-display:swap;');
  out.push(`  src:url('./${file}') format('woff2');`);
  if (range) out.push(`  unicode-range:${range};`);
  out.push('}');
}

if (!saved) throw new Error('не удалось разобрать ответ Google Fonts');

// Правила идут прямо в страницу: отдельный файл — это лишний поход к
// серверу, а при большой задержке он стоит дороже собственного веса. Раньше
// рядом писался ещё и assets/fonts/fonts.css — его не читал никто.
const fontCss = out.join('\n') + '\n';
const page = await readFile(PAGE, 'utf8');
const from = page.indexOf(MARK_BEGIN);
const to = page.indexOf(MARK_END);
if (from === -1 || to === -1) {
  console.warn('! меток для шрифтов в index.html нет — вставка пропущена');
} else {
  const inline = fontCss.replace(/url\('\.\//g, "url('/assets/fonts/");
  const next = page.slice(0, from + MARK_BEGIN.length) + '\n' + inline + page.slice(to);
  if (next !== page) {
    await writeFile(PAGE, next);
    console.log('Правила шрифтов вставлены в index.html');
  }
}

console.log(`Сохранено ${saved} файлов шрифтов, ${Math.round(bytes / 1024)} КБ`);
for (const f of (await readdir(OUT_DIR)).sort()) console.log('  ' + f);
