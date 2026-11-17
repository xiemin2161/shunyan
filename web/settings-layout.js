(() => {
  'use strict';

  const sectionTitles = {
    device: '界面设置',
    function: '功能配置'
  };
  const $ = (id) => document.getElementById(id);
  const navLinks = [...document.querySelectorAll('[data-settings-section]')];
  const panels = [...document.querySelectorAll('[data-settings-panel]')];
  const actionFooter = document.querySelector('[data-settings-actions]');
  const stateLabels = { idle: '待命', working: '处理中', waiting: '需要确认', completed: '已完成', error: '遇到问题', offline: 'Codex 离线', unknown: '状态未知' };
  let currentSection = 'function';
  let codexTimer = 0;
  let localHost = false;
  let statusRequestId = 0;
  let statusPending = null;
  let configRevision = null;

  function setSection(name, updateAddress = true) {
    name=({appearance:'device',mapping:'function',connection:'function',codex:'function'})[name]||name;
    if (!sectionTitles[name]) name = 'function';
    if(window.ShunyanConnection?.deviceHint()==='mobile')name='function';
    currentSection = name;
    panels.forEach((panel) => {
      const active = panel.dataset.settingsPanel === name;
      panel.hidden = !active;
      panel.classList.toggle('is-visible', active);
    });
    navLinks.forEach((link) => {
      const active = link.dataset.settingsSection === name;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    $('section-title').textContent = sectionTitles[name];
    if (actionFooter) actionFooter.hidden = false;
    document.body.dataset.settingsModule=name;
    if($('reset-button'))$('reset-button').textContent=name==='device'?'恢复外观默认':'恢复功能默认';
    if($('save-button'))$('save-button').textContent=name==='device'?'保存外观设置':'保存功能配置';
    document.title = `${sectionTitles[name]} · 顺言`;
    if (updateAddress && location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
    requestAnimationFrame(resizeDevices);
  }

  navLinks.forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    setSection(link.dataset.settingsSection);
  }));
  window.addEventListener('hashchange', () => setSection(location.hash.slice(1).split('&')[0], false));

  function isMobileForm() { return window.matchMedia('(max-width: 700px)').matches; }
  function fitDevice(stage, mobile) {
    const shell = stage.querySelector('[data-device-shell]');
    if (!shell) return;
    const baseWidth = 386;
    const baseHeight = 321;
    shell.dataset.formFactor = mobile ? 'mobile' : 'desktop';
    shell.style.width = `${baseWidth}px`;
    shell.style.height = `${baseHeight}px`;

    let scale = 1;
    if (stage.dataset.deviceStage === 'controller') {
      const style = getComputedStyle(document.documentElement);
      const safeTop = parseFloat(style.getPropertyValue('--safe-top')) || 0;
      const safeBottom = parseFloat(style.getPropertyValue('--safe-bottom')) || 0;
      const marginX = mobile ? 32 : 48;
      const marginY = mobile ? 32 : 48;
      const availableWidth = Math.max(1, window.innerWidth - marginX);
      const availableHeight = Math.max(1, window.innerHeight - safeTop - safeBottom - marginY);
      scale = Math.min(1, availableWidth / baseWidth, availableHeight / baseHeight);
      stage.style.width = `${baseWidth * scale}px`;
      stage.style.height = `${baseHeight * scale}px`;
    } else {
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      scale = Math.min(1, width / baseWidth, height / baseHeight);
    }
    shell.style.transform = `translate(-50%, -50%) scale(${Math.max(0.1, scale)})`;
  }

  function resizeDevices() {
    const mobile = isMobileForm();
    document.body.classList.toggle('mobile-form', mobile);
    document.querySelectorAll('[data-device-stage]').forEach((stage) => fitDevice(stage, mobile));
  }

  const resizeObserver = 'ResizeObserver' in window ? new ResizeObserver(resizeDevices) : null;
  document.querySelectorAll('.preview-stage').forEach((stage) => resizeObserver?.observe(stage));
  window.addEventListener('resize', resizeDevices, { passive: true });
  window.visualViewport?.addEventListener('resize', resizeDevices, { passive: true });

  function tokenForThisHost() {
    return localStorage.getItem(`shunyan_session_${encodeURIComponent(location.origin)}`);
  }

  function paintStatus(data, connected) {
    const state = connected && Object.hasOwn(stateLabels, data?.state) ? data.state : (connected ? 'unknown' : 'offline');
    const title = connected && typeof data?.title === 'string' && data.title.length <= 24 ? data.title : (connected ? stateLabels[state] : '主机离线');
    const detail = connected && typeof data?.detail === 'string' && data.detail.length <= 72 ? data.detail : (connected ? '尚无可验证的 Codex 状态事件' : '无法读取本机状态');
    const badge = $('settings-codex-badge');
    const statusPanel = $('settings-codex-state');
    const sourceConnected=connected&&data?.connected!==false&&Boolean(data?.source)&&data.source!=='none'&&state!=='offline';
    badge.textContent = !connected?'等待主机':data?.source==='none'?'未接入':sourceConnected?'已接入':'未连接';
    badge.dataset.state = state;
    const access=$('codex-access-pill');
    const integrated=sourceConnected&&state!=='unknown';
    access.classList.toggle('connected',integrated);
    access.querySelector('span').textContent=integrated?'Codex 已接入':sourceConnected?'Codex 待接入':'Codex 未连接';
    access.title=detail;
    statusPanel.dataset.state = state;
    $('settings-codex-title').textContent = title;
    $('settings-codex-detail').textContent = detail;
    $('settings-codex-detail').hidden=['working','idle','completed'].includes(state);
    const updatedAt = connected && data?.updatedAt ? new Date(data.updatedAt) : null;
    $('settings-codex-time').textContent = updatedAt && Number.isFinite(updatedAt.getTime()) ? `状态更新于 ${updatedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` : (connected ? '暂无 Codex 状态事件' : '等待主机状态');
    document.querySelectorAll('[data-codex-title]').forEach((node) => { node.textContent = title; });
    document.querySelectorAll('[data-codex-detail]').forEach((node) => { node.textContent = detail; });
    document.querySelectorAll('[data-codex-led]').forEach((node) => { node.className = `screen-led screen-state-${state}`; });
  }

  async function refreshCodexStatus() {
    const token = tokenForThisHost();
    const useLocalHost = localHost;
    if (document.hidden) return;
    if (!token && !useLocalHost) { ++statusRequestId; statusPending=null; paintStatus(null, false); return; }
    if(statusPending?.local===useLocalHost && statusPending.token===token) return;
    const requestId=++statusRequestId;
    statusPending={local:useLocalHost,token};
    let invalidated=false;
    const current=()=>requestId===statusRequestId && useLocalHost===localHost && !document.hidden;
    try {
      const response = await fetch(useLocalHost ? '/api/v1/local-status' : '/api/v1/status', { headers: useLocalHost ? {Accept:'application/json'} : { Accept: 'application/json', Authorization: `Bearer ${token}` }, cache: 'no-store' });
      const payload = await response.json();
      if(!current() || (!useLocalHost && token!==tokenForThisHost())) return;
      if (!response.ok || payload?.ok === false) {
        if(!useLocalHost && response.status===401){
          invalidated=true;
          localStorage.removeItem(`shunyan_session_${encodeURIComponent(location.origin)}`);
          document.dispatchEvent(new CustomEvent('shunyan-session-invalid'));
        }
        throw new Error(payload?.error?.code || 'STATUS_UNAVAILABLE');
      }
      const value = payload?.data ?? payload;
      if(useLocalHost && typeof value.configRevision==='number'){
        if(configRevision!==null && configRevision!==value.configRevision)document.dispatchEvent(new CustomEvent('shunyan-config-revision'));
        configRevision=value.configRevision;
      }
      if (useLocalHost && value.connection) {
        const connection = value.connection;
        const label = connection.clientDevice === 'mobile' ? '手机' : '控制端';
        const pill = $('connection-pill');
        pill.classList.toggle('connected',Boolean(connection.online));
        pill.querySelector('span').textContent = connection.paired ? `${label}${connection.online ? '已连接' : '已配对'}` : '等待手机连接';
      } else if(!useLocalHost) {
        $('connection-pill').classList.add('connected');$('connection-pill').querySelector('span').textContent='已连接';
      }
      paintStatus(value.codex || value.status || value, true);
    } catch {
      if(!current() || (!useLocalHost && !invalidated && token!==tokenForThisHost())) return;
      paintStatus(null, false);
      if(useLocalHost){$('connection-pill').classList.remove('connected');$('connection-pill').querySelector('span').textContent='服务离线';}
      else {$('connection-pill').classList.remove('connected');$('connection-pill').querySelector('span').textContent=tokenForThisHost()?'连接中断':'请重新配对';}
    } finally {
      if(requestId===statusRequestId) statusPending=null;
    }
  }

  document.addEventListener('shunyan-host-context',event=>{localHost=event.detail?.isLocalHost===true;refreshCodexStatus();});
  document.addEventListener('shunyan-codex-refresh',()=>refreshCodexStatus());

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshCodexStatus(); });
  setSection(location.hash.slice(1).split('&')[0] || 'function', false);
  resizeDevices();
  refreshCodexStatus();
  codexTimer = window.setInterval(refreshCodexStatus, 2000);
})();
