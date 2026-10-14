// Offline focus regressions for Applications sorting; all rows and labels are synthetic.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8')
  .replace("import { showSteering } from './steering.js';", 'const showSteering = () => {};')
  .replace('import { auth } from "./auth.js";', 'const auth = { header: () => ({}) };')
  .replace(/\nboot\(\);\s*$/, '');

function harness() {
  const nodes = new Map();
  let header, focusCalls = 0, focusedNode = null;
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: {}, style: { setProperty() {} }, listeners: {}, classList: {
        add() {}, remove() {}, contains: () => false, toggle() { return false; },
      }, getBoundingClientRect: () => ({ height: 0, width: 0 }), getClientRects: () => [],
      querySelectorAll: () => [], appendChild() {}, setAttribute() {}, focus() {}, click() {},
      scrollIntoView() {}, addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); },
    });
    return nodes.get(selector);
  }
  const pane = node('#pane');
  const document = {
    activeElement: null,
    querySelector(selector) {
      if (selector === '.applications-table [data-app-sort="company"]') return header;
      return node(selector);
    },
    querySelectorAll: () => [], addEventListener() {},
    documentElement: { dataset: {}, style: { setProperty() {} } },
    body: { appendChild() {} },
  };
  const context = vm.createContext({
    window: { APPLIEDIN_CONFIG: {}, addEventListener() {} }, document,
    location: { search: '', hash: '', pathname: '/' }, URLSearchParams, console,
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    fetch() { throw Error('Unexpected network request'); },
  });
  vm.runInContext(source, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  state.tab = 'apps';
  context.renderPane = () => {
    header = { dataset: { appSort: 'company' }, focus(options) {
      focusCalls++; focusedNode = this; document.activeElement = this; this.options = options;
    } };
  };
  vm.runInContext('wire()', context);
  header = { dataset: { appSort: 'company' }, focus() { focusCalls++; focusedNode = this; document.activeElement = this; } };
  return {
    document, pane, state,
    click(target) { return pane.listeners.click.at(-1)({ target: { closest: () => target } }); },
    header: () => header,
    focusCalls: () => focusCalls,
    focusedNode: () => focusedNode,
  };
}

test('focused sortable header restores focus to the fresh post-render node', () => {
  const h = harness();
  const before = h.header();
  h.document.activeElement = before;
  h.click(before);
  assert.notEqual(h.header(), before);
  assert.equal(h.focusedNode(), h.header());
  assert.equal(h.document.activeElement.dataset.appSort, 'company');
  assert.equal(h.focusCalls(), 1);
});

test('sorting from an unfocused header makes no focus call', () => {
  const h = harness();
  const unrelated = {};
  h.document.activeElement = unrelated;
  h.click(h.header());
  assert.equal(h.document.activeElement, unrelated);
  assert.equal(h.focusCalls(), 0);
});
