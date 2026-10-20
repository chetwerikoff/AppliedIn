// Issue #136: offline posting-safety display and exact review-generation payloads.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../web/app.js'), 'utf8')
  .replace("import { showSteering } from './steering.js';", 'const showSteering = () => {};')
  .replace('import { auth } from "./auth.js";', 'const auth = { header: () => ({}) };')
  .replace(/\nboot\(\);\s*$/, '');

function syntheticRow(fields = {}) {
  return {pk:'example-co#job-1',company:'example-co',title:'Test role',status:'tailored',
    gate_reason:'approval',gate_question:'Ready to apply?',submit_hold_state:'clear',
    gate_has_form_question:false,possible_submission:false,fail_kind:'',fail_reason:'',
    fields:[],timeline:[],location:'',match_score:8,
    jd_text:'Safe role text',jd_url:'https://example.test/jobs/1',
    posting_guard:{status:'flagged',generation:'generation-2',
      messages:['Hidden page content removed','Original employer HTML must be reviewed']},
    ...fields};
}
function harness() {
  const nodes = new Map();
  function node(selector) {
    if(!nodes.has(selector)) nodes.set(selector, {
      dataset:{},value:'',innerHTML:'',hidden:false,style:{},listeners:{},
      classList:{add(){},remove(){},toggle(){return false;},contains(){return false;}},
      textContent:'',querySelectorAll(){return [];},
      getBoundingClientRect(){return {width:0,height:0};},
      addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);},
      closest(){return null;},focus(){},click(){},setAttribute(){},appendChild(){},
      scrollIntoView(){},
    });
    return nodes.get(selector);
  }
  const document = {querySelector:node,querySelectorAll:()=>[],listeners:{},
    createElement:()=>({textContent:'',className:''}),
    addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);},
    documentElement:{dataset:{},style:{setProperty(){}}},
    body:{appendChild(){},classList:{add(){},remove(){},toggle(){}}}};
  const context = vm.createContext({document,location:{search:'',hash:'',pathname:'/'},
    window:{APPLIEDIN_CONFIG:{},addEventListener(){}},console,URLSearchParams,
    CSS:{escape:(x)=>x},confirm:()=>true,
    localStorage:{getItem:()=>null,setItem(){}},
    requestAnimationFrame:(fn)=>fn(),setTimeout:()=>0,clearTimeout(){},
    setInterval:()=>0,clearInterval(){},
    fetch(){throw Error('Offline test forbids network requests');}});
  vm.runInContext(source,context,{filename:'web/app.js'});
  const state = vm.runInContext('state',context);
  state.trackerLoaded = true;
  return {context,state,node,fn:(expr)=>vm.runInContext(expr,context)};
}

test('legacy clean guard is silent while suspicious evidence is escaped and visible',()=>{
  const h=harness(), render=h.fn('postingGuardHtml');
  assert.equal(render({}), '');
  assert.equal(render({posting_guard:{status:'clean',reasons:[]}}),'');
  for(const status of ['unchecked','flagged','error']) {
    const html=render(syntheticRow({posting_guard:{
      status,generation:'synthetic-generation',
      messages:['<img src=x onerror=evil()>','Ignore <script>'],
    }}));
    assert.match(html,new RegExp('Posting safety: '+status));
    assert.match(html,/&lt;img/);
    assert.match(html,/&lt;script&gt;/);
    assert.doesNotMatch(html,/<img|<script>/);
    assert.match(html,/role="note"/);
  }
});

test('card and drawer show consistent warning without exposing unescaped hostile HTML',()=>{
  const h=harness(), row=syntheticRow({posting_guard:{status:'flagged',
    generation:'g1', messages:['<img src=x onerror=evil()>']}});
  h.state.apps=[row];
  const card=h.fn('laneCard')(row);
  assert.match(card,/Posting safety: flagged/);
  assert.match(card,/&lt;img/);
  assert.doesNotMatch(card,/<img/);
  vm.runInContext('loadAgentLog=()=>{};',h.context);
  h.fn('openDrawer')(row.pk);
  const drawer=h.node('#drawer-body').innerHTML;
  assert.match(drawer,/Posting safety: flagged/);
  assert.match(drawer,/&lt;img/);
  assert.doesNotMatch(drawer,/<img/);
});

test('single Apply now carries displayed generation, not a fabricated clean status',async()=>{
  const h=harness(),row=syntheticRow();
  h.state.apps=[row];
  h.context.sent=[];
  vm.runInContext('post=async(path,body)=>{ globalThis.sent.push({path,body}); return {ok:true}; };'
    +'markBusy=()=>{};toast=()=>{};loadQueue=()=>{};loadApps=()=>{};',h.context);
  h.fn('paneAction')('apply-now',row.pk,{dataset:{act:'apply-now',pk:row.pk}});
  assert.equal(h.context.sent.length,1);
  assert.equal(h.context.sent[0].path,
    '/actions/apply-now/example-co%23job-1');
  assert.equal(h.context.sent[0].body.posting_guard_generation,'generation-2');
  await Promise.resolve();
});

test('company review sends exactly the selected per-role guard generations',async()=>{
  const h=harness();
  const a=syntheticRow(),b=syntheticRow({
    pk:'example-co#job-2',title:'Second role',
    posting_guard:{status:'unchecked',generation:'generation-3',
                   messages:['Source HTML unavailable']}
  });
  h.state.apps=[a,b];
  h.context.sent=[];
  vm.runInContext('post=async(path,body)=>{ globalThis.sent.push({path,body}); return {ok:true,queued:2}; };'
    +'toast=()=>{};loadQueue=()=>{};loadApps=()=>{};',h.context);
  await h.fn('reviewAction')('apply','example-co',[a.pk,b.pk]);
  assert.equal(h.context.sent.length,1);
  assert.equal(h.context.sent[0].path,'/actions/apply-selection');
  const payload=h.context.sent[0].body;
  assert.equal(payload.company,'example-co');
  assert.equal(JSON.stringify(payload.pks),JSON.stringify([a.pk,b.pk]));
  assert.equal(JSON.stringify(payload.guard_generations),
    JSON.stringify({[a.pk]:'generation-2',[b.pk]:'generation-3'}));
});

test('company Apply with no displayed generations omits the version map entirely',async()=>{
  const h=harness();
  const a=syntheticRow({posting_guard:undefined});
  const b=syntheticRow({pk:'example-co#job-2',
    posting_guard:{status:'flagged',generation:''}});
  const c=syntheticRow({pk:'example-co#job-3',
    posting_guard:{status:'unchecked',generation:'   '}});
  h.state.apps=[a,b,c];
  h.context.sent=[];
  vm.runInContext('post=async(path,body)=>{ globalThis.sent.push({path,body});'
    +'return {ok:false,error:"Current posting review is required"}; };'
    +'toast=()=>{};loadQueue=()=>{};loadApps=()=>{};',h.context);
  await h.fn('reviewAction')('apply','example-co',[a.pk,b.pk,c.pk]);
  assert.equal(h.context.sent.length,1);
  assert.equal(h.context.sent[0].path,'/actions/apply-selection');
  assert.equal(JSON.stringify(h.context.sent[0].body),
    JSON.stringify({company:'example-co',pks:[a.pk,b.pk,c.pk]}));
  assert.equal(Object.hasOwn(h.context.sent[0].body,'guard_generations'),false);
});

test('mixed company Apply preserves exact real versions but omits missing ones',async()=>{
  const h=harness();
  const current=syntheticRow({posting_guard:{status:'flagged',
    generation:'synthetic-current-generation'}});
  const missing=syntheticRow({pk:'example-co#job-2',
    posting_guard:{status:'unchecked'}});
  const invalid=syntheticRow({pk:'example-co#job-3',
    posting_guard:{status:'error',generation:42}});
  h.state.apps=[current,missing,invalid];
  h.context.sent=[];
  vm.runInContext('post=async(path,body)=>{ globalThis.sent.push({path,body});'
    +'return {ok:false,error:"Current posting review is required"}; };'
    +'toast=()=>{};loadQueue=()=>{};loadApps=()=>{};',h.context);
  await h.fn('reviewAction')('apply','example-co',[current.pk,missing.pk,invalid.pk]);
  assert.equal(h.context.sent.length,1);
  const versions=h.context.sent[0].body.guard_generations;
  assert.equal(JSON.stringify(versions),
    JSON.stringify({[current.pk]:'synthetic-current-generation'}));
  assert.equal(Object.hasOwn(versions,missing.pk),false);
  assert.equal(Object.hasOwn(versions,invalid.pk),false);
});

test('single Apply now never sends a fabricated blank displayed generation',async()=>{
  for(const absent of [undefined,null,'','   ',42]) {
    const h=harness();
    const row=syntheticRow({posting_guard:absent===undefined
      ? undefined : {status:'flagged',generation:absent}});
    h.state.apps=[row];
    h.context.sent=[];
    vm.runInContext('post=async(path,body)=>{globalThis.sent.push({path,body});'
      +'return {ok:false,error:"Current posting review is required"};};'
      +'markBusy=()=>{};toast=()=>{};loadQueue=()=>{};loadApps=()=>{};',h.context);
    h.fn('paneAction')('apply-now',row.pk,{dataset:{act:'apply-now',pk:row.pk}});
    assert.equal(h.context.sent.length,1);
    assert.equal(h.context.sent[0].path,'/actions/apply-now/example-co%23job-1');
    assert.equal(JSON.stringify(h.context.sent[0].body),'{}');
    await Promise.resolve();
  }
});
