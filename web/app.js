(() => {
  'use strict';

  const DEFAULTS = {
    voiceMode: 'toggle',
    mappings: { voiceStart: ['ALT_RIGHT'], voiceStop: ['ALT_RIGHT'], confirm: ['ENTER'], cancel: ['ESC'] },
    appearance: {
      keyTheme: 'glass', keyStyle: 'soft', keyRadius: 22,
      keys: {
        voice: { icon: 'mic', iconData: null, color: '#d9eee7', glow: '#8ce9be' },
        confirm: { icon: 'check', iconData: null, color: '#e0ebf5', glow: '#83c9ff' },
        cancel: { icon: 'x', iconData: null, color: '#f3e4e3', glow: '#f5a39a' }
      }
    }
  };

  const ICONS = {
    mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3m-4 0h8"/></svg>',
    wave: '<svg viewBox="0 0 24 24"><path d="M3 10v4m4-8v12m4-15v18m4-14v10m4-13v16m3-11v6"/></svg>',
    record: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.3" fill="currentColor" stroke="none"/></svg>',
    check: '<svg viewBox="0 0 24 24" preserveAspectRatio="none"><path d="m4 12.5 5.2 5.2L20 6.8"/></svg>',
    enter: '<svg viewBox="0 0 24 24"><path d="M5 5v7h14M13 7l6 5-6 5"/></svg>',
    'check-circle': '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m7.5 12.5 3 3 6-7"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="m5 5 14 14M19 5 5 19"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="m14.5 5-7 7 7 7M8 12h12"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>'
  };
  const $ = (id) => document.getElementById(id);
  const storageKey = `shunyan_session_${encodeURIComponent(location.origin)}`;
  let token = localStorage.getItem(storageKey);
  let localViewer = false;
  let voiceActive = false;
  let held = false;
  let leaseTimer = 0;
  let statusTimer = 0;
  let statusBusy = false;
  let configRevision = null;
  let config = JSON.parse(JSON.stringify(DEFAULTS));
  let toastTimer = 0;
  let lastActionErrorAt = 0;
  let audioStreamPromise = null;
  let voiceOperation = 0;
  let voiceBusy = false;
  let holdStartPromise = null;
  let voiceActionPromise = null;
  let holdKeyDown = false;

  function toast(message, kind = '') {
    const region = $('toast-region');
    region.replaceChildren();
    const node = document.createElement('div');
    node.className = `toast ${kind}`.trim();
    node.textContent = message;
    region.append(node);
    requestAnimationFrame(() => node.classList.add('visible'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.classList.remove('visible'); setTimeout(() => node.remove(), 240); }, 2800);
  }

  async function request(path, options = {}, authenticated = true) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body) headers.set('Content-Type', 'application/json');
    if (authenticated && token) headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(`/api/v1${path}`, { ...options, headers, cache: 'no-store' });
    let body;
    try { body = await response.json(); } catch { throw new Error('本机服务返回了无法识别的数据'); }
    if (!response.ok || body?.ok === false) {
      const err = new Error(body?.error?.message || `连接失败（${response.status}）`);
      err.code = body?.error?.code; err.status = response.status; throw err;
    }
    return body?.data ?? body;
  }

  function safeImageData(dataUrl) {
    if (typeof dataUrl !== 'string') return false;
    const match = /^data:image\/(png|webp);base64,([a-z0-9+/]+={0,2})$/i.exec(dataUrl);
    if (!match || match[2].length > 350000) return false;
    try {
      const bytes = Uint8Array.from(atob(match[2]), (char) => char.charCodeAt(0));
      if (bytes.byteLength > 256 * 1024) return false;
      if (match[1].toLowerCase() === 'png') return bytes.length >= 8 && [137,80,78,71,13,10,26,10].every((n, i) => bytes[i] === n);
      return bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
    } catch { return false; }
  }

  function renderIcon(id, icon, iconData) {
    const node = $(id);
    if (!node) return;
    node.replaceChildren();
    if (safeImageData(iconData)) {
      const image = document.createElement('img'); image.src = iconData; image.alt = ''; image.decoding = 'async'; node.append(image); return;
    }
    node.innerHTML = Object.hasOwn(ICONS, icon) ? ICONS[icon] : ICONS.mic;
  }

  function applyAppearance() {
    const shell = document.querySelector('.device-shell');
    if (!shell) return;
    const look = config.appearance || DEFAULTS.appearance;
    shell.classList.add('reference-hardware');
    shell.classList.toggle('theme-recessed',look.keyTheme==='recessed');
    shell.classList.toggle('theme-matte-black',look.keyTheme==='matte-black');
    shell.dataset.keyTheme=look.keyTheme||'glass';
    const keyStyle = ['soft', 'glass', 'solid'].includes(look.keyStyle) ? look.keyStyle : 'soft';
    shell.classList.remove('style-soft', 'style-glass', 'style-solid'); shell.classList.add(`style-${keyStyle}`);
    shell.style.setProperty('--key-radius', `${Math.max(12, Math.min(34, Number(look.keyRadius) || 22))}px`);
    for (const key of ['voice', 'confirm', 'cancel']) {
      const value = look.keys?.[key] || DEFAULTS.appearance.keys[key];
      shell.style.setProperty(`--${key}-color`, /^#[0-9a-f]{6}$/i.test(value.color) ? value.color : DEFAULTS.appearance.keys[key].color);
      shell.style.setProperty(`--${key}-glow`, /^#[0-9a-f]{6}$/i.test(value.glow) ? value.glow : DEFAULTS.appearance.keys[key].glow);
      renderIcon(`${key}-icon`, value.icon, value.iconData);
      const original=DEFAULTS.appearance.keys[key];
      const reference=(!look.keyTheme||look.keyTheme==='glass') && keyStyle==='soft' && (Number(look.keyRadius)||22)===22 && String(value.color).toLowerCase()===original.color && String(value.glow).toLowerCase()===original.glow;
      document.querySelector('.'+key+'-key').classList.toggle('is-reference-key',reference);
      document.querySelector('.'+key+'-key').classList.toggle('has-custom-glyph',value.icon!==original.icon||Boolean(safeImageData(value.iconData)));

    }
  }

  // The controller must show its configurable default keycaps before pairing.
  // A saved host appearance replaces these defaults after a session is loaded.
  applyAppearance();

  function labelForState(state) {
    return ({ idle: '待命', working: '处理中', waiting: '需要确认', completed: '已完成', error: '遇到问题', offline: '状态离线', unknown: '状态未知' })[state] || '状态未知';
  }

  function renderStatus(status, connected) {
    const states = ['idle', 'working', 'waiting', 'completed', 'error', 'offline', 'unknown'];
    const state = connected && status && states.includes(status.state) ? status.state : (connected ? 'unknown' : 'offline');
    const title = connected && typeof status?.title === 'string' && status.title.length <= 24 ? status.title : labelForState(state);
    const detail = connected && typeof status?.detail === 'string' && status.detail.length <= 48 ? status.detail : (connected ? '尚无可验证的 Codex 状态事件' : '等待已配对主机反馈');
    $('screen-title').textContent = title;
    $('screen-detail').textContent = detail;
    $('screen-led').className = `screen-led screen-state-${state}`;
  }

  function newId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
      const random = globalThis.crypto?.getRandomValues ? crypto.getRandomValues(new Uint8Array(1))[0] & 15 : Math.floor(Math.random() * 16);
      return (char === 'x' ? random : (random & 3) | 8).toString(16);
    });
  }

  function ensurePhoneAudioStream() {
    if (window.PhoneAudioStream) return Promise.resolve(window.PhoneAudioStream);
    if (!audioStreamPromise) {
      audioStreamPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = new URL('./audio-stream.js', location.href).href;
        script.async = true;
        script.onload = () => {
          const stream = window.PhoneAudioStream;
          if (!stream) { reject(new Error('手机麦克风组件未能启动，请刷新页面后重试')); return; }
          stream.setErrorHandler((message) => {
            toast(message || '手机麦克风转接已中断', 'error');
            releaseAll(true);
          });
          resolve(stream);
        };
        script.onerror = () => reject(new Error('手机麦克风组件加载失败，请检查网络后刷新页面'));
        document.head.append(script);
      }).catch((error) => {
        audioStreamPromise = null;
        throw error;
      });
    }
    return audioStreamPromise;
  }

  function stopPhoneAudio(keepalive = false) {
    const stream = window.PhoneAudioStream;
    return stream ? stream.stop(keepalive).catch(() => {}) : Promise.resolve();
  }

  async function send(button, phase, keepalive = false) {
    if (!token) { if (phase === 'tap') toast('请先在设置页连接电脑服务', 'error'); return null; }
    try {
      return await request('/control', { method: 'POST', body: JSON.stringify({ requestId: newId(), button, phase }), keepalive });
    } catch (error) {
      const now = Date.now();
      if (document.visibilityState === 'visible' && now - lastActionErrorAt > 2200) {
        toast(error.message || `按键${phase}发送失败`, 'error'); lastActionErrorAt = now;
      }
      if (error.status === 401) { localStorage.removeItem(storageKey); token = null; }
      return null;
    }
  }

  async function releaseAll(keepalive = false) {
    clearPhysical();
    clearInterval(leaseTimer); leaseTimer = 0;
    voiceOperation += 1;
    const releaseRequest = token
      ? request('/control/release-all', { method: 'POST', body: JSON.stringify({ requestId: newId() }), keepalive }).catch(() => {})
      : Promise.resolve();
    const audioStop = stopPhoneAudio(keepalive);
    held = false; voiceActive = false;
    holdKeyDown = false;
    $('voice-key')?.classList.remove('is-active', 'visual-pressed');
    $('voice-key')?.setAttribute('aria-pressed', 'false');
    await Promise.all([releaseRequest, audioStop]);
  }

  async function refreshStatus() {
    if ((!token && !localViewer) || document.hidden || !navigator.onLine) {
      renderStatus(null, false);
      if ((!navigator.onLine || !token) && (held || voiceActive || window.PhoneAudioStream?.active)) releaseAll(true);
      return;
    }
    if (statusBusy) return;
    statusBusy = true;
    try {
      const data = await request(localViewer?'/local-status':'/status',{},!localViewer);
      const status = data.codex || data.status || data;
      if(typeof data.configRevision==='number'){
        if(configRevision!==data.configRevision && !held && !voiceBusy && !voiceActive){if(await loadSettings())configRevision=data.configRevision;}
      }
      renderStatus(status, true);
      if (typeof data.voiceActive === 'boolean') {
        voiceActive = data.voiceActive;
        $('voice-key').classList.toggle('is-active', voiceActive || held);
        $('voice-key').classList.toggle('visual-pressed', voiceActive || held);
        $('voice-key').setAttribute('aria-pressed', String(voiceActive || held));
        if (config.voiceMode === 'toggle' && !voiceActive && window.PhoneAudioStream?.active && !voiceBusy) {
          stopPhoneAudio(true);
        }
      }
    } catch {
      renderStatus(null, false);
      if (navigator.onLine) await releaseAll(true);
      else {
        clearInterval(leaseTimer); leaseTimer = 0; held = false; voiceActive = false;
        $('voice-key').classList.remove('is-active', 'visual-pressed'); $('voice-key').setAttribute('aria-pressed', 'false');
      }
    } finally {
      statusBusy = false;
    }
  }

  async function loadSettings() {
    if (!token && !localViewer) return false;
    try {
      const data = await request(localViewer?'/local-config':'/config',{},!localViewer);
      config = JSON.parse(JSON.stringify(data.config || data || DEFAULTS));
      applyAppearance();
      return true;
    } catch { return false; /* 暂用安全外观，下次状态轮询重试 */ }
  }

  async function startToggleVoice() {
    if (voiceBusy) return;
    voiceBusy = true;
    const operation = ++voiceOperation;
    let stream = null;
    try {
      stream = await ensurePhoneAudioStream();
      await stream.start();
      if (operation !== voiceOperation) { await stream.stop(); return; }
      const result = await send('voice', 'tap');
      if (operation !== voiceOperation) {
        if (result?.voiceActive || !result) await releaseAll(true);
        else await stream.stop();
        return;
      }
      if (!result || typeof result.voiceActive !== 'boolean') {
        await releaseAll(true);
        if (result) toast('主机未返回语音按键状态，请检查服务版本', 'error');
        return;
      }
      voiceActive = result.voiceActive;
      if (!voiceActive) { await stream.stop(); return; }
      stream.resume();
      $('voice-key').classList.add('is-active', 'visual-pressed');
      $('voice-key').setAttribute('aria-pressed', 'true');
      toast('手机麦克风已接入语音输入', 'success');
    } catch (error) {
      if (operation === voiceOperation) toast(error.message || '手机麦克风启动失败', 'error');
      await releaseAll(true);
    } finally { voiceBusy = false; }
  }

  async function stopToggleVoice() {
    if (voiceBusy) return;
    voiceBusy = true;
    const operation = ++voiceOperation;
    try {
      const result = await send('voice', 'tap');
      if (!result || typeof result.voiceActive !== 'boolean') { await releaseAll(true); return; }
      voiceActive = result.voiceActive;
      if (operation !== voiceOperation) { await releaseAll(true); return; }
      if (voiceActive) {
        $('voice-key').classList.add('is-active', 'visual-pressed');
        $('voice-key').setAttribute('aria-pressed', 'true');
        toast('主机仍处于语音输入状态，请再按一次停止', 'error');
        return;
      }
      $('voice-key').classList.remove('is-active', 'visual-pressed');
      $('voice-key').setAttribute('aria-pressed', 'false');
      await stopPhoneAudio();
      toast('语音输入已停止', 'success');
    } finally { voiceBusy = false; }
  }

  async function clickVoice() {
    if(localViewer && !token){toast('电脑预览模式，请使用已配对手机触发按键');return;}
    if (config.voiceMode === 'hold') return;
    if (voiceBusy) {
      // A second tap while permission or host startup is pending cancels that start.
      voiceOperation += 1;
      return;
    }
    const action = voiceActive ? stopToggleVoice() : startToggleVoice();
    voiceActionPromise = action;
    try { await action; }
    finally { if (voiceActionPromise === action) voiceActionPromise = null; }
  }

  async function startHold(event) {
    if(localViewer && !token)return;
    if (config.voiceMode !== 'hold' || event.button !== 0 || held || voiceBusy) return;
    event.preventDefault(); held = true;
    voiceBusy = true;
    const operation = ++voiceOperation;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* 某些 WebView 不支持捕获 */ }
    $('voice-key').classList.add('is-active', 'visual-pressed'); $('voice-key').setAttribute('aria-pressed', 'true');
    holdStartPromise = (async () => {
      try {
        const stream = await ensurePhoneAudioStream();
        await stream.start();
        if (operation !== voiceOperation || !held) { await stream.stop(); return; }
        const pressed = await send('voice', 'press');
        if (!pressed) { await releaseAll(true); return; }
        holdKeyDown = true;
        if (operation !== voiceOperation || !held) {
          const released = await send('voice', 'release');
          holdKeyDown = false;
          if (!released) await releaseAll(true);
          else await stream.stop();
          return;
        }
        stream.resume();
        leaseTimer = setInterval(async () => {
          if (held && !(await send('voice', 'press'))) await releaseAll(true);
        }, 1000);
      } catch (error) {
        if (operation === voiceOperation) toast(error.message || '手机麦克风启动失败', 'error');
        await releaseAll(true);
      }
    })().finally(() => { holdStartPromise = null; voiceBusy = false; });
    await holdStartPromise;
  }

  async function endHold(event) {
    if (!held && !holdKeyDown && !holdStartPromise) return;
    held = false; clearInterval(leaseTimer); leaseTimer = 0;
    try { if (event?.pointerId && $('voice-key').hasPointerCapture(event.pointerId)) $('voice-key').releasePointerCapture(event.pointerId); } catch { /* ignore */ }
    const starting = holdStartPromise;
    if (starting) {
      voiceOperation += 1;
      await starting;
    }
    if (holdKeyDown) {
      const released = await send('voice', 'release');
      holdKeyDown = false;
      if (!released) { await releaseAll(true); return; }
    }
    await stopPhoneAudio();
    $('voice-key').classList.remove('is-active', 'visual-pressed'); $('voice-key').setAttribute('aria-pressed', 'false');
  }

  async function stopVoiceBeforeAction() {
    const pending = holdStartPromise || voiceActionPromise;
    const cleanup = releaseAll();
    if (pending) await pending;
    await cleanup;
  }


  // Only visual feedback is shared; mapped keyboard/audio control stays below.
  const physicalFeedback=window.PhysicalKeyFeedback.attach(['voice','confirm','cancel'].map(name=>$(name+'-key')));
  function clearPhysical(){physicalFeedback.clear('release-all');}
  $('voice-key').addEventListener('pointerdown', startHold);
  $('voice-key').addEventListener('pointerup', endHold);
  $('voice-key').addEventListener('pointercancel', endHold);
  $('voice-key').addEventListener('lostpointercapture', () => { if (held) endHold(); });
  $('voice-key').addEventListener('click', clickVoice);
  $('confirm-key').addEventListener('click', async () => {
    if (held || voiceActive || voiceBusy || window.PhoneAudioStream?.active) await stopVoiceBeforeAction();
    await send('confirm', 'tap');
  });
  $('cancel-key').addEventListener('click', async () => {
    await stopVoiceBeforeAction();
    await send('cancel', 'tap');
  });

  document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(true); else refreshStatus(); });
  window.addEventListener('pagehide', () => releaseAll(true));
  window.addEventListener('online', refreshStatus);
  window.addEventListener('offline', () => { renderStatus(null, false); releaseAll(true); });
  window.addEventListener('storage', (event) => {
    if (event.key !== storageKey) return;
    if (!event.newValue) {
      releaseAll(true);
      token = null; renderStatus(null, false); clearInterval(statusTimer); statusTimer = 0; return;
    }
    token = event.newValue;
    if (!statusTimer) statusTimer = setInterval(refreshStatus, 2000);
    loadSettings().then(refreshStatus);
  });

  (async () => {
    try{const info=await request('/connection-info',{},false);localViewer=info.isLocalHost===true;}catch{ /* 配对手机仍可使用认证接口 */ }
    if (!token && !localViewer) { renderStatus(null, false); return; }
    statusTimer = setInterval(refreshStatus, 2000);
    try { await request('/health', {}, false); await loadSettings(); await refreshStatus(); }
    catch { renderStatus(null, false); }
  })();

  // Warm the same-origin audio module before the first user gesture so Safari can
  // resume AudioContext while the voice button still has a trusted activation.
  ensurePhoneAudioStream().catch(() => {});

  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('./service-worker.js').catch(() => {});
})();
