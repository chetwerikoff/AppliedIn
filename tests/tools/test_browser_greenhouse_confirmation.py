"""A missing form after a committing click is not a missing pre-action receiver."""
from copy import deepcopy
import inspect
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import (
    GH_DIRECT, GH_PK, GH_WRAP, _greenhouse_session, field, inventory, submit,
)


@pytest.mark.parametrize('after_kind', [
    'rootless-success', 'foreign-route', 'revoked-provenance', 'passive-frame',
    'unknown-frame', 'response-control', 'unsupported-frame', 'malformed-root',
    'no-confirmation', 'preexisting-confirmation',
])
async def test_greenhouse_post_submit_rootless_confirmation(monkeypatch, tmp_path, after_kind):
    current = inventory(
        field('Resume', '#resume', required=False, type='file', form_action=GH_DIRECT),
        field(required=False, form_action=GH_DIRECT), submit(form_action=GH_DIRECT), url=GH_DIRECT)
    if after_kind == 'preexisting-confirmation':
        current['text'] += ' Thank you for applying'
    browser, decide, row = _greenhouse_session(monkeypatch, tmp_path, current)
    browser.allow_mutations = True
    decide.side_effect = [
        {'action': 'upload', 'selector': '#resume'},
        {'action': 'submit', 'selector': '#submit'},
    ]
    after = inventory(url=GH_DIRECT)
    after['text'] = 'Thank you for applying' if after_kind != 'no-confirmation' else 'Processing'
    after['greenhouse'] = dict(root=None, frames=[], response_controls=False, badge_responses=0)
    if after_kind == 'foreign-route':
        after['url'] = 'https://job-boards.greenhouse.io/other-co/jobs/123456'
    elif after_kind in {'passive-frame', 'unknown-frame'}:
        after['greenhouse']['frames'] = [dict(status='R' if after_kind == 'passive-frame' else 'unknown')]
    elif after_kind == 'response-control':
        after['greenhouse']['response_controls'] = True
    elif after_kind == 'unsupported-frame':
        after['unsupported_frames'] = ['#untrusted']
    elif after_kind == 'malformed-root':
        after['greenhouse']['root'] = {}
    original_call = browser.call
    clicked = False

    async def call(*args, **kwargs):
        nonlocal clicked
        if args == ('click', '#submit'):
            browser.calls.append(args)
            clicked = True
            if after_kind == 'revoked-provenance':
                row['job_id'] = '654321'
            return {}
        if clicked and args == ('evaluate', forms.GREENHOUSE_PAGE):
            browser.calls.append(args)
            return {'value': deepcopy(after)}
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(browser, 'call', call)
    monkeypatch.setattr(forms, 'hold_possible_submission', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'check_before_committing_click', lambda *a, **kw: None)
    monkeypatch.setattr('core.events.emit', lambda *a, **kw: None)
    monkeypatch.setattr(forms.asyncio, 'sleep', AsyncMock())
    result = await forms.apply(GH_WRAP, 'example-co', {}, 'synthetic',
                               pk=GH_PK, resume_path='Resume.pdf')
    assert browser.calls.count(('click', '#submit')) == 1, result
    assert decide.await_count == 2
    if after_kind == 'rootless-success':
        assert result['status'] == 'applied', result
        assert result['confirmation'].lower() == 'thank you for applying'
    else:
        assert result['status'] == 'uncertain', result


@pytest.mark.parametrize('submitted', [False, True])
@pytest.mark.parametrize('frames', [[], [dict(status='R')]])
def test_rootless_observation_requires_commit_and_no_frames(submitted, frames):
    from tests.tools.test_browser_greenhouse_wait import ALIAS, page
    observed = page()
    observed['greenhouse'].update(root=None, frames=frames)
    allowed = lambda url: url == ALIAS.url
    if submitted and not frames:
        forms._greenhouse_safety(observed, allowed, ALIAS, submitted=submitted)
    else:
        with pytest.raises(forms.Gate):
            forms._greenhouse_safety(observed, allowed, ALIAS, submitted=submitted)


@pytest.mark.parametrize(('replacement', 'submitted', 'frames'), [
    ('not frames', False, []), ('submitted', True, [dict(status='R')]),
])
def test_removing_each_confirmation_boundary_admits_an_unsafe_read(replacement, submitted, frames):
    from tests.tools.test_browser_greenhouse_wait import ALIAS, page
    observed = page()
    observed['greenhouse'].update(root=None, frames=frames)
    allowed = lambda url: url == ALIAS.url
    with pytest.raises(forms.Gate):
        forms._greenhouse_safety(observed, allowed, ALIAS, submitted=submitted)
    # Remove one predicate only in memory: both commit timing and absence of
    # frames are independently necessary to avoid broadening pre-action trust.
    source = inspect.getsource(forms._greenhouse_safety)
    assert source.count('submitted and not frames') == 1
    namespace = dict(vars(forms))
    exec(source.replace('submitted and not frames', replacement), namespace)
    namespace['_greenhouse_safety'](observed, allowed, ALIAS, submitted=submitted)
