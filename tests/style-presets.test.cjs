const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mergeStyle } = require('../ui/static/modules/style-presets.js');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('append preserves author instructions and separates the preset', () => {
  assert.equal(mergeStyle('Author instructions\n', '# Preset\n', 'append'), 'Author instructions\n\n# Preset\n');
});
test('append to an empty editor does not add a blank prefix', () => {
  assert.equal(mergeStyle('  ', '# Preset\n', 'append'), '# Preset\n');
});
test('replace and invalid modes are explicit', () => {
  assert.equal(mergeStyle('Old', 'New', 'replace'), 'New');
  assert.throws(() => mergeStyle('Old', 'New', 'unknown'));
});

function browserHarness() {
  const elements = {};
  const requests = [];
  let button;
  const element = () => ({ value: '', disabled: false, focus() {}, dispatchEvent() {},
    append(option) { if (!this.value) this.value = option.value; } });
  const body = { set innerHTML(value) {
    for (const id of ['stylePresetSelect', 'stylePresetPreview', 'appendStylePreset', 'replaceStylePreset']) {
      elements[id] = element();
    }
  } };
  const dialog = { open: false, querySelector: () => body,
    showModal() { this.open = true; }, close() { this.open = false; } };
  elements.style = element();
  elements.style.value = 'Author instructions';
  elements.saveBible = { before(value) { button = value; } };
  const state = { novel: 'Test' };
  const context = {
    state, URLSearchParams, Event: class {}, enc: encodeURIComponent,
    $: id => elements[id], ensureUtilityDialog: () => dialog,
    toast() {}, confirm: () => false, localStorage: { getItem: () => 'en' },
    json: url => new Promise(resolve => requests.push({ url, resolve })),
    document: { createElement: element, addEventListener: (_, callback) => callback() },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../ui/static/modules/style-presets.js'), 'utf8'), context);
  return { elements, requests, state, open: () => button.onclick() };
}

test('a late response never replaces the newly selected preset', async () => {
  const h = browserHarness();
  const opening = h.open();
  h.requests[0].resolve({ presets: [{ name: 'First', builtin: true, description: '' }] });
  await new Promise(setImmediate);
  h.elements.stylePresetSelect.value = JSON.stringify(['Second', 'custom']);
  const second = h.elements.stylePresetSelect.onchange();
  assert.equal(h.elements.appendStylePreset.disabled, true);
  h.requests[2].resolve({ style_text: 'Second preview' });
  await second;
  h.requests[1].resolve({ style_text: 'Stale first preview' });
  await opening;
  assert.equal(h.elements.stylePresetPreview.value, 'Second preview');
  h.elements.appendStylePreset.onclick();
  assert.equal(h.elements.style.value, 'Author instructions\n\nSecond preview');
});

test('changing projects prevents applying an old preview', async () => {
  const h = browserHarness();
  const opening = h.open();
  h.requests[0].resolve({ presets: [{ name: 'First', builtin: true, description: '' }] });
  await new Promise(setImmediate);
  h.state.novel = 'AnotherProject';
  h.requests[1].resolve({ style_text: 'Wrong project' });
  await opening;
  h.elements.appendStylePreset.onclick();
  assert.equal(h.elements.style.value, 'Author instructions');
  assert.equal(h.elements.appendStylePreset.disabled, true);
});
