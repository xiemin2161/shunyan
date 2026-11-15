(() => {
  'use strict';

  const sessionKey = `shunyan_session_${encodeURIComponent(location.origin)}`;
  const MAX_QUEUED_CHUNKS = 4;
  let current = null;
  let starting = null;
  let onError = () => {};

  function getToken() {
    return localStorage.getItem(sessionKey);
  }

  async function postJson(path, token, payload, keepalive = false) {
    const response = await fetch(`/api/v1/audio/${path}`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: payload === null ? undefined : JSON.stringify(payload),
      cache: 'no-store',
      keepalive
    });
    let body = null;
    try { body = await response.json(); } catch { /* host may already have closed the session */ }
    if (!response.ok || body?.ok === false) {
      const error = new Error(body?.error?.message || `音频连接失败（${response.status}）`);
      error.code = body?.error?.code;
      error.status = response.status;
      throw error;
    }
    return body?.data ?? body;
  }

  function explain(error) {
    if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') return '请允许此网页使用手机麦克风';
    if (error?.name === 'NotFoundError' || error?.name === 'DevicesNotFoundError') return '没有找到手机麦克风';
    if (error?.name === 'NotReadableError' || error?.name === 'TrackStartError') return '手机麦克风正在被其他应用占用';
    if (error?.name === 'NotSupportedError') return '当前浏览器不支持麦克风实时转接';
    if (error?.name === 'AbortError') return '电脑音频转接超时，请检查局域网后重试';
    if (error instanceof TypeError) return '电脑连接已断开或局域网不稳定，音频转接已停止';
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) return '请用可信 HTTPS 地址和最新版 Safari/Chrome 打开';
    return error?.message || '手机麦克风启动失败';
  }

  async function start() {
    if (current && !current.closed) return;
    if (starting) return starting;
    const token = getToken();
    if (!token) throw new Error('请先在设置页连接电脑服务');
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) throw new Error(explain());
    if (!window.AudioWorkletNode || !window.AudioContext) throw new Error('当前浏览器不支持麦克风实时转接，请更新 Safari 或 Chrome');

    starting = (async () => {
      const context = new AudioContext({ latencyHint: 'interactive' });
      const resumePromise = context.resume();
      let media = null;
      let state = null;
      try {
        media = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        await resumePromise;
        await context.audioWorklet.addModule(new URL('./audio-capture-worklet.js', location.href));
        const source = context.createMediaStreamSource(media);
        const processor = new AudioWorkletNode(context, 'shunyan-pcm-capture', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1]
        });
        const silentOutput = context.createGain();
        silentOutput.gain.value = 0;
        state = {
          token, context, media, source, processor, silentOutput,
          ready: false, closed: false, sequence: 0, pending: 0, queue: Promise.resolve(), stopSent: false
        };
        current = state;
        processor.port.onmessage = (event) => queueChunk(state, event.data);
        source.connect(processor);
        processor.connect(silentOutput);
        silentOutput.connect(context.destination);

        await postJson('start', token, { sampleRate: context.sampleRate });
        if (state.closed) {
          await sendStop(state, true);
          return;
        }
        await context.resume();
        for (const track of media.getTracks()) {
          track.addEventListener('ended', () => fail(state, new Error('手机麦克风连接已结束')), { once: true });
        }
      } catch (error) {
        if (state) cleanup(state);
        else {
          for (const track of media?.getTracks?.() || []) track.stop();
          try { await context.close(); } catch { /* already closed */ }
        }
        if (current === state) current = null;
        throw new Error(explain(error));
      }
    })();
    try { await starting; }
    finally { starting = null; }
  }

  function queueChunk(state, buffer) {
    if (!state.ready || state.closed || !(buffer instanceof ArrayBuffer)) return;
    if (state.pending >= MAX_QUEUED_CHUNKS) {
      fail(state, new Error('局域网音频延迟过高，请检查 Wi-Fi 后重新开始'));
      return;
    }
    const sequence = state.sequence++;
    state.pending += 1;
    state.queue = state.queue.then(async () => {
      if (state.closed) return;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2500);
      try {
        const response = await fetch('/api/v1/audio/chunk', {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/octet-stream',
            Authorization: `Bearer ${state.token}`,
            'X-Audio-Sequence': String(sequence)
          },
          body: buffer,
          cache: 'no-store',
          signal: controller.signal
        });
        if (!response.ok) {
          let payload = null;
          try { payload = await response.json(); } catch { /* error body is optional */ }
          throw new Error(payload?.error?.message || '电脑音频转接中断');
        }
      } finally {
        clearTimeout(timeout);
        state.pending = Math.max(0, state.pending - 1);
      }
    }).catch((error) => fail(state, error));
  }

  function cleanup(state) {
    state.closed = true;
    state.ready = false;
    try { state.processor.port.onmessage = null; state.processor.port.close(); } catch { /* worklet already stopped */ }
    for (const node of [state.source, state.processor, state.silentOutput]) {
      try { node.disconnect(); } catch { /* node already disconnected */ }
    }
    for (const track of state.media.getTracks()) {
      try { track.stop(); } catch { /* track already stopped */ }
    }
    try { state.context.close(); } catch { /* context already closed */ }
  }

  async function sendStop(state, keepalive = false) {
    if (state.stopSent) return;
    state.stopSent = true;
    try { await postJson('stop', state.token, null, keepalive); } catch { /* host idle lease also closes audio */ }
  }

  async function stop(keepalive = false) {
    const state = current;
    if (!state) return;
    current = null;
    cleanup(state);
    await sendStop(state, keepalive);
  }

  function fail(state, error) {
    if (state.closed) return;
    onError(explain(error));
    if (current === state) stop().catch(() => {});
    else cleanup(state);
  }

  function resume() {
    if (!current || current.closed) throw new Error('手机麦克风通道尚未准备好');
    current.ready = true;
  }

  window.PhoneAudioStream = {
    start,
    resume,
    stop,
    setErrorHandler(handler) { onError = typeof handler === 'function' ? handler : () => {}; },
    get active() { return Boolean(current && !current.closed && current.ready); }
  };
})();
