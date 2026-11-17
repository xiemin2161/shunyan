const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/settings.js', 'utf8');
const marker = "$('shortcut-manual-confirm').addEventListener('click', () => {";
const start = source.indexOf(marker);
const end = source.indexOf('\n  });', start);
const body = source.slice(start + marker.length, end);
function submit(main, checked) {
  let result = null, message = null;
  const context = { manualKey: {value: main}, modifierBox: {querySelectorAll: () => checked.map(value => ({value}))}, MODIFIERS: new Set(['ALT_LEFT','ALT_RIGHT','CTRL_LEFT','CTRL_RIGHT','SHIFT_LEFT','SHIFT_RIGHT','META_LEFT','META_RIGHT']), toast: m => {message=m}, manualTarget: {}, manualDialog: {close() {}}, window: {ShunyanShortcutRecorder: {setMapping: (_, value) => {result=value}}} };
  vm.runInNewContext(`(() => {${body}\n})()`, context);
  return {result, message};
}
test('selecting right Alt in both controls sets a single right Alt', () => {
  assert.equal(submit('ALT_RIGHT', ['ALT_RIGHT']).result, 'ALT_RIGHT');
});
test('a single checked modifier can be used on its own', () => {
  assert.equal(submit('', ['CTRL_RIGHT']).result, 'CTRL_RIGHT');
});
test('a combination contains modifiers followed by a main key', () => {
  assert.equal(submit('A', ['CTRL_LEFT', 'ALT_RIGHT']).result, 'CTRL_LEFT+ALT_RIGHT+A');
});
test('two different modifiers without a main key are rejected', () => {
  assert.equal(submit('ALT_RIGHT', ['CTRL_LEFT']).result, null);
});
