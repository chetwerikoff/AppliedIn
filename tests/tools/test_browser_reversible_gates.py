"""Synthetic Ashby accessibility, ungated preparation and final Submit checks.

PAGE and independent readback use offline DOM readers. Historical per-action
owner grants and skip tests were removed under Issue #293.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import run
from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, dom, posting, entry
from tests.tools.test_browser_formless import effect_calls, observed, resume_set_name, root_world
from tests.tools.test_browser_controller_support import install_controller_session


def _fixture(monkeypatch, tmp_path, **shape):
    shape.setdefault('missingPane', not shape.get('movePane', False))
    row, endpoint, decision, _ = root_world(monkeypatch, **shape)
    assert forms._formless_root(endpoint.current)['root'] == ''
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    variants = (
        ('#location', 'input', 'combobox', 'Location', 'location'),
        ('#start-date', 'input', 'date', 'Start date', 'date'),
        ('#boolean-yes', 'button', 'button', 'Yes', 'boolean'),
        ('#boolean-no', 'button', 'button', 'No', 'boolean'),
        ('#certification', 'div', 'combobox',
         'I certify that these synthetic statements are true', 'certification'),
    )
    for snapshot in (endpoint.current, endpoint.fixed):
        # Preserve the real form-less entry witness and all its original
        # recipients. The original fields are optional in this scenario.
        for c in snapshot['controls']:
            if c.get('type') not in {'file', 'submit'}:
                c['required'] = False
        sample = next(c for c in snapshot['controls']
                      if c['selector'] == '#_systemfield_name')
        for selector, tag, kind, label, name in variants:
            control = deepcopy(sample)
            control.update(selector=selector, id=selector[1:], name=name,
                           label=label, question=label, tag=tag, type=kind,
                           native_type=('text' if kind == 'combobox' and tag == 'input'
                                        else kind if tag in {'input', 'button'} else ''),
                           value='', has_value=False, checked=False, required=True,
                           submit=False, commits_application=False,
                           in_form=False, native_form_owner_selector='',
                           form_selector='', form_action='', formaction='',
                           disabled=False, readonly=False, inert=False, visible=True)
            if name == 'boolean':
                control['question'] = 'Work arrangement?'
            if kind == 'combobox':
                options = ([dict(selector='#remote-option', unique=True, label='Remote',
                                 value='remote', safe=True)]
                           if name == 'location' else
                           [dict(selector='#cert-option', unique=True,
                                 label='I certify', value='yes', safe=True)])
                control['combobox'] = dict(
                    linked=True, link_id=name + '-list',
                    listbox_selector='#' + name + '-list', options=options,
                    selected_text='', displayed_text='', selected_value='',
                    hidden_selector='', hidden_value='')
                if name == 'certification':
                    control['widget'] = 'MultiValueSelect'
            else:
                control['combobox'] = None
            snapshot['controls'].append(control)
            # The fixed reader must independently identify the same receiver
            # INSIDE the application. A PAGE-only appended control has no grant
            # authority regardless of its label and required flag.
            evidence_sample = next(e for e in snapshot['entry']['root_evidence']
                                   if e['selector'] == '#_systemfield_name')
            evidence = deepcopy(evidence_sample)
            evidence.update(
                selector=selector, id=selector[1:],
                tag=tag, type=kind, native_type=control['native_type'],
                label=label if tag == 'input' else '',
                associated=tag == 'input',
                inside=True, in_container=True, unique=True,
                native_owner='', declared_owner='', formaction=None,
                submit=False, dropzone=False, iti=False, contenteditable=False,
                visible=True, readonly=False, disabled=False, inert=False,
                required=True, value='', files=[])
            snapshot['entry']['root_evidence'].append(evidence)

    _session, tracking, pdf = install_controller_session(
        monkeypatch, row, endpoint, tmp_path)
    # One fresh application controller must bind to each new BrowserSkill
    # Session. The fixture transport and both independent readers remain real.
    session_class = type(_session)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *args: session_class('apply'))

    class Bank:
        def __init__(self):
            self.values = {}
            self.writes = []

        def put(self, label, value, scope, **kwargs):
            self.values[label] = value
            self.writes.append((label, value, scope, kwargs))

    bank = Bank()
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank)
    monkeypatch.setattr('core.stores.make_stores', lambda: stores)
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *args, **kwargs: False)

    def enqueue(pk, stores_arg, **kwargs):
        assert pk == PK and stores_arg is stores
        row['status'] = 'submitting'
        return {'result': 'queued', 'pk': pk}

    monkeypatch.setattr(run, '_enqueue_apply', enqueue)
    return row, endpoint, decision, stores, pdf


async def _apply(pdf, facts=None):
    return await forms.apply(posting('ashby'), COMPANY, facts or {}, 'synthetic',
                             pk=PK, resume_path=pdf)


def _bind_consume(stores, row):
    """The shared row-stub tracking gains the production consume semantics.

    The patch itself is the storage contract's own ``direct_gate_patch``; only
    the persistence is this file's synthetic row.
    """
    from core.storage.base import direct_gate_patch

    def consume(pk, *, expected_pending, disposition, grant=None, skip=None):
        assert pk == row['pk']
        patch = direct_gate_patch(row, expected_pending, disposition, grant, skip)
        if patch is None:
            return False
        row.update(deepcopy(patch))
        return True

    stores.tracking.consume_direct_form_gate = consume


def _issue(row, gate):
    """Persist the gate exactly as run._apply_direct does: fresh generation."""
    q = gate['form_question']
    row.update(status='needs_human', gate_source='applier',
               gate_call_id='direct', gate_reason='owner_question',
               gate_pending=run._direct_gate_pending(gate['question'], deepcopy(q), 'owner'))
    return {'expected_gate_generation': row['gate_pending']['generation'],
            'expected_question_identity': forms.owner_question_identity(PK, q)}


def _answer(row, stores, gate, value):
    assert gate['status'] == 'gate'
    assert gate['reason'] == 'owner_question'
    assert gate['review_route'] == 'owner'
    q = gate['form_question']
    assert q['pk'] == PK and q['url'] == entry('ashby')
    assert q['owner_kind'] == 'ashby-reversible-entry'
    assert q['form_selector'] == '' and q['form_action'] == ''
    _bind_consume(stores, row)
    tokens = _issue(row, gate)
    assert run._direct_form_label(PK, row) == q['label']
    assert run.resume_job(PK, value, stores, disposition='answer', **tokens)[
        'result'] == 'queued'
    grant = row['human_approved_answers'][q['label']]
    for k in ('selector', 'url', 'control_label', 'question',
              'tag', 'type', 'form_selector', 'form_action'):
        expected = q['control_label'] if k == 'control_label' else q[k]
        observed = grant['label'] if k == 'control_label' else grant[k]
        assert observed == expected
    assert grant['value'] == value
    if q['type'] == 'button':
        assert grant['boolean_members'] == q['boolean_members']
    return q

def _mark_census_answered(control):
    """Census fallback tests isolate one blank; siblings are already answered."""
    if control.get('type') in {'radio', 'checkbox'}:
        control.update(checked=True, value='', has_value=False)
    elif control.get('type') == 'button':
        control.update(checked=control.get('label') == 'No')
    elif control.get('type') == 'combobox':
        combo = control.setdefault('combobox', {})
        combo.update(selected_text='Synthetic choice', selected_value='synthetic')
    elif control.get('type') not in {'file', 'hidden', 'password', 'submit', 'image'}:
        control.update(value='Synthetic answer', has_value=True)

def _only_required(endpoint, selectors):
    """Keep PAGE and independent ENTRY receiver requirements identical."""
    for snapshot in (endpoint.current, endpoint.fixed):
        for control in snapshot['controls']:
            if control['selector'] != '#dropzone':
                control['required'] = control['selector'] in selectors
                if control['selector'] not in selectors:
                    _mark_census_answered(control)
        for evidence in snapshot['entry']['root_evidence']:
            if evidence['selector'] != '#dropzone':
                evidence['required'] = evidence['selector'] in selectors
                if evidence['selector'] not in selectors:
                    evidence.update(value='Synthetic answer', checked=True)



def _witnessed_native(endpoint, *, selector, kind, label, question, name, value):
    for snapshot in (endpoint.current, endpoint.fixed):
        sample = next(c for c in snapshot['controls'] if c['selector'] == '#start-date')
        control = deepcopy(sample)
        control.update(
            selector=selector, id=selector[1:], tag='input', type=kind,
            native_type=kind, label=label, question=question, name=name,
            value=value, has_value=False, required=True, checked=False,
            disabled=False, visible=True, readonly=False, inert=False,
            combobox=None, visible_text=label)
        snapshot['controls'].append(control)
        original = next(e for e in snapshot['entry']['root_evidence']
                        if e['selector'] == '#start-date')
        e = deepcopy(original)
        e.update(selector=selector, id=selector[1:], tag='input', type=kind,
                 native_type=kind, label=label, associated=True, value=value,
                 required=True, unique=True, in_container=True, inside=True,
                 visible=True, disabled=False, readonly=False, inert=False)
        snapshot['entry']['root_evidence'].append(e)


def _record_choice_click(endpoint, selector, value, *, checked_selector=None):
    previous = endpoint.call

    async def call(method, *args, **kwargs):
        outcome = await previous(method, *args, **kwargs)
        if method == 'click' and args == (selector,):
            target = checked_selector or selector
            for snapshot in (endpoint.current, endpoint.fixed):
                for control in (snapshot['controls'] +
                                snapshot['entry']['root_evidence']):
                    if control['selector'] == target:
                        control.update(value=value, has_value=True, checked=True)
        return outcome

    endpoint.call = call



def _record_boolean_press(endpoint, selector):
    """The probed live Ashby shape of a pressed Yes/No member (FF-243a).

    Ashby marks the chosen <button> only with aria-pressed, which the reader
    reports as checked; its value stays '' and has_value False. An earlier
    double also wrote value/has_value onto the button, a shape no live page
    produces, which kept the formless readback green while every live row gated.
    """
    previous = endpoint.call

    async def call(method, *args, **kwargs):
        outcome = await previous(method, *args, **kwargs)
        if method == 'click' and args == (selector,):
            for snapshot in (endpoint.current, endpoint.fixed):
                for control in snapshot['controls']:
                    if control['selector'] == selector:
                        control.update(value='', has_value=False, checked=True)
        return outcome

    endpoint.call = call


_LIVE_ENTRIES = r"""
(() => {
  const c=document.querySelector('.ashby-application-form-container');
  const resume=c.children.findIndex(n=>!!n.querySelector('#_systemfield_resume'));
  let at=resume+1;
  const entry=(title, owner)=>{
    const e=node('div',{class:'ashby-application-form-field-entry'},c);
    c.children.splice(c.children.indexOf(e),1); c.children.splice(at++,0,e);
    node('label',{'for':owner,class:'_heading_x1y2z_52 '+
      (__REQUIRED__ ? '_required_x1y2z_91 ' : '')+
      '_label_a1b2c_42 ashby-application-form-question-title',text:title},e);
    return e;
  };
  const untyped=(parent,attrs)=>{
    const b=node('button',attrs,parent); b.type='submit'; delete b.required; return b;};
  const location=node('div',{},entry('Synthetic location','synthetic-unbound-location'));
  // A typeless input still reports type text, and the live picker is closed
  // (an unlinked open combobox would make the whole inventory opaque).
  node('input',{role:'combobox','aria-autocomplete':'list',
    'aria-expanded':'false',placeholder:'Start typing'},location).type='text';
  untyped(location,{'aria-label':'Toggle'});
  node('input',{type:'text',required:true,placeholder:'Pick date'},
    node('div',{},node('div',{},entry('Synthetic start date','synthetic-unbound-date'))));
  const group=node('div',{class:'ashby-application-form-input-yesno'},
    entry('Synthetic Boolean question','synthetic-unbound-boolean'));
  untyped(group,{text:'Yes'});
  untyped(group,{text:'No'});
  node('input',{type:'checkbox',style:{display:'none'},rect:null},group);
  __MUTATE__
  // Readers enumerate in tree order, as a browser does.
  const ordered=[]; const visit=e=>{ordered.push(e); e.children.forEach(visit);};
  visit(document.body); nodes.splice(0,nodes.length,...ordered);
})();
"""
_LOCATION = 'div:nth-of-type(4) > div:nth-of-type(1) > input:nth-of-type(1)'
_DATE = 'div:nth-of-type(5) > div:nth-of-type(1) > div:nth-of-type(1) > input:nth-of-type(1)'
_YES = 'div:nth-of-type(6) > div:nth-of-type(1) > button:nth-of-type(1)'
_NO = 'div:nth-of-type(6) > div:nth-of-type(1) > button:nth-of-type(2)'


def _live_dom(required=True, mutate='', **shape):
    prefix = (_LIVE_ENTRIES.replace('__REQUIRED__', 'true' if required else 'false')
              .replace('__MUTATE__', mutate))
    spec = dict(root=False, formless=True, movePane=True, domOrder=True,
                url=entry('ashby'), **shape)
    return (dom(spec, prefix + forms.ENTRY_PAGE),
            dom(spec, prefix + forms._ENTRY_FIXED_CONTROL_WITNESS))


def _by_locator(page, suffix):
    matches = [c for c in page['controls']
               if suffix.endswith(c['diagnostic_locator'])
               and c['selector'].endswith(suffix)]
    assert len(matches) == 1, suffix
    return matches[0]


def _live_census(monkeypatch, tmp_path, **dom_shape):
    row, endpoint, decision, _ = root_world(monkeypatch, movePane=True)
    endpoint.current, endpoint.fixed = _live_dom(**dom_shape)
    formless = endpoint.current['entry']['formless']
    assert formless['root'] == '' and formless['pane'] and formless['container']
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _session, tracking, pdf = install_controller_session(
        monkeypatch, row, endpoint, tmp_path)
    session_class = type(_session)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *args: session_class('apply'))
    bank = SimpleNamespace(values={}, put=lambda *args, **kwargs: None)
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank)
    monkeypatch.setattr('core.stores.make_stores', lambda: stores)
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(run, '_enqueue_apply', lambda pk, s, **kwargs: (
        row.update(status='submitting'), {'result': 'queued', 'pk': pk})[1])
    return row, endpoint, decision, stores, pdf


def test_live_census_reports_the_sibling_mismatch_and_titled_idless_receivers():
    page, _ = _live_dom()
    assert page['entry']['formless']['error'] == 'application root sibling identity mismatch'
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    for suffix, question, titled in ((_LOCATION, 'Synthetic location', True),
                                     (_DATE, 'Synthetic start date', True),
                                     (_YES, 'Synthetic Boolean question', False)):
        control = _by_locator(page, suffix)
        e = evidence[control['selector']]
        # Issue #252 ARCH 17:33: an id-less input takes its single field-entry
        # title as caption in both readers; a Yes/No button keeps its own text.
        assert (e['id'], e['associated'], e['entry_titles']) == ('', titled, 1)
        assert e['entry_label'] == control['question'] == question
        if titled:
            assert e['label'] == control['label'] == question
    yes, no = _by_locator(page, _YES), _by_locator(page, _NO)
    for member, caption in ((yes, 'Yes'), (no, 'No')):
        e = evidence[member['selector']]
        assert (member['tag'], member['type'], member['native_type']) == (
            'button', 'button', 'submit')
        assert member['submit'] is False and member['in_form'] is False
        assert (e['yes_no'], e['untyped'], e['caption']) == (True, True, caption)
    # The Location toggle is untyped too, but it is no Yes/No member.
    toggle = _by_locator(page, 'div:nth-of-type(4) > div:nth-of-type(1) > button:nth-of-type(1)')
    assert toggle['type'] == 'submit' and evidence[toggle['selector']]['yes_no'] is False



def test_title_marker_never_makes_a_receiver_required():
    page, _ = _live_dom(mutate="location.children[0].required=false;")
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    location = _by_locator(page, _LOCATION)
    assert location['required'] is False
    assert evidence[location['selector']]['required'] is False

def test_live_census_readers_retain_accessible_choices_without_admission_gates():
    """Both production readers expose ordinary fields, not field-grant scopes."""
    page, fixed = _live_dom()
    assert page['entry']['formless']['error'] == 'application root sibling identity mismatch'
    observed = {c['selector']: c for c in page['controls']}
    independent = {c['selector']: c for c in fixed['controls']}
    for suffix in (_LOCATION, _DATE, _YES, _NO):
        selector = _by_locator(page, suffix)['selector']
        assert selector in observed and selector in independent
        assert observed[selector]['type'] == independent[selector]['type']
    assert '#_systemfield_email' in observed and '#_systemfield_email' in independent
    assert '#structural-submit' in observed
    assert not hasattr(forms, '_reversible_entry_scope')

async def _owner_order(row, endpoint, stores, pdf):
    facts, asked = {}, []
    while True:
        gate = await _apply(pdf, dict(facts))
        if gate.get('reason') != 'owner_question':
            return asked, gate
        q = gate['form_question']
        kind = next(c['type'] for c in endpoint.current['controls']
                    if c['selector'] == q['selector'])
        answer = {'button': 'No', 'combobox': 'Remote'}.get(kind, 'Synthetic answer')
        _answer(row, stores, gate, answer)
        asked.append(q['selector'])
        facts[q['label']] = answer
        assert len(asked) < 12


def _make_optional_census_reach_model_gate(endpoint, *, leave_unanswered):
    for snapshot in (endpoint.current, endpoint.fixed):
        snapshot['entry']['formless']['error'] = ''
        for control in snapshot['controls'] + snapshot['entry']['root_evidence']:
            control['required'] = False
            if control.get('selector') == leave_unanswered:
                control.update(value='', has_value=False, checked=False)
            elif control.get('type') == 'button':
                control['checked'] = control.get('label') == 'Yes'
            elif control.get('type') != 'file':
                control.update(value='Synthetic prior answer', has_value=True)


@pytest.mark.parametrize('error', [
    'ambiguous application roots', 'unsupported custom control', 'other',
])
def test_unproven_ashby_root_cannot_pass_final_submit_boundary(error):
    page, _ = _live_dom()
    page['entry']['formless']['error'] = error
    with pytest.raises(forms.Gate, match='final Ashby application container'):
        forms._formless_boundary(page)


@pytest.mark.parametrize('damage', ['pane', 'container', 'submit', 'truncated'])
def test_incomplete_ashby_root_cannot_pass_final_submit_boundary(damage):
    page, _ = _live_dom()
    root = page['entry']['formless']
    if damage == 'truncated':
        root[damage] = True
    else:
        root[damage] = ''
    with pytest.raises(forms.Gate, match='final Ashby application container'):
        forms._formless_boundary(page)

def test_submit_text_or_exact_submit_is_never_a_boolean_member():
    page, _ = _live_dom(mutate=(
        "Object.defineProperty(group.children[0],'innerText',"
        "{get:()=>'Submit application'});"))
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    assert not any(e['yes_no'] for e in evidence.values())
    submit = next(e for e in evidence.values() if e['submit'])
    assert submit['yes_no'] is False and submit['type'] == 'submit'


@pytest.mark.asyncio
async def test_untyped_yes_no_choice_is_reversible_and_does_not_submit(monkeypatch, tmp_path):
    """An ordinary Yes/No click must not be classified as committing Submit."""
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), pdf, {}, {}, [], {}, lambda: None)
    monkeypatch.setattr(controller, '_page', AsyncMock(return_value=endpoint.current))
    final_guard = AsyncMock(side_effect=forms.Gate('final guard decided'))
    monkeypatch.setattr(controller, 'check_pre_submit', final_guard)
    await controller.before_ipc(
        SimpleNamespace(), ('click', _by_locator(endpoint.current, _NO)['selector']), {})
    final_guard.assert_not_awaited()
    assert controller.committed is False

@pytest.mark.parametrize('spec', [
    {'wrapperTag': 'section'}, {'wrapperClass': 'generic'}, {'paneTag': 'section'},
    {'movePane': True}, {'moveSubmit': True},
])
@pytest.mark.asyncio
async def test_tolerated_mismatch_with_nothing_unanswered_keeps_final_value_guard(
        monkeypatch, tmp_path, spec):
    """Submit on a tolerated mismatch is still refused by the final value re-check when this run did not fill the values."""
    def optional(snapshots):
        # Already-valued receivers are never re-asked, so nothing is left for
        # the owner on this route and preparation reaches the final guard.
        for snapshot in snapshots:
            for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                if c.get('type') not in {'file', 'submit'} and c.get('tag') != 'button':
                    c['value'] = 'Synthetic value'
                    if 'has_value' in c:
                        c['has_value'] = True

    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    row, raw, decide, _ = root_world(monkeypatch, **spec)
    optional((raw.current, raw.fixed))
    transport, _, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    button = next(c for c in raw.current['controls'] if c['label'] == 'Submit application')
    original_call = raw.call

    async def call(method, *args, **kwargs):
        name = resume_set_name(args[0]) if method == 'evaluate' else None
        if name:
            raw.calls.append((method, *args))
            raw.current, raw.fixed = observed(
                **spec, attached=True, files=[name], displayName=name)
            optional((raw.current, raw.fixed))
            return {'value': {'set': True, 'files': [name]}}
        return await original_call(method, *args, **kwargs)
    raw.call = call
    # FF-224e: the one server upload runs before the first decision, so the
    # first Submit already meets the final value guard.
    decide.side_effect = [dict(action='submit', selector=button['selector'])]
    result = await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert result['status'] == 'gate', result
    assert result['reason'] == 'defect_pre_submit_item_2', result
    assert result['item'] == 2
    assert decide.await_count == 1
    assert effect_calls(raw.calls) == [
        ('set', Path(transport._application_controller.resume_path).name)]
    assert not transport._application_controller.committed


# Issue #202 AC6/AC8: exact SKIP suppression and question-key collisions on the
# live-shaped rootless sibling-mismatch census (real PAGE and fixed readers).

def _skip(row, stores, gate):
    q = gate['form_question']
    assert forms.owner_skip_eligible(q), q
    _bind_consume(stores, row)
    tokens = _issue(row, gate)
    assert run.resume_job(PK, '', stores, disposition='skip', **tokens)['result'] == 'queued'
    return tokens['expected_question_identity']


_TWIN_TEXT = (
    "node('input',{type:'text',placeholder:'Twin'},"
    "node('div',{},entry('Synthetic location','synthetic-twin')));")
_TWIN_BOOLEAN = (
    "const twin=node('div',{class:'ashby-application-form-input-yesno'},"
    "entry('Synthetic Boolean question','synthetic-twin-boolean'));"
    "untyped(twin,{text:'Yes'}); untyped(twin,{text:'No'});")


def test_distinct_named_required_radio_groups_are_checked_independently_at_final_submit():
    """A checked radio in group A cannot satisfy required group B with same caption."""
    def radio(selector, name, value, checked):
        return {
            'selector': selector, 'tag': 'input', 'type': 'radio',
            'name': name, 'form_selector': '#synthetic-form',
            'question': 'Synthetic choice', 'label': value.title(),
            'value': value, 'checked': checked, 'required': True,
            'disabled': False,
        }

    controls = [
        radio('#a-yes', 'a', 'yes', True), radio('#a-no', 'a', 'no', False),
        radio('#b-yes', 'b', 'yes', False), radio('#b-no', 'b', 'no', False),
        {'selector': '#resume', 'type': 'file', 'label': 'Resume',
         'disabled': False, 'files': ['Synthetic.pdf']},
    ]
    page = {'controls': controls, 'inventory_verified': True,
            'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(page, {}, uploaded=True, attachment='Synthetic.pdf')
    controls[2]['checked'] = True
    forms.check_form(page, {}, uploaded=True, attachment='Synthetic.pdf')

# Issue #202 r08 / ARCH19:48 AC8 bounds on the rootless sibling-mismatch route,
# where final Submit always refuses. A reload replaces every element and the
# id-less layout has no real non-positional discriminator, so an exact same
# question and shape reuses the owner's grant; any changed caption or question
# text voids it and re-asks. Same-key twins (two candidates) are covered by
# test_same_question_key_entries_refuse_answer_skip_and_bank.

# Swapped captions keep a proven Yes/No pair but change which member is "No".
_CAPTION = ("group.children[0].setAttribute('text','No');"
            "group.children[1].setAttribute('text','Yes');")
_QUESTION = ("group.parentElement.children[0]"
             ".setAttribute('text','Synthetic changed question');")


async def _boolean_granted(row, endpoint, stores, pdf):
    yes = _by_locator(endpoint.current, _YES)['selector']
    facts = {}
    while True:
        gate = await _apply(pdf, dict(facts))
        assert gate.get('reason') == 'owner_question', gate
        q = gate['form_question']
        kind = next(c['type'] for c in endpoint.current['controls']
                    if c['selector'] == q['selector'])
        answer = {'button': 'No', 'combobox': 'Remote'}.get(kind, 'Synthetic answer')
        _answer(row, stores, gate, answer)
        facts[q['label']] = answer
        if q['selector'] == yes:
            return facts, q
        assert len(facts) < 12


@pytest.mark.asyncio
async def test_privacy_policy_link_in_hidden_label_never_gets_a_click(monkeypatch, tmp_path):
    """Even a model-selected label does not authorize a navigating fallback."""
    _, endpoint, decision, _, pdf = _fixture(
        monkeypatch, tmp_path, hiddenAttestation=True, movePane=True,
        attestationInteractive='link')
    assert not any(c.get('ashby_hidden_choice') is True
                   for c in endpoint.current['controls'])
    for snapshot in (endpoint.current, endpoint.fixed):
        for c in snapshot['controls']:
            if c.get('type') not in {'file', 'submit'}:
                _mark_census_answered(c)
                c['required'] = False
        for e in snapshot['entry']['root_evidence']:
            if e.get('selector') != '#hidden-attestation':
                e.update(value='Synthetic answer', checked=True)
    decision.return_value = {'action': 'click', 'selector': '#hidden-attestation-label'}
    outcome = await _apply(pdf)
    assert outcome['status'] == 'gate'
    assert not [call for call in endpoint.calls if call[0] == 'click']

