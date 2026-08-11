/* ─────────────────────────────────────────────────────────────────────────
   САЙТ sarykov.ru — весь сценарий страницы

   СОДЕРЖАНИЕ (ищите по названию блока)
     1. НАСТРОЙКИ         — откуда берутся записи, пределы
     2. КЭШ И ЭКОНОМИЯ    — служебный работник, режим слабой связи
     3. ШАПКА             — часы, город, три точки, линия при прокрутке
     4. РАЗДЕЛЫ           — вкладки, адреса вида #/dnevnik, капсула
     5. ПЛЕЕР             — видео и кружочки: пуск, перемотка, звук
     6. ЗАПИСИ            — разбор данных и сборка карточек
     7. ЛЕНТА И РУБРИКИ   — отрисовка, фильтры, ошибки загрузки
   ───────────────────────────────────────────────────────────────────────── */
(function(){
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var THIN = document.documentElement.classList.contains('thin');

  /* ── шрифты включаем, когда лента уже показана ──
     Пока начертания не подключены, текст читается системным шрифтом: на узком
     канале это отдаёт данным дорогу и лента приходит в несколько раз раньше.
     Запас по времени — на случай, если данные не доехали вовсе. */
  var fontStyles = document.getElementById('fontStyles');
  function enableFonts(){
    if (fontStyles && fontStyles.media !== 'all') fontStyles.media = 'all';
  }
  setTimeout(enableFonts, 4000);

  /* ── повторный заход из кэша: мгновенно и даже без сети ── */
  if ('serviceWorker' in navigator){
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('/sw.js').catch(function(){});
    });
  }

  /* ── год в подвале ── */
  var yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ── город посетителя по часовому поясу ── */
  var TZ_CITY = {
    'Europe/Kaliningrad':'Калининград','Europe/Moscow':'Москва','Europe/Simferopol':'Симферополь',
    'Europe/Volgograd':'Волгоград','Europe/Saratov':'Саратов','Europe/Astrakhan':'Астрахань',
    'Europe/Ulyanovsk':'Ульяновск','Europe/Kirov':'Киров','Europe/Samara':'Самара',
    'Asia/Yekaterinburg':'Екатеринбург','Asia/Omsk':'Омск','Asia/Novosibirsk':'Новосибирск',
    'Asia/Barnaul':'Барнаул','Asia/Tomsk':'Томск','Asia/Krasnoyarsk':'Красноярск',
    'Asia/Irkutsk':'Иркутск','Asia/Chita':'Чита','Asia/Yakutsk':'Якутск',
    'Asia/Vladivostok':'Владивосток','Asia/Magadan':'Магадан','Asia/Sakhalin':'Сахалин',
    'Asia/Kamchatka':'Камчатка','Asia/Anadyr':'Анадырь',
    'Europe/Minsk':'Минск','Europe/Kyiv':'Киев','Europe/Kiev':'Киев','Europe/Chisinau':'Кишинёв',
    'Asia/Almaty':'Алматы','Asia/Tashkent':'Ташкент','Asia/Bishkek':'Бишкек',
    'Asia/Tbilisi':'Тбилиси','Asia/Yerevan':'Ереван','Asia/Baku':'Баку',
    'Europe/Istanbul':'Стамбул','Asia/Dubai':'Дубай','Europe/Berlin':'Берлин',
    'Europe/Warsaw':'Варшава','Europe/London':'Лондон','Europe/Paris':'Париж',
    'America/New_York':'Нью-Йорк','Asia/Tokyo':'Токио'
  };
  var placeEl = document.getElementById('nowPlace');
  if (placeEl){
    try{
      var tz = (Intl.DateTimeFormat().resolvedOptions().timeZone) || '';
      var city = TZ_CITY[tz] || (tz.split('/').pop() || '').replace(/_/g,' ');
      if (city) placeEl.textContent = city;
    }catch(e){}
  }

  /* ── часы и дата ── */
  var timeEl = document.getElementById('nowTime');
  var dateEl = document.getElementById('nowDate');
  function tick(){
    var d = new Date();
    if (timeEl) timeEl.textContent = ('0'+d.getHours()).slice(-2) + ':' + ('0'+d.getMinutes()).slice(-2);
    if (dateEl) dateEl.textContent = ('0'+d.getDate()).slice(-2) + '.' + ('0'+(d.getMonth()+1)).slice(-2) + '.' + d.getFullYear();
  }
  tick();
  setInterval(tick, 30000);

  /* ── три точки: на телефоне под ними прячутся разделы ── */
  var moreBtn = document.getElementById('moreBtn');
  var navMenu = document.getElementById('navMenu');
  if (moreBtn && navMenu){
    var setOpen = function(open){
      navMenu.classList.toggle('is-open', open);
      moreBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    moreBtn.addEventListener('click', function(e){
      e.stopPropagation();
      setOpen(moreBtn.getAttribute('aria-expanded') !== 'true');
    });
    // Выбрали раздел — меню закрывается, чтобы не перекрывать страницу.
    navMenu.addEventListener('click', function(e){
      if (e.target.closest('.tab')) setOpen(false);
    });
    document.addEventListener('click', function(e){
      if (!navMenu.contains(e.target) && e.target !== moreBtn) setOpen(false);
    });
    document.addEventListener('keydown', function(e){
      if (e.key === 'Escape') setOpen(false);
    });
  }

  /* ── линия под шапкой появляется при прокрутке ── */
  var topBar = document.getElementById('top');
  function stuck(){ if (topBar) topBar.classList.toggle('is-stuck', window.scrollY > 8); }
  window.addEventListener('scroll', stuck, { passive:true });
  stuck();

  /* ── кнопка «наверх» ── */
  var up = document.getElementById('up');
  if (up){
    var toggleUp = function(){ up.classList.toggle('is-on', window.scrollY > window.innerHeight * 0.7); };
    window.addEventListener('scroll', toggleUp, { passive:true });
    toggleUp();
    up.addEventListener('click', function(){ window.scrollTo({ top:0, behavior: reduce ? 'auto' : 'smooth' }); });
  }

  /* ── «скопировать» ── */
  var copyBtn = document.getElementById('copyBtn');
  if (copyBtn){
    copyBtn.addEventListener('click', function(){
      var text = copyBtn.getAttribute('data-copy');
      var done = function(){
        var was = copyBtn.textContent;
        copyBtn.textContent = 'Скопировано';
        setTimeout(function(){ copyBtn.textContent = was; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done, function(){});
      } else {
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try{ document.execCommand('copy'); done(); }catch(e){}
        document.body.removeChild(ta);
      }
    });
  }

  /* ── появление блоков при прокрутке ── */
  var io = null;
  if ('IntersectionObserver' in window && !reduce){
    io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if (e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { threshold:.08, rootMargin:'0px 0px -40px 0px' });
  }
  function watch(el){
    if (!el.classList.contains('rise')) return;
    if (io) io.observe(el); else el.classList.add('in');
  }
  [].slice.call(document.querySelectorAll('.rise')).forEach(watch);

  /* ── разделы: адрес вида #/dnevnik, чтобы на раздел можно было дать ссылку ── */
  var TABS = [
    { key:'home',     slug:'glavnaya', title:'Данил Сарыков — Вокруг меня' },
    { key:'diary',    slug:'dnevnik',  title:'Записи — Данил Сарыков' },
    { key:'about',    slug:'o-sebe',   title:'О дневнике — Данил Сарыков' },
    { key:'faq',      slug:'voprosy',  title:'Вопросы — Данил Сарыков' },
    { key:'contacts', slug:'kontakty', title:'Контакты — Данил Сарыков' }
  ];
  var OLD_ANCHORS = { posts:'diary', feed:'diary', main:'home', about:'about', hero:'home' };
  // Имя в шапке — тоже раздел, поэтому ищем по data-tab, а не по классу.
  var tabLinks = [].slice.call(document.querySelectorAll('[data-tab]'));

  function findTab(key){
    for (var i=0;i<TABS.length;i++) if (TABS[i].key === key) return TABS[i];
    return TABS[0];
  }
  function tabByHash(){
    var raw = (location.hash || '').replace(/^#\/?/, '');
    for (var i=0;i<TABS.length;i++) if (TABS[i].slug === raw) return TABS[i];
    if (OLD_ANCHORS[raw]) return findTab(OLD_ANCHORS[raw]);
    return TABS[0];
  }

  /* Капсула едет к выбранному разделу. На телефоне разделы лежат столбиком
     в выпадающем меню — там она не нужна. */
  var navPill = document.getElementById('navPill');
  var topIn = document.querySelector('.top__in');
  var pillPlaced = false;
  function movePill(){
    if (!navPill || !topIn) return;
    var narrow = window.matchMedia('(max-width:900px)').matches;
    var active = document.querySelector('[data-tab][aria-selected="true"]');
    if (narrow || !active || !active.offsetWidth){
      topIn.classList.remove('has-pill');
      pillPlaced = false;
      return;
    }
    // Считаем от края шапки, а не от полосы разделов: имя лежит вне полосы,
    // а сама полоса может быть прокручена.
    var pad = 14;
    var barBox = topIn.getBoundingClientRect();
    var box = active.getBoundingClientRect();
    // Первую установку не анимируем, иначе капсула приезжает из угла.
    if (!pillPlaced) navPill.classList.add('no-anim');
    navPill.style.width = (box.width + pad * 2) + 'px';
    navPill.style.transform = 'translateX(' + (box.left - barBox.left - pad) + 'px)';
    topIn.classList.add('has-pill');
    if (!pillPlaced){
      void navPill.offsetWidth;
      navPill.classList.remove('no-anim');
      pillPlaced = true;
    }
  }
  // Полосу разделов можно прокрутить — капсула едет вместе с ней.
  if (navMenu) navMenu.addEventListener('scroll', movePill, { passive:true });

  var pillTick = 0;
  window.addEventListener('resize', function(){
    if (pillTick) return;
    pillTick = requestAnimationFrame(function(){ pillTick = 0; movePill(); });
  });
  // Ширина надписей меняется, когда доезжают шрифты — пересчитываем.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(movePill);

  function showTab(tab, keepScroll){
    TABS.forEach(function(t){
      var panel = document.getElementById('tab-' + t.key);
      if (panel) panel.hidden = t.key !== tab.key;
    });
    tabLinks.forEach(function(link){
      var active = link.getAttribute('data-tab') === tab.key;
      link.setAttribute('aria-selected', active ? 'true' : 'false');
      // Если строка разделов не помещается и прокручивается — подводим выбранный.
      // В выпадающем меню на телефоне прокручивать нечего.
      if (active && link.scrollIntoView && navMenu && navMenu.scrollWidth > navMenu.clientWidth + 1){
        link.scrollIntoView({ block:'nearest', inline:'nearest', behavior: reduce ? 'auto' : 'smooth' });
      }
    });
    movePill();
    document.title = tab.title;
    // Останавливаем всё, что играло на покинутой вкладке.
    [].slice.call(document.querySelectorAll('video')).forEach(function(v){
      if (!v.paused && !v.closest('.panel:not([hidden])')) v.pause();
    });
    if (!keepScroll) window.scrollTo({ top:0, behavior: reduce ? 'auto' : 'smooth' });
  }

  showTab(tabByHash(), true);
  window.addEventListener('hashchange', function(){ showTab(tabByHash(), false); });

  /* ── записи дневника ──
     GitHub Action каждые 15 минут читает публичную превью-страницу Telegram-канала,
     складывает тексты в data/posts.json, а фото и видео — в assets/posts. Поэтому
     всё отдаёт сам сайт: Telegram у читателя может быть закрыт. */
  var POSTS_SOURCES = [
    '/data/posts.json',
    'https://raw.githubusercontent.com/Danilka1820-ai/Danilka1820-ai.github.io/main/data/posts.json'
  ];
  var railEl = document.getElementById('rail');
  var latestEl = document.getElementById('latestRail');
  var filtersEl = document.getElementById('filters');
  var MAX_FILTERS = 8;
  var WEEKDAYS = ['Вс','Пн','Вт','Ср','Чт','Пт','Сб'];

  function isSafeUrl(url){ return /^(https?:)?\/\//i.test(url) || url.charAt(0) === '/' || /^assets\//.test(url); }

  function partsFromDate(post){
    var out = { short:'', day:'', iso:'' };
    var d = post.datetime ? new Date(post.datetime) : null;
    if (d && !isNaN(d.getTime())){
      out.iso = d.toISOString().slice(0,10);
      try{
        out.short = new Intl.DateTimeFormat('ru-RU', { day:'2-digit', month:'2-digit', timeZone:'Europe/Moscow' }).format(d);
      }catch(e){
        out.short = ('0'+d.getDate()).slice(-2) + '.' + ('0'+(d.getMonth()+1)).slice(-2);
      }
      out.day = WEEKDAYS[d.getDay()];
    } else if (post.date){
      out.short = post.date;
    }
    return out;
  }

  /* Заголовков в Telegram нет — берём первую строку, если она короткая. */
  function splitTitle(text){
    var lines = text.trim().split('\n').map(function(l){ return l.trim(); }).filter(Boolean);
    if (lines.length > 1 && lines[0].length <= 72 && !/[.!?]$/.test(lines[0])){
      return { title: lines[0], body: lines.slice(1) };
    }
    return { title:'', body: lines };
  }

  /* ═══════════════════ 5. ПЛЕЕР ═══════════════════
     Один плеер и для обычного видео, и для кружочка. Отличается только сцена:
     у кружочка круглый кадр, у видео прямоугольный. Панель управления всегда
     под кадром — в круге её обрезало бы, а на телевизоре по ней удобнее
     попадать пультом, чем по наложенным поверх кнопкам.
     Работает и с клавиатуры: пробел — пуск, стрелки — перемотка. */

  var SKIP = 10; // на сколько секунд прыгают кнопки перемотки

  /* ─── Окно просмотра ───
     Само окно — наше: затемнение, стрелки к соседним роликам, подпись, Esc.
     Плеер внутри — Plyr: перемотка, громкость, скорость, меню качества и
     полный экран, включая обходные пути для iPhone.

     Библиотека подгружается только при первом открытии ролика. Кто не смотрит
     видео, не платит за неё ни байтом — а это 32 КБ вместе со стилями. */

  var PLYR_JS = '/assets/vendor/plyr/plyr.min.js';
  var PLYR_CSS = '/assets/vendor/plyr/plyr.css';

  var tv = null;         // разметка окна
  var player = null;     // экземпляр Plyr
  var tvList = null;     // список роликов, по которому листаем
  var tvIndex = -1;
  var tvReturn = null;   // куда вернуть фокус после закрытия
  var plyrReady = null;  // обещание загрузки библиотеки

  function loadOnce(){
    if (plyrReady) return plyrReady;
    plyrReady = new Promise(function(resolve, reject){
      var css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = PLYR_CSS;
      document.head.appendChild(css);

      var js = document.createElement('script');
      js.src = PLYR_JS;
      js.onload = resolve;
      js.onerror = reject;
      document.head.appendChild(js);
    });
    return plyrReady;
  }

  function buildTheater(){
    var root = document.createElement('div');
    root.className = 'tv';
    root.hidden = true;
    root.innerHTML =
      '<div class="tv__backdrop"></div>' +
      '<div class="tv__win" role="dialog" aria-modal="true" aria-label="Просмотр видео">' +
        '<button type="button" class="tv__close" aria-label="Закрыть">✕</button>' +
        '<button type="button" class="tv__nav tv__prev" aria-label="Предыдущее видео">‹</button>' +
        '<button type="button" class="tv__nav tv__next" aria-label="Следующее видео">›</button>' +
        '<div class="tv__stage"><video class="tv__video" playsinline></video></div>' +
        '<div class="tv__cap"></div>' +
      '</div>';
    document.body.appendChild(root);

    root.querySelector('.tv__close').addEventListener('click', closeTheater);
    root.querySelector('.tv__backdrop').addEventListener('click', closeTheater);
    root.querySelector('.tv__prev').addEventListener('click', function(){ step(-1); });
    root.querySelector('.tv__next').addEventListener('click', function(){ step(1); });
    root.video = root.querySelector('.tv__video');
    return root;
  }

  function startPlayer(){
    if (player) return player;
    player = new window.Plyr(tv.video, {
      iconUrl: '/assets/vendor/plyr/plyr.svg',
      // Иначе Plyr при смене качества ходит за пустым роликом на cdn.plyr.io —
      // чужой сервер, который в части регионов недоступен.
      blankVideo: '/assets/vendor/plyr/blank.mp4',
      controls: ['play-large','play','rewind','fast-forward','progress',
                 'current-time','duration','mute','volume','settings','fullscreen'],
      settings: ['quality','speed'],
      speed: { selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 2] },
      seekTime: 10,
      keyboard: { focused: true, global: true },
      tooltips: { controls: true, seek: true },
      i18n: {
        play: 'Проиграть', pause: 'Пауза', restart: 'Сначала',
        rewind: 'Назад на {seektime} с', fastForward: 'Вперёд на {seektime} с',
        seek: 'Перемотка', played: 'Проиграно', buffered: 'Загружено',
        currentTime: 'Текущее время', duration: 'Длительность',
        volume: 'Громкость', mute: 'Выключить звук', unmute: 'Включить звук',
        enterFullscreen: 'Во весь экран', exitFullscreen: 'Выйти из полного экрана',
        settings: 'Настройки', speed: 'Скорость', normal: 'Обычная',
        quality: 'Качество', loop: 'Повтор', start: 'Начало', all: 'Все',
        reset: 'Сбросить', disabled: 'Выключено', enabled: 'Включено'
      }
    });
    return player;
  }

  /* Пробел и стрелки плеер слушает на всём документе — удобно, пока окно
     открыто. Закрытое окно так же ловило бы их и запускало ролик, которого
     не видно. Поэтому на время закрытия эти слушатели снимаем. */
  function globalKeys(on){
    if (player && player.listeners && player.listeners.global){
      try { player.listeners.global(on); } catch (e){}
    }
  }

  /* Какое качество нужно этому экрану.
     Считаем не «телефон или компьютер», а сколько настоящих точек займёт кадр:
     геометрия окна, а не текущая разметка — в момент выбора кадр ещё не
     разложен и намерил бы ерунду. */
  function bestHeight(item){
    if (!item.hLow || !item.h) return item.h || 720;
    var round = item.type === 'round';
    var availW = round
      ? Math.min(window.innerHeight * 0.68, window.innerWidth * 0.74)
      : Math.min(window.innerWidth * 0.92, 1000);
    var availH = round ? availW : window.innerHeight * 0.68;
    var shownW = Math.min(availW, availH * (item.w / item.h));
    // Плотность выше двойки на глаз не отличить, а файл тяжелее вдвое.
    var density = Math.min(window.devicePixelRatio || 1, 2);
    // Лёгкая версия принимается, если покрывает четыре пятых нужного.
    return item.wLow >= shownW * density * 0.8 ? item.hLow : item.h;
  }

  function step(dir){
    if (!tvList) return;
    var next = tvIndex + dir;
    if (next < 0 || next >= tvList.length) return;
    showInTheater(next);
  }

  function showInTheater(index){
    var entry = tvList[index];
    if (!entry) return;
    tvIndex = index;

    var item = entry.item;
    var round = item.type === 'round';
    var win = tv.querySelector('.tv__win');
    win.classList.toggle('tv__win--round', round);
    // Растягивать кадр много выше настоящего размера бессмысленно: детали не
    // прибавится, прибавится мыла. Предел считаем от самого файла.
    win.style.setProperty('--tv-cap', item.w ? Math.round(item.w * 1.6) + 'px' : '100vw');

    var sources = [{ src: item.src, type: 'video/mp4', size: item.h || 720 }];
    if (item.srcLow) sources.push({ src: item.srcLow, type: 'video/mp4', size: item.hLow || 360 });

    var pick = THIN && item.hLow ? item.hLow : bestHeight(item);
    // Пропорции знаем из данных, поэтому место под кадр держим ещё до чтения
    // файла — иначе окно дёргается. Пишем прямо в настройки: у плеера свойство
    // ratio принимает строку, но кладёт её в разбор для массива и обнуляет.
    // Настройки читаются при подключении источника, значит задавать раньше.
    player.config.ratio = item.w && item.h
      ? item.w + ':' + item.h
      : (round ? '1:1' : '16:9');
    player.source = {
      type: 'video',
      title: (round ? 'Кружочек' : 'Видео') + (entry.date ? ' · ' + entry.date : ''),
      sources: sources,
      poster: item.poster || ''
    };
    if (sources.length > 1){
      player.quality = pick;
    }
    player.loop = round;

    tv.querySelector('.tv__cap').textContent =
      (round ? 'Кружочек' : 'Видео') +
      (entry.date ? ' · ' + entry.date : '') +
      ' · ' + (index + 1) + ' из ' + tvList.length;
    tv.querySelector('.tv__prev').disabled = index === 0;
    tv.querySelector('.tv__next').disabled = index === tvList.length - 1;

    var started = player.play();
    if (started && started.catch) started.catch(function(){});
  }

  function onTheaterKey(e){
    if (!tv || tv.hidden) return;
    if (e.key === 'Escape'){ closeTheater(); e.preventDefault(); }
    else if (e.key === 'n' || e.key === 'N'){ step(1); }
    else if (e.key === 'p' || e.key === 'P'){ step(-1); }
  }

  function openTheater(list, index, opener){
    tvReturn = opener || null;
    loadOnce().then(function(){
      if (!tv){
        tv = buildTheater();
        document.addEventListener('keydown', onTheaterKey);
      }
      startPlayer();
      globalKeys(true);
      tvList = list;
      tv.hidden = false;
      document.body.style.overflow = 'hidden';
      showInTheater(index);
      tv.querySelector('.tv__close').focus();
    }).catch(function(){
      // Библиотека не доехала — открываем ролик напрямую, чтобы человек
      // всё-таки его посмотрел.
      var item = list[index] && list[index].item;
      if (item) window.open(item.src, '_blank', 'noopener');
    });
  }

  function closeTheater(){
    if (!tv || tv.hidden) return;
    if (player){
      player.pause();
      if (player.fullscreen && player.fullscreen.active) player.fullscreen.exit();
      globalKeys(false);
    }
    tv.hidden = true;
    document.body.style.overflow = '';
    if (tvReturn && tvReturn.focus) tvReturn.focus();
  }

  function buildPoster(item, list, date){
    var round = item.type === 'round';

    var slot = -1;
    if (list){ slot = list.length; list.push({ item:item, date:date }); }

    var root = document.createElement('button');
    root.type = 'button';
    root.className = 'pl' + (round ? ' pl--round' : '');
    root.setAttribute('aria-label', (round ? 'Проиграть кружочек' : 'Проиграть видео') + (date ? ' от ' + date : ''));

    var stage = document.createElement('span');
    stage.className = 'pl__stage';

    // Постер — обычная картинка: без элемента <video> страница легче, и ни
    // байта ролика не уходит по сети до нажатия.
    if (item.poster && !THIN){
      var img = document.createElement('img');
      img.className = 'pl__poster';
      img.src = item.poster;
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.setAttribute('fetchpriority', 'low');
      img.onerror = function(){ img.remove(); };
      stage.appendChild(img);
    }

    var big = document.createElement('span');
    big.className = 'pl__big';
    big.innerHTML = '<span class="pl__glyph" aria-hidden="true">▶</span>';
    stage.appendChild(big);
    root.appendChild(stage);

    var cap = document.createElement('span');
    cap.className = 'pl__cap';
    cap.textContent = round ? 'кружочек' : 'видео';
    root.appendChild(cap);

    root.addEventListener('click', function(){
      if (list && slot >= 0) openTheater(list, slot, root);
    });
    return root;
  }

  function buildMedia(item, list, date){
    if (item.type === 'photo'){
      var img = document.createElement('img');
      img.className = 'entry__photo';
      img.alt = ''; img.loading = 'lazy'; img.decoding = 'async';
      // Фотографии уступают дорогу тексту и данным: на узком канале они иначе
      // забивают его первыми, и лента появляется много позже.
      img.setAttribute('fetchpriority', 'low');
      img.src = item.src;
      img.onerror = function(){ img.style.display = 'none'; };
      return img;
    }
    return buildPoster(item, list, date);
  }

  function normalize(post){
    var media = Array.isArray(post.media) ? post.media : [];
    return {
      date: post.date || '',
      datetime: post.datetime || '',
      text: post.text || '',
      link: post.link || '',
      tags: Array.isArray(post.tags) ? post.tags : [],
      media: media.filter(function(m){ return m && m.src && isSafeUrl(m.src); })
        .map(function(m){
          if (m.srcLow && !isSafeUrl(m.srcLow)) { m = Object.assign({}, m); delete m.srcLow; }
          return m;
        })
    };
  }

  function buildEntry(post, mediaList){
    var art = document.createElement('article');
    art.className = 'entry rise';
    if (post.tags.length) art.setAttribute('data-tags', post.tags.join(','));

    var when = partsFromDate(post);
    var meta = document.createElement('div');
    meta.className = 'entry__meta';

    var time = document.createElement('time');
    time.className = 'entry__date';
    if (when.iso) time.setAttribute('datetime', when.iso);
    time.textContent = when.short;
    meta.appendChild(time);

    if (when.day){
      var day = document.createElement('span');
      day.className = 'entry__day';
      day.textContent = when.day;
      meta.appendChild(day);
    }
    if (post.tags.length){
      var tag = document.createElement('span');
      tag.className = 'entry__tag';
      tag.textContent = post.tags[0];
      meta.appendChild(tag);
    }
    art.appendChild(meta);

    var body = document.createElement('div');
    body.className = 'entry__body';

    var split = splitTitle(post.text);
    if (split.title){
      var h3 = document.createElement('h3');
      h3.className = 'entry__title';
      h3.textContent = split.title;
      body.appendChild(h3);
    }

    if (post.media.length){
      var box = document.createElement('div');
      box.className = 'media';
      var onlyPhotos = post.media.every(function(m){ return m.type === 'photo'; });
      if (onlyPhotos && post.media.length > 1) box.className += ' media--pair';
      post.media.forEach(function(item){ box.appendChild(buildMedia(item, mediaList, post.date)); });
      body.appendChild(box);
    }

    split.body.forEach(function(line){
      var p = document.createElement('p');
      p.textContent = line;
      body.appendChild(p);
    });

    if (post.link && isSafeUrl(post.link)){
      var more = document.createElement('a');
      more.className = 'entry__more';
      more.href = post.link;
      more.target = '_blank';
      more.rel = 'noopener noreferrer';
      more.textContent = 'Оригинал в Telegram →';
      body.appendChild(more);
    }

    art.appendChild(body);
    return art;
  }

  function renderInto(target, posts, limit){
    target.innerHTML = '';
    var frag = document.createDocumentFragment();
    var shown = 0;
    var tagCount = {};
    var mediaList = [];   // ролики этой ленты — по ним листает окно просмотра
    posts.forEach(function(raw){
      if (limit && shown >= limit) return;
      var post = normalize(raw);
      if (!post.text.trim() && !post.media.length) return;
      shown++;
      post.tags.forEach(function(t){ tagCount[t] = (tagCount[t] || 0) + 1; });
      frag.appendChild(buildEntry(post, mediaList));
    });
    if (!shown){
      target.innerHTML = '<p class="feed__empty">Записей пока нет.</p>';
      return null;
    }
    target.appendChild(frag);
    [].slice.call(target.querySelectorAll('.rise')).forEach(watch);
    // Тем набирается много — показываем те, что встречаются чаще всего.
    return Object.keys(tagCount).sort(function(a,b){
      return tagCount[b] - tagCount[a] || a.localeCompare(b, 'ru');
    });
  }

  function renderFilters(tags){
    if (!filtersEl) return;
    filtersEl.innerHTML = '';
    if (!tags || !tags.length){ filtersEl.hidden = true; return; }
    filtersEl.hidden = false;

    // Один и тот же узел, сколько бы раз ни перерисовывали рубрики.
    var empty = railEl.querySelector('.feed__empty--filter');
    if (!empty){
      empty = document.createElement('p');
      empty.className = 'feed__empty feed__empty--filter';
      empty.textContent = 'В этой рубрике пока нет записей.';
      railEl.appendChild(empty);
    }
    empty.hidden = true;

    function chip(label, value, active){
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('data-tag', value);
      b.setAttribute('aria-pressed', active ? 'true' : 'false');
      b.textContent = label;
      b.addEventListener('click', function(){
        [].slice.call(filtersEl.querySelectorAll('button')).forEach(function(x){
          x.setAttribute('aria-pressed','false');
        });
        b.setAttribute('aria-pressed','true');
        var visible = 0;
        [].slice.call(railEl.querySelectorAll('.entry')).forEach(function(el){
          var own = (el.getAttribute('data-tags') || '').split(',');
          var show = value === 'all' || own.indexOf(value) !== -1;
          el.hidden = !show;
          if (show) visible++;
        });
        empty.hidden = visible > 0;
      });
      return b;
    }

    filtersEl.appendChild(chip('Все','all',true));
    tags.slice(0, MAX_FILTERS).forEach(function(t){ filtersEl.appendChild(chip(t, t, false)); });
  }

  function failure(err){
    var p = document.createElement('p');
    p.className = 'feed__state';
    p.appendChild(document.createTextNode('Не получилось загрузить записи. Загляните в '));
    var a = document.createElement('a');
    a.className = 'u';
    a.href = 'https://t.me/danilka2028k';
    a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.textContent = 'Telegram';
    p.appendChild(a);
    p.appendChild(document.createTextNode('.'));
    if (err && err.message){
      var small = document.createElement('small');
      small.style.cssText = 'display:block;margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--ink-3)';
      small.textContent = String(err.message);
      p.appendChild(small);
    }
    return p;
  }

  if (railEl){
    var loadFrom = function(index){
      if (index >= POSTS_SOURCES.length) throw new Error('источники недоступны');
      return fetch(POSTS_SOURCES[index], { cache:'no-store' })
        .then(function(res){
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.json();
        })
        .then(function(posts){
          if (!Array.isArray(posts) || !posts.length) throw new Error('пустой ответ');
          return posts;
        })
        .catch(function(err){
          if (index + 1 < POSTS_SOURCES.length) return loadFrom(index + 1);
          throw err;
        });
    };

    // Страница успела начать загрузку записей ещё до этого скрипта —
    // пользуемся её результатом, а к запасным источникам идём только если
    // тот запрос не удался.
    var early = window.__posts;
    var first = early
      ? early.then(function(posts){
          if (!Array.isArray(posts) || !posts.length) throw new Error('пустой ответ');
          return posts;
        }).catch(function(){ return loadFrom(0); })
      : loadFrom(0);

    first
      .then(function(posts){
        var tags = renderInto(railEl, posts, 0);
        renderFilters(tags);
        if (latestEl) renderInto(latestEl, posts, 3);
        enableFonts();
      })
      .catch(function(err){
        enableFonts();
        railEl.innerHTML = '';
        railEl.appendChild(failure(err));
        if (latestEl){ latestEl.innerHTML = ''; latestEl.appendChild(failure(err)); }
        if (filtersEl) filtersEl.hidden = true;
      });
  }
})();
