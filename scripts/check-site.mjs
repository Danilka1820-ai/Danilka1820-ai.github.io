/* Проверка сайта перед публикацией.
   Запускается сама на каждый push (.github/workflows/proverka.yml) и вручную:

       node scripts/check-site.mjs

   Ловит ровно те поломки, которые тут уже случались: ссылка на чужой сервер,
   опечатка в пути к файлу, синтаксическая ошибка в скрипте, случайно снесённый
   раздел. Всё, что она пишет, — на русском и с указанием, что чинить. */

import { readFileSync, existsSync, readdirSync, statSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { Script } from 'node:vm';
import { spawnSync } from 'node:child_process';

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
const стили = [
  ...(html.match(/<style[\s\S]*?<\/style>/gi) || []),
  readFileSync(ROOT + 'assets/css/living-diary.css', 'utf8'),
].join('\n');
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

/* Эти два — модули (import/export), vm.Script их не разберёт — она понимает
   только классический скрипт. Раз уже роняли синхронизацию на час дублем
   объявления в fetch-telegram-posts.mjs: он написан, запушен, а само падение
   заметили только по упавшему workflow. Синтаксис модуля умеет проверить
   только node --check — гоняем через него. */
шаг('скрипты scripts/*.mjs без синтаксических ошибок', () => {
  for (const имя of ['fetch-telegram-posts.mjs', 'vendor-fonts.mjs', 'generate-post-pages.mjs', 'check-site.mjs']) {
    const путь = ROOT + 'scripts/' + имя;
    if (!existsSync(путь)) continue;
    const рез = spawnSync(process.execPath, ['--check', путь], { encoding: 'utf8' });
    if (рез.status !== 0) {
      const строки = рез.stderr.trim().split('\n');
      // node --check печатает саму ошибку не последней строкой, а перед стеком
      // и версией — ищем строку вида «...Error: ...», иначе берём последнюю.
      const суть = строки.find((s) => /error:/i.test(s)) || строки.pop();
      throw new Error(имя + ' — ' + суть.trim());
    }
  }
});

шаг('дополнительный клиентский JavaScript без синтаксических ошибок', () => {
  const путь = ROOT + 'assets/js/atmosphere.js';
  const рез = spawnSync(process.execPath, ['--check', путь], { encoding: 'utf8' });
  if (рез.status !== 0) throw new Error(рез.stderr.trim() || 'node --check завершился с ошибкой');
});

/* ── 2. Ни одного обращения к чужому серверу ──
   Там, где интернет режут, страница из-за такой ссылки встаёт колом. */
const СВОИ = ['sarykov.ru', 'www.sarykov.ru', 'danilka1820-ai.github.io'];
const SITE_ORIGIN = 'https://www.sarykov.ru';

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
  const нет = ['home','diary','about','projects','contacts']
    .filter((k) => !html.includes('id="tab-' + k + '"'));
  if (нет.length) throw new Error('пропали панели: ' + нет.join(', '));
  if (!html.includes('href="#/proekty"')) throw new Error('нет ссылки на раздел проектов');
  if (!html.includes('IT-портфолио, проекты и личный дневник')) {
    throw new Error('первый экран не описывает портфолио, проекты и дневник');
  }
  if (!html.includes('id="projectVideoShelf"') || !html.includes('renderProjectVideos(posts)')) {
    throw new Error('раздел проектов не подключён к видеоархиву');
  }
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

шаг('индикатор загрузки и кнопка плеера показываются только по делу', () => {
  const ожидание = стили.match(/\.tv__wait\s*\{([^}]*)\}/);
  const загрузка = стили.match(/\.tv\.is-waiting\s+\.tv__wait\s*\{([^}]*)\}/);
  const игра = стили.match(/\.tv\.is-playing\s+\.tv__big\s*\{([^}]*)\}/);
  if (!ожидание || !/display:\s*none/.test(ожидание[1]) ||
      !загрузка || !/display:\s*block/.test(загрузка[1])) {
    throw new Error('индикатор загрузки видео должен быть виден только при ожидании');
  }
  if (!игра || !/pointer-events:\s*none/.test(игра[1])) {
    throw new Error('невидимая кнопка запуска не должна перехватывать нажатия во время воспроизведения');
  }
});

// Однажды кнопка «назад» в браузере оставляла окно просмотра открытым и
// играющим поверх нового раздела: hashchange менял вкладку, не закрывая
// модалку. Ловим регресс на уровне кода, а не только живым кликом.
шаг('смена адреса закрывает окно просмотра и фото', () => {
  const слушатель = код.match(/window\.addEventListener\('hashchange',\s*function\s*\(\)\s*\{[\s\S]{0,300}?\}\);/);
  if (!слушатель) throw new Error('не нашёл обработчик hashchange');
  if (!/closeTheater\(\)/.test(слушатель[0]) || !/closePhoto\(\)/.test(слушатель[0])) {
    throw new Error('hashchange больше не закрывает окно просмотра/фото — видео может остаться играть поверх смененного раздела');
  }
});

// Кружочек 512×512 в полном экране один раз растягивался цифровым
// увеличением в 2,24 раза (два независимых предела складывались:
// --tv-cap уже нёс запас ×1.6, а CSS домножал его ещё на ×1.4).
шаг('кружочек в полном экране не растягивается вторым множителем', () => {
  const блок = стили.match(/\.tv\.is-fs \.tv__win--round \.tv__stage,\s*\.tv__win--round:fullscreen \.tv__stage\s*\{[\s\S]{0,240}?\}/);
  if (!блок) throw new Error('не нашёл правило размера кружочка в полном экране');
  if (/var\(--tv-cap,\s*96vw\)\s*\*\s*1\.4/.test(блок[0])) {
    throw new Error('предел --tv-cap снова домножается в CSS — кружочек будет растягиваться сильнее заложенного в sizeStage');
  }
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
  const ids = ['home','diary','about','contacts'];
  ids.forEach((id, i) => {
    const start = html.indexOf(`id="tab-${id}"`);
    const end = i + 1 < ids.length ? html.indexOf(`id="tab-${ids[i + 1]}"`) : html.indexOf('</main>');
    if (start < 0 || end < start || !/<h1\b/.test(html.slice(start, end))) нет.push(id);
  });
  if (нет.length) throw new Error('нет h1 в разделах: ' + нет.join(', '));
});

шаг('ссылки в тексте заметны и не ломают ширину', () => {
  if (!/\.entry__body a\.u[^\{]*\{[^}]*text-decoration:\s*underline/.test(стили)) {
    throw new Error('ссылки внутри записей снова выглядят как обычный текст');
  }
  if (!/\.entry__body a\.u[^\{]*\{[^}]*overflow-wrap:\s*anywhere/.test(стили)) {
    throw new Error('длинная ссылка снова может раздвинуть карточку');
  }
});

шаг('крестик плеера остаётся доступным на телефоне', () => {
  if (!/@media \(max-width: 680px\)[\s\S]*?\.tv__close\s*\{\s*top:\s*\.5rem;\s*right:\s*\.5rem;/.test(стили) ||
      !/@media \(max-height: 500px\)\s*\{\s*\.tv__close\s*\{\s*top:\s*\.5rem;\s*right:\s*\.5rem;/.test(стили)) {
    throw new Error('крестик окна плеера должен оставаться внутри экрана на телефонах и в альбомной ориентации');
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
  if (!/\.thin body\s*\{[^}]*background-image:\s*none/.test(стили)){
    throw new Error('html.thin больше не убирает световой фон');
  }
  if (!/\.thin \.top\s*\{[^}]*backdrop-filter:\s*none/.test(стили)){
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

шаг('дизайн-система и настройки эффектов подключены', () => {
  if (!/<link rel="stylesheet" href="\/assets\/css\/living-diary\.css">/.test(html)) {
    throw new Error('index.html не подключает общую дизайн-систему');
  }
  if (!/id="weatherFx"/.test(html) || !/pointer-events:\s*none/.test(стили)) {
    throw new Error('дождевой слой должен быть декоративным и не перехватывать клики');
  }
  if (!/id="effectsToggle"/.test(html) || !/id="transparencyToggle"/.test(html) ||
      !/prefers-reduced-motion:\s*reduce/.test(стили) ||
      !/prefers-reduced-transparency:\s*reduce/.test(стили)) {
    throw new Error('не найдены настройки эффектов или системные режимы доступности');
  }
  const atmosphere = readFileSync(ROOT + 'assets/js/atmosphere.js', 'utf8');
  if (!/visibilitychange/.test(atmosphere) || !/localStorage/.test(atmosphere) ||
      !/catch\s*\(/.test(atmosphere)) {
    throw new Error('эффекты должны останавливаться в фоновой вкладке и работать без localStorage');
  }
});

шаг('ошибка загрузки дневника предлагает повтор', () => {
  if (!/Повторить загрузку/.test(html) || !/loadFrom\(0\)\.then\(renderPosts\)\.catch\(showLoadFailure\)/.test(html)) {
    throw new Error('для повторного запроса данных дневника нужна работающая кнопка');
  }
  if (!/Записей пока нет\./.test(html) || /!Array\.isArray\(posts\)\s*\|\|\s*!posts\.length/.test(html)) {
    throw new Error('пустой список записей должен показывать отдельное пустое состояние, а не ошибку загрузки');
  }
});

шаг('404 использует общую дизайн-систему и настройки', () => {
  const page = readFileSync(ROOT + '404.html', 'utf8');
  if (!page.includes('/assets/css/living-diary.css') ||
      !page.includes('id="effectsToggle"') || !page.includes('id="transparencyToggle"')) {
    throw new Error('404.html не использует общие стили и настройки оформления');
  }
});

шаг('количество файлов не превышает 500', () => {
  const result = spawnSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], {
    cwd: ROOT,
  });
  if (result.status !== 0) throw new Error('не удалось получить список файлов Git');
  const files = new Set(result.stdout.toString().split('\0').filter(Boolean));
  const count = [...files].filter((file) => existsSync(ROOT + file)).length;
  if (count > 500) throw new Error(`в проекте ${count} файлов, допустимо не больше 500`);
  if (count >= 475) предупреждения.push(`до лимита 500 файлов осталось ${500 - count}`);
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
      if (!['photo', 'video', 'round', 'audio'].includes(media.type)) ошибки.push(`неверный тип медиа у ${post.id}`);
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

шаг('голосовые доступны в ленте и на страницах записей', () => {
  const генератор = readFileSync(ROOT + 'scripts/generate-post-pages.mjs', 'utf8');
  if (!/item\.type === 'audio'/.test(html) || !/audio\.preload = 'none'/.test(html)) {
    throw new Error('голосовые должны воспроизводиться по запросу в ленте');
  }
  if (!/item\.type === 'audio'/.test(генератор) || !/<audio controls preload="none"/.test(генератор)) {
    throw new Error('голосовые должны воспроизводиться по запросу на страницах записей');
  }
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
  if (!sitemap.includes(`<loc>${SITE_ORIGIN}/</loc>`) || !sitemap.includes(`<lastmod>${lastmod}</lastmod>`)) {
    throw new Error(`sitemap.xml должен содержать ${SITE_ORIGIN} и lastmod ${lastmod}`);
  }
  const robots = readFileSync(ROOT + 'robots.txt', 'utf8');
  if (!robots.includes(`Sitemap: ${SITE_ORIGIN}/sitemap.xml`)) throw new Error('в robots.txt неверный Sitemap');
  if (sitemap.includes('https://sarykov.ru')) throw new Error('sitemap.xml содержит неканонический apex-домен');
});

шаг('CNAME и SEO используют целевой домен', () => {
  const cname = readFileSync(ROOT + 'CNAME', 'utf8').trim();
  if (cname !== 'www.sarykov.ru') throw new Error(`CNAME должен быть www.sarykov.ru, получено: ${cname}`);
  if (!html.includes(`<link rel="canonical" href="${SITE_ORIGIN}/">`)) {
    throw new Error('canonical главной страницы не совпадает с целевым доменом');
  }
  if (!html.includes(`<meta property="og:url" content="${SITE_ORIGIN}/">`)) {
    throw new Error('OpenGraph URL главной страницы не совпадает с целевым доменом');
  }
  const generator = readFileSync(ROOT + 'scripts/generate-post-pages.mjs', 'utf8');
  if (!generator.includes(`const SITE = '${SITE_ORIGIN}'`)) {
    throw new Error('генератор страниц записей использует другой домен');
  }
  for (const entry of readdirSync(ROOT + 'dnevnik', { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const page = readFileSync(ROOT + 'dnevnik/' + entry.name + '/index.html', 'utf8');
    if (!page.includes('<link rel="stylesheet" href="/assets/css/living-diary.css">') ||
        !page.includes('id="effectsToggle"') || !page.includes('id="transparencyToggle"')) {
      throw new Error(`страница дневника ${entry.name} не использует общий стиль и настройки`);
    }
    if (!page.includes(`<link rel="canonical" href="${SITE_ORIGIN}/dnevnik/`) ||
        !page.includes(`<meta property="og:url" content="${SITE_ORIGIN}/dnevnik/`)) {
      throw new Error(`canonical страницы дневника ${entry.name} не совпадает с целевым доменом`);
    }
    if (/<link rel="canonical" href="https:\/\/sarykov\.ru\//.test(page) ||
        /<meta property="og:url" content="https:\/\/sarykov\.ru\//.test(page)) {
      throw new Error(`SEO-метаданные страницы дневника ${entry.name} используют apex-домен`);
    }
  }
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
