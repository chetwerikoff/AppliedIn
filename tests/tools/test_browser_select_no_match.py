"""Offline regression: an unmatched native select re-asks on its first refusal.

The fake PAGE inventory and independent DOM witness have exact form and option
identity. No browser, network, owner data, or application action is involved.
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
        'native_form_owner_selector': '#application',
        'formaction': '', 'submit': False, 'checked': False,
        'options': deepcopy(OPTIONS),
    }
    submit = {**select, 'selector': '#submit', 'label': 'Submit application',
              'question': '', 'tag': 'button', 'type': 'submit',
              'required': False, 'submit': True, 'options': []}
    return {'url': URL, 'text': 'Synthetic application form',
            'controls': [select, submit], 'inventory_verified': True,
            'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}


def approved_grant(select, answer):
    grant = {key: select[key] for key in (
        'selector', 'label', 'question', 'tag', 'type',
        'form_selector', 'form_action')}
    grant.update(value=answer, url=URL, options=deepcopy(select['options']))
    return grant


class ReadOnlySession:
    def __init__(self, page):
        self.current = page
        self.calls = []

    async def page(self):
        return deepcopy(self.current)

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args != ('evaluate', forms._FIXED_CONTROL_WITNESS):
            pytest.fail('A select, fill, click, upload, or other mutation is forbidden')
        return {'value': {'url': self.current['url'], 'controls': [
            {'selector': c['selector'], 'unique': True, 'unique_form': True,
             'tag': c['tag'], 'type': c['type'], 'in_form': c['in_form'],
             'form_selector': c['form_selector'], 'form_action': c['form_action'],
             'native_form_owner_selector': c['native_form_owner_selector'],
             'formaction': c['formaction'], 'disabled': c['disabled'],
             'readonly': False, 'inert': False, 'visible': True,
             'visible_text': '', 'options': deepcopy(c['options'])}
            for c in self.current['controls']]}}


async def select_attempt(monkeypatch, answer, *, grant=True):
    page = page_with_native_select()
    grants = ({QUESTION: approved_grant(page['controls'][0], answer)}
              if grant else {})
    row = {'pk': PK, 'status': 'submitting',
           'possible_submission': False, 'human_approved_answers': grants}
    before = deepcopy(row)
    session = ReadOnlySession(page)
    filled = {}

    def read_row(pk):
        assert pk == PK
        return row

    monkeypatch.setattr(forms, '_row', read_row)
    action = {'action': 'select', 'selector': '#arrangement', 'fact': QUESTION}
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(
            session, page, action, facts={QUESTION: answer}, filled=filled,
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=URL)
    assert row == before  # Even an exact old grant is read-only on refusal.
    assert filled == {}
    assert [call[0] for call in session.calls] == ['evaluate']
    return exc.value


async def test_unmatched_granted_fact_reasks_with_original_question_and_all_options(
        monkeypatch):
    # Hybrid is an existing exact job grant, but it is not a DOM option.
    gate = await select_attempt(monkeypatch, 'Hybrid')
    assert str(gate) == (
        'Where will you work?\nWork arrangement\nOptions:\nRemote\nOn-site')
    assert gate.form_question == {
        'pk': PK, 'label': QUESTION, 'selector': '#arrangement', 'url': URL,
        'control_label': 'Work arrangement', 'question': QUESTION,
        'tag': 'select', 'type': 'select', 'form_selector': '#application',
        'form_action': URL, 'options': OPTIONS,
    }


async def test_bank_fact_without_exact_owner_grant_still_refuses(monkeypatch):
    gate = await select_attempt(monkeypatch, 'Hybrid', grant=False)
    assert 'An exact human-approved answer is needed' in str(gate)
    assert str(gate).endswith(
        'Where will you work?\nWork arrangement\nOptions:\nRemote\nOn-site')
    assert gate.form_question['options'] == OPTIONS


async def test_matched_option_guard_refusal_is_not_recast_as_choice_question(
        monkeypatch):
    # The chosen value matches a real option; a distinct safety guard must
    # retain its own refusal rather than becoming a grantable option prompt.
    original = forms.guarded_value

    def reject_caption(target, value):
        if value == 'Remote':
            raise forms.Gate('Synthetic option safety guard rejected the caption.')
        return original(target, value)

    monkeypatch.setattr(forms, 'guarded_value', reject_caption)
    gate = await select_attempt(monkeypatch, 'remote')
    assert str(gate) == 'Synthetic option safety guard rejected the caption.'
    assert gate.form_question is None
