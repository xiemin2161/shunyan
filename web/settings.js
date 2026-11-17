(() => {
  'use strict';

  const DEFAULTS = {
    voiceMode: 'toggle',
    mappings: { voiceStart: ['ALT_RIGHT'], voiceStop: ['ALT_RIGHT'], confirm: ['ENTER'], cancel: ['ESC'] },
    audio: { outputDeviceId: null },
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
  const VALID_KEYS = new Set([
    'ALT_LEFT', 'ALT_RIGHT', 'CTRL_LEFT', 'CTRL_RIGHT', 'SHIFT_LEFT', 'SHIFT_RIGHT', 'META_LEFT', 'META_RIGHT',
    'ENTER', 'ESC', 'SPACE', 'TAB', 'BACKSPACE', 'DELETE', 'HOME', 'END', 'PAGE_UP', 'PAGE_DOWN', 'UP', 'DOWN', 'LEFT', 'RIGHT',
    ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split(''), ...'0123456789'.split(''), ...Array.from({ length: 12 }, (_, i) => `F${i + 1}`)
  ]);
  const MODIFIERS = new Set(['ALT_LEFT', 'ALT_RIGHT', 'CTRL_LEFT', 'CTRL_RIGHT', 'SHIFT_LEFT', 'SHIFT_RIGHT', 'META_LEFT', 'META_RIGHT']);
  const $ = (id) => document.getElementById(id);
  const sessionKey = `shunyan_session_${encodeURIComponent(location.origin)}`;
  const savedDraftKey = `shunyan_settings_draft_${encodeURIComponent(location.origin)}`;
  const clientIdKey = `shunyan_device_${encodeURIComponent(location.origin)}`;
  let token = localStorage.getItem(sessionKey);
  let config = JSON.parse(JSON.stringify(DEFAULTS));
  let hostConfig = JSON.parse(JSON.stringify(DEFAULTS));
  let audioDevices = [];
  let toastTimer = 0;
  let localAdmin = false;

  $('pairing-page-link').href = `${location.origin}/pairing.html`;

  function deepMerge(base, value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return base;
    const result = JSON.parse(JSON.stringify(base));
    for (const [key, entry] of Object.entries(value)) {
      if (entry && typeof entry === 'object' && !Array.isArray(entry) && result[key] && typeof result[key] === 'object') result[key] = deepMerge(result[key], entry);
      else result[key] = entry;
    }
    return result;
  }

  function toast(message, kind = '') {
    const region = $('toast-region'); region.replaceChildren();
    const node = document.createElement('div'); node.className = `toast ${kind}`.trim(); node.textContent = message; region.append(node);
    requestAnimationFrame(() => node.classList.add('visible'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.classList.remove('visible'); setTimeout(() => node.remove(), 240); }, 3000);
  }

  document.addEventListener('shunyan-shortcut-feedback',event=>{
    const {message,type}=event.detail||{};
    if(typeof message==='string')toast(message,type==='error'?'error':type==='success'?'success':'');
  });

  async function request(path, options = {}, authenticated = true) {
    const headers = new Headers(options.headers || {}); headers.set('Accept', 'application/json');
    headers.set('X-Shunyan-Device', window.ShunyanConnection.deviceHint());
    if (options.body) headers.set('Content-Type', 'application/json');
    if (authenticated && token) headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(`/api/v1${path}`, { ...options, headers, cache: 'no-store' });
    let body; try { body = await response.json(); } catch { throw new Error('本机服务返回了无法识别的数据'); }
    if (!response.ok || body?.ok === false) {
      const err = new Error(body?.error?.message || `连接失败（${response.status}）`); err.status = response.status; err.code = body?.error?.code; throw err;
    }
    return body?.data ?? body;
  }

  function validAudioDeviceId(value) {
    return typeof value === 'string' && /^[a-f0-9]{24}$/.test(value) ? value : null;
  }

  function paintAudioDevices(devices, selectedId, emptyLabel) {
    const select = $('audio-output-device');
    select.replaceChildren(new Option(emptyLabel, ''));
    audioDevices = devices;
    for (const device of devices) {
      if (!validAudioDeviceId(device.id) || typeof device.name !== 'string' || !device.name.trim()) continue;
      const source = typeof device.hostApi === 'string' && device.hostApi ? ` · ${device.hostApi}` : '';
      select.add(new Option(`${device.name.trim()}${source}`, device.id));
    }
    if (selectedId && ![...select.options].some((option) => option.value === selectedId)) {
      select.add(new Option('已保存设备（系统当前未列出）', selectedId));
    }
    select.value = selectedId || '';
    select.disabled = (!token && !localAdmin) || devices.length === 0;
    $('refresh-audio-devices').disabled = !token && !localAdmin;
    $('save-audio-device').disabled = (!token && !localAdmin) || devices.length === 0;
  }

  async function loadAudioDevices({ notifyError = true } = {}) {
    const requestedToken=token;
    if (!token && !localAdmin) {
      paintAudioDevices([], validAudioDeviceId(config.audio?.outputDeviceId), '连接电脑后读取可用设备');
      return false;
    }
    const select = $('audio-output-device');
    select.disabled = true;
    $('refresh-audio-devices').disabled = true;
    $('save-audio-device').disabled = true;
    try {
      const data = await request(localAdmin?'/local-audio/devices':'/audio/devices',{},!localAdmin);
      if(token!==requestedToken) return false;
      if (!Array.isArray(data.devices)) throw new Error('电脑服务没有返回音频设备列表');
      const devices = data.devices.filter((device) => validAudioDeviceId(device?.id) && typeof device?.name === 'string' && device.name.trim());
      const selectedId = Object.hasOwn(data, 'selectedDeviceId')
        ? validAudioDeviceId(data.selectedDeviceId)
        : validAudioDeviceId(config.audio?.outputDeviceId);
      config.audio.outputDeviceId = selectedId;
      hostConfig.audio.outputDeviceId = selectedId;
      const emptyLabel = devices.length ? '请选择电脑端虚拟音频播放设备' : '电脑当前没有可用的音频播放设备';
      paintAudioDevices(devices, selectedId, emptyLabel);
      return true;
    } catch (error) {
      if(token!==requestedToken) return false;
      paintAudioDevices([], validAudioDeviceId(config.audio?.outputDeviceId), '暂时无法读取设备，请刷新重试');
      if (notifyError) toast(`读取电脑音频设备失败：${error.message || '请检查本机服务'}`, 'error');
      return false;
    }
  }

  function safeImageData(dataUrl) {
    if (typeof dataUrl !== 'string') return false;
    const match = /^data:image\/(png|webp);base64,([a-z0-9+/]+={0,2})$/i.exec(dataUrl);
    if (!match || match[2].length > 350000) return false;
    try {
      const bytes = Uint8Array.from(atob(match[2]), (char) => char.charCodeAt(0));
      if (bytes.byteLength > 256 * 1024) return false;
      if (match[1].toLowerCase() === 'png') return bytes.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((n, i) => bytes[i] === n);
      return bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
    } catch { return false; }
  }

  function renderIcon(node, value) {
    node.replaceChildren();
    if (safeImageData(value.iconData)) {
      const image = document.createElement('img'); image.src = value.iconData; image.alt = ''; image.decoding = 'async'; node.append(image); return;
    }
    node.innerHTML = Object.hasOwn(ICONS, value.icon) ? ICONS[value.icon] : ICONS.mic;
  }

  function updatePreview() {
    const shell = $('settings-device-preview'); const appearance = config.appearance;
    shell.classList.add('reference-hardware');
    shell.classList.toggle('theme-recessed',appearance.keyTheme==='recessed');
    shell.classList.toggle('theme-matte-black',appearance.keyTheme==='matte-black');
    shell.dataset.keyTheme=appearance.keyTheme||'glass';
    shell.classList.remove('style-soft', 'style-glass', 'style-solid');
    shell.classList.add(`style-${['soft', 'glass', 'solid'].includes(appearance.keyStyle) ? appearance.keyStyle : 'soft'}`);
    shell.style.setProperty('--key-radius', `${Math.max(12, Math.min(34, Number(appearance.keyRadius) || 22))}px`);
    for (const key of ['voice', 'confirm', 'cancel']) {
      const item = appearance.keys[key];
      shell.style.setProperty(`--${key}-color`, /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : DEFAULTS.appearance.keys[key].color);
      shell.style.setProperty(`--${key}-glow`, /^#[0-9a-f]{6}$/i.test(item.glow) ? item.glow : DEFAULTS.appearance.keys[key].glow);
      renderIcon($(`preview-${key}-icon`), item);
      const original=DEFAULTS.appearance.keys[key];
      const referenceCompatible=appearance.keyStyle==='soft' && Number(appearance.keyRadius)===22 && String(item.color).toLowerCase()===original.color && String(item.glow).toLowerCase()===original.glow;
      const reference=appearance.keyTheme==='glass' && referenceCompatible;
      shell.querySelector('.'+key+'-key').dataset.referenceCompatible=String(referenceCompatible);
      shell.querySelector('.'+key+'-key').classList.toggle('is-reference-key',reference);
      shell.querySelector('.'+key+'-key').classList.toggle('has-custom-glyph',item.icon!==original.icon||Boolean(safeImageData(item.iconData)));

      $(`icon-state-${key}`).textContent = safeImageData(item.iconData) ? '已选自定义图片' : '内置图标';
      $(`clear-icon-${key}`).hidden = !safeImageData(item.iconData);
      $(`upload-${key}`).value = '';
    }
    document.dispatchEvent(new CustomEvent('shunyan-appearance-updated'));
  }

  function configFromForm() {
    const voiceMode = document.querySelector('input[name="voice-mode"]:checked')?.value || 'toggle';
    const parseMapping = (id, label) => {
      const keys = $(id).value.trim().toUpperCase().split('+').map((part) => part.trim().replace(/^ARROW_/,''));
      if (!keys.length || keys.length > 5 || keys.some((key) => !VALID_KEYS.has(key)) || new Set(keys).size !== keys.length || keys.slice(0, -1).some((key) => !MODIFIERS.has(key))) {
        throw new Error(`${label}快捷键格式无效：修饰键用 + 连接，并把主键放在最后`);
      }
      return keys;
    };
    const voiceMapping = parseMapping('mapping-voice-start', '语音按键');
    return {
      voiceMode,
      audio: { outputDeviceId: validAudioDeviceId($('audio-output-device').value) },
      mappings: {
        voiceStart: [...voiceMapping], voiceStop: [...voiceMapping],
        confirm: parseMapping('mapping-confirm', '确认'), cancel: parseMapping('mapping-cancel', '取消')
      },
      appearance: {
        keyTheme: $('key-theme').value,
        keyStyle: 'soft',
        keyRadius: 22,
        keys: Object.fromEntries(['voice', 'confirm', 'cancel'].map((key) => {
          const icon = $('icon-' + key).value;
          const iconData = safeImageData(config.appearance.keys[key].iconData) ? config.appearance.keys[key].iconData : null;
          if (icon === 'custom' && !iconData) throw new Error('请选择内置图标或上传有效的 PNG/WebP 图片');
          return [key, { icon, iconData, color: $('color-' + key).value, glow: $('glow-' + key).value }];
        }))
      }
    };
  }

  function fillForm(value) {
    config = deepMerge(DEFAULTS, value);
    config.voiceMode = config.voiceMode === 'hold' ? 'hold' : 'toggle';
    config.audio.outputDeviceId = validAudioDeviceId(config.audio.outputDeviceId);
    config.appearance.keyStyle = 'soft';
    config.appearance.keyTheme = ['glass','recessed','matte-black'].includes(config.appearance.keyTheme)?config.appearance.keyTheme:'glass';
    $('key-theme').value=config.appearance.keyTheme;
    config.appearance.keyRadius = 22;
    document.querySelector(`input[name="voice-mode"][value="${config.voiceMode}"]`).checked = true;
    const voiceMapping = Array.isArray(config.mappings.voiceStart)
      ? config.mappings.voiceStart
      : Array.isArray(config.mappings.voiceStop) ? config.mappings.voiceStop : ['ALT_RIGHT'];
    $('mapping-voice-start').value = voiceMapping.join('+');
    $('mapping-confirm').value = Array.isArray(config.mappings.confirm) ? config.mappings.confirm.join('+') : 'ENTER';
    $('mapping-cancel').value = Array.isArray(config.mappings.cancel) ? config.mappings.cancel.join('+') : 'ESC';
    const audioSelect = $('audio-output-device');
    const savedDeviceId = config.audio.outputDeviceId;
    if (savedDeviceId && ![...audioSelect.options].some((option) => option.value === savedDeviceId)) {
      audioSelect.add(new Option('已保存设备（等待主机设备列表）', savedDeviceId));
    }
    audioSelect.value = savedDeviceId || '';
    for (const key of ['voice', 'confirm', 'cancel']) {
      const item = config.appearance.keys[key];
      item.color = /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : DEFAULTS.appearance.keys[key].color;
      item.glow = /^#[0-9a-f]{6}$/i.test(item.glow) ? item.glow : DEFAULTS.appearance.keys[key].glow;
      if (!safeImageData(item.iconData)) item.iconData = null;
      item.icon = item.iconData ? 'custom' : (Object.hasOwn(ICONS, item.icon) ? item.icon : DEFAULTS.appearance.keys[key].icon);
      $(`icon-${key}`).value = item.icon; $(`color-${key}`).value = item.color; $(`glow-${key}`).value = item.glow;
    }
    updatePreview();
  }

  function saveDraft() {
    try { config = configFromForm(); localStorage.setItem(savedDraftKey, JSON.stringify(config)); updatePreview(); return true; }
    catch (error) { toast(error.message, 'error'); return false; }
  }

  function setConnection(connected, text = connected ? '已连接' : '未连接') {
    const pill = $('connection-pill'); pill.classList.toggle('connected', connected); pill.querySelector('span').textContent = text;
    $('disconnect-button').hidden = !token;
  }
  document.addEventListener('shunyan-session-invalid',()=>{
    localStorage.removeItem(sessionKey);
    token=null;setConnection(false,'请重新配对');
    paintAudioDevices([],null,'连接电脑后读取可用设备');
    $('refresh-audio-devices').disabled=true;$('save-audio-device').disabled=true;
  });
  async function loadLocalConfig(){
    if(!localAdmin)return;
    try{
      const saved=await request('/local-config',{},false);
      hostConfig=deepMerge(DEFAULTS,saved);
      if(!localStorage.getItem(savedDraftKey))fillForm(hostConfig);
      await loadAudioDevices({notifyError:false});
    }catch{toast('无法读取电脑设置，请刷新重试','error');}
  }
  document.addEventListener('shunyan-host-context',event=>{localAdmin=event.detail?.isLocalHost===true;if(localAdmin)loadLocalConfig();});
  document.addEventListener('shunyan-config-revision',()=>{if(localAdmin)loadLocalConfig();});

  async function checkHost() {
    const checkedToken=token;
    $('host-origin').value = location.origin;
    try {
      await request('/health', {}, false);
      if(token!==checkedToken) return;
      if (!token) { setConnection(false, '待配对'); return; }
      const data = await request('/config');
      if(token!==checkedToken) return;
      const saved = data.config || data;
      if (saved && typeof saved === 'object') {
        hostConfig = deepMerge(DEFAULTS, saved);
        fillForm(hostConfig);
        localStorage.removeItem(savedDraftKey);
      }
      await loadAudioDevices();
      if(token!==checkedToken) return;
      setConnection(true);
    } catch (error) {
      if(token!==checkedToken) return;
      setConnection(false, error.status === 401 ? '待配对' : '服务离线');
      if (error.status === 401) { token = null; localStorage.removeItem(sessionKey); }
      const draft = localStorage.getItem(savedDraftKey);
      if (draft) { try { fillForm(JSON.parse(draft)); } catch { localStorage.removeItem(savedDraftKey); } }
      await loadAudioDevices({ notifyError: false });
    }
  }

  function newId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
      const random = globalThis.crypto?.getRandomValues ? crypto.getRandomValues(new Uint8Array(1))[0] & 15 : Math.floor(Math.random() * 16);
      return (char === 'x' ? random : (random & 3) | 8).toString(16);
    });
  }

  async function pairWithCode(pairCode) {
    if (!pairCode) { toast('请输入电脑上显示的一次性配对码', 'error'); $('pair-code').focus(); return; }
    const button = $('pair-button'); button.disabled = true; button.textContent = '连接中…';
    try {
      let clientDeviceId=localStorage.getItem(clientIdKey);
      if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(clientDeviceId||'')){clientDeviceId=newId();localStorage.setItem(clientIdKey,clientDeviceId);}
      const result = await request('/pair', { method: 'POST', headers:token?{Authorization:`Bearer ${token}`}:{}, body: JSON.stringify({ pairCode,clientDeviceId }) }, false);
      if (typeof result.sessionToken !== 'string' || !result.sessionToken) throw new Error('配对响应缺少会话令牌');
      token = result.sessionToken; localStorage.setItem(sessionKey, token); $('pair-code').value = '';
      const data = await request('/config');
      hostConfig = deepMerge(DEFAULTS, data.config || data || DEFAULTS);
      fillForm(hostConfig);
      localStorage.removeItem(savedDraftKey);
      const audioDevicesLoaded = await loadAudioDevices({ notifyError: false });
      setConnection(true);
      toast(audioDevicesLoaded ? '电脑已连接' : '电脑已连接，但音频设备列表读取失败，请在设备栏刷新', audioDevicesLoaded ? 'success' : 'error');
    } catch (error) { toast(error.message || '配对失败，请确认配对码仍有效', 'error'); }
    finally { button.disabled = false; button.textContent = '连接'; }
  }
  $('pair-button').addEventListener('click', () => pairWithCode($('pair-code').value.trim()));

  $('disconnect-button').addEventListener('click', async () => {
    const button=$('disconnect-button');button.disabled=true;
    try{
      await request('/devices/disconnect',{method:'POST',body:'{}'});
      document.dispatchEvent(new CustomEvent('shunyan-session-invalid'));
      toast('本设备已断开，历史记录已保留','success');
    }catch(error){toast(error.message||'无法断开设备，请重试','error');}
    finally{button.disabled=false;}
  });

  $('save-button').addEventListener('click', async () => {
    if (!saveDraft()) return;
    if (!token && !localAdmin) { toast('设置预览已保存在此浏览器；连接电脑后再保存到主机', 'error'); return; }
    const button = $('save-button'); button.disabled = true;
    try {
      const form = configFromForm();
      const saved = document.body.dataset.settingsModule==='device'
        ? {...hostConfig,appearance:form.appearance}
        : {...hostConfig,voiceMode:form.voiceMode,mappings:form.mappings,audio:form.audio};
      await request(localAdmin?'/local-config':'/config', { method: 'PUT', body: JSON.stringify({ requestId: newId(), config: saved }) },!localAdmin);
      config = form; hostConfig = JSON.parse(JSON.stringify(saved));
      if(JSON.stringify(form)===JSON.stringify(saved))localStorage.removeItem(savedDraftKey);
      else localStorage.setItem(savedDraftKey,JSON.stringify(form));
      toast('设置已保存到电脑', 'success');
    } catch (error) { toast(error.message || '保存失败', 'error'); }
    finally { button.disabled = false; }
  });

  $('reset-button').addEventListener('click', () => {
    const restored=document.body.dataset.settingsModule==='device'?{...config,appearance:JSON.parse(JSON.stringify(DEFAULTS.appearance))}:{...JSON.parse(JSON.stringify(DEFAULTS)),appearance:config.appearance};
    fillForm(restored);localStorage.setItem(savedDraftKey,JSON.stringify(config));toast('已恢复本模块默认预览，保存后应用','success');
  });

  $('audio-output-device').addEventListener('change', () => saveDraft());
  $('refresh-audio-devices').addEventListener('click', () => loadAudioDevices());
  $('save-audio-device').addEventListener('click', async () => {
    if (!token && !localAdmin) { toast('请先连接电脑服务，再保存音频设备', 'error'); return; }
    const selectedId = validAudioDeviceId($('audio-output-device').value);
    if (selectedId && !audioDevices.some((device) => device.id === selectedId)) {
      toast('当前保存的设备未出现在系统列表中，请刷新后重新选择', 'error');
      return;
    }
    const button = $('save-audio-device'); button.disabled = true;
    const saved = JSON.parse(JSON.stringify(hostConfig));
    saved.audio = { outputDeviceId: selectedId };
    try {
      await request(localAdmin?'/local-config':'/config', { method: 'PUT', body: JSON.stringify({ requestId: newId(), config: saved }) },!localAdmin);
      hostConfig = saved;
      config.audio.outputDeviceId = selectedId;
      const draft = localStorage.getItem(savedDraftKey);
      if (draft) {
        try {
          const savedDraft = JSON.parse(draft);
          savedDraft.audio = { outputDeviceId: selectedId };
          localStorage.setItem(savedDraftKey, JSON.stringify(savedDraft));
        } catch { localStorage.removeItem(savedDraftKey); }
      }
      toast(selectedId ? '音频设备已保存到电脑' : '已清除电脑端音频设备选择', 'success');
    } catch (error) { toast(error.message || '保存音频设备失败', 'error'); }
    finally { button.disabled = (!token && !localAdmin) || audioDevices.length === 0; }
  });

  async function uploadIcon(key, file) {
    if (!file) return;
    if (file.size > 256 * 1024) { toast('图标文件不能超过 256 KiB', 'error'); $(`upload-${key}`).value = ''; return; }
    if (file.type !== 'image/png' && file.type !== 'image/webp') { toast('只支持 PNG 或 WebP 图片', 'error'); $(`upload-${key}`).value = ''; return; }
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const validPng = file.type === 'image/png' && bytes.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value);
      const validWebp = file.type === 'image/webp' && bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
      if (!validPng && !validWebp) throw new Error('图片文件内容与格式不匹配');
      let binary = ''; for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      const dataUrl = `data:${file.type};base64,${btoa(binary)}`;
      if (!safeImageData(dataUrl)) throw new Error('图片格式无效或文件过大');
      if (!saveDraft()) return;
      config.appearance.keys[key].iconData = dataUrl;
      config.appearance.keys[key].icon = 'custom';
      $(`icon-${key}`).value = 'custom';
      localStorage.setItem(savedDraftKey, JSON.stringify(config)); updatePreview();
      toast('自定义图标已加入预览；保存后同步到电脑', 'success');
    } catch (error) { toast(error.message || '无法读取图标文件', 'error'); $(`upload-${key}`).value = ''; }
  }

  for (const key of ['voice', 'confirm', 'cancel']) {
    $(`upload-${key}`).addEventListener('change', (event) => uploadIcon(key, event.currentTarget.files?.[0]));
    $(`clear-icon-${key}`).addEventListener('click', () => {
      config.appearance.keys[key].iconData = null;
      if ($(`icon-${key}`).value === 'custom') $(`icon-${key}`).value = DEFAULTS.appearance.keys[key].icon;
      saveDraft(); toast('已移除自定义图片', 'success');
    });
    $(`icon-${key}`).addEventListener('change', () => { config.appearance.keys[key].iconData = null; saveDraft(); });
  }
  for (const id of ['key-theme', 'icon-voice', 'icon-confirm', 'icon-cancel', 'color-voice', 'color-confirm', 'color-cancel', 'glow-voice', 'glow-confirm', 'glow-cancel']) $(id).addEventListener('input', saveDraft);
  for (const id of ['mapping-voice-start', 'mapping-confirm', 'mapping-cancel']) $(id).addEventListener('change', saveDraft);
  const manualDialog = $('shortcut-manual-dialog');
  const manualKey = $('shortcut-manual-key');
  const labels = { ALT_LEFT:'左 Alt / Option', ALT_RIGHT:'右 Alt / Option', CTRL_LEFT:'左 Ctrl', CTRL_RIGHT:'右 Ctrl', SHIFT_LEFT:'左 Shift', SHIFT_RIGHT:'右 Shift', META_LEFT:'左 Command / Win', META_RIGHT:'右 Command / Win', SPACE:'空格', BACKSPACE:'退格', PAGE_UP:'Page Up', PAGE_DOWN:'Page Down', UP:'方向上', DOWN:'方向下', LEFT:'方向左', RIGHT:'方向右' };
  manualKey.add(new Option('请选择按键', ''));
  for (const key of VALID_KEYS) manualKey.add(new Option(labels[key] || key, key));
  const modifierBox = $('shortcut-manual-modifiers');
  for (const key of MODIFIERS) {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = key;
    label.append(input, document.createTextNode(labels[key])); modifierBox.append(label);
  }
  let manualTarget = null;
  document.addEventListener('shunyan-shortcut-manual', (event) => {
    const input = $(event.detail?.inputId);
    if (!input || manualDialog.open) return;
    manualTarget = input;
    modifierBox.querySelectorAll('input').forEach((item) => { item.checked = false; });
    manualKey.value = '';
    manualDialog.showModal();
  });
  $('shortcut-manual-cancel').addEventListener('click', () => manualDialog.close());
  $('shortcut-manual-confirm').addEventListener('click', () => {
    let main = manualKey.value;
    let modifiers = [...modifierBox.querySelectorAll('input:checked')].map((item) => item.value);
    if (MODIFIERS.has(main)) modifiers = modifiers.filter((key) => key !== main);
    if (!main && modifiers.length === 1) main = modifiers.pop();
    if (!main) { toast('请选择一个按键；组合键还需选择普通按键', 'error'); return; }
    if (modifiers.length > 4 || (modifiers.length && MODIFIERS.has(main))) {
      toast('组合键最多包含四个修饰键，并须选择一个普通按键', 'error'); return;
    }
    manualDialog.close();
    window.ShunyanShortcutRecorder.setMapping(manualTarget, [...modifiers, main].join('+'));
    toast('按键已选择，请点击保存设置', 'success');
  });
  document.querySelectorAll('input[name="voice-mode"]').forEach((input) => input.addEventListener('change', saveDraft));

  function bestEffortRelease() {
    if (!token) return;
    fetch('/api/v1/control/release-all', {
      method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ requestId: newId() })
    }).catch(() => {});
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) bestEffortRelease(); });
  window.addEventListener('pagehide', bestEffortRelease);

  const draft = localStorage.getItem(savedDraftKey);
  if (draft) { try { fillForm(JSON.parse(draft)); } catch { localStorage.removeItem(savedDraftKey); fillForm(DEFAULTS); } }
  else fillForm(DEFAULTS);
  window.ShunyanCodexComponent.init({toast});
  window.PreviewCustomizer.init({icons:ICONS});
  window.PairedDevices.init({toast,request:(path,options={})=>request(path,options,!localAdmin),isLocal:()=>localAdmin});
  checkHost().finally(() => window.ShunyanConnection.init({ toast, pair: pairWithCode }));
})();
