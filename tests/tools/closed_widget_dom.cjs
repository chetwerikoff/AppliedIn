// Offline graph only: both literal production readers run against identical nodes.
const scenario = JSON.parse(process.argv[2]);
const nodes = [];
function make(tag,id,attrs={},parent=null,text='') {
  const n = {tagName:tag.toUpperCase(),id,nodeType:1,parentElement:parent,children:[],
    attrs, value:attrs.value || '',name:attrs.name || '',type:attrs.type || (tag==='input'?'text':tag==='button'?'submit':''),
    disabled:false,required:!!attrs.required,readOnly:false,checked:false,labels:[],
    getAttribute:k=>Object.hasOwn(attrs,k)?attrs[k]:null,hasAttribute:k=>Object.hasOwn(attrs,k),
    matches:q=>matches(n,q),contains:o=>{for(let p=o;p;p=p.parentElement)if(p===n)return true;return false;},
    closest:q=>{for(let p=n;p;p=p.parentElement)if(matches(p,q))return p;return null;},
    querySelectorAll:q=>nodes.filter(o=>o!==n&&n.contains(o)&&matches(o,q)),
    querySelector:q=>n.querySelectorAll(q)[0]||null,
    getClientRects:()=>attrs.hidden||n.type==='hidden'?[]:[{width:120,height:24,right:120,bottom:24}]};
  Object.defineProperty(n,'form',{get:()=>tag==='input'||tag==='button'||tag==='textarea'?n.closest('form'):undefined});
  Object.defineProperty(n,'innerText',{get:()=>text+n.children.map(c=>c.innerText).join('')});
  if(parent)parent.children.push(n);nodes.push(n);return n;
}
function matches(n,q){return q.split(',').some(s=>{
  s=s.trim();if(s==='*')return true;if(s===':disabled')return n.disabled;
  let neg=null;s=s.replace(/:not\(([^)]+)\)/g,(_,x)=>{neg=x;return '';});
  if(neg&&matches(n,neg))return false;
  if(s.startsWith('#'))return n.id===s.slice(1);
  const tag=s.match(/^[a-z][\w-]*/i);if(tag&&n.tagName!==tag[0].toUpperCase())return false;
  const cls=s.match(/\.([\w-]+)/);if(cls&&!(n.attrs.class||'').split(/\s+/).includes(cls[1]))return false;
  for(const a of s.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)){
    const value=a[1]==='type'?n.type:n.getAttribute(a[1]);
    if(value===null||value===undefined||(a[2]!==undefined&&value!==a[2]))return false;
  }
  return !!(tag||cls||s.startsWith('['));
});}
const root=make('html','root'),body=make('body','body',{},root);
const form=make('form','application',{},body);form.action='https://example-co.test/job/1';
const keys=['location','school','degree','discipline'];
for(const key of keys){
  const phase=scenario.phases?.[key]||'C0';
  const field=make('div',key+'-field',{},form);
  const label=make('label',key+'-label',{},field,key);
  const v=make('div',key+'-value',{},field);
  const w=make('div',key+'-input',{},v);
  const attrs={role:'combobox','aria-labelledby':label.id,'aria-expanded':phase==='C1'?'true':'false','aria-haspopup':'true','aria-autocomplete':'list'};
  const input=make('input',key,attrs,w);
  if(phase==='C1')attrs[scenario.activeOnly?'aria-activedescendant':'aria-controls']=key+(scenario.activeOnly?'-option':'-list');
  make('div',key+'-display',{class:phase==='C2'?'react-select__single-value':'react-select__placeholder'},v,phase==='C2'?(scenario.token||'Approved '+key):'Choose');
  if(!(scenario.noReceiver&&key==='location'))make('input',key+'-hidden',{type:'hidden',value:phase==='C2'?(scenario.hidden||'Approved '+key):''},scenario.sharedReceiver&&key==='location'?form:field);
  if(scenario.multipleReceiver&&key==='location')make('input','extra-hidden',{type:'hidden'},field);
  if(scenario.badGraph&&key==='location')attrs['aria-haspopup']='false';
  if(scenario.searchToken&&key==='location'){v.children.pop();input.value='Approved location';}
  if(key==='location'){
    if(scenario.drift==='field')field.id+='-drift';
    if(scenario.drift==='value')v.id+='-drift';
    if(scenario.drift==='wrapper')w.id+='-drift';
    if(scenario.drift==='label'){label.id+='-drift-label';attrs['aria-labelledby']=label.id;}
    if(scenario.extraInput)make('input','competing-input',{},w);
    if(scenario.noAutocomplete)delete attrs['aria-autocomplete'];
    if(scenario.wrongLabel)attrs['aria-labelledby']='missing-label';
    if(scenario.duplicateLabel)make('label',label.id,{},field,'Duplicate');
    if(scenario.explicitOutside)attrs['data-value-input']='school-hidden';
    if(scenario.invalidExpanded)attrs['aria-expanded']='mixed';
    if(scenario.displayQuery)v.children[1].attrs.class='ordinary-text';
  }
  if(phase==='C1'){
    const parent=scenario.danger==='ancestor'?make('a',key+'-anchor',{href:'/away'},body):body;
    if(parent.tagName==='A')parent.href='/away';
    const list=make('div',key+'-list',{role:'listbox'},parent);
    const option=make('div',key+'-option',{role:'option','data-value':scenario.code||'Approved '+key},list,scenario.caption||'Approved '+key);
    if(scenario.danger==='descendant')make('button',key+'-danger',{type:'submit'},option);
    if(scenario.conflict)attrs['aria-owns']='school-list';
    if(scenario.shared)make('input','other-owner',{role:'combobox','aria-controls':list.id},form);
    if(scenario.duplicate)make('div',list.id,{role:'listbox'},body);
    if(scenario.foreign){const other=make('form','foreign',{},body);list.parentElement=other;other.children.push(list);}
    if(scenario.nestedList)make('div','nested-list',{role:'listbox'},list);
    if(scenario.noPopup){nodes.splice(nodes.indexOf(list),1);nodes.splice(nodes.indexOf(option),1);}
  }
}
const iti=make('div','telephone',{class:'iti'},form);
make('input','phone',{type:'tel'},iti);
if(scenario.multipleTel)make('input','second-phone',{type:'tel'},iti);
const cc=make('div','countries',{class:'iti__country-container'},iti);
make('button','country-button',{type:scenario.unsafePicker?'submit':'button',class:'iti__selected-country'},cc);
const dd=make('div','dropdown',{class:'iti__dropdown-content'},iti);
make('input','country-search',{class:'iti__search-input',role:'combobox'},dd);
const list=make('ul','country-list',{class:'iti__country-list',role:'listbox'},dd);
make('li','country-option',{class:'iti__country',role:'option'},list,'Synthetic country');
if(scenario.extraPicker)make('input','extra-picker',{role:'combobox',required:true},iti);
if(scenario.nestedPicker)make('div','nested-picker',{class:'iti'},iti);
make('textarea','additional',{},form);
make('input','resume',{type:'file'},form);
make('button','submit',{type:'submit'},form,'Submit application');
for(let i=0;i<25;i++)make('input','filler-'+i,{},form);
global.document={readyState:'complete',title:'Synthetic',body,
  querySelectorAll:q=>nodes.filter(n=>matches(n,q)),querySelector:q=>nodes.find(n=>matches(n,q))||null,
  getElementById:id=>nodes.find(n=>n.id===id)||null};
global.location={href:form.action};global.CSS={escape:s=>s};global.scrollX=0;global.scrollY=0;
global.getComputedStyle=()=>({visibility:'visible',display:'block',opacity:'1',position:'static'});
const page=eval(process.argv[3]),witness=eval(process.argv[4]);
console.log(JSON.stringify({page,witness}));
