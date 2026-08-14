/* Проверка сайта перед публикацией.
   Запускается сама на каждый push (.github/workflows/proverka.yml) и вручную:

       node scripts/check-site.mjs

   Ловит ровно те поломки, которые тут уже случались: ссылка на чужой сервер,
   опечатка в пути к файлу, синтаксическая ошибка в скрипте, случайно снесённый
   раздел. Всё, что она пишет, — на русском и с указанием, что чинить. */

import { readFileSync, existsSync, readdirSync, statSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
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
// Страница ошибки — тоже страница сайта, и правила у неё те же. Её долго
// никто не проверял, и на ней всё это время висели чужие шрифты.
const другие = ['404.html'];

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

  // fetch() тоже сетевой запрос. Раньше внешний запасной JSON проходил
  // проверку, хотя нарушал правило «всё отдаёт сам сайт».
  for (const m of код.matchAll(/\bfetch\(\s*['"]((?:https?:)?\/\/[^'"]+)['"]/gi)) {
    const адрес = m[1];
    const хост = (адрес.match(/^(?:https?:)?\/\/([^/]+)/i) || [])[1];
    if (хост && !СВОИ.includes(хост.toLowerCase())) чужие.add(адрес);
  }

  // Остальные страницы сайта — по тем же правилам.
  for (const файл of другие){
    const текст = безКомментариев(readFileSync(ROOT + файл, 'utf8'));
    for (const m of текст.matchAll(раздатчики)) чужие.add(файл + ': ' + m[0]);
    for (const m of текст.matchAll(/<link\b[^>]*\bhref="(https?:)?\/\/([^"]+)"/gi)){
      const хост = (m[2] || '').split('/')[0].toLowerCase();
      if (!СВОИ.includes(хост)) чужие.add(файл + ': ' + хост);
    }
  }

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

шаг('файлы других страниц на месте', () => {
  const нет = [];
  for (const файл of другие){
    const текст = readFileSync(ROOT + файл, 'utf8');
    for (const m of текст.matchAll(/(?:href|src)="(\/[^"?#]+)"/g)){
      if (!existsSync(ROOT + m[1].replace(/^\//, ''))) нет.push(файл + ' → ' + m[1]);
    }
    for (const m of текст.matchAll(/url\((\/[^)]+)\)/g)){
      if (!existsSync(ROOT + m[1].replace(/^\//, ''))) нет.push(файл + ' → ' + m[1]);
    }
  }
  if (нет.length) throw new Error('ссылки на несуществующее:\n      ' + нет.join('\n      '));
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

шаг('навигация доступна скринридерам', () => {
  if (/role="(?:tab|tablist|tabpanel)"/.test(html) || /aria-selected=/.test(html)) {
    throw new Error('не возвращайте tab-ARIA без полного клавиатурного паттерна; это обычные ссылки разделов');
  }
  if (!/data-tab="home"[^>]*aria-current="page"/.test(html)) {
    throw new Error('текущий раздел не отмечен aria-current="page"');
  }
});

шаг('базовая защита браузера на месте', () => {
  if (!/<meta[^>]+http-equiv="Content-Security-Policy"/i.test(html)) {
    throw new Error('в index.html нет Content-Security-Policy');
  }
  if (!/<meta[^>]+name="referrer"[^>]+strict-origin-when-cross-origin/i.test(html)) {
    throw new Error('в index.html нет безопасной referrer policy');
  }
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

шаг('плеер честно завершает ролик и умеет повторять', () => {
  const нужно = [
    ['перемотка ровно в конец', /seekPart\(1\)/],
    ['отдельное состояние конца', /is-ended/],
    ['понятная кнопка повтора', /Повторить видео/],
    ['нативное событие конца', /addEventListener\('ended',\s*закончено\)/],
  ];
  const нет = нужно.filter(([, re]) => !re.test(код)).map(([имя]) => имя);
  if (нет.length) throw new Error('пропало: ' + нет.join(', '));
  if (/seekPart\(0\.999\)/.test(код)) {
    throw new Error('дорожка снова останавливается на 99,9% вместо настоящего конца');
  }
});

шаг('сторож плеера следит за ходом кадра', () => {
  if (!/Math\.abs\(here\s*-\s*былоВремя\)/.test(код)) {
    throw new Error('сторож больше не замечает тихую остановку воспроизведения');
  }
  const playing = код.match(/addEventListener\('playing',[\s\S]{0,300}?\}\);/);
  if (!playing || !/сторожить\(\)/.test(playing[0])) {
    throw new Error('сторож не запускается после начала воспроизведения');
  }
});

шаг('у каждого раздела есть главный заголовок', () => {
  const нет = [];
  const ids = ['home','diary','about','faq','contacts'];
  ids.forEach((id, i) => {
    const start = html.indexOf(`id="tab-${id}"`);
    const end = i + 1 < ids.length ? html.indexOf(`id="tab-${ids[i + 1]}"`) : html.indexOf('</main>');
    if (start < 0 || end < start || !/<h1\b/.test(html.slice(start, end))) нет.push(id);
  });
  if (нет.length) throw new Error('нет h1 в разделах: ' + нет.join(', '));
});

шаг('ссылки в тексте заметны и не ломают ширину', () => {
  if (!/\.entry__body a\.u[^\{]*\{[^}]*text-decoration:\s*underline/.test(код)) {
    throw new Error('ссылки внутри записей снова выглядят как обычный текст');
  }
  if (!/\.entry__body a\.u[^\{]*\{[^}]*overflow-wrap:\s*anywhere/.test(код)) {
    throw new Error('длинная ссылка снова может раздвинуть карточку');
  }
});

шаг('крестик плеера остаётся на экране в альбомном телефоне', () => {
  const mobileTop = код.indexOf('.tv__close{ top:-52px; }');
  const landscape = код.lastIndexOf('@media (max-height:500px)');
  if (mobileTop < 0 || landscape < mobileTop ||
      !/\.tv__close\{\s*top:8px;\s*right:8px;\s*\}/.test(код.slice(landscape))) {
    throw new Error('правило альбомной ориентации должно идти после мобильного top:-52px');
  }
});

/* ── 5. Ничего лишнего ──
   Проверено аудитом: мёртвых стилей нет, утечек слушателей нет, осиротевших
   медиафайлов нет. Здесь караулим то, что уже накапливалось. */
шаг('нет одинаковых файлов среди своих', () => {
  const свои = ['.', 'scripts', 'assets/fonts', 'assets/images', 'assets/icons'];
  const хэши = new Map();
  const дубли = [];
  for (const папка of свои){
    for (const имя of readdirSync(папка)){
      const путь = папка === '.' ? имя : папка + '/' + имя;
      if (имя.startsWith('.') || lstatSync(путь).isSymbolicLink()) continue;
      if (!statSync(путь).isFile()) continue;
      const h = createHash('md5').update(readFileSync(путь)).digest('hex');
      if (хэши.has(h)) дубли.push(путь + ' == ' + хэши.get(h));
      else хэши.set(h, путь);
    }
  }
  if (дубли.length) throw new Error('одно и то же лежит дважды:\n      ' + дубли.join('\n      '));
});

шаг('нет файлов, которых никто не читает', () => {
  const мёртвые = ['assets/fonts/fonts.css'].filter((f) => existsSync(ROOT + f));
  if (мёртвые.length){
    throw new Error('эти файлы никто не подключает, их незачем держать:\n      ' + мёртвые.join('\n      '));
  }
});

/* ── 6. Быстрая загрузка ── */
шаг('экономный режим гасит стекло', () => {
  // Класс вешает скрипт в шапке. Однажды правила для него потерялись при
  // переборке стилей: класс был, а снимать было нечего.
  if (!/html\.thin\s+body\{[^}]*background-image:\s*none/.test(html)){
    throw new Error('html.thin больше не убирает световой фон');
  }
  if (!/html\.thin[^{]*\{[^}]*backdrop-filter:\s*none/.test(html)){
    throw new Error('html.thin больше не отключает размытия');
  }
});

шаг('экономный режим ловит медленный канал, а не только «2g»', () => {
  // effectiveType на канале 400 кбит/с — эталон замеров этого сайта — Chrome
  // репортит как «3g», не «2g»: проверено эмуляцией throttling. Без запасной
  // проверки по downlink html.thin на таком канале не включался бы вовсе.
  if (!/c\.saveData\s*===\s*true/.test(html) || !/2g\$/.test(html)){
    throw new Error('пропала проверка saveData/effectiveType для html.thin');
  }
  if (!/c\.downlink/.test(html)){
    throw new Error('пропала проверка downlink для html.thin — на 400 кбит/с эконом-режим перестанет включаться');
  }
});

шаг('богатый режим не трогает радиус блюра', () => {
  // html.rich — противоположность html.thin: включается на сильном канале и
  // железе. Радиус --glass-blur там пробовали увеличить и убрали — даже на
  // «подходящем» по проверкам железе (4 ядра, 8 ГБ) прилипшая шапка при
  // прокрутке проседала до 200 мс на кадр вместо 17-33. Если проверка ниже
  // упала — кто-то вернул это без нового замера.
  const блок = html.match(/html\.rich\{[^}]*\}/);
  if (!блок){
    throw new Error('пропал блок html.rich с токенами богатого режима');
  }
  if (/--glass-blur/.test(блок[0])){
    throw new Error('html.rich снова меняет --glass-blur — это уже роняло кадр при прокрутке до 200 мс, см. комментарий рядом с блоком в index.html');
  }
});

шаг('у видео остаются 1080p, 720p и лёгкий уровень', () => {
  // Лёгкая версия делается шириной 640 и только если она легче основной на
  // 15 процентов. Опусти потолок основной до 640 или ниже — makeSmaller молча
  // откажется её делать, второй уровень исчезнет, и на канале 400 кбит/с
  // ролики перестанут открываться. Порог 704 = 640 * 1.1, ровно то сравнение,
  // которое makeSmaller делает внутри.
  const сборщик = readFileSync(ROOT + 'scripts/fetch-telegram-posts.mjs', 'utf8');
  const m = сборщик.match(/scale='min\(\$\{round \? \d+ : (\d+)\}/);
  if (!m){
    throw new Error('не нашёл потолок основной версии в fetch-telegram-posts.mjs — проверку надо чинить');
  }
  const потолок = Number(m[1]);
  if (потолок < 1920){
    throw new Error(
      `потолок основной версии ${потолок} — Full HD больше недоступен; ` +
      'см. README, «Три качества»');
  }
  if (!/width:\s*kind === 'round' \? 720 : 1280[^\n]+suffix:\s*'mid'/.test(сборщик)) throw new Error('пропала версия 720p (ширина 1280, у кружочка — 720)');
  if (!/width:\s*kind === 'round' \? 360 : 640/.test(сборщик)) throw new Error('пропала лёгкая версия 360p');
  if (!/class="tv__qualityMenu"[^>]*role="menu"/.test(html)) throw new Error('выбор качества больше не раскрывается списком');
});

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

шаг('данные записей целы и ссылаются на свои файлы', () => {
  const данные = JSON.parse(readFileSync(ROOT + 'data/posts.json', 'utf8'));
  if (!Array.isArray(данные) || !данные.length) throw new Error('data/posts.json пуст');
  const ошибки = [];
  const ids = new Set();
  let previousDate = Infinity;

  for (const post of данные) {
    if (!/^danilka2028k\/\d+$/.test(post.id || '')) ошибки.push(`неверный id: ${post.id}`);
    if (ids.has(post.id)) ошибки.push(`дубль id: ${post.id}`);
    ids.add(post.id);
    if (post.link !== `https://t.me/${post.id}`) ошибки.push(`неверная link у ${post.id}`);
    const time = new Date(post.datetime).getTime();
    if (!Number.isFinite(time)) ошибки.push(`неверная datetime у ${post.id}`);
    else if (time > previousDate) ошибки.push(`нарушен порядок дат у ${post.id}`);
    previousDate = time;
    if (!(post.text || '').trim() && !(post.media || []).length) ошибки.push(`пустая запись ${post.id}`);

    for (const media of post.media || []) {
      if (!['photo', 'video', 'round'].includes(media.type)) ошибки.push(`неверный тип медиа у ${post.id}`);
      for (const [key, sizeKey] of [['src','size'], ['srcMid','sizeMid'], ['srcLow','sizeLow'], ['poster','']]) {
        const src = media[key];
        if (!src) continue;
        if (!/^\/?assets\/posts\/[\w.-]+$/.test(src) || src.includes('..')) {
          ошибки.push(`внешний/опасный ${key} у ${post.id}: ${src}`);
          continue;
        }
        const file = ROOT + src.replace(/^\//, '');
        if (!existsSync(file)) ошибки.push(`пропал ${src}`);
        else if (sizeKey && Number.isFinite(media[sizeKey]) && statSync(file).size !== media[sizeKey]) {
          ошибки.push(`неверный ${sizeKey} у ${post.id}: ${src}`);
        }
      }
    }
  }
  if (ошибки.length) throw new Error(ошибки.slice(0, 10).join('\n      '));
});

шаг('синхронизация не может случайно стереть дневник', () => {
  const сборщик = readFileSync(ROOT + 'scripts/fetch-telegram-posts.mjs', 'utf8');
  if (!/if\s*\(!raw\.length\)/.test(сборщик) || !/TG_ALLOW_SHRINK/.test(сборщик)) {
    throw new Error('нет защиты от пустой или резко обрезанной страницы Telegram');
  }
  if (!/path\.extname\(file\)/.test(сборщик) || !/\.tmp\$\{ext\}/.test(сборщик)) {
    throw new Error('оптимизация фото снова может записать JPEG под расширением PNG/WebP');
  }
});

шаг('sitemap совпадает с последней записью', () => {
  const posts = JSON.parse(readFileSync(ROOT + 'data/posts.json', 'utf8'));
  const newest = posts.map((p) => new Date(p.datetime)).filter((d) => !Number.isNaN(d.getTime())).sort((a,b) => b-a)[0];
  const sitemap = readFileSync(ROOT + 'sitemap.xml', 'utf8');
  const lastmod = newest.toISOString().slice(0, 10);
  if (!sitemap.includes('<loc>https://www.sarykov.ru/</loc>') || !sitemap.includes(`<lastmod>${lastmod}</lastmod>`)) {
    throw new Error(`sitemap.xml должен содержать www.sarykov.ru и lastmod ${lastmod}`);
  }
  const robots = readFileSync(ROOT + 'robots.txt', 'utf8');
  if (!robots.includes('Sitemap: https://www.sarykov.ru/sitemap.xml')) throw new Error('в robots.txt неверный Sitemap');
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
