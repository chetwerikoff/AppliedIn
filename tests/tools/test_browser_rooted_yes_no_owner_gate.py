"""Issue #283: a required Yes/No button pair on a root-proven Ashby page.

Sanitized live shape: the page is rooted (not rootless), the certification is a
hidden native checkbox, and the required unanswered controls are Ashby Yes/No
`button[type=button]` pairs. The required-owner gate used to refuse them with
`defect_button` because only rootless pages admitted buttons. Synthetic records
only; no network, browser or owner data.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_formless import (
    COMPANY, PK, install_controller_session, observed, posting, root_world)

QUESTION = 'Synthetic Boolean question'


def _relax(raw, mutate=None):
    """Isolate the pair: every other receiver is not required."""
    for snap in (raw.current, raw.fixed):
        for c in snap['controls']:
            if c['type'] != 'file' and c.get('label') not in ('Yes', 'No'):
                c.update(required=False)
        for e in snap['entry']['root_evidence']:
            if e.get('type') != 'file' and not e.get('yes_no'):
                e.update(required=False)
        if mutate:
            mutate(snap)


async def _run(monkeypatch, tmp_path, *, mutate=None, grant=None):
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 2)
    row, raw, decide, _ = root_world(monkeypatch, yesNo=True)
    assert forms._formless_root(raw.current)['root']  # rooted, never rootless
    _relax(raw, mutate)
    if grant:
        member = next(c for c in raw.current['controls'] if c.get('label') == 'Yes')
        bound = forms._bound_target(raw.current, member)
        row['human_approved_answers'] = {forms._key(raw.current, member): {
            'value': grant, 'url': raw.current['url'],
            **{k: bound.get(k) for k in (
                'selector', 'label', 'question', 'tag', 'type',
                'form_selector', 'form_action')},
            **({'boolean_members': bound['boolean_members']}
               if 'boolean_members' in bound else {})}}
    transport, _, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    file = next(c for c in raw.current['controls']
                if c['type'] == 'file' and c['label'] == 'Resume')
    submit = next(c for c in raw.current['controls']
                  if c['label'] == 'Submit application')
    original_call = raw.call

    async def call(method, *args, **kwargs):
        if method == 'upload':
            raw.calls.append((method, *args))
            name = Path(args[2]).name
            raw.current, raw.fixed = observed(
                attached=True, files=[name], displayName=name, yesNo=True)
            _relax(raw, mutate)
            return {}
        return await original_call(method, *args, **kwargs)
    raw.call = call
    decide.side_effect = [dict(action='upload', selector=file['selector']),
                          dict(action='submit', selector=submit['selector'])]
    result = await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    return raw, result, transport


def _clicks(raw):
    return [c for c in raw.calls if c[0] == 'click']


@pytest.mark.asyncio
async def test_rooted_required_yes_no_pair_raises_a_tokened_owner_question(
        monkeypatch, tmp_path):
    raw, result, transport = await _run(monkeypatch, tmp_path)
    assert result['status'] == 'gate', result
    assert result['reason'] == 'owner_question', result
    question = result['form_question']
    assert question['label'] == QUESTION or QUESTION in str(result['question'])
    members = {c['selector'] for c in raw.current['controls']
               if c.get('label') in ('Yes', 'No')}
    assert question['selector'] in members
    assert not _clicks(raw)  # asked, never answered or inferred
    assert not transport._application_controller.committed


def _drop_no(snap):
    no = next(c['selector'] for c in snap['controls'] if c.get('label') == 'No')
    snap['controls'] = [c for c in snap['controls'] if c['selector'] != no]
    snap['entry']['root_evidence'] = [
        e for e in snap['entry']['root_evidence'] if e['selector'] != no]


def _submit_native_type(snap):
    for c in snap['controls']:
        if c.get('label') in ('Yes', 'No'):
            c.update(native_type='button')


@pytest.mark.asyncio
@pytest.mark.parametrize('mutate', [_drop_no, _submit_native_type],
                         ids=['lone-member', 'pair-contradicts-census'])
async def test_rooted_button_that_is_not_a_proven_pair_keeps_defect_button(
        monkeypatch, tmp_path, mutate):
    raw, result, _ = await _run(monkeypatch, tmp_path, mutate=mutate)
    assert result['status'] == 'gate', result
    assert result['reason'] != 'owner_question', result
    assert not _clicks(raw)


@pytest.mark.asyncio
async def test_rooted_yes_no_pair_with_a_current_grant_is_not_asked_again(
        monkeypatch, tmp_path):
    raw, result, _ = await _run(monkeypatch, tmp_path, grant='Yes')
    assert result.get('reason') != 'defect_button'
    assert not (result.get('reason') == 'owner_question'
                and result['form_question'].get('label') == QUESTION)
