// Offline parent-document fixture; no browser, networking or provider contents.
const spec = JSON.parse(process.argv[2]);
const nodes = [];
const node = (tag, attrs={}, parent=null) => {
  const e = {tagName:tag.toUpperCase(),localName:tag.toLowerCase(),nodeType:1,children:[],parentElement:parent,
    namespaceURI:'http://www.w3.org/1999/xhtml',
    id:Object.hasOwn(attrs,'id') ? String(attrs.id) : '',name:attrs.name || '',type:attrs.type || '',
    value:'',disabled:false,readOnly:false,shadowRoot:null,
    get innerText() {return (attrs.text || '')+e.children.map(n=>n.innerText).join('');},
    get textContent() {return (attrs.text || '')+e.children.map(n=>n.textContent).join('');},
    get childNodes() {return [...(attrs.text ? [{nodeType:3,parentElement:e,textContent:attrs.text}] : []),...e.children];},
    get labels() {return nodes.filter(n=>n.tagName==='LABEL' && (n.contains(e) || (e.id && n.getAttribute('for')===e.id)));},
    get elements() {return nodes.filter(n=>n.form===e && n.matches('input,button,select,textarea,output,object'));},
    getAttribute:k => Object.hasOwn(attrs,k) ? attrs[k] : null,
    hasAttribute:k=>Object.hasOwn(attrs,k),
    getAttributeNames:()=>Object.keys(attrs),
    setAttribute:(k,v)=> {attrs[k]=String(v); if(['id','name','type'].includes(k))e[k]=String(v);},
    removeAttribute:k=> {delete attrs[k]; if(['id','name','type'].includes(k))e[k]='';},
    get attributes() {return Object.keys(attrs).filter(k=>!['rect','bounds','text','pseudo'].includes(k)).map(name=>({name,value:String(attrs[name])}));},
    getClientRects:()=>attrs.rect===null ? [] : [attrs.rect || {width:100,height:30,right:100,bottom:30}],
    getBoundingClientRect:()=> {if(attrs.bounds===null) throw Error('unreadable'); return attrs.bounds || attrs.rect || {width:100,height:30};},
    matches:q=>q.split(',').some(s=>match(e,s)),
    closest:q=> {for(let n=e;n;n=n.parentElement) if(n.matches(q)) return n; return null;},
    contains:n=> {for(;n;n=n.parentElement) if(n===e) return true; return false;},
    querySelectorAll:q=>nodes.filter(n=>n!==e && e.contains(n) && n.matches(q)),
    querySelector:q=>e.querySelectorAll(q)[0] || null,
    style:{display:'block',visibility:'visible',opacity:'1',position:'static',...attrs.style},
    pseudo:attrs.pseudo || {},
    required:!!attrs.required
  };
  nodes.push(e); if(parent) parent.children.push(e);
  e.form=e.closest('form');
  return e;
};
function match(e,q) {
  q=q.trim(); if(q==='*') return true;
  // Resolve real positional selector paths for nodes without id attributes.
  if(q.includes(' > ')) {
    const parts=q.split(' > ');let current=e;
    for(let i=parts.length-1;i>=0;i--) {
      if(!current || !match(current,parts[i]))return false;
      current=current.parentElement;
    }
    return true;
  }
  const nth=q.match(/:nth-of-type\((\d+)\)/);
  if(nth) {
    const siblings=e.parentElement ? e.parentElement.children.filter(n=>n.tagName===e.tagName) : [e];
    if(siblings.indexOf(e)+1!==Number(nth[1]))return false;
    q=q.replace(/:nth-of-type\(\d+\)/,'');
  }
  const child=q.match(/:nth-child\((\d+)\)/);
  if(child) {
    if(!e.parentElement || e.parentElement.children.indexOf(e)+1!==Number(child[1]))return false;
    q=q.replace(/:nth-child\(\d+\)/,'');
  }
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
const canonicalAction=spec.action || spec.url ||
  'https://jobs.lever.co/example-co/12345678-1234-4234-8234-123456789abc/apply';
const actionAttrs=spec.actionAttr==='absent' ? {} :
  {action:spec.actionAttr==='empty' ? '' : canonicalAction};
const form=spec.root===false ? null : node('form',{id:'application-form',...actionAttrs},body);
if(form) {
  // A missing/empty raw action can still resolve to PAGE as a property.
  form.action=spec.actionAttr==='absent' || spec.actionAttr==='empty'
    ? (spec.url || canonicalAction) : canonicalAction;
  if(spec.searchOnly) {
    // Complete, separately owned native search shell with no application
    // marker; both production readers must inventory it before waiting.
    node('input',{id:'job-search',type:'search'},form);
    node('button',{id:'search-submit',type:'submit',text:'Search jobs'},form);
  } else {
  node('label',{'for':'email',text:'Email'},form);
  node('input',{id:'email',name:'Email',type:'email',required:true},form);
  if(spec.nativeCheckbox) {
    node('label',{'for':'native-choice',text:'Synthetic choice'},form);
    node('input',{id:'native-choice',type:'checkbox',required:true},form);
  }
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
}
if(spec.lever) {
  const build = require('./lever_card_fixture.cjs');
  build({spec:spec.lever,node,nodes,form,body});
}
if(spec.ambiguous) {
  const other=node('form',{id:'other-application'},body);other.action=form.action;
  node('button',{type:'submit',text:'Submit application'},other);
}
if(spec.nativePair) {
  const foreignAction=spec.foreignAction ||
    'https://jobs.ashbyhq.com/example-co/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/application';
  const other=node('form',{id:'foreign-application',action:foreignAction},body);
  other.action=foreignAction;
  node('label',{'for':'foreign-email',text:'Email'},other);
  node('input',{id:'foreign-email',name:'Foreign email',type:'email',required:true},other);
  node('label',{'for':'foreign-resume',text:'Résumé'},other);
  const file=node('input',{id:'foreign-resume',type:'file',required:true},other);
  file.files=[];
  node('button',{id:'foreign-submit',type:'submit',text:'Submit application'},other);
  if(spec.foreignBorrow) {
    const borrowed=node('input',{id:'foreign-borrow',type:'email',
      form:'foreign-application'},form);
    borrowed.form=other;
    node('label',{'for':'foreign-borrow',text:'Borrowed email'},form);
  }
  if(spec.foreignOverride) {
    const member=nodes.find(n=>n.id==='foreign-email');
    member.setAttribute('formaction',spec.foreignOverride);
    member.formAction=spec.foreignOverride;
  }
}
if(spec.nativePair && spec.foreignFirst) {
  // Reorder DOM and query enumeration so the tracked owner is LAST.
  const foreign=nodes.find(n=>n.id==='foreign-application');
  const subtree=nodes.filter(n=>foreign.contains(n));
  const remainder=nodes.filter(n=>!foreign.contains(n));
  const position=remainder.indexOf(form);
  nodes.splice(0,nodes.length,...remainder.slice(0,position),...subtree,
    ...remainder.slice(position));
  body.children.splice(body.children.indexOf(foreign),1);
  body.children.splice(body.children.indexOf(form),0,foreign);
}
if(spec.formless) {
  if(spec.leadingInert)node('div',{},body);
  const wrapper=node(spec.wrapperTag || 'div',
    {...(spec.idless ? {} : {id:'application-root'}),class:spec.wrapperClass || ''},body);
  const pane=node(spec.paneTag || 'div',
    {...(spec.idless ? {} : {id:'autofill'}),class:'ashby-application-form-autofill-pane'},wrapper);
  const helper=node('input',{id:'autofill-file',type:'file'},pane);
  node('button',{id:'autofill-button',type:'button',text:'Autofill'},pane);
  if(!form) Object.defineProperty(helper,'value',{get:()=> {throw Error('AUTOFILL VALUE READ');}});
  const container=node('div',
    {...(spec.idless ? {} : {id:'application-fields'}),class:'ashby-application-form-container'},wrapper);
  const field=(tag,id,type,text,required=false) => {
    const entry=node('div',{class:'ashby-application-form-field-entry'},container);
    const l=node('label',{id:id+'-label','for':id,class:'ashby-application-form-question-title',text},entry);
    const e=node(tag,{id,type,required},entry); e.labels=[l]; e.files=type==='file' ? [] : undefined;
    return e;
  };
  field('input','_systemfield_name','text','Name',true);
  field('input','_systemfield_email','email','Email',true);
  const resume=field('input','_systemfield_resume','file','Resume',true);
  resume.getClientRects=()=>[{width:1,height:1,right:1,bottom:1}];
  const file=node('div',{id:'file-container',class:'ashby-application-form-input-file'},resume.parentElement);
  resume.parentElement.children.splice(resume.parentElement.children.indexOf(resume),1);
  resume.parentElement=file;file.children.push(resume);
  const drop=node('button',{id:'dropzone',type:'button',class:'ashby-application-form-input-file-dropzone-upload'},file);
  const icon=node('svg',{id:'upload-icon'},drop);node('path',{id:'icon-path',d:'M0 0'},icon);
  if(spec.disabledDrop)drop.disabled=true;
  if(spec.files) resume.files=spec.files.map(name=>({name}));
  if(spec.attached) {
    for(let i=0;i<(spec.twoItems ? 2:1);i++) {
      const item=node('div',{id:'attached-'+i,class:'ashby-application-form-input-file-item'},spec.outsideItem ? container:file);
      const name=node('p',{id:'filename-'+i,class:'ashby-application-form-input-file-item-name',
        ...(spec.filenameChild ? {}:{text:spec.displayName || 'Resume.pdf'})},item);
      if(spec.filenameChild) node(spec.filenameChild,{text:spec.displayName || 'Resume.pdf',
        ...(spec.filenameAttrs || {})},name);
      for(let j=0;j<(spec.filenameSvgs || 0);j++) {
        const svg=node('svg',{...(spec.filenameSvgAttrs || {})},name);
        const path=node('path',{d:'M0 0'},svg);
        svg.namespaceURI=path.namespaceURI='http://www.w3.org/2000/svg';
      }
      const del=node('button',{id:'delete-'+i,class:'ashby-application-form-input-file-item-delete',
        ...(spec.noTitle ? {}:{title:'Remove attachment'}),...(spec.deleteText ? {text:'Delete'}:{})},spec.outsideDelete ? file:item);
      del.type='submit';
      if(spec.deleteIcon) {
        const svg=node('svg',{id:'delete-icon'},del), path=node('path',{id:'delete-path',d:'M0 0L1 1'},svg);
        svg.namespaceURI=path.namespaceURI='http://www.w3.org/2000/svg';
        if(spec.iconAction)path.setAttribute('onclick','syntheticAction()');
      }
    }
    if(!spec.keepIcon) {drop.children=[];for(const n of [icon,...icon.querySelectorAll('*')])nodes.splice(nodes.indexOf(n),1);}
  }
  if(spec.extraInert) node('div',{id:spec.inertId || 'extra-inert'},container);
  if(spec.adjacentText) node('p',{id:'adjacent',text:'Unexpected structure'},container);
  if(spec.changedDrop)drop.setAttribute('title','Changed dropzone');
  if(spec.secondDrop)node('button',{id:'second-drop',type:'button',class:'ashby-application-form-input-file-dropzone-upload'},file);
  field('textarea','additional','textarea','Additional information');
  const submit=node('button',{...(spec.idless ? {} : {id:'structural-submit'}),
    class:'ashby-application-form-submit-button',text:'Submit application',
    ...(spec.submitAttrs || {})},wrapper);
  submit.type='submit'; // HTMLButtonElement's default, despite no type attribute.
  const move=(e,to)=> {e.parentElement.children.splice(e.parentElement.children.indexOf(e),1); e.parentElement=to; to.children.push(e);};
  if(spec.outside) move(nodes.find(e=>e.id===spec.outside),body);
  if(spec.extraType) node(spec.extraType==='select' ? 'select':'input',
    {id:'unsupported',type:spec.extraType,required:true,...(spec.extraAttrs || {})},container);
  if(spec.duplicate) node('div',{class:'ashby-application-form-container'},body);
  if(spec.secondFormlessComplete) {
    const second=node('div',{},body);
    node('div',{class:'ashby-application-form-autofill-pane'},second);
    const fields=node('div',{class:'ashby-application-form-container'},second);
    const field=(id,type,caption)=>{
      const parent=node('div',{},fields);
      node('label',{'for':id,class:'ashby-application-form-question-title',text:caption},parent);
      const input=node('input',{id,type,required:true},parent);
      if(type==='file')input.files=[];
    };
    field('second-name','text','Name');
    field('second-email','email','Email');
    field('second-resume','file','Résumé');
    const other=node('button',{class:'ashby-application-form-submit-button',
      text:'Submit application'},second);
    other.type='submit';
  }
  if(spec.wrongOrder) wrapper.children.reverse();
  if(spec.duplicateLabel) node('label',{'for':'_systemfield_name',class:'ashby-application-form-question-title',text:'Other'},container);
  if(spec.hiddenOutside) node('input',{id:'outside-hidden',type:'hidden',rect:null},body);
  if(spec.extraChild) node('span',{},wrapper);
  if(spec.inertSibling) {
    const sibling=node('div',{},wrapper);
    if(spec.inertBeforeContainer) {
      wrapper.children.splice(wrapper.children.indexOf(sibling),1);
      wrapper.children.splice(wrapper.children.indexOf(container),0,sibling);
    }
  }
  if(spec.movePane) move(pane,body);
  if(spec.moveSubmit) move(submit,container);
  if(spec.duplicateSubmit) node('button',{class:'ashby-application-form-submit-button'},body);
  if(spec.duplicatePane) node('div',{class:'ashby-application-form-autofill-pane'},body);
  if(spec.duplicateId) node('input',{id:'_systemfield_name',type:'text'},container);
  if(spec.noResume) move(resume,pane);
  if(spec.invisible) {const e=nodes.find(e=>e.id===spec.invisible);e.getClientRects=()=>[];}
  if(spec.nativeOwnerMissing) for(const e of nodes.filter(e=>e.id==='submit')) e.form=null;
  if(spec.descriptionLink) node('a',{id:'description-link',href:'https://example-co.test/info'},body);
  if(spec.missingPane) {
    for(const n of [pane,...pane.querySelectorAll('*')]) nodes.splice(nodes.indexOf(n),1);
    wrapper.children.splice(wrapper.children.indexOf(pane),1);
  }
  if(spec.clipStyle) {
    const e=nodes.find(n=>n.id===(spec.clipTarget || '_systemfield_name'));
    Object.assign((spec.clipAncestor ? e.parentElement : e).style,spec.clipStyle);
  }
  if(spec.searchForm) {
    const search=node('form',{id:'search-form'},body);
    search.action='https://jobs.ashbyhq.com/example-co/search';
    node('input',{id:'job-search',type:spec.searchForm==='foreign' ? 'text':'search'},search);
    node('button',{id:'search-submit',type:'submit',text:'Search jobs'},search);
  }
  if(spec.treeNodes) {
    const count=()=> {let total=0,stack=[wrapper];while(stack.length){const n=stack.pop();total++;if(n.nodeType===1)stack.push(...n.childNodes);}return total;};
    for(let i=count();i<spec.treeNodes;i++)node('span',{id:'padding-'+i},container);
  }
  if(spec.unreadableTree)Object.defineProperty(file,'childNodes',{get:()=>{throw Error('unreadable subtree');}});
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
    const w=spec.w || {};
    const row=node('li',{id:'w-row',class:'application-question awli-application-row',
      ...(spec.required ? {required:true}: {}),...(spec.rowRole ? {role:spec.rowRole}: {}),...w.row},
      spec.wOutside ? body : form || body);
    parent=row;
    if(w.rowPropertyRequired)row.required=true;
    if(w.rowAttributeRequired) {row.setAttribute('required','');row.required=false;}
    if(spec.extra) node('input',{type:'text'},row);
    if(spec.extraRole) node('span',{id:'aria-applicant',role:spec.extraRole,'aria-checked':'false',text:'Synthetic consent'},row);
    const field=node('div',{class:'application-field',...w.field},row);
    const container=node('div',{class:'awli-button-container',...w.container},field);
    parent=node('span',{class:'IN-widget',...w.span},container);
    if(!w.missingButton) {
      let owner=container;
      if(w.wrappingLabel)owner=node('label',{text:'Synthetic label'},container);
      const button=node('button',{id:'w-button',class:'awli-button',type:'button',...w.button},owner);
      if(w.buttonNamespace)button.namespaceURI=w.buttonNamespace;
      if(w.missingType)button.removeAttribute('type');
      if(w.externalLabel)node('label',{'for':button.id,text:'Synthetic external label'},body);
      if(w.labelledby) {button.setAttribute('aria-labelledby','w-caption');node('span',{id:'w-caption',text:'Synthetic caption'},body);}
      if(w.child)node(w.child.tag || 'span',w.child.attrs || {},button);
      if(w.unknown)Object.defineProperty(w.unknown==='elements' ? form : button,w.unknown==='elements' ? 'elements' : w.unknown,
        {get:()=>{throw Error('unreadable W evidence');}});
    }
    if(!w.noLink) {
      const link=node('a',{id:'w-link',class:'learn-more-link',href:'https://widgets.example-co.test',...w.link},container);
      if(w.linkNamespace)link.namespaceURI=w.linkNamespace;
      if(w.missingHref)link.removeAttribute('href');
    }
    for(const extra of w.extra || []) {
      const owner=extra.parent ? node(extra.parent,{},row) : row;
      const added=node(extra.tag || 'div',{id:'w-extra',...extra.attrs},owner);
      if(extra.shadow) {
        const child={nodeType:1,localName:'input',tagName:'INPUT',type:'text'};
        added.shadowRoot={children:[child],querySelectorAll:()=>[child],querySelector:()=>null};
      }
    }
    if(w.unreadableRow)Object.defineProperty(row,w.unreadableRow,{get:()=>{throw Error('unreadable row evidence');}});
    attrs={...attrs,title:'LinkedIn Embedded Content',rect:{width:236,height:48,right:236,bottom:48}};
  } else if(kind==='X') attrs={...attrs,style:{visibility:'hidden'},rect:{width:1,height:1,right:1,bottom:1}};
  else if(kind==='Z') {
    parent=node(spec.zParentTag || 'div',spec.zParentAttrs || {},
      spec.zInside ? nodes.find(n=>n.id==='application-fields') : body);
    if(!spec.zParentAttrs?.id) parent.id='';
    if(spec.zNested) {const outer=node('div',{},body);parent.parentElement=outer;}
    attrs={...attrs,style:{display:'none'},rect:null};
  }
  if(kind==='X' && spec.inside) parent=form || body;
  if(spec.brokenEdge) parent=node('div',{},parent);
  if(spec.bounds==='missing') attrs.bounds=null;
  else if(spec.bounds==='nan') attrs.bounds={width:NaN,height:60};
  else if(spec.bounds==='infinity') attrs.bounds={width:256,height:Infinity};
  else if(spec.bounds) attrs.bounds=spec.bounds;
  const result=node('iframe',{...attrs,...edit},parent);
  if(kind==='W' && spec.w?.iframe)for(const [k,v] of Object.entries(spec.w.iframe))result.setAttribute(k,v);
  return result;
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
if(spec.domOrder) {
  // Unlike insertion order, actual querySelectorAll follows the current DOM
  // tree. Reordered inert siblings must genuinely re-key the witness census.
  const ordered=[];
  const visit=e=>{ordered.push(e);for(const child of e.children)visit(child);};
  visit(body);
  if(ordered.length!==nodes.length || new Set(ordered).size!==nodes.length)
    throw Error('Incomplete synthetic DOM tree order');
  nodes.splice(0,nodes.length,...ordered);
}
// Keep the 4096-node synthetic census cheap without weakening duplicate-ID proof.
const ids=new Map();for(const n of nodes) {
  if(!n.id)continue;
  if(!ids.has(n.id))ids.set(n.id,[]);
  ids.get(n.id).push(n);
}
const currentUrl=new URL(spec.url || form?.action || 'https://example-co.test/');
global.document={baseURI:currentUrl.href,readyState:'complete',body,title:'Synthetic',
  querySelectorAll:q=>/^#[\w-]+$/.test(q) ? ids.get(q.slice(1)) || [] : nodes.filter(n=>n.matches(q)),
  querySelector:q=>document.querySelectorAll(q)[0] || null,
  getElementById:id=>id ? ids.get(id)?.[0] || null : null};
global.CSS={escape:s=>s}; global.location={href:currentUrl.href,protocol:currentUrl.protocol,
  hostname:currentUrl.hostname,host:currentUrl.host};
global.scrollX=0; global.scrollY=0;
global.getComputedStyle=(e,pseudo)=>pseudo ? {content:e.pseudo[pseudo] ?? 'none'} : e.style;
console.log(JSON.stringify(eval(process.argv[3])));
