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
        assert run.retry_job(pk, stores)["result"] == "already_running"

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


@pytest.mark.parametrize("mode", ["auto", "gated"])
@pytest.mark.parametrize("requested", ["", "2030-01-01T00:00:00+00:00"])
def test_review_only_generic_ready_gate_never_creates_submission_authority(
        monkeypatch, mode, requested):
    from core import flags
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.TAILORING,
                        resume_s3_key="resumes/example-co-job-1.pdf",
                        apply_requested_at=requested, match_score=9,
                        jd_read_prepare_only=True)
    stores = SimpleNamespace(tracking=tracking)
    emits = []
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: emits.append(a))
    monkeypatch.setattr(flags, "apply_mode", lambda: mode)
    monkeypatch.setattr(run, "_auto_decision",
                        lambda *a: pytest.fail("prepare-only cannot auto-approve"))

    class ReadyEvent:
        author = "review"
        content = None

        def get_function_calls(self):
            return [SimpleNamespace(name="ask_human", id="synthetic-call",
                                    args={"question": "Ready to apply?"})]

        def get_function_responses(self):
            return []

    class ReviewRunner:
        async def run_async(self, **kwargs):
            yield ReadyEvent()

    result = run._run(run._drive_async(
        ReviewRunner(), pk, SimpleNamespace(), stores, prepare_only=True))
    assert result["result"] == "gated"
    assert tracking.get(pk)["status"] == "tailored"
    assert tracking.get(pk)["apply_requested_at"] == requested
    queue = ApplyQueue(tracking.r)
    pending = [it for it in queue.pending() if it["pk"] == pk]
    assert bool(pending) is bool(requested)
    assert all(ev[0] != "applied" for ev in emits)


def test_recovered_reader_uses_review_agent_for_ordinary_posting(monkeypatch):
    from agent import graph

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        fail_kind="jd_reader_exhausted", jd_read_revision=8,
                        apply_requested_at="")
    stores = SimpleNamespace(tracking=tracking)
    agents = []
    events = []
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: events.append(a))
    monkeypatch.setattr(run, "_session_state",
                        lambda row, text: {"base_latex": "Test User synthetic seed"})
    monkeypatch.setattr(run, "_save_output", lambda *a, **kw: None)

    class Sessions:
        async def get_session(self, **kwargs):
            return None

        async def create_session(self, **kwargs):
            return None

        async def delete_session(self, **kwargs):
            return None

    monkeypatch.setattr(run, "_session_service", lambda: Sessions())

    async def recovered(row, **kwargs):
        return GOOD

    monkeypatch.setattr(run, "_jd_text", recovered)

    class FakeRunner:
        def __init__(self, agent, **kw):
            agents.append(agent)

    monkeypatch.setattr(run, "Runner", FakeRunner)

    async def prepared(runner, pk, message, stores, *, prepare_only):
        assert prepare_only
        stores.tracking.set_status(pk, Status.TAILORED,
                                   resume_s3_key="resumes/example-co-job-1.pdf")
        return {"result": "prepared", "pk": pk}

    monkeypatch.setattr(run, "_drive_async", prepared)
    assert run.retry_job(pk, stores)["result"] == "prepared"
    assert agents == [graph.review_agent]
    assert tracking.get(pk)["jd_read_attempts"] == 0
    assert tracking.get(pk)["jd_read_prepare_only"] is True
    assert tracking.get(pk)["jd_read_error"] == ""
    assert tracking.get(pk)["status"] == "tailored"
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_preparation_only_enqueue_rechecks_revoked_request(monkeypatch):
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    tracking.set_status(pk, Status.TAILORED,
                        resume_s3_key="resumes/example-co-job-1.pdf",
                        jd_read_prepare_only=True, apply_requested_at="")
    assert run._enqueue_apply(pk, stores, require_request=True)["result"] == "refused"
    assert not any(item["pk"] == pk for item in ApplyQueue(tracking.r).pending())
    tracking.set_status(pk, Status.TAILORED,
                        apply_requested_at="2030-01-01T00:00:00+00:00",
                        confirmation_id="synthetic-confirmed")
    assert run._enqueue_apply(pk, stores, require_request=True)["result"] == "refused"
    assert not ApplyQueue(tracking.r).pending()


def test_reopen_safe_closed_row_schedules_once_and_conflicts_stop(monkeypatch):
    from fastapi import BackgroundTasks
    import server

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.SKIPPED, skip_reason="synthetic")
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr(server, "get_settings", lambda: SimpleNamespace())
    events = []
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: events.append(a))
    app = server.create_app()
    callback = next(r.endpoint for r in app.routes
                    if getattr(r, "path", "") == "/actions/reopen/{pk:path}")
    tasks = BackgroundTasks()
    result = callback(pk, tasks)
    assert result["ok"] is True
    assert tracking.get(pk)["status"] == "found"
    assert tracking.get(pk)["jd_read_revision"] == 1
    assert len(tasks.tasks) == 1
    assert len(events) == 1

    tracking.set_status(pk, Status.FAILED, jd_read_attempts=0,
                        jd_read_retry_at=None)
    before = tracking.get(pk)
    orig = tracking.update_if_status

    def manual_wins(*a, **kw):
        tracking.set_status(pk, Status.APPLIED_MANUAL,
                            confirmation_id="synthetic-confirmed")
        return orig(*a, **kw)

    monkeypatch.setattr(tracking, "update_if_status", manual_wins)
    events.clear()
    tasks = BackgroundTasks()
    assert callback(pk, tasks)["error"] == "conflict"
    assert tracking.get(pk)["status"] == "applied_manual"
    assert tracking.get(pk)["jd_read_attempts"] == before["jd_read_attempts"]
    assert not events
    assert not tasks.tasks


def test_process_backlog_contains_reader_storage_fault_after_manual_decision(
        monkeypatch):
    from contextlib import nullcontext
    from tools import submit_hold
    import daemon
    from core import flags, preparation

    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(daemon, "make_stores", lambda: stores)
    monkeypatch.setattr(flags, "skipped_companies", lambda: set())
    monkeypatch.setattr(flags, "stop_epoch", lambda: 0)
    monkeypatch.setattr(daemon, "_pass_cancelled", lambda *a, **kw: False)
    monkeypatch.setattr(run, "_browser_companies", lambda: set())
    monkeypatch.setattr(run, "_jd_text", lambda *a, **kw: _synthetic_good())
    from types import SimpleNamespace as SN

    class Progress:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def start(self, row):
            pass

        def complete(self, status):
            pass

    monkeypatch.setattr(preparation, "track", lambda selected: Progress())
    original = tracking.update_if_status
    original_set = tracking.set_status
    writes = []
    terminal = []
    counter = [0]

    def set_status(pk, status, **attrs):
        if status == Status.ERROR:
            writes.append(attrs)
        return original_set(pk, status, **attrs)

    def faulty(pk, expected_status, updates, *, expected_reader):
        counter[0] += 1
        if counter[0] == 2:
            original_set(pk, Status.APPLIED_MANUAL, confirmation_id="synthetic")
            submit_hold.mark(pk, tracking=tracking)
            terminal.append(tracking.get(pk))
            raise RuntimeError("synthetic storage error")
        return original(pk, expected_status, updates, expected_reader=expected_reader)

    monkeypatch.setattr(tracking, "set_status", set_status)
    monkeypatch.setattr(tracking, "update_if_status", faulty)
    result = daemon.process_backlog_once(prepare_only=True)
    assert result["evaluated"] == 1
    assert not writes
    assert tracking.get(pk) == terminal[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_requested_queue_put_hold_race_preserves_manual_terminal(monkeypatch):
    """A refused queue insertion cannot turn a newer APPLIED_MANUAL into TAILORED."""
    from core.apply_queue import ApplyQueue
    from tools import submit_hold

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.TAILORING, jd_read_revision=2,
                        apply_requested_at="2030-01-01T00:00:00+00:00",
                        resume_s3_key="resumes/synthetic-new.pdf")
    stores = SimpleNamespace(tracking=tracking)
    queue = ApplyQueue(tracking.r)
    pending_before, leases_before = queue.pending(), queue.in_flight()
    snapshots = []
    original_put = ApplyQueue.put

    def manual_before_put(self, *args, **kwargs):
        tracking.set_status(pk, Status.APPLIED_MANUAL,
                            confirmation_id="synthetic-confirmed",
                            applied_at="2030-01-02T00:00:00+00:00")
        submit_hold.mark(pk, tracking=tracking)
        snapshots.append(tracking.get(pk))
        # The real put checks the durable hold and refuses without enqueuing.
        return original_put(self, *args, **kwargs)

    monkeypatch.setattr(ApplyQueue, "put", manual_before_put)
    result = run._enqueue_apply(pk, stores, require_request=True)
    assert result["result"] == "failed" and result["reason"] == "uncertain"
    assert tracking.get(pk) == snapshots[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    assert tracking.status_counts().get("applied_manual") == 1
    assert tracking.status_counts().get("tailored", 0) == 0
    assert queue.pending() == pending_before
    assert queue.in_flight() == leases_before


def test_requested_queue_duplicate_is_not_a_protection_refusal(monkeypatch):
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.TAILORING, jd_read_revision=3,
                        apply_requested_at="2030-01-01T00:00:00+00:00")
    stores = SimpleNamespace(tracking=tracking)
    queue = ApplyQueue(tracking.r)
    assert queue.put(pk, "example-co", requested=True)
    original = tracking.get(pk)
    pending = queue.pending()

    result = run._enqueue_apply(pk, stores, require_request=True)
    assert result["result"] == "already_queued" and result["queued"] is False
    assert tracking.get(pk) == original
    assert queue.pending() == pending


def test_requested_queue_post_put_terminal_wins_atomic_status_cas(monkeypatch):
    """Even a successful new insertion cannot authorize a stale row write."""
    from core.apply_queue import ApplyQueue
    from tools import submit_hold

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.TAILORING,
                        apply_requested_at="2030-01-01T00:00:00+00:00")
    stores = SimpleNamespace(tracking=tracking)
    snapshots = []
    original_update = tracking.update_if_status

    def manual_before_status_cas(target, expected, patch, *, expected_reader):
        tracking.set_status(pk, Status.APPLIED_MANUAL, confirmation_id="synthetic")
        submit_hold.mark(pk, tracking=tracking)
        snapshots.append(tracking.get(pk))
        return original_update(target, expected, patch, expected_reader=expected_reader)

    monkeypatch.setattr(tracking, "update_if_status", manual_before_status_cas)
    outcome = run._enqueue_apply(pk, stores, require_request=True)
    assert outcome["result"] == "conflict"
    assert tracking.get(pk) == snapshots[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    # The pending item is subject to the existing dispatch/final-submit guards.
    assert [x["pk"] for x in ApplyQueue(tracking.r).pending()] == [pk]


@pytest.mark.parametrize("discovery_source", ["", "career_ops"])
def test_prepared_authority_revoked_between_wrapper_and_enqueue_get(
        monkeypatch, discovery_source):
    """Both ordinary and career_ops prepared results require a CURRENT request."""
    from core.apply_queue import ApplyQueue

    _quiet(monkeypatch)
    tracking, pk = _synthetic_tracking()
    request = "2030-01-01T00:00:00+00:00"
    tracking.set_status(pk, Status.FOUND, discovery_source=discovery_source,
                        apply_requested_at=request,
                        resume_s3_key="resumes/synthetic-new.pdf")
    stores = SimpleNamespace(tracking=tracking)
    calls = []

    async def prepared(*args, **kwargs):
        return {"result": "prepared", "pk": pk}

    original_enqueue = run._enqueue_apply

    def revoke_before_second_read(target, store, **kwargs):
        calls.append(kwargs)
        tracking.set_status(target, Status.TAILORING, apply_requested_at="")
        return original_enqueue(target, store, **kwargs)

    monkeypatch.setattr(run, "_run_job_async", prepared)
    monkeypatch.setattr(run, "_enqueue_apply", revoke_before_second_read)
    result = run.run_job(pk, stores, prepare_only=True)
    assert len(calls) == 1
    assert result["result"] == "refused"
    assert result["reason"] == "apply_request_or_preparation_not_current"
    assert tracking.get(pk)["apply_requested_at"] == ""
    assert tracking.get(pk)["status"] == "tailoring"
    assert ApplyQueue(tracking.r).pending() == []
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_jd_attention_retry_invalidates_old_pdf_without_new_save(monkeypatch):
    """A PDF from the previous JD cannot authorize the recovered preparation."""
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    request = "2030-01-01T00:00:00+00:00"
    tracking.set_status(pk, Status.ERROR, fail_kind="jd_reader_exhausted",
                        jd_read_attempts=3, jd_read_revision=6,
                        resume_s3_key="resumes/old-posting.pdf",
                        resume_seed="synthetic-existing-seed",
                        apply_requested_at=request)
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr("core.events.emit", lambda *args, **kw: None)
    monkeypatch.setattr(run, "_session_state",
                        lambda row, text: {"base_latex": "Test User seed"})
    monkeypatch.setattr(run, "_save_output", lambda *args: None)

    async def recovered(row, **kwargs):
        return GOOD

    class EmptySession:
        async def delete_session(self, **kwargs):
            return None

        async def get_session(self, **kwargs):
            return None

        async def create_session(self, **kwargs):
            return None

    class EmptyReviewRunner:
        def __init__(self, agent, **kwargs):
            pass

        async def run_async(self, **kwargs):
            if False:
                yield None  # No current-run PDF save or other ADK output.

    monkeypatch.setattr(run, "_session_service", lambda: EmptySession())
    monkeypatch.setattr(run, "_jd_text", recovered)
    monkeypatch.setattr(run, "Runner", EmptyReviewRunner)
    result = run.retry_job(pk, stores)
    row = tracking.get(pk)
    assert result["result"] == "failed" and result["reason"] == "no_resume"
    assert row["resume_s3_key"] == ""
    assert row["resume_seed"] == "synthetic-existing-seed"
    assert row["apply_requested_at"] == request
    assert row["jd_read_prepare_only"] is True
    assert ApplyQueue(tracking.r).pending() == []
    assert row["status"] == "failed"
    assert not tracking.r.exists(f"lock:job:{pk}")


@pytest.mark.parametrize("mode", ["auto", "gated"])
@pytest.mark.parametrize("requested", [False, True])
def test_actual_skip_then_retry_remains_review_only(
        monkeypatch, mode, requested):
    """Skipping an exhausted ERROR must not re-enter the full apply graph."""
    from agent import graph
    from core import flags
    from core.apply_queue import ApplyQueue
    import server

    tracking, pk = _synthetic_tracking()
    authority = "2030-01-01T00:00:00+00:00" if requested else ""
    tracking.set_status(pk, Status.ERROR, fail_kind="jd_reader_exhausted",
                        jd_read_attempts=3, jd_read_revision=6,
                        jd_read_retry_at=None, apply_requested_at=authority)
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    app = server.create_app()
    skip = next(r.endpoint for r in app.routes
                if getattr(r, "path", "") == "/actions/skip/{pk}")
    assert skip(pk)["ok"] is True
    assert tracking.get(pk)["status"] == "skipped"
    assert tracking.get(pk)["jd_read_attempts"] == 3
    monkeypatch.setattr(flags, "apply_mode", lambda: mode)
    monkeypatch.setattr("core.events.emit", lambda *args, **kw: None)
    monkeypatch.setattr(run, "_session_state",
                        lambda row, text: {"base_latex": "Test User seed"})
    monkeypatch.setattr(run, "_save_output", lambda *args: None)

    class NoNetworkSession:
        async def delete_session(self, **kw):
            return None

        async def get_session(self, **kw):
            return None

        async def create_session(self, **kw):
            return None

    used_agents = []

    class GraphCapture:
        def __init__(self, agent, **kwargs):
            used_agents.append(agent)

    async def recovered(row, **kw):
        return GOOD

    async def prepared(runner, current_pk, message, current_stores, *, prepare_only):
        assert prepare_only is True
        current_stores.tracking.set_status(
            current_pk, Status.TAILORED,
            resume_s3_key="resumes/synthetic-new.pdf")
        return {"result": "prepared", "pk": current_pk}

    monkeypatch.setattr(run, "_session_service", lambda: NoNetworkSession())
    monkeypatch.setattr(run, "_jd_text", recovered)
    monkeypatch.setattr(run, "Runner", GraphCapture)
    monkeypatch.setattr(run, "_drive_async", prepared)
    result = run.retry_job(pk, stores)
    assert used_agents == [graph.review_agent]
    row = tracking.get(pk)
    assert row["status"] == "tailored"
    assert row["jd_read_attempts"] == 0
    assert row["jd_read_prepare_only"] is True
    assert row["apply_requested_at"] == authority
    queued = [item for item in ApplyQueue(tracking.r).pending()
              if item["pk"] == pk]
    if requested:
        assert result["result"] == "queued" and len(queued) == 1
    else:
        assert result["result"] == "prepared" and queued == []
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_actual_reopen_refuses_exhaustion_after_skip(monkeypatch):
    from fastapi import BackgroundTasks
    from core.apply_queue import ApplyQueue
    import server

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        fail_kind="jd_reader_exhausted", jd_read_revision=6)
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr(server, "get_settings", lambda: SimpleNamespace())
    events = []
    monkeypatch.setattr("core.events.emit", lambda *args, **kw: events.append(args))
    app = server.create_app()
    skip = next(r.endpoint for r in app.routes
                if getattr(r, "path", "") == "/actions/skip/{pk}")
    reopen = next(r.endpoint for r in app.routes
                  if getattr(r, "path", "") == "/actions/reopen/{pk:path}")
    assert skip(pk)["ok"] is True
    before = tracking.get(pk)
    counts = tracking.status_counts()
    queue = ApplyQueue(tracking.r)
    pending, leases = queue.pending(), queue.in_flight()
    tasks = BackgroundTasks()
    result = reopen(pk, tasks)
    assert result["ok"] is False
    assert "Retry" in result["note"]
    assert tracking.get(pk) == before
    assert tracking.status_counts() == counts
    assert queue.pending() == pending and queue.in_flight() == leases
    assert tasks.tasks == [] and events == []


def test_retry_session_reset_failure_is_visible_and_second_retry_works(monkeypatch):
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, fail_kind="jd_reader_exhausted",
                        jd_read_attempts=3, jd_read_revision=6,
                        resume_s3_key="resumes/old.pdf", apply_requested_at="")
    stores = SimpleNamespace(tracking=tracking)
    messages = []
    monkeypatch.setattr("core.events.emit",
                        lambda kind, **kw: messages.append((kind, kw)))

    async def reset_failed(target):
        raise RuntimeError("synthetic session backend unavailable")

    async def no_reader(*args, **kw):
        pytest.fail("Reader must not run after session reset failure")

    monkeypatch.setattr(run, "_reset_session", reset_failed)
    monkeypatch.setattr(run, "_run_job_async", no_reader)
    result = run.retry_job(pk, stores)
    assert result["result"] == "error"
    assert result["reason"] == "jd_retry_session_error"
    row = tracking.get(pk)
    assert row["status"] == "error"
    assert row["jd_read_revision"] == 8  # admitted + guarded attention
    assert row["jd_read_prepare_only"] is True
    assert row["jd_read_attempts"] == 0
    assert row["resume_s3_key"] == ""
    assert "Retry" in row["error"]
    assert row["apply_requested_at"] == ""
    assert [event for event, _ in messages] == ["error"]
    assert not tracking.r.exists(f"lock:job:{pk}")

    async def reset_ok(target):
        return None

    async def private_prepared(target, observed, stores, *, prepare_only):
        assert prepare_only is True
        tracking.set_status(target, Status.TAILORED,
                            resume_s3_key="resumes/new.pdf")
        return {"result": "prepared", "pk": target}

    monkeypatch.setattr(run, "_reset_session", reset_ok)
    monkeypatch.setattr(run, "_run_job_async", private_prepared)
    second = run.retry_job(pk, stores)
    assert second["result"] == "prepared"
    assert tracking.get(pk)["status"] == "tailored"
    assert tracking.get(pk)["jd_read_prepare_only"] is True
    assert ApplyQueue(tracking.r).pending() == []
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_retry_reset_failure_conflicts_with_new_manual_terminal(monkeypatch):
    from tools import submit_hold

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        jd_read_revision=6, fail_kind="jd_reader_exhausted")
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
    snapshots = []
    original_update = tracking.update_if_status

    def manual_before_error_settlement(target, old_status, patch, *, expected_reader):
        if patch.get("fail_kind") == "jd_retry_session_error":
            tracking.set_status(pk, Status.APPLIED_MANUAL,
                                confirmation_id="synthetic-confirmed")
            submit_hold.mark(pk, tracking=tracking)
            snapshots.append(tracking.get(pk))
        return original_update(target, old_status, patch,
                               expected_reader=expected_reader)

    async def reset_failed(target):
        raise RuntimeError("synthetic session failure")

    monkeypatch.setattr(tracking, "update_if_status", manual_before_error_settlement)
    monkeypatch.setattr(run, "_reset_session", reset_failed)
    result = run.retry_job(pk, stores)
    assert result["result"] == "conflict"
    assert tracking.get(pk) == snapshots[0]
    assert submit_hold.is_held(pk, tracking=tracking)
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_retry_session_setup_failure_settles_after_successful_jd_read(monkeypatch):
    """Session creation failures must not strand a freshly recovered reader."""
    from core.apply_queue import ApplyQueue

    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        jd_read_revision=6, fail_kind="jd_reader_exhausted",
                        apply_requested_at="")
    stores = SimpleNamespace(tracking=tracking)
    events = []
    monkeypatch.setattr("core.events.emit",
                        lambda kind, **kw: events.append((kind, kw)))
    monkeypatch.setattr(run, "_session_state",
                        lambda row, text: {"base_latex": "Test User synthetic seed"})

    async def reset_ok(target):
        return None

    async def recovered(row, **kw):
        return GOOD

    class BrokenSession:
        async def get_session(self, **kw):
            raise RuntimeError("synthetic ADK session backend unavailable")

    monkeypatch.setattr(run, "_reset_session", reset_ok)
    monkeypatch.setattr(run, "_jd_text", recovered)
    monkeypatch.setattr(run, "_session_service", lambda: BrokenSession())
    result = run.retry_job(pk, stores)
    row = tracking.get(pk)
    assert result["result"] == "error"
    assert result["reason"] == "jd_retry_session_error"
    assert result["attention_persisted"] is True
    assert row["status"] == "error"
    assert row["jd_read_revision"] == 9  # reset, successful JD, session failure
    assert row["jd_read_attempts"] == 0
    assert row["jd_read_prepare_only"] is True
    assert row["jd_text"] == GOOD
    assert "Retry" in row["error"]
    assert [name for name, _ in events if name == "error"] == ["error"]
    assert ApplyQueue(tracking.r).pending() == []
    assert not tracking.r.exists(f"lock:job:{pk}")


def test_retry_session_attention_storage_fault_is_bounded(monkeypatch):
    """An unavailable attention write is diagnosed without replaying the CAS."""
    tracking, pk = _synthetic_tracking()
    tracking.set_status(pk, Status.ERROR, jd_read_attempts=3,
                        jd_read_revision=6, fail_kind="jd_reader_exhausted")
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
    attempts = [0]
    original = tracking.update_if_status

    def failure_once(pk_value, status, patch, *, expected_reader):
        if patch.get("fail_kind") == "jd_retry_session_error":
            attempts[0] += 1
            raise RuntimeError("synthetic persistence unavailable")
        return original(pk_value, status, patch,
                        expected_reader=expected_reader)

    async def reset_failed(target):
        raise RuntimeError("synthetic session unavailable")

    monkeypatch.setattr(run, "_reset_session", reset_failed)
    monkeypatch.setattr(tracking, "update_if_status", failure_once)
    result = run.retry_job(pk, stores)
    assert attempts == [1]
    assert result["result"] == "error"
    assert result["reason"] == "jd_tracking_storage_error"
    assert result["attention_persisted"] is False
    assert "could not be persisted" in result["detail"]
    assert tracking.get(pk)["status"] == "tailoring"
    assert not tracking.r.exists(f"lock:job:{pk}")
