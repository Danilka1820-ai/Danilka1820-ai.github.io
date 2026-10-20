(function(){
  'use strict';

  var root = document.documentElement;
  var canvas = document.getElementById('weatherFx');
  var reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  var phone = matchMedia('(max-width: 680px)');
  var settings = { effects:true, lowTransparency:false };

  try{
    settings = Object.assign(settings, JSON.parse(localStorage.getItem('sarykov-appearance-v1') || '{}'));
  }catch(e){}

  function save(){
    try{ localStorage.setItem('sarykov-appearance-v1', JSON.stringify(settings)); }catch(e){}
  }

  function updateSettings(){
    root.classList.toggle('no-effects', !settings.effects);
    root.classList.toggle('low-transparency', settings.lowTransparency);
    var effectsButton = document.getElementById('effectsToggle');
    var transparencyButton = document.getElementById('transparencyToggle');
    if (effectsButton){
      effectsButton.textContent = 'Эффекты: ' + (settings.effects ? 'вкл.' : 'выкл.');
      effectsButton.setAttribute('aria-pressed', String(settings.effects));
      effectsButton.title = reduceMotion.matches && settings.effects
        ? 'Движение отключено системной настройкой уменьшения движения' : '';
    }
    if (transparencyButton){
      transparencyButton.textContent = settings.lowTransparency
        ? 'Прозрачность: уменьшена' : 'Уменьшить прозрачность';
      transparencyButton.setAttribute('aria-pressed', String(settings.lowTransparency));
    }
    syncRain();
  }

  function bindSetting(id, name){
    var button = document.getElementById(id);
    if (button) button.addEventListener('click', function(){
      settings[name] = !settings[name];
      save();
      updateSettings();
    });
  }
  bindSetting('effectsToggle', 'effects');
  bindSetting('transparencyToggle', 'lowTransparency');

  var context = null, drops = [], frame = 0, lastFrame = 0, width = 0, height = 0;
  if (canvas){
    try{ context = canvas.getContext('2d', { alpha:true }); }catch(e){}
  }

  function resize(){
    if (!context) return;
    var scale = Math.min(devicePixelRatio || 1, phone.matches ? 1 : 1.25);
    width = innerWidth;
    height = innerHeight;
    canvas.width = Math.ceil(width * scale);
    canvas.height = Math.ceil(height * scale);
    context.setTransform(scale, 0, 0, scale, 0, 0);
    drops = Array.from({ length:phone.matches ? 12 : 38 }, function(){
      return { x:Math.random() * width, y:Math.random() * height,
        length:5 + Math.random() * 17, speed:1.8 + Math.random() * 2.4,
        alpha:.08 + Math.random() * .15 };
    });
    syncRain();
  }

  function draw(now){
    frame = 0;
    if (!context || !settings.effects || reduceMotion.matches || document.hidden) return;
    if (now - lastFrame < 34){ frame = requestAnimationFrame(draw); return; }
    lastFrame = now;
    context.clearRect(0, 0, width, height);
    drops.forEach(function(drop){
      context.beginPath();
      context.moveTo(drop.x, drop.y);
      context.lineTo(drop.x - 2, drop.y + drop.length);
      context.strokeStyle = 'rgba(190, 214, 220, ' + drop.alpha + ')';
      context.stroke();
      drop.x -= .25;
      drop.y += drop.speed;
      if (drop.y > height + drop.length){
        drop.y = -drop.length;
        drop.x = Math.random() * width;
      }
    });
    frame = requestAnimationFrame(draw);
  }

  function syncRain(){
    if (!context) return;
    if (!settings.effects || reduceMotion.matches || document.hidden){
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      context.clearRect(0, 0, width, height);
    } else if (!frame) frame = requestAnimationFrame(draw);
  }

  if (canvas && context){
    addEventListener('resize', resize, { passive:true });
    document.addEventListener('visibilitychange', syncRain);
    if (reduceMotion.addEventListener) reduceMotion.addEventListener('change', syncRain);
    else reduceMotion.addListener(syncRain);
    resize();
  }
  updateSettings();
})();
