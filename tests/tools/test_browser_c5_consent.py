"""Issue #208 (C5): consent stems, promotional owner routing, linkage predicates.

Offline only: synthetic control dicts and the inspection-only bootstrap fakes.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import (
    field, secure_attempt, secure_inventory, secure_world)
from tests.tools import test_browser_combobox as combo_fx


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
def test_bank_exclusion_stems_cover_every_inflection(label, monkeypatch):
    # Neutralize the separate material-assent guard so the stem exclusion
    # itself decides (agreed/agreement/certified/certification match it on main).
    monkeypatch.setattr(forms, '_material_assent_match', lambda *_a, **_k: None)
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
])
def test_existing_assent_scopes_are_not_reclassified(question, expected):
    assert forms._assent_scope(_checkbox(question)) == expected


@pytest.mark.parametrize('question', [
    'Newsletter',
    'I agree that I have five years of marketing experience',
    'I agree to the processing of my marketing portfolio',
])
def test_non_opt_in_marketing_checkbox_stays_fact(question):
    assert forms._assent_scope(_checkbox(question)) == 'fact'


def test_noun_before_verb_split_is_a_pinned_false_negative():
    target = _checkbox('Marketing communications')
    target['label'] = 'I agree to receive emails'
    assert forms._assent_scope(target) == 'fact'


@pytest.mark.parametrize('label', [
    'Years of marketing experience',
    'Where do you receive marketing reports for your team?',
    'I agree to receive promotional emails',
])
def test_promotional_clause_never_reclassifies_text_fields(label):
    target = _text(label)
    assert forms._assent_scope(target) == 'fact'
    assert forms._missing_answer_reason(target) == 'agent_review_unavailable'


async def test_promotional_checkbox_routes_owner_end_to_end(monkeypatch):
    consent = field('I agree to receive promotional emails', '#promo',
                    tag='input', type='checkbox', value='on')
    page = secure_inventory(consent)
    browser, decide, _ = secure_world(monkeypatch, page)
    report = await secure_attempt(page)
    assert report['status'] == 'gate'
    assert report['reason'] == 'owner_question'
    assert report['review_route'] == 'owner'
    assert decide.await_count == 0
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)


async def test_subscribe_checkbox_keeps_existing_account_guard(monkeypatch):
    consent = field('I subscribe to recruitment marketing', '#promo',
                    tag='input', type='checkbox', value='on')
    page = secure_inventory(consent)
    browser, decide, _ = secure_world(monkeypatch, page)
    report = await secure_attempt(page)
    assert report['status'] == 'gate'
    assert report['reason'] != 'owner_question'
    # Known D36 residual: the unchanged account guard keeps this label agent-first.
    assert report['review_route'] == 'agent_unavailable'
    assert 'Login or account form requires human inspection.' in report['question']
    assert decide.await_count == 0
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)


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
    forms._OPTION_LINKAGE_PREDICATES
    - {'witness.never_linked', 'combobox.linked.shape'}))
def test_option_linkage_gate_names_exact_predicate(predicate):
    with pytest.raises(forms.Gate) as raised:
        forms._combobox_options(_option_case(predicate))
    assert str(raised.value) == 'The combobox option linkage is incomplete or ambiguous.'
    assert forms._option_linkage_predicate(raised.value) == predicate


def test_first_failing_predicate_follows_option_list_order():
    target = _linked()
    options = target['combobox']['options']
    options[0]['unique'] = False
    options[1] = 'No'
    with pytest.raises(forms.Gate) as raised:
        forms._combobox_options(target)
    assert forms._option_linkage_predicate(raised.value) == 'option.unique'


def test_option_linkage_vocabulary_is_exactly_sixteen_names():
    assert len(forms._OPTION_LINKAGE_PREDICATES) == 16
    assert {'witness.never_linked', 'combobox.linked.shape'} <= forms._OPTION_LINKAGE_PREDICATES
    target = _linked()
    for name in forms._OPTION_LINKAGE_PREDICATES:
        assert forms._material_witness_disclosure_detail(
            target, attempted=False, option_predicate=name
        ).endswith(',option_predicate=' + name)


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


def _closed_material():
    current = combo_fx.material_page()
    current['controls'][0]['combobox'].update(
        linked=False, options=[], link_id='', listbox_selector='')
    return current


class _NoEffects:
    def __init__(self, current):
        self.current = current

    async def page(self):
        return deepcopy(self.current)

    async def call(self, *args):
        if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            return {'value': combo_fx.evidence(self.current)}
        pytest.fail(f'no irreversible effects permitted: {args}')


@pytest.mark.parametrize('predicate', ['options.label_unique', 'witness.never_linked'])
async def test_witness_predicate_outranks_closed_original(monkeypatch, predicate):
    current = _closed_material()
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': combo_fx.PK, 'status': 'submitting', 'human_approved_answers': {}})

    async def rejected_witness(*args, **kwargs):
        raise forms._option_linkage_gate(predicate)

    monkeypatch.setattr(forms, '_witness_material_combobox', rejected_witness)
    with pytest.raises(forms.MaterialAssentGate) as exc:
        await forms._bootstrap(_NoEffects(current), current, combo_fx.PK, {}, combo_fx.URL)
    detail = str(exc.value)
    assert 'witness=attempted,linked=false,options=empty,' in detail
    assert ('witness_reason=The combobox option linkage is incomplete or ambiguous.'
            in detail)
    assert detail.endswith(f',option_predicate={predicate}.')
    assert 'option_predicate=combobox.linked,' not in detail
    assert exc.value.form_question is None


async def test_real_witness_open_with_duplicate_labels_names_predicate(monkeypatch):
    closed = _closed_material()
    opened = combo_fx.material_page()
    opened['controls'][0]['combobox']['options'][1]['label'] = 'i agree'
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': combo_fx.PK, 'status': 'submitting', 'human_approved_answers': {}})

    class Opening:
        def __init__(self):
            self.open, self.calls = False, []

        def current(self):
            return opened if self.open else closed

        async def page(self):
            return deepcopy(self.current())

        async def call(self, *args, **kwargs):
            self.calls.append(args)
            if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
                return {'value': combo_fx.evidence(self.current())}
            if args == ('click', '#country'):
                self.open = True
                return {}
            if args == ('press', 'Escape'):
                self.open = False
                return {}
            pytest.fail(f'Unexpected browser IPC: {args}')

    session = Opening()
    with pytest.raises(forms.MaterialAssentGate) as exc:
        await forms._bootstrap(session, deepcopy(closed), combo_fx.PK, {}, combo_fx.URL)
    detail = str(exc.value)
    assert 'witness=attempted' in detail
    assert detail.endswith(',option_predicate=options.label_unique.')
    assert exc.value.form_question is None
    assert [c for c in session.calls if c[0] != 'evaluate'] == [
        ('click', '#country'), ('press', 'Escape')]


async def test_malformed_link_prefilter_names_shape_predicate(monkeypatch):
    current = combo_fx.material_page()
    current['controls'][0]['combobox']['listbox_selector'] = None
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': combo_fx.PK, 'status': 'submitting', 'human_approved_answers': {}})
    with pytest.raises(forms.MaterialAssentGate) as exc:
        await forms._bootstrap(_NoEffects(current), current, combo_fx.PK, {}, combo_fx.URL)
    detail = str(exc.value)
    assert 'Disclosure: The combobox option linkage is incomplete or ambiguous.' in detail
    assert detail.endswith(',option_predicate=combobox.linked.shape.')
    assert exc.value.form_question is None


@pytest.mark.parametrize('message,expected', [
    ('The opened picker is still unlinked.', None),
    ('Control witness mismatch: predicate=combobox.linked.shape; selector=#x',
     'combobox.linked.shape'),
])
async def test_witness_without_option_predicate_never_blames_closed_original(
        monkeypatch, message, expected):
    current = _closed_material()
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': combo_fx.PK, 'status': 'submitting', 'human_approved_answers': {}})

    async def rejected_witness(*args, **kwargs):
        raise forms.Gate(message)

    monkeypatch.setattr(forms, '_witness_material_combobox', rejected_witness)
    with pytest.raises(forms.MaterialAssentGate) as exc:
        await forms._bootstrap(_NoEffects(current), current, combo_fx.PK, {}, combo_fx.URL)
    detail = str(exc.value)
    assert 'witness=attempted' in detail
    assert not detail.endswith(',option_predicate=combobox.linked.')
    if expected is None:
        assert 'option_predicate=' not in detail
    else:
        assert detail.endswith(f',option_predicate={expected}.')
    assert exc.value.form_question is None
