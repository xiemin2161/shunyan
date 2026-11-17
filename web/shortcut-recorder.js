(() => {
  'use strict';

  const INPUT_IDS = [
    'mapping-voice-start',
    'mapping-confirm',
    'mapping-cancel'
  ];
  const MODIFIERS = new Set([
    'ALT_LEFT', 'ALT_RIGHT', 'CTRL_LEFT', 'CTRL_RIGHT',
    'SHIFT_LEFT', 'SHIFT_RIGHT', 'META_LEFT', 'META_RIGHT'
  ]);
  const ALT_KEYS = new Set(['ALT_LEFT', 'ALT_RIGHT']);
  const FRIENDLY = {
    ALT_LEFT: '左 Alt / Option', ALT_RIGHT: '右 Alt / Option',
    CTRL_LEFT: '左 Ctrl', CTRL_RIGHT: '右 Ctrl',
    SHIFT_LEFT: '左 Shift', SHIFT_RIGHT: '右 Shift',
    META_LEFT: '左 Command / Win', META_RIGHT: '右 Command / Win',
    ENTER: 'Enter', ESC: 'Esc', TAB: 'Tab', SPACE: '空格',
    BACKSPACE: '退格', DELETE: 'Delete', HOME: 'Home', END: 'End',
    PAGE_UP: 'Page Up', PAGE_DOWN: 'Page Down',
    UP: '↑', DOWN: '↓', LEFT: '←', RIGHT: '→'
  };
  const attached = new WeakMap();
  let activeEntry = null;
  let listenersInstalled = false;
  let altGraphNoticeShown = false;

  function locationSide(event, code) {
    if (code.endsWith('Left')) return 'LEFT';
    if (code.endsWith('Right')) return 'RIGHT';
    if (event.location === 1) return 'LEFT';
    if (event.location === 2) return 'RIGHT';
    return null;
  }

  function modifierName(event) {
    const code = String(event.code || '');
    const key = String(event.key || '');
    let family = null;
    if (code.startsWith('Alt') || key === 'Alt' || key === 'AltGraph') family = 'ALT';
    else if (code.startsWith('Control') || key === 'Control') family = 'CTRL';
    else if (code.startsWith('Shift') || key === 'Shift') family = 'SHIFT';
    else if (code.startsWith('Meta') || code.startsWith('OS') || key === 'Meta' || key === 'OS') family = 'META';
    if (!family) return null;
    const side = code === 'AltGraph' || key === 'AltGraph'
      ? 'RIGHT'
      : locationSide(event, code);
    return side ? `${family}_${side}` : null;
  }

  function keyName(event) {
    const code = String(event.code || '');
    const modifier = modifierName(event);
    if (modifier) return modifier;
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (/^F(?:[1-9]|1[0-2])$/.test(code)) return code;
    const named = {
      Enter: 'ENTER', NumpadEnter: 'ENTER', Escape: 'ESC', Tab: 'TAB',
      Space: 'SPACE', Backspace: 'BACKSPACE', Delete: 'DELETE',
      Home: 'HOME', End: 'END', PageUp: 'PAGE_UP', PageDown: 'PAGE_DOWN',
      ArrowUp: 'UP', ArrowDown: 'DOWN', ArrowLeft: 'LEFT', ArrowRight: 'RIGHT'
    };
    if (named[code]) return named[code];
    const key = String(event.key || '');
    const keyFallback = {
      Enter: 'ENTER', Escape: 'ESC', Tab: 'TAB', ' ': 'SPACE',
      Backspace: 'BACKSPACE', Delete: 'DELETE', Home: 'HOME', End: 'END',
      PageUp: 'PAGE_UP', PageDown: 'PAGE_DOWN',
      ArrowUp: 'UP', ArrowDown: 'DOWN', ArrowLeft: 'LEFT', ArrowRight: 'RIGHT'
    };
    if (keyFallback[key]) return keyFallback[key];
    if (/^[a-z]$/i.test(key)) return key.toUpperCase();
    if (/^[0-9]$/.test(key)) return key;
    if (/^F(?:[1-9]|1[0-2])$/.test(key)) return key;
    return null;
  }

  function physicalIdentity(event) {
    return `${String(event.code || event.key || 'Unidentified')}:${Number(event.location) || 0}`;
  }

  function friendlyKey(key) {
    return FRIENDLY[key] || key;
  }

  function friendlyMapping(value) {
    if (typeof value !== 'string' || !value.trim()) return '点击后按下快捷键';
    return value.split('+').map((key) => friendlyKey(key.trim().toUpperCase())).join(' + ');
  }

  function dispatchFeedback(message, type = 'error') {
    const EventCtor = window.CustomEvent || CustomEvent;
    document.dispatchEvent(new EventCtor('shunyan-shortcut-feedback', {
      detail: { message, type }
    }));
  }

  function render(entry) {
    if (entry.state.recording) {
      entry.display.textContent = entry.state.keys.length
        ? `${entry.state.keys.map(friendlyKey).join(' + ')} …`
        : '按下按键或组合键…';
      entry.input.setAttribute('aria-valuetext', '正在录入，请按下按键或组合键');
      entry.input.setAttribute('data-recording', 'true');
      entry.display.setAttribute('data-recording', 'true');
      return;
    }
    entry.display.textContent = friendlyMapping(entry.input.value);
    entry.input.setAttribute('aria-valuetext', friendlyMapping(entry.input.value));
    entry.input.removeAttribute('data-recording');
    entry.display.removeAttribute('data-recording');
  }

  function cancel(entry) {
    if (!entry || !entry.state.recording) return;
    clearTimeout(entry.state.waitTimer);
    clearTimeout(entry.state.focusLossTimer);
    entry.state.focusLossTimer = 0;
    entry.state.recording = false;
    entry.state.down.clear();
    entry.state.ignoredDown.clear();
    entry.state.keys = [];
    if (activeEntry === entry) activeEntry = null;
    render(entry);
  }

  function begin(entry) {
    if (entry.state.recording) return;
    if (activeEntry && activeEntry !== entry) cancel(activeEntry);
    entry.state.recording = true;
    entry.state.down.clear();
    entry.state.ignoredDown.clear();
    entry.state.keys = [];
    activeEntry = entry;
    render(entry);
    entry.state.waitTimer = setTimeout(() => {
      if (activeEntry !== entry || !entry.state.recording || entry.state.keys.length) return;
      cancel(entry);
      requestManual(entry);
    }, 5000);
  }

  function requestManual(entry) {
    const EventCtor = window.CustomEvent || CustomEvent;
    document.dispatchEvent(new EventCtor('shunyan-shortcut-manual', { detail: { inputId: entry.input.id } }));
  }

  function finish(entry) {
    const uniqueKeys = [...new Set(entry.state.keys)];
    const modifiers = uniqueKeys.filter((key) => MODIFIERS.has(key));
    const mainKeys = uniqueKeys.filter((key) => !MODIFIERS.has(key));
    let result = null;

    if (uniqueKeys.length === 1) {
      result = uniqueKeys;
    } else if (mainKeys.length === 1 && uniqueKeys.length <= 5) {
      result = [...modifiers, mainKeys[0]];
    }

    if (!result) {
      const message = uniqueKeys.length > 5
        ? '快捷键最多支持 5 个键，请重新录入'
        : '组合键需要由修饰键和一个主键组成，请重新录入';
      cancel(entry);
      dispatchFeedback(message);
      return;
    }

    entry.state.recording = false;
    clearTimeout(entry.state.waitTimer);
    clearTimeout(entry.state.focusLossTimer);
    entry.state.focusLossTimer = 0;
    entry.state.down.clear();
    entry.state.ignoredDown.clear();
    entry.state.keys = [];
    if (activeEntry === entry) activeEntry = null;
    entry.input.value = result.join('+');
    render(entry);
    entry.input.dispatchEvent(new Event('input', { bubbles: true }));
    entry.input.dispatchEvent(new Event('change', { bubbles: true }));
    dispatchFeedback(`已录入：${friendlyMapping(entry.input.value)}`, 'success');
  }

  function deferFocusLoss(entry) {
    if (!entry || !entry.state.recording || entry.state.focusLossTimer) return;
    const keys = [...new Set(entry.state.keys)];
    const awaitingAltRelease = keys.length === 1 && entry.state.down.size === 1 && ALT_KEYS.has(keys[0]);
    if (!awaitingAltRelease) {
      const hadPartialInput = keys.length > 0;
      cancel(entry);
      dispatchFeedback(
        hadPartialInput
          ? '按键录入已中断，请重新点击输入框后再试'
          : '没有捕获到按键，请重新点击输入框后再按一次；系统保留的快捷键可能会被拦截',
        'warning'
      );
      return;
    }
    entry.state.focusLossTimer = setTimeout(() => {
      entry.state.focusLossTimer = 0;
      if (activeEntry !== entry || !entry.state.recording) return;
      finish(entry);
    }, 180);
  }

  function stopEvent(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function handleKeyDown(event) {
    const entry = activeEntry;
    if (!entry || !entry.state.recording) return;
    clearTimeout(entry.state.focusLossTimer);
    entry.state.focusLossTimer = 0;
    stopEvent(event);
    if (event.repeat) return;

    const name = keyName(event);
    if (!name) {
      cancel(entry);
      requestManual(entry);
      return;
    }
    clearTimeout(entry.state.waitTimer);
    const identity = physicalIdentity(event);
    if (entry.state.down.has(identity)) return;
    if (isAltGraph(event)) {
      const syntheticControl = findSyntheticAltGraphControl(entry);
      if (syntheticControl) {
        entry.state.down.delete(syntheticControl);
        const index = entry.state.keys.indexOf('CTRL_LEFT');
        if (index !== -1) entry.state.keys.splice(index, 1);
        entry.state.ignoredDown.add(syntheticControl);
        warnAltGraphLimit();
      }
      if (event.code === 'ControlLeft' || event.code === 'Control') {
        entry.state.ignoredDown.add(identity);
        warnAltGraphLimit();
        return;
      }
    }
    if (entry.state.keys.length >= 5) {
      cancel(entry);
      dispatchFeedback('快捷键最多支持 5 个键，请重新录入');
      return;
    }
    entry.state.down.set(identity, name);
    if (!entry.state.keys.includes(name)) entry.state.keys.push(name);
    render(entry);
  }

  function handleKeyUp(event) {
    const entry = activeEntry;
    if (!entry || !entry.state.recording) return;
    stopEvent(event);
    const identity = physicalIdentity(event);
    if (entry.state.ignoredDown.delete(identity)) return;
    if (!entry.state.down.has(identity)) return;
    entry.state.down.delete(identity);
    if (entry.state.down.size === 0) finish(entry);
  }

  function isAltGraph(event) {
    if (typeof event.getModifierState !== 'function') return false;
    try { return event.getModifierState('AltGraph') === true; }
    catch { return false; }
  }

  function findSyntheticAltGraphControl(entry) {
    for (const [identity, key] of entry.state.down) {
      if (key === 'CTRL_LEFT') return identity;
    }
    return null;
  }

  function warnAltGraphLimit() {
    if (altGraphNoticeShown) return;
    altGraphNoticeShown = true;
    dispatchFeedback('当前键盘会把 AltGr 报告为右 Alt；此布局无法单独区分 Ctrl+右 Alt', 'warning');
  }

  function installListeners() {
    if (listenersInstalled) return;
    listenersInstalled = true;
    document.addEventListener('keydown', handleKeyDown, true);
    document.addEventListener('keyup', handleKeyUp, true);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) cancel(activeEntry);
    });
    window.addEventListener('blur', () => deferFocusLoss(activeEntry));
    window.addEventListener('pagehide', () => cancel(activeEntry));
  }

  function watchValue(entry) {
    const prototype = window.HTMLInputElement && window.HTMLInputElement.prototype;
    const descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, 'value');
    if (!descriptor || !descriptor.get || !descriptor.set) return;
    Object.defineProperty(entry.input, 'value', {
      configurable: true,
      enumerable: descriptor.enumerable,
      get() { return descriptor.get.call(this); },
      set(value) {
        descriptor.set.call(this, value);
        if (entry.display) render(entry);
      }
    });
  }

  function attach(input) {
    if (!input || attached.has(input)) return;
    const field = input.closest('.field') || input.parentElement;
    if (!field) return;
    const display = document.createElement('span');
    display.className = 'shortcut-recorder-display';
    display.setAttribute('aria-hidden', 'true');
    field.classList.add('shortcut-recorder-field');
    field.appendChild(display);

    const entry = {
      input,
      field,
      display,
      state: { recording: false, down: new Map(), ignoredDown: new Set(), keys: [], focusLossTimer: 0, waitTimer: 0 }
    };
    attached.set(input, entry);
    input.readOnly = true;
    input.classList.add('shortcut-recorder-input');
    input.title = '点击后使用实体键盘录入；手机端需要连接实体键盘。若键盘把右 Alt 报告为 AltGraph，则无法单独区分 Ctrl+右 Alt。';
    input.setAttribute('aria-description', '点击或聚焦后按下快捷键。手机需要连接实体键盘录入。部分布局会将 Ctrl+右 Alt 报告为 AltGraph，此时会按右 Alt 录入。');
    watchValue(entry);
    render(entry);

    input.addEventListener('focus', () => begin(entry));
    input.addEventListener('pointerdown', (event) => {
      if (event.button === 0) begin(entry);
    });
    input.addEventListener('blur', () => deferFocusLoss(entry));
  }

  function init() {
    INPUT_IDS.forEach((id) => attach(document.getElementById(id)));
    installListeners();
  }

  window.ShunyanShortcutRecorder = {
    init,
    setMapping(input, value) {
      const entry = attached.get(input);
      if (entry) cancel(entry);
      input.value = value;
      if (entry) render(entry);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
