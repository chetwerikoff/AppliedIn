// Issue #301: private form review UI in an offline VM. Nothing is submitted.
// No network, browser, real applicant, Redis, credentials or external services.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8')
  .replace("import { showSteering } from './steering.js';", 'const showSteering = () => {};')
  .replace('import { auth } from "./auth.js";', 'const auth = { header: () => ({}) };')
  .replace(/\nboot\(\);\s*$/, '');

function harness(packetReply = null) {
  const nodes = new Map(), requests = [], persisted = [];
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: {}, value: '', innerHTML: '', hidden: false, style: {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      textContent: '', open: false,
      replaceChildren() { this.innerHTML = ''; this.textContent = ''; },
      showModal() { this.open = true; }, close() { this.open = false; },
      querySelectorAll() { return []; }, getBoundingClientRect() { return { width: 0, height: 0 }; },
      addEventListener() {}, closest() { return null; }, focus() {}, click() {},
      setAttribute() {},
    });
    return nodes.get(selector);
  };
  const document = {
    querySelector: node, querySelectorAll: () => [],
    createElement: () => ({ textContent: '', className: '' }),
    addEventListener() {},
    documentElement: { dataset: {}, style: { setProperty() {} } },
    body: { appendChild() {}, classList: { add() {}, remove() {} } },
  };
  const context = vm.createContext({
    document, location: { search: '', hash: '', pathname: '/', href: 'http://localhost:8000/' },
    window: { APPLIEDIN_CONFIG: {}, addEventListener() {} },
    console, URLSearchParams, Date, Promise,
    CSS: { escape: (value) => value }, confirm: () => true,
    localStorage: {
      getItem: () => null,
      setItem: (...args) => persisted.push(args),
    },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0,
    clearInterval() {}, requestAnimationFrame: (fn) => fn(),
    fetch: async (...args) => {
      requests.push(args);
      return packetReply || { ok: false, status: 409,
        json: async () => ({ error: 'synthetic refusal' }) };
    },
  });
  vm.runInContext(source, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  return { node, state, requests, persisted,
    fn: (name) => vm.runInContext(name, context) };
}

const PK = 'example-co#synthetic';
function job() {
  return { pk: PK, company: 'example-co', title: 'Synthetic role',
    status: 'needs_human', gate_reason: 'submit_review',
    review_state: 'pending', gate_generation: 'synthetic-generation',
    review_expires_at: '2099-01-01T00:00:00+00:00',
    submit_hold_state: 'clear', possible_submission: false };
}

test('submit review never borrows generic resume/queue/mark-applied authority', () => {
  const h = harness();
  h.state.apps = [job()];
  assert.equal(h.fn('routeOf')(job()), 'submit_review');
  for (const action of ['answer', 'mark-applied', 'queue-apply', 'retry',
    'apply-now', 'skip', 'reopen', 'cancel', 'retailor']) {
    assert.equal(h.fn('rowActionAllowed')(PK, action), false);
  }
  const card = h.fn('needsCard')(job());
  assert.match(card, /data-submit-review-open/);
  assert.doesNotMatch(card, /data-act="answer"|data-act="mark-applied"|data-act="skip"/);
});

test('without current private packet, decision controls are always hidden', async () => {
  const h = harness();
  h.state.apps = [job()];
  await h.fn('openSubmitReview')(PK);
  assert.equal(h.requests.length, 1);
  assert.match(h.requests[0][0], /\/applications\/example-co%23synthetic\/submit-review/);
  assert.equal(h.requests[0][1].cache, 'no-store');
  assert.equal(h.node('#submit-review-actions').hidden, true);
  assert.equal(h.persisted.length, 0);
});

test('private values are HTML escaped, never placed in browser storage', async () => {
  const pkt = {
    gate_generation: 'synthetic-generation',
    job: { company: 'example-co', job_id: '1' },
    page: { form_selector: '#form', submit_selector: '#submit' },
    attachment: { filename: 'Test User.pdf', pdf_sha256: 'abc' },
    expires_at: '2099-01-01T00:00:00+00:00',
    controls: [
      { label: '<img src=x onerror=evil()>', selector: '#email',
        value: '<script>evil()</script>', checked: false,
        files: [], options: [], combobox: null },
    ],
  };
  const h = harness({ ok: true, status: 200, json: async () => ({
    packet: pkt, screenshot: 'data:image/png;base64,iVBORw0KGgo=',
  }) });
  h.state.apps = [job()];
  await h.fn('openSubmitReview')(PK);
  const html = h.node('#submit-review-body').innerHTML;
  assert.match(html, /&lt;img src=x onerror=evil\(\)&gt;/);
  assert.match(html, /&lt;script&gt;evil\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|<img src=x/);
  assert.equal(h.node('#submit-review-actions').hidden, false);
  assert.deepEqual(h.persisted, []);
});

test('stale reviewer generation cannot perform a POST', async () => {
  const h = harness();
  h.state.apps = [job()];
  // This safety is independently visible to the review control renderer.
  h.state.apps[0].review_state = 'expired';
  assert.doesNotMatch(h.fn('needsCard')(h.state.apps[0]), /data-submit-review-open/);
});


test('native multi-select review separates every selected caption and value', async () => {
  const selected = [
    { label: 'Hybrid', value: 'hybrid' },
    { label: 'Remote', value: 'remote' },
  ];
  const options = [...selected,
    { label: 'Office', value: 'office' },
    { label: '<Unselected>', value: 'decline' }];
  const packet = {
    gate_generation: 'synthetic-generation',
    job: { company: 'example-co', job_id: 'synthetic' },
    page: { form_selector: '#form', submit_selector: '#submit' },
    attachment: { filename: 'Test User.pdf', pdf_sha256: 'synthetic' },
    expires_at: '2099-01-01T00:00:00+00:00',
    controls: [{
      label: 'Work preferences', selector: '#work', tag: 'select',
      value: 'hybrid', checked: false, files: [], combobox: null,
      selected_options: selected, options,
    }],
  };
  const h = harness({ ok: true, status: 200, json: async () => ({
    packet, screenshot: 'data:image/png;base64,iVBORw0KGgo=',
  }) });
  h.state.apps = [job()];
  await h.fn('openSubmitReview')(PK);
  const html = h.node('#submit-review-body').innerHTML;
  const selection = html.split('Selected options (caption / value):</b>')[1]
    .split('Available but unselected:')[0];
  const available = html.split('Available but unselected:</b>')[1].split('</div>')[0];
  assert.match(selection, /Hybrid \(hybrid\).*Remote \(remote\)/);
  assert.doesNotMatch(selection, /Office|Unselected/);
  assert.match(available, /Office \(office\).*&lt;Unselected&gt; \(decline\)/);
  assert.doesNotMatch(available, /Hybrid|Remote/);
  assert.equal(h.node('#submit-review-actions').hidden, false);
  assert.deepEqual(h.persisted, []);
});

test('only closed harmless review states display the explicit fresh Retry action', () => {
  for (const review_state of ['rejected', 'expired', 'restart', 'session_lost']) {
    const h = harness();
    const closed = { ...job(), review_state };
    h.state.apps = [closed];
    assert.equal(h.fn('rowActionAllowed')(PK, 'retry'), true);
    assert.match(h.fn('needsCard')(closed), /data-act="retry"/);
    for (const action of ['answer', 'queue-apply', 'mark-applied', 'apply-now']) {
      assert.equal(h.fn('rowActionAllowed')(PK, action), false);
    }
  }
  for (const review_state of ['pending', 'claimed', 'invalidated']) {
    const h = harness();
    const active = { ...job(), review_state };
    h.state.apps = [active];
    assert.equal(h.fn('rowActionAllowed')(PK, 'retry'), false);
    assert.doesNotMatch(h.fn('needsCard')(active), /data-act="retry"/);
  }
});
