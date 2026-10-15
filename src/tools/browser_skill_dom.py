"""Read-only DOM inventory. Models never supply JavaScript to the browser."""

_WIDGET_GRAPH = r"""
  // Executed afresh in each reader. No page attributes or model flags retain admission.
  const nativeForm = e => e.form || e.closest('form');
  const uniqueNode = e => !!e && document.querySelectorAll(selector(e)).length === 1;
  const byId = id => {
    const hits = id ? document.querySelectorAll('#'+CSS.escape(id)) : [];
    return hits.length === 1 ? hits[0] : null;
  };
  const attr = (e, k) => e.getAttribute(k) || '';
  const editable = 'input:not([type="hidden"]),textarea,select,[role="combobox"],[contenteditable]';
  const singleBranch = e => {
    const names = attr(e, 'class').split(/\s+/);
    return names.some(s=>s.endsWith('__single-value') || /(?:^|-)singleValue$/.test(s));
  };
  const placeholderBranch = e => attr(e, 'class').split(/\s+/)
    .some(s=>s.endsWith('__placeholder') || /(?:^|-)placeholder$/.test(s));
  const closedGraph = e => {
    if (e.tagName !== 'INPUT' || attr(e,'role') !== 'combobox' || !visible(e) ||
        attr(e,'aria-haspopup') !== 'true' || attr(e,'aria-autocomplete') !== 'list') return null;
    const id = attr(e,'aria-labelledby'), L = byId(id), W = e.parentElement, V = W?.parentElement;
    const form = nativeForm(e);
    if (!id.endsWith('-label') || !L || !visible(L) || !form || !W || !V ||
        W.querySelectorAll(editable).length !== 1 || !W.contains(e)) return null;
    const branches = Array.from(V.children);
    if (branches.filter(n=>n===W || n.querySelectorAll(editable).length).length !== 1) return null;
    const display = branches.filter(n=>n!==W && visible(n) &&
      (singleBranch(n) || placeholderBranch(n)));
    if (display.length !== 1 || display[0].querySelectorAll(editable).length) return null;
    let F = V; while (F && !F.contains(L)) F = F.parentElement;
    if (!F || F===form || nativeForm(F)!==form || nativeForm(L)!==form ||
        F.querySelectorAll('[role="combobox"]').length !== 1 ||
        Array.from(F.querySelectorAll(editable)).some(n=>n!==e) ||
        ![e,L,W,V,F,form].every(uniqueNode)) return null;
    const labels = Array.from(document.querySelectorAll('[role="combobox"]'))
      .filter(n=>attr(n,'aria-labelledby')===id);
    if (labels.length !== 1) return null;
    const hidden = Array.from(F.querySelectorAll('input[type="hidden"]'));
    if (hidden.length > 1 || hidden.some(h=>nativeForm(h)!==form || !uniqueNode(h))) return null;
    const H = hidden[0];
    const explicit = attr(e,'data-value-input'), name = attr(e,'data-value-name');
    if ((explicit && (!H || byId(explicit)!==H)) || (name && (!H || H.name!==name))) return null;
    const expanded = attr(e,'aria-expanded');
    const token = singleBranch(display[0]);
    const noLinks = !attr(e,'aria-controls') && !attr(e,'aria-owns') && !attr(e,'aria-activedescendant');
    const phase = expanded==='true' ? 'C1' : expanded==='false' && noLinks ? (token ? 'C2' : 'C0') : '';
    if (!phase) return null;
    return {mode:'closed-react-select',phase,label_selector:selector(L),
      field_selector:selector(F),value_container_selector:selector(V),
      input_wrapper_selector:selector(W),hidden_selector:H ? selector(H) : '',
      hidden_value:H ? H.value : '',selected_value:H ? H.value : '',
      selected_text:token ? display[0].innerText : '',
      displayed_text:token ? display[0].innerText : ''};
  };
  const newGraphs = new Map(Array.from(document.querySelectorAll('[role="combobox"]'))
    .map(e=>[e,closedGraph(e)]));
  const popupClaims = e => {
    const lists = [];
    for (const key of ['aria-controls','aria-owns']) {
      const refs = attr(e,key).trim().split(/\s+/).filter(Boolean);
      if (refs.length > 1) return null;
      for (const ref of refs) {
        const list = byId(ref);
        if (!list || attr(list,'role')!=='listbox') return null;
        lists.push(list);
      }
    }
    const active = attr(e,'aria-activedescendant');
    if (active) {
      const option = byId(active), list = option?.closest('[role="listbox"]');
      if (!option || attr(option,'role')!=='option' || !list || !uniqueNode(list)) return null;
      lists.push(list);
    }
    return lists;
  };
  const closedPopup = e => {
    if (newGraphs.get(e)?.phase!=='C1') return null;
    const claims = popupClaims(e);
    if (!claims?.length || claims.some(n=>n!==claims[0])) return null;
    const list = claims[0], form = nativeForm(e);
    if (!visible(list) || (nativeForm(list) && nativeForm(list)!==form) ||
        list.parentElement?.closest('[role="listbox"]') || list.querySelectorAll('[role="listbox"]').length) return null;
    const options = Array.from(list.querySelectorAll('[role="option"]'));
    if (!options.length || options.some(o=>o.closest('[role="listbox"]')!==list || !uniqueNode(o))) return null;
    const others = Array.from(document.querySelectorAll('[role="combobox"]')).filter(c=>c!==e);
    if (others.some(c=> {
      const refs = ['aria-controls','aria-owns','aria-activedescendant']
        .flatMap(k=>attr(c,k).trim().split(/\s+/)).filter(Boolean);
      return refs.some(id=>id===list.id || options.some(o=>o.id===id));
    })) return null;
    return list;
  };
  const unsafeOption = o => {
    const dangerous = n => (n.tagName==='A' && !!n.href) ||
      ((n.tagName==='BUTTON' || n.tagName==='INPUT') && ['submit','image'].includes(n.type)) ||
      n.hasAttribute('formaction');
    for (let n=o;n;n=n.parentElement) if (dangerous(n)) return true;
    return Array.from(o.querySelectorAll('*')).some(dangerous);
  };
  const pickerParts = new Set();
  for (const wrapper of document.querySelectorAll('.iti')) {
    const form = nativeForm(wrapper), tels = Array.from(wrapper.querySelectorAll('input[type="tel"]'));
    if (!form || !uniqueNode(wrapper) || wrapper.parentElement?.closest('.iti') ||
        wrapper.querySelectorAll('.iti').length || tels.length!==1 || nativeForm(tels[0])!==form) continue;
    const one = (root, q) => { const a=Array.from(root.querySelectorAll(q)); return a.length===1 ? a[0] : null; };
    const container = one(wrapper,'.iti__country-container');
    const button = container && one(container,'.iti__selected-country');
    const dropdown = one(wrapper,'.iti__dropdown-content');
    const search = dropdown && one(dropdown,'.iti__search-input');
    const list = dropdown && one(dropdown,'.iti__country-list');
    if (!button || button.tagName!=='BUTTON' || button.type!=='button' ||
        button.hasAttribute('formaction') || button.closest('a[href]') ||
        !search || search.tagName!=='INPUT' || !['text','search'].includes(search.type) ||
        attr(search,'role')!=='combobox' || !list || attr(list,'role')!=='listbox' ||
        [container,dropdown].some(n=>n.tagName!=='DIV' || attr(n,'role')) ||
        container.parentElement!==wrapper || dropdown.parentElement!==wrapper ||
        button.parentElement!==container || search.parentElement!==dropdown || list.parentElement!==dropdown) continue;
    const options = Array.from(list.querySelectorAll('.iti__country'));
    const parts = [container,button,dropdown,search,list,...options];
    if (parts.some(n=>nativeForm(n)!==form || !uniqueNode(n)) ||
        options.some(n=>attr(n,'role')!=='option' || n.closest('[role="listbox"]')!==list || unsafeOption(n))) continue;
    parts.forEach(n=>pickerParts.add(n));
  }
"""

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
  /* WIDGET_GRAPH */
  const linkId = e => {
    const refs = (e.getAttribute('aria-controls') || e.getAttribute('aria-owns') || '')
      .trim().split(/\s+/).filter(Boolean);
    if (refs.length !== 1) return '';
    const owners = Array.from(document.querySelectorAll('[role="combobox"]'))
      .filter(c => {
        const candidate = (c.getAttribute('aria-controls') || c.getAttribute('aria-owns') || '')
          .trim().split(/\s+/).filter(Boolean);
        return candidate.length === 1 && candidate[0] === refs[0];
      });
    return owners.length === 1 ? refs[0] : '';
  };
  const linkedListbox = e => {
    const id = linkId(e);
    const matches = id ? document.querySelectorAll('#'+CSS.escape(id)) : [];
    if (!id || !matches.length) return null;
    return matches.length === 1 && matches[0].getAttribute('role') === 'listbox'
      ? matches[0] : null;
  };
  const currentListbox = e => newGraphs.get(e) ? closedPopup(e) : linkedListbox(e);
  const controls = nodes.filter(e => !pickerParts.has(e) && (e.getAttribute('role') !== 'option' ||
    !Array.from(document.querySelectorAll('[role="combobox"]'))
      .some(c=>currentListbox(c)?.contains(e))) &&
    (visible(e) || e.form || e.closest('form') || e.type === 'file')).map(e => {
    const group = e.closest(
      'fieldset,[role="radiogroup"],[data-automation-id="formField"],.form-group');
    const graph = newGraphs.get(e);
    const question = graph ? label(e) : clean(group?.querySelector(
      'legend,[data-automation-id="formLabel"]')?.innerText || group?.innerText || '');
    const type = e.getAttribute('role') ||
      ((e.tagName === 'INPUT' || e.tagName === 'BUTTON') ? e.type :
        (e.getAttribute('type') || e.tagName.toLowerCase()));
    const submit = e.tagName === 'BUTTON' ? e.type === 'submit' && !!e.form :
      type === 'submit' || type === 'image';
    const form = e.form || e.closest('form');
    // DOM properties resolve relative actions against document.baseURI.
    // Do not infer a receiver from the visible button label.
    const formAction = form?.action || '';
    const override = e.getAttribute('formaction') !== null ? (e.formAction || '') : '';
    return {selector:selector(e), label:label(e).slice(0,600),
      question:question.slice(0,1200), tag:e.tagName.toLowerCase(), type, submit,
      in_form:!!form, form_selector:form ? selector(form) : '',
       form_action:formAction, formaction:override, name:e.name || '',
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
        return {...(graph || {}),linked:!!list, link_id:graph ? (list?.id || '') : linkId(e),
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
      if (pickerParts.has(e)) return true;
      if (newGraphs.get(e)) {
        const list = closedPopup(e);
        if (newGraphs.get(e).phase==='C1' && !list) return false;
        if (list) { linkedParts.add(list); list.querySelectorAll('[role="option"]').forEach(o=>linkedParts.add(o)); }
        return true;
      }
      const list = linkedListbox(e);
      if (!linkId(e) || !visible(e) || (list && list.closest('form') && list.closest('form') !==
          (e.form || e.closest('form')))) return false;
      // Only a genuinely absent target is a provisional closed picker.
      if (!list) return document.getElementById(linkId(e)) === null;
      const options = Array.from(list.querySelectorAll('[role="option"]'));
      if (options.some(o => o.closest('[role="listbox"]') !== list)) return false;
      linkedParts.add(list); options.forEach(o=>linkedParts.add(o));
      return true;
    });
  const opaqueControls = Array.from(document.querySelectorAll('*')).some(e =>
    (visible(e) || e.closest('form') || e.getAttribute('aria-required') === 'true') &&
    e.tagName !== 'BROWSER-SKILL-OVERLAY' && !pickerParts.has(e) && (
      (e.matches('[role="combobox"]') && !linkedComboboxes.includes(e)) ||
      (e.matches('[role="listbox"],[role="option"]') && !linkedParts.has(e)) ||
      e.matches('[contenteditable]') || e.tagName.includes('-') ||
      (e.closest('form') && e.matches('canvas,object,embed,[aria-controls]') &&
        !e.matches('[role="combobox"]') && !linkedParts.has(e))));
  const inventoryVerified = document.readyState === 'complete' &&
    controls.length <= 500 && !opaqueControls && !siteShadow;
  return {url:location.href, title, text:text.slice(0,100000),
    description:description?.innerText || '', controls:controls.slice(0,500),
    truncated:controls.length > 500 || text.length > 100000,
    unsupported_frames:Array.from(document.querySelectorAll('iframe'))
      .filter(visible).map(e=>e.src),
    shadow_roots:siteShadow, opaque_controls:opaqueControls,
    inventory_verified:inventoryVerified};
})()""".replace('  /* WIDGET_GRAPH */', _WIDGET_GRAPH)
