"""Issue #196: production apply() owner gates on an offline rootless Ashby route.

The PAGE and fixed witness come from the same synthetic DOM readers used by
test_browser_formless. No real browser, user profile, submission, or network.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import run
from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, posting, entry
from tests.tools.test_browser_formless import root_world
from tests.tools.test_browser_controller_support import install_controller_session


def _fixture(monkeypatch, tmp_path):
    row, endpoint, decision, _ = root_world(monkeypatch, missingPane=True)
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
                           native_type=kind if tag in {'input', 'button'} else '',
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

    blocked = await _apply(pdf, {'Location': 'Remote', 'Start date': '2026-11-10'})
    assert blocked['reason'] == 'defect_button'
    assert blocked['review_route'] == 'hard_refusal'
    assert blocked['form_question'] is None
    assert 'agent_unavailable' not in str(blocked)
    assert not decision.await_count
    assert set(row['human_approved_answers']) == {'Location', 'Start date'}
    assert not [c for c in endpoint.calls if c[0] in {'fill', 'click', 'upload', 'select'}]

    # Certification is still owned by the human. This synthetic Boolean
    # button pair cannot be written and is therefore excluded from this
    # independent *grant* check; it never becomes an automatic yes.
    for snapshot in (endpoint.current, endpoint.fixed):
        for c in snapshot['controls']:
            if c['selector'] in {'#boolean-yes', '#boolean-no'}:
                c['required'] = False
    certification = await _apply(
        pdf, {'Location': 'Remote', 'Start date': '2026-11-10'})
    assert certification['form_question']['type'] == 'combobox'
    assert 'certify' in certification['form_question']['label']
    _answer(row, stores, certification, 'I certify')
    assert 'I certify that these synthetic statements are true' not in stores.answer_bank.values
    assert set(row['human_approved_answers']) == {
        'Location', 'Start date', 'I certify that these synthetic statements are true'}


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
