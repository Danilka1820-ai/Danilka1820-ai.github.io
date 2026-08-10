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
  var tabLinks = [].slice.call(document.querySelectorAll('.tab'));

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
  var pillPlaced = false;
  function movePill(){
    if (!navPill || !navMenu) return;
    var narrow = window.matchMedia('(max-width:900px)').matches;
    var active = navMenu.querySelector('.tab[aria-selected="true"]');
    if (narrow || !active || !active.offsetWidth){
      navMenu.classList.remove('has-pill');
      pillPlaced = false;
      return;
    }
    var pad = 14;
    // Первую установку не анимируем, иначе капсула приезжает из угла.
    if (!pillPlaced) navPill.classList.add('no-anim');
    navPill.style.width = (active.offsetWidth + pad * 2) + 'px';
    navPill.style.transform = 'translateX(' + (active.offsetLeft - pad) + 'px)';
    navMenu.classList.add('has-pill');
    if (!pillPlaced){
      void navPill.offsetWidth;
      navPill.classList.remove('no-anim');
      pillPlaced = true;
    }
  }

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
    'https://raw.githubusercontent.com/Danilka1820-ai/Danilka1820-ai.github.io/main/data/posts.json',
    'https://cool-butterfly-3969.dahytan22.workers.dev/'
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

  function clockText(sec){
    if (!isFinite(sec) || sec < 0) sec = 0;
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function iconButton(cls, label, glyph){
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'pl__btn ' + cls;
    b.setAttribute('aria-label', label);
    b.innerHTML = '<span aria-hidden="true">' + glyph + '</span>';
    return b;
  }

  function buildPlayer(item){
    var round = item.type === 'round';

    var video = document.createElement('video');
    video.src = item.src;
    // На слабом интернете видео не должно скачиваться само: до нажатия
    // виден только постер, файл идёт по сети лишь по клику зрителя.
    video.preload = 'none';
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.className = 'pl__video';
    // Постер — это ещё одна картинка на запись. На 2G его не грузим.
    if (item.poster && !THIN) video.poster = item.poster;

    var root = document.createElement('div');
    root.className = 'pl' + (round ? ' pl--round' : '');

    var stage = document.createElement('div');
    stage.className = 'pl__stage';

    var big = document.createElement('button');
    big.type = 'button';
    big.className = 'pl__big';
    big.setAttribute('aria-label', 'Проиграть');
    big.innerHTML = '<span aria-hidden="true">▶</span>';

    stage.appendChild(video);
    stage.appendChild(big);
    root.appendChild(stage);

    var bar = document.createElement('div');
    bar.className = 'pl__bar';

    var back = iconButton('pl__back', 'Назад на ' + SKIP + ' секунд', '↺');
    var play = iconButton('pl__play', 'Проиграть', '▶');
    var fwd  = iconButton('pl__fwd', 'Вперёд на ' + SKIP + ' секунд', '↻');

    var track = document.createElement('div');
    track.className = 'pl__track';
    track.setAttribute('role', 'slider');
    track.setAttribute('tabindex', '0');
    track.setAttribute('aria-label', 'Перемотка');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', '0');
    var fill = document.createElement('div');
    fill.className = 'pl__fill';
    var knob = document.createElement('div');
    knob.className = 'pl__knob';
    track.appendChild(fill);
    track.appendChild(knob);

    var time = document.createElement('span');
    time.className = 'pl__time';
    time.textContent = '0:00';

    var sound = iconButton('pl__sound', 'Выключить звук', '♪');

    bar.appendChild(back);
    bar.appendChild(play);
    bar.appendChild(fwd);
    bar.appendChild(track);
    bar.appendChild(time);
    bar.appendChild(sound);

    if (!round && document.fullscreenEnabled){
      var full = iconButton('pl__full', 'Во весь экран', '⛶');
      full.addEventListener('click', function(){
        if (document.fullscreenElement) document.exitFullscreen();
        else if (root.requestFullscreen) root.requestFullscreen().catch(function(){});
      });
      bar.appendChild(full);
    }

    root.appendChild(bar);

    if (round){
      var cap = document.createElement('div');
      cap.className = 'pl__cap';
      cap.textContent = 'кружочек';
      root.appendChild(cap);
    }

    /* ── управление ── */

    function paint(){
      var dur = video.duration;
      var known = isFinite(dur) && dur > 0;
      var part = known ? (video.currentTime / dur) : 0;
      fill.style.width = (part * 100) + '%';
      knob.style.left = (part * 100) + '%';
      track.setAttribute('aria-valuenow', Math.round(part * 100));
      track.setAttribute('aria-valuetext', clockText(video.currentTime) + (known ? ' из ' + clockText(dur) : ''));
      time.textContent = clockText(video.currentTime) + (known ? ' / ' + clockText(dur) : '');
    }

    function toggle(){
      if (video.paused) video.play().catch(function(){});
      else video.pause();
    }

    // До первого запуска длительность неизвестна: файл ещё не качался.
    // Поэтому перемотка сначала подгружает данные, а потом прыгает.
    // Пока данные едут, ведём одну отложенную цель: при перетаскивании
    // ползунка иначе накопились бы десятки обработчиков и вызовов load().
    var pendingPart = -1;
    function applyPending(){
      video.removeEventListener('loadedmetadata', applyPending);
      if (pendingPart < 0) return;
      var part = pendingPart;
      pendingPart = -1;
      if (isFinite(video.duration) && video.duration > 0){
        video.currentTime = part * video.duration;
        paint();
      }
    }

    function seekTo(part){
      part = Math.max(0, Math.min(1, part));
      if (isFinite(video.duration) && video.duration > 0){
        video.currentTime = part * video.duration;
        paint();
        return;
      }
      var waiting = pendingPart >= 0;
      pendingPart = part;
      if (waiting) return;
      video.addEventListener('loadedmetadata', applyPending);
      video.load();
    }

    function nudge(sec){
      if (isFinite(video.duration) && video.duration > 0){
        video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + sec));
        paint();
      } else if (sec > 0){
        seekTo(0);
      }
    }

    big.addEventListener('click', toggle);
    play.addEventListener('click', toggle);
    video.addEventListener('click', toggle);
    back.addEventListener('click', function(){ nudge(-SKIP); });
    fwd.addEventListener('click', function(){ nudge(SKIP); });

    sound.addEventListener('click', function(){
      video.muted = !video.muted;
      sound.querySelector('span').textContent = video.muted ? '✕' : '♪';
      sound.setAttribute('aria-label', video.muted ? 'Включить звук' : 'Выключить звук');
    });

    function partFromEvent(e){
      var box = track.getBoundingClientRect();
      var x = (e.touches ? e.touches[0].clientX : e.clientX) - box.left;
      return box.width ? x / box.width : 0;
    }

    var dragging = false;
    track.addEventListener('pointerdown', function(e){
      dragging = true;
      if (track.setPointerCapture) track.setPointerCapture(e.pointerId);
      seekTo(partFromEvent(e));
    });
    track.addEventListener('pointermove', function(e){
      if (dragging) seekTo(partFromEvent(e));
    });
    var stopDrag = function(){ dragging = false; };
    track.addEventListener('pointerup', stopDrag);
    track.addEventListener('pointercancel', stopDrag);

    // Стрелками ходят и с клавиатуры, и пультом телевизора.
    track.addEventListener('keydown', function(e){
      var k = e.key;
      if (k === 'ArrowRight'){ nudge(5); e.preventDefault(); }
      else if (k === 'ArrowLeft'){ nudge(-5); e.preventDefault(); }
      else if (k === 'Home'){ seekTo(0); e.preventDefault(); }
      else if (k === 'End'){ seekTo(0.999); e.preventDefault(); }
      else if (k === ' ' || k === 'Enter'){ toggle(); e.preventDefault(); }
    });

    video.addEventListener('play', function(){
      // Два ролика разом — это каша из звука. Останавливаем остальные.
      [].slice.call(document.querySelectorAll('.pl__video')).forEach(function(other){
        if (other !== video && !other.paused) other.pause();
      });
      root.classList.add('is-playing');
      play.querySelector('span').textContent = '❚❚';
      play.setAttribute('aria-label', 'Пауза');
      big.setAttribute('aria-label', 'Пауза');
    });
    var stopped = function(){
      root.classList.remove('is-playing');
      play.querySelector('span').textContent = '▶';
      play.setAttribute('aria-label', 'Проиграть');
      big.setAttribute('aria-label', 'Проиграть');
    };
    video.addEventListener('pause', stopped);
    video.addEventListener('ended', stopped);
    video.addEventListener('loadedmetadata', paint);
    video.addEventListener('timeupdate', paint);
    video.addEventListener('waiting', function(){ root.classList.add('is-waiting'); });
    video.addEventListener('playing', function(){ root.classList.remove('is-waiting'); });

    return root;
  }

  function buildMedia(item){
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
    return buildPlayer(item);
  }

  function normalize(post){
    var media = Array.isArray(post.media) ? post.media : null;
    if (!media){
      var legacy = Array.isArray(post.photos) ? post.photos : (post.photo ? [post.photo] : []);
      media = legacy.map(function(src){ return { type:'photo', src:src }; });
    }
    return {
      date: post.date || '',
      datetime: post.datetime || '',
      text: post.text || '',
      link: post.link || '',
      tags: Array.isArray(post.tags) ? post.tags : [],
      media: media.filter(function(m){ return m && m.src && isSafeUrl(m.src); })
    };
  }

  function buildEntry(post){
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
      post.media.forEach(function(item){ box.appendChild(buildMedia(item)); });
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
    posts.forEach(function(raw){
      if (limit && shown >= limit) return;
      var post = normalize(raw);
      if (!post.text.trim() && !post.media.length) return;
      shown++;
      post.tags.forEach(function(t){ tagCount[t] = (tagCount[t] || 0) + 1; });
      frag.appendChild(buildEntry(post));
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
