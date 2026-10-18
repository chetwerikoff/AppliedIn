// Reply UI contracts run in a Node VM; no browser, backend, or mailbox is used.
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

function harness() {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { value: '', dataset: {}, innerHTML: '',
      textContent: '', hidden: false, disabled: false, classList: { add() {}, remove() {}, toggle() {} } });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    window: { APPLIEDIN_CONFIG: {}, addEventListener() {} },
    document: { querySelector: node, querySelectorAll: () => [], body: {}, documentElement: { dataset: {} } },
    location: { hash: '', search: '' }, URLSearchParams, CSS: { escape: (s) => s }, console,
    localStorage: { getItem: () => null, setItem() {} }, setTimeout: () => 0, clearTimeout() {},
    setInterval: () => 0, clearInterval() {}, fetch: async () => { throw Error('Unexpected request'); },
  });
  vm.runInContext(prepared, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  vm.runInContext('trackerRows = rows => rows; appliedProfHtml = () => ""; againHtml = () => ""; renderPane = () => {}; toast = () => {};', context);
  return { context, state, node, needs: () => vm.runInContext('viewNeeds()', context), apps: () => vm.runInContext('viewApps()', context) };
}
const reply = (fields = {}) => ({ record_id: 'record-1', label: 'assessment', date: '2026-10-01',
  sender: 'recruiter@example-co.test', subject: 'Next steps', ...fields });
const app = (fields = {}) => ({
  pk: 'example-co#role-1', company: 'example-co', title: 'Engineer', status: 'applied',
  submit_hold_state: 'clear', applied_at: '2026-09-30', updated_at: '2026-09-30', replies: [], ...fields,
});

 test('matched reply renders in Needs and Applications with escaped metadata, without gate actions', () => {
  const h = harness();
  h.state.apps = [app({ replies: [reply({ sender: '<img src=x>', subject: '"onerror"' })] })];
  h.state.tab = 'needs';
  const needs = h.needs();
  assert.match(needs, /Recruiter replies/);
  assert.match(needs, /assessment/);
  assert.match(needs, /&lt;img src=x&gt;/);
  assert.match(needs, /&quot;onerror&quot;/);
  assert.doesNotMatch(needs, /data-act|data-approve-all|gate-input/);
  const apps = h.apps();
  assert.match(apps, /assessment/);
  assert.match(apps, /&lt;img src=x&gt;/);
  assert.match(apps, /<td class="t-co">example-co<\/td>\s*<td class="t-role"[\s\S]*?<\/td>\s*<td><span class="score">/);
  assert.ok(apps.indexOf('data-app-sort="role"') < apps.indexOf('data-app-sort="match"'));
});

test('unassigned-only replies stay visible without gates and ignore company filtering', () => {
  const h = harness();
  h.state.unassignedReplies = [reply({ subject: '<script>inert</script>' })];
  h.state.coFilter = 'another-company';
  const html = h.needs();
  assert.match(html, /Unassigned replies/);
  assert.match(html, /&lt;script&gt;inert&lt;\/script&gt;/);
  assert.match(html, /data-reply-target="record-1"/);
  assert.doesNotMatch(html, /data-act|data-approve-all|gate-input/);
});

test('reply-only empty and gate-plus-reply views preserve the existing gate controls', () => {
  const h = harness();
  assert.match(h.needs(), /Nothing needs you/);
  h.state.apps = [app({ status: 'needs_human', gate_reason: 'approval', gate_question: 'Review this',
    replies: [reply()] })];
  const html = h.needs();
  assert.match(html, /Approve &amp; continue/);
  assert.match(html, /Skip job/);
  assert.match(html, /Recruiter replies/);
  assert.match(html, /data-act="answer"/);
  assert.doesNotMatch(html.match(/<article class="reply-card">[\s\S]*?<\/article>/)?.[0] || '', /data-act|data-approve-all|gate-input/);
});

test('failed assignment retains the item; success reloads and moves the stored reply to its row', async () => {
  const h = harness();
  h.state.apps = [app()];
  h.state.unassignedReplies = [reply()];
  h.node('#pane [data-reply-target="record-1"]').value = 'example-co#role-1';
  let outcome = { ok: false, error: 'temporary failure' }, reloads = 0;
  vm.runInContext(`post = async () => globalThis.assignmentResult;
    loadApps = async () => { globalThis.reloadCount++; state.apps[0].replies = [${JSON.stringify(reply())}]; };
    loadUnassigned = async () => { state.unassignedReplies = globalThis.freshUnassigned; };`, h.context);
  h.context.assignmentResult = outcome;
  h.context.freshUnassigned = [reply()];
  h.context.reloadCount = reloads;
  await vm.runInContext('assignReply("record-1")', h.context);
  assert.equal(h.state.unassignedReplies.length, 1);
  h.context.freshUnassigned = [];
  outcome = { ok: true };
  h.context.assignmentResult = outcome;
  await vm.runInContext('assignReply("record-1")', h.context);
  assert.equal(h.state.unassignedReplies.length, 0);
  assert.equal(h.state.apps[0].replies[0].record_id, 'record-1');
  assert.equal(h.context.reloadCount, 1);
});

test('matched replies obey application search and company filters; the Applications sort order remains full-list based', () => {
  const h = harness();
  h.state.apps = Array.from({ length: 55 }, (_, i) => app({
    pk: `example-co#role-${i}`, title: `Engineer ${i}`, replies: [reply({ record_id: `r${i}` })],
  }));
  h.state.tab = 'needs';
  h.state.coFilter = 'example-co';
  assert.match(h.needs(), /Show 5 more of 55/);
  h.state.coFilter = 'other-company';
  assert.doesNotMatch(h.needs(), /Recruiter replies/);
  h.state.coFilter = '';
  h.state.appsSort = { key: 'company', direction: 'asc' };
  const table = h.apps();
  assert.match(table, /data-app-sort="company"/);
  assert.match(table, /Show 5 more of 55/);
});

test('Needs sorts reply timestamps by instant across timezone offsets', () => {
  const h = harness();
  h.state.apps = [app({ replies: [
    reply({ record_id: 'older', date: '2026-10-01T10:00:00+02:00', sender: 'older@example-co.test' }),
    reply({ record_id: 'newer', date: '2026-10-01T09:00:00+00:00', sender: 'newer@example-co.test' }),
  ] })];
  const html = h.needs();
  assert.ok(html.indexOf('newer@example-co.test') < html.indexOf('older@example-co.test'));
});

test('background refresh and successful assignment repaint actual reply sections', async () => {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { value: '', dataset: {}, innerHTML: '', textContent: '',
      hidden: false, disabled: false, scrollTop: 0, scrollWidth: 0, style: { setProperty() {} },
      classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, querySelector() { return null; },
      querySelectorAll() { return []; }, focus() {}, });
    return nodes.get(selector);
  };
  const serverState = { applications: [app()], unassigned: [] };
  const context = vm.createContext({
    window: { APPLIEDIN_CONFIG: {}, addEventListener() {} },
    document: { activeElement: null, querySelector: node, querySelectorAll: () => [], body: {},
      documentElement: { dataset: {}, style: { setProperty() {} } } },
    location: { hash: '', search: '' }, URLSearchParams, CSS: { escape: (s) => s }, console,
    requestAnimationFrame: () => 0,
    localStorage: { getItem: () => null, setItem() {} }, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options = {}) => {
      if (url === '/applications') return { ok: true, status: 200, json: async () => ({ items: serverState.applications }) };
      if (url === '/replies/unassigned') return { ok: true, status: 200, json: async () => ({ replies: serverState.unassigned }) };
      if (url.endsWith('/assign') && options.method === 'POST') {
        const assigned = serverState.unassigned[0];
        serverState.unassigned = [];
        serverState.applications[0].replies = [...serverState.applications[0].replies, assigned];
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      throw Error(`Unexpected request ${url}`);
    },
  });
  vm.runInContext(prepared, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  context.toast = () => {};
  state.tab = 'needs';
  context.renderTabs = context.renderFooter = context.scheduleFeed = () => {};
  const pane = node('#pane');
  await vm.runInContext('loadApps()', context);
  assert.match(pane.innerHTML, /Nothing needs you/);
  serverState.applications[0].replies = [reply({ record_id: 'arrived', subject: 'New matched mail' })];
  await vm.runInContext('loadApps()', context);
  assert.match(pane.innerHTML, /New matched mail/);
  serverState.unassigned = [reply({ record_id: 'record-1', subject: 'Needs assignment' })];
  await vm.runInContext('loadApps()', context);
  assert.match(pane.innerHTML, /Needs assignment/);
  node('#pane [data-reply-target="record-1"]').value = 'example-co#role-1';
  await vm.runInContext('assignReply("record-1")', context);
  assert.equal(serverState.unassigned.length, 0);
  assert.doesNotMatch(pane.innerHTML, /Unassigned replies|data-reply-target="record-1"/);
  assert.equal((pane.innerHTML.match(/Needs assignment/g) || []).length, 1);
  assert.match(pane.innerHTML, /Recruiter replies/);
  assert.match(pane.innerHTML, /New matched mail|Needs assignment/);
});
