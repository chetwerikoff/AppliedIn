import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scanNetwork} from '../../scripts/integrations/career-network.mjs';
const input = {ats:['ashby'], cutoff:'2026-01-01', include_undated:true, limit:2, cursor:{}};
function setup(fetch) {
 const events=[];
 return {events, deps:{sources:{ashby:{toEntry:name=>({name}), concurrency:2, provider:{fetch}}}, loadList:async()=>({list:['A','B','C','D'],status:'ok'}), makeContext:()=>({}), matches:()=>true, emit:e=>events.push(e)}};
}
test('continue visits the next companies rather than sampling the same prefix', async()=>{
 const visited=[]; const {events,deps}=setup(async entry=>{visited.push(entry.name); return []});
 await scanNetwork(input,deps);
 const cursor=events.filter(e=>e.kind==='company').at(-1).cursor;
 await scanNetwork({...input,cursor:{ashby:cursor}},deps);
 assert.deepEqual(visited,['A','B','C','D']);
});
test('out of order completions never checkpoint past unfinished company',async()=>{
 let finishA; const blocked=new Promise(r=>finishA=r);
 const {events,deps}=setup(async entry=>{if(entry.name==='A')await blocked; return []});
 const run=scanNetwork(input,deps);
 await new Promise(r=>setImmediate(r));
 assert.equal(events.find(e=>e.company==='B').cursor.next,0);
 finishA(); await run;
 assert.equal(events.filter(e=>e.kind==='company').at(-1).cursor.next,2);
});
test('unknown dates are explicit and all matches are retained beyond the old 15 job cap',async()=>{
 const jobs=Array.from({length:25},(_,i)=>({title:'Engineer',url:`https://example.com/jobs/${i}`}));
 const {events,deps}=setup(async()=>jobs);
 await scanNetwork({...input,limit:1},deps);
 assert.equal(events.find(e=>e.kind==='company').jobs.length,25);
 const other=setup(async()=>jobs);
 await scanNetwork({...input,limit:1,include_undated:false},other.deps);
 assert.equal(other.events.find(e=>e.kind==='company').undated,25);
});
test('changed dataset invalidates offsets and unreachable boards do not discard others',async()=>{
 const {events,deps}=setup(async entry=>{if(entry.name==='A')throw Error('HTTP 404'); return [{title:'Engineer'}]});
 await scanNetwork({...input,cursor:{ashby:{hash:'old',next:2}}},deps);
 assert.ok(events.find(e=>e.kind==='notice'));
 assert.equal(events.find(e=>e.company==='A').error,'HTTP 404');
 assert.equal(events.find(e=>e.company==='B').jobs.length,1);
});
