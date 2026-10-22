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
    """Actual guarded raw click, then independently observed Greenhouse outcome.

    The offline BSK transport and in-memory tracking are synthetic. The real
    Session.application_effect -> ApplicationIPCController is not replaced;
    final guard, durable hold ordering and post-click reads run as production.
    """
    from types import SimpleNamespace
    from core.models import Status
    from tools import browser_skill as bsk
    from tools import submit_hold, posting_guard
    from tools import browser_apply
    from core import stores
    from core import apply_queue
    from agent import run

    actual_session = bsk.Session
    current = inventory(
        field('Resume', '#resume', required=False, type='file', form_action=GH_DIRECT),
        field(required=False, form_action=GH_DIRECT), submit(form_action=GH_DIRECT),
        url=GH_DIRECT)
    current['title'] = 'Synthetic Greenhouse role'
    if after_kind == 'preexisting-confirmation':
        current['text'] += ' Thank you for applying'
    browser, decide, row = _greenhouse_session(monkeypatch, tmp_path, current)
    row.update(title=current['title'], resume_seed='synthetic-seed')
    browser.allow_mutations = True
    decide.side_effect = [
        {'action': 'upload', 'selector': '#resume'},
        {'action': 'submit', 'selector': '#submit'},
    ]

    pdf = tmp_path / 'Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\\nsynthetic isolated PDF bytes\\n')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda _row: str(pdf))
    monkeypatch.setattr(run, 'seed_fingerprint', lambda: 'synthetic-seed')
    monkeypatch.setattr('tools.claude_chrome._stage_resume',
                        lambda path, owner: str(pdf))
    marker = {'held': False}
    class Tracking:
        r = object()
        def get(self, pk):
            assert pk == GH_PK
            return dict(row)
        def get_current(self, pk):
            assert pk == GH_PK
            return dict(row)
        def update_if_status(self, pk, status, updates, **_kwargs):
            assert pk == GH_PK
            if row['status'] != getattr(status, 'value', status):
                return False
            row.update({k: getattr(v, 'value', v) for k, v in updates.items()})
            return True
    tracking = Tracking()
    monkeypatch.setattr(stores, 'make_stores', lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(posting_guard, 'authorised', lambda checked: checked.get('pk') == GH_PK)
    monkeypatch.setattr(browser_apply, '_duplicate_refusal', lambda pk: False)
    monkeypatch.setattr(submit_hold, 'blocked', lambda *_a, **_kw: False)
    monkeypatch.setattr(submit_hold, 'mark', lambda *_a, **_kw: marker.update(held=True))
    monkeypatch.setattr(submit_hold, 'is_held', lambda *_a, **_kw: marker['held'])
    class Queue:
        def __init__(self, backend):
            assert backend is tracking.r
        def in_flight(self):
            return {GH_PK}
        def depth(self):
            return {'running': {'example-co'}}
    monkeypatch.setattr(apply_queue, 'ApplyQueue', Queue)

    after = inventory(url=GH_DIRECT)
    after['title'] = current['title']
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

    underlying = browser.call
    clicked = False
    raw_effects = []
    async def command(*args, **kwargs):
        nonlocal clicked
        assert args[-2:] == ('--session', 'synthetic-offline')
        op = args[:-2]
        if op == ('evaluate', bsk.PAGE):
            # The only unscoped read available on the raw Session stays
            # observational, even when the Greenhouse form has disappeared.
            browser.calls.append(op)
            return {'value': deepcopy(after if clicked else browser.current)}
        if clicked and op == ('evaluate', forms.GREENHOUSE_PAGE):
            browser.calls.append(op)
            return {'value': deepcopy(after)}
        if clicked and op == ('evaluate', forms._GREENHOUSE_FIXED_CONTROL_WITNESS):
            browser.calls.append(op)
            return {'value': {
                'url': after['url'], 'greenhouse': deepcopy(after['greenhouse']),
                'controls': [],
            }}
        if op == ('click', '#submit'):
            assert marker['held'] and row['status'] == 'needs_human'
            raw_effects.append(op)
            browser.calls.append(op)
            clicked = True
            if after_kind == 'revoked-provenance':
                row['job_id'] = '654321'
            return {}
        if op and op[0] in {'navigate', 'upload'}:
            raw_effects.append(op)
        return await underlying(*op, **kwargs)

    monkeypatch.setattr(bsk, 'command', command)

    class OfflineSession(actual_session):
        async def __aenter__(self):
            self.id = 'synthetic-offline'
            return self
        async def __aexit__(self, *args):
            self._application_controller.release_snapshot()
            return False

    monkeypatch.setattr(bsk, 'Session', OfflineSession)
    monkeypatch.setattr('core.events.emit', lambda *a, **kw: None)
    monkeypatch.setattr(forms.asyncio, 'sleep', AsyncMock())
    result = await forms.apply(GH_WRAP, 'example-co', {}, 'synthetic',
                               pk=GH_PK, resume_path=str(pdf))
    assert [op[0] for op in raw_effects] == [
        'navigate', 'upload', 'click'], (raw_effects, result)
    assert raw_effects[0] == ('navigate', GH_DIRECT)
    assert raw_effects[-1] == ('click', '#submit')
    assert marker['held'] and clicked and row['status'] == 'needs_human'
    assert decide.await_count == 2
    assert not row.get('possible_submission')
    if after_kind == 'revoked-provenance':
        assert row['job_id'] == '654321'
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
