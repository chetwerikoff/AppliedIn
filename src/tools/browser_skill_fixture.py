"""Daemon-exclusive synthetic application form and read-only safety proof.

No alternative browser driver or general fixture framework. The caller must be
the exact daemon -m entry before production configuration imports. No committing
BSK IPC is permitted; only the fixed form GET is served and every form method
is refused with 405.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextvars import ContextVar
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

URL = "http://127.0.0.1:18789/fixture/example-co/job/1"
_PATH = "/fixture/example-co/job/1"
PK = "example-co#synthetic-job"
_RESUME_BYTES = (b"%PDF-1.4" + bytes([10])
                 + b"% synthetic fixture; never submitted" + bytes([10])
                 + b"%%EOF" + bytes([10]))
COMPANY = "example-co"
_COMBO_SELECTORS = {
    'Where are you located?': '#location',
    'School': '#school', 'Degree': '#degree', 'Discipline': '#discipline',
}
_CHOICE_SELECTORS = {**_COMBO_SELECTORS, 'Country': '#country'}
_FACTS = {
    'Name': 'Test User', 'Additional question': 'Synthetic answer',
    'Where are you located?': 'Synthetic Example City',
    'School': 'Synthetic School', 'Degree': 'Synthetic Degree',
    'Discipline': 'Synthetic Discipline', 'Phone': '+12025550123',
    'Country': 'Synthetic Country',
}
_MACHINE_VALUES = {key: _FACTS[key] for key in _COMBO_SELECTORS}
_OPTION_SELECTORS = {selector+'-option': key for key, selector in _CHOICE_SELECTORS.items()}
_HIDDEN_SELECTORS = {key: selector+'-code' for key, selector in _COMBO_SELECTORS.items()}


def _closed_form() -> bytes:
    # Component-rendered display branches replace placeholders; no invented
    # aria-valuetext/data-selected-* attributes carry commitment evidence.
    from html import escape
    fields = []
    for question, selector in _COMBO_SELECTORS.items():
        name = selector[1:]
        fields.append(f'''<div id="{name}-field" class="form-group">
<label id="{name}-label" for="{name}" data-automation-id="formLabel">{escape(question)}</label>
<div class="select-shell"><div id="{name}-value"><div id="{name}-input">
<input id="{name}" name="{name}" role="combobox" aria-expanded="false"
 aria-haspopup="true" aria-autocomplete="list" aria-labelledby="{name}-label" aria-required="true">
</div><div class="react-select__placeholder">Choose</div></div>
{('<input required tabindex="-1" aria-hidden="true" class="validation-only" value="">' if name == 'location' else '')}</div>
<input type="hidden" id="{name}-code" name="{name}_value"></div>''')
    fillers = ''.join(f'<input id="filler-{i}" name="filler_{i}" aria-label="Optional synthetic {i}">'
                      for i in range(24))
    picker_options = ''.join(
        f'<li id="iti-0__item-synthetic-{i}" class="iti__country" role="option">Synthetic {i}</li>'
        for i in range(244))
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Synthetic application</title>
<style>[role="listbox"][hidden],[role="option"][hidden]{display:none}
.validation-only{opacity:0;position:absolute;pointer-events:none}</style>
</head><body><h1>Synthetic role at example-co</h1>
<p>Offline fixture for synthetic example-co facts only. No real person or employer.
This form is served only by the daemon's --synthetic-browser-fixture mode.
It has no external links, cross-origin resources, redirects or real ATS connection.</p>
<form id="application-form" class="application--form" data-synthetic-tenant="example-co.test"
 method="POST" enctype="multipart/form-data"
 action="http://127.0.0.1:18789/fixture/example-co/job/1">
<div class="form-group"><label for="name" data-automation-id="formLabel">Name</label>
<input id="name" name="name"></div>
<label for="resume">Résumé</label><input id="resume" name="resume" type="file" required>
''' + ''.join(fields) + '''
<div class="application--questions"><div><fieldset class="phone-input"><legend>Phone</legend>
<div class="phone-input__country"><div class="select"><div class="select__container">
<label id="country-label" for="country">Country</label>
<div class="select-shell"><div id="country-owner">
<div class="select__control"><div id="country-value" class="select__value-container">
<div class="select__input-container"><input id="country" type="text" class="select__input"
 role="combobox" autocomplete="off" tabindex="0" aria-autocomplete="list" aria-expanded="false"
 aria-haspopup="true" aria-required="true" aria-invalid="false" aria-labelledby="country-label"
 aria-describedby="react-select-country-placeholder country-error" aria-errormessage="country-error"
 aria-activedescendant=""></div><div id="react-select-country-placeholder" class="select__placeholder">Choose</div>
</div></div></div>
<input required tabindex="-1" aria-hidden="true" class="validation-only" value=""></div></div></div></div>
<div class="phone-input__phone"><div class="text-input-wrapper"><div class="input-wrapper">
<div id="telephone" class="iti iti--allow-dropdown iti--show-flags iti--inline-dropdown">
<div class="iti__country-container" hidden>
<button id="country-button" type="button" class="iti__selected-country" aria-haspopup="dialog"
 aria-expanded="false" aria-controls="iti-0__dropdown-content" aria-label="Select country">
<div class="iti__selected-country-primary"><div class="iti__flag iti__globe"></div>
<div class="iti__arrow" aria-hidden="true"></div></div></button>
<div id="iti-0__dropdown-content" class="iti__dropdown-content iti__hide" role="dialog" aria-modal="true" hidden>
<div class="iti__search-input-wrapper"><span class="iti__search-icon" aria-hidden="true"><svg></svg></span>
<input id="iti-0__search-input" class="iti__search-input" type="search" role="combobox"
 aria-expanded="true" aria-controls="iti-0__country-listbox" aria-autocomplete="list" autocomplete="off">
<button id="country-clear" class="iti__search-clear iti__hide" type="button" tabindex="-1" hidden>Clear search</button></div>
<span class="iti__a11y-text"></span><div class="iti__no-results iti__hide" aria-hidden="true"></div>
<ul id="iti-0__country-listbox" class="iti__country-list" role="listbox">''' + picker_options + '''</ul></div></div>
<input id="phone" class="input input__single-line iti__tel-input" type="tel" aria-label="Phone"
 aria-required="true" aria-invalid="false" autocomplete="off" data-intl-tel-input-id="0"
 aria-describedby="phone-description phone-error phone-help" aria-errormessage="phone-error">
</div></div></div><span id="phone-description"></span><span id="phone-error"></span><span id="phone-help"></span>
</div></fieldset></div></div>
<div class="form-group"><label for="additional" data-automation-id="formLabel">Additional question</label>
<textarea id="additional" name="additional"></textarea></div>
''' + fillers + '''<button id="submit" type="submit">Submit application</button></form>
<script>
const answers = ''' + json.dumps({s[1:]: _FACTS[k] for k, s in _CHOICE_SELECTORS.items()}) + ''';
for (const [id,answer] of Object.entries(answers)) {
 const input=document.getElementById(id);
 input.addEventListener('click',()=>{
  if(input.getAttribute('aria-expanded')==='true')return;
  const list=document.createElement('div');list.id=id+'-options';list.setAttribute('role','listbox');
  const option=document.createElement('div');option.id=id+'-option';
  option.setAttribute('role','option');if(id!=='country')option.setAttribute('data-value',answer);option.textContent=answer;
  list.append(option);(id==='country'?document.getElementById('country-owner'):document.body).append(list);
  input.setAttribute('aria-expanded','true');input.setAttribute('aria-controls',list.id);
  option.addEventListener('click',()=>{
   const display=document.getElementById(id+'-value').lastElementChild;
   display.className='react-select__single-value';display.textContent=option.innerText;
   if(id!=='country')document.getElementById(id+'-code').value=option.getAttribute('data-value');
   else {input.setAttribute('aria-describedby','react-select-country-placeholder country-error');input.setAttribute('aria-invalid','false');}
   input.closest('.select-shell').querySelector('.validation-only')?.remove();
   input.value='';input.setAttribute('aria-expanded','false');input.removeAttribute('aria-controls');list.remove();
  });
 });
}
</script></body></html>''').encode('utf-8')


def _ashby_form() -> bytes:
    # One sealed page; real root admission must reach a direct human question.
    return b'''<!doctype html><html><head><meta charset="utf-8">
<title>Synthetic application</title><style>
label {display:block} .clipped {position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)}
</style></head><body><h1>Synthetic example-co.test application</h1><div>
<div class="ashby-application-form-autofill-pane">
<input type="file" class="clipped"><button type="button">Autofill</button></div>
<div class="ashby-application-form-container">
<div class="ashby-application-form-field-entry">
<label class="ashby-application-form-question-title" for="_systemfield_name">Application answer</label>
<div><input id="_systemfield_name" type="text" required></div></div>
<div class="ashby-application-form-field-entry">
<label class="ashby-application-form-question-title" for="_systemfield_email">Email</label>
<div><input id="_systemfield_email" type="email" required></div></div>
<div class="ashby-application-form-field-entry">
<label class="ashby-application-form-question-title" for="_systemfield_resume">Resume</label>
<div class="ashby-application-form-input-file">
<input id="_systemfield_resume" type="file" class="clipped" required>
<button type="button" class="ashby-application-form-input-file-dropzone-upload">Upload</button></div></div>
<div class="ashby-application-form-field-entry">
<label class="ashby-application-form-question-title" for="additional">Additional information</label>
<textarea id="additional" class="ashby-application-form-input-textarea"></textarea></div>
</div><button class="ashby-application-form-submit-button">Submit application</button>
</div>
<iframe src="https://www.recaptcha.net/recaptcha/enterprise/anchor" style="display:none"></iframe>
<iframe src="https://www.recaptcha.net/recaptcha/enterprise/bframe" style="display:none"></iframe>
<div><iframe style="display:none"></iframe></div></body></html>'''


def _entry_form() -> bytes:
    # Same sealed page: root appears first; passive frames arrive while inert.
    text = _closed_form().decode('utf-8')
    text = text.replace('<form id="application-form"',
                        '<template id="entry-template"><form inert id="application-form"')
    text = text.replace('</form>\n<script>', '</form></template>\n<script>\nsetTimeout(() => {\n'
                        'document.body.append(document.getElementById("entry-template").content);')
    injection = r'''
 setTimeout(() => {
  const form=document.getElementById('application-form');
  const h=document.createElement('div'); h.id='h-captcha'; h.className='h-captcha'; form.append(h);
  const add=(parent,src,style) => {
   const e=document.createElement('iframe'); if(src!==null)e.setAttribute('src',src);
   Object.assign(e.style,style); parent.append(e); return e;
  };
  for(let i=0;i<2;i++)add(h,'https://newassets.hcaptcha.com/captcha/v1/synthetic/static/hcaptcha-enclave.html',
    {visibility:'hidden',width:'100vw',height:'100vh'});
  for(const path of ['anchor','bframe'])add(document.body,'https://www.recaptcha.net/recaptcha/enterprise/'+path,
    {visibility:'hidden'});
  const row=document.createElement('li'); row.className='application-question awli-application-row';
  row.innerHTML='<div class="application-field"><div class="awli-button-container"><span class="IN-widget"></span>' +
    '<button id="w-button" class="awli-button" type="button"></button>' +
    '<a id="w-link" class="learn-more-link" href="https://widgets.example-co.test">Learn more</a></div></div>';
  form.append(row); const w=add(row.querySelector('span'),null,{width:'236px',height:'48px'});
  w.title='LinkedIn Embedded Content';
  // X bounds are outer client rects: the UA iframe border otherwise adds 4px.
  add(document.body,null,{width:'1px',height:'1px',visibility:'hidden',
    border:'0',padding:'0',display:'block'});
  for(const name of ['h-captcha-response','g-recaptcha-response']) {
   const e=document.createElement('textarea');e.name=name;e.hidden=true;form.append(e);
  }
  const helper=document.createElement('button');helper.id='hcaptchaSubmitBtn';helper.className='hidden';
  helper.hidden=true;form.append(helper);
  form.removeAttribute('inert');
 }, 1000);
}, 1000);
'''
    return text.replace('</script></body>', injection + '</script></body>').encode('utf-8')


_FORM = _entry_form()


def _greenhouse_form() -> bytes:
    # CSP blocks provider loads; only the top-document passive signature is read.
    badge = ('<div><div class="grecaptcha-badge"><iframe '
             'src="https://www.recaptcha.net/recaptcha/enterprise/anchor" '
             'style="width:256px;height:60px;border:0;padding:0;visibility:visible;display:block">'
             '</iframe><textarea class="g-recaptcha-response" id="g-recaptcha-response-123" '
             'name="g-recaptcha-response" style="display:none"></textarea></div></div>')
    text = _closed_form().decode('utf-8')
    text = text.replace('<svg></svg>', '<svg><circle></circle><line></line></svg>')
    text = text.replace('hidden>Clear search</button>',
                        'hidden><svg><line></line><mask id="synthetic-clear-mask">'
                        '<rect></rect><path></path></mask><circle></circle></svg></button>')
    # The native submit keeps a unique root for the passive badge, but NO
    # applicant field can qualify before the template mounts, at any cadence.
    start = '<div class="form-group"><label for="name"'
    text = text.replace(start, '<template id="greenhouse-template">' + start, 1)
    text = text.replace('<button id="submit"', '</template><button id="submit"', 1)
    text = text.replace('for (const [id,answer]',
                        'setTimeout(() => {\n'
                        'const template=document.getElementById("greenhouse-template");\n'
                        'document.getElementById("application-form").insertBefore('
                        'template.content,document.getElementById("submit"));\n'
                        'for (const [id,answer]')
    text = text.replace('</script>', '}, 3000);\n</script>')
    return text.replace('</form>', '</form>' + badge).encode('utf-8')


_COMMITTED_VALIDITY = r"""(() => {
  const selectors=['#location','#school','#degree','#discipline','#country'];
  return Object.fromEntries(selectors.map(s => {
    const e=document.querySelector(s);
    return [s, !!e && !e.required && e.value==='' && e.checkValidity() &&
      !e.closest('.select-shell').querySelector('.validation-only')];
  }));
})()"""


def _inventory_diagnostic_script() -> str:
    # Instrument the fixed observer only for reporting. Its original result is
    # still the sole inventory passed to the controller and all its guards.
    from tools.browser_skill_dom import PAGE
    script = PAGE
    return script.replace(
        "return {url:location.href, title, text:text.slice(0,100000),",
        "return {ready_state:document.readyState, "
        "offending_elements:opaqueNodes.slice(0,3).map(e=>({selector:diagnosticLocator(e),"
        "tag:e.tagName.toLowerCase(),role:e.getAttribute('role')})), "
        "shadow_hosts:shadowRoots.map(e=>({selector:selector(e),"
        "tag:e.tagName.toLowerCase()})), "
        "frames:Array.from(document.querySelectorAll('iframe')).map(e=>{ "
        "const s=getComputedStyle(e), path=[]; "
        "for(let p=e.parentElement;p;p=p.parentElement)path.unshift(selector(p)); "
        "return {selector:selector(e),tag:e.tagName.toLowerCase(),parent_path:path, "
        "rects:Array.from(e.getClientRects(),r=>({x:r.x,y:r.y,width:r.width,height:r.height})), "
        "display:s.display,visibility:s.visibility,width:s.width,height:s.height,box_sizing:s.boxSizing, "
        "borders:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth], "
        "padding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft]};}), "
        "url:location.href, title, text:text.slice(0,100000),")


# This context carries ONLY a one-shot object minted in run(). It is not a flag,
# user input, serialized token, request parameter or globally installed patch.
_SESSION_ENTRY: ContextVar[object | None] = ContextVar(
    "appliedin_private_native_fixture_entry", default=None)


def _daemon_origin() -> bool:
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return (getattr(spec, "name", None) == "daemon"
            and sys.argv[1:] == ["--synthetic-browser-fixture"]
            and os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
            and os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
            and os.environ.get("BSK_AUTO_START") == "0")


def _read_only_entry() -> bool:
    state = _SESSION_ENTRY.get()
    return type(state) is _SyntheticRun and state._origin is _ORIGIN and _daemon_origin()


def authorized(context: object, *, pk: str = "", company: str = "",
               url: str = "") -> bool:
    return (context is not None and context is _SESSION_ENTRY.get()
            and _read_only_entry()
            and (not pk or pk == PK)
            and (not company or company == COMPANY)
            and (not url or url == URL))


# Neither this private sentinel nor _SyntheticRun grants authority on its own:
# run() must create the instance AND bind the exact identity in _SESSION_ENTRY.
_ORIGIN = object()


class _SyntheticRun:
    __slots__ = ("_origin", "resume_path", "row", "events", "hold", "fills",
                 "navigations", "decisions", "status_writes", "positive",
                 "ipc_index", "open_controls", "input_values", "committed",
                 "post_count", "post_payload", "submit_ipc", "uploaded",
                 "last_page", "combo_observations", "combo_touched",
                 "submit_call_entered", "submit_guard", "post_attempts",
                 "post_rejection", "post_receipts", "inventory_diagnostics", "widget_receipts",
                 "entry_initial_not_ready", "entry_wait", "entry_observations",
                 "greenhouse", "visible_badge_anchor", "native_validity",
                 "w_lookalike", "w_target_ipc", "ashby")
    def __init__(self, resume_path: str):
        self._origin = _ORIGIN
        self.resume_path = resume_path
        self.row = {
            "pk": PK, "company": COMPANY, "status": "submitting",
            "gate_reason": "", "resume_seed": "fixture-base-seed",
            "resume_tex_key": "synthetic-only-resume",
            "human_approved_answers": {
                "Name": {"value": "Test User", "selector": "#name",
                         "label": "Name", "question": "", "url": URL},
            },
        }
        self.events = []
        self.hold = False
        self.fills = 0
        self.navigations = 0
        self.decisions = 0
        self.status_writes = []
        self.positive = False
        self.ipc_index = 0
        self.open_controls = set()
        self.input_values = {}
        self.committed = {}
        self.post_count = 0
        self.post_payload = None
        self.submit_ipc = 0
        self.uploaded = False
        self.last_page = None
        self.combo_observations = {}
        self.combo_touched = set()
        self.submit_call_entered = 0
        self.submit_guard = "not_reached"
        self.post_attempts = 0
        self.post_rejection = ""
        self.post_receipts = []
        self.inventory_diagnostics = None
        self.widget_receipts = []
        self.entry_initial_not_ready = False
        self.entry_wait = 'PENDING'
        self.entry_observations = []
        self.greenhouse = False
        self.visible_badge_anchor = 'PENDING'
        self.native_validity = {}
        self.w_lookalike = False
        self.w_target_ipc = 0
        self.ashby = False
        self.observe_page(None)

    def entry_provenance(self):
        if (not authorized(self, pk=PK, company=COMPANY, url=URL)
                or self.row.get('pk') != PK or self.row.get('company') != COMPANY):
            raise PermissionError('Synthetic entry authority is absent.')
        if not self.positive:
            return None
        if self.greenhouse:
            from tools.claude_chrome import GreenhouseJobAlias
            return GreenhouseJobAlias(pk=PK, company=COMPANY, ats='greenhouse',
                                      job_id='synthetic-job', wrapper=URL,
                                      careers_url=URL, board='example-co', url=URL)
        from tools.browser_skill_apply import ApplicationEntryProvenance
        return ApplicationEntryProvenance(PK, COMPANY, 'ashby' if self.ashby else 'lever', 'synthetic-job',
                                          URL, 'example-co', URL)

    def entry_admitted(self, page):
        root = (page.get('entry', {}).get('formless') or {}) if self.ashby else page.get('entry', {})
        if (self.entry_provenance() and root.get('root') and not root.get('error')
                and (self.ashby or (self.entry_initial_not_ready and
                    {f['status'] for f in page['entry']['frames']} == {'H', 'R', 'W', 'X'}))):
            self.entry_wait = 'PASS'


    def enable_positive_fixture(self) -> None:
        self.positive = True
        self.row['human_approved_answers'] = {}
        # Only Ashby stops before applicant IPC; the independent native leg
        # retains its pre-existing exact grants to exercise picker readbacks.
        if self.ashby:
            return
        specs = {
            'Name': ('#name', 'Name', 'Name', 'input', 'text'),
            'Phone': ('#phone', 'Phone', 'Phone', 'input', 'tel'),
            **{key: (selector, key, key, 'input', 'combobox')
               for key, selector in _CHOICE_SELECTORS.items()},
        }
        for key, (selector, label, question, tag, typ) in specs.items():
            self.row['human_approved_answers'][key] = {
                'value': _FACTS[key], 'selector': selector, 'label': label,
                'question': question, 'url': URL, 'tag': tag, 'type': typ,
                'form_selector': '#application-form', 'form_action': URL,
            }

    def _positive_ipc(self) -> tuple[tuple[str, ...], ...]:
        return (('fill', '#name', '--value', _FACTS['Name']),
                *(ipc for selector in _CHOICE_SELECTORS.values()
                  for ipc in (('click', selector), ('click', selector+'-option'))),
                ('fill', '#phone', '--value', _FACTS['Phone']))

    def observe_page(self, observed: dict) -> None:
        """Retain browser-read caption/value pairs as committed fixture state."""
        self.last_page = observed
        self.committed = {}
        self.combo_observations = {}
        frames = (observed or {}).get('greenhouse', {}).get('frames', [])
        if (self.greenhouse and frames and all(f.get('status') == 'R' for f in frames)
                and observed.get('greenhouse', {}).get('badge_responses') == 1
                and not observed.get('greenhouse', {}).get('response_controls')):
            self.visible_badge_anchor = 'PASS'
        controls = observed.get("controls", []) if isinstance(observed, dict) else []
        country = [c for c in controls if c.get('selector') == '#country']
        combo = country[0].get('combobox') or {} if len(country) == 1 else {}
        country_ok = ('Country' in self.combo_touched
                      and combo.get('receiverless_country') is True
                      and combo.get('selected_text') == _FACTS['Country']
                      and combo.get('displayed_text') == _FACTS['Country']
                      and not any(k in combo for k in ('hidden_selector', 'hidden_value', 'selected_value')))
        self.combo_observations['Country'] = {
            'outcome': ('not_reached' if 'Country' not in self.combo_touched else
                        'committed' if country_ok else 'not_committed'),
            'selected_text': combo.get('selected_text', ''),
            'displayed_text': combo.get('displayed_text', ''),
        }
        for key, selector in _COMBO_SELECTORS.items():
            if key not in self.combo_touched:
                self.combo_observations[key] = {
                    "outcome": "not_reached",
                    "visible_text": "", "selected_value": "", "hidden_value": "",
                    "reason": "combobox interaction was not reached",
                }
                continue
            matches = [c for c in controls
                       if isinstance(c, dict) and c.get("selector") == selector]
            if len(matches) != 1:
                self.combo_observations[key] = {
                    "outcome": "not_committed",
                    "visible_text": "", "selected_value": "", "hidden_value": "",
                    "reason": f"expected one observed control, found {len(matches)}",
                }
                continue
            combo = matches[0].get("combobox") or {}
            visible = combo.get("selected_text") or ""
            selected = combo.get("selected_value") or ""
            hidden_selector = combo.get("hidden_selector") or ""
            hidden = combo.get("hidden_value") or ""
            reasons = []
            if visible != _FACTS[key]:
                reasons.append("visible caption did not match the approved answer")
            if selected != _MACHINE_VALUES[key]:
                reasons.append("selected machine value did not match the expected value")
            if hidden_selector != _HIDDEN_SELECTORS[key]:
                reasons.append("linked hidden-value control was missing or changed")
            if hidden != _MACHINE_VALUES[key]:
                reasons.append("hidden machine value did not match the selected value")
            if combo.get('displayed_text') != _FACTS[key]:
                reasons.append('current displayed caption did not match the approved answer')
            observation = {
                "outcome": "committed" if not reasons else "not_committed",
                "visible_text": visible, "displayed_text": combo.get('displayed_text'), "selected_value": selected,
                "hidden_value": hidden,
                "reason": "; ".join(reasons),
            }
            self.combo_observations[key] = observation
            if not reasons:
                self.committed[key] = (_FACTS[key], _MACHINE_VALUES[key])


    def matches(self, *, url: str, company: str, pk: str, facts: dict,
                model: str, resume_path: str) -> bool:
        return (authorized(self, pk=pk, company=company, url=url)
                and facts == _FACTS and model == "synthetic-fixture"
                and resume_path == self.resume_path)

    def duplicate_refusal(self) -> bool:
        return self.row.get("status") in {"applied", "applied_manual"}

    def blocked(self, pk: str, row: dict) -> bool:
        return (pk != PK or self.hold or row.get("possible_submission")
                or row.get("fail_kind") == "uncertain"
                or row.get("gate_reason") == "submit_uncertain")

    def seed_fingerprint(self) -> str:
        return "fixture-base-seed"

    def stage_resume(self, path: str, owner: str) -> str:
        if path != self.resume_path or not Path(path).is_file():
            raise ValueError("Synthetic attachment missing; correct fixture and rerun.")
        return path

    def site_rules(self, url: str, company: str) -> str:
        if (url, company) != (URL, COMPANY):
            raise ValueError("Synthetic fixture has no authority for this origin.")
        return ""

    def emit(self, kind: str, **details) -> None:
        self.events.append({"kind": kind, "pk": details.get("pk")})

    async def decision(self, task: str, page: dict, history: list, *, model: str):
        self.widget_receipts = [r for r in history if r.get('action') == 'combobox']
        actions = (
            {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
            *({'action': 'combobox', 'selector': selector, 'fact': key}
              for key, selector in _CHOICE_SELECTORS.items()),
            {'action': 'fill', 'selector': '#phone', 'fact': 'Phone'},
            {'action': 'fill', 'selector': '#additional', 'fact': 'Additional question'},
        )
        if model != "synthetic-fixture" or self.decisions >= len(actions):
            raise ValueError("Synthetic decision sequence exhausted; rerun fixture.")
        action = actions[self.decisions]
        self.decisions += 1
        return action

    def mark_hold(self, pk: str) -> None:
        if pk != PK:
            raise ValueError("No synthetic hold for this job.")
        self.hold = True

    def is_held(self, pk: str) -> bool:
        return pk == PK and self.hold

    def set_status(self, pk: str, status, **attrs) -> None:
        if pk != PK:
            raise ValueError("No synthetic row for this job.")
        value = getattr(status, "value", status)
        self.row.update(status=value, **attrs)
        self.status_writes.append(value)

    def facade(self, raw):
        """A closed edge exposing only the original controller's exact fixture path."""
        state = self
        async def diagnose(observed):
            field = 'greenhouse' if state.greenhouse else 'entry'
            admitted = {'R'} if state.greenhouse else {'H', 'R', 'W', 'X', 'Z'}
            rejected = [f for f in (observed or {}).get(field, {}).get('frames', [])
                        if f.get('status') not in admitted]
            if (isinstance(observed, dict) and (
                    observed.get("inventory_verified") is not True
                    or observed.get("truncated") or observed.get("opaque_controls")
                    or observed.get("shadow_roots") or observed.get("unsupported_frames")
                    or rejected)):
                from tools.browser_skill_dom import entry_reader, greenhouse_reader
                script = _inventory_diagnostic_script()
                reader = greenhouse_reader if state.greenhouse else entry_reader
                result = await raw.call('evaluate', reader(script) if state.positive else script)
                state.inventory_diagnostics = result.get("value")
                if rejected and isinstance(state.inventory_diagnostics, dict):
                    # Keep the actual rejected snapshot distinct from the later
                    # diagnostic read: a settled second read cannot authorize it.
                    state.inventory_diagnostics['rejected_frames'] = [
                        {k:f.get(k) for k in ('selector','status','box','bounds',
                         'visibility','display','outside','root','ancestors')} for f in rejected]


        class _Closed:
            __slots__ = ('_closed_admissions',)

            async def navigate(self, url: str):
                if state.positive:
                    raise PermissionError('Typed fixture entry requires the single navigation IPC.')
                if url != URL or state.navigations:
                    raise PermissionError("Fixture permits one navigate to the fixed job URL.")
                if not authorized(state, pk=PK, company=COMPANY, url=URL):
                    raise PermissionError("Fixture authority has expired.")
                state.navigations += 1
                observed = await raw.navigate(url)
                state.observe_page(observed)
                if not isinstance(observed, dict) or observed.get("url") != URL:
                    raise PermissionError("Fixture redirected outside the fixed job URL.")
                await diagnose(observed)
                return observed

            async def page(self):
                if state.positive:
                    raise PermissionError('Typed fixture entry requires scoped PAGE evaluation.')
                if not state.navigations or not authorized(state):
                    raise PermissionError("Fixture cannot inspect another session.")
                observed = await raw.page()
                state.observe_page(observed)
                if not isinstance(observed, dict) or observed.get("url") != URL:
                    raise PermissionError("Fixture page escaped the fixed job URL.")
                await diagnose(observed)
                return observed

            async def call(self, *args, **kwargs):
                if (len(args)>1 and args[0] in {'click','focus','fill','evaluate','navigate'}
                        and args[1] in {'#w-button','#w-link','https://widgets.example-co.test'}):
                    state.w_target_ipc += 1
                if state.positive and args == ('navigate', URL) and not kwargs:
                    state.entry_provenance()
                    if state.navigations:
                        raise PermissionError('Fixture permits only one navigation IPC.')
                    state.navigations += 1
                    return await raw.call(*args)
                if not state.navigations or not authorized(state, pk=PK, url=URL):
                    raise PermissionError("Fixture denies this browser operation.")
                if state.positive and args and args[0] == "evaluate":
                    from tools.browser_skill_apply import (
                        ENTRY_PAGE, _ENTRY_FIXED_CONTROL_WITNESS,
                        GREENHOUSE_PAGE, _GREENHOUSE_FIXED_CONTROL_WITNESS)
                    page_script, witness_script = (
                        (GREENHOUSE_PAGE, _GREENHOUSE_FIXED_CONTROL_WITNESS) if state.greenhouse
                        else (ENTRY_PAGE, _ENTRY_FIXED_CONTROL_WITNESS))
                    if args not in {('evaluate', page_script),
                                    ('evaluate', witness_script)} or kwargs:
                        raise PermissionError("Fixture denies this browser operation.")
                    result = await raw.call(*args)
                    observed = result.get('value') if isinstance(result, dict) else None
                    if not isinstance(observed, dict) or observed.get('url') != URL:
                        raise PermissionError('Fixture page escaped the fixed job URL.')
                    field = 'greenhouse' if state.greenhouse else 'entry'
                    frames = observed.get(field, {}).get('frames', [])
                    structural = ((observed.get('entry', {}).get('formless') or {}) if state.ashby
                                  else observed.get('entry', {}))
                    if args[1] == page_script:
                        if not state.entry_observations:
                            if state.greenhouse:
                                from tools.browser_skill_apply import _native_answer
                                state.entry_initial_not_ready = not any(
                                    _native_answer(c) and c.get('in_form')
                                    for c in observed.get('controls', []))
                            else:
                                state.entry_initial_not_ready = not structural.get('root')
                        state.observe_page(observed)
                        await diagnose(observed)
                    elif any(f.get('status') not in ({'R'} if state.greenhouse else {'H','R','W','X','Z'})
                             for f in frames):
                        await diagnose(observed)
                    state.entry_observations.append({
                        'reader':'page' if args[1] == page_script else 'witness',
                        'root':bool(observed.get(field, {}).get('root') if state.greenhouse else structural.get('root')),
                        'frames':[f.get('status') for f in frames],
                        **({'badge_responses':observed.get(field, {}).get('badge_responses'),
                            'controls':[c.get('selector') for c in observed.get('controls', [])]}
                           if state.greenhouse else {})})
                    return result
                if not state.positive:
                    if (args != ("fill", "#name", "--value", "Test User")
                            or kwargs or state.fills):
                        raise PermissionError("Fixture denies this browser operation.")
                    state.fills += 1
                    return await raw.call(*args)
                if args == ('click', '#submit') or (state.ashby and args and args[0] == 'click'):
                    state.submit_call_entered += 1
                    state.submit_guard = "denied: fixture forbids committing BSK IPC"
                    raise PermissionError("Fixture never permits submission-capable IPC.")
                if state.ashby:
                    raise PermissionError("Fixture never permits applicant or committing BSK IPC.")
                sequence = state._positive_ipc()
                if (state.ipc_index >= len(sequence) or args != sequence[state.ipc_index]
                        or (args[0] == "upload" and kwargs != {"timeout": 130})
                        or (args[0] != "upload" and kwargs)):
                    raise PermissionError("Fixture denies this browser operation.")
                if args[0] == "upload" and (
                        args[3] != state.resume_path
                        or Path(state.resume_path).read_bytes() != _RESUME_BYTES):
                    raise PermissionError("Fixture attachment is not the staged synthetic PDF.")
                if args[0] == "click" and args[1] in _OPTION_SELECTORS:
                    key = _OPTION_SELECTORS[args[1]]
                    selector = _CHOICE_SELECTORS[key]
                    if selector not in state.open_controls:
                        raise PermissionError("Fixture option was not opened by its combobox.")
                await raw.call(*args, **kwargs)
                if state.greenhouse and args[0] == 'click' and args[1] in _OPTION_SELECTORS:
                    result = await raw.call('evaluate', _COMMITTED_VALIDITY)
                    observed = result.get('value') if isinstance(result, dict) else None
                    if not isinstance(observed, dict):
                        raise PermissionError('Synthetic native validity readback unavailable.')
                    selector = _CHOICE_SELECTORS[_OPTION_SELECTORS[args[1]]]
                    state.native_validity[selector] = observed.get(selector) is True
                for key, selector in _CHOICE_SELECTORS.items():
                    if (args[0] in {"click", "fill"} and len(args) > 1
                            and (args[1] == selector
                                 or _OPTION_SELECTORS.get(args[1]) == key)):
                        state.combo_touched.add(key)
                state.ipc_index += 1
                if args[0] == "fill":
                    state.fills += 1
                    state.input_values[args[1]] = args[3]
                elif args[0] == "upload":
                    state.uploaded = True
                elif args[0] == 'click' and args[1] in _CHOICE_SELECTORS.values():
                    state.open_controls.add(args[1])
                elif args[0] == "click" and args[1] in _OPTION_SELECTORS:
                    key = _OPTION_SELECTORS[args[1]]
                    selector = _CHOICE_SELECTORS[key]
                    state.open_controls.discard(selector)

        return _Closed()


class _FixtureHandler(BaseHTTPRequestHandler):
    # Chrome can leave an idle preconnect; shutdown cannot interrupt its read.
    timeout = 2

    def _reject(self, status: int = 404) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if (self.path != _PATH or self.headers.get("Host") != "127.0.0.1:18789"):
            self._reject()
            return
        state = getattr(getattr(self, 'server', None), 'synthetic_run', None)
        document = _greenhouse_form() if type(state) is _SyntheticRun and state.greenhouse else _FORM
        if type(state) is not _SyntheticRun or state.ashby:
            document = _ashby_form()
        if type(state) is _SyntheticRun and state.w_lookalike:
            document = document.replace(b'id="w-button"', b'id="w-button" aria-label="Synthetic consent"')
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(document)))
        self.end_headers()
        self.wfile.write(document)

    def _record_post_receipt(self, state, status: int) -> None:
        if type(state) is _SyntheticRun:
            state.post_receipts.append({
                "request_line": getattr(self, "requestline", f"POST {self.path}"),
                "host": self.headers.get("Host", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "response_status": status,
            })

    def do_POST(self) -> None:
        state = getattr(getattr(self, "server", None), "synthetic_run", None)
        if type(state) is _SyntheticRun:
            state.post_attempts += 1
            state.post_rejection = "Synthetic fixture refuses every non-GET form method."
        self._record_post_receipt(state, 405)
        self._reject(405)
    do_HEAD = do_POST
    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
    do_OPTIONS = do_POST
    do_CONNECT = do_POST
    do_TRACE = do_POST
    def log_message(self, *args) -> None:
        pass  # Never log request headers, URLs or session data.


def _run_scenario(*, greenhouse=False, w_lookalike=False, ashby=False) -> int:
    """Independent sealed sessions share only the original fixed loopback route."""
    if not _daemon_origin() or _SESSION_ENTRY.get() is not None:
        raise RuntimeError("Synthetic fixture requires the exclusive daemon CLI entry.")
    # Fail closed if the fixed port is occupied; NEVER choose another port,
    # host, profile or browser. No socket is started by import or offline tests.
    with HTTPServer(("127.0.0.1", 18789), _FixtureHandler) as server:
        server.timeout = 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory(prefix="appliedin-synthetic-fixture-") as root:
                document = Path(root) / "Synthetic-Resume.pdf"
                document.write_bytes(_RESUME_BYTES)
                state = _SyntheticRun(str(document))
                state.greenhouse = greenhouse
                state.w_lookalike = w_lookalike
                state.ashby = ashby
                state.enable_positive_fixture()
                server.synthetic_run = state
                token = _SESSION_ENTRY.set(state)
                try:
                    # Imports only AFTER daemon sanitized its environment. The
                    # ordinary controller runs with its original F1-F5 checks.
                    from tools.browser_skill_apply import (
                        apply, _approval_question_collisions)
                    result = asyncio.run(apply(
                        URL, COMPANY, dict(_FACTS), "synthetic-fixture", pk=PK,
                        resume_path=state.resume_path, fixture_context=state))
                finally:
                    _SESSION_ENTRY.reset(token)
                form_question = result.get("form_question") or {}
                expected_commits = {key: (_FACTS[key], value)
                                     for key, value in _MACHINE_VALUES.items()}
                # Four choices and exact tel precede the unapproved optional field.
                expected_fills = 2
                expected_decisions = 8
                phone = [c for c in (state.last_page or {}).get('controls', [])
                         if c.get('selector') == '#phone']
                phone_ok = len(phone)==1 and phone[0].get('value')==_FACTS['Phone']
                from tools.browser_skill_apply import _check_closed_commit, control, Gate
                receipt_ok = len(state.widget_receipts) == 5
                try:
                    for receipt in state.widget_receipts:
                        _check_closed_commit(control(state.last_page, receipt),
                                             receipt['approved_value'], receipt)
                except Gate:
                    receipt_ok = False
                ok = (result.get("status") == "gate"
                      and (state.visible_badge_anchor == 'PASS' if greenhouse else state.entry_wait == 'PASS')
                      and (not greenhouse or state.native_validity == dict.fromkeys(_CHOICE_SELECTORS.values(), True))
                      and form_question.get("selector") == "#additional"
                      and state.fills == expected_fills
                      and state.decisions == expected_decisions
                      and state.committed == expected_commits
                      and phone_ok
                      and receipt_ok
                      and state.combo_observations['Country']['outcome'] == 'committed'
                      and state.ipc_index == len(state._positive_ipc())
                      and state.last_page.get('inventory_verified') is True
                      and state.last_page.get('opaque_controls') is False
                      and all(state.combo_observations.get(key, {}).get("outcome")
                              == "committed"
                              and state.combo_observations[key].get("visible_text")
                              == _FACTS[key]
                              and state.combo_observations[key].get("selected_value")
                              == _MACHINE_VALUES[key]
                              and state.combo_observations[key].get("hidden_value")
                              == _MACHINE_VALUES[key]
                              for key in _MACHINE_VALUES)
                      and not state.uploaded
                      and state.submit_call_entered == 0
                      and state.submit_guard == "not_reached"
                      and state.submit_ipc == 0
                      and state.post_attempts == 0
                      and state.post_count == 0)
                if w_lookalike:
                    ok = (result.get('status') == 'gate'
                          and 'unknown/untrusted application frame' in result.get('question', '')
                          and any('unknown' in o['frames'] for o in state.entry_observations)
                          and state.fills == state.decisions == state.ipc_index == 0
                          and not state.uploaded and state.submit_call_entered == 0
                          and state.submit_ipc == state.post_attempts == state.post_count == 0)
                ok = ok and state.w_target_ipc == 0
                field_selectors = {'Name': '#name', 'Phone': '#phone',
                                   'Additional question': '#additional'}
                outcomes = [
                    {"field": key, "outcome": (
                        "filled" if state.input_values.get(selector) == _FACTS[key]
                        else "not_reached")}
                    for key, selector in field_selectors.items()
                ]
                for key in _MACHINE_VALUES:
                    observation = state.combo_observations.get(key, {
                        "outcome": "not_reached",
                        "visible_text": "", "selected_value": "",
                        "hidden_value": "",
                        "reason": "no BrowserSkill interaction reached this combobox",
                    })
                    outcomes.append({"field": key, **observation})
                outcomes.append({'field': 'Country', **state.combo_observations['Country']})
                outcomes.append({
                    "field": "Résumé",
                    "outcome": "uploaded" if state.uploaded else "not_reached",
                })
                collisions = []
                seen_collision_sets = set()
                if isinstance(state.last_page, dict):
                    for target in state.last_page.get("controls", []):
                        if (not isinstance(target, dict)
                                or target.get("type") in {"radio", "hidden"}):
                            continue
                        question = target.get("question") or target.get("label")
                        if not isinstance(question, str) or not question.strip():
                            continue
                        others = [
                            c for c in _approval_question_collisions(
                                state.last_page, target)
                            if c.get("type") != "hidden"]
                        pair = [target, *others]
                        selectors = tuple(sorted(
                            c.get("selector", "") for c in pair))
                        if len(pair) < 2 or selectors in seen_collision_sets:
                            continue
                        seen_collision_sets.add(selectors)
                        collisions.append({
                            "question": question,
                            "controls": [{
                                "selector": c.get("selector"),
                                "label": c.get("label"),
                                "type": c.get("type"),
                            } for c in pair],
                        })
                gate = None if result.get("status") == "applied" else {
                    "kind": result.get("reason") or result.get("status"),
                    "field": {selector: field for field, selector in (
                        *field_selectors.items(),
                        *_CHOICE_SELECTORS.items(),
                    )}.get(form_question.get("selector")),
                    "reason": result.get("question") or result.get("detail"),
                }
                structural = (state.last_page or {}).get('entry', {}).get('formless') or {}
                if state.ashby:
                    ok = (result.get('status') == 'gate' and state.entry_wait == 'PASS'
                          and form_question.get('owner_kind') == 'ashby-formless-root'
                          and form_question.get('selector') == '#_systemfield_name'
                          and form_question.get('form_selector') == structural.get('root')
                          and form_question.get('form_action') == structural.get('submit')
                          and state.fills == 0 and state.decisions == 0 and state.ipc_index == 0
                          and not state.uploaded and not state.committed
                          and state.submit_call_entered == 0 and state.submit_ipc == 0
                          and state.post_attempts == 0 and state.post_count == 0
                          and (state.last_page or {}).get('inventory_verified') is True)
                    gate = {'kind': result.get('reason') or result.get('status'),
                            'field': form_question.get('control_label'),
                            'reason': result.get('question') or result.get('detail')}
                inventory = {key: (state.last_page or {}).get(key) for key in (
                    "inventory_verified", "truncated", "opaque_controls",
                    "shadow_roots", "unsupported_frames")}
                if isinstance(state.inventory_diagnostics, dict):
                    inventory["diagnostics"] = {
                        key: state.inventory_diagnostics.get(key) for key in (
                            "ready_state", "offending_elements", "shadow_hosts",
                            "frames", "rejected_frames")}
                print(json.dumps({"fixture": "ashby-formless" if state.ashby else "native-form",
                                  'scenario': ('ashby' if state.ashby else
                                               'w_button_aria_label' if w_lookalike else
                                               'greenhouse' if greenhouse else 'entry'),
                                  'w_target_ipc': state.w_target_ipc,
                                  'session_safe_gate': ok,
                                  'safe_gate': ok,
                                  'visible_badge_anchor': state.visible_badge_anchor,
                                  'native_committed_validity': state.native_validity,
                                  'entry_wait': state.entry_wait,
                                  'entry_initial_not_ready': state.entry_initial_not_ready,
                                  'entry_observations': state.entry_observations,
                                  "submitted": state.submit_ipc > 0,
                                  "status": result.get("status"),
                                  'applicant_ipc': state.ipc_index, 'model_decisions': state.decisions,
                                  'root_witnessed': state.entry_wait == 'PASS',
                                  'final_stage': ('unknown-frame-gate' if w_lookalike else
                                                  'direct-human-question' if state.ashby else
                                                  'Additional question') if ok else 'premature',
                                  "post_received": state.post_count == 1,
                                  "gate": gate,
                                  "inventory": inventory,
                                  "submit": {
                                      "guard": state.submit_guard,
                                      "call_entered": state.submit_call_entered > 0,
                                      "ipc_returned": state.submit_ipc > 0,
                                      "post_attempts": state.post_attempts,
                                      "post_rejection": state.post_rejection,
                                      "receipts": state.post_receipts,
                                  }}))
                return 0 if ok else 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError('Synthetic fixture harness server did not stop.')


def run() -> int:
    """One invocation, existing provenances, positive and isolated W human Gates."""
    entry = _run_scenario()
    greenhouse = _run_scenario(greenhouse=True)
    lookalike = _run_scenario(w_lookalike=True)
    ashby = _run_scenario(ashby=True)
    scenarios = (entry, greenhouse, lookalike, ashby)
    ok = all(result == 0 for result in scenarios)
    print(json.dumps({'fixture': 'native-form', 'safe_gate': ok,
                      'entry_wait': 'PASS' if entry == 0 else 'FAIL',
                      'visible_badge_anchor': 'PASS' if greenhouse == 0 else 'FAIL',
                      'w_lookalike_gate': 'PASS' if lookalike == 0 else 'FAIL',
                      'sessions': len(scenarios)}))
    return 0 if ok else 1
