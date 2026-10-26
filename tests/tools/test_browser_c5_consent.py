"""Issue #208 (C5): consent stems, promotional owner routing, linkage predicates.

Offline only: synthetic control dicts and the inspection-only bootstrap fakes.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import (
    field, secure_attempt, secure_inventory, secure_world)


def _text(label):
    return {
        'selector': '#f', 'label': label, 'question': '', 'tag': 'input',
        'type': 'email' if label == 'Email' else 'text', 'required': True,
        'disabled': False, 'value': '', 'has_value': False, 'in_form': True,
        'form_selector': '#application', 'form_action': 'https://example.test/apply',
    }


def _checkbox(question, *, required=True):
    return {
        'selector': '#agree', 'question': question, 'label': '', 'tag': 'input',
        'type': 'checkbox', 'required': required, 'checked': False,
        'disabled': False, 'in_form': True, 'form_selector': '#application',
        'form_action': 'https://example.test/application', 'value': 'on',
    }


# AC1 (D54, D55)
@pytest.mark.parametrize('label', [
    'Declaration of accuracy', 'I declare the above',
    'Professional certification number', 'Certified translator ID',
    'Attestation of eligibility', 'Attested by',
    'User agreement version', 'Agreed start date',
])
def test_bank_exclusion_stems_cover_every_inflection(label):
    assert not forms._bank_auto_eligible(_text(label), 'synthetic answer')


def test_bank_exclusion_stems_keep_ordinary_fact_bankable():
    assert forms._bank_auto_eligible(_text('Email'), 'synthetic@example.test')


# AC2 (D36)
@pytest.mark.parametrize('question', [
    'I agree to receive promotional emails',
    'I subscribe to recruitment marketing',
])
def test_promotional_consent_without_privacy_words_is_owner_routed(question):
    target = _checkbox(question)
    assert forms._assent_scope(target) == 'owner'
    assert forms._missing_answer_reason(target) == 'owner_question'


# AC3
@pytest.mark.parametrize('question,expected', [
    ('I acknowledge processing of personal data for this application',
     'standard_privacy'),
    ('I consent to processing personal data for recruitment and marketing',
     'owner'),
    ('I consent to processing personal data for application and sharing with partners',
     'owner'),
    ('I accept a privacy policy', 'owner'),
    ('I certify all statements are correct', 'owner'),
    ('I am legally authorized to work', 'fact'),
    ('I agree to arbitration', 'owner'),
    ('Newsletter', 'fact'),
])
def test_existing_assent_scopes_are_not_reclassified(question, expected):
    assert forms._assent_scope(_checkbox(question)) == expected


def test_marketing_experience_question_stays_fact():
    target = _text('Years of marketing experience')
    assert forms._assent_scope(target) == 'fact'
    assert forms._missing_answer_reason(target) == 'agent_review_unavailable'


def test_promotional_checkbox_without_grant_fails_closed(monkeypatch):
    target = _checkbox('I agree to receive promotional emails')
    page = {'url': 'https://example.test/application', 'controls': [target]}
    monkeypatch.setattr(forms, '_row', lambda pk, **kw: {
        'pk': 'example-co#1', 'status': 'submitting', 'human_approved_answers': {}})
    monkeypatch.setattr(forms, '_require_native_choice', lambda t: None)
    monkeypatch.setattr(forms, '_unanswered', lambda *a, **kw: True)
    with pytest.raises(forms.Gate) as raised:
        forms._approved_native_choice(page, target, 'example-co#1')
    assert raised.value.reason == 'owner_question'


# AC4 (D72)
def _linked(options=None, **combo):
    return {
        'selector': '#privacy', 'type': 'combobox', 'tag': 'input',
        'combobox': {
            'linked': True, 'link_id': 'privacy-list',
            'listbox_selector': '#privacy-list',
            'options': options if options is not None else [
                {'selector': '#o1', 'label': 'Yes', 'value': 'yes', 'unique': True},
                {'selector': '#o2', 'label': 'No', 'value': 'no', 'unique': True}],
            **combo,
        },
    }


def _option_case(predicate):
    target = _linked()
    combo, options = target['combobox'], target['combobox']['options']
    if predicate == 'combobox.type':
        target['type'] = 'select'
    elif predicate == 'combobox.linked':
        combo['linked'] = False
    elif predicate == 'combobox.link_id':
        combo['link_id'] = ''
    elif predicate == 'combobox.listbox_selector':
        combo['listbox_selector'] = None
    elif predicate == 'options.list':
        combo['options'] = []
    elif predicate == 'option.object':
        options[1] = 'No'
    elif predicate == 'option.unique':
        options[1]['unique'] = False
    elif predicate == 'option.selector':
        options[1]['selector'] = ' '
    elif predicate == 'option.label':
        options[1]['label'] = ''
    elif predicate == 'option.value':
        options[1]['value'] = None
    elif predicate == 'options.selector_unique':
        options[1]['selector'] = '#o1'
    elif predicate == 'options.label_unique':
        options[1]['label'] = 'yes'
    elif predicate == 'hidden.selector':
        combo['hidden_selector'] = ['#hidden']
    elif predicate == 'hidden.value':
        combo.update(hidden_selector='#hidden', hidden_value=None)
    return target


@pytest.mark.parametrize('predicate', sorted(
    forms._OPTION_LINKAGE_PREDICATES - {'witness.never_linked'}))
def test_option_linkage_gate_names_exact_predicate(predicate):
    with pytest.raises(forms.Gate) as raised:
        forms._combobox_options(_option_case(predicate))
    assert str(raised.value) == 'The combobox option linkage is incomplete or ambiguous.'
    assert forms._option_linkage_predicate(raised.value) == predicate


def test_valid_linked_combobox_still_returns_options():
    target = _linked()
    assert forms._combobox_options(target) == target['combobox']['options']


def test_disclosure_detail_option_predicate_is_fixed_vocabulary():
    target = _linked()
    plain = forms._material_witness_disclosure_detail(target, attempted=False)
    assert 'option_predicate=' not in plain
    named = forms._material_witness_disclosure_detail(
        target, attempted=False, option_predicate='options.label_unique')
    assert named == plain + ',option_predicate=options.label_unique'
    unsafe = forms._material_witness_disclosure_detail(
        target, attempted=False, option_predicate='#secret selector')
    assert unsafe.endswith(',option_predicate=unspecified')
    assert forms._option_linkage_predicate(forms.Gate(
        'The combobox option linkage is incomplete or ambiguous.')) is None


# AC5 (D72)
async def test_material_assent_disclosure_names_duplicate_label_predicate(monkeypatch):
    privacy = field(
        'Please Acknowledge receipt of our Candidate Privacy Notice.',
        '#privacy', tag='input', type='combobox',
        combobox={'linked': True, 'link_id': 'privacy-list',
                  'listbox_selector': '#privacy-list',
                  'options': [
                      {'selector': '#o1', 'label': 'Yes', 'value': 'yes', 'unique': True},
                      {'selector': '#o2', 'label': 'yes', 'value': 'y', 'unique': True}],
                  'selected_text': '', 'displayed_text': '', 'selected_value': '',
                  'hidden_selector': '', 'hidden_value': ''})
    page = secure_inventory(deepcopy(privacy))
    browser, decide, _ = secure_world(monkeypatch, page)
    report = await secure_attempt(page, facts={
        'PRIVATE_FACT_SENTINEL': 'PRIVATE_VALUE_SENTINEL'})
    assert report['status'] == 'gate'
    assert report['material_assent_diagnostic'] is True
    assert report['form_question'] is None
    assert ('Disclosure: The combobox option linkage is incomplete or ambiguous.'
            in report['question'])
    assert 'option_predicate=options.label_unique' in report['question']
    assert decide.await_count == 0
    for secret in ('PRIVATE_FACT_SENTINEL', 'PRIVATE_VALUE_SENTINEL', '#o1', '#o2'):
        assert secret not in report['question']
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)
