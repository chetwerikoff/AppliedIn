"""A Claude quota outage must not burn an entire browser-only job backlog."""
import asyncio
import json
from types import SimpleNamespace

import fakeredis
import pytest

from agent import run
from core.models import Status
from core.storage.local import RedisTracking
from tools import claude_chrome, jd

LIMIT = 'The Claude browser reader has reached its usage limit: resets 10:10pm. Retry after the limit resets.'
GOOD = 'Responsibilities: build reliable distributed systems. ' * 15


@pytest.fixture(autouse=True)
def isolate_reader(monkeypatch):
    monkeypatch.setattr(jd, '_browser_retry', (0, ''))
    monkeypatch.setattr(jd, 'available', lambda: (True, ''))
    monkeypatch.setattr(claude_chrome, 'available', lambda: (True, ''))
    monkeypatch.setattr(jd, '_from_ats', lambda url: None)
    monkeypatch.setattr(jd, '_get', lambda *a: None)


def test_session_limit_preserves_the_cli_reset_time_and_is_retryable():
    raw = json.dumps({'is_error': True, 'api_error_status': 429, 'num_turns': 1,
                      'result': "You've hit your session limit · resets 10:10pm (America/Los_Angeles)"})
    detail = claude_chrome._envelope_reason(raw, 1)
    assert 'resets 10:10pm (America/Los_Angeles)' in detail
    assert 'partway through' not in detail
    assert 'lower how many' not in detail
    assert claude_chrome.is_infrastructure(detail)


def test_one_outage_does_not_launch_a_browser_for_every_job(monkeypatch):
    calls = []
    async def unavailable(*args, **kwargs):
        calls.append(args)
        return {}, LIMIT
    monkeypatch.setattr(claude_chrome, 'run_task', unavailable)
    for i in range(4):
        with pytest.raises(jd.PostingReadUnavailable, match='usage limit'):
            jd.fetch_jd(f'https://example.com/jobs/{i}')
    assert len(calls) == 1
    monkeypatch.setattr(jd.time, 'monotonic', lambda: jd._browser_retry[0] + 1)
    async def recovered(*args, **kwargs):
        return {'title': 'Engineer', 'description': GOOD}, ''
    monkeypatch.setattr(claude_chrome, 'run_task', recovered)
    assert jd.fetch_jd('https://example.com/jobs/1') == GOOD


def test_batch_retains_successes_and_stops_on_shared_outage(monkeypatch):
    calls = []
    async def read(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return {'postings': [{'url': 'https://x/1', 'description': GOOD}]}, ''
        return {}, LIMIT
    monkeypatch.setattr(jd, 'run_task', read)
    descriptions, gone = jd.read_postings(['https://x/1', 'https://x/2', 'https://x/3'], batch=1, with_gone=True)
    assert descriptions == {'https://x/1': GOOD.strip()}
    assert not gone
    assert len(calls) == 2
    with pytest.raises(jd.PostingReadUnavailable):
        jd.fetch_jd('https://x/2')


def test_unavailable_reader_keeps_job_found_with_real_reason_and_never_tailors(monkeypatch):
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = "example-co#job-1"
    tracking.set_status(pk, Status.FOUND, company="example-co",
                        jd_url="https://example.test/jobs/1", jd_text="Test role")
    stores = SimpleNamespace(tracking=tracking)
    def unavailable(*args, **kwargs):
        raise jd.PostingReadUnavailable(LIMIT)
    monkeypatch.setattr(jd, 'fetch_jd', unavailable)
    monkeypatch.setattr('core.events.emit', lambda *a, **kw: None)
    monkeypatch.setattr(run, '_session_service', lambda: pytest.fail('No tailoring without a posting'))
    result = run.run_job(pk, stores, prepare_only=True)
    row = tracking.get(pk)
    assert result['result'] == 'deferred'
    assert row['status'] == 'found'
    assert LIMIT in row['jd_read_error']
    assert 'Attempt 1 of 3' in row['jd_read_error']
    assert 'UTC' in row['jd_read_error']
    assert not row.get('fail_kind')
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_existing_usable_description_survives_reader_outage(monkeypatch):
    def unavailable(*args, **kwargs):
        raise jd.PostingReadUnavailable(LIMIT)
    monkeypatch.setattr(jd, 'fetch_jd', unavailable)
    text = 'Build infrastructure. ' * 10
    assert asyncio.run(run._jd_text({'jd_url':'https://x/1', 'jd_text':text})).strip() == text.strip()


def test_missing_cli_and_empty_batch_do_not_break_with_gone_contract(monkeypatch):
    assert jd.read_postings([], with_gone=True) == ({}, set())
    monkeypatch.setattr(jd, 'available', lambda: (False, 'Claude Code is not installed'))
    with pytest.raises(jd.PostingReadUnavailable, match='not installed'):
        jd.read_postings(['https://x/1'], with_gone=True)


def _synthetic_tracking():
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = "example-co#job-1"
    tracking.set_status(pk, Status.FOUND, company="example-co",
                        jd_url="https://example.test/jobs/1",
                        title="Test role", jd_text="Test role", attempts=7)
    return tracking, pk


def _quiet(monkeypatch):
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
    monkeypatch.setattr(run, "_session_service",
                        lambda: pytest.fail("No ADK session is permitted here"))


def test_persisted_budget_deadlines_and_real_ui_projection(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from server import _to_ui

    _quiet(monkeypatch)
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(run, "_utc_now", lambda: clock[0])
    calls = []

    async def unavailable(row, **kw):
        calls.append(row["pk"])
        raise jd.PostingReadUnavailable("synthetic reader outage")

    monkeypatch.setattr(run, "_jd_text", unavailable)
    tracking, pk = _synthetic_tracking()
    other = "example-co#other"
    tracking.set_status(other, Status.FOUND, note="untouched", jd_text="other",
                        jd_read_attempts=2, attempts=19)
    other_before = tracking.get(other)
    stores = SimpleNamespace(tracking=tracking)
    artifacts = SimpleNamespace(exists=lambda key: False, version=lambda key: "")

    first = run.run_job(pk, stores)
    row = tracking.get(pk)
    assert first["result"] == "deferred"
    assert row["status"] == "found"
    assert row["jd_read_attempts"] == 1
    assert row["jd_read_revision"] == 2
    assert row["jd_read_retry_at"] == (clock[0] + timedelta(seconds=60)).isoformat()
    assert "2030-01-01 00:01:00 UTC" in _to_ui(row, artifacts)["jd_read_error"]
    before_wait = dict(row)
    clock[0] += timedelta(seconds=59)
    assert run.run_job(pk, stores)["reason"] == "jd_read_not_eligible"
    assert tracking.get(pk) == before_wait
    assert len(calls) == 1

    clock[0] += timedelta(seconds=1)
    # Reconstruct the store instance over the persisted fake Redis backing.
    stores.tracking = tracking = RedisTracking(tracking.r)
    assert run.run_job(pk, stores)["result"] == "deferred"
    row = tracking.get(pk)
    assert row["jd_read_attempts"] == 2
    assert row["jd_read_revision"] == 4
    assert row["jd_read_retry_at"] == (clock[0] + timedelta(seconds=300)).isoformat()
    assert "2030-01-01 00:06:00 UTC" in _to_ui(row, artifacts)["jd_read_error"]
    clock[0] += timedelta(seconds=299)
    assert run.run_job(pk, stores)["reason"] == "jd_read_not_eligible"
    clock[0] += timedelta(seconds=1)
    assert run.run_job(pk, stores)["reason"] == "jd_reader_exhausted"
    row = tracking.get(pk)
    assert row["status"] == "error"
    assert row["jd_read_attempts"] == 3
    assert row["jd_read_revision"] == 6
    assert row["jd_read_retry_at"] is None
    assert row["jd_read_error"] == ""
    assert row["fail_kind"] == "jd_reader_exhausted"
    assert "Retry" in _to_ui(row, artifacts)["closed_reason"]
    assert "did not prepare or send" in row["error"]
    assert tracking.get(other) == other_before
    assert len(calls) == 3


@pytest.mark.parametrize("outcome", ["unavailable", "usable", "unreadable", "sponsorship"])
@pytest.mark.parametrize("terminal", [Status.APPLIED_MANUAL, Status.JOB_GONE])
def test_read_derived_terminal_results_never_overwrite_newer_outcome(
        monkeypatch, outcome, terminal):
    from tools import submit_hold

    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    events = []
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: events.append((a, kw)))
    terminal_row = []

    async def delayed(row, **kw):
        tracking.set_status(pk, terminal, note="terminal decision",
                            confirmation_id="synthetic-confirmed")
        submit_hold.mark(pk, tracking=tracking)
        terminal_row.append(tracking.get(pk))
        if outcome == "unavailable":
            raise jd.PostingReadUnavailable("synthetic outage")
        if outcome == "unreadable":
            return "Error page"
        if outcome == "sponsorship":
            return "This employer does not sponsor visas. " + GOOD
        return GOOD

    monkeypatch.setattr(run, "_jd_text", delayed)
    result = run.run_job(pk, stores, prepare_only=True)
    assert result["result"] == "conflict"
    assert tracking.get(pk) == terminal_row[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    assert not tracking.r.exists(f"lock:job:{pk}")
    assert all(event[0][0] not in ("failed", "applied", "error") for event in events)


@pytest.mark.parametrize("reason", ["durable_hold", "row_hold", "confirmation"])
def test_same_status_fresh_protection_during_read_blocks_preparation(monkeypatch, reason):
    from tools import submit_hold

    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)

    async def read(row, **kw):
        if reason == "durable_hold":
            submit_hold.mark(pk, tracking=tracking)
        elif reason == "row_hold":
            tracking.set_status(pk, Status.TAILORING, possible_submission=True)
        else:
            tracking.set_status(pk, Status.TAILORING, confirmation_id="synthetic")
        return GOOD

    monkeypatch.setattr(run, "_jd_text", read)
    result = run.run_job(pk, stores, prepare_only=True)
    assert result["result"] == "refused"
    assert result["reason"] == "submission_protected"
    assert "jd_read_attempts" not in tracking.get(pk)
    assert not tracking.r.exists(f"lock:job:{pk}")


@pytest.mark.parametrize("kind", ["unreadable", "sponsorship"])
def test_second_guard_after_successful_metadata_patch(monkeypatch, kind):
    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    text = "Error page" if kind == "unreadable" else "We do not sponsor visas. " + GOOD

    async def read(row, **kw):
        return text

    monkeypatch.setattr(run, "_jd_text", read)
    predicate = run._unreadable if kind == "unreadable" else run._no_sponsorship
    snapshot = []

    def competing(result):
        # This decision runs AFTER the successful JD metadata CAS.
        tracking.set_status(pk, Status.APPLIED_MANUAL,
                            confirmation_id="synthetic", note="terminal")
        snapshot.append(tracking.get(pk))
        return predicate(result)

    monkeypatch.setattr(run, "_unreadable" if kind == "unreadable" else "_no_sponsorship",
                        competing)
    result = run.run_job(pk, stores, prepare_only=True)
    assert result["result"] == "conflict"
    assert tracking.get(pk) == snapshot[0]
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_idle_sweep_filters_before_batch_cap(monkeypatch):
    from datetime import UTC, datetime, timedelta
    import daemon

    due = {"pk": "example-co#due", "status": "found"}
    pending = [
        {"pk": f"example-co#pending{i}", "status": "found",
         "jd_read_attempts": 1,
         "jd_read_retry_at": (datetime.now(UTC) + timedelta(days=5)).isoformat()}
        for i in range(3)]
    enqueued = []
    tracking = SimpleNamespace(query_status=lambda status: pending + [due])
    q = SimpleNamespace(enqueue=lambda name, body: enqueued.append(body["pk"]))
    daemon._sweep_found(SimpleNamespace(tracking=tracking, queue=q, tailor_queue="synthetic"))
    assert enqueued == ["example-co#due"]


def test_retry_owns_session_reset_without_a_found_gap(monkeypatch):
    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        fail_kind="jd_reader_exhausted", jd_read_revision=8,
                        jd_read_retry_at=None, error="Reader exhausted; Retry")
    stores = SimpleNamespace(tracking=tracking)
    observed = tracking.get(pk)
    calls = []

    async def reset(session_pk):
        calls.append("reset")
        row = tracking.get(pk)
        assert row["status"] == "tailoring"
        assert row["jd_read_revision"] == 9
        assert row["jd_read_attempts"] == 0
        assert row["jd_read_prepare_only"] is True
        assert run.run_job(pk, stores)["result"] == "already_done"
        assert run.retry_job(pk, stores)["result"] == "already_done"

    async def private_run(session_pk, row, fake_stores, *, prepare_only=False):
        calls.append("private")
        assert prepare_only is True
        return {"result": "prepared", "pk": pk}

    monkeypatch.setattr(run, "_reset_session", reset)
    monkeypatch.setattr(run, "_run_job_async", private_run)
    result = run.retry_job(pk, stores)
    assert result["result"] == "prepared"
    assert calls == ["reset", "private"]
    assert tracking.get(pk)["status"] == "tailoring"
    assert tracking.get(pk)["attempts"] == observed["attempts"]
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_retry_refuses_in_flight_and_unknown_lease(monkeypatch):
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        fail_kind="jd_reader_exhausted")
    stores = SimpleNamespace(tracking=tracking)
    original = tracking.get(pk)
    monkeypatch.setattr(ApplyQueue, "in_flight", lambda q: {pk})
    assert run.retry_job(pk, stores)["reason"] == "apply_in_flight"
    assert tracking.get(pk) == original
    monkeypatch.setattr(ApplyQueue, "in_flight",
                        lambda q: (_ for _ in ()).throw(RuntimeError("synthetic lease outage")))
    assert run.retry_job(pk, stores)["reason"] == "apply_lease_unknown"
    assert tracking.get(pk) == original


def test_reopen_refuses_active_and_exhausted_without_events_or_tasks(monkeypatch):
    from fastapi import BackgroundTasks
    from core.apply_queue import ApplyQueue
    import server

    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr(server, "get_settings", lambda: SimpleNamespace())
    events = []
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: events.append(a))
    app = server.create_app()
    callback = next(r.endpoint for r in app.routes
                    if getattr(r, "path", "") == "/actions/reopen/{pk:path}")
    for status in (Status.FOUND, Status.TAILORING, Status.TAILORED,
                   Status.NEEDS_HUMAN, Status.SUBMITTING,
                   Status.APPLIED_MANUAL):
        tracking.set_status(pk, status)
        before = tracking.get(pk)
        tasks = BackgroundTasks()
        assert callback(pk, tasks)["ok"] is False
        assert tracking.get(pk) == before
        assert not tasks.tasks
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        fail_kind="jd_reader_exhausted")
    before = tracking.get(pk)
    tasks = BackgroundTasks()
    assert "Retry" in callback(pk, tasks)["note"]
    assert tracking.get(pk) == before
    assert not tasks.tasks
    tracking.set_status(pk, Status.SKIPPED, jd_read_attempts=0,
                        fail_kind="", confirmation_id="")
    before = tracking.get(pk)
    monkeypatch.setattr(ApplyQueue, "in_flight", lambda q: {pk})
    assert callback(pk, BackgroundTasks())["ok"] is False
    assert tracking.get(pk) == before
    assert not events


def test_storage_fault_after_manual_interleaving_stays_inside_daemon(monkeypatch):
    import daemon
    from tools import submit_hold

    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(daemon, "make_stores", lambda: stores)
    from core import flags
    monkeypatch.setattr(flags, "paused", lambda: False)
    monkeypatch.setattr(run, "_jd_text", lambda *a, **kw: _synthetic_good())
    original = tracking.update_if_status
    status_errors = []
    before = []
    counter = [0]

    def faulty(pk_value, expected_status, updates, *, expected_reader):
        counter[0] += 1
        if counter[0] == 2:
            tracking.set_status(pk_value, Status.APPLIED_MANUAL,
                                confirmation_id="synthetic", note="manual terminal")
            submit_hold.mark(pk_value, tracking=tracking)
            before.append(tracking.get(pk_value))
            raise RuntimeError("synthetic storage I/O failure")
        return original(pk_value, expected_status, updates,
                        expected_reader=expected_reader)

    def tracking_set(pk_value, status, **kw):
        if status == Status.ERROR:
            status_errors.append(kw)
        return original_set(pk_value, status, **kw)

    original_set = tracking.set_status
    monkeypatch.setattr(tracking, "update_if_status", faulty)
    monkeypatch.setattr(tracking, "set_status", tracking_set)
    daemon._evaluate_one(pk)
    assert counter[0] >= 3  # admission, fault, bounded same-witness attention
    assert not status_errors  # daemon's generic catch never wrote ERROR
    assert tracking.get(pk) == before[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    assert not tracking.r.exists(f"lock:job:{pk}")


async def _synthetic_good():
    return GOOD
