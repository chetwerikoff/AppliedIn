// A disconnected application browser needs a top-level actionable state, not log-only deferrals.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8');
const render = source.match(/function renderLlmBanner\(\) \{[\s\S]*?\n\}/)[0];

function show(status, llm_error = null) {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: true, textContent: '',
      classList: { toggle(name, value) { this[name] = value; } },
    });
    return nodes.get(id);
  };
  vm.runInNewContext(render + '\nrenderLlmBanner();', {
    $: get, state: { stats: { browser_status: status, llm_error } },
  });
  return { banner: get('#llm-banner'), message: get('#llm-banner-msg').textContent };
}

test('stopped browser explains automatic startup', () => {
  const result = show({ state: 'not_running', detail: 'Chrome will start automatically.' });
  assert.equal(result.banner.hidden, false);
  assert.match(result.message, /automatically/);
});

test('live Chrome without its extension overrides stale browser quota text', () => {
  const result = show({ state: 'extension_missing', detail: 'BrowserSkill is not connected.' },
    { where: 'browser', msg: 'Old quota error' });
  assert.equal(result.message, 'BrowserSkill is not connected.');
  assert.equal(result.banner.classList.soft, true);
});

test('connected browser does not leave a disconnect banner', () => {
  assert.equal(show({ state: 'connected', detail: 'Connected.' }).banner.hidden, true);
});

test('model errors still surface independently of a connected browser', () => {
  const result = show({ state: 'connected' }, { where: 'scorer', msg: 'Model offline' });
  assert.equal(result.banner.hidden, false);
  assert.match(result.message, /Model offline/);
});
