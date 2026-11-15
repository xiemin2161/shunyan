(() => {
  'use strict';

  const sectionTitles = {
    device: '设备控制',
    mapping: '按键映射',
    connection: '连接设置',
    appearance: '外观设置'
  };
  const $ = (id) => document.getElementById(id);
  const navLinks = [...document.querySelectorAll('[data-settings-section]')];
  const panels = [...document.querySelectorAll('[data-settings-panel]')];
  const actionFooter = document.querySelector('[data-settings-actions]');
  const stateLabels = { idle: '待命', working: '处理中', waiting: '需要确认', completed: '已完成', error: '遇到问题', offline: 'Codex 离线', unknown: '状态未知' };
  let currentSection = 'device';
  let codexTimer = 0;

  function setSection(name, updateAddress = true) {
    if (!sectionTitles[name]) name = 'device';
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
    if (actionFooter) actionFooter.hidden = !['mapping', 'appearance'].includes(name);
    document.title = `${sectionTitles[name]} · 顺言`;
    if (updateAddress && location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
    requestAnimationFrame(resizeDevices);
  }

  navLinks.forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    setSection(link.dataset.settingsSection);
  }));
  window.addEventListener('hashchange', () => setSection(location.hash.slice(1), false));

  function isMobileForm() { return window.matchMedia('(max-width: 700px)').matches; }
  function fitDevice(stage, mobile) {
    const shell = stage.querySelector('[data-device-shell]');
    if (!shell) return;
    const baseWidth = mobile ? 320 : 620;
    const baseHeight = mobile ? 320 : 465;
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
      scale = Math.min(.85, width / baseWidth, height / baseHeight);
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
    badge.textContent = connected ? stateLabels[state] : '未连接';
    badge.dataset.state = state;
    statusPanel.dataset.state = state;
    $('settings-codex-title').textContent = title;
    $('settings-codex-detail').textContent = detail;
    const updatedAt = connected && data?.updatedAt ? new Date(data.updatedAt) : null;
    $('settings-codex-time').textContent = updatedAt && Number.isFinite(updatedAt.getTime()) ? `状态更新于 ${updatedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` : (connected ? '暂无状态事件' : '等待配对主机');
    document.querySelectorAll('[data-codex-title]').forEach((node) => { node.textContent = title; });
    document.querySelectorAll('[data-codex-detail]').forEach((node) => { node.textContent = detail; });
    document.querySelectorAll('[data-codex-led]').forEach((node) => { node.className = `screen-led screen-state-${state}`; });
  }

  async function refreshCodexStatus() {
    const token = tokenForThisHost();
    if (!token || document.hidden) { paintStatus(null, false); return; }
    try {
      const response = await fetch('/api/v1/status', { headers: { Accept: 'application/json', Authorization: `Bearer ${token}` }, cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) throw new Error(payload?.error?.code || 'STATUS_UNAVAILABLE');
      const value = payload?.data ?? payload;
      paintStatus(value.codex || value.status || value, true);
    } catch { paintStatus(null, false); }
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshCodexStatus(); });
  setSection(location.hash.slice(1) || 'device', false);
  resizeDevices();
  refreshCodexStatus();
  codexTimer = window.setInterval(refreshCodexStatus, 2000);
})();
