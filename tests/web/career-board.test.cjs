// Offline stream regressions: incomplete packets must never erase UI drafts.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../web/career-board.js'), 'utf8');
const fitSource = fs.readFileSync(path.join(__dirname, '../../web/career-fit.js'), 'utf8').replace(/^export /gm, '');
function harness(fetch) {
  let Board;
  const context = vm.createContext({HTMLElement: class {}, customElements: {define: (_, cls) => Board = cls},
    window: {}, fetch, AbortController, TextDecoder, Date, Set});
  const script = source
    .replace("import { auth } from './auth.js';", "const auth = {header: () => ({Authorization:'Bearer test'})};")
    .replace("import { compareMatch, domainBadge, domainClass } from './career-fit.js';\n", fitSource.endsWith('\n') ? fitSource : fitSource + '\n');
  vm.runInContext(script, context);
  const board = new Board();
  board.data = {running:true}; board.hidden = false;
  board.selected = new Set(['selected-role']); board.busy = false;
  board.paintStatus = () => {};
  return board;
}

test('split UTF-8 progress reaches the UI before completion without changing a selection', async () => {
  let controller, painted = [], refreshed = 0, headers;
  const body = new ReadableStream({start: c => controller = c});
  const board = harness(async (_, opts) => {
    headers = opts.headers;
    return new Response(body, {headers:{'content-type':'text/event-stream'}});
  });
  board.paintStatus = () => painted.push(board.data.progress.events[0].message);
  board.refresh = async () => {refreshed++; board.data.running = false;};
  const running = board.streamProgress();
  const message = 'Searched: Seattle — developer tools';
  const packet = new TextEncoder().encode('data: ' + JSON.stringify({running:true,run_id:'run',events:[{seq:1,message}],active_search:{}}) + '\n\n');
  for (const byte of packet) controller.enqueue(Uint8Array.of(byte));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(painted, [message], 'A live update must arrive while the stream is still open');
  assert.deepEqual([...board.selected], ['selected-role']);
  assert.equal(headers.Authorization, 'Bearer test');
  controller.enqueue(new TextEncoder().encode('data: {"running":false,"events":[]}\n\n'));
  await running;
  assert.equal(refreshed, 1);
  assert.equal(board.progressController, null);
});

test('a broken connection releases the stream so polling can reconnect', async () => {
  let attempts = 0;
  const board = harness(async () => { attempts++; throw Error('offline'); });
  await board.streamProgress();
  assert.equal(board.progressController, null);
  await board.streamProgress();
  assert.equal(attempts, 2);
  assert.deepEqual([...board.selected], ['selected-role']);
});

test('hidden and idle search pages do not open streaming connections', async () => {
  const board = harness(async () => { throw Error('Should not fetch'); });
  board.hidden = true;
  await board.streamProgress();
  board.hidden = false; board.data.running = false;
  await board.streamProgress();
  assert.equal(board.progressController, undefined);
});

test('scanner startup failures name the cause instead of looking like empty results', () => {
  const board = harness(async () => {});
  board.data = {running:false, error:'', jobs:[], last_network:{kind:'network', found:0,
    read:0, unreachable:0, complete:false, errors:[
      {company:'claude', error:'Search timed out'},
      {company:'Scanner', error:'Cannot find module scan-ats-full.mjs'}
    ]}};
  board.getAttribute = () => '';
  board.$ = () => ({value:'network', setAttribute:()=>{}});
  board.paintNetwork = board.paintProgress = () => {};
  board.rows = () => [];
  let message, error;
  board.message = (text, failed) => {message=text; error=failed};
  Object.getPrototypeOf(board).paintStatus.call(board);
  assert.match(message, /Scan failed: Cannot find module/);
  assert.match(message, /Claude search: Search timed out/);
  assert.doesNotMatch(message, /Partial scan/);
  assert.equal(error, true);
});
test('search activity groups repeats and separates actual matches from zero-match messages', () => {
  const board = harness(async () => {});
  const events = [
    {seq:1,at:'first',message:'Example: 0 postings read · 0 matches · HTTP 422 Unprocessable Entity'},
    {seq:2,at:'second',message:'Example: 0 postings read · 0 matches · HTTP 422 Unprocessable Entity'},
    {seq:3,at:'third',message:'Other: 200 postings read · 0 matches'},
    {seq:4,at:'fourth',message:'Sample: 20 postings read · 2 matches'},
  ];
  const all = board.progressRows(events);
  assert.equal(all.length,3);
  assert.equal(all[0].seq,4);
  assert.equal(all[2].count,2);
  assert.equal(all[2].at,'second');
  assert.equal(board.progressRows(events,'issues').length,1);
  const matches = board.progressRows(events,'matches');
  assert.equal(matches.length,1);
  assert.equal(matches[0].seq,4);
  assert.equal(events.length,4, 'Display grouping must not change the stored event history');
});
test('matching jobs exclude confirmed applications without removing their history', () => {
  const board = harness(async () => {});
  board.getAttribute = () => '';
  board.data.jobs = [
    {id:'new', state:'new'},
    {id:'done', pipeline_status:'applied'},
    {id:'manual', pipeline_status:'applied_manual'},
    {id:'older', application:{phase:'applied'}},
    {id:'waiting', application:{phase:'apply',status:'submitting'}},
  ];
  assert.deepEqual(Array.from(board.rows(),r=>r.id),['new','waiting']);
  assert.equal(board.data.jobs.length,5);
});

test('application groups separate active work, skipped roles and confirmed submissions', () => {
  const board = harness(async () => {});
  const groups = board.applicationGroups([
    {id:'applied',application:{phase:'applied',status:'applied'}},
    {id:'skipped',application:{phase:'attention',status:'skipped'}},
    {id:'applying',application:{phase:'apply',status:'submitting'}},
    {id:'unknown',application:{phase:'unknown'}},
  ]);
  assert.deepEqual(Array.from(groups,g=>g.key),['apply','attention','skipped','applied']);
  assert.equal(groups[0].items[0].id,'applying');
});
test('role receipts use durable submission status and never mistake a request for an application', () => {
  const board = harness(async () => {});
  const row = {pipeline_status:'submitting', application:{phase:'applied', requested_at:'2026-09-14'}};
  const pending = board.receiptHtml(row,{status:'submitting',match_score:8},'');
  assert.match(pending,/Applying in browser/);
  assert.match(pending,/Not confirmed/);
  assert.match(pending,/Requested on/);
  assert.doesNotMatch(pending,/Applied on|Application confirmed/);
  const done = board.receiptHtml(row,{status:'applied',applied_at:'2026-09-14'},'/artifact/resume.pdf');
  assert.match(done,/Application confirmed/);
  assert.match(done,/Applied on/);
  assert.match(done,/Open résumé/);
  const missingDate = board.receiptHtml(row,{status:'applied'},'');
  assert.match(missingDate,/No date recorded/);
  assert.doesNotMatch(missingDate,/Requested on/);
});

test('best match is the default and orders AI, IT, unclassified, then Not IT', () => {
  assert.match(source, /<option value="match" selected>Best match<\/option>/);
  const board = harness(async () => {});
  board.getAttribute = () => '';
  board.page = 1;
  board.selected = new Set();
  board.detailId = '';
  board.wide = () => false;
  board.paintSelection = () => {};
  const nodes = new Map();
  board.$ = sel => {
    if (!nodes.has(sel)) nodes.set(sel, {value: sel === '.cb-sort' ? 'match' : 'all', textContent: '', innerHTML: '', hidden: false, disabled: false, classList: {toggle() {}}});
    return nodes.get(sel);
  };
  board.data = {jobs: [
    {id:'other', title:'Health', company:'Acme', state:'new', domain:'other', domain_reason:'clinical "program"', fit_score:9, posted_at:'2026-10-01'},
    {id:'ai-low', title:'ML', company:'Acme', state:'new', domain:'ai', fit_score:1, posted_at:'2026-09-01'},
    {id:'plain', title:'PM', company:'Acme', state:'new', fit_score:8, posted_at:'2026-10-02'},
    {id:'it', title:'Platform', company:'Acme', state:'new', domain:'it', fit_score:3, posted_at:'2026-08-01'},
    {id:'ai-old', title:'Agent', company:'Acme', state:'new', domain:'ai', fit_score:4, posted_at:'2026-01-01'},
    {id:'ai-new', title:'Agent later', company:'Acme', state:'new', domain:'ai', fit_score:4, posted_at:'2026-06-01'},
  ]};
  board.renderRows();
  const ids = () => [...nodes.get('.cb-list').innerHTML.matchAll(/data-id="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(ids(), ['ai-new', 'ai-old', 'ai-low', 'it', 'plain', 'other']);
  const html = nodes.get('.cb-list').innerHTML;
  const articles = Object.fromEntries(html.split('<article ').slice(1).map(chunk => {
    const id = chunk.match(/data-id="([^"]+)"/)[1];
    return [id, chunk];
  }));
  assert.match(articles['ai-new'], /cb-domain-ai">AI</);
  assert.doesNotMatch(articles.it, /cb-domain/);
  assert.doesNotMatch(articles.plain, /cb-domain/);
  assert.match(articles.other, /is-other/);
  assert.match(articles.other, /cb-domain-other" title="clinical &quot;program&quot;">Not IT</);
  nodes.get('.cb-sort').value = 'posted';
  board.renderRows();
  assert.deepEqual(ids(), ['plain', 'other', 'ai-low', 'it', 'ai-new', 'ai-old']);
});
