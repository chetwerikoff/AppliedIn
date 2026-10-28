"""Issue #196: production apply() owner gates on an offline rootless Ashby route.

The PAGE and fixed witness come from the same synthetic DOM readers used by
test_browser_formless. No real browser, user profile, submission, or network.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import run
from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, dom, posting, entry
from tests.tools.test_browser_formless import root_world, observed
from tests.tools.test_browser_controller_support import install_controller_session


def _fixture(monkeypatch, tmp_path, **shape):
    row, endpoint, decision, _ = root_world(monkeypatch, missingPane=True, **shape)
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


@pytest.mark.asyncio
async def test_reversible_required_questions_resume_in_observed_order(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    first = await _apply(pdf)
    assert first['form_question']['type'] == 'combobox'
    assert first['form_question']['control_label'] == 'Location'
    assert not decision.await_count
    assert row['human_approved_answers'] == {}
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'upload', 'select'}]

    _answer(row, stores, first, 'Remote')
    second = await _apply(pdf, {'Location': 'Remote'})
    assert second['form_question']['type'] == 'date'
    assert second['form_question']['control_label'] == 'Start date'
    assert not decision.await_count
    _answer(row, stores, second, '2026-11-10')

    boolean = await _apply(pdf, {'Location': 'Remote', 'Start date': '2026-11-10'})
    assert boolean['reason'] == 'owner_question'
    assert boolean['form_question']['type'] == 'button'
    assert boolean['form_question']['boolean_members'] == [
        {'selector': '#boolean-yes', 'caption': 'Yes', 'value': 'yes'},
        {'selector': '#boolean-no', 'caption': 'No', 'value': 'no'}]
    assert not decision.await_count
    assert set(row['human_approved_answers']) == {'Location', 'Start date'}
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'upload', 'select'}]
    _answer(row, stores, boolean, 'No')
    certification = await _apply(
        pdf, {'Location': 'Remote', 'Start date': '2026-11-10',
              'Work arrangement?': 'No'})
    assert certification['form_question']['type'] == 'combobox'
    assert 'certify' in certification['form_question']['label']
    _answer(row, stores, certification, 'I certify')
    assert 'I certify that these synthetic statements are true' not in stores.answer_bank.values
    assert set(row['human_approved_answers']) == {
        'Location', 'Start date', 'Work arrangement?',
        'I certify that these synthetic statements are true'}


@pytest.mark.asyncio
async def test_ambiguous_root_wins_over_open_required_location(monkeypatch, tmp_path):
    """A reversible fallback cannot ask for Location on two unsafe roots."""
    _, endpoint, decision, _, pdf = _fixture(
        monkeypatch, tmp_path, secondFormlessComplete=True)
    assert endpoint.current['entry']['formless']['error'] == 'ambiguous application roots'
    assert next(c for c in endpoint.current['controls']
                if c['selector'] == '#location')['required']
    file = next(c for c in endpoint.current['controls']
                if c['type'] == 'file' and c['label'] == 'Resume')
    submit = next(c for c in endpoint.current['controls']
                  if c['label'] == 'Submit application')
    original_call = endpoint.call

    async def keep_both_roots_after_upload(method, *args, **kwargs):
        if method != 'upload':
            return await original_call(method, *args, **kwargs)
        assert args[0] == file['selector'] and args[1] == '--file'
        assert kwargs == {'timeout': 130}
        endpoint.calls.append((method, *args))
        refreshed = observed(
            missingPane=True, secondFormlessComplete=True, attached=True,
            files=[Path(args[2]).name], displayName=Path(args[2]).name)
        for earlier, later in zip((endpoint.current, endpoint.fixed), refreshed):
            later['controls'].extend(
                deepcopy(c) for c in earlier['controls']
                if c['selector'] in {'#location', '#start-date', '#boolean-yes',
                                     '#boolean-no', '#certification'})
        endpoint.current, endpoint.fixed = refreshed
        return {}

    endpoint.call = keep_both_roots_after_upload
    decision.side_effect = [
        {'action': 'upload', 'selector': file['selector']},
        {'action': 'submit', 'selector': submit['selector']},
    ]
    result = await _apply(pdf)
    assert result['status'] == 'gate', result
    assert 'ambiguous application roots' in result['question'], result
    assert result['reason'] != 'owner_question'
    assert result['form_question'] is None
    assert decision.await_count == 2
    assert [c[0] for c in endpoint.calls
            if c[0] in {'fill', 'click', 'upload', 'select'}] == ['upload']


@pytest.mark.asyncio
async def test_model_free_form_gate_uses_fresh_code_owned_control(monkeypatch, tmp_path):
    row, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    for snapshot in (endpoint.current, endpoint.fixed):
        for c in snapshot['controls']:
            if c['selector'] in {
                    '#location', '#start-date', '#boolean-yes', '#boolean-no',
                    '#certification'}:
                c['required'] = False

    async def free_gate(*args, **kwargs):
        # The question becomes required after pre-model witness, but before
        # the model's prose gate. Code must re-observe and bind the field.
        for snapshot in (endpoint.current, endpoint.fixed):
            next(c for c in snapshot['controls']
                 if c['selector'] == '#location')['required'] = True
        return {'action': 'gate', 'question': 'UNBOUND_MODEL_PROSE'}

    decision.side_effect = free_gate
    result = await _apply(pdf)
    assert decision.await_count == 1
    assert result['review_route'] == 'owner'
    assert result['reason'] == 'owner_question'
    assert result['form_question']['selector'] == '#location'
    assert 'UNBOUND_MODEL_PROSE' not in result['question']
    assert row['human_approved_answers'] == {}


@pytest.mark.asyncio
async def test_unwritable_multivalue_select_has_exact_defect_and_no_model(monkeypatch, tmp_path):
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    for snapshot in (endpoint.current, endpoint.fixed):
        for c in snapshot['controls']:
            c['required'] = c['selector'] == '#certification'
            if c['selector'] == '#certification':
                c['type'] = 'MultiValueSelect'
                c['combobox'] = None
        # A real unsupported widget appears in both independent readers.
        # A PAGE-only type change is a receiver-identity defect instead.
        for evidence in snapshot['entry']['root_evidence']:
            evidence['required'] = evidence['selector'] == '#certification'
            if evidence['selector'] == '#certification':
                evidence['type'] = 'MultiValueSelect'
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert result['reason'] == 'defect_multivalueselect'
    assert result['review_route'] == 'hard_refusal'
    assert result['form_question'] is None
    assert not decision.await_count


@pytest.mark.asyncio
async def test_no_required_question_keeps_original_model_gate(monkeypatch, tmp_path):
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    for snapshot in (endpoint.current, endpoint.fixed):
        for c in snapshot['controls']:
            c['required'] = False
    decision.return_value = {'action': 'gate', 'question': 'Manual synthetic review'}
    result = await _apply(pdf)
    assert decision.await_count == 1
    assert result['review_route'] == 'agent_unavailable'
    assert result['reason'] == 'unknown_field'
    assert result['form_question'] is None


def _only_required(endpoint, selectors):
    """Keep PAGE and independent ENTRY receiver requirements identical."""
    for snapshot in (endpoint.current, endpoint.fixed):
        for control in snapshot['controls']:
            if control['selector'] != '#dropzone':
                control['required'] = control['selector'] in selectors
        for evidence in snapshot['entry']['root_evidence']:
            if evidence['selector'] != '#dropzone':
                evidence['required'] = evidence['selector'] in selectors


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


def _record_choice_click(endpoint, selector, value):
    previous = endpoint.call

    async def call(method, *args, **kwargs):
        outcome = await previous(method, *args, **kwargs)
        if method == 'click' and args == (selector,):
            for snapshot in (endpoint.current, endpoint.fixed):
                for control in (snapshot['controls'] +
                                snapshot['entry']['root_evidence']):
                    if control['selector'] == selector:
                        control.update(value=value, has_value=True, checked=True)
        return outcome

    endpoint.call = call


@pytest.mark.asyncio
async def test_boolean_pair_grant_only_clicks_witnessed_selected_member(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    assert gate['reason'] == 'owner_question'
    assert gate['form_question']['control_label'] == 'Yes'
    assert not decision.await_count
    assert not [call for call in endpoint.calls if call[0] == 'click']
    _answer(row, stores, gate, 'No')
    _record_choice_click(endpoint, '#boolean-no', 'no')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['status'] == 'gate'
    assert decision.await_count == 2
    assert [c for c in endpoint.calls if c[0] == 'click'] == [
        ('click', '#boolean-no')]
    assert row['human_approved_answers']['Work arrangement?']['value'] == 'No'


@pytest.mark.asyncio
async def test_boolean_pair_rejects_unapproved_member_before_click(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'Yes')
    decision.return_value = {
        'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'}
    result = await _apply(pdf, {'Work arrangement?': 'Yes'})
    assert result['status'] == 'gate'
    assert [c for c in endpoint.calls if c[0] == 'click'] == []


@pytest.mark.parametrize('untrusted', ['no-evidence', 'off-container', 'excluded'])
@pytest.mark.asyncio
async def test_foreign_required_ownerless_receiver_never_discloses_or_writes(
        monkeypatch, tmp_path, untrusted):
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    for snapshot in (endpoint.current, endpoint.fixed):
        original = next(c for c in snapshot['controls']
                        if c['selector'] == '#start-date')
        foreign = deepcopy(original)
        foreign.update(selector='#foreign-required', id='foreign-required',
                       name='foreign-required', label='Foreign required',
                       question='Foreign required', type='text', native_type='text',
                       value='', has_value=False, required=True, combobox=None)
        index = next(i for i, c in enumerate(snapshot['controls'])
                     if c['selector'] == '#location')
        snapshot['controls'].insert(index, foreign)
        if untrusted != 'no-evidence':
            source = next(e for e in snapshot['entry']['root_evidence']
                          if e['selector'] == '#start-date')
            witness = deepcopy(source)
            witness.update(selector='#foreign-required', id='foreign-required',
                           label='Foreign required', type='text',
                           native_type='text', required=True,
                           associated=untrusted == 'excluded',
                           in_container=untrusted == 'excluded',
                           inside=untrusted == 'excluded',
                           value='', files=[])
            snapshot['entry']['root_evidence'].append(witness)
        if untrusted == 'excluded':
            snapshot['entry']['formless']['excluded'].append('#foreign-required')
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert result['reason'] == 'defect_receiver'
    assert result['review_route'] == 'hard_refusal'
    assert result['form_question'] is None
    assert not decision.await_count
    assert not [c for c in endpoint.calls if c[0] in {
        'fill', 'click', 'select', 'upload'}]
    # Even the raw IPC producer, not only the planner, refuses the receiver.
    raw = SimpleNamespace(call=endpoint.call)
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), pdf, {}, {}, [], {}, lambda: None)
    foreign = next(c for c in endpoint.current['controls']
                   if c['selector'] == '#foreign-required')
    with pytest.raises(forms.Gate) as refusal:
        await controller._reversible_receiver(raw, endpoint.current, foreign)
    assert refusal.value.reason == 'defect_receiver'


@pytest.mark.parametrize('relevant', [False, True])
@pytest.mark.asyncio
async def test_hidden_witness_only_blocks_application_receivers(
        monkeypatch, tmp_path, relevant):
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    for snapshot in (endpoint.current, endpoint.fixed):
        source = next(e for e in snapshot['entry']['root_evidence']
                      if e['selector'] == '#start-date')
        hidden = deepcopy(source)
        hidden.update(selector='#unrelated-hidden', id='unrelated-hidden',
                      type='hidden', native_type='hidden', label='', value='',
                      required=False, associated=False,
                      in_container=relevant, inside=relevant, visible=False)
        snapshot['entry']['root_evidence'].append(hidden)
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert result['reason'] == ('defect_structural' if relevant else 'owner_question')
    if relevant:
        assert result['form_question'] is None
    else:
        assert result['form_question']['selector'] == '#location'
    assert not decision.await_count
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'select'}]


@pytest.mark.asyncio
async def test_native_owner_checkbox_answer_and_guarded_choose(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, set())
    _witnessed_native(endpoint, selector='#accept', kind='checkbox',
                      label='I agree', question='I agree to the terms',
                      name='accept', value='yes')
    gate = await _apply(pdf)
    assert gate['reason'] == 'owner_question'
    assert gate['form_question']['type'] == 'checkbox'
    assert not decision.await_count
    assert not [c for c in endpoint.calls if c[0] == 'click']
    _answer(row, stores, gate, 'yes')
    assert 'I agree to the terms' not in stores.answer_bank.values
    _record_choice_click(endpoint, '#accept', 'yes')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#accept', 'fact': 'I agree to the terms'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert decision.await_count == 2
    assert [c for c in endpoint.calls if c[0] == 'click'] == [
        ('click', '#accept')]


@pytest.mark.asyncio
async def test_witnessed_native_radio_owner_question_and_guarded_choose(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, set())
    _witnessed_native(endpoint, selector='#radio-remote', kind='radio',
                      label='Remote', question='Preferred setup?',
                      name='setup', value='remote')
    _witnessed_native(endpoint, selector='#radio-onsite', kind='radio',
                      label='On site', question='Preferred setup?',
                      name='setup', value='onsite')
    gate = await _apply(pdf)
    assert gate['reason'] == 'owner_question'
    assert gate['form_question']['type'] == 'radio'
    assert len(gate['form_question']['radio_members']) == 2
    assert not decision.await_count
    _answer(row, stores, gate, 'Remote')
    _record_choice_click(endpoint, '#radio-remote', 'remote')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#radio-remote', 'fact': 'Preferred setup?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Preferred setup?': 'Remote'})
    assert result['status'] == 'gate'
    assert decision.await_count == 2
    assert [c for c in endpoint.calls if c[0] == 'click'] == [
        ('click', '#radio-remote')]


@pytest.mark.asyncio
async def test_unwitnessed_native_radio_member_is_named_defect(monkeypatch, tmp_path):
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, set())
    _witnessed_native(endpoint, selector='#radio-remote', kind='radio',
                      label='Remote', question='Preferred setup?',
                      name='setup', value='remote')
    _witnessed_native(endpoint, selector='#radio-onsite', kind='radio',
                      label='On site', question='Preferred setup?',
                      name='setup', value='onsite')
    for snapshot in (endpoint.current, endpoint.fixed):
        snapshot['entry']['root_evidence'] = [
            e for e in snapshot['entry']['root_evidence']
            if e['selector'] != '#radio-onsite']
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert result['reason'] == 'defect_radio'
    assert result['form_question'] is None
    assert not decision.await_count
    assert not [c for c in endpoint.calls if c[0] == 'click']


@pytest.mark.asyncio
async def test_date_only_populated_field_guards_opaque_continue_before_click(monkeypatch):
    # All unrelated controls absent: the populated date alone is private data.
    target = {'selector': '#continue', 'tag': 'button', 'type': 'button',
              'native_type': 'button', 'label': 'Continue', 'disabled': False,
              'submit': False, 'navigates': False, 'commits_application': False}
    page = {'url': entry('ashby'), 'controls': [
        {'selector': '#date', 'type': 'date',
         'value': '2026-11-10', 'has_value': True},
        target]}
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), '', {}, {}, [], {}, lambda: None)
    monkeypatch.setattr(controller, '_page', AsyncMock(return_value=page))
    monkeypatch.setattr(controller, '_fresh_preparation', lambda url: None)
    final_guard = AsyncMock(side_effect=forms.Gate(
        'The final Submit guard must decide before a possibly committing click.'))
    monkeypatch.setattr(controller, 'authorize_final_submit', final_guard)
    with pytest.raises(forms.Gate):
        await controller.before_ipc(SimpleNamespace(), ('click', '#continue'), {})
    final_guard.assert_awaited_once()
    assert controller.committed is False


@pytest.mark.asyncio
async def test_rootless_standard_privacy_checkbox_needs_exact_owner_grant(
        monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, set())
    _witnessed_native(
        endpoint, selector='#privacy', kind='checkbox', label='I acknowledge',
        question='I acknowledge processing of my application data',
        name='privacy', value='yes')
    gate = await _apply(pdf)
    assert gate['reason'] == 'owner_question'
    assert gate['form_question']['selector'] == '#privacy'
    assert not [c for c in endpoint.calls if c[0] == 'click']
    _answer(row, stores, gate, 'yes')
    _record_choice_click(endpoint, '#privacy', 'yes')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#privacy',
         'fact': 'I acknowledge processing of my application data'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert decision.await_count == 2
    assert [c for c in endpoint.calls if c[0] == 'click'] == [
        ('click', '#privacy')]


@pytest.mark.asyncio
async def test_boolean_pair_requires_both_independent_member_captions(monkeypatch, tmp_path):
    """A PAGE-only Yes/No label cannot confer a selected-member grant."""
    _, endpoint, decision, _, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    second = next(c for c in endpoint.fixed['controls']
                  if c['selector'] == '#boolean-no')
    second['label'] = 'Different option'
    result = await _apply(pdf)
    assert result['status'] == 'gate'
    assert result['reason'] == 'defect_button'
    assert result['review_route'] == 'hard_refusal'
    assert result['form_question'] is None
    assert not decision.await_count
    assert not [c for c in endpoint.calls
                if c[0] in {'fill', 'click', 'select', 'upload'}]


# Issue #198: the live Ashby census structure, replayed through both real ENTRY
# readers. The autofill pane sits outside the container/Submit wrapper, so the
# root certificate reports a sibling-identity mismatch with an empty root.
# Location, Date and the Yes/No pair have no id: their title's for= resolves to
# nothing, and the pair members are untyped (DOM submit) nameless buttons with
# a hidden checkbox. Tags, types, classes and nesting are copied; every caption
# is synthetic. Titles copy the live class structure (heading, label and, when
# marked, a required token) with synthetic module hashes. A real button has no
# required property, so the pair carries none; the native required inputs
# exercise the owner gates (live Location was optional).
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


def _scope(endpoint):
    witness = {c['selector']: c for c in endpoint.fixed['controls']}
    return forms._reversible_entry_scope(endpoint.current, witness)


def test_live_census_reports_the_sibling_mismatch_and_titled_idless_receivers():
    page, _ = _live_dom()
    assert page['entry']['formless']['error'] == 'application root sibling identity mismatch'
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    for suffix, question in ((_LOCATION, 'Synthetic location'),
                             (_DATE, 'Synthetic start date'),
                             (_YES, 'Synthetic Boolean question')):
        control = _by_locator(page, suffix)
        e = evidence[control['selector']]
        assert (e['id'], e['associated'], e['entry_titles']) == ('', False, 1)
        assert e['entry_label'] == control['question'] == question
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



@pytest.mark.parametrize('marked,mutate', [
    (True, ''),
    (False, ''),
    (False, "group.children.forEach(b=>b.setAttribute('aria-required','true'));"),
])
def test_every_proven_yes_no_pair_is_owner_required(marked, mutate):
    """Ashby's title marker is a per-build hash and a button has no required
    property, so an optional pair is asked rather than a required one skipped."""
    for page in _live_dom(required=marked, mutate=mutate):
        evidence = {e['selector']: e for e in page['entry']['root_evidence']}
        for suffix in (_YES, _NO):
            member = _by_locator(page, suffix)
            assert member['required'] is True
            assert evidence[member['selector']]['required'] is True


def test_title_marker_never_makes_a_receiver_required():
    page, _ = _live_dom(mutate="location.children[0].required=false;")
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    location = _by_locator(page, _LOCATION)
    assert location['required'] is False
    assert evidence[location['selector']]['required'] is False

def test_live_census_admits_email_location_date_and_boolean(monkeypatch, tmp_path):
    """Every live control was refused by the root error, not by its own evidence."""
    _, endpoint, _, _, _ = _live_census(monkeypatch, tmp_path)
    page = endpoint.current
    scope = _scope(endpoint)
    admitted = {_by_locator(page, s)['selector'] for s in (_LOCATION, _DATE, _YES, _NO)}
    assert admitted | {'#_systemfield_email'} <= scope
    # The pane, the real Submit and the untitled toggle are never receivers.
    assert '#structural-submit' not in scope
    assert not scope & set(page['entry']['formless']['excluded'])
    toggle = _by_locator(page, 'div:nth-of-type(4) > div:nth-of-type(1) > button:nth-of-type(1)')
    assert toggle['selector'] not in scope


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


@pytest.mark.asyncio
async def test_live_census_owner_questions_follow_full_field_order(monkeypatch, tmp_path):
    """ARCH14:39: on the sibling-mismatch route every admitted unanswered
    receiver is owner-asked in field order, natively required or not."""
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    page = endpoint.current
    model_calls = []
    decision.side_effect = lambda *a, **k: model_calls.append(len(row.get(
        'human_approved_answers') or {})) or forms.Gate('synthetic model stop')
    asked, _ = await _owner_order(row, endpoint, stores, pdf)
    assert asked == ['#_systemfield_name', '#_systemfield_email',
                     *(_by_locator(page, s)['selector'] for s in (_LOCATION, _DATE, _YES)),
                     '#additional']
    # Location and the textarea are optional on the live page; both were asked.
    assert not _by_locator(page, _LOCATION)['required']
    boolean = row['human_approved_answers']['Synthetic Boolean question']
    assert boolean
    # The model is reached only once every owner question is answered.
    assert model_calls and all(n == len(asked) for n in model_calls)
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'upload', 'select'}]


@pytest.mark.asyncio
async def test_prevalued_receiver_is_never_owner_asked(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _live_census(
        monkeypatch, tmp_path, mutate="location.children[0].value='Synthetic city';")
    asked, _ = await _owner_order(row, endpoint, stores, pdf)
    assert _by_locator(endpoint.current, _LOCATION)['selector'] not in asked
    assert _by_locator(endpoint.current, _DATE)['selector'] in asked


def test_only_the_sibling_mismatch_route_asks_every_receiver():
    page, _ = _live_dom()
    assert forms._owner_asks_every_receiver(page)
    for change in ({'error': 'ambiguous application roots'},
                   {'root': '#application-root'}):
        changed = deepcopy(page)
        changed['entry']['formless'].update(change)
        assert not forms._owner_asks_every_receiver(changed)

@pytest.mark.parametrize('error', [
    'ambiguous application roots', 'unsupported custom control', 'other'])
def test_any_other_root_error_admits_no_live_receiver(monkeypatch, tmp_path, error):
    _, endpoint, _, _, _ = _live_census(monkeypatch, tmp_path)
    endpoint.current['entry']['formless']['error'] = error
    assert not _scope(endpoint)
    email = next(c for c in endpoint.current['controls']
                 if c['selector'] == '#_systemfield_email')
    with pytest.raises(forms.Gate) as refusal:
        forms._reversible_entry_recipient(endpoint.current, email)
    assert refusal.value.reason == 'defect_receiver'
    assert 'predicate=formless.error;' in str(refusal.value)


@pytest.mark.parametrize('damage,predicate', [
    ('pane', 'formless.pane'), ('container', 'formless.container'),
    ('submit', 'formless.submit'), ('truncated', 'formless.truncated')])
def test_tolerated_mismatch_still_needs_complete_structure(
        monkeypatch, tmp_path, damage, predicate):
    _, endpoint, _, _, _ = _live_census(monkeypatch, tmp_path)
    endpoint.current['entry']['formless'][damage] = damage == 'truncated'
    assert not _scope(endpoint)
    with pytest.raises(forms.Gate, match=f'predicate={predicate};'):
        forms._reversible_entry_recipient(
            endpoint.current, _by_locator(endpoint.current, _DATE))


@pytest.mark.parametrize('mutate,suffix,predicate', [
    # A second title in the entry leaves the caption unproven.
    ("node('label',{class:'ashby-application-form-question-title',text:'Other'},"
     "c.children[4]);", _DATE, 'label.associated'),
    # A title owned by a different live control cannot caption this one.
    ("c.children[4].children[0].setAttribute('for','_systemfield_name');",
     _DATE, 'label.associated'),
    # A third button makes the group no exact Yes/No pair.
    ("untyped(group,{text:'Maybe'});", _YES, 'label.associated'),
    # A form owner or override can submit: never a Boolean member.
    ("group.children[0].setAttribute('formaction','/synthetic');", _YES, 'evidence.formaction'),
])
def test_unproven_idless_receivers_fail_closed_by_name(
        monkeypatch, tmp_path, mutate, suffix, predicate):
    _, endpoint, _, _, _ = _live_census(monkeypatch, tmp_path, mutate=mutate)
    target = _by_locator(endpoint.current, suffix)
    assert target['selector'] not in _scope(endpoint)
    with pytest.raises(forms.Gate) as refusal:
        forms._reversible_entry_recipient(endpoint.current, target)
    assert refusal.value.reason == 'defect_receiver'
    assert (f"predicate={predicate}; control={target['diagnostic_locator']}; tag="
            in str(refusal.value))


def test_submit_text_or_exact_submit_is_never_a_boolean_member():
    page, _ = _live_dom(mutate=(
        "Object.defineProperty(group.children[0],'innerText',"
        "{get:()=>'Submit application'});"))
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    assert not any(e['yes_no'] for e in evidence.values())
    submit = next(e for e in evidence.values() if e['submit'])
    assert submit['yes_no'] is False and submit['type'] == 'submit'


@pytest.mark.asyncio
async def test_boolean_click_after_choose_reaches_final_submit_guard(monkeypatch, tmp_path):
    """An untyped Yes/No is DOM submit: the raw producer never treats it as a write."""
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), pdf, {}, {}, [], {}, lambda: None)
    monkeypatch.setattr(controller, '_page', AsyncMock(return_value=endpoint.current))
    monkeypatch.setattr(controller, '_fresh_preparation', lambda url: None)
    final_guard = AsyncMock(side_effect=forms.Gate('final guard decided'))
    monkeypatch.setattr(controller, 'authorize_final_submit', final_guard)
    with pytest.raises(forms.Gate, match='final guard decided'):
        await controller.before_ipc(
            SimpleNamespace(), ('click', _by_locator(endpoint.current, _NO)['selector']), {})
    final_guard.assert_awaited_once()
    assert controller.committed is False


def test_receiver_defect_names_structure_never_ids_or_values(monkeypatch, tmp_path):
    _, endpoint, _, _, _ = _live_census(monkeypatch, tmp_path)
    page = deepcopy(endpoint.current)
    email = next(c for c in page['controls'] if c['selector'] == '#_systemfield_email')
    email['value'] = 'PRIVATE_SYNTHETIC_VALUE'
    with pytest.raises(forms.Gate) as refusal:
        forms._reversible_entry_recipient(page, email)
    text = str(refusal.value)
    assert refusal.value.reason == 'defect_receiver'
    assert f"predicate=value.equal; control={email['diagnostic_locator']}" in text
    assert 'tag=input; type=email.' in text
    assert 'PRIVATE_SYNTHETIC_VALUE' not in text and '_systemfield' not in text


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
    file = next(c for c in raw.current['controls']
                if c['type'] == 'file' and c['label'] == 'Resume')
    button = next(c for c in raw.current['controls'] if c['label'] == 'Submit application')
    original_call = raw.call

    async def call(method, *args, **kwargs):
        if method == 'upload':
            raw.calls.append((method, *args))
            name = Path(args[2]).name
            raw.current, raw.fixed = observed(
                **spec, attached=True, files=[name], displayName=name)
            optional((raw.current, raw.fixed))
            return {}
        return await original_call(method, *args, **kwargs)
    raw.call = call
    decide.side_effect = [dict(action='upload', selector=file['selector']),
                          dict(action='submit', selector=button['selector']),
                          dict(action='submit', selector=button['selector'])]
    result = await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert result['status'] == 'gate', result
    assert 'Unverified or changed form value' in result['question'], result
    assert decide.await_count == 3
    assert [c for c in raw.calls if c[0] not in {'evaluate', 'navigate'}] == [
        ('upload', file['selector'], '--file',
         transport._application_controller.resume_path)]
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


@pytest.mark.asyncio
async def test_exact_skip_suppresses_only_that_receiver_then_asks_the_next(monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    page = endpoint.current
    location = _by_locator(page, _LOCATION)['selector']
    facts, gate = {}, None
    while True:
        gate = await _apply(pdf, dict(facts))
        assert gate['reason'] == 'owner_question', gate
        q = gate['form_question']
        if q['selector'] == location:
            break
        _answer(row, stores, gate, 'Synthetic answer')
        facts[q['label']] = 'Synthetic answer'
    q = gate['form_question']
    # Natively optional on this rootless route means UNKNOWN, never optional.
    assert (q['route'], q['requiredness']) == ('ashby-sibling-mismatch', 'unknown')
    identity = _skip(row, stores, gate)
    assert row['owner_gate_skips'][identity]['route'] == 'ashby-sibling-mismatch'
    assert q['label'] not in (row.get('human_approved_answers') or {})
    assert stores.answer_bank.values.get(q['label']) is None
    nxt = await _apply(pdf, dict(facts))
    assert nxt['reason'] == 'owner_question'
    assert nxt['form_question']['selector'] == _by_locator(page, _DATE)['selector']
    # A known REQUIRED field is never skippable.
    assert nxt['form_question']['requiredness'] == 'required'
    assert not forms.owner_skip_eligible(nxt['form_question'])
    # The skipped receiver was never written: a SKIP is not a value.
    assert not [c for c in endpoint.calls
                if c[0] in {'fill', 'click', 'upload', 'select'} and location in c]


@pytest.mark.asyncio
async def test_skip_record_for_another_identity_or_generation_never_suppresses(
        monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    location = _by_locator(endpoint.current, _LOCATION)['selector']
    facts = {}
    while True:
        gate = await _apply(pdf, dict(facts))
        if gate['form_question']['selector'] == location:
            break
        _answer(row, stores, gate, 'Synthetic answer')
        facts[gate['form_question']['label']] = 'Synthetic answer'
    identity = forms.owner_question_identity(PK, gate['form_question'])
    good = {'identity': identity, 'route': 'ashby-sibling-mismatch', 'kind': 'combobox',
            'label': gate['form_question']['label'], 'generation': 'gen-synthetic'}
    for record in ({**good, 'identity': 'q1_other'}, {**good, 'route': ''},
                   {**good, 'kind': 'text'}, {**good, 'generation': ''}):
        row['owner_gate_skips'] = {identity: record}
        again = await _apply(pdf, dict(facts))
        assert again['form_question']['selector'] == location, record
    # A record under a different identity key names a different question.
    row['owner_gate_skips'] = {'q1_other': {**good, 'identity': 'q1_other'}}
    assert (await _apply(pdf, dict(facts)))['form_question']['selector'] == location


_TWIN_TEXT = (
    "node('input',{type:'text',placeholder:'Twin'},"
    "node('div',{},entry('Synthetic location','synthetic-twin')));")
_TWIN_BOOLEAN = (
    "const twin=node('div',{class:'ashby-application-form-input-yesno'},"
    "entry('Synthetic Boolean question','synthetic-twin-boolean'));"
    "untyped(twin,{text:'Yes'}); untyped(twin,{text:'No'});")


@pytest.mark.parametrize('mutate,twin', [
    (_TWIN_TEXT, 'Synthetic location'), (_TWIN_BOOLEAN, 'Synthetic Boolean question'),
])
@pytest.mark.asyncio
async def test_same_question_key_entries_refuse_answer_skip_and_bank(
        monkeypatch, tmp_path, mutate, twin):
    """Grants, bank and SKIP records are question-keyed: two same-title entries
    (two Yes/No pairs included) are indistinguishable there, so both refuse."""
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path, mutate=mutate)
    twins = [c for c in endpoint.current['controls'] if c.get('question') == twin
             and c.get('type') != 'submit']
    assert len(twins) >= 2
    with pytest.raises(forms.Gate, match='share this question') as refusal:
        forms._key(endpoint.current, twins[0])
    assert refusal.value.reason == 'defect_receiver'
    facts = {}
    for _ in range(12):
        gate = await _apply(pdf, dict(facts))
        if gate.get('reason') != 'owner_question':
            break
        q = gate['form_question']
        assert q['question'] != twin, 'a colliding key must never be owner-asked'
        _answer(row, stores, gate, 'No' if q['type'] == 'button' else 'Synthetic answer')
        facts[q['label']] = 'Synthetic answer'
    assert gate['reason'] == 'defect_receiver', gate
    assert twin not in (row.get('human_approved_answers') or {})
    assert twin not in stores.answer_bank.values
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'upload', 'select'}]


def test_location_toggle_shares_its_title_but_is_no_collision():
    page, _ = _live_dom()
    location = _by_locator(page, _LOCATION)
    assert forms._mismatch_question_twins(page, location) == []
    yes = _by_locator(page, _YES)
    assert forms._mismatch_question_twins(page, yes) == []


def test_one_radio_group_is_one_receiver_but_two_groups_on_one_question_collide(
        monkeypatch):
    """Issue #202 AC7: members of one validated radio group are one logical
    receiver; a second group sharing that question key still fails closed."""
    monkeypatch.setattr(forms, '_owner_asks_every_receiver', lambda page: True)
    monkeypatch.setattr(forms, '_formless_root', lambda page: {})

    def radio(selector, name, value):
        return dict(selector=selector, tag='input', type='radio', name=name,
                    form_selector='#synthetic-form', form_action='', in_form=True,
                    question='Synthetic choice', label=value.title(), value=value)

    one = [radio('#a-yes', 'a', 'yes'), radio('#a-no', 'a', 'no')]
    page = {'controls': one}
    assert forms._mismatch_question_twins(page, one[0]) == []
    assert forms._key(page, one[0]) == 'Synthetic choice'
    other = [radio('#b-yes', 'b', 'yes'), radio('#b-no', 'b', 'no')]
    page = {'controls': one + other}
    assert {c['selector'] for c in forms._mismatch_question_twins(page, one[0])} == {
        '#b-yes', '#b-no'}
    with pytest.raises(forms.Gate) as refused:
        forms._key(page, one[0])
    assert refused.value.reason == 'defect_receiver'


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
@pytest.mark.parametrize('mutate', ['', _CAPTION, _QUESTION], ids=['same', 'captions', 'question'])
async def test_rootless_grant_reuse_is_exact_shape_only_after_a_reload(
        monkeypatch, tmp_path, mutate):
    row, endpoint, decision, stores, pdf = _live_census(monkeypatch, tmp_path)
    facts, asked = await _boolean_granted(row, endpoint, stores, pdf)
    granted = deepcopy(row['human_approved_answers'][asked['label']])
    # A fresh real-reader census: every element is new, coordinates unchanged.
    endpoint.current, endpoint.fixed = _live_dom(mutate=mutate)
    before = len(endpoint.calls)
    gate = await _apply(pdf, dict(facts))
    again = (gate.get('reason') == 'owner_question'
             and gate['form_question']['selector'] in {
                 _by_locator(endpoint.current, s)['selector'] for s in (_YES, _NO)})
    if not mutate:
        # Exact URL, question, tag, type and Boolean members: the same answer.
        assert not again, gate
    else:
        # A changed caption or question voids the old grant: ask again.
        assert again, gate
        assert gate['form_question']['question'] != asked['question'] or [
            m['caption'] for m in gate['form_question']['boolean_members']] != [
            m['caption'] for m in granted['boolean_members']]
        assert not [c for c in endpoint.calls[before:] if c[0] == 'click']
    # The final Submit guard is never reached through a reused grant.
    assert gate.get('status') == 'gate'
    assert not [c for c in endpoint.calls if c[0] == 'click'
                and c[1] == '#structural-submit']
