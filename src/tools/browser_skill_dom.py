"""Read-only DOM inventory. Models never supply JavaScript to the browser."""

_DIAGNOSTIC_TAGS = (
    'html body form div span input label button textarea select option fieldset legend '
    'p section main article header footer ul ol li a canvas object embed iframe table '
    'tbody tr td h1 h2 h3'
)


# Share conservative per-locator size bounds with the Gate validator; the
# inventory must still include every present opaque node, not just the first few.
_DIAGNOSTIC_MAX_SEGMENTS = 256
_DIAGNOSTIC_MAX_CHARS = 8192


_WIDGET_GRAPH = r"""
  // Accessible roles and relationships only: no site/library selectors, C0/C1/C2
  // phases or page-controlled exceptions to a privileged application write.
  const graphNodes = new WeakMap(), newGraphs = new Map();
  const requiredProxies = new Set(), pickerParts = new Set(), quarantined = new Set();
  const nativeForm = e => e.form || e.closest('form');
  const attr = (e, k) => e.getAttribute(k) || '';
  const uniqueNode = e => !!e && document.querySelectorAll(selector(e)).length === 1;
  const byId = id => {
    const matches = id ? document.querySelectorAll('#'+CSS.escape(id)) : [];
    return matches.length === 1 ? matches[0] : null;
  };
  // Explicit inline application commits are observable; autosave is not one.
  const commitsApplication = e => {
    for (let n=e; n && n !== document; n=n.parentElement) {
      const handlers=Array.from(n.attributes || []);
      if (handlers.some(a => /^on(?:click|change|input|pointerup|mouseup|keydown|keyup|blur)$/i.test(a.name)
          && /\b(?:requestSubmit|submit)\s*\(/i.test(a.value))) return true;
      if (n.tagName === 'FORM') break;
    }
    return false;
  };
  // Explicit labels inside the application receiver, not sibling jobs or footers.
  const applicationIdentity=()=>{
    const roots=Array.from(document.querySelectorAll('form'));
    const containers=Array.from(document.querySelectorAll('.ashby-application-form-container'));
    if(containers.length===1 && !containers[0].closest('form')) roots.push(containers[0]);
    const id=/^(?:job|requisition|req|posting)\s*(?:id|number|#)\s*[:#]\s*(\S.{0,159})$/i;
    const employer=/^(?:employer|company(?:\s+name)?)\s*:\s*(\S.{0,159})$/i;
    return roots.map(root=>{
      const lines=String(root.innerText||'').split(/\r?\n/).map(clean);
      const values=rx=>[...new Set(lines.map(s=>s.match(rx)?.[1]?.trim()).filter(Boolean))];
      return {selector:selector(root),job_ids:values(id),employers:values(employer)};
    });
  };
  // Referenced captions from another native form cannot witness this control.
  const refsStayInForm = e => {
    const form=nativeForm(e);
    if (!form) return true;
    for (const key of ['aria-describedby','aria-errormessage']) {
      for (const id of (e.getAttribute(key)||'').split(/\s+/).filter(Boolean)) {
        const ref=byId(id);
        if (ref) {
          if (nativeForm(ref)!==form || ref.hasAttribute('formaction')) return false;
        }
      }
    }
    return true;
  };
  const unsafeOption = o => {
    const dangerous = e => (e.tagName==='A' && !!e.href) ||
      ((e.tagName==='BUTTON' || e.tagName==='INPUT') &&
        ['submit','image'].includes(e.type)) || e.hasAttribute('formaction');
    for (let n=o;n;n=n.parentElement) if (dangerous(n)) return true;
    return Array.from(o.querySelectorAll('*')).some(dangerous);
  };
  const pickerCandidate = () => false, closedPopup = () => null;
  const singleBranch = () => false, placeholderBranch = () => false;
  const diagnosticTags = new Set('DIAGNOSTIC_TAGS'.split(' '));
  const diagnosticLocator = e => {
    const parts = [];
    for (let n=e; n && n.nodeType===1; n=n.parentElement) {
      const tag=n.tagName.toLowerCase(), siblings=n.parentElement ? Array.from(n.parentElement.children) : [n];
      parts.unshift(diagnosticTags.has(tag) ? tag+':nth-of-type('+(siblings.filter(x=>x.tagName===n.tagName).indexOf(n)+1)+')' : '*:nth-child('+(siblings.indexOf(n)+1)+')');
    }
    // Use the shortest unique, value-free suffix. Repeated deep trees need
    // their distinguishing ancestor; never substitute DOM ids or page values.
    for (let depth=1; depth<=Math.min(parts.length,DIAGNOSTIC_LIMIT); depth++) {
      const path=parts.slice(-depth).join(' > ');
      if (path.length>DIAGNOSTIC_CHARS) break;
      if (document.querySelectorAll(path).length===1) return path;
    }
    return 'unavailable';
  };
""".replace('DIAGNOSTIC_TAGS', _DIAGNOSTIC_TAGS).replace(
    'DIAGNOSTIC_LIMIT', str(_DIAGNOSTIC_MAX_SEGMENTS)).replace(
    'DIAGNOSTIC_CHARS', str(_DIAGNOSTIC_MAX_CHARS))

PAGE = r"""(() => {
  const visible = e => !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const selector = e => {
    if (e.id && document.querySelectorAll('#'+CSS.escape(e.id)).length === 1)
      return '#'+CSS.escape(e.id);
    const parts = [];
    for (let n=e; n && n.nodeType===1; n=n.parentElement) {
      const siblings = n.parentElement ? Array.from(n.parentElement.children)
        .filter(x=>x.tagName===n.tagName) : [n];
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
    }
    return parts.join(' > ');
  };
  const label = e => clean(Array.from(e.labels || []).map(x=>x.innerText).join(' ') ||
    (e.getAttribute('aria-labelledby') || '').split(' ')
      .map(id=>document.getElementById(id)?.innerText || '').join(' ') ||
    e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || e.name);
  const query = 'input,textarea,select,button,a[href],[role="button"],[role="combobox"],'+
    '[role="option"],[role="radio"],[role="checkbox"],[contenteditable="true"]';
  const nodes = Array.from(document.querySelectorAll(query));
  // Ordinary PAGE is evaluated alone; the entry-only native-root reader cannot
  // supply this predicate to its controls mapper.
  const pageAshbyNative = location.protocol==='https:' &&
    location.hostname==='jobs.ashbyhq.com';
  /* WIDGET_GRAPH */
  const linkId = e => {
    const claims = ['aria-controls','aria-owns'].flatMap(key =>
      (e.getAttribute(key) || '').trim().split(/\s+/).filter(Boolean));
    if (!claims.length || claims.some(id => id !== claims[0])) return '';
    const owners = Array.from(document.querySelectorAll('[role="combobox"]'))
      .filter(c => ['aria-controls','aria-owns'].some(key =>
        (c.getAttribute(key) || '').trim().split(/\s+/).includes(claims[0])));
    return owners.length === 1 && owners[0] === e ? claims[0] : '';
  };
  const linkedListbox = e => {
    if (!refsStayInForm(e)) return null;
    const id = linkId(e);
    const matches = id ? document.querySelectorAll('#'+CSS.escape(id)) : [];
    if (matches.length !== 1 || matches[0].getAttribute('role') !== 'listbox')
      return null;
    const list = matches[0], listForm = list.closest('form');
    // An ARIA portal outside native forms is permissible; a listbox owned
    // by a DIFFERENT form is not. A nested listbox has another option owner
    // and cannot borrow this combobox's current approved answer.
    if ((listForm && listForm !== nativeForm(e)) ||
        list.querySelector('[role="listbox"]')) return null;
    return list;
  };
  const currentListbox = e => {
    if (pickerCandidate(e) || quarantined.has(e)) return null;
    const list = newGraphs.get(e) ? closedPopup(e) : linkedListbox(e);
    return list && !pickerCandidate(list) ? list : null;
  };
  const controls = nodes.filter(e => !requiredProxies.has(e) && !pickerParts.has(e) && (e.getAttribute('role') !== 'option' ||
    !Array.from(document.querySelectorAll('[role="combobox"]'))
      .some(c=>currentListbox(c)?.contains(e))) &&
    (visible(e) || e.form || e.closest('form') || e.type === 'file')).map(e => {
    const group = e.closest(
      'fieldset,[role="radiogroup"],[data-automation-id="formField"],.form-group');
    const graph = newGraphs.get(e);
    let question = graph ? label(e) : clean(group?.querySelector(
      'legend,[data-automation-id="formLabel"]')?.innerText || group?.innerText || '');
    const nativeType=['INPUT','BUTTON'].includes(e.tagName) ? e.type : '';
    const type=e.getAttribute('role') || nativeType || e.getAttribute('type') || e.tagName.toLowerCase();
    const submit=['submit','image'].includes(nativeType) &&
      (e.tagName === 'INPUT' || !!e.form);
    const form = e.form || e.closest('form');
    // DOM properties resolve relative actions against document.baseURI.
    // Do not infer a receiver from the visible button label.
    const formAction = form?.action || '';
    const override = e.getAttribute('formaction') !== null ? (e.formAction || '') : '';
    return {selector:selector(e), diagnostic_locator:diagnosticLocator(e), label:label(e).slice(0,600),
      question:question.slice(0,1200), tag:e.tagName.toLowerCase(), type, native_type:nativeType, submit,
      commits_application:commitsApplication(e),
      in_form:!!form, form_selector:form ? selector(form) : '',
      native_form_owner_selector:e.form ? selector(e.form) : '',
       form_action:formAction, formaction:override, name:e.name || '',
      ...(pageAshbyNative ? {form_action_attr_present:!!form && form.hasAttribute('action'),
        form_action_raw:form ? form.getAttribute('action') : null,
        formaction_attr_present:e.hasAttribute('formaction'),
        formaction_raw:e.getAttribute('formaction')} : {}),
      disabled:!!e.disabled || e.getAttribute('aria-disabled') === 'true',
      required:!!e.required || e.getAttribute('aria-required') === 'true',
      value:type === 'password' ? '' : (e.value || e.getAttribute('data-value') || ''),
      has_value:!!e.value, checked:!!e.checked || e.getAttribute('aria-checked') === 'true',
      files:e.files ? Array.from(e.files).map(f=>f.name) : [],
      options:e.options ? Array.from(e.options)
        .map(o=>({value:o.value,label:clean(o.textContent)})) : [],
      combobox: type === 'combobox' ? (() => {
        const list = currentListbox(e);
        const options = list ? Array.from(list.querySelectorAll('[role="option"]'))
          .filter(o=>o.closest('[role="listbox"]') === list && visible(o))
          .map(o=>({selector:selector(o),unique:document.querySelectorAll(selector(o)).length === 1,
            tag:o.tagName.toLowerCase(), native_type:o.type || '',
            submit:(o.tagName === 'BUTTON' && o.type === 'submit' && !!o.form) ||
              o.type === 'submit' || o.type === 'image',
            navigates:(o.tagName === 'A' && !!o.href) || (!!graph && unsafeOption(o)),
            commits_application:commitsApplication(o),
            // Independent option safety is consumed only by the Lever location path.
            safe:!unsafeOption(o),
            value:o.getAttribute('data-value') || o.getAttribute('value') || '',
            ...(graph ? {value_attributes:['data-value','value']
              .filter(k=>o.hasAttribute(k)).map(k=>o.getAttribute(k))} : {}),
            label:graph ? o.innerText : clean(o.innerText),selected:o.getAttribute('aria-selected') === 'true'})) : [];
        const hiddenId = e.getAttribute('data-value-input');
        const hiddenName = e.getAttribute('data-value-name');
        const receivers = Array.from(document.querySelectorAll('input[type="hidden"]'))
          .filter(h=>h.form === form && ((hiddenId && h.id === hiddenId) ||
            (typeof e.contains === 'function' && e.contains(h)) ||
            (hiddenName && h.name === hiddenName)));
        return {...(graph || {}),...(quarantined.has(e) ? {quarantined:true} : {}),
          linked:!!list, link_id:quarantined.has(e) ? '' : graph ? (list?.id || '') : linkId(e),
          listbox_selector:list ? selector(list) : '', options,
          selected_text:clean(e.getAttribute('aria-valuetext') ||
            e.getAttribute('data-selected-label') || ''),
          displayed_text:clean(e.value || e.innerText || ''),
          selected_value:e.getAttribute('data-selected-value') || '',
          hidden_selector:receivers.length === 1 ? selector(receivers[0]) : '',
          hidden_value:receivers.length === 1 ? receivers[0].value : '', ...(graph || {})};
      })() : null,
      href:e.tagName === 'A' ? e.href : ''};
  });
  const description = document.querySelector('[data-automation-id="jobPostingDescription"]');
  const title = document.querySelector('[data-automation-id="jobPostingHeader"]')?.innerText
    || document.querySelector('h1')?.innerText || document.title;
  const text = document.body?.innerText || '';
  // BrowserSkill injects its own Interrupt button in a shadow root. Treating
  // that overlay as an uninspected employer form gated every normal page.
  // Exempt only that exact, button-only overlay; site shadow controls still gate.
  const shadowRoots = Array.from(document.querySelectorAll('*')).filter(e=>e.shadowRoot);
  const siteShadow = shadowRoots.some(e => {
    const nested = Array.from(e.shadowRoot.querySelectorAll(query));
    const ownOverlay = e.tagName === 'BROWSER-SKILL-OVERLAY' && nested.length > 0 &&
      !e.shadowRoot.querySelector('iframe') && nested.every(n =>
        n.tagName === 'BUTTON' && n.type === 'button' && label(n) === 'Interrupt');
    return !ownOverlay;
  });
  // A closed shadow root cannot be inspected. Detect visible custom hosts and
  // opaque widgets instead of treating 'no open shadow root' as completeness.
  const linkedParts = new Set();
  const linkedComboboxes = Array.from(document.querySelectorAll('[role="combobox"]'))
    .filter(e => {
      if (!refsStayInForm(e)) return false;
      if (pickerParts.has(e)) return true;
      if (pickerCandidate(e) || quarantined.has(e)) return false;
      if (newGraphs.get(e)) {
        const list = currentListbox(e);
        if (newGraphs.get(e).phase==='C1' && !list) return false;
        if (list) { linkedParts.add(list); list.querySelectorAll('[role="option"]').forEach(o=>linkedParts.add(o)); }
        return true;
      }
      // This quarantine is phone-fieldset-only: an unrelated #country with
      // its own ARIA listbox (or absent provisional target) must retain the
      // existing generic linked-combobox checks below. Receiverless C2 is
      // excluded by closedGraph and has no legacy linkId to downgrade through.
      const list = currentListbox(e);
      if (!visible(e) || (list && list.closest('form') && list.closest('form') !==
          (e.form || e.closest('form')))) return false;
      if (!linkId(e)) {
        // Only genuinely absent ARIA ownership is provisional. A present
        // duplicate, contradictory or unbound claim is NOT a closed picker.
        const declared = ['aria-controls', 'aria-owns'].some(key =>
          (e.getAttribute(key) || '').trim().length > 0);
        return !declared && e.getAttribute('aria-expanded') === 'false' &&
          !unsafeOption(e);
      }
      // An absent target is provisional only while the owning role stays safe.
      if (!list) return document.getElementById(linkId(e)) === null;
      const options = Array.from(list.querySelectorAll('[role="option"]'));
      if (options.some(o => o.closest('[role="listbox"]') !== list)) return false;
      linkedParts.add(list); options.forEach(o=>linkedParts.add(o));
      return true;
    });
  const opaqueNodes = Array.from(document.querySelectorAll('*')).filter(e =>
    (visible(e) || e.closest('form') || e.getAttribute('aria-required') === 'true') &&
    e.tagName !== 'BROWSER-SKILL-OVERLAY' && !pickerParts.has(e) &&
    (
      (e.matches('[role="combobox"]') && !linkedComboboxes.includes(e)) ||
      (e.matches('[role="listbox"],[role="option"]') && !linkedParts.has(e)) ||
      e.matches('[contenteditable]') || e.tagName.includes('-') ||
      (e.closest('form') && e.matches('canvas,object,embed,[aria-controls]') &&
        !e.matches('[role="combobox"]') && !linkedParts.has(e))));
  const opaqueControls = opaqueNodes.length>0;
  const inventoryVerified = document.readyState === 'complete' &&
    controls.length <= 500 && !opaqueControls && !siteShadow;
  return {url:location.href, title, text:text.slice(0,100000),
    description:description?.innerText || '', controls:controls.slice(0,500),
    truncated:controls.length > 500 || text.length > 100000,
    unsupported_frames:Array.from(document.querySelectorAll('iframe'))
      .filter(visible).map(e=>e.src),
    shadow_roots:siteShadow, opaque_controls:opaqueControls,
    opaque_count:opaqueNodes.length, opaque_locators:opaqueNodes.map(diagnosticLocator),
    inventory_verified:inventoryVerified, application_identity:applicationIdentity()};
})()""".replace('  /* WIDGET_GRAPH */', _WIDGET_GRAPH)


# Native application-root proof and the visible badge predicate have one owner.
_APPLICATION_ROOT = r"""
  const entryLabel = e => clean(Array.from(e.labels || []).map(x=>x.innerText).join(' ') ||
    (e.getAttribute('aria-labelledby') || '').split(' ')
      .map(id=>document.getElementById(id)?.innerText || '').join(' ') ||
    e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || e.name);
  const candidateVisible = e => {
    const rects=Array.from(e.getClientRects());
    if(!rects.length) return false;
    let fixed=false;
    for(let n=e;n && n.nodeType===1;n=n.parentElement) {
      const style=getComputedStyle(n);
      if(!style || style.display==='none' || style.visibility==='hidden' ||
          style.visibility==='collapse' || !(Number(style.opacity)>0)) return false;
      if(style.position==='fixed') fixed=true;
    }
    return rects.some(r=>r.width>0 && r.height>0 &&
      r.right+scrollX>0 && r.bottom+scrollY>0 &&
      (!fixed || (r.right>0 && r.bottom>0)));
  };
  const forms = Array.from(document.querySelectorAll('form')).filter(f =>
    Array.from(f.querySelectorAll('input,button')).filter(e=>!excluded.has(e)).some(e => {
      const group=e.closest('fieldset,[role="radiogroup"],[data-automation-id="formField"],.form-group');
      const question=clean(group?.querySelector('legend,[data-automation-id="formLabel"]')?.innerText ||
        group?.innerText || '');
      return (e.type==='file' && /resume|résumé|\bcv\b/i.test(question+' '+entryLabel(e))) ||
        (['submit','image'].includes(e.type) && [entryLabel(e),
          e.tagName==='INPUT' && e.type==='submit' ? e.value : ''].some(t =>
            ['submit application','send application','complete application',
              'finish application','apply now'].includes(clean(t).toLowerCase())));
    }));
  const nativeRoot = forms.length===1 ? forms[0] : null;
  // PAGE route alone is not a job witness. Both independently evaluated readers
  // report explicit raw attributes plus a complete non-value native census.
  const ashbyNative = location.protocol==='https:' && location.hostname==='jobs.ashbyhq.com';
  const nativeCandidates = ashbyNative ? Array.from(document.querySelectorAll('form'))
    .map(f => {
      const members=Array.from(document.querySelectorAll(query)).filter(e=>
        !excluded.has(e) && (e.form || e.closest('form'))===f);
      return {selector:selector(f), plausible:forms.includes(f),
        action:f.action || '', action_attr_present:f.hasAttribute('action'),
        action_attr:f.getAttribute('action'),
        controls:members.map(e => {
          const nativeType=['INPUT','BUTTON'].includes(e.tagName) ? e.type : '';
          const type=e.getAttribute('role') || nativeType || e.getAttribute('type') || e.tagName.toLowerCase();
          const labels=Array.from(e.labels || []);
          return {selector:selector(e),tag:e.tagName.toLowerCase(),type,native_type:nativeType,
            submit:['submit','image'].includes(nativeType),
            caption:entryLabel(e),owned:f.contains(e),native_owner:e.form ? selector(e.form) : '',
            declared_owner:e.getAttribute('form'),label_count:labels.length,
            labels_owned:labels.every(l=>f.contains(l)),
            disabled:!!e.disabled || e.getAttribute('aria-disabled')==='true',
            required:!!e.required || e.getAttribute('aria-required')==='true',
            visible:candidateVisible(e),
            readonly:!!e.readOnly || e.hasAttribute('readonly'),
            override_present:e.hasAttribute('formaction'),
            override_raw:e.getAttribute('formaction'),
            override_resolved:e.hasAttribute('formaction') ? (e.formAction || '') : ''};
        })};
    }) : [];
  const rectOf = (e, bounding=false) => {
    try {
      const r = bounding ? e.getBoundingClientRect() : e.getClientRects()[0];
      return r && Number.isFinite(r.width) && Number.isFinite(r.height) &&
        r.width>=0 && r.height>=0 ? {width:r.width,height:r.height} : null;
    } catch (_) { return null; }
  };
"""

# Structural admission belongs only to ApplicationEntryProvenance, never an alias.
_ASHBY_ROOT = r"""
  // The pane is structural evidence only: never read its values or build targets.
  const containers=Array.from(document.querySelectorAll('.ashby-application-form-container'));
  const submitters=Array.from(document.querySelectorAll('button.ashby-application-form-submit-button'));
  const panes=Array.from(document.querySelectorAll('.ashby-application-form-autofill-pane'));
  const container=containers.length===1 ? containers[0] : null;
  const exactSubmit=submitters.length===1 ? submitters[0] : null;
  const wrapper=container?.parentElement;
  // Layout order is decoration, not authority. Every additional sibling must
  // be inert; the pane remains quarantined and all controls stay inventoried.
  const inertSibling=e=>{
    if(!['DIV','SPAN'].includes(e.tagName) || e.attributes.length) return false;
    const nodes=[e,...Array.from(e.querySelectorAll('*'))];
    const interactive=query+',iframe,[contenteditable],[role],[tabindex],summary,details,form,object,embed,area[href],audio[controls],video[controls],label[for],[autofocus],[controls]';
    return !e.querySelectorAll(interactive).length && nodes.every(n=>
      !n.shadowRoot && !Array.from(n.attributes).some(a=>/^on/i.test(a.name)));
  };
  const siblings=wrapper ? Array.from(wrapper.children) : [];
  const ordered=!!wrapper && wrapper.tagName==='DIV' && !wrapper.getAttribute('class') &&
    container?.tagName==='DIV' && exactSubmit?.tagName==='BUTTON' &&
    panes.length===1 && panes[0].tagName==='DIV' && panes[0].parentElement===wrapper &&
    exactSubmit?.parentElement===wrapper &&
    siblings.filter(n=>[container,panes[0],exactSubmit].includes(n)).length===3 &&
    siblings.every(n=>n===container || n===panes[0] || n===exactSubmit || inertSibling(n));
  const structuralRoot=!forms.length && ordered ? wrapper : null;
  const root=nativeRoot || structuralRoot;
  let structuralError='';
  if (!forms.length && (containers.length>1 || submitters.length>1 || panes.length>1))
    structuralError='ambiguous application roots';
  else if (!forms.length && container && exactSubmit && !ordered && !(
    panes.length===0 && wrapper?.tagName==='DIV' && !wrapper.getAttribute('class') &&
    exactSubmit.parentElement===wrapper && wrapper.children.length===2 &&
    wrapper.children[0]===container && wrapper.children[1]===exactSubmit))
    structuralError='application root sibling identity mismatch';
  const paneNodes=new Set();
  if (!forms.length) for (const p of panes) {
    paneNodes.add(p); p.querySelectorAll('*').forEach(n=>paneNodes.add(n));
  }
  paneNodes.forEach(n=>excluded.add(n));
  // This full-node witness is compared only by the single upload transition.
  // Signatures never export raw static text or value-bearing attributes.
  const signature = text => {
    const h=[2166136261,2246822519,3266489917,668265263];
    for (const c of text) for(let i=0;i<h.length;i++)
      h[i]=Math.imul(h[i]^c.codePointAt(0),16777619+i*2)>>>0;
    return h.map(n=>n.toString(16).padStart(8,'0')).join('');
  };
  const uploadTree={complete:false,nodes:[],input:null,container:null,dropzone:null,
    items:[],svgs:[],attached:false};
  if (structuralRoot) try {
    const source=[], stack=[[structuralRoot,-1]];
    while(stack.length) {
      const [n,parent]=stack.pop();
      if (![1,3].includes(n.nodeType)) throw Error('unreadable structural node');
      if(source.length>=4096) throw Error('structural node bound');
      const index=source.length; source.push(n);
      let attrs=[], text='';
      if(n.nodeType===1) {
        if(n.shadowRoot || !n.attributes || !n.childNodes) throw Error('unreadable subtree');
        attrs=Array.from(n.attributes,a=>[a.name,
          /value|token|response|password/i.test(a.name)
            ? '' : signature(a.value)]).sort((a,b)=>a[0].localeCompare(b[0]));
      } else {
        // Never read an applicant value, autofill, script or CAPTCHA response.
        const p=n.parentElement;
        if(!p || !p.closest('input,textarea,select,[contenteditable],script,style,.ashby-application-form-autofill-pane')
            && !responseNodes.some(r=>r===p || r.contains(p))) text=signature(n.textContent);
      }
      uploadTree.nodes.push({parent,tag:n.nodeType===3 ? '#text' : n.localName,
        attrs,text,selector:n.nodeType===1 ? (n.id ? '#'+signature(n.id) : selector(n)) : ''});
      if(n.nodeType===1) {
        const children=Array.from(n.childNodes);
        if(children.length!==n.childNodes.length) throw Error('incomplete subtree');
        for(let i=children.length-1;i>=0;i--) stack.push([children[i],index]);
      }
    }
    const inputs=source.filter(n=>n.nodeType===1 && n.id==='_systemfield_resume');
    const input=inputs.length===1 ? inputs[0] : null;
    const file=input?.closest('div.ashby-application-form-input-file');
    const drops=file ? Array.from(file.querySelectorAll('button.ashby-application-form-input-file-dropzone-upload')) : [];
    const drop=drops.length===1 ? drops[0] : null;
    if(!input || input.type!=='file' || !file || !container.contains(file) ||
        file.querySelectorAll('input').length!==1 || !drop || drop.form ||
        structuralRoot.querySelectorAll('button.ashby-application-form-input-file-dropzone-upload').length!==1 ||
        drop.hasAttribute('form') || drop.hasAttribute('formaction') ||
        drop.disabled || drop.closest('[inert]') || !visible(drop)) throw Error('ambiguous file container');
    uploadTree.input=source.indexOf(input); uploadTree.container=source.indexOf(file);
    uploadTree.dropzone=source.indexOf(drop);
    uploadTree.nodes[uploadTree.input].target_selector=selector(input);
    uploadTree.nodes[uploadTree.dropzone].target_selector=selector(drop);
    uploadTree.svgs=Array.from(drop.querySelectorAll('svg')).filter(n=>
      !n.parentElement.closest('svg')).map(n=>source.indexOf(n));
    const items=Array.from(structuralRoot.querySelectorAll('div.ashby-application-form-input-file-item'));
    uploadTree.items=items.map(n=>source.indexOf(n));
    if(items.length===1) {
      const item=items[0], names=item.querySelectorAll('p.ashby-application-form-input-file-item-name'),
        deletes=item.querySelectorAll('button.ashby-application-form-input-file-item-delete[title]');
      const name=names[0], del=deletes[0];
      const passiveAttrs=(n,allowed)=>Array.from(n.attributes).every(a=>allowed.includes(a.name));
      const passiveIcon=n=>n.namespaceURI==='http://www.w3.org/2000/svg' &&
        ['svg','g','path','circle','ellipse','line','polyline','polygon','rect'].includes(n.localName) &&
        Array.from(n.attributes).every(a=>!/^on|href|tabindex|role|form/i.test(a.name));
      const passiveName=n=>['span','strong','em','b','i'].includes(n.localName) &&
        Array.from(n.attributes).every(a=>!/^on|^(role|tabindex|href|contenteditable)$/i.test(a.name));
      const valid=item.parentElement===file && names.length===1 && deletes.length===1 &&
        item.children.length===2 && name.parentElement===item && del.parentElement===item &&
        name.querySelectorAll('svg').length<=1 &&
        Array.from(name.querySelectorAll('*')).every(n=>
          n.closest('svg') ? passiveIcon(n) : passiveName(n)) &&
        Array.from(del.querySelectorAll('*')).every(passiveIcon) &&
        !clean(del.textContent) &&
        passiveAttrs(item,['id','class']) && passiveAttrs(name,['id','class']) &&
        passiveAttrs(del,['id','class','title','type']) &&
        [null,'button'].includes(del.getAttribute('type')) &&
        Array.from(item.childNodes).every(n=>n===name || n===del ||
          n.nodeType===3 && !n.textContent.trim()) &&
        !del.form && !del.hasAttribute('form') && !del.hasAttribute('formaction') &&
        !del.getAttribute('role') && !del.disabled && !del.closest('[inert]') &&
        input.files?.length===1 && clean(name.innerText)===input.files[0].name;
      if(valid) {
        uploadTree.attached=true; uploadTree.filename=clean(name.innerText);
        uploadTree.delete=source.indexOf(del);
        // A proven passive delete never enters any downstream action consumer.
        excluded.add(del);
      }
    }
    uploadTree.complete=true;
  } catch (_) { uploadTree.complete=false; }
  // Description links remain in the original PAGE inventory; they are not
  // input/receiver candidates. Role-button links still enter this census and
  // no link acquires action authority through the structural branch.
  const evidenceNodes=Array.from(document.querySelectorAll(query.replace('a[href],','')+
    ',input[type="hidden"],[contenteditable],[role="listbox"],canvas,object,embed'))
    .filter(e=>!excluded.has(e));
  const entryVisible=e => {
    if (!visible(e)) return false;
    // Clipping can hide a text recipient despite a positive layout rectangle.
    // Unknown clipping geometry is not proof of an editable applicant target.
    for (let n=e;n;n=n.parentElement) {
      const s=getComputedStyle(n);
      if ((s.clip && s.clip!=='auto') || (s.clipPath && s.clipPath!=='none')) return false;
    }
    return true;
  };
  const rootEvidence=!forms.length && (containers.length || submitters.length) ?
    evidenceNodes.slice(0,501).map(e => {
      const sel=selector(e), id=e.id || '';
      const labels=id ? Array.from(document.querySelectorAll('label[for]'))
        .filter(l=>l.getAttribute('for')===id) : [];
      const actualLabels=Array.from(e.labels || []);
      const linked=labels.length===1 && actualLabels.length===1 && actualLabels[0]===labels[0] &&
        labels[0].matches('label.ashby-application-form-question-title') &&
        !!container && container.contains(labels[0]) && container.contains(e) &&
        document.querySelectorAll('#'+CSS.escape(id)).length===1 &&
        document.getElementById(labels[0].getAttribute('for'))===e;
      return {selector:sel,unique:document.querySelectorAll(sel).length===1,
        tag:e.tagName.toLowerCase(),type:e.getAttribute('role') || e.type || e.tagName.toLowerCase(),
        native_type:['INPUT','BUTTON'].includes(e.tagName) ? e.type : '',
        id,label:linked ? clean(labels[0].innerText) : '',label_selector:linked ? selector(labels[0]) : '',
        associated:linked,inside:!!structuralRoot && structuralRoot.contains(e),
        in_container:!!container && container.contains(e),
        native_owner:e.form ? selector(e.form) : '',
        declared_owner:e.getAttribute('form') || '',formaction:e.getAttribute('formaction'),
        submit:e===exactSubmit,untyped:e.getAttribute('type')===null,
        dropzone:e.matches('button.ashby-application-form-input-file-dropzone-upload'),
        required:!!e.required,disabled:!!e.disabled || e.matches(':disabled') || e.getAttribute('aria-disabled')==='true',
        readonly:!!e.readOnly || e.hasAttribute('readonly'),inert:!!e.closest('[inert]'),
        visible:entryVisible(e),value:e.type==='password' ? '' : (e.value || ''),
        files:e.files ? Array.from(e.files).map(f=>f.name) : [],
        autocomplete:e.getAttribute('autocomplete') || '',role:e.getAttribute('role') || '',
        iti:!!e.closest('.iti'),contenteditable:e.hasAttribute('contenteditable')};
    }) : [];
  const formless=!forms.length && (containers.length || submitters.length) ? {
    root:structuralRoot ? selector(structuralRoot) : '',
    container:container ? selector(container) : '',
    submit:exactSubmit ? selector(exactSubmit) : '',
    excluded:Array.from(paneNodes).filter(e=>e.matches(query)).map(selector),
    pane:panes.length===1 ? selector(panes[0]) : '',
    error:structuralError,truncated:evidenceNodes.length>500} : null;
"""

# Hidden R and the other providers stay exclusively in the entry classifier.
_VISIBLE_RECAPTCHA = r"""
  const visibleRecaptcha = (origin,path,shown,outside,badge,bounds) => {
    const provider=['https://www.recaptcha.net','https://www.google.com'].includes(origin);
    if (provider && path==='/recaptcha/enterprise/bframe' && shown) return 'visible-recaptcha';
    if (provider && path==='/recaptcha/enterprise/anchor' && shown && outside &&
        badge && !!bounds && bounds.width===256 && bounds.height===60) return 'R';
    return 'unknown';
  };
"""

# Metadata only: neither consumer may inspect a response value to recognize it.
_CAPTCHA_RESPONSE_NODES = r"""
  const responseNodes = Array.from(document.querySelectorAll('input,textarea')).filter(e =>
    ['h-captcha-response','g-recaptcha-response'].includes(e.getAttribute('name')));
"""

# Inserted before either reader's graph: filtering the final list is too late,
# because closedGraph and linked receivers read hidden values eagerly.
_ENTRY_OBSERVATION = _CAPTCHA_RESPONSE_NODES + r"""
  const reservedButtons = Array.from(document.querySelectorAll('button'))
    .filter(e => e.id === 'hcaptchaSubmitBtn');
  const excluded = new Set([...responseNodes, ...reservedButtons]);
""" + _APPLICATION_ROOT + _ASHBY_ROOT + _VISIBLE_RECAPTCHA + r"""
  // W is a passive row, not permission to act on either widget node. All raw
  // audits are local to W; the canonical inventory and ordinary PAGE stay intact.
  let wUnreadable=false;
  const wNodes = row => {
    try {
      const list = value => {
        if (!value || !Number.isSafeInteger(value.length) || value.length<0) throw Error('unknown collection');
        const result=Array.from(value);
        if (result.length!==value.length || result.some(n=>!n || n.nodeType!==1)) throw Error('unknown node');
        return result;
      };
      const attrs = e => {
        if (!e.attributes || !Number.isSafeInteger(e.attributes.length)) throw Error('unknown attributes');
        return Array.from(e.attributes, a => {
          if (typeof a.name!=='string' || typeof a.value!=='string') throw Error('unknown attribute');
          return a.name.toLowerCase();
        });
      };
      const buttons=list(row.querySelectorAll('button'));
      const links=list(row.querySelectorAll('a.learn-more-link'));
      if (buttons.length!==1 || !buttons[0].matches('button.awli-button') || links.length>1) return null;
      const button=buttons[0], link=links[0];
      if ([button,...links].some(n=>n.namespaceURI!=='http://www.w3.org/1999/xhtml')) return null;
      if (button.getAttribute('type')!=='button' || button.type!=='button' ||
          ['name','form','formaction'].some(a=>button.hasAttribute(a))) return null;
      // A contained form owner is expected; an explicit form attribute is not.
      if (link) {
        const raw=link.getAttribute('href');
        let valid=false;
        try {
          const url=new URL(raw);
          valid=typeof raw==='string' && /^https:\/\//i.test(raw) && raw===raw.trim() && url.protocol==='https:' &&
            url.origin!==new URL(location.href).origin && link.matches('a[href]');
        } catch (_) {}
        if (!valid) return null;
      }
      const anchors=list(row.querySelectorAll('a'));
      if (anchors.length!==(link ? 1 : 0) || (link && anchors[0]!==link)) return null;
      const permitted=link ? [button,link] : [button];
      const inventory=list(row.querySelectorAll(query));
      if (inventory.length!==permitted.length || inventory.some(n=>!permitted.includes(n))) return null;
      const subtree=[row,...list(row.querySelectorAll('*'))];
      for (const n of subtree) {
        const names=attrs(n), role=n.getAttribute('role');
        if (role!==null && (typeof role!=='string' || role.trim()!=='')) return null;
        if (!permitted.includes(n)) {
          if (names.includes('tabindex')) return null;
          if (names.some(a=>a.startsWith('on'))) return null;
        }
        if (typeof n.localName!=='string' || !n.localName || n.localName.includes('-') ||
            n.shadowRoot!==null) return null;
        if (n!==row && n.matches('summary,area[href],audio[controls],video[controls],object,embed')) return null;
      }
      if (list(root.elements).some(n=>row.contains(n) && n!==button)) return null;
      // labels includes external label[for] and wrapping labels. Missing evidence
      // is unknown, never an empty name. Do not read the iframe child document.
      if (list(button.labels).length || button.closest('label')) return null;
      const nameAttrs=['aria-label','aria-labelledby','aria-owns','title','alt',
        'placeholder','label','value','aria-description','aria-describedby'];
      for (const n of [button,...list(button.querySelectorAll('*'))]) {
        if (attrs(n).some(a=>nameAttrs.includes(a))) return null;
        if (typeof n.textContent!=='string' || n.textContent.trim() ||
            typeof n.innerText!=='string' || n.innerText.trim()) return null;
        for (const pseudo of ['::before','::after']) {
          const content=getComputedStyle(n,pseudo).content;
          if (typeof content!=='string' || !['none','normal','""',"''"].includes(content)) return null;
        }
      }
      return permitted;
    } catch (_) { wUnreadable=true; return null; }
  };
  const entryFrames = Array.from(document.querySelectorAll('iframe')).map(e => {
    const raw = e.getAttribute('src');
    let origin='', path='', diagnosticOrigin='', diagnosticPath='';
    // Absolute-only H/R/W/X/Z classifier inputs are unchanged. Resolve
    // relative sources only for the parent-page diagnostic metadata.
    try { if (raw!==null) { const u=new URL(raw); origin=u.origin; path=u.pathname; } } catch (_) {}
    try { if (raw!==null) {
      const u=new URL(raw, document.baseURI);
      diagnosticOrigin=u.origin; diagnosticPath=u.pathname;
    }} catch (_) {}
    const style=getComputedStyle(e), hidden=style.visibility==='hidden' || style.display==='none';
    const shown=style.visibility==='visible' && style.display!=='none';
    const outside=!!root && !root.contains(e), box=rectOf(e);
    // Ashby already uses structuralRoot for outside; do not lend it to non-Ashby R.
    const hiddenROutside=nativeRoot ? !nativeRoot.contains(e) :
      ashbyNative && !!structuralRoot && !structuralRoot.contains(e);
    const parent=e.parentElement;
    const h=origin==='https://newassets.hcaptcha.com' && path.endsWith('/static/hcaptcha-enclave.html') &&
      !!nativeRoot && parent?.matches('div#h-captcha.h-captcha') && parent.parentElement===nativeRoot;
    const r=['https://www.recaptcha.net','https://www.google.com'].includes(origin) &&
      ['/recaptcha/enterprise/anchor','/recaptcha/enterprise/bframe'].includes(path);
    const badge=!!e.closest('div.grecaptcha-badge'), bounds=rectOf(e,true);
    let n=e.parentElement;
    const chain=['span.IN-widget','div.awli-button-container','div.application-field',
      'li.application-question.awli-application-row'];
    const ancestry=[];
    for (const shape of chain) {
      if (!n || !n.matches(shape)) break;
      ancestry.push(n); n=n.parentElement;
    }
    const row=ancestry.length===4 ? ancestry[3] : null;
    const w=(raw===null || raw==='about:blank') && e.getAttribute('title')==='LinkedIn Embedded Content' &&
      !!nativeRoot && !!row && nativeRoot.contains(row) && !!box && box.width<=400 && box.height<=100 &&
      !row.required && row.getAttribute('aria-required')!=='true' && !row.hasAttribute('required') &&
      !row.matches(query) &&
      !row.querySelector('[contenteditable],[required],[aria-required="true"]');
    const widget=w ? wNodes(row) : null;
    const x=raw===null && !!box && box.width<=1 && box.height<=1 &&
      style.visibility==='hidden' && outside;
    const visibleR=visibleRecaptcha(origin,path,shown,outside,badge,bounds);
    // AC-Z is scoped to the entry reader; ordinary PAGE gains no frame waiver.
    const z=raw===null && e.getAttribute('title')===null &&
      !e.getAttributeNames().some(a=>a.startsWith('data-') || a.startsWith('aria-')) &&
      style.display==='none' && e.getClientRects().length===0 && outside &&
      parent?.tagName==='DIV' && !parent.id && !parent.getAttribute('class') &&
      parent.parentElement===document.body;
    let status='unknown';
    // Visible bframe precedence is independent of root proof/placement.
    if (visibleR==='visible-recaptcha') status=visibleR;
    else if (h) status=hidden ? 'H' : 'visible-hcaptcha';
    else if ((r && hiddenROutside && hidden) || visibleR==='R') status='R';
    else if (widget) { status='W'; widget.forEach(n=>excluded.add(n)); }
    else if (x) status='X';
    else if (z) status='Z';
    // A stale complete child (including about:blank) can outlive a src
    // change. Check full URLs internally, but export no URL payload or DOM.
    let formControls='unknown';
    if (!['H','R','W','X','Z'].includes(status)) try {
      const child=e.contentDocument;
      let current=false;
      if (child && raw!==null && !e.hasAttribute('srcdoc') &&
          typeof child.URL==='string' && child.URL) {
        try {
          current=new URL(raw, document.baseURI).href===new URL(child.URL).href;
        } catch (_) {}
      }
      if (current && child.readyState==='complete') {
        if (child.querySelector('input,textarea,select,button,[role="button"],' +
            '[role="textbox"],[role="searchbox"],[role="combobox"],' +
            '[role="checkbox"],[role="radio"],[role="switch"],' +
            '[role="slider"],[role="spinbutton"],[role="listbox"],' +
            '[role="option"],[role="menuitem"],[role="menuitemcheckbox"],' +
            '[role="menuitemradio"],[contenteditable]'))
          formControls='yes';
        else if (!child.querySelector('iframe,object,embed,form,[role],[tabindex],' +
            'a[href],area[href],summary,details,audio[controls],video[controls]')) {
          const descendants=child.querySelectorAll('*');
          if (descendants.length<=1024 && !Array.from(descendants).some(n =>
              n.shadowRoot || n.tagName.includes('-') ||
              Array.from(n.attributes || []).some(a => /^on/i.test(a.name))))
            formControls='no';
        }
      }
    } catch (_) {}
    return {selector:selector(e),origin:diagnosticOrigin || origin,
      path:diagnosticPath || path,source:raw===null ? 'absent' : raw==='about:blank' ? 'blank' : 'url',
      visibility:style.visibility,display:style.display,outside,root:root ? selector(root) : '',
      ancestors:(() => {const a=[]; for(let p=e.parentElement;p;p=p.parentElement) a.push(selector(p)); return a;})(),
      box,badge,bounds,status,
      // Parent-reader proof; the rootless frame remains unadmitted.
      ...(status==='unknown' && ashbyNative && !root && r && hidden
        ? {rootless_hidden_r:true} : {}),
      ...(!['H','R','W','X','Z'].includes(status) ? {form_controls:formControls} : {})};
  });
  let responseError=false;
  const entryResponses=responseNodes.map(e => {
    const name=e.getAttribute('name'), owner=e.form || e.closest('form');
    const signature=name==='h-captcha-response' ? 'H' : 'R';
    if (responseNodes.filter(n=>n.getAttribute('name')===name).length!==1 ||
        !entryFrames.some(f=>f.status===signature) || (owner && owner!==root)) responseError=true;
    return {selector:selector(e),name,form:owner ? selector(owner) : ''};
  });
  // Reject a graph claiming a reserved receiver before closedGraph/newGraphs or
  // any linked combobox path can touch its value (including a throwing getter).
  for (const c of document.querySelectorAll('[role="combobox"]')) {
    const form=c.form || c.closest('form');
    const labelId=c.getAttribute('aria-labelledby');
    const labels=labelId ? document.querySelectorAll('#'+CSS.escape(labelId)) : [];
    let field=c.parentElement;
    if (labels.length===1) while(field && !field.contains(labels[0])) field=field.parentElement;
    else field=c.closest('[data-automation-id="formField"],.form-group');
    if (responseNodes.some(h =>
      c.getAttribute('data-value-input')===h.id && !!h.id ||
      c.getAttribute('data-value-name')===h.getAttribute('name') || c.contains(h) ||
      (field && field!==form && field.contains(h) && (h.form || h.closest('form'))===form)))
      responseError=true;
  }
  const entry={frames:entryFrames,responses:entryResponses,response_error:responseError,
    // A native Ashby element is only a CANDIDATE until Python binds the raw
    // action to the typed tracked job. A defaulted action cannot set root.
    root:ashbyNative ? null : nativeRoot ?
      {selector:selector(nativeRoot),action:nativeRoot.action} : null,
    ...(ashbyNative ? {native_candidates:nativeCandidates} : {}),
    formless,root_evidence:rootEvidence,upload_tree:structuralRoot ? uploadTree : null,
    helper_challenge:reservedButtons.some(e=>!(e.tagName === 'BUTTON' && e.id === 'hcaptchaSubmitBtn' &&
          e.matches('.hidden')) ||
      (!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' &&
        getComputedStyle(e).display!=='none'))};
  // An unreadable W name must reach the named frame Gate before generic graphs
  // attempt to read that same property as an ordinary applicant control.
  if (responseError || wUnreadable) return {url:location.href,controls:[],entry,inventory_verified:false};
"""


def entry_reader(script: str) -> str:
    """Scope the two code-owned readers without changing ordinary PAGE/graphs.

    All exclusions precede the shared graph; no protected value is read even
    transiently. The fixed witness observes provider identities independently.
    """
    script = script.replace(_WIDGET_GRAPH, _ENTRY_OBSERVATION + _WIDGET_GRAPH)
    script = script.replace('nodes.filter(e =>', 'nodes.filter(e => !excluded.has(e) &&')
    script = script.replace("Array.from(document.querySelectorAll(query)).filter(e =>",
                            "Array.from(document.querySelectorAll(query)).filter(e => !excluded.has(e) &&")
    script = script.replace('.filter(h=>h.form === form', '.filter(h=>!excluded.has(h) && h.form === form')
    script = script.replace('return {selector:ownSelector, diagnostic_locator:diagnosticLocator(e), unique:',
                            "return {...(formless ? {label:entryLabel(e), value:e.value || '', "
                            "submit:['submit','image'].includes(e.type), "
                            "required:!!e.required,files:e.files ? Array.from(e.files).map(f=>f.name) : []} : {}), "
                            'selector:ownSelector, diagnostic_locator:diagnosticLocator(e), unique:')
    script = script.replace('return {url:location.href, title,', 'return {entry, url:location.href, title,')
    script = script.replace('return {url: location.href, controls:', 'return {entry, url: location.href, controls:')
    script = script.replace("unsupported_frames:Array.from(document.querySelectorAll('iframe'))\n"
                            "      .filter(visible).map(e=>e.src)", 'unsupported_frames:[]')
    return script


ENTRY_PAGE = entry_reader(PAGE)


_GREENHOUSE_OBSERVATION = _CAPTCHA_RESPONSE_NODES + r"""
  // All response nodes stay out of root probing and every later value traversal.
  // Only the architect-bound passive badge textarea may pass the pre-read Gate.
  const excluded = new Set(responseNodes);
""" + _APPLICATION_ROOT.replace('filter(e=>!excluded.has(e))',
                                'filter(e=>e.form===f && !excluded.has(e))') + _VISIBLE_RECAPTCHA + r"""
  const root=nativeRoot;
  // Both readers use ordinary PAGE's frame visibility, not witness editability.
  // Otherwise a collapse/transparent transition could disappear from the witness.
  const greenhouseFrames = Array.from(document.querySelectorAll('iframe')).filter(e =>
    !!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
  const greenhouse = {response_controls:false, badge_responses:0,
    root:root ? {selector:selector(root),action:root.action} : null,
    frames:greenhouseFrames.map(e => {
      const raw=e.getAttribute('src');
      let origin='', path='';
      try { if(raw!==null) { const u=new URL(raw); origin=u.origin; path=u.pathname; } } catch (_) {}
      const style=getComputedStyle(e), bounds=rectOf(e,true);
      const shown=style.visibility==='visible' && style.display!=='none';
      const outside=!!root && !root.contains(e), badge=!!e.closest('div.grecaptcha-badge');
      return {selector:selector(e),origin,path,visibility:style.visibility,display:style.display,
        outside,badge,bounds,status:visibleRecaptcha(origin,path,shown,outside,badge,bounds)};
    })};
  const badges=Array.from(document.querySelectorAll('div.grecaptcha-badge'));
  const badgeResponse = e => {
    const badge=e.parentElement, wrapper=badge?.parentElement, style=getComputedStyle(e);
    const names=Array.from(e.attributes || []).map(a=>a.name).sort().join(',');
    return responseNodes.length===1 && e.tagName==='TEXTAREA' &&
      e.getAttribute('name')==='g-recaptcha-response' &&
      /^g-recaptcha-response-\d+$/.test(e.getAttribute('id') || '') &&
      e.getAttribute('class')==='g-recaptcha-response' && names==='class,id,name,style' &&
      !!root && !root.contains(e) && !e.form && !e.closest('form') &&
      !e.getClientRects().length && style.display==='none' &&
      badges.length===1 && badge===badges[0] && badge.matches('div.grecaptcha-badge') &&
      wrapper?.tagName==='DIV' && !wrapper.hasAttribute('id') && !wrapper.hasAttribute('class') &&
      wrapper.parentElement===document.body &&
      greenhouseFrames.some((f,i)=>badge.contains(f) && greenhouse.frames[i].status==='R');
  };
  const safeResponses=responseNodes.filter(badgeResponse);
  greenhouse.badge_responses=safeResponses.length;
  greenhouse.response_controls=safeResponses.length!==responseNodes.length;
  // A page cannot turn even a passive response node into an applicant receiver.
  for (const c of document.querySelectorAll('input,textarea,select,[role="combobox"]')) {
    if (excluded.has(c)) continue;
    if (responseNodes.some(r => c.getAttribute('data-value-input')===r.id && !!r.id ||
        c.getAttribute('data-value-name')===r.getAttribute('name'))) greenhouse.response_controls=true;
  }
  // PAGE and closedGraph eagerly read values. Returning after the shared frame
  // observation preserves visible-bframe precedence without reading a response.
  if (greenhouse.response_controls) return {url:location.href, greenhouse};
"""


def greenhouse_reader(script: str) -> str:
    """Visible-only observation, selected by current typed alias in the controller."""
    script = script.replace(_WIDGET_GRAPH, _GREENHOUSE_OBSERVATION + _WIDGET_GRAPH)
    script = script.replace('nodes.filter(e =>', 'nodes.filter(e => !excluded.has(e) &&')
    script = script.replace("Array.from(document.querySelectorAll(query)).filter(e =>",
                            "Array.from(document.querySelectorAll(query)).filter(e => !excluded.has(e) &&")
    script = script.replace("Array.from(document.querySelectorAll('[role=\"combobox\"]'))",
                            "Array.from(document.querySelectorAll('[role=\"combobox\"]')).filter(e=>!excluded.has(e))")
    script = script.replace('return {url:location.href, title,',
                            'return {greenhouse, url:location.href, title,')
    script = script.replace('return {url: location.href, controls:',
                            'return {greenhouse, url: location.href, controls:')
    script = script.replace("unsupported_frames:Array.from(document.querySelectorAll('iframe'))\n"
                            "      .filter(visible).map(e=>e.src)",
                            "unsupported_frames:greenhouse.frames.filter(f=>f.status!=='R').map(f=>f.selector)")
    return script


GREENHOUSE_PAGE = greenhouse_reader(PAGE)


def scoped_identity_conflict(page: dict, root_selector: str, job_id: str,
                             company: str) -> str:
    """Report only an explicitly labelled disagreement inside the bound form."""
    if not isinstance(root_selector, str) or not root_selector:
        return ''
    witnesses = page.get('application_identity') or []
    if not isinstance(witnesses, list):
        return 'application identity'
    matches = [w for w in witnesses if isinstance(w, dict)
               and w.get('selector') == root_selector]
    if len(matches) > 1:
        return 'application identity'
    if not matches:
        return ''
    observed = matches[0]
    ids, employers = observed.get('job_ids'), observed.get('employers')
    if not isinstance(ids, list) or not isinstance(employers, list):
        return 'application identity'
    if any(not isinstance(v, str) or v.casefold() != job_id.casefold() for v in ids):
        return 'job ID'
    normal = lambda s: ' '.join(s.replace('-', ' ').casefold().split())
    if any(not isinstance(v, str) or normal(v) != normal(company) for v in employers):
        return 'employer'
    return ''


def submission_like_transition(before: dict, after: dict) -> bool:
    """A new completion-looking page after JS Continue warrants an existing hold."""
    import re
    from urllib.parse import urlsplit
    if any(c.get('submit') or c.get('commits_application')
           for c in after.get('controls', []) if isinstance(c, dict)):
        return False
    if re.search(r'/(?:thanks|thank-you|confirmation|success|submitted)(?:/|$)',
                 urlsplit(after.get('url') or '').path, re.I) and after.get('url') != before.get('url'):
        return True
    return after.get('title') != before.get('title') and bool(re.search(
        r'thank you|application (?:received|submitted|complete)',
        after.get('title') or '', re.I))
