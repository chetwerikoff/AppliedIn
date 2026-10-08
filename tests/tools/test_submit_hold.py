"""A stale full-row tracking write must never authorize another application."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest

import daemon
import server
from agent import run
from core.apply_queue import ApplyQueue
from core.models import Status
from core.storage.local import RedisTracking
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import submit_hold

PK = 'example-co#synthetic-job'
URL = 'https://example.test/job/1'


@pytest.fixture
def world(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    tracking.set_status(PK, Status.SUBMITTING, company='example-co', jd_url=URL)
    stores = SimpleNamespace(tracking=tracking, apply_queue='apply',
                             queue=SimpleNamespace(enqueue=AsyncMock()))
    monkeypatch.setattr('core.stores.make_stores', lambda *a, **kw: stores)
    monkeypatch.setattr(run, 'make_stores', lambda *a, **kw: stores)
    monkeypatch.setattr(server, 'make_stores', lambda *a, **kw: stores)
    monkeypatch.setattr(bsk, 'applies_running', lambda: 0)
    return stores, ApplyQueue(client)


def test_mark_uses_tracking_client_without_ttl(world):
    stores, _ = world
    submit_hold.mark(PK, tracking=stores.tracking)
    client = stores.tracking.r
    value = json.loads(client.get(f'submit_hold:{PK}'))
    assert value['timestamp'] and value['reason'] == submit_hold.REASON
    assert client.ttl(f'submit_hold:{PK}') == -1
    assert submit_hold.is_held(PK, tracking=stores.tracking)


@pytest.mark.parametrize('recovery', ['startup', 'periodic', 'server'])
@pytest.mark.parametrize('held', [False, True])
def test_stale_full_row_interleaving_recovery_and_replay(world, monkeypatch, recovery, held):
    stores, q = world
    tracking = stores.tracking
    stale = tracking.get(PK)
    if held:
        forms.hold_possible_submission(PK, {'last_button': 'Submit', 'url': URL})
        # Even AFTER recording unresolved uncertainty, its independent key stays.
        tracking.set_status(PK, Status.NEEDS_HUMAN, fail_kind='uncertain')
        tracking._write(PK, stale, prev_status='needs_human')
        assert tracking.get(PK) == stale
        assert submit_hold.is_held(PK, tracking=tracking)
    enqueued = []
    stores.queue.enqueue = lambda *args: enqueued.append(args)
    if recovery == 'startup':
        daemon._recover_orphans(stores)
    elif recovery == 'periodic':
        monkeypatch.setattr(daemon, '_LAST_RECLAIM', [-1e9])
        daemon._reclaim_orphans(stores, q)
    else:
        server._recover_stuck(None)
    row = tracking.get(PK)
    assert row['status'] == ('needs_human' if held else 'tailored')
    if held:
        assert row['fail_kind'] == 'uncertain'
        assert row['fail_reason'] == submit_hold.REASON
        assert not enqueued and not q.pending()
        # Another stale writer can erase the recovery row too, but not the key.
        tracking._write(PK, {**stale, 'status': 'found'}, prev_status='needs_human')
        assert not q.put(PK, 'example-co')
        assert not q.retry({'pk': PK, 'company': 'example-co'}, 'timeout')
        assert run.run_job(PK, stores)['reason'] == 'uncertain'
        assert run.retry_job(PK, stores)['reason'] == 'uncertain'
        assert run._enqueue_apply(PK, stores)['reason'] == 'uncertain'
    elif recovery == 'startup':
        assert len(enqueued) == 1
    elif recovery == 'periodic':
        assert len(q.pending()) == 1


async def test_stale_row_cannot_bypass_shared_dispatch_or_worker_exception(world, monkeypatch):
    stores, q = world
    submit_hold.mark(PK, tracking=stores.tracking)
    stores.tracking.set_status(PK, Status.TAILORED, fail_kind='', gate_reason='')
    assert (await run._apply_direct(PK, stores))['reason'] == 'uncertain'
    monkeypatch.setattr(run, '_run', lambda coro: (coro.close(), (_ for _ in ()).throw(RuntimeError('synthetic crash')))[1])
    item = {'pk': PK, 'company': 'example-co'}
    assert run.run_queued(item, q)['reason'] == 'uncertain'
    assert not q.pending() and not q.dead_letters()
    assert submit_hold.is_held(PK, tracking=stores.tracking)


@pytest.mark.parametrize('failure', ['mark', 'readback', 'missing', 'cloud'])
@pytest.mark.parametrize('verb,button', [('submit', 'Submit application'), ('click', 'Continue')])
async def test_failed_mark_or_readback_means_gate_and_zero_clicks(world, monkeypatch, failure, verb, button):
    stores, _ = world
    current = {'url': URL, 'text': '', 'inventory_verified': True,
               'controls': [{'selector': '#resume', 'type': 'file', 'tag': 'input',
                             'label': 'Resume', 'files': ['Resume.pdf']},
                            {'selector': '#submit', 'type': 'submit', 'tag': 'button',
                             'label': button, 'submit': True}]}
    calls = []
    class Session:
        def __init__(self, kind):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, url):
            return current
        async def page(self):
            return current
        async def call(self, *args, **kwargs):
            calls.append(args[0])
            if args[0] == 'click':
                pytest.fail('Failed hold cannot reach browser IPC')
    if failure == 'cloud':
        original = stores.tracking
        stores.tracking = SimpleNamespace(get=original.get, set_status=original.set_status)
    elif failure == 'mark':
        monkeypatch.setattr(submit_hold, 'mark', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic SET failure')))
    else:
        def read(*a, **k):
            if failure == 'readback':
                raise RuntimeError('synthetic GET failure')
            return False
        monkeypatch.setattr(submit_hold, 'is_held', read)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a: None)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda *a: 'Resume.pdf')
    monkeypatch.setattr(bsk, 'Session', Session)
    monkeypatch.setattr(bsk, 'decision', AsyncMock(side_effect=[
        {'action': 'upload', 'selector': '#resume'}, {'action': verb, 'selector': '#submit'}]))
    result = await forms.apply(URL, 'example-co', {}, 'synthetic', pk=PK, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'no click is safe' in result['question']
    assert calls == ['upload']


@pytest.mark.parametrize('path', ['/actions/reopen/{pk:path}', '/actions/queue-apply/{pk}',
    '/actions/apply-now/{pk}', '/actions/retry/{pk}', '/actions/resume/{pk}',
    '/actions/force-apply/{pk:path}', '/actions/retailor/{pk:path}'])
def test_manual_replay_refuses_key_even_after_stale_row_overwrite(world, path):
    from fastapi import BackgroundTasks
    stores, _ = world
    submit_hold.mark(PK, tracking=stores.tracking)
    stores.tracking.set_status(PK, Status.TAILORED, fail_kind='', gate_reason='approval')
    endpoint = next(r.endpoint for r in server.create_app().routes if getattr(r, 'path', '') == path)
    args = {'pk': PK}
    import inspect
    if 'background' in inspect.signature(endpoint).parameters:
        args['background'] = BackgroundTasks()
    if 'body' in inspect.signature(endpoint).parameters:
        args['body'] = {}
    assert not endpoint(**args)['ok']
    assert submit_hold.is_held(PK, tracking=stores.tracking)


def test_reset_finds_key_even_without_tracking_row(world, monkeypatch):
    stores, _ = world
    submit_hold.mark(PK, tracking=stores.tracking)
    stores.tracking.r.delete(f'app:{PK}')
    monkeypatch.setattr('tools.browser_runtime.kill_live_sessions', lambda: pytest.fail('Reset reached browser effects'))
    endpoint = next(r.endpoint for r in server.create_app().routes if getattr(r, 'path', '') == '/actions/reset')
    assert not endpoint()['ok']
    assert submit_hold.is_held(PK, tracking=stores.tracking)


def test_clear_after_verified_preclick_refusal(world):
    stores, _ = world
    forms.hold_possible_submission(PK, {'last_button': 'Submit', 'url': URL})
    # This fake never issued IPC; record that refusal before removing evidence.
    stores.tracking.set_status(PK, Status.NEEDS_HUMAN, gate_reason='unknown_field', fail_kind='')
    submit_hold.clear(PK, tracking=stores.tracking)
    assert not submit_hold.is_held(PK, tracking=stores.tracking)


@pytest.mark.parametrize('status', ['uncertain', 'applied'])
async def test_outcome_recording_retains_unresolved_and_clears_only_confirmed(world, monkeypatch, status):
    stores, _ = world
    stores.answer_bank = SimpleNamespace(all_facts=lambda company: {},
                                        put=lambda *a, **kw: pytest.fail('No BrowserSkill draft may be banked'))
    stores.secrets = None
    monkeypatch.setattr(run, '_jd_text', AsyncMock(return_value='Engineering role. ' * 50))
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: '')
    monkeypatch.setattr(run, '_note_rotation_use', lambda *a: None)
    monkeypatch.setattr('core.rotation.ensure', lambda *a: None)
    monkeypatch.setattr('core.profiles.resolve_for', lambda row: None)
    monkeypatch.setattr('tools.credentials.get_login', lambda *a: None)
    monkeypatch.setattr('tools.browser_runtime.configuration', lambda: {'engine': 'browser_skill'})
    async def apply(*a, **kw):
        forms.hold_possible_submission(PK, {'last_button': 'Submit', 'url': URL + '?token=private'})
        return {'status': status, 'confirmation': 'Synthetic new page confirmation',
                'drafted': {'Essay': 'Unapproved essay'},
                'step': {'last_button': 'Submit', 'url': URL + '?token=private'}}
    monkeypatch.setattr('tools.browser_apply.apply', apply)
    result = await run._apply_direct(PK, stores)
    row = stores.tracking.get(PK)
    if status == 'uncertain':
        assert result['reason'] == 'uncertain'
        assert row['fail_kind'] == 'uncertain'
        assert row['last_button'] == 'Submit' and row['last_url'] == URL
        assert 'private' not in row['fail_reason']
        assert submit_hold.is_held(PK, tracking=stores.tracking)
    else:
        assert result['result'] == 'done'
        assert row['status'] == 'applied'
        assert row['confirmation_id'] == 'Synthetic new page confirmation'
        assert not submit_hold.is_held(PK, tracking=stores.tracking)


async def test_sibling_graph_and_tool_dispatch_cannot_bypass_key(world, monkeypatch):
    from agent.graph import apply_to_job
    from tools.browser_apply import apply
    stores, _ = world
    submit_hold.mark(PK, tracking=stores.tracking)
    # Stop before bank, browser readiness, rotation or a new SUBMITTING write.
    monkeypatch.setattr('tools.browser_runtime.available', lambda: pytest.fail('Held tool reached browser readiness'))
    result = await apply(URL, 'example-co', {}, 'synthetic', pk=PK)
    assert result['status'] == 'uncertain'
    result = await apply_to_job(SimpleNamespace(state={'pk': PK, 'company': 'example-co'}))
    assert result['status'] == 'uncertain'
    assert submit_hold.is_held(PK, tracking=stores.tracking)


def test_reapply_cannot_clone_unresolved_hold(world, monkeypatch):
    from fastapi import BackgroundTasks
    monkeypatch.setattr('core.profiles.get', lambda profile_id: SimpleNamespace(id='synthetic'))
    monkeypatch.setattr('core.profiles.reapply', lambda *a: pytest.fail('Held job cannot be cloned'))
    stores, _ = world
    submit_hold.mark(PK, tracking=stores.tracking)
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if getattr(r, 'path', '') == '/actions/reapply/{pk:path}')
    assert not endpoint(PK, {'profile_id': 'synthetic'}, BackgroundTasks())['ok']

@pytest.mark.parametrize('active', ['lease', 'browser'])
def test_mark_applied_refuses_inflight_attempt_without_clearing_hold(
        world, monkeypatch, active):
    stores, _ = world
    forms.hold_possible_submission(PK, {'last_button': 'Continue', 'url': URL})
    before = stores.tracking.get(PK)
    if active == 'lease':
        monkeypatch.setattr(ApplyQueue, 'in_flight', lambda self: [PK])
    else:
        monkeypatch.setattr(bsk, 'applies_running', lambda: 1)
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if getattr(r, 'path', '') == '/actions/mark-applied/{pk}')
    response = endpoint(PK, {'note': 'Synthetic portal evidence'})
    assert response == {'ok': False, 'error': 'An application attempt is still in flight.'}
    assert stores.tracking.get(PK) == before
    assert submit_hold.is_held(PK, tracking=stores.tracking)


def test_mark_applied_after_attempt_stops_records_outcome_and_clears_hold(world):
    stores, _ = world
    forms.hold_possible_submission(PK, {'last_button': 'Submit', 'url': URL})
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if getattr(r, 'path', '') == '/actions/mark-applied/{pk}')
    result = endpoint(PK, {'note': 'Synthetic portal confirmation'})
    assert result == {'ok': True}
    row = stores.tracking.get(PK)
    assert row['status'] == 'applied_manual'
    assert row['confirmation_id'] == 'Synthetic portal confirmation'
    assert not submit_hold.is_held(PK, tracking=stores.tracking)


def test_mark_applied_refuses_when_lease_proof_unavailable(world, monkeypatch):
    stores, _ = world
    forms.hold_possible_submission(PK, {'last_button': 'Submit', 'url': URL})
    monkeypatch.setattr(ApplyQueue, 'in_flight', lambda self: (_ for _ in ()).throw(
        RuntimeError('Synthetic Redis failure')))
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if getattr(r, 'path', '') == '/actions/mark-applied/{pk}')
    assert not endpoint(PK, {})['ok']
    assert stores.tracking.get(PK)['status'] == 'needs_human'
    assert submit_hold.is_held(PK, tracking=stores.tracking)
