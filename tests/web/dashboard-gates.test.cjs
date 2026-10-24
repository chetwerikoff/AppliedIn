// Issue #121: typed, read-only gate routing in an offline Node VM.
// No browser, network, application profile, queue or owner data is involved.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8')
  .replace("import { showSteering } from './steering.js';", 'const showSteering = () => {};')
  .replace('import { auth } from "./auth.js";', 'const auth = { header: () => ({}) };')
  .replace(/\nboot\(\);\s*$/, '');

function row(pk, fields = {}) {
  return { pk, company: 'example-co', title: 'Synthetic role', status: 'needs_human',
    gate_reason: 'owner_question', gate_question: 'What is your preferred location?',
    submit_hold_state: 'clear', possible_submission: false, fail_kind: '',
    gate_has_form_question: fields.gate_reason == null || fields.gate_reason === 'owner_question',
    fail_reason: '', fields: [], timeline: [], location: '',
    match_score: 7, jd_url: 'https://example.test/jobs/1', ...fields };
}
function harness() {
  const nodes = new Map(), requests = [];
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset: {}, value: '', innerHTML: '', hidden: false, style: {},
      classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
      listeners: {}, textContent: '', querySelectorAll() { return []; },
      getBoundingClientRect() { return { width: 0, height: 0 }; },
      addEventListener(event, callback) { (this.listeners[event] ||= []).push(callback); },
      closest() { return null; }, focus() {}, click() {}, setAttribute() {}, appendChild() {},
      scrollIntoView() {},
    });
    return nodes.get(selector);
  };
  const document = { querySelector: node, querySelectorAll: () => [],
    listeners: {}, createElement: () => ({textContent: '', className: ''}),
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    documentElement: { dataset: {}, style: {setProperty() {}} },
    body: { appendChild() {}, classList: {add() {}, remove() {}, toggle() {}} } };
  const context = vm.createContext({ document, location: {search: '', hash: '', pathname: '/'},
    window: {APPLIEDIN_CONFIG: {}, addEventListener() {}}, console, URLSearchParams,
    CSS: {escape: (v) => v}, confirm: () => true, localStorage: {getItem: () => null, setItem() {}},
    requestAnimationFrame: (fn) => fn(), setTimeout: () => 0, clearTimeout() {},
    setInterval: () => 0, clearInterval() {},
    fetch(...args) { requests.push(args); throw Error('Offline test forbids fetch'); } });
  vm.runInContext(source, context, { filename: 'web/app.js' });
  const state = vm.runInContext('state', context);
  state.trackerLoaded = true;
  return { state, context, node, requests, document,
    fn: (name) => vm.runInContext(name, context) };
}

test('submit hold and raw uncertainty override every lower-priority status', () => {
  const h = harness(), route = h.fn('routeOf');
  for (const status of ['needs_human','tailored','submitting','applied','applied_manual','skipped']) {
    assert.equal(route(row('held', {status, submit_hold_state: 'blocked'})), 'portal_check');
    assert.equal(route(row('unknown', {status, submit_hold_state: 'unknown'})), 'portal_check');
    assert.equal(route(row('missing', {status, submit_hold_state: undefined})), 'portal_check');
    assert.equal(route(row('raw', {status, possible_submission: true})), 'portal_check');
    assert.equal(route(row('legacy', {status, fail_kind: 'uncertain'})), 'portal_check');
    assert.equal(route(row('legacy-gate', {status, gate_reason: 'submit_uncertain'})), 'portal_check');
  }
  assert.equal(route(row('uncertain', {status:'uncertain'})), 'portal_check');
});

test('typed active causes do not borrow authority from prose or stale codes', () => {
  const h = harness(), route = h.fn('routeOf');
  assert.equal(route(row('answer')), 'owner_question');
  for (const cause of ['defect_structural','defect_provenance','defect_tenant',
    'defect_witness','defect_ashby_root_identity','defect_greenhouse_tenant_alias'])
    assert.equal(route(row('defect', {gate_reason: cause})), 'waiting_for_fix');
  for (const cause of ['unknown_field', 'NOT_A_REAL_CODE', '', null])
    assert.equal(route(row('generic', {gate_reason: cause, gate_question: 'Ready to apply'})), 'manual_triage');
  assert.equal(route(row('missing-question', {gate_question:''})), 'manual_triage');
  assert.equal(route(row('bad-status', {status:'unexpected'})), 'manual_triage');
  assert.equal(route(row('contradictory', {fail_kind:'failed'})), 'manual_triage');
  assert.equal(route(row('old-defect', {status:'applied', gate_reason:'defect_structural'})), 'applied');
  assert.equal(route(row('old-answer', {status:'tailored'})), 'tailored');
  for (const cause of ['approval','low_confidence','captcha','no_account'])
    assert.equal(route(row(cause, {gate_reason:cause})), cause);
});

test('fixed Issue URLs are exact, unmapped text cannot invent a link', () => {
  const h = harness(), notice = h.fn('routeNotice');
  const ashby = notice(row('ashby', {gate_reason:'defect_ashby_root_identity'}));
  const gh = notice(row('gh', {gate_reason:'defect_greenhouse_tenant_alias'}));
  assert.match(ashby, /href="https:\/\/github\.com\/chetwerikoff\/AppliedIn\/issues\/120"/);
  assert.match(gh, /href="https:\/\/github\.com\/chetwerikoff\/AppliedIn\/issues\/118"/);
  const untrusted = notice(row('unknown-defect', {
    gate_reason:'defect_structural',
    gate_question:'<img src=x onerror=evil()> https://github.com/elsewhere/issues/999',
  }));
  assert.match(untrusted, /Fix Issue not linked/);
  assert.doesNotMatch(untrusted, /<img|href=.*999/);
  assert.match(untrusted, /&lt;img/);
  const held = notice(row('held', {status:'applied', submit_hold_state:'blocked',
    fail_reason:'Unresolved synthetic click'}));
  assert.match(held, /Check the employer portal/);
  assert.match(held, /before any separate later attempt/);
  assert.match(held, /Unresolved synthetic click/);
});

test('Need, portal, fix and triage sections are exclusive, with correct actions', () => {
  const h = harness(), section = h.fn('sectionOf'), cards = h.fn('needsCard');
  const owner = row('owner');
  const approval = row('approval', {gate_reason:'approval'});
  const captcha = row('captcha', {gate_reason:'captcha'});
  const low = row('low', {gate_reason:'low_confidence'});
  const fix = row('fix', {gate_reason:'defect_structural'});
  const portal = row('portal', {status:'applied',submit_hold_state:'blocked'});
  const generic = row('generic', {gate_reason:'unknown_field'});
  h.state.apps = [owner, approval, captcha, low, fix, portal, generic];
  const routes = h.state.apps.map((r) => section(r, new Set()));
  assert.deepEqual(routes, ['needs','ready','needs','needs','fix','portal','triage']);
  assert.match(cards(owner), /data-answer-for="owner"/);
  assert.match(cards(owner), /data-act="answer"/);
  assert.doesNotMatch(cards(captcha), /data-act="answer"|data-answer-for/);
  assert.doesNotMatch(cards(low), /data-answer-for=/);
  assert.match(cards(low), /Review &amp; approve/);
  for (const r of [fix, portal, generic]) {
    assert.match(cards(r), /View details/);
    assert.doesNotMatch(cards(r), /data-act=|textarea|data-reapply-prof|data-save-tracker/);
  }
  assert.equal(h.fn('approveAllPicks')(approval), true);
  for (const r of [owner,captcha,fix,portal,generic])
    assert.equal(h.fn('approveAllPicks')(r), false);
  const pipe = h.fn('viewPipeline')();
  assert.match(pipe, /Portal verification/);
  assert.match(pipe, /Waiting for fix/);
  assert.match(pipe, /Manual triage/);
});

test('read-only rows have no drawer mutation controls and stale delegated clicks do not POST', () => {
  const h = harness();
  h.state.apps = [row('held', {submit_hold_state:'blocked',status:'applied'}),
    row('fix', {gate_reason:'defect_structural'}),
    row('unknown', {gate_reason:'unknown_field'})];
  vm.runInContext('loadAgentLog = () => {};', h.context);
  for (const r of h.state.apps) {
    h.fn('openDrawer')(r.pk);
    const html = h.node('#drawer-body').innerHTML;
    assert.match(html, /read-only|Read-only|reconcile|triage|fix/i);
    assert.doesNotMatch(html, /data-act=|data-job-profile=|data-reapply-prof=|data-save-tracker=|data-track=/);
  }
  h.fn('wire')();
  const clicks = h.node('#pane').listeners.click;
  assert.ok(clicks?.length);
  const baseline = h.requests.length;
  for (const pk of ['held','fix','unknown']) {
    for (const act of ['answer','retry','reopen','retailor','apply-now','queue-apply',
      'mark-applied','skip','cancel','force-apply']) {
      const control = { dataset: { act, pk } };
      const target = { closest: (selector) => selector === '[data-act]' ? control : null };
      clicks.at(-1)({target,stopPropagation() {}});
      assert.equal(h.requests.length, baseline, `${pk}:${act} must not POST`);
    }
  }
});

test('read-only causes have the same no-action surface in every dashboard view', () => {
  for (const sample of [
    row('held-applied', {status: 'applied', submit_hold_state: 'blocked'}),
    row('held-approval', {gate_reason: 'approval', submit_hold_state: 'unknown'}),
    row('held-question', {possible_submission: true}),
    row('old-uncertain', {status: 'uncertain', gate_reason: 'unknown_field'}),
    row('fix', {gate_reason: 'defect_witness'}),
    row('manual', {gate_reason: 'unknown_field', gate_question: '<img src=x>'}),
  ]) {
    const h = harness();
    h.state.apps = [sample];
    h.state.coFilter = 'example-co';
    h.state.queue = {pending: [{pk: sample.pk, company: 'example-co'}], concurrency: 1};
    const category = h.fn('routeOf')(sample);
    for (const name of ['viewPipeline', 'viewNeeds', 'viewStuck', 'viewApps']) {
      const html = h.fn(name)();
      if (name !== 'viewApps')
        assert.match(html, /Portal verification|Waiting for fix|Manual triage/i, sample.pk + '/' + name);
      assert.doesNotMatch(html,
        /data-act=|data-answer-for=|class="gate-input"|data-review-action=|data-qsel=|data-approve-all=/,
        sample.pk + '/' + name);
    }
    vm.runInContext('loadAgentLog = () => {};', h.context);
    h.fn('openDrawer')(sample.pk);
    const drawer = h.node('#drawer-body').innerHTML;
    assert.match(drawer, /Read-only until this blocker|Portal verification|code fix|Manual triage/i);
    assert.doesNotMatch(drawer,
      /data-act=|data-job-profile=|data-reapply-prof=|data-save-tracker=|data-track=|class="gate-input"/);
    assert.equal(h.fn('sectionOf')(sample, new Set([sample.pk])),
      category === 'portal_check' ? 'portal' : category === 'waiting_for_fix' ? 'fix' : 'triage');
    assert.equal(h.requests.length, 0);
  }
});

test('only verified owner questions say Answer; approval and review are decisions', () => {
  const h = harness();
  const question = row('q');
  const approval = row('approval', {gate_reason: 'approval'});
  const low = row('low', {gate_reason: 'low_confidence'});
  assert.match(h.fn('gateMini')(question), />✎ Answer/);
  for (const decision of [approval, low]) {
    const mini = h.fn('gateMini')(decision);
    assert.doesNotMatch(mini, />✎ Answer|type an answer/);
    assert.match(mini, /Review approval|Review decision/);
    assert.doesNotMatch(h.fn('needsCard')(decision), /data-answer-for=|<textarea/);
  }
  h.state.apps = [question, approval, low];
  vm.runInContext('loadAgentLog = () => {};', h.context);
  for (const item of h.state.apps) {
    h.fn('openDrawer')(item.pk);
    const html = h.node('#drawer-body').innerHTML;
    if (item.pk === 'q') assert.match(html, /id="gate-answer"/);
    else assert.doesNotMatch(html, /id="gate-answer"/);
  }
});

test('company/search filters and stale queue selection preserve exclusive buckets', () => {
  const h = harness();
  const clear = row('clear', {status: 'tailored'});
  const held = row('held', {status: 'tailored', submit_hold_state: 'blocked'});
  const fix = row('fix', {gate_reason: 'defect_structural'});
  const triage = row('triage', {gate_reason: 'unknown_field'});
  const question = row('question');
  const other = row('other', {company: 'second-co', submit_hold_state: 'unknown'});
  h.state.apps = [clear, held, fix, triage, question, other];
  h.state.coFilter = 'example-co';
  h.state.query = 'Synthetic role';
  assert.deepEqual(Array.from(h.fn('visible')(h.state.apps), r => r.pk).sort(),
    ['clear', 'fix', 'held', 'question', 'triage']);
  const sections = Array.from(h.fn('visible')(h.state.apps), (r) => h.fn('sectionOf')(r, new Set()));
  assert.deepEqual(sections.sort(), ['fix','needs','portal','ready','triage']);
  h.state.queue = {pending: [], concurrency: 1};
  assert.deepEqual(Array.from(h.fn('visibleApprovalPicks')(), r => r.pk), ['clear']);
  h.state.queue.pending = [{pk:'held', company:'example-co'}, {pk:'clear', company:'example-co'}];
  h.state.qPicked = new Set(['held','clear']);
  const queueHtml = h.fn('queuedSec')(h.state.apps);
  assert.doesNotMatch(queueHtml, /data-qsel="held"/);
  assert.match(queueHtml, /data-qsel="clear"/);
  assert.doesNotMatch(queueHtml, /data-act="drain-co"|data-co-prof=|data-act="rot-co"/);
  assert.equal(h.fn('companyActionAllowed')('example-co'), false);
  assert.equal(h.state.qPicked.has('held'), false);
  assert.equal(h.state.qPicked.size, 1);
  h.state.queue.pending = [{pk:'held', company:'example-co'}];
  h.state.qPicked = new Set(['held']);
  assert.equal(h.fn('queuedSec')(h.state.apps), '');
  assert.equal(h.state.qPicked.size, 0);
  h.state.qPicked.add('held');
  h.fn('paneAction')('qsel-bulk', '', {dataset: {mode:'remove'}});
  assert.equal(h.requests.length, 0);
  assert.equal(h.fn('rowActionAllowed')('held', 'queue-apply'), false);
  assert.equal(h.fn('rowActionAllowed')('fix', 'answer'), false);
  assert.equal(h.fn('rowActionAllowed')('triage', 'retry'), false);
  assert.equal(h.fn('rowActionAllowed')('question', 'answer'), true);
});

test('the drawer delegate rejects stale action and tracker controls for held submissions', () => {
  const h = harness();
  h.state.apps = [row('held', {submit_hold_state: 'blocked', status: 'applied'})];
  h.fn('wire')();
  const drawer = h.node('#drawer');
  const listeners = drawer.listeners.click;
  assert.ok(listeners && listeners.length);
  // wire() performs unrelated initial reads; stale clicks must not add requests.
  const initialRequests = h.requests.length;
  vm.runInContext('saveTracker = () => { globalThis.trackerWrites = (globalThis.trackerWrites || 0) + 1; };', h.context);
  for (const hold of ['blocked', 'unknown']) {
    h.state.apps[0].submit_hold_state = hold;
    for (const act of ['answer','skip','cancel','retry','reopen','mark-applied',
      'force-apply','retailor','apply-now','queue-apply']) {
      assert.equal(h.fn('rowActionAllowed')('held', act), false, `${hold}:${act}`);
      const control = {dataset: {act, pk: 'held'}};
      const target = {closest: (selector) => selector === '[data-act]' ? control : null};
      for (const handler of listeners) handler({target, stopPropagation() {}});
      assert.equal(h.requests.length, initialRequests, `${hold}:${act}`);
    }
    assert.equal(h.fn('rowActionAllowed')('held', 'save-tracker'), false, hold);
    const save = {dataset: {saveTracker: 'held'}};
    const saveTarget = {closest: (selector) => selector === '[data-save-tracker]' ? save : null};
    for (const handler of listeners) handler({target: saveTarget, stopPropagation() {}});
    assert.equal(h.context.trackerWrites || 0, 0, `${hold}:save-tracker`);
    assert.equal(h.requests.length, initialRequests, `${hold}:save-tracker`);
    const input = {dataset: {track: 'outcome'}, value: 'synthetic',
      closest: (selector) => selector === '[data-tracker-pk]'
        ? {dataset: {trackerPk: 'held'}} : null};
    for (const handler of (drawer.listeners.input || [])) handler({target: input});
    assert.equal(h.state.trackerDrafts.held, undefined, `${hold}:tracker-input`);
  }
});


test('active owner questions require a projected native witness; contradictory defects triage', () => {
  const h = harness(), route = h.fn('routeOf');
  const bad = [row('missing', {gate_has_form_question:undefined}),
    row('false', {gate_has_form_question:false}),
    row('spoof', {gate_has_form_question:'true'}),
    row('contradictory-defect', {gate_reason:'defect_structural',gate_has_form_question:true})];
  for (const r of bad) {
    assert.equal(route(r),'manual_triage',r.pk);
    assert.equal(h.fn('sectionOf')(r,new Set()),'triage');
    assert.doesNotMatch(h.fn('needsCard')(r),/data-act="answer"|data-answer-for=/);
  }
  assert.equal(route(row('real')),'owner_question');
  assert.equal(route(row('real-defect',{gate_reason:'defect_structural'})),'waiting_for_fix');
  h.state.apps=bad;
  h.fn('wire')();
  const before=h.requests.length, onClick=h.node('#pane').listeners.click.at(-1);
  for (const item of bad) {
    assert.equal(h.fn('rowActionAllowed')(item.pk,'answer'),false);
    const ctrl={dataset:{act:'answer',pk:item.pk}};
    onClick({target:{closest:(s)=>s==='[data-act]'?ctrl:null},stopPropagation(){}});
    assert.equal(h.requests.length,before,'stale Answer must not post: '+item.pk);
  }
});

test('blank owner text never turns into approved in Needs-you or drawer', () => {
  const h=harness();
  h.state.apps=[row('q'),row('approval',{gate_reason:'approval'}),
    row('tailored',{status:'tailored',gate_reason:''})];
  h.context.sent=[];
  vm.runInContext('post = async (path, body) => { globalThis.sent.push({path,body}); return {ok:true}; };'
    + 'markBusy=()=>{}; loadQueue=()=>{}; renderPane=()=>{};'
    + 'toast=()=>{}; closeDrawer=()=>{}; loadAgentLog=()=>{};',h.context);
  const field=h.node('#pane textarea[data-answer-for="q"]');
  field.value='   ';
  h.fn('paneAction')('answer','q');
  assert.equal(h.context.sent.length,0,'blank pane answer must not POST');
  field.value='  synthetic location  ';
  h.fn('paneAction')('answer','q');
  assert.equal(h.context.sent.length,1);
  assert.equal(h.context.sent[0].body.answer,'synthetic location');
  h.fn('paneAction')('answer','approval');
  assert.equal(h.context.sent[1].body.answer,'approved','approval remains a decision');

  h.fn('wire')();
  const drawerClick=h.node('#drawer').listeners.click.at(-1);
  const invoke=(pk)=>{
    const ctrl={dataset:{act:'answer',pk}};
    drawerClick({target:{closest:(s)=>s==='[data-act]'?ctrl:null}});
  };
  h.node('#gate-answer').value='   ';
  invoke('q');
  assert.equal(h.context.sent.length,2,'blank drawer answer must not POST');
  h.node('#gate-answer').value='  synthetic schedule  ';
  invoke('q');
  assert.equal(h.context.sent[2].body.answer,'synthetic schedule');
  h.node('#gate-answer').value='';
  assert.equal(h.fn('rowActionAllowed')('tailored','answer'),true);
  h.fn('openDrawer')('tailored');
  assert.match(h.node('#drawer-body').innerHTML,/data-act="answer"/);
  invoke('tailored');
  assert.equal(h.context.sent[3].body.answer,'approved','tailored approval must work');
});

test('Process prepares a selected clean company despite a different company hold',async()=>{
  const h=harness();
  h.state.apps=[row('held',{company:'held-co',status:'tailored',submit_hold_state:'blocked'}),
    row('safe',{company:'clean-co',status:'found'})];
  h.state.companies=['held-co','clean-co'];
  h.state.picked=new Set(['clean-co']);
  h.context.sent=[];
  vm.runInContext('post = async (path, body) => { globalThis.sent.push({path,body}); return {ok:true}; };'
    + 'beginLaunch=()=>true; finishLaunch=()=>{}; renderDeck=()=>{};'
    + 'renderPane=()=>{}; closeAllPickers=()=>{}; pollStats=()=>{}; toast=()=>{};',h.context);
  await h.fn('runProcess')();
  assert.equal(h.context.sent.length,1);
  assert.equal(h.context.sent[0].path,'/actions/process');
  assert.equal(JSON.stringify(h.context.sent[0].body),'{"companies":["clean-co"]}');
  h.state.stats.processing=false;
  h.state.picked=new Set(h.state.companies);
  await h.fn('runProcess')();
  assert.equal(h.context.sent.length,1,'all-company scope must still protect held rows');
});

test('stale rotation and profile bulk controls cannot mutate held jobs',async()=>{
  const h=harness();
  h.state.apps=[row('held',{company:'Example-Co',status:'tailored',submit_hold_state:'blocked'})];
  h.state.rotation=[{company:'example-co',email:'synthetic@example.test',profile:'rot',
    used:0,limit:5,style:'plus'}];
  h.state.profiles=[{id:'standard',kind:'standard',label:'Test User',
    email:'synthetic@example.test',usage:{}},
    {id:'rot',kind:'rotating',label:'Synthetic rotating identity',
      email:'rotate@example.test',usage:{}}];
  h.state.detailCo='example-co';
  h.state.cprefs={};h.state.cpDrafts={};h.state.prefs={};
  assert.equal(h.fn('companyActionAllowed')('example-co'),false,'case-fold the company');
  h.fn('renderDetail')(true);
  assert.doesNotMatch(h.node('#cp-detail').innerHTML,/id="cp-dt-rotgo"/);
  assert.doesNotMatch(h.fn('viewProfiles')(),/data-prof-all=|data-act="rot-co"/);

  h.context.sent=[];
  vm.runInContext('post = async (path, body) => { globalThis.sent.push({path,body}); return {ok:true}; };'
    + 'toast=()=>{}; reload=()=>{}; loadQueue=()=>{}; loadApps=()=>{};'
    + 'loadProfiles=()=>{}; loadRotation=()=>{};',h.context);
  // wire() starts offline initial profile reads; restore the synthetic bulk scope.
  const rotation=h.state.rotation, profiles=h.state.profiles;
  h.fn('wire')();
  h.state.rotation=rotation; h.state.profiles=profiles;
  const rotate=h.node('#btn-rotate-all').listeners.click.at(-1);
  const company=h.node('#cp-detail').listeners.click.find(fn=>fn.toString().includes('#cp-dt-rotgo'));
  const profile=h.document.listeners.click.find(fn=>fn.toString().includes('data-prof-all'));
  assert.ok(rotate&&company&&profile);
  const cp={dataset:{co:'example-co'},classList:{add(){},remove(){}},textContent:''};
  const prof={dataset:{profAll:'standard'},textContent:'Whole board'};
  await rotate();
  await company({target:{closest:(s)=>s==='#cp-dt-rotgo'?cp:null}});
  profile({target:{closest:(s)=>s==='[data-prof-all]'?prof:null}});
  assert.equal(h.context.sent.length,0,'stale held bulk clicks must not POST');

  h.state.apps=[row('clear',{company:'Example-Co',status:'tailored'})];
  h.fn('renderDetail')(true);
  assert.match(h.node('#cp-detail').innerHTML,/id="cp-dt-rotgo"/);
  assert.match(h.fn('viewProfiles')(),/data-prof-all=/);
  await rotate();
  await company({target:{closest:(s)=>s==='#cp-dt-rotgo'?cp:null}});
  profile({target:{closest:(s)=>s==='[data-prof-all]'?prof:null}});
  assert.deepEqual(Array.from(h.context.sent,x=>x.path),[
    '/actions/rotate-and-approve','/actions/rotate-and-approve',
    '/actions/apply-profile-to-all']);
});

test('posting-safety warning survives read-only portal hold and remains escaped',()=>{
  const h=harness();
  const guarded=row('held-pg',{status:'tailored',submit_hold_state:'blocked',
    posting_guard:{status:'flagged',generation:'untrusted-guard',
      messages:['<svg onload=evil()>','Hidden HTML removed']}});
  h.state.apps=[guarded];
  assert.equal(h.fn('rowActionAllowed')(guarded.pk,'apply-now'),false);
  const html=h.fn('laneCard')(guarded);
  assert.match(html,/Posting safety: flagged/);
  assert.match(html,/&lt;svg/);
  assert.doesNotMatch(html,/<svg/);
  assert.doesNotMatch(html,/data-act="apply-now"/);
  vm.runInContext('loadAgentLog=()=>{};',h.context);
  h.fn('openDrawer')(guarded.pk);
  const drawer=h.node('#drawer-body').innerHTML;
  assert.match(drawer,/Posting safety: flagged/);
  assert.match(drawer,/&lt;svg/);
  assert.doesNotMatch(drawer,/data-act="apply-now"/);
});


test('non-resumable structural gates render the existing Requeue control without unlocking other actions', () => {
  const h = harness();
  const structural = row('structural-recovery', {
    gate_reason: 'defect_structural', gate_has_form_question: false,
    gate_has_call_id: false, gate_question: 'Synthetic structural gate',
  });
  h.state.apps = [structural];
  assert.equal(h.fn('routeOf')(structural), 'waiting_for_fix');
  assert.equal(h.fn('isReadOnly')(structural), true);
  assert.equal(h.fn('canRequeueStructuralGate')(structural), true);
  assert.equal(h.fn('rowActionAllowed')(structural.pk, 'queue-apply'), true);
  for (const act of ['answer', 'retry', 'reopen', 'retailor', 'apply-now',
    'mark-applied', 'force-apply', 'skip', 'save-tracker']) {
    assert.equal(h.fn('rowActionAllowed')(structural.pk, act), false, act);
  }
  for (const name of ['laneCard', 'closedRow', 'needsCard']) {
    assert.match(h.fn(name)(structural), /data-act="queue-apply"/, name);
  }
  for (const name of ['viewPipeline', 'viewNeeds', 'viewStuck']) {
    assert.match(h.fn(name)(), /data-act="queue-apply"/, name);
  }
  vm.runInContext('loadAgentLog = () => {};', h.context);
  h.fn('openDrawer')(structural.pk);
  const drawer = h.node('#drawer-body').innerHTML;
  assert.match(drawer, /data-act="queue-apply"/);
  assert.doesNotMatch(drawer, /data-act="answer"|data-act="mark-applied"|data-act="retailor"/);
  assert.equal(h.requests.length, 0, 'rendering and authorization must not queue anything');
});

test('structural Requeue latches the first delegated click before another can POST', () => {
  const h = harness();
  const structural = row('structural-once', {
    gate_reason: 'defect_structural', gate_has_form_question: false, gate_has_call_id: false,
  });
  h.state.apps = [structural];
  h.context.sent = [];
  // Mock the existing POST path: the regression must never fetch or queue a real job.
  vm.runInContext('post = (path) => { globalThis.sent.push(path); return Promise.resolve({ok:true}); };'
    + 'renderPane = () => {}; scheduleLive = () => {}; loadQueue = () => {}; toast = () => {};',
    h.context);
  h.fn('wire')();
  const click = h.node('#pane').listeners.click.at(-1);
  assert.ok(click);
  const control = {dataset: {act: 'queue-apply', pk: structural.pk}};
  const event = {target: {closest: (selector) => selector === '[data-act]' ? control : null},
    stopPropagation() {}};
  const requestsBefore = h.requests.length;
  assert.equal(h.fn('isReadOnly')(structural), true);
  assert.equal(h.fn('rowActionAllowed')(structural.pk, 'queue-apply'), true);

  click(event);
  assert.deepEqual(Array.from(h.context.sent), ['/actions/queue-apply/structural-once']);
  assert.equal(structural.status, 'submitting', 'first click must latch before POST completes');
  assert.equal(h.fn('rowActionAllowed')(structural.pk, 'queue-apply'), false);
  click(event);
  assert.equal(h.context.sent.length, 1, 'rapid delegated retry must never POST again');
  assert.equal(h.requests.length, requestsBefore, 'offline test must not fetch or queue a job');
});

test('structural Requeue fails closed for form/call witnesses, holds, uncertainty and completed rows', () => {
  const h = harness();
  const structural = {gate_reason: 'defect_structural',
    gate_has_form_question: false, gate_has_call_id: false};
  const negatives = [
    ['field-object', {gate_has_form_question: true}],
    ['call-id', {gate_has_call_id: true}],
    ['call-id-missing', {gate_has_call_id: undefined}],
    ['call-id-untyped', {gate_has_call_id: 'false'}],
    ['question-missing', {gate_has_form_question: undefined}],
    ['hold-blocked', {submit_hold_state: 'blocked'}],
    ['hold-unknown', {submit_hold_state: 'unknown'}],
    ['hold-missing', {submit_hold_state: undefined}],
    ['possible-submit', {possible_submission: true}],
    ['uncertain-failure', {fail_kind: 'uncertain'}],
    ['uncertain-status', {status: 'uncertain'}],
    ['submit-uncertain', {gate_reason: 'submit_uncertain'}],
    ['applied', {status: 'applied'}],
    ['applied-manual', {status: 'applied_manual'}],
    ['confirmed', {confirmation_id: 'synthetic-confirmation'}],
    ['unknown-reason', {gate_reason: 'unknown_field'}],
  ].map(([pk, override]) => row(pk, {...structural, ...override}));
  const field = row('answerable-field', {
    gate_reason: 'owner_question', gate_has_form_question: true,
    gate_has_call_id: true, gate_question: 'Synthetic field question?',
  });
  h.state.apps = [...negatives, field];
  for (const r of negatives) {
    assert.equal(h.fn('canRequeueStructuralGate')(r), false, r.pk);
    assert.equal(h.fn('rowActionAllowed')(r.pk, 'queue-apply'), false, r.pk);
    for (const name of ['laneCard', 'closedRow', 'needsCard'])
      assert.doesNotMatch(h.fn(name)(r), /data-act="queue-apply"/, r.pk + '/' + name);
    h.fn('paneAction')('queue-apply', r.pk);
  }
  assert.equal(h.requests.length, 0, 'stale negative clicks must not POST');
  assert.equal(h.fn('routeOf')(field), 'owner_question');
  assert.match(h.fn('needsCard')(field), /data-answer-for="answerable-field"/);
  assert.equal(h.fn('rowActionAllowed')(field.pk, 'answer'), true);
  assert.equal(h.fn('resumeAnswer')(field.pk, ' synthetic answer '), 'synthetic answer');
});


test('drawer delegates only eligible structural Requeue through the existing action', () => {
  const h = harness();
  const structural = row('drawer-structural', {
    gate_reason: 'defect_structural', gate_has_form_question: false, gate_has_call_id: false,
  });
  const withCall = row('drawer-call', {
    gate_reason: 'defect_structural', gate_has_form_question: false, gate_has_call_id: true,
  });
  h.state.apps = [structural, withCall];
  h.fn('wire')();
  const click = h.node('#drawer').listeners.click.at(-1);
  assert.ok(click);
  vm.runInContext('paneAction = (act, pk) => { (globalThis.drawerDelegations ||= []).push({act, pk}); };',
    h.context);
  const requestsBefore = h.requests.length;
  const invoke = (pk) => click({target: {closest: (selector) => selector === '[data-act]'
    ? {dataset: {act: 'queue-apply', pk}} : null}});
  invoke(structural.pk);
  assert.equal(h.context.drawerDelegations.length, 1);
  assert.equal(h.context.drawerDelegations[0].act, 'queue-apply');
  assert.equal(h.context.drawerDelegations[0].pk, structural.pk);
  invoke(withCall.pk);
  assert.equal(h.context.drawerDelegations.length, 1, 'stored call IDs deny stale drawer clicks');
  assert.equal(h.requests.length, requestsBefore, 'no network or queue mutations in the VM');
});
