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
  // Executed afresh in each reader. No page attributes or model flags retain admission.
  const nativeForm = e => e.form || e.closest('form');
  // Recognition must classify the same proxy owners in each independent reader.
  // Action evidence still uses the witness's stricter visible() on these exact nodes.
  const graphVisible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const graphNodes = new WeakMap();
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
  // Diagnostics never reuse action selectors: even escaped IDs can hold answers.
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
  const requiredProxies = new Set();
  const requiredProxy = (p,e,F,L,form) => {
    const names=Array.from(p.attributes || []).map(a=>a.name).sort();
    const shell=p.parentElement, style=getComputedStyle(p);
    return e.tagName==='INPUT' && attr(e,'aria-required')==='true' &&
      (names.join(',')==='aria-hidden,class,required,tabindex' ||
        (names.join(',')==='aria-hidden,class,required,tabindex,value' &&
         p.getAttribute('value')==='')) &&
      p.tagName==='INPUT' && p.hasAttribute('required') && attr(p,'tabindex')==='-1' &&
      attr(p,'aria-hidden')==='true' && p.value==='' &&
      !!e.form && p.form===e.form && e.form===form &&
      nativeForm(F)===form && nativeForm(L)===form &&
      shell?.matches('div.select-shell') && shell.parentElement===F && shell.contains(e) &&
      F.querySelectorAll('div.select-shell').length===1 &&
      style.opacity==='0' && style.position==='absolute' && style.pointerEvents==='none';
  };
  const countryGraph = (e, L, F, form, phase) => {
    // This receiverless authority belongs to the observed phone field only.
    const chain = ['input.select__input','div.select__input-container',
      'div.select__value-container','div.select__control','div','div.select-shell',
      'div.select__container','div.select','div.phone-input__country',
      'fieldset.phone-input','div','div.application--questions','form'];
    const path = []; let n=e;
    for (const shape of chain) {
      if (!n || !n.matches(shape) || nativeForm(n)!==form || !uniqueNode(n)) return null;
      path.push(n); n=n.parentElement;
    }
    const fieldset=path[9], branches=Array.from(fieldset.children);
    if (e.id!=='country' || byId('country')!==e || form.id!=='application-form' || path[12]!==form ||
        document.querySelectorAll('fieldset.phone-input').length!==1 ||
        branches.length!==3 || branches.filter(x=>x.tagName==='LEGEND').length!==1 ||
        branches.filter(x=>x.matches('div.phone-input__country')).length!==1 ||
        branches.filter(x=>x.matches('div.phone-input__phone')).length!==1 ||
        !path[8].contains(F) || L.tagName!=='LABEL' || L.id!=='country-label' ||
        attr(L,'for')!==e.id || document.querySelectorAll('label[for="country"]').length!==1 ||
        fieldset.querySelectorAll('input[type="hidden"]').length ||
        e.type!=='text' || attr(e,'autocomplete')!=='off' || attr(e,'tabindex')!=='0' ||
        attr(e,'aria-required')!=='true' || !['true','false'].includes(attr(e,'aria-invalid')) ||
        !e.hasAttribute('aria-activedescendant') ||
        !attr(e,'aria-describedby') || !attr(e,'aria-errormessage')) return null;
    // Mutable error/placeholder references are current safety evidence, not identity.
    // At C2 react-select can replace the placeholder with an ID-less single-value
    // branch. closedGraph already proved that unique, visible C2 branch; only
    // its exact now-absent placeholder IDREF may lag that DOM replacement.
    for (const k of ['aria-describedby','aria-errormessage']) {
      let resolved=0, replacedPlaceholder=false;
      for (const id of attr(e,k).trim().split(/\s+/)) {
        if (!id) return null;
        const hits=document.querySelectorAll('#'+CSS.escape(id));
        // Validation mounts this exact error lazily; duplicates are never absence.
        if (!hits.length && id===e.id+'-error') continue;
        if (!hits.length && k==='aria-describedby' && phase==='C2' &&
            id==='react-select-country-placeholder') {
          replacedPlaceholder=true;
          continue;
        }
        const ref=hits.length===1 ? hits[0] : null;
        if (!ref || nativeForm(ref)!==form || !path[8].contains(ref)) return null;
        resolved++;
      }
      if (k==='aria-describedby' && !resolved && !replacedPlaceholder) return null;
    }
    if (phase!=='C1' && (e.hasAttribute('aria-controls') || e.hasAttribute('aria-owns') ||
        attr(e,'aria-activedescendant') || e.value)) return null;
    return path.map(selector);
  };
  const closedGraph = e => {
    if (e.tagName !== 'INPUT' || attr(e,'role') !== 'combobox' || !graphVisible(e) ||
        attr(e,'aria-haspopup') !== 'true' || attr(e,'aria-autocomplete') !== 'list') return null;
    const id = attr(e,'aria-labelledby'), L = byId(id), W = e.parentElement, V = W?.parentElement;
    const form = nativeForm(e);
    if (!id.endsWith('-label') || !L || !graphVisible(L) || !form || !W || !V ||
        W.querySelectorAll(editable).length !== 1 || !W.contains(e)) return null;
    const branches = Array.from(V.children);
    if (branches.filter(n=>n===W || n.querySelectorAll(editable).length).length !== 1) return null;
    const display = branches.filter(n=>n!==W && graphVisible(n) &&
      (singleBranch(n) || placeholderBranch(n)));
    if (display.length !== 1 || display[0].querySelectorAll(editable).length) return null;
    let F = V; while (F && !F.contains(L)) F = F.parentElement;
    if (!F || F===form || nativeForm(F)!==form || nativeForm(L)!==form ||
        F.querySelectorAll('[role="combobox"]').length !== 1 ||
        ![e,L,W,V,F,form].every(uniqueNode)) return null;
    const extras=Array.from(F.querySelectorAll(editable)).filter(n=>n!==e);
    if (extras.length>1 || (extras.length===1 && !requiredProxy(extras[0],e,F,L,form))) return null;
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
    const country = !H && e.id==='country' ? countryGraph(e,L,F,form,phase) : null;
    // Only the exact phone graph can admit receiverless #country. An unrelated
    // #country with a proven receiver keeps ordinary closed-select authority.
    if (e.id==='country' && !country && (!H || e.closest('fieldset.phone-input'))) return null;
    if (extras.length) requiredProxies.add(extras[0]);
    graphNodes.set(e, {label:L, display:display[0]});
    return {mode:'closed-react-select',phase,required_proxy_mounted:!!extras.length,label_selector:selector(L),
      field_selector:selector(F),value_container_selector:selector(V),
      input_wrapper_selector:selector(W),hidden_selector:country ? undefined : H ? selector(H) : '',
      hidden_value:country ? undefined : H ? H.value : '',
      selected_value:country ? undefined : H ? H.value : '',
      ...(country ? {receiverless_country:true,country_graph:country} : {}),
      selected_text:token ? display[0].innerText : '',
      displayed_text:token ? display[0].innerText : ''};
  };
  const newGraphs = new Map(Array.from(document.querySelectorAll('[role="combobox"]'))
    .map(e=>[e,closedGraph(e)]));
  // Rejected react-select owners must never downgrade to legacy linked authority.
  const rejectedCandidate = e => {
    if (e.tagName!=='INPUT' || attr(e,'role')!=='combobox' || newGraphs.get(e)) return false;
    const W=e.parentElement, V=W?.parentElement;
    if (e.form && W?.contains(e) && V && Array.from(V.children).some(n=>n!==W &&
        graphVisible(n) && (singleBranch(n) || placeholderBranch(n)))) return true;
    const L=byId(attr(e,'aria-labelledby'));
    let F=e.parentElement; while (F && F.tagName!=='FORM' && !F.contains(L)) F=F.parentElement;
    return !!L && !!F && F.tagName!=='FORM' && Array.from(F.querySelectorAll(editable)).some(n=> {
      if (n===e || n.tagName!=='INPUT' || requiredProxies.has(n)) return false;
      const s=getComputedStyle(n);
      return attr(n,'aria-hidden')==='true' || (s.opacity==='0' && s.pointerEvents==='none');
    });
  };
  const quarantined = new Set(Array.from(newGraphs.keys()).filter(rejectedCandidate));
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
    if (newGraphs.get(e).receiverless_country && nativeForm(list)!==form) return null;
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
  // Failed ITI ownership must not fall through to legacy popup omission.
  const pickerCandidate = e => !!e.closest('.iti,.iti__dropdown-content') ||
    e.matches('.iti__search-input,.iti__country-list');
  for (const wrapper of document.querySelectorAll('.iti')) {
    const form = nativeForm(wrapper), tels = Array.from(wrapper.querySelectorAll('input[type="tel"]'));
    if (!form || !uniqueNode(wrapper) || !visible(wrapper) || wrapper.parentElement?.closest('.iti') ||
        wrapper.querySelectorAll('.iti').length || tels.length!==1 || !visible(tels[0]) ||
        tels[0].parentElement!==wrapper || nativeForm(tels[0])!==form) continue;
    const one = (root,q) => { const a=Array.from(root.querySelectorAll(q)); return a.length===1 ? a[0] : null; };
    // A locally valid picker elsewhere is not the witnessed phone-fieldset decoration.
    const inputWrapper=wrapper.parentElement, textWrapper=inputWrapper?.parentElement;
    const phoneBranch=textWrapper?.parentElement, fieldset=phoneBranch?.parentElement;
    if (document.querySelectorAll('.iti').length!==1 ||
        document.querySelectorAll('input[type="tel"]').length!==1 ||
        tels[0].id!=='phone' || byId('phone')!==tels[0] ||
        !inputWrapper?.matches('div.input-wrapper') ||
        !textWrapper?.matches('div.text-input-wrapper') ||
        !phoneBranch?.matches('div.phone-input__phone') ||
        !fieldset?.matches('fieldset.phone-input') || nativeForm(fieldset)!==form ||
        form.id!=='application-form' || byId('application-form')!==form ||
        one(form,'fieldset.phone-input')!==fieldset ||
        one(fieldset,'.phone-input__phone')!==phoneBranch) continue;
    const container=one(wrapper,'.iti__country-container');
    const button=container && one(container,'.iti__selected-country');
    const dialog=container && one(container,'.iti__dropdown-content');
    const searchWrapper=dialog && one(dialog,'.iti__search-input-wrapper');
    const search=searchWrapper && one(searchWrapper,'.iti__search-input');
    const clear=searchWrapper && one(searchWrapper,'.iti__search-clear');
    const list=dialog && one(dialog,'.iti__country-list');
    if (!container || !button || !dialog || !searchWrapper || !search || !clear || !list ||
        container.tagName!=='DIV' || attr(container,'role') || container.parentElement!==wrapper ||
        container.children.length!==2 || button.parentElement!==container || dialog.parentElement!==container ||
        button.tagName!=='BUTTON' || button.type!=='button' || attr(button,'aria-haspopup')!=='dialog' ||
        attr(button,'aria-expanded')!=='false' || !dialog.id || byId(attr(button,'aria-controls'))!==dialog ||
        dialog.tagName!=='DIV' || attr(dialog,'role')!=='dialog' || attr(dialog,'aria-modal')!=='true' ||
        searchWrapper.tagName!=='DIV' || searchWrapper.parentElement!==dialog ||
        search.tagName!=='INPUT' || search.type!=='search' || attr(search,'role')!=='combobox' ||
        search.parentElement!==searchWrapper || attr(search,'aria-expanded')!=='true' ||
        attr(search,'aria-autocomplete')!=='list' || attr(search,'autocomplete')!=='off' ||
        !list.id || byId(attr(search,'aria-controls'))!==list ||
        clear.tagName!=='BUTTON' || clear.type!=='button' || clear.parentElement!==searchWrapper ||
        list.tagName!=='UL' || attr(list,'role')!=='listbox' || list.parentElement!==dialog) continue;
    const options=Array.from(list.querySelectorAll('[role="option"]'));
    const parts=[container,...container.querySelectorAll('*')];
    const structural=new Set([container,button,dialog,searchWrapper,search,clear,list,...options]);
    const auxiliary=n => {
      const tag=n.tagName.toUpperCase(), newTag=['LINE','MASK','RECT'].includes(tag);
      if (!['DIV','SPAN','SVG','PATH','G','CIRCLE','LINE','MASK','RECT'].includes(tag) ||
          attr(n,'role') || n.matches('input,button,a,select,textarea,[contenteditable]')) return false;
      const svg=n.closest('svg'), icon=n.closest('.iti__search-icon');
      const iconPlacement=!!icon && icon.parentElement===searchWrapper;
      const clearPlacement=!!svg && svg.parentElement===clear;
      // Neither pre-existing containment shortcut grants these new SVG tags.
      if (newTag && (!svg || (!iconPlacement && !clearPlacement))) return false;
      if (button.contains(n) || options.some(o=>o.contains(n))) return true;
      if (n.parentElement===dialog) return n.matches('span.iti__a11y-text,div.iti__no-results');
      return iconPlacement || clearPlacement;
    };
    const claimants=Array.from(document.querySelectorAll('[aria-controls],[aria-owns],[aria-activedescendant]'));
    if (options.length!==244 || list.children.length!==244 ||
        options.some(n=>n.tagName!=='LI' || !n.matches('.iti__country') || n.parentElement!==list || !n.id) ||
        parts.some(n=>nativeForm(n)!==form || !uniqueNode(n) || (n.id && byId(n.id)!==n) || n.getClientRects().length ||
          unsafeOption(n) || (!structural.has(n) && !auxiliary(n))) ||
        wrapper.querySelectorAll(editable).length!==2 ||
        claimants.some(n=>['aria-controls','aria-owns','aria-activedescendant'].some(k=>
          attr(n,k).split(/\s+/).filter(Boolean).some(id=>parts.some(p=>p.id===id) &&
            !((n===button && k==='aria-controls' && id===dialog.id) ||
              (n===search && k==='aria-controls' && id===list.id)))))) continue;
    parts.forEach(n=>pickerParts.add(n));
  }
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
  const currentListbox = e => {
    if (pickerCandidate(e) || quarantined.has(e)) return null;
    const list = newGraphs.get(e) ? closedPopup(e) : linkedListbox(e);
    return list && !pickerCandidate(list) ? list : null;
  };
  const leverQuestion = e => {
    const cardSelector = 'li.application-question.custom-question';
    if (e.tagName !== 'INPUT' || !['radio','checkbox'].includes(e.type)) return null;
    const card = e.closest(cardSelector);
    if (!card) return null;
    // A question is approval identity: prove the whole nearest card, never a subset.
    const owner = e.form;
    const labels = Array.from(card.querySelectorAll('div.application-label'));
    const fields = Array.from(card.querySelectorAll('div.application-field'));
    const texts = labels.length === 1 ? Array.from(labels[0].querySelectorAll('div.text')) : [];
    const members = Array.from(card.querySelectorAll(
      'input,textarea,select,button,[role="button"],[role="combobox"],'+
      '[role="radio"],[role="checkbox"],[contenteditable="true"]'));
    if (!owner || !owner.contains(card) || !owner.contains(e) ||
        labels.length !== 1 || fields.length !== 1 || texts.length !== 1 ||
        labels[0].parentElement?.tagName !== 'DIV' ||
        labels[0].parentElement !== fields[0].parentElement ||
        labels[0].parentElement.parentElement !== card ||
        texts[0].parentElement !== labels[0] || !visible(texts[0]) ||
        !clean(texts[0].innerText) || !members.length) return '';
    // Concealed text retains innerText; unknown clipping geometry is not proof.
    const shownOnPath = n => {
      for (let p = n; p; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (Number(s.opacity) === 0 || s.visibility !== 'visible' || s.display === 'none' ||
            (s.clip && s.clip !== 'auto') || (s.clipPath && s.clipPath !== 'none')) return false;
        if (p === card) return true;
      }
      return false;
    };
    if (!shownOnPath(texts[0]) || !Array.from(texts[0].querySelectorAll('*'))
        .every(n => n.getClientRects().length > 0 && shownOnPath(n))) return '';
    if (e.type === 'checkbox' ? members.length !== 1 : !clean(e.name)) return '';
    if (!members.every(m => {
      const caption = m.closest('label');
      const item = caption?.parentElement;
      const list = item?.parentElement;
      return m.tagName === 'INPUT' && m.type === e.type &&
        (e.type !== 'radio' || m.name === e.name) &&
        m.closest(cardSelector) === card && m.form === owner && owner.contains(m) &&
        caption?.tagName === 'LABEL' && item?.tagName === 'LI' &&
        list?.tagName === 'UL' && list.hasAttribute('data-qa') &&
        list.parentElement === fields[0];
    })) return '';
    return clean(texts[0].innerText);
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
    const lever = group === null ? leverQuestion(e) : null;
    if (lever !== null) question = lever;
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
    return {selector:selector(e), diagnostic_locator:diagnosticLocator(e), label:label(e).slice(0,600),
      question:question.slice(0,1200), tag:e.tagName.toLowerCase(), type, submit,
      ...(lever === '' ? {lever_card_unresolved:true} : {}),
      in_form:!!form, form_selector:form ? selector(form) : '',
      native_form_owner_selector:e.form ? selector(e.form) : '',
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
          listbox_selector:list ? selector(list) : '', listbox_visible:!!list && visible(list), options,
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
      if (e.id==='country' && e.closest('fieldset.phone-input')) return false;
      const list = currentListbox(e);
      if (!linkId(e) || !visible(e) || (list && list.closest('form') && list.closest('form') !==
          (e.form || e.closest('form')))) return false;
      // Only a genuinely absent target is a provisional closed picker.
      if (!list) return document.getElementById(linkId(e)) === null;
      const options = Array.from(list.querySelectorAll('[role="option"]'));
      if (options.some(o => o.closest('[role="listbox"]') !== list)) return false;
      linkedParts.add(list); options.forEach(o=>linkedParts.add(o));
      return true;
    });
  const opaqueNodes = Array.from(document.querySelectorAll('*')).filter(e =>
    (visible(e) || e.closest('form') || e.getAttribute('aria-required') === 'true') &&
    e.tagName !== 'BROWSER-SKILL-OVERLAY' && !pickerParts.has(e) && (
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
    inventory_verified:inventoryVerified};
})()""".replace('  /* WIDGET_GRAPH */', _WIDGET_GRAPH)


# Native application-root proof and the visible badge predicate have one owner.
_APPLICATION_ROOT = r"""
  const entryLabel = e => clean(Array.from(e.labels || []).map(x=>x.innerText).join(' ') ||
    (e.getAttribute('aria-labelledby') || '').split(' ')
      .map(id=>document.getElementById(id)?.innerText || '').join(' ') ||
    e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || e.name);
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
  const ordered=!!wrapper && wrapper.tagName==='DIV' && !wrapper.getAttribute('class') &&
    exactSubmit?.parentElement===wrapper && wrapper.children.length===3 &&
    wrapper.children[0].matches('div.ashby-application-form-autofill-pane') &&
    wrapper.children[1]===container && wrapper.children[2]===exactSubmit &&
    panes.length===1 && panes[0]===wrapper.children[0];
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
  const reservedButtons = Array.from(document.querySelectorAll('button#hcaptchaSubmitBtn'));
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
    let origin='', path='';
    try { if (raw!==null) { const u=new URL(raw); origin=u.origin; path=u.pathname; } } catch (_) {}
    const style=getComputedStyle(e), hidden=style.visibility==='hidden' || style.display==='none';
    const shown=style.visibility==='visible' && style.display!=='none';
    const outside=!!root && !root.contains(e), box=rectOf(e);
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
    else if ((r && outside && hidden) || visibleR==='R') status='R';
    else if (widget) { status='W'; widget.forEach(n=>excluded.add(n)); }
    else if (x) status='X';
    else if (z) status='Z';
    return {selector:selector(e),origin,path,source:raw===null ? 'absent' : raw==='about:blank' ? 'blank' : 'url',
      visibility:style.visibility,display:style.display,outside,root:root ? selector(root) : '',
      ancestors:(() => {const a=[]; for(let p=e.parentElement;p;p=p.parentElement) a.push(selector(p)); return a;})(),
      box,badge,bounds,status};
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
    root:nativeRoot ? {selector:selector(nativeRoot),action:nativeRoot.action} : null,
    formless,root_evidence:rootEvidence,upload_tree:structuralRoot ? uploadTree : null,
    helper_challenge:reservedButtons.some(e=>!e.matches('button#hcaptchaSubmitBtn.hidden') ||
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
    script = script.replace('const closedGraph = e => {',
                            'const closedGraph = e => { if (excluded.has(e)) return null;')
    script = script.replace('const H = hidden[0];',
                            'const H = hidden[0]; if (responseNodes.includes(H)) '
                            '{ entry.response_error=true; return null; }')
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
