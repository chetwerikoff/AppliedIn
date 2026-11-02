"""Offline native select migration: preparation never grants Submit authority.

No browser, network, job data or Submit call. Only actual DOM readback,
not a stale fact/approval history, can satisfy the final required field.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms


URL = 'http://127.0.0.1/fixture/example-co/job/1'
PK = 'example-co#40'
QUESTION = 'Where will you work?'
OPTIONS = [{'value': 'remote', 'label': 'Remote'},
           {'value': 'onsite', 'label': 'On-site'}]


def page_with_native_select():
    select = {
        'selector': '#arrangement', 'label': 'Work arrangement',
        'question': QUESTION, 'tag': 'select', 'type': 'select',
        'required': True, 'disabled': False, 'value': '', 'has_value': False,
        'in_form': True, 'form_selector': '#application', 'form_action': URL,
        'native_form_owner_selector': '#application', 'formaction': '',
        'submit': False, 'checked': False, 'options': deepcopy(OPTIONS),
    }
    resume = {
        **select, 'selector': '#resume', 'label': 'Resume', 'question': '',
        'tag': 'input', 'type': 'file', 'required': False, 'value': '',
        'has_value': True, 'files': ['Resume.pdf'], 'options': [],
    }
    submit = {
        **select, 'selector': '#submit', 'label': 'Submit application',
        'question': '', 'tag': 'button', 'type': 'submit',
        'required': False, 'submit': True, 'options': [],
    }
    return {'url': URL, 'text': 'Synthetic application form',
            'controls': [select, resume, submit], 'inventory_verified': True,
            'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}


class SelectSession:
    def __init__(self, page):
        self.current = page
        self.calls = []

    async def page(self):
        return deepcopy(self.current)

    async def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        assert args[:3] == ('select', '#arrangement', '--value')
        assert not kwargs
        assert args[3] in {'remote', 'onsite'}
        self.current['controls'][0]['value'] = args[3]
        self.current['controls'][0]['has_value'] = True
        return {}


async def select_attempt(answer, *, has_fact=True):
    page = page_with_native_select()
    session = SelectSession(page)
    filled = {}
    action = {'action': 'select', 'selector': '#arrangement', 'fact': QUESTION}
    if not has_fact:
        with pytest.raises(forms.Gate) as refusal:
            await forms.execute(
                session, page, action, facts={}, filled=filled, resume_path='',
                company='example-co', jd_text='', resume_tex='', github='', pk=PK,
                job_url=URL)
        assert refusal.value.reason == 'owner_question'
        return session, filled, refusal.value
    await forms.execute(
        session, page, action, facts={QUESTION: answer}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL)
    return session, filled, None


async def test_unmatched_option_is_not_written_or_accepted_at_final_readback():
    # An existing fact that is not an available option can be considered
    # during preparation but cannot satisfy a required select at Submit.
    session, filled, _ = await select_attempt('Hybrid')
    assert session.calls == []
    assert filled == {'#arrangement': 'Hybrid'}
    assert session.current['controls'][0]['value'] == ''
    with pytest.raises(forms.Gate, match='required application field is empty'):
        forms.check_form(await session.page(), filled, True, 'Resume.pdf')


async def test_missing_select_fact_retains_observed_owner_question_and_options():
    session, filled, refusal = await select_attempt('', has_fact=False)
    assert session.calls == [] and filled == {}
    assert refusal.form_question['selector'] == '#arrangement'
    assert refusal.form_question['question'] == QUESTION
    assert refusal.form_question['options'] == OPTIONS


async def test_real_matched_native_option_survives_final_readback():
    session, filled, _ = await select_attempt('Remote')
    assert session.calls == [
        (('select', '#arrangement', '--value', 'remote'), {})]
    assert filled == {'#arrangement': 'remote'}
    forms.check_form(await session.page(), filled, True, 'Resume.pdf')
