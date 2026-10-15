// Offline graph only: both literal production readers run against identical nodes.
const scenario = JSON.parse(process.argv[2]);
const nodes = [];
function make(tag,id,attrs={},parent=null,text='') {
  const n = {tagName:tag.toUpperCase(),id,nodeType:1,parentElement:parent,children:[],
    attrs, value:attrs.value || '',name:attrs.name || '',type:attrs.type || (tag==='input'?'text':tag==='button'?'submit':''),
    disabled:false,required:Object.hasOwn(attrs,'required'),readOnly:false,checked:false,labels:[],
    getAttribute:k=>Object.hasOwn(attrs,k)?attrs[k]:null,hasAttribute:k=>Object.hasOwn(attrs,k),
    matches:q=>matches(n,q),contains:o=>{for(let p=o;p;p=p.parentElement)if(p===n)return true;return false;},
    closest:q=>{for(let p=n;p;p=p.parentElement)if(matches(p,q))return p;return null;},
    querySelectorAll:q=>nodes.filter(o=>o!==n&&n.contains(o)&&matches(o,q)),
    querySelector:q=>n.querySelectorAll(q)[0]||null,
    getClientRects:()=>n.closest('[hidden]')||n.type==='hidden'?[]:[{width:120,height:24,right:120,bottom:24}]};
  Object.defineProperty(n,'attributes',{get:()=>Object.keys(attrs).map(name=>({name,value:attrs[name]}))});
  Object.defineProperty(n,'form',{get:()=>Object.hasOwn(n,'formOwner')?n.formOwner:tag==='input'||tag==='button'||tag==='textarea'?n.closest('form'):undefined});
  Object.defineProperty(n,'innerText',{get:()=>text+n.children.map(c=>c.innerText).join('')});
  if(parent)parent.children.push(n);nodes.push(n);return n;
}
function matches(n,q){return q.split(',').some(s=>{
  s=s.trim();if(s==='*')return true;if(s===':disabled')return n.disabled;
  if(s.includes(':nth-of-type(')||s.includes(':nth-child(')){
    let p=n;
    for(const part of s.split(' > ').reverse()){
      const m=part.match(/^([a-z]+|\*):nth-(of-type|child)\((\d+)\)$/);
      if(!m||!p||(m[1]!=='*'&&p.tagName!==m[1].toUpperCase()))return false;
      const sib=p.parentElement?p.parentElement.children.filter(x=>m[2]==='child'||x.tagName===p.tagName):[p];
      if(sib.indexOf(p)+1!==Number(m[3]))return false;p=p.parentElement;
    }
    return !p;
  }
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
function proxyFor(key,input,F,shell,phase){
  if(!scenario.requiredProxy)return;
  input.attrs['aria-required']='true';
  const change=scenario.proxyMutation?.key===key?scenario.proxyMutation:{};
  if(change.ownerAttr){const [k,v]=change.ownerAttr;if(v===null)delete input.attrs[k];else input.attrs[k]=v;}
  if(phase==='C2'&&!change.mounted)return;
  const attrs={required:'',tabindex:'-1','aria-hidden':'true',class:'validation-only'};
  if(change.attr){const [k,v]=change.attr;if(v===null)delete attrs[k];else attrs[k]=v;}
  if(change.shellCount)make('div','extra-shell',{class:'select-shell'},F);
  if(change.shellNested){
    shell.parentElement.children.splice(shell.parentElement.children.indexOf(shell),1);
    const wrapper=make('div','shell-parent',{},F);shell.parentElement=wrapper;wrapper.children.push(shell);
  }
  if(change.shellOutsideControl){shell.attrs.class='ordinary';shell=make('div',key+'-proxy-shell',{class:'select-shell'},F);}
  if(change.comboForeign)input.formOwner=null;
  const p=make(change.tag||'input','',attrs,change.parent==='outside'?form:change.parent==='inside'?F:shell);
  p.proxy=true;p.value=change.value||'';
  p.computed={opacity:'0',position:'absolute',pointerEvents:'none',...(change.style||{})};
  if(change.foreign)p.formOwner=change.foreign==='null'?null:make('form','foreign-proxy',{},body);
  if(change.second){const second=make('input','', {...attrs},shell);second.computed=p.computed;}
  if(change.extra)make(change.extra==='contenteditable'?'div':'input','extra-editable',
    change.extra==='contenteditable'?{contenteditable:'true'}:change.extra==='combobox'?{role:'combobox'}:{},F);
}
for(const key of keys){
  const phase=scenario.phases?.[key]||'C0';
  const field=make('div',key+'-field',{},form);
  const label=make('label',key+'-label',{},field,key);
  const shell=scenario.requiredProxy?make('div',key+'-shell',{class:'select-shell'},field):field;
  const v=make('div',key+'-value',{},shell);
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
  proxyFor(key,input,field,shell,phase);
  if(phase==='C1'){
    const parent=scenario.danger==='ancestor'?make('a',key+'-anchor',{href:'/away'},body):body;
    if(parent.tagName==='A')parent.href='/away';
    const list=make('div',key+'-list',{role:'listbox'},parent);
    const option=make('div',key+'-option',{role:'option','data-value':scenario.code||'Approved '+key},list,scenario.caption||'Approved '+key);
    if(scenario.optionAttrs) {
      delete option.attrs['data-value'];
      Object.assign(option.attrs,scenario.optionAttrs);
    }
    if(scenario.danger==='descendant')make('button',key+'-danger',{type:'submit'},option);
    if(scenario.conflict)attrs['aria-owns']='school-list';
    if(scenario.shared)make('input','other-owner',{role:'combobox','aria-controls':list.id},form);
    if(scenario.duplicate)make('div',list.id,{role:'listbox'},body);
    if(scenario.foreign){const other=make('form','foreign',{},body);list.parentElement=other;other.children.push(list);}
    if(scenario.nestedList)make('div','nested-list',{role:'listbox'},list);
    if(scenario.noPopup){nodes.splice(nodes.indexOf(list),1);nodes.splice(nodes.indexOf(option),1);}
  }
}
let phoneParent=form;
form.id='application-form';
const questions=make('div','questions',{class:'application--questions'},form);
const outer=make('div','phone-outer',{},questions);
const fs=make('fieldset','phone-field',{class:'phone-input'},outer);
make('legend','phone-legend',{},fs,'Phone');
const country=make('div','country-field',{class:'phone-input__country'},fs);
if(scenario.fullPhone){
  const select=make('div','country-select',{class:'select'},country);
  const container=make('div','country-container',{class:'select__container'},select);
  const shell=make('div','country-shell',{class:'select-shell'},container);
  const owner=make('div','country-owner',{},shell);
  const label=make('label','country-label',{for:'country'},container,'Country');
  const control=make('div','country-control',{class:'select__control'},owner);
  const v=make('div','country-value',{class:'select__value-container'},control);
  const w=make('div','country-input',{class:'select__input-container'},v);
  const phase=scenario.phases?.country||'C0';
  const attrs={type:'text',class:'select__input',role:'combobox',autocomplete:'off',tabindex:'0',
    'aria-labelledby':'country-label','aria-autocomplete':'list','aria-haspopup':'true',
    'aria-expanded':phase==='C1'?'true':'false','aria-required':'true','aria-invalid':'false',
    'aria-describedby':'react-select-country-placeholder country-error','aria-errormessage':'country-error',
    'aria-activedescendant':''};
  const input=make('input','country',attrs,w);
  make('div','react-select-country-placeholder',{class:phase==='C2'?'select__single-value':'select__placeholder'},v,phase==='C2'?(scenario.countryToken||'Approved country'):'Choose');
  make('span','country-error',{},owner);
  if(scenario.presentation && phase==='C2'){attrs['aria-describedby']='country-error';attrs['aria-invalid']='true';}
  if(phase==='C1'){
    attrs['aria-controls']='country-options';
    const list=make('div','country-options',{role:'listbox'},owner);
    const option=make('div','country-option',{role:'option',...(scenario.countryCodes||{})},list,scenario.countryCaption||'Approved country');
    if(scenario.countryDanger)make('button','country-danger',{type:'submit'},option);
    if(scenario.countryPortal){list.parentElement=body;body.children.push(list);}
  }
  if(scenario.countryAttr) {const [k,value]=scenario.countryAttr; if(value===null)delete attrs[k];else attrs[k]=value;}
  if(scenario.countryWrongFor)label.attrs.for='phone';
  if(scenario.countryHidden)make('input','country-hidden',{type:'hidden'},country);
  if(scenario.countryExtra)make('input','country-extra',{},w);
  if(scenario.countryTyped)input.value='Approved country';
  if(scenario.countryDuplicate)make('input','country',{type:'text'},form);
  if(scenario.countryNoToken)nodes.find(n=>n.id==='react-select-country-placeholder').attrs.class='ordinary-text';
  if(scenario.countryDrift)owner.id+='-changed';
  if(scenario.countryWrongChain)shell.attrs.class='wrong-shell';
  proxyFor('country',input,container,shell,phase);
}
phoneParent=make('div','phone-branch',{class:'phone-input__phone'},fs);
phoneParent=make('div','phone-text',{class:'text-input-wrapper'},phoneParent);
phoneParent=make('div','phone-input-wrapper',{class:'input-wrapper'},phoneParent);
const iti=make('div','telephone',{class:'iti'},phoneParent);
make('input','phone',{type:'tel','aria-label':'Phone'},iti);
if(scenario.multipleTel)make('input','second-phone',{type:'tel'},iti);
const flat=scenario.flatPicker;
const cc=make('div','countries',{class:'iti__country-container',...(flat||scenario.visiblePicker?{}:{hidden:true})},iti);
const button=make('button','country-button',{type:scenario.unsafePicker?'submit':'button',class:'iti__selected-country',
  ...(flat?{}:{'aria-haspopup':'dialog','aria-expanded':'false','aria-controls':'dropdown'})},cc);
const dd=make('div','dropdown',{class:'iti__dropdown-content',...(flat?{}:{role:'dialog','aria-modal':'true'})},flat?iti:cc);
const sw=flat?dd:make('div','search-wrapper',{class:'iti__search-input-wrapper'},dd);
const search=make('input','country-search',{class:'iti__search-input',role:'combobox',type:flat?'text':'search',
  ...(flat?{}:{'aria-expanded':'true','aria-controls':'country-list','aria-autocomplete':'list',autocomplete:'off'})},sw);
if(!flat){
  const icon=make('span','search-icon',{class:'iti__search-icon','aria-hidden':'true'},sw);
  make('svg','search-svg',{},icon);
  make('button','country-clear',{class:'iti__search-clear',type:'button'},sw);
  make('span','country-a11y',{class:'iti__a11y-text'},dd);
  make('div','country-no-results',{class:'iti__no-results','aria-hidden':'true'},dd);
}
const list=make('ul','country-list',{class:'iti__country-list',role:'listbox'},dd);
for(let i=0;i<244;i++)make('li','picker-option-'+i,{class:'iti__country',role:'option'},list,'Synthetic '+i);
if(scenario.secondIti){
  const clone=(n,parent)=>{
    const attrs={...n.attrs};
    for(const k of ['aria-controls','aria-owns','aria-labelledby','aria-describedby'])
      if(attrs[k])attrs[k]=attrs[k].split(' ').map(id=>'second-'+id).join(' ');
    const c=make(n.tagName.toLowerCase(),'second-'+n.id,attrs,parent);
    for(const child of n.children)clone(child,c);
    return c;
  };
  clone(iti,form);
}
if(scenario.movedIti){
  phoneParent.children.splice(phoneParent.children.indexOf(iti),1);
  iti.parentElement=form;form.children.push(iti);
}
if(scenario.renamedPhone)nodes.find(n=>n.id==='phone').id='other-phone';
if(scenario.secondTelOutside)make('input','outside-tel',{type:'tel'},form);
if(scenario.brokenPhoneChain)phoneParent.attrs.class='other-input-wrapper';
if(scenario.pickerAttr){const [id,k,v]=scenario.pickerAttr;const n=nodes.find(n=>n.id===id);if(v===null)delete n.attrs[k];else n.attrs[k]=v;if(k==='type')n.type=v;}
if(scenario.pickerDuplicate)make('div','dropdown',{},cc);
if(scenario.pickerForeign){const other=make('form','other',{},body);dd.parentElement=other;other.children.push(dd);}
if(scenario.pickerExtra)make('input','extra-hidden-picker',{type:'hidden'},cc);
if(scenario.pickerOptionDanger)make('a','picker-danger',{href:'/away'},list.children[0]).href='/away';
if(scenario.pickerShared)make('div','foreign-claim',{'aria-controls':'country-list'},form);
if(scenario.pickerDuplicateOption)make('span','picker-option-0',{},list.children[0]);
if(scenario.pickerUnknown)make('canvas','picker-canvas',{},button);
if(scenario.pickerMove){const [id,to]=scenario.pickerMove;const n=nodes.find(n=>n.id===id);n.parentElement.children.splice(n.parentElement.children.indexOf(n),1);n.parentElement=nodes.find(n=>n.id===to);n.parentElement.children.push(n);}
if(scenario.pickerDecoration){button.attrs['aria-label']='Updated decoration';list.children[17].attrs['aria-selected']='true';list.children[17].attrs.class+=' iti__highlight';}
if(scenario.outsideOption)make('li','outside-option',{role:'option'},form,'Unrelated');
if(scenario.extraPicker)make('input','extra-picker',{role:'combobox',required:true},iti);
if(scenario.nestedPicker)make('div','nested-picker',{class:'iti'},iti);
make('textarea','additional',{},form);
make('input','resume',{type:'file','aria-label':'Résumé'},form);
make('button','submit',{type:'submit'},form,'Submit application');
for(let i=0;i<25;i++)make('input','filler-'+i,{},form);
if(scenario.diagnostics){
  for(let i=0;i<(scenario.diagnostics.count||1);i++){
    let parent=form;
    for(let d=0;d<(scenario.diagnostics.depth||0);d++)parent=make('div','deep-'+i+'-'+d,{},parent);
    make(scenario.diagnostics.custom?'private-test-user-example-com':'canvas','test-user@example-co.test-'+i,{},parent);
  }
}
if(scenario.fixtureTree){
  nodes.length=0;
  const build=(tree,parent=null)=>{
    const n=make(tree.tag,tree.attrs.id||'',tree.attrs,parent,tree.text);
    if(n.tagName==='FORM')n.action=tree.attrs.action;
    for(const child of tree.children)build(child,n);
  };
  build(scenario.fixtureTree);
}
global.document={readyState:'complete',title:'Synthetic',body:nodes.find(n=>n.tagName==='BODY'),
  querySelectorAll:q=>nodes.filter(n=>matches(n,q)),querySelector:q=>nodes.find(n=>matches(n,q))||null,
  getElementById:id=>nodes.find(n=>n.id===id)||null};
global.location={href:scenario.fixtureUrl||form.action};global.CSS={escape:s=>s};global.scrollX=0;global.scrollY=0;
global.getComputedStyle=n=>({visibility:'visible',display:'block',opacity:'1',position:'static',pointerEvents:'auto',...(n.computed||{}),
  ...(scenario.fixtureTree&&n.attrs.class==='validation-only'?{opacity:'0',position:'absolute',pointerEvents:'none'}:{})});
const page=eval(process.argv[3]),witness=eval(process.argv[4]);
console.log(JSON.stringify({page,witness}));
