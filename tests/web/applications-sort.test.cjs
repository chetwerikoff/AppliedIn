// Offline Applications sorting regressions. All rows use synthetic example-co data.
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
const SORT_KEY = 'appliedin.applicationsSort';
const DATE = '2026-10-01T00:00:00Z';

function fixture(fields = {}, pk = 'synthetic-row') {
  return { pk, company: 'example-co', title: 'Test Role', status: 'applied',
    match_score: 5, applied_at: DATE, updated_at: DATE, ...fields };
}

function harness({ initial = {}, throwStorage = false } = {}) {
  const storage = new Map(Object.entries(initial)), nodes = new Map();
  let reads = 0, writes = 0;
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: {}, value: '', checked: false, disabled: false, hidden: false,
      innerHTML: '', textContent: '', className: '', listeners: {},
      classList: { add() {}, remove() {}, contains: () => false, toggle: () => false },
      style: { setProperty() {} },
      getBoundingClientRect: () => ({ height: 0, width: 0 }),
      getClientRects: () => [], querySelectorAll: () => [],
      appendChild() {}, setAttribute() {}, focus() {}, click() {}, scrollIntoView() {},
      addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); },
      async emit(event, data) {
        for (const fn of this.listeners[event] || []) await fn(data);
      },
    });
    return nodes.get(selector);
  }
  const document = {
    querySelector: node, querySelectorAll: () => [], addEventListener() {},
    activeElement: null, body: { appendChild() {} },
    documentElement: { dataset: {}, style: { setProperty() {} } },
  };
  const context = vm.createContext({
    window: { APPLIEDIN_CONFIG: {}, addEventListener() {} },
    document, location: { search: '', hash: '', pathname: '/' },
    URLSearchParams, console,
    localStorage: {
      getItem(k) { reads++; if (throwStorage) throw Error('storage denied'); return storage.get(k) ?? null; },
      setItem(k, v) { writes++; if (throwStorage) throw Error('storage denied'); storage.set(k, String(v)); },
    },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    fetch() { throw Error('Unexpected network request'); },
  });
  vm.runInContext(prepared, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  vm.runInContext('renderPane = () => { globalThis.rendered = viewApps(); }; renderActivityLayout = () => {};', context);
  return {
    context, state, storage, node,
    counters: () => ({ reads, writes }),
    sort: vm.runInContext('sortApplications', context),
    html: () => vm.runInContext('viewApps()', context),
    rendered: () => context.rendered,
    wire: () => vm.runInContext('wire()', context),
    async boot() {
      vm.runInContext([
        'load = async () => {};', 'loadCompanies = async () => {};',
        'loadTracker = () => {};', 'loadQueue = () => {};',
        'loadScanLog = async () => {};', 'connectLive = () => {};',
        'adoptFinishedRun = () => {};', 'renderScanNow = () => {};',
        'renderScanResults = () => {};', 'renderMenuState = () => {};',
        'renderPickerState = () => {};', 'renderDiscoverLabel = () => {};',
      ].join('\n'), context);
      await vm.runInContext('boot()', context);
    },
  };
}

function keys(html) {
  return [...html.matchAll(/<tr data-pk="([^"]+)"/g)].map((match) => match[1]);
}
function target(selector, data = {}, extra = {}) {
  const element = { dataset: data, tagName: 'BUTTON', value: '', checked: false, ...extra };
  element.closest = (asked) => asked === selector ? element : null;
  return element;
}
async function paneClick(h, selector, data = {}) {
  await h.node('#pane').emit('click', { target: target(selector, data) });
}
async function paneChange(h, field, value) {
  const isCheck = field === 'appDue';
  await h.node('#pane').emit('change', {
    target: target('', { appFilter: field },
      isCheck ? { checked: value } : { value }),
  });
}
function ordered(sort, rows, key, direction) {
  return Array.from(sort(rows, key, direction), (row) => row.pk);
}

test('pure comparator sorts each of six displayed columns in both directions', () => {
  const h = harness();
  const values = {
    status: ['tailoring', 'found', 'needs_human'],
    company: ['example-co-z', 'example-co-a', 'example-co-m'],
    role: ['Zeta Role', 'Alpha Role', 'Middle Role'],
    match: [9.5, 1.5, 7],
    applied: ['2026-10-09T00:00:00Z', '2026-10-01T00:00:00Z', '2026-10-04T00:00:00Z'],
    updated: ['2026-10-09T00:00:00Z', '2026-10-01T00:00:00Z', '2026-10-04T00:00:00Z'],
  };
  const fields = { status: 'status', company: 'company', role: 'title',
    match: 'match_score', applied: 'applied_at', updated: 'updated_at' };
  for (const [key, data] of Object.entries(values)) {
    const rows = Object.freeze(data.map((value, i) =>
      Object.freeze(fixture({ [fields[key]]: value }, 'row-' + i))));
    const saved = JSON.stringify(rows);
    assert.deepEqual(ordered(h.sort, rows, key, 'asc'), ['row-1', 'row-2', 'row-0'], key + ' asc');
    assert.deepEqual(ordered(h.sort, rows, key, 'desc'), ['row-0', 'row-2', 'row-1'], key + ' desc');
    assert.equal(JSON.stringify(rows), saved, key + ' input unchanged');
  }
  assert.deepEqual(ordered(h.sort, [fixture({}, 'first'), fixture({}, 'second')],
    'not-a-column', 'asc'), ['first', 'second']);
});

test('invalid, blank and dash-like values stay last in both directions', () => {
  const h = harness();
  const missing = {
    status: { status: '—' }, company: { company: ' - ' },
    role: { title: '' }, match: { match_score: 'not-a-number' },
    applied: { status: 'found', applied_at: '2026-10-12T00:00:00Z' },
    updated: { updated_at: 'not-a-date' },
  };
  for (const [key, fields] of Object.entries(missing)) {
    const valid = fixture({ updated_at: '2026-10-01T00:00:00Z' }, 'valid');
    const invalid = fixture({ updated_at: '2026-10-12T00:00:00Z', ...fields }, 'invalid');
    for (const dir of ['asc', 'desc']) {
      assert.deepEqual(ordered(h.sort, [invalid, valid], key, dir), ['valid', 'invalid'],
        key + ' missing last in ' + dir);
    }
  }
  for (const value of [null, '', '  ', '-', '–', '—']) {
    for (const [key, field] of [['company', 'company'], ['role', 'title'],
      ['match', 'match_score'], ['updated', 'updated_at']]) {
      const valid = fixture({}, 'valid');
      const invalid = fixture({ [field]: value }, 'invalid');
      assert.deepEqual(ordered(h.sort, [invalid, valid], key, 'desc'), ['valid', 'invalid']);
      assert.deepEqual(ordered(h.sort, [invalid, valid], key, 'asc'), ['valid', 'invalid']);
    }
  }
  const manual = fixture({ status: 'applied_manual', applied_at: DATE }, 'manual');
  const stale = fixture({ status: 'needs_human', applied_at: '2026-10-12T00:00:00Z' }, 'stale');
  assert.deepEqual(ordered(h.sort, [stale, manual], 'applied', 'desc'), ['manual', 'stale']);
});

test('ties always prefer latest update and then stable source order', () => {
  const h = harness();
  const old = fixture({ updated_at: '2026-09-01T00:00:00Z' }, 'old');
  const newest = fixture({ updated_at: '2026-10-10T00:00:00Z' }, 'newest');
  const sameTime = fixture({ updated_at: '2026-10-10T00:00:00Z' }, 'same-time');
  for (const key of ['status', 'company', 'role', 'match', 'applied']) {
    for (const dir of ['asc', 'desc']) {
      assert.deepEqual(ordered(h.sort, [old, newest, sameTime], key, dir),
        ['newest', 'same-time', 'old'], key + ' updated newest');
    }
  }
  assert.deepEqual(ordered(h.sort,
    [fixture({}, 'first'), fixture({}, 'second')], 'updated', 'asc'),
    ['first', 'second']);
});

test('no valid preference retains the exact existing visible() default order', () => {
  for (const stored of [undefined, '{broken', '{"key":"other","direction":"asc"}',
    '{"key":"company","direction":"sideways"}']) {
    const h = harness({ initial: stored == null ? {} : { [SORT_KEY]: stored } });
    assert.equal(h.state.appsSort, null);
    h.state.apps = [
      fixture({ applied_at: '2026-09-01T00:00:00Z', updated_at: '2026-10-12T00:00:00Z' }, 'old'),
      fixture({ applied_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z' }, 'new'),
    ];
    assert.deepEqual(keys(h.html()), ['new', 'old']);
    assert.doesNotMatch(h.html(), /aria-sort="(?:ascending|descending)"/);
  }
});

test('clickable native headers set direction and aria-sort; links stay inert', async () => {
  const h = harness();
  h.state.tab = 'apps'; h.state.apps = [
    fixture({ company: 'example-co-b' }, 'b'), fixture({ company: 'example-co-a' }, 'a'),
  ];
  h.wire();
  const html = h.html();
  assert.equal([...html.matchAll(/data-app-sort="[^"]+"/g)].length, 6);
  assert.match(html, /<th>links<\/th>/);
  assert.match(html, /<button type="button" class="app-sort" data-app-sort="company"/);
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  assert.equal(h.state.appsSort.direction, 'asc');
  assert.deepEqual(keys(h.rendered()), ['a', 'b']);
  assert.match(h.rendered(), /<th aria-sort="ascending">[\s\S]*?data-app-sort="company"[^>]*>company<span[^>]*>↑/);
  assert.equal((h.rendered().match(/aria-sort="/g) || []).length, 1);
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  assert.equal(h.state.appsSort.direction, 'desc');
  assert.deepEqual(keys(h.rendered()), ['b', 'a']);
  assert.match(h.rendered(), /<th aria-sort="descending">[\s\S]*?data-app-sort="company"[^>]*>company<span[^>]*>↓/);
  for (const key of ['match', 'applied', 'updated']) {
    await paneClick(h, '.applications-table [data-app-sort]', { appSort: key });
    assert.equal(h.state.appsSort.direction, 'desc', key + ' starts descending');
  }
  const retained = JSON.stringify(h.state.appsSort);
  h.state.tab = 'pipeline';
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  assert.equal(JSON.stringify(h.state.appsSort), retained);
  h.state.tab = 'apps';
  await paneClick(h, 'a');
  assert.equal(JSON.stringify(h.state.appsSort), retained);
});

test('sorting full filtered set precedes every 50-row page and more increment', async () => {
  const h = harness();
  h.state.tab = 'apps';
  h.state.apps = Array.from({ length: 125 }, (_, i) =>
    fixture({ status: 'found', match_score: i / 12.5 }, 'row-' + i));
  h.wire();
  assert.deepEqual(keys(h.html()).slice(0, 3), ['row-0', 'row-1', 'row-2']);
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'match' });
  assert.equal(h.state.appsSort.direction, 'desc');
  assert.equal(keys(h.rendered()).length, 50);
  assert.deepEqual(keys(h.rendered()), Array.from({ length: 50 }, (_, i) => 'row-' + (124 - i)));
  assert.match(h.rendered(), /data-more="appsTable" data-next="100"/);
  await paneClick(h, '[data-more]', { more: 'appsTable', next: '100' });
  assert.equal(keys(h.rendered()).length, 100);
  assert.deepEqual(keys(h.rendered()), Array.from({ length: 100 }, (_, i) => 'row-' + (124 - i)));
});

test('all Applications filters retain selected column, direction and filtered order', async () => {
  const h = harness();
  h.state.tab = 'apps';
  h.state.apps = [
    fixture({ company: 'example-co-b', title: 'Test Engineer', match_score: 7,
      applied_at: '2026-09-08T00:00:00Z' }, 'b'),
    fixture({ company: 'example-co-a', title: 'Test Engineer', match_score: 5,
      applied_at: '2026-09-09T00:00:00Z' }, 'a'),
    fixture({ company: 'example-co-b', title: 'Test Engineer', match_score: 8,
      applied_at: '2026-09-10T00:00:00Z' }, 'c'),
    fixture({ company: 'example-co-a', title: 'Test Analyst', match_score: 9,
      status: 'found', applied_at: null }, 'd'),
  ];
  h.state.tracker = {
    b: { outcome: 'waiting', follow_up: '2020-01-01' },
    a: { outcome: 'waiting', follow_up: '2020-01-01' },
    c: { outcome: 'waiting', follow_up: '2020-01-01' },
  };
  h.wire();
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'match' });
  assert.deepEqual(keys(h.rendered()), ['d', 'c', 'b', 'a']);
  await paneClick(h, '[data-filter]', { filter: 'applied' });
  assert.deepEqual(keys(h.rendered()), ['c', 'b', 'a']);
  await h.node('#search').emit('input', { target: { value: 'Engineer' } });
  assert.deepEqual(keys(h.rendered()), ['c', 'b', 'a']);
  await h.node('#co-filter').emit('change', {
    target: { value: 'example-co-b', classList: { toggle() {} } },
  });
  assert.deepEqual(keys(h.rendered()), ['c', 'b']);
  await h.node('#co-filter').emit('change', {
    target: { value: '', classList: { toggle() {} } },
  });
  await paneChange(h, 'appFrom', '2026-09-09');
  assert.deepEqual(keys(h.rendered()), ['c', 'a']);
  await paneChange(h, 'appFrom', '');
  await paneChange(h, 'appOutcome', 'waiting');
  assert.deepEqual(keys(h.rendered()), ['c', 'b', 'a']);
  await paneChange(h, 'appDue', true);
  assert.deepEqual(keys(h.rendered()), ['c', 'b', 'a']);
  assert.equal(h.state.appsSort.key, 'match');
  assert.equal(h.state.appsSort.direction, 'desc');
  assert.match(h.rendered(), /aria-sort="descending"/);
  await paneChange(h, 'appOutcome', 'offer');
  assert.deepEqual(keys(h.rendered()), []);
  assert.match(h.rendered(), /Nothing under this status filter/);
  assert.equal(h.state.appsSort.key, 'match');
});

test('stored sort restores on fresh VM and legacy preference keys still work', async () => {
  const h = harness({ initial: {
    'appliedin.reviewSort': 'company', 'appliedin.reviewCollapsed': 'yes',
    'appliedin.activityCollapsed': 'yes', 'appliedin.theme': 'dark',
    'appliedin.scanw': '24', 'appliedin.freshw': '48',
  } });
  assert.equal(h.state.reviewCollapsed, true);
  assert.equal(h.state.activityCollapsed, true);
  assert.equal(h.state.reviewSort, 'company');
  h.state.tab = 'apps'; h.state.apps = [
    fixture({ company: 'example-co-a' }, 'a'), fixture({ company: 'example-co-b' }, 'b'),
  ];
  h.wire();
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  assert.equal(h.storage.get(SORT_KEY), '{"key":"company","direction":"desc"}');
  const restored = harness({ initial: Object.fromEntries(h.storage) });
  assert.equal(restored.state.appsSort.key, 'company');
  assert.equal(restored.state.appsSort.direction, 'desc');
  restored.state.apps = h.state.apps;
  assert.deepEqual(keys(restored.html()), ['b', 'a']);
  assert.match(restored.html(), /aria-sort="descending"/);
});

test('denied localStorage cannot break source evaluation, boot or UI preference clicks', async () => {
  const h = harness({ throwStorage: true });
  h.state.tab = 'apps';
  h.state.apps = [fixture({ company: 'example-co-z' }, 'z'),
    fixture({ company: 'example-co-a' }, 'a')];
  await h.boot();
  assert.equal(h.context.document.documentElement.dataset.theme, 'light');
  await paneClick(h, '.applications-table [data-app-sort]', { appSort: 'company' });
  assert.equal(h.state.appsSort.direction, 'asc');
  assert.deepEqual(keys(h.rendered()), ['a', 'z']);
  await h.node('#btn-activity-panel').emit('click', {});
  assert.equal(h.state.activityCollapsed, true);
  await h.node('#m-theme').emit('click', {});
  assert.equal(h.context.document.documentElement.dataset.theme, 'dark');
  await h.node('#cp-window').emit('click', {
    target: target('[data-scan-w]', { scanW: '24' }),
  });
  assert.equal(h.state.scanHours, 24);
  vm.runInContext('setReviewSort("oldest")', h.context);
  assert.equal(h.state.reviewSort, 'oldest');
  assert.ok(h.counters().reads >= 5);
  assert.ok(h.counters().writes >= 4);
  assert.equal(h.storage.size, 0);
});
