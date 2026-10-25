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
from tests.tools.test_browser_application_entry import COMPANY, PK, posting, entry
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


def _answer(row, stores, gate, value):
    assert gate['status'] == 'gate'
    assert gate['reason'] == 'owner_question'
    assert gate['review_route'] == 'owner'
    q = gate['form_question']
    assert q['pk'] == PK and q['url'] == entry('ashby')
    assert q['owner_kind'] == 'ashby-reversible-entry'
    assert q['form_selector'] == '' and q['form_action'] == ''
    row.update(status='needs_human', gate_source='applier',
               gate_call_id='direct', gate_reason='owner_question',
               gate_pending={'question': gate['question'],
                             'form_question': deepcopy(q)})
    assert run._direct_form_label(PK, row) == q['label']
    assert run.resume_job(PK, value, stores)['result'] == 'queued'
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
