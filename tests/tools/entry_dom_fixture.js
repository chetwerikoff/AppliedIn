// Offline parent-document fixture; no browser, networking or provider contents.
const spec = JSON.parse(process.argv[2]);
const nodes = [];
const node = (tag, attrs={}, parent=null) => {
  const e = {tagName:tag.toUpperCase(),nodeType:1,children:[],parentElement:parent,
    id:attrs.id || 'node-'+nodes.length,name:attrs.name || '',type:attrs.type || '',
    value:'',innerText:attrs.text || '',labels:[],disabled:false,readOnly:false,
    getAttribute:k => k==='id' ? e.id : Object.hasOwn(attrs,k) ? attrs[k] : null,
    hasAttribute:k=>Object.hasOwn(attrs,k),
    attributes:Object.keys(attrs).filter(k=>!['rect','bounds','text'].includes(k)).map(name=>({name})),
    getClientRects:()=>attrs.rect===null ? [] : [attrs.rect || {width:100,height:30,right:100,bottom:30}],
    getBoundingClientRect:()=> {if(attrs.bounds===null) throw Error('unreadable'); return attrs.bounds || attrs.rect || {width:100,height:30};},
    matches:q=>q.split(',').some(s=>match(e,s)),
    closest:q=> {for(let n=e;n;n=n.parentElement) if(n.matches(q)) return n; return null;},
    contains:n=> {for(;n;n=n.parentElement) if(n===e) return true; return false;},
    querySelectorAll:q=>nodes.filter(n=>n!==e && e.contains(n) && n.matches(q)),
    querySelector:q=>e.querySelectorAll(q)[0] || null,
    style:{display:'block',visibility:'visible',opacity:'1',position:'static',...attrs.style},
    required:!!attrs.required
  };
  nodes.push(e); if(parent) parent.children.push(e);
  e.form=e.closest('form');
  return e;
};
function match(e,q) {
  q=q.trim(); if(q==='*') return true;
  if(q===':disabled') return false;
  if(q.includes(':not(')) {const [_,base,neg]=q.match(/^(.*):not\((.*)\)$/); return match(e,base) && !match(e,neg);}
  const tag=q.match(/^[a-zA-Z][\w-]*/)?.[0];
  if(tag && e.tagName!==tag.toUpperCase()) return false;
  const id=q.match(/#([\w-]+)/)?.[1]; if(id && e.id!==id) return false;
  for(const c of q.matchAll(/\.([\w-]+)/g)) if(!(e.getAttribute('class') || '').split(/\s+/).includes(c[1])) return false;
  for(const a of q.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    if(a[2]===undefined ? !e.hasAttribute(a[1]) : e.getAttribute(a[1])!==a[2]) return false;
  }
  return true;
}
const body=node('body',{id:'body'});
const form=spec.root===false ? null : node('form',{id:'application-form'},body);
if(form) {
  form.action=spec.action || spec.url || 'https://jobs.lever.co/example-co/12345678-1234-4234-8234-123456789abc/apply';
  node('input',{id:'email',name:'Email',type:'email'},form);
  if(spec.marker!=='absent') {
    const marker=spec.marker || 'text';
    const submit=node(marker==='value' || marker==='image' ? 'input':'button',
      {id:'submit',type:marker==='image' ? 'image':'submit',
        ...(marker==='aria' ? {'aria-label':'Submit application'} :
          marker==='labelledby' ? {'aria-labelledby':'submit-caption'} :
          marker==='value' ? {name:'submit'} : {text:'Submit application'})},form);
    if(marker==='value') submit.value='Submit application';
    if(spec.foreignMarker)submit.form=null;
    if(marker==='labelledby') node('span',{id:'submit-caption',text:'Submit application'},body);
  }
}
if(spec.ambiguous) {
  const other=node('form',{id:'other-application'},body);other.action=form.action;
  node('button',{type:'submit',text:'Submit application'},other);
}
if(spec.formless) {
  const wrapper=node('div',{id:'application-root'},body);
  const pane=node('div',{id:'autofill',class:'ashby-application-form-autofill-pane'},wrapper);
  const helper=node('input',{id:'autofill-file',type:'file'},pane);
  node('button',{id:'autofill-button',type:'button',text:'Autofill'},pane);
  Object.defineProperty(helper,'value',{get:()=> {throw Error('AUTOFILL VALUE READ');}});
  const container=node('div',{id:'application-fields',class:'ashby-application-form-container'},wrapper);
  const field=(tag,id,type,text,required=false) => {
    const entry=node('div',{class:'ashby-application-form-field-entry'},container);
    const l=node('label',{'for':id,class:'ashby-application-form-question-title',text},entry);
    const e=node(tag,{id,type,required},entry); e.labels=[l]; e.files=type==='file' ? [] : undefined;
    return e;
  };
  field('input','_systemfield_name','text','Name',true);
  field('input','_systemfield_email','email','Email',true);
  const resume=field('input','_systemfield_resume','file','Resume',true);
  resume.getClientRects=()=>[{width:1,height:1,right:1,bottom:1}];
  node('button',{id:'dropzone',type:'button',class:'ashby-application-form-input-file-dropzone-upload'},resume.parentElement);
  field('textarea','additional','textarea','Additional information');
  const submit=node('button',{id:'structural-submit',class:'ashby-application-form-submit-button',text:'Submit application'},wrapper);
  submit.type='submit'; // HTMLButtonElement's default, despite no type attribute.
  const move=(e,to)=> {e.parentElement.children.splice(e.parentElement.children.indexOf(e),1); e.parentElement=to; to.children.push(e);};
  if(spec.outside) move(nodes.find(e=>e.id===spec.outside),body);
  if(spec.extraType) node(spec.extraType==='select' ? 'select':'input',
    {id:'unsupported',type:spec.extraType,required:true,...(spec.extraAttrs || {})},container);
  if(spec.duplicate) node('div',{class:'ashby-application-form-container'},body);
  if(spec.wrongOrder) wrapper.children.reverse();
  if(spec.duplicateLabel) node('label',{'for':'_systemfield_name',class:'ashby-application-form-question-title',text:'Other'},container);
  if(spec.hiddenOutside) node('input',{id:'outside-hidden',type:'hidden',rect:null},body);
}
function frame(kind, edit={}) {
  let parent=body, attrs={id:'frame-'+nodes.length};
  if(kind==='H') {
    parent=node('div',{id:'h-captcha',class:'h-captcha'},form || body);
    attrs={...attrs,src:'https://newassets.hcaptcha.com/captcha/v1/test/static/hcaptcha-enclave.html?secret=redacted#fragment',style:{visibility:'hidden'}};
  } else if(kind==='R' || kind==='B') {
    parent=spec.inside ? form || body : body;
    if(spec.badge) {
      if(spec.tokenBadge)parent=node('div',spec.badgeWrapper || {},parent);
      parent=node('div',{class:'grecaptcha-badge'},parent);
    }
    attrs={...attrs,src:'https://www.recaptcha.net/recaptcha/enterprise/'+(kind==='B' ? 'bframe' : 'anchor'),style:{visibility:spec.visible ? 'visible':'hidden'},bounds:{width:256,height:60}};
  } else if(kind==='W') {
    parent=node('li',{class:'application-question awli-application-row',...(spec.required ? {required:true}: {}),
      ...(spec.rowRole ? {id:'w-row',role:spec.rowRole}: {})},form || body);
    if(spec.extra) node('input',{type:'text'},parent);
    if(spec.extraRole) node('span',{id:'aria-applicant',role:spec.extraRole,'aria-checked':'false',text:'Synthetic consent'},parent);
    parent=node('div',{class:'application-field'},parent);
    parent=node('div',{class:'awli-button-container'},parent);
    parent=node('span',{class:'IN-widget'},parent);
    attrs={...attrs,title:'LinkedIn Embedded Content',rect:{width:236,height:48,right:236,bottom:48}};
  } else if(kind==='X') attrs={...attrs,style:{visibility:'hidden'},rect:{width:1,height:1,right:1,bottom:1}};
  if(kind==='X' && spec.inside) parent=form || body;
  if(spec.brokenEdge) parent=node('div',{},parent);
  if(spec.bounds==='missing') attrs.bounds=null;
  else if(spec.bounds==='nan') attrs.bounds={width:NaN,height:60};
  else if(spec.bounds==='infinity') attrs.bounds={width:256,height:Infinity};
  else if(spec.bounds) attrs.bounds=spec.bounds;
  return node('iframe',{...attrs,...edit},parent);
}
for(const k of spec.frames || []) frame(k,spec.edit || {});
if(spec.sibling) frame('R',{src:'https://untrusted.example-co.test/widget'});
for(const e of nodes.filter(e=>e.tagName==='IFRAME')) {
  Object.defineProperty(e,'contentDocument',{get:()=>{throw Error('CHILD FRAME READ');}});
  Object.defineProperty(e,'contentWindow',{get:()=>{throw Error('CHILD FRAME READ');}});
}
if(spec.helper) node('button',{id:'hcaptchaSubmitBtn',class:spec.helper==='hidden' ? 'hidden':'',type:'submit',rect:spec.helper==='hidden' ? null:undefined},form || body);
for(let i=0;i<(spec.tokens || []).length;i++) {
  const name=spec.tokens[i];
  let parent=spec.tokenOutside ? body : form || body;
  let attrs={id:'token-'+i,name,type:'hidden'};
  if(spec.tokenBadge) {
    parent=nodes.find(e=>e.matches('div.grecaptcha-badge')) ||
      node('div',{class:'grecaptcha-badge'},node('div',spec.badgeWrapper || {},body));
    if(spec.tokenNotInBadge)parent=parent.parentElement;
    if(spec.tokenInForm)parent=form;
    attrs={id:'g-recaptcha-response-'+(100+i),class:'g-recaptcha-response',name,
      style:{display:'none'},rect:null,...spec.tokenEdit};
  }
  const token=node(spec.textarea ? 'textarea':'input',attrs,parent);
  if(spec.tokenAssociated)token.form=form;
  Object.defineProperty(token,'value',{get:()=> {throw Error('PROTECTED TOKEN VALUE READ');}});
  if(spec.tokenVisible)token.getClientRects=()=>[{width:1,height:1}];
  if(spec.tokenNestedWrapper)token.parentElement.parentElement.parentElement=node('div',{},body);
  if(spec.claim) node('input',{role:'combobox',type:'text','data-value-input':token.id,'aria-haspopup':'true','aria-autocomplete':'list'},token.parentElement);
}
if(spec.tokenSiblingBadge)node('div',{class:'grecaptcha-badge'},body);
global.document={baseURI:'https://example-co.test/',readyState:'complete',body,title:'Synthetic',
  querySelectorAll:q=>nodes.filter(n=>n.matches(q)),
  querySelector:q=>document.querySelectorAll(q)[0] || null,
  getElementById:id=>nodes.find(n=>n.id===id) || null};
global.CSS={escape:s=>s}; global.location={href:spec.url || form?.action || 'https://example-co.test/'};
global.scrollX=0; global.scrollY=0; global.getComputedStyle=e=>e.style;
console.log(JSON.stringify(eval(process.argv[3])));
