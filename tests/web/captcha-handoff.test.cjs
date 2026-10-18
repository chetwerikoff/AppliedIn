// CAPTCHA handoff dashboard contract, executed in an offline Node VM.
// Synthetic rows only: no browser, endpoint, owner profile or application.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8');
const prepared = source
  .replace("import { showSteering } from './steering.js';", 'const showSteering = () => {};')
  .replace('import { auth } from "./auth.js";', 'const auth = { header: () => ({}) };')
  .replace(/\nboot\(\);\s*$/, '');
const MANUAL = 'CAPTCHA present; complete the application manually and verify the result in the employer portal before marking it applied.';

function row(pk, gate_reason = 'captcha', gate_question = '', status = 'needs_human') {
  return { pk, company: 'example-co', title: 'Synthetic Engineer', status,
    gate_reason, gate_question, submit_hold_state: 'clear', fail_kind: '',
    fields: [], timeline: [], location: '',
    match_score: 5, jd_url: 'https://example.test/jobs/synthetic' };
}

function harness() {
  const nodes = new Map();
  const requests = [];
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: {}, innerHTML: '', textContent: '', value: '', hidden: false,
      listeners: {}, style: { setProperty() {} },
      classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
      getBoundingClientRect: () => ({ width: 0, height: 0 }),
      getClientRects: () => [], querySelectorAll: () => [],
      addEventListener(name, cb) { (this.listeners[name] ||= []).push(cb); },
      appendChild() {}, setAttribute() {}, focus() {}, click() {}, scrollIntoView() {},
      closest() { return null; },
    });
    return nodes.get(selector);
  }
  const document = {
    querySelector: node, querySelectorAll: () => [], addEventListener() {},
    createElement: () => ({ textContent: '', className: '' }),
    documentElement: { dataset: {}, style: { setProperty() {} } },
    body: { appendChild() {}, classList: { toggle() {}, add() {}, remove() {} } },
  };
  const context = vm.createContext({
    document, window: { APPLIEDIN_CONFIG: {}, addEventListener() {} },
    location: { search: '', hash: '', pathname: '/' },
    URLSearchParams, console, CSS: { escape: (s) => s },
    localStorage: { getItem: () => null, setItem() {} },
    requestAnimationFrame: (fn) => fn(),
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    fetch(...args) { requests.push(args); throw Error('Unexpected network access'); },
  });
  vm.runInContext(prepared, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  state.trackerLoaded = true;
  return { context, state, node, requests,
    fn: (name) => vm.runInContext(name, context),
  };
}

test('exact CAPTCHA fallback, specific question precedence and unchanged other defaults', () => {
  const h = harness();
  assert.equal(h.fn('defaultGateText')(row('captcha')), MANUAL);
  assert.equal(h.fn('defaultGateText')(row('login', 'no_account')),
    'Automatic signup was blocked. Approve once the account exists.');
  assert.equal(h.fn('defaultGateText')(row('other', 'unknown_field')),
    'Paused for your approval. Approve to continue.');
  const specific = row('specific', 'captcha', 'Visible reCAPTCHA needs review.');
  const card = h.fn('needsCard')(specific);
  assert.match(card, /Visible reCAPTCHA needs review/);
  assert.ok(card.includes(MANUAL));
  assert.ok(card.indexOf('Visible reCAPTCHA') < card.indexOf(MANUAL));
  const fallback = h.fn('needsCard')(row('fallback'));
  assert.equal(fallback.split(MANUAL).length, 2);
});

test('CAPTCHA lane and compact actions navigate to nonmutating details, not answer/resume', () => {
  const h = harness();
  const captcha = row('captcha-lane');
  for (const html of [h.fn('laneCard')(captcha), h.fn('gateMini')(captcha)]) {
    assert.match(html, /View details/);
    assert.match(html, /data-open="captcha-lane"/);
    assert.doesNotMatch(html, /data-act="answer"|Solve &amp; submit|Opens the filled application/);
  }
  const regular = h.fn('gateMini')(row('regular', 'owner_question', 'A real question'));
  assert.match(regular, /Answer/);
  assert.doesNotMatch(regular, /View details/);
  assert.doesNotMatch(source, /captcha_wait/);
});

test('Needs-you CAPTCHA has manual controls, no answer field or approval-to-resume', () => {
  const h = harness();
  const captcha = h.fn('needsCard')(row('captcha-needs', 'captcha', 'Check the challenge.'));
  assert.match(captcha, /\bDetails\b/);
  assert.match(captcha, /Already applied/);
  assert.match(captcha, /data-act="mark-applied"/);
  assert.match(captcha, /data-act="skip"/);
  assert.match(captcha, /view posting/);
  assert.ok(captcha.includes(MANUAL));
  assert.doesNotMatch(captcha, /data-act="answer"|data-answer-for|Approve &amp; continue/);

  const regular = h.fn('needsCard')(row('regular-needs', 'owner_question', 'Can you clarify?'));
  assert.match(regular, /data-answer-for="regular-needs"/);
  assert.match(regular, /data-act="answer"/);
  assert.match(regular, /Answer &amp; continue/);
});

test('details drawer contains manual portal guidance and keeps explicit mark-applied/skip', () => {
  const h = harness();
  h.state.apps = [row('captcha-drawer', 'captcha', 'Visible hCaptcha is blocking.')];
  vm.runInContext('loadAgentLog = () => {};', h.context);
  h.fn('openDrawer')('captcha-drawer');
  const html = h.node('#drawer-body').innerHTML;
  assert.match(html, /Visible hCaptcha is blocking/);
  assert.ok(html.includes(MANUAL));
  assert.match(html, /data-act="mark-applied"/);
  assert.match(html, /Already applied/);
  assert.match(html, /data-act="skip"/);
  assert.doesNotMatch(html, /id="gate-answer"|data-act="answer"|Approve &amp; continue/);
  assert.equal(h.requests.length, 0);

  h.state.apps = [row('regular-drawer', 'owner_question', 'What is your preferred schedule?')];
  h.fn('openDrawer')('regular-drawer');
  const regular = h.node('#drawer-body').innerHTML;
  assert.match(regular, /id="gate-answer"/);
  assert.match(regular, /data-act="answer"/);
  assert.match(regular, /Answer &amp; continue/);
});

test('delegated View details action dispatches only to the existing drawer', () => {
  const h = harness();
  h.fn('wire')();
  vm.runInContext('openDrawer = pk => { globalThis.opened = pk; };', h.context);
  const button = { dataset: { open: 'captcha-routed' } };
  const target = {
    closest(selector) { return selector === '[data-open]' ? button : null; },
  };
  const clicks = h.node('#pane').listeners.click;
  assert.ok(clicks?.length);
  // wire() makes unrelated initial reads; View details must add none.
  const beforeClickRequests = h.requests.length;
  clicks.at(-1)({ target });
  assert.equal(h.context.opened, 'captcha-routed');
  assert.equal(h.requests.length, beforeClickRequests);
});

test('only currently active gate codes classify rows; stale metadata never creates answer authority', () => {
  const h = harness();
  const cases = [
    row('captcha-ready', 'captcha', 'Ready to apply after CAPTCHA'),
    row('captcha-approval', 'captcha', 'Approved?'),
    row('captcha-tailored', 'captcha', '', 'tailored'),
    row('ordinary-ready', 'unknown_field', 'Ready to apply after review'),
    row('ordinary-approval', 'approval', 'Please confirm'),
    row('ordinary-tailored', '', '', 'tailored'),
    row('ordinary-gate', 'unknown_field', 'Provide missing answer'),
  ];
  const pick = h.fn('approveAllPicks');
  assert.deepEqual(cases.map(pick), [false, false, true, false, true, true, false]);
  h.state.apps = cases;
  h.state.tab = 'needs';
  assert.deepEqual(Array.from(h.fn('visibleApprovalPicks')(), (r) => r.pk),
    ['ordinary-approval']);
  h.state.tab = 'pipeline';
  // A stale CAPTCHA code on an ordinary tailored row is not a live CAPTCHA gate.
  assert.deepEqual(Array.from(h.fn('visibleApprovalPicks')(), (r) => r.pk),
    ['captcha-tailored', 'ordinary-approval', 'ordinary-tailored']);
});