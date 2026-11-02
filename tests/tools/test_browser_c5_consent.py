"""Synthetic consent owner-question routing and surviving account boundary.

This suite no longer asserts removed bank grants or per-widget option witnesses.
"""
from __future__ import annotations

import pytest

from tools import browser_skill_apply as forms



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


class _SyntheticSession:
    def __init__(self):
        self.calls = []

    async def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {}


def _choice_args():
    return {
        'facts': {}, 'filled': {}, 'resume_path': '',
        'company': 'example-co', 'jd_text': 'Synthetic job',
        'resume_tex': '', 'github': '', 'pk': 'example-co#1',
        'job_url': 'https://example.test/application',
    }


@pytest.mark.asyncio
async def test_missing_promotional_choice_asks_owner_without_a_grant():
    target = _checkbox('I agree to receive promotional emails')
    page = {'url': 'https://example.test/application', 'controls': [target]}
    session = _SyntheticSession()
    with pytest.raises(forms.Gate) as error:
        await forms.execute(session, page, {
            'action': 'choose', 'selector': '#agree',
        }, **_choice_args())
    assert error.value.reason == 'owner_question'
    assert error.value.form_question['selector'] == '#agree'
    assert session.calls == []


@pytest.mark.asyncio
async def test_account_purpose_choice_cannot_automate_subscription():
    target = _checkbox('I subscribe to recruitment marketing')
    page = {'url': 'https://example.test/application', 'controls': [target]}
    session = _SyntheticSession()
    with pytest.raises(forms.Gate, match='owner account action') as error:
        await forms.execute(session, page, {
            'action': 'choose', 'selector': '#agree', 'value': 'Yes',
        }, **_choice_args())
    assert error.value.reason != 'owner_question'
    assert session.calls == []
