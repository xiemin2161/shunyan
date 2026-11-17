const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/shortcut-recorder.js'), 'utf8');

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
    this.detail = options.detail;
    this.defaultPrevented = false;
    this.propagationStopped = false;
    this.repeat = Boolean(options.repeat);
    this.button = options.button ?? 0;
    this.code = options.code || '';
    this.location = options.location || 0;
    this.altGraph = Boolean(options.altGraph);
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.propagationStopped = true; }
  getModifierState(name) { return name === 'AltGraph' && this.altGraph; }
}

class FakeElement {
  constructor(tagName = 'span') {
    this.tagName = tagName.toUpperCase();
    this.className = '';
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.parentElement = null;
    this.children = [];
    this.textContent = '';
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  dispatchEvent(event) {
    event.target = this;
    for (const callback of this.listeners.get(event.type) || []) callback(event);
    return !event.defaultPrevented;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  closest(selector) { return selector === '.field' ? this.field || null : null; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 300, height: 44 }; }
}

class FakeInput extends FakeElement {
  constructor(id, value) {
    super('input');
    this.id = id;
    this._value = value;
    this.readOnly = false;
    this.title = '';
    this.field = new FakeElement('label');
    this.field.classList = new FakeClassList();
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  focus(document) {
    if (document.activeElement && document.activeElement !== this) {
      document.activeElement.dispatchEvent(new FakeEvent('blur'));
    }
    document.activeElement = this;
    this.dispatchEvent(new FakeEvent('focus'));
  }
  blur(document) {
    if (document.activeElement === this) {
      document.activeElement = null;
      this.dispatchEvent(new FakeEvent('blur'));
    }
  }
}

function makeHarness(values = {}) {
  const ids = [
    'mapping-voice-start', 'mapping-voice-stop', 'mapping-confirm', 'mapping-cancel'
  ];
  const inputs = new Map(ids.map((id) => [id, new FakeInput(id, values[id] || 'ENTER')]));
  const documentListeners = new Map();
  const document = {
    readyState: 'complete',
    hidden: false,
    activeElement: null,
    getElementById(id) { return inputs.get(id) || null; },
    createElement(name) { return new FakeElement(name); },
    addEventListener(type, callback) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(callback);
    },
    dispatchEvent(event) {
      event.target = document;
      for (const callback of documentListeners.get(event.type) || []) callback(event);
      return !event.defaultPrevented;
    }
  };
  const windowListeners = new Map();
  const window = {
    HTMLInputElement: FakeInput,
    CustomEvent: FakeEvent,
    addEventListener(type, callback) {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(callback);
    },
    ShunyanShortcutRecorder: null
  };
  const context = {
    window,
    document,
    CustomEvent: FakeEvent,
    Event: FakeEvent,
    setTimeout,
    clearTimeout,
    requestAnimationFrame(callback) { callback(); },
    console
  };
  vm.runInNewContext(source, context, { filename: 'shortcut-recorder.js' });

  function key(type, code, location = 0, repeat = false, altGraph = false) {
    const event = new FakeEvent(type, { code, location, repeat, altGraph });
    for (const callback of documentListeners.get(type) || []) callback(event);
    return event;
  }
  return { document, inputs, key, window, documentListeners };
}

function tapKey(harness, code, location = 0) {
  harness.key('keydown', code, location);
  harness.key('keyup', code, location);
}

test('captures Enter, Escape, and Tab as serialized enum values', () => {
  for (const [code, expected] of [['Enter', 'ENTER'], ['Escape', 'ESC'], ['Tab', 'TAB']]) {
    const h = makeHarness();
    const input = h.inputs.get('mapping-confirm');
    input.focus(h.document);
    const down = h.key('keydown', code);
    h.key('keyup', code);
    assert.equal(down.defaultPrevented, true);
    assert.equal(input.value, expected);
  }
});

test('captures a standalone right Alt using code and location', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-voice-start');
  input.focus(h.document);
  tapKey(h, 'AltRight', 2);
  assert.equal(input.value, 'ALT_RIGHT');
});

test('ambiguous Alt never changes the previous mapping', () => {
  const h = makeHarness({ 'mapping-voice-start': 'ALT_RIGHT' });
  const input = h.inputs.get('mapping-voice-start');
  input.focus(h.document);
  h.key('keydown', 'Alt');
  assert.equal(input.value, 'ALT_RIGHT');
});

test('preset selection cancels partial input and records right Alt', () => {
  const h = makeHarness({ 'mapping-voice-start': 'ALT_LEFT' });
  const input = h.inputs.get('mapping-voice-start');
  input.focus(h.document);
  h.key('keydown', 'AltLeft', 1);
  let changes = 0;
  input.addEventListener('change', () => changes++);
  h.window.ShunyanShortcutRecorder.setMapping(input, 'ALT_RIGHT');
  h.key('keyup', 'AltLeft', 1);
  assert.equal(input.value, 'ALT_RIGHT');
  assert.equal(changes, 1);
});

test('collapses an AltGraph synthetic ControlLeft into standalone right Alt', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-voice-start');
  input.focus(h.document);
  h.key('keydown', 'ControlLeft', 1, false, true);
  h.key('keydown', 'AltRight', 2, false, true);
  h.key('keyup', 'ControlLeft', 1, false, true);
  h.key('keyup', 'AltRight', 2, false, true);
  assert.equal(input.value, 'ALT_RIGHT');
});

test('collapses a late AltGraph flag and exposes the Ctrl plus right Alt limitation', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-voice-start');
  const feedback = [];
  h.document.addEventListener('shunyan-shortcut-feedback', (event) => feedback.push(event.detail));
  input.focus(h.document);
  h.key('keydown', 'ControlLeft', 1, false, false);
  h.key('keydown', 'AltRight', 2, false, true);
  h.key('keyup', 'ControlLeft', 1, false, true);
  h.key('keyup', 'AltRight', 2, false, true);
  assert.equal(input.value, 'ALT_RIGHT');
  assert.match(feedback[0].message, /无法单独区分 Ctrl\+右 Alt/);
});

test('keeps the enum value while displaying a friendly label', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-confirm');
  input.focus(h.document);
  h.key('keydown', 'MetaLeft', 1);
  h.key('keydown', 'ShiftRight', 2);
  h.key('keydown', 'KeyA');
  h.key('keyup', 'KeyA');
  h.key('keyup', 'ShiftRight', 2);
  h.key('keyup', 'MetaLeft', 1);
  assert.equal(input.value, 'META_LEFT+SHIFT_RIGHT+A');
  assert.equal(input.field.children[0].textContent, '左 Command / Win + 右 Shift + A');
});

test('captures modifier combinations and puts the main key last', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-voice-start');
  input.focus(h.document);
  h.key('keydown', 'KeyS');
  h.key('keydown', 'ShiftLeft', 1);
  h.key('keydown', 'AltRight', 2);
  h.key('keyup', 'KeyS');
  h.key('keyup', 'ShiftLeft', 1);
  h.key('keyup', 'AltRight', 2);
  assert.equal(input.value, 'SHIFT_LEFT+ALT_RIGHT+S');
});

test('captures Mac Meta plus Shift and a letter', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-confirm');
  input.focus(h.document);
  h.key('keydown', 'MetaLeft', 1);
  h.key('keydown', 'ShiftRight', 2);
  h.key('keydown', 'KeyA');
  h.key('keyup', 'KeyA');
  h.key('keyup', 'ShiftRight', 2);
  h.key('keyup', 'MetaLeft', 1);
  assert.equal(input.value, 'META_LEFT+SHIFT_RIGHT+A');
});

test('autorepeat does not add another key or commit twice', () => {
  const h = makeHarness();
  const input = h.inputs.get('mapping-confirm');
  let changes = 0;
  input.addEventListener('change', () => { changes += 1; });
  input.focus(h.document);
  h.key('keydown', 'KeyA');
  h.key('keydown', 'KeyA', 0, true);
  h.key('keyup', 'KeyA');
  assert.equal(input.value, 'A');
  assert.equal(changes, 1);
});

test('blur and hidden document cancel incomplete capture without changing the old value', () => {
  const blurHarness = makeHarness({ 'mapping-cancel': 'ESC' });
  const blurInput = blurHarness.inputs.get('mapping-cancel');
  blurInput.focus(blurHarness.document);
  blurHarness.key('keydown', 'ControlLeft', 1);
  blurInput.blur(blurHarness.document);
  assert.equal(blurInput.value, 'ESC');
  assert.equal(blurInput.getAttribute('data-recording'), null);

  const hiddenHarness = makeHarness({ 'mapping-cancel': 'ESC' });
  const hiddenInput = hiddenHarness.inputs.get('mapping-cancel');
  hiddenInput.focus(hiddenHarness.document);
  hiddenHarness.key('keydown', 'ControlLeft', 1);
  hiddenHarness.document.hidden = true;
  hiddenHarness.document.dispatchEvent(new FakeEvent('visibilitychange'));
  assert.equal(hiddenInput.value, 'ESC');
});

test('unsupported keys request manual selection and do not change the mapping', () => {
  const h = makeHarness({ 'mapping-cancel': 'ESC' });
  const messages = [];
  h.document.addEventListener('shunyan-shortcut-manual', (event) => messages.push(event.detail));
  const input = h.inputs.get('mapping-cancel');
  input.focus(h.document);
  h.key('keydown', 'Unidentified');
  assert.equal(input.value, 'ESC');
  assert.equal(messages.length, 1);
  assert.equal(messages[0].inputId, 'mapping-cancel');
});

test('rejects more than five unique keys without replacing the previous mapping', () => {
  const h = makeHarness({ 'mapping-confirm': 'ENTER' });
  const input = h.inputs.get('mapping-confirm');
  input.focus(h.document);
  for (const code of ['ControlLeft', 'ShiftLeft', 'AltLeft', 'MetaLeft', 'KeyA']) h.key('keydown', code);
  const sixth = h.key('keydown', 'KeyB');
  assert.equal(sixth.defaultPrevented, true);
  assert.equal(input.value, 'ENTER');
});

