(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  let currentCode = '';
  let expiresAt = 0;
  let timer = 0;
  let pollTimer = 0;
  let busy = false;
  let toastTimer = 0;

  function toast(message, kind = '') {
    const region = $('toast-region'); region.replaceChildren();
    const node = document.createElement('div'); node.className = `toast ${kind}`.trim(); node.textContent = message; region.append(node);
    requestAnimationFrame(() => node.classList.add('visible'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.classList.remove('visible'); setTimeout(() => node.remove(), 240); }, 2800);
  }

  function showError(message) {
    currentCode = ''; expiresAt = 0;
    $('pairing-code').textContent = '······'; $('pairing-code').removeAttribute('data-code'); $('copy-code').disabled = true;
    $('pairing-expiry').textContent = message;
  }

  function updateExpiry() {
    if (!currentCode) return;
    if (!expiresAt) { $('pairing-expiry').textContent = '有效期由本机服务管理 · 单次使用'; $('copy-code').disabled = false; return; }
    const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    if (!remaining) {
      $('pairing-expiry').textContent = '配对码已过期，请刷新'; $('pairing-code').classList.add('expired'); $('copy-code').disabled = true; return;
    }
    $('pairing-code').classList.remove('expired'); $('copy-code').disabled = false;
    const minutes = String(Math.floor(remaining / 60)).padStart(2, '0');
    const seconds = String(remaining % 60).padStart(2, '0');
    $('pairing-expiry').textContent = `有效期剩余 ${minutes}:${seconds}`;
  }

  async function refresh(rotate = false, silent = false) {
    if (busy) return;
    busy = true;
    const button = $(rotate ? 'rotate-code' : 'refresh-code'); button.disabled = true; button.textContent = rotate ? '生成中…' : '读取中…';
    if (!currentCode || rotate) showError('正在读取配对码…');
    try {
      const response = await fetch(`/api/v1/pairing-code${rotate ? '/rotate' : ''}`, {
        method: rotate ? 'POST' : 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store'
      });
      let body; try { body = await response.json(); } catch { throw new Error('本机服务返回了无法识别的数据'); }
      if (!response.ok || body?.ok === false) {
        const message = body?.error?.message || (response.status === 403 ? '请在运行服务的电脑本机打开此页面' : `读取失败（${response.status}）`);
        throw new Error(message);
      }
      const data = body?.data ?? body;
      const code = data.pairCode || data.code || data.pairingCode;
      if (data.available === false || (!code && Number(data.expiresIn) === 0)) {
        currentCode = ''; expiresAt = 0;
        $('pairing-code').textContent = '······'; $('pairing-code').removeAttribute('data-code'); $('copy-code').disabled = true;
        $('pairing-expiry').textContent = '暂无有效码，请点“重新生成”';
        return;
      }
      if (typeof code !== 'string' || !/^\d{6}$/.test(code)) throw new Error('本机服务未返回有效的六位配对码');
      currentCode = code;
      if (data.expiresAt) {
        const parsed = Date.parse(data.expiresAt); expiresAt = Number.isFinite(parsed) ? parsed : 0;
      }
      if (!expiresAt && Number.isFinite(Number(data.expiresIn))) {
        expiresAt = Date.now() + Math.max(0, Number(data.expiresIn)) * 1000;
      }
      $('pairing-code').textContent = code; $('pairing-code').dataset.code = code;
      updateExpiry(); clearInterval(timer); timer = setInterval(updateExpiry, 1000);
    } catch (error) {
      showError(error.message || '无法读取本机配对码');
      if (!silent) toast(error.message || '无法读取本机配对码', 'error');
    } finally { button.disabled = false; button.textContent = rotate ? '重新生成' : '重新读取'; busy = false; }
  }

  $('refresh-code').addEventListener('click', () => refresh(false));
  $('rotate-code').addEventListener('click', () => refresh(true));
  $('copy-code').addEventListener('click', async () => {
    if (!currentCode) return;
    try {
      await navigator.clipboard.writeText(currentCode);
      toast('配对码已复制到剪贴板', 'success');
    } catch {
      const selection = document.createRange(); selection.selectNodeContents($('pairing-code'));
      const active = document.getSelection(); active.removeAllRanges(); active.addRange(selection);
      toast('浏览器未开放剪贴板，请手动选择配对码复制', 'error');
    }
  });

  refresh();
  pollTimer = setInterval(() => refresh(false, true), 5000);
})();
