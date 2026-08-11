/* Проверка сайта перед публикацией.
   Запускается сама на каждый push (.github/workflows/proverka.yml) и вручную:

       node scripts/check-site.mjs

   Ловит ровно те поломки, которые тут уже случались: ссылка на чужой сервер,
   опечатка в пути к файлу, синтаксическая ошибка в скрипте, случайно снесённый
   раздел. Всё, что она пишет, — на русском и с указанием, что чинить. */

import { readFileSync, existsSync } from 'node:fs';
import { Script } from 'node:vm';

const ROOT = new URL('..', import.meta.url).pathname;
const ошибки = [];
const предупреждения = [];
const порядок = [];

function шаг(имя, дело){
  const было = ошибки.length;
  try { дело(); } catch (e){ ошибки.push(имя + ': ' + e.message); }
  порядок.push({ имя, ок: ошибки.length === было });
}

const html = readFileSync(ROOT + 'index.html', 'utf8');

/* Комментарии в счёт не идут: в них мы сами пишем «не возвращайте
   fonts.googleapis.com», и проверка ловила бы собственное предупреждение. */
function безКомментариев(текст){
  return текст
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:/])\/\/[^\n]*/g, '$1 ');
}
/* Разметка без скриптов: строки внутри кода — не ссылки страницы. */
const разметка = безКомментариев(html.replace(/<script[\s\S]*?<\/script>/gi, ' '));
const стили = (html.match(/<style[\s\S]*?<\/style>/gi) || []).join('\n');
const код = безКомментариев(html);

/* ── 1. Синтаксис скриптов внутри страницы ──
   Опечатка в скрипте убивает весь сайт разом: ни ленты, ни вкладок. */
шаг('скрипты страницы без синтаксических ошибок', () => {
  // Свойства ищем только в открывающем теге: внутри кода есть свои строки
  // с type="button", и раньше проверка принимала их за тип скрипта.
  const блоки = [...html.matchAll(/<script((?![^>]*\ssrc=)[^>]*)>([\s\S]*?)<\/script>/gi)];
  if (!блоки.length) throw new Error('в index.html не найдено ни одного скрипта');
  let проверено = 0;
  блоки.forEach((m, i) => {
    const тип = (m[1].match(/type\s*=\s*"([^"]+)"/) || [])[1];
    if (тип && !/javascript|module/i.test(тип)) return;   // ld+json и прочее
    проверено++;
    try { new Script(m[2]); }
    catch (e){ throw new Error('блок №' + (i+1) + ' — ' + e.message); }
  });
  if (!проверено) throw new Error('ни один блок не удалось проверить — сломан разбор страницы');
});

шаг('sw.js без синтаксических ошибок', () => {
  new Script(readFileSync(ROOT + 'sw.js', 'utf8'));
});

/* ── 2. Ни одного обращения к чужому серверу ──
   Там, где интернет режут, страница из-за такой ссылки встаёт колом. */
const СВОИ = ['sarykov.ru', 'www.sarykov.ru', 'danilka1820-ai.github.io'];

шаг('нет ссылок на чужие серверы', () => {
  const чужие = new Set();

  // Ресурсы: то, что браузер обязан скачать, чтобы страница заработала.
  const ресурсы = [
    ...разметка.matchAll(/<(?:script|img|video|audio|source|iframe)\b[^>]*\ssrc="([^"]+)"/gi),
    ...разметка.matchAll(/<link\b[^>]*\brel="(?:stylesheet|preload|prefetch|modulepreload)"[^>]*\bhref="([^"]+)"/gi),
    ...стили.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/gi),
  ].map((m) => m[1]);

  for (const адрес of ресурсы){
    if (/^(data:|blob:|#)/i.test(адрес)) continue;
    const m = адрес.match(/^(?:https?:)?\/\/([^/]+)/i);
    if (m && !СВОИ.includes(m[1].toLowerCase())) чужие.add(адрес);
  }

  // Известные раздатчики библиотек — даже если попали не в атрибут ресурса.
  const раздатчики = /(cdn\.plyr\.io|video\.js|videojs|unpkg\.com|jsdelivr\.net|cdnjs\.|fonts\.googleapis\.com|fonts\.gstatic\.com|ajax\.googleapis\.com)/gi;
  for (const m of код.matchAll(раздатчики)) чужие.add(m[0]);

  if (чужие.size){
    throw new Error('уберите их, всё нужное лежит в репозитории:\n      ' +
      [...чужие].join('\n      '));
  }
});

/* ── 3. Все файлы, на которые ссылается страница, на месте ── */
шаг('все файлы на месте', () => {
  const пути = new Set();
  const собрать = (текст, re) => { for (const m of текст.matchAll(re)) пути.add(m[1]); };
  собрать(разметка, /<(?:script|img|video|audio|source)\b[^>]*\ssrc="([^"]+)"/gi);
  собрать(разметка, /<link\b[^>]*\bhref="([^"]+)"/gi);
  собрать(стили, /url\(\s*['"]?([^'")]+)['"]?\s*\)/gi);

  const нет = [];
  for (let путь of пути){
    if (/^(data:|blob:|mailto:|tel:|#)/i.test(путь)) continue;
    const свой = путь.match(/^https?:\/\/([^/]+)(\/.*)?$/i);
    if (свой){
      if (!СВОИ.includes(свой[1].toLowerCase())) continue;  // чужие — пункт выше
      путь = свой[2] || '/';
    }
    if (путь === '/') continue;
    const файл = ROOT + путь.replace(/^\//, '').split('?')[0];
    if (!existsSync(файл)) нет.push(путь);
  }
  if (нет.length) throw new Error('в index.html ссылки на несуществующее:\n      ' + нет.join('\n      '));
});

шаг('список в sw.js совпадает с файлами', () => {
  const sw = readFileSync(ROOT + 'sw.js', 'utf8');
  const блок = sw.match(/const SHELL = \[([\s\S]*?)\];/);
  if (!блок) throw new Error('в sw.js не найден список SHELL');
  const нет = [...блок[1].matchAll(/'([^']+)'/g)]
    .map((m) => m[1])
    .filter((путь) => путь !== '/' && !existsSync(ROOT + путь.replace(/^\//, '')));
  if (нет.length) throw new Error('в SHELL перечислено несуществующее:\n      ' + нет.join('\n      '));
});

/* ── 4. Разделы и окно просмотра на месте ──
   Однажды при переделке плеера окно просмотра снесли целиком и заметили
   это только на живом сайте. */
шаг('пять разделов на месте', () => {
  const нет = ['home','diary','about','faq','contacts']
    .filter((k) => !html.includes('id="tab-' + k + '"'));
  if (нет.length) throw new Error('пропали панели: ' + нет.join(', '));
});

шаг('окно просмотра и плеер на месте', () => {
  const нужно = [
    ['окно просмотра',        /function openTheater\s*\(/],
    ['переход к соседнему',   /function step\s*\(/],
    ['выбор качества',        /function pickLevel\s*\(/],
    ['панель управления',     /function buildTheater\s*\(/],
    ['перемотка',             /function seekPart\s*\(/],
    ['пропорции кадра',       /--tv-ratio/],
    ['сообщение об ошибке',   /tv__fail/],
    ['выход из полного экрана', /function leaveFullscreen\s*\(/],
    ['спасение на плохой связи', /function спасти\s*\(/],
  ];
  const нет = нужно.filter(([, re]) => !re.test(html)).map(([имя]) => имя);
  if (нет.length) throw new Error('пропало: ' + нет.join(', '));
});

/* ── 5. Быстрая загрузка ── */
шаг('шрифты подключаются после ленты', () => {
  if (!/id="fontStyles"[^>]*media="print"/.test(html)){
    throw new Error('пропал media="print" у #fontStyles — страница снова будет ждать шрифты');
  }
});

шаг('в ленте нет элементов video', () => {
  const тело = html.slice(html.indexOf('<body'));
  const скрипты = тело.replace(/<script[\s\S]*?<\/script>/gi, '');
  if (/<video\b/i.test(скрипты)){
    throw new Error('в разметке появился <video> — ролики начнут качаться до нажатия');
  }
});

шаг('данные записей читаются', () => {
  const данные = JSON.parse(readFileSync(ROOT + 'data/posts.json', 'utf8'));
  if (!Array.isArray(данные) || !данные.length) throw new Error('data/posts.json пуст');
  const битые = данные
    .flatMap((p) => (p.media || []).map((m) => m.src))
    .filter((src) => src && !existsSync(ROOT + src.replace(/^\//, '')));
  if (битые.length) throw new Error('в записях ссылки на пропавшие файлы:\n      ' + битые.slice(0, 5).join('\n      '));
});

/* ── Итог ── */
console.log('');
for (const { имя, ок } of порядок) console.log((ок ? '  ✓ ' : '  ✗ ') + имя);
for (const w of предупреждения) console.log('  · ' + w);
console.log('');

if (ошибки.length){
  console.log('Сайт публиковать нельзя, сначала почините:\n');
  for (const e of ошибки) console.log('  ✗ ' + e + '\n');
  console.log('Что и почему нельзя ломать — в README.md, раздел «Что нельзя ломать».\n');
  process.exit(1);
}
console.log('Всё в порядке, можно публиковать.\n');
