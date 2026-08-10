/* Служебный работник: делает повторный заход мгновенным и оставляет сайт
   читаемым, когда сеть отвалилась совсем. Это главный запас прочности там,
   где интернет то есть, то нет.

   Правила простые:
   — страницу и ленту записей берём из сети, кэш только на случай обрыва,
     иначе читатель увидит вчерашний дневник;
   — шрифты, картинки и прочую статику берём из кэша: они не меняются;
   — видео не трогаем вовсе: браузер запрашивает его кусками, а кэш с такими
     запросами не работает и ломает перемотку. */

const CACHE = 'sarykov-v3';

// Кладём заранее только то, без чего страница не покажется. Все тридцать два
// файла шрифтов сюда не идут: на 2G это полмегабайта впустую.
const SHELL = [
  '/',
  '/assets/fonts/spectral-200-normal-cyrillic.woff2',
  '/assets/fonts/spectral-300-normal-cyrillic.woff2',
  '/assets/fonts/spectral-400-normal-cyrillic.woff2',
  '/assets/fonts/ibm-plex-mono-400-normal-cyrillic.woff2',
  '/assets/fonts/ibm-plex-mono-500-normal-cyrillic.woff2',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // Отдельные файлы могут не доехать — из-за одного не должна падать
      // вся установка.
      .then((cache) => Promise.all(SHELL.map((url) => cache.add(url).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

function freshFirst(request) {
  return fetch(request)
    .then((res) => {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return res;
    })
    .catch(() => caches.match(request).then((hit) => hit || caches.match('/')));
}

function cacheFirst(request) {
  return caches.match(request).then((hit) => {
    if (hit) return hit;
    return fetch(request).then((res) => {
      if (res && res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return res;
    });
  });
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Видео отдаём напрямую: запросы кусками мимо кэша.
  if (url.pathname.endsWith('.mp4')) return;

  if (request.mode === 'navigate' || url.pathname.endsWith('/posts.json')) {
    event.respondWith(freshFirst(request));
    return;
  }

  event.respondWith(cacheFirst(request));
});
