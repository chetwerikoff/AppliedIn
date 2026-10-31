"""Synthetic consent owner-question routing and surviving account boundary.

This suite no longer asserts removed bank grants or per-widget option witnesses.
"""
from __future__ import annotations

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
