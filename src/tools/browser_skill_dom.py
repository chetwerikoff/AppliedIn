"""Read-only DOM inventory. Models never supply JavaScript to the browser."""
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
  const controls = nodes.filter(e => visible(e) || e.form || e.closest('form') || e.type === 'file').map(e => {
    const group = e.closest(
      'fieldset,[role="radiogroup"],[data-automation-id="formField"],.form-group');
    const question = clean(group?.querySelector(
      'legend,[data-automation-id="formLabel"]')?.innerText || group?.innerText || '');
    const type = e.getAttribute('role') || e.getAttribute('type') ||
      (e.tagName === 'INPUT' ? e.type : e.tagName.toLowerCase());
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
  const opaqueControls = Array.from(document.querySelectorAll('*')).some(e =>
    (visible(e) || e.closest('form') || e.getAttribute('aria-required') === 'true') &&
    e.tagName !== 'BROWSER-SKILL-OVERLAY' && (
      e.matches('[contenteditable], [role="combobox"], [role="listbox"], [role="option"]') ||
      e.tagName.includes('-') ||
      (e.closest('form') && e.matches('canvas,object,embed,[aria-controls]'))));
  const inventoryVerified = document.readyState === 'complete' &&
    controls.length <= 500 && !opaqueControls && !siteShadow;
  return {url:location.href, title, text:text.slice(0,100000),
    description:description?.innerText || '', controls:controls.slice(0,500),
    truncated:controls.length > 500 || text.length > 100000,
    unsupported_frames:Array.from(document.querySelectorAll('iframe'))
      .filter(visible).map(e=>e.src),
    shadow_roots:siteShadow, opaque_controls:opaqueControls,
    inventory_verified:inventoryVerified};
})()"""
