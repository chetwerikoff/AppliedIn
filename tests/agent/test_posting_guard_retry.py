"""Issue #310: explicit Retry of a closed posting-guard refusal (offline, synthetic).

Bounded guarantees only: existing `_claim`/`_reader_check`/`_active_apply_lease`/
`submit_hold.blocked`/`submit_review.guarded_row` protections plus a strict,
read-only pending-queue refusal. These tests do NOT claim origin provenance,
a recovery-specific CAS, artifact-write protection or dispatch protection.
Residual (accepted, not tested as prevented): an approve-all enqueue that lands
after the pending check.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest

from agent import run
from core.apply_queue import ApplyQueue
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tools import jd, submit_hold
from tools import posting_guard as pg

PK = "example-co#job-1"
URL = "https://jobs.ashbyhq.com/example-co/job-1"
TEXT = "Build safe and useful systems with a small engineering team. " * 20
CHANGED = TEXT.replace("safe", "fast", 1) + " A new synthetic requirement."
OLD_PDF = "resumes/example-co#job-1.pdf"


@pytest.fixture
def stores(monkeypatch):
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    source = pg.analyze(TEXT, source_required=True)
    assert tracking.put_new(JobRecord(
        company="example-co", job_id="job-1", jd_url=URL, title="Test Engineer",
        jd_text=source["text"], posting_guard=source["posting_guard"], ats="ashby"))
    monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
    return SimpleNamespace(tracking=tracking)


def _source(text=TEXT, *, url=URL, kind="ats_html"):
    guarded = pg.analyze(text, html_variants=[f"<p>{text}</p>"],
                         source_required=True, source_complete=True)
    return {**guarded, "source_kind": kind, "source_url": url}


def _row(stores):
    return stores.tracking.get_current(PK)


def _replace_row(stores, **fields):
    row = _row(stores)
    row.update(fields)
    stores.tracking.r.set(f"app:{PK}", json.dumps(row))


def _fake_reads(monkeypatch, results):
    calls, pending = [], iter(results)

    def read(url, kind):
        calls.append((url, kind))
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(jd, "fetch_jd_meta", read)
    return calls


@pytest.fixture
def forbidden(monkeypatch):
    """Spies that fail the test on any enqueue / browser / queue-put call."""
    from tools import browser_apply

    class Hits(list):
        armed = False

    hits = Hits()
    real_apply = run._apply_direct
    monkeypatch.setattr(run, "_enqueue_apply",
                        lambda *a, **kw: hits.append("enqueue") or {"result": "queued"})
    monkeypatch.setattr(ApplyQueue, "put",
                        lambda self, *a, **kw: hits.append("put") or True)
    browser = AsyncMock()
    monkeypatch.setattr(browser_apply, "apply", browser)

    def apply_direct(*a, **kw):
        # The real call builds the refusal fixture; once armed any call is a hit.
        if hits.armed:
            hits.append("apply_direct")
        return real_apply(*a, **kw)

    monkeypatch.setattr(run, "_apply_direct", apply_direct)
    return hits


class _FakeRunner:
    """Review-only agent double: records the agent and 'saves' a PDF."""
    agents: list = []

    def __init__(self, *, agent, app_name, session_service):
        type(self).agents.append(agent)

    async def run_async(self, *, user_id, session_id, new_message):
        # AC5: run the real save boundary and yield its actual current response.
        from agent.graph import save_tailored_resume
        result = save_tailored_resume(_FakeRunner.seed, SimpleNamespace(
            state={"pk": session_id, "base_latex": _FakeRunner.seed}))
        yield SimpleNamespace(
            author="tailor", content=None, get_function_calls=lambda: [],
            get_function_responses=lambda: [SimpleNamespace(
                name="save_tailored_resume", response=result)])


@pytest.fixture
def agent_doubles(monkeypatch, stores):
    _FakeRunner.agents = []
    _FakeRunner.stores = stores
    from discovery.watchlist import Preferences
    monkeypatch.setattr(run, "_effective_prefs", lambda co: Preferences())
    monkeypatch.setattr("core.steering.read", lambda: {"content": ""})
    monkeypatch.setattr("tools.render.render_pdf", lambda source: b"%PDF-synthetic")
    monkeypatch.setattr("core.profiles.resolve_for", lambda row: None)
    _FakeRunner.seed = r"\role{example-co}{2024}" + "\n" + r"\item One"
    data = {}
    def put(category, name, body, kind):
        key = category + "/" + name
        data[key] = body
        return key
    stores.artifacts = SimpleNamespace(put=put, get=lambda key: data[key])
    sessions = SimpleNamespace(get_session=AsyncMock(return_value=None),
                               create_session=AsyncMock())
    monkeypatch.setattr(run, "_session_service", lambda: sessions)
    monkeypatch.setattr(run, "_reset_session", AsyncMock())
    _FakeRunner.session_inputs = []

    def session_state(row, jd_text):
        _FakeRunner.session_inputs.append(jd_text)
        return {"base_latex": _FakeRunner.seed}

    monkeypatch.setattr(run, "_session_state", session_state)
    monkeypatch.setattr(run, "_save_output", lambda *a, **kw: None)
    monkeypatch.setattr(run, "Runner", _FakeRunner)
    return _FakeRunner


def _refused_row(stores, monkeypatch):
    """Persist a real pre-browser changed-JD refusal via `_apply_direct`."""
    from tools import browser_apply

    old = _row(stores)
    assert stores.tracking.update_posting_guard(PK, old, _source(), source_witness=True)
    old_gen = _row(stores)["posting_guard"]["generation"]
    stores.tracking.set_status(PK, Status.TAILORED, gate_reason="approval")
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")
    monkeypatch.setattr("core.rotation.ensure", lambda *a, **kw: None)
    monkeypatch.setattr("core.profiles.resolve_for", lambda *a: None)
    monkeypatch.setattr("tools.credentials.get_login", lambda *a, **kw: None)
    monkeypatch.setattr("tools.browser_runtime.configuration",
                        lambda: {"engine": "browser_skill"})
    monkeypatch.setattr(run, "_resume_pdf_path", lambda *a: "")
    monkeypatch.setattr(run, "_github_context", lambda: "")
    stores.answer_bank = SimpleNamespace(all_facts=lambda *a: {})
    stores.secrets = None
    browser = AsyncMock()
    monkeypatch.setattr(browser_apply, "apply", browser)
    _replace_row(stores, apply_requested_at="2026-01-01T00:00:00+00:00",
                 reviewed_guard_generation=old_gen,
                 resume_s3_key=OLD_PDF, resume_tex_key=OLD_PDF[:-4] + ".tex",
                 resume_seed="synthetic-seed")
    _fake_reads(monkeypatch, [_source(CHANGED)])
    outcome = asyncio.run(run._apply_direct(PK, stores))
    assert outcome["reason"] == "posting_guard_review_required"
    browser.assert_not_awaited()
    row = _row(stores)
    assert row["status"] == "tailored"
    assert row["fail_kind"] == "posting_guard_review_required"
    return old_gen


# --- success -----------------------------------------------------------------

def test_retry_rereads_current_source_and_prepares_review_only(
        stores, monkeypatch, agent_doubles, forbidden):
    from agent import graph

    old_gen = _refused_row(stores, monkeypatch)
    forbidden.armed = True
    calls = _fake_reads(monkeypatch, [_source(CHANGED)])

    outcome = run.retry_job(PK, stores)

    assert outcome["result"] == "prepared"
    # one forced matched-source read despite the long cached JD
    assert calls == [(URL, "jd")]
    assert len(TEXT) > 400
    assert agent_doubles.agents == [graph.review_agent]
    # the fresh JD, not the stale captured text, is what tailoring receives
    assert agent_doubles.session_inputs == [_source(CHANGED)["text"]]
    assert TEXT not in agent_doubles.session_inputs[0]
    row = _row(stores)
    assert row["status"] == "tailored"
    assert row["gate_reason"] == "approval"
    assert row["jd_text"] == _source(CHANGED)["text"]
    assert pg.valid(row["jd_text"], row["posting_guard"])
    assert row["posting_guard"]["generation"] != old_gen
    assert row.get("reviewed_guard_generation") != row["posting_guard"]["generation"]
    # old authority / refusal evidence gone, fresh review required
    assert not row.get("apply_requested_at")
    assert not row.get("fail_kind")
    assert row["resume_s3_key"].startswith("resumes/" + PK + "-")
    assert row["resume_s3_key"].endswith(".pdf")
    assert stores.artifacts.get(row["resume_s3_key"]) == b"%PDF-synthetic"
    assert forbidden == []
    assert not stores.tracking.r.exists(f"lock:job:{PK}")
    assert ApplyQueue(stores.tracking.r).pending() == []


# --- refusals before any state change ---------------------------------------

def _snapshot(stores):
    return json.dumps(_row(stores), sort_keys=True, default=str)


def _queue_item(stores, **extra):
    item = {"pk": PK, "company": "example-co", "attempts": 0, "not_before": 0.0,
            "history": [], "queued_at": 1.0, "priority": False, "requested": True}
    item.update(extra)
    r = stores.tracking.r
    r.sadd("applyq:companies", "example-co")
    r.rpush("applyq:co:example-co", json.dumps(item))


def _hold(stores):
    submit_hold.mark(PK, tracking=stores.tracking)


def _possible_submission(stores):
    _replace_row(stores, possible_submission=True)


def _confirmation(stores):
    _replace_row(stores, confirmation_id="synthetic")


def _review(stores):
    _replace_row(stores, gate_reason="submit_review")


def _lease(stores):
    stores.tracking.r.sadd("applyq:inflight:pks", PK)


def _claim_held(stores):
    stores.tracking.r.set(f"lock:job:{PK}", "1")


def _pending(stores):
    _queue_item(stores)


def _pending_other_company_list(stores):
    # Present even though the companies index does not list it.
    stores.tracking.r.rpush("applyq:co:elsewhere", json.dumps(
        {"pk": PK, "company": "Elsewhere", "queued_at": 2.0, "requested": True}))


def _pending_malformed(stores):
    stores.tracking.r.sadd("applyq:companies", "example-co")
    stores.tracking.r.rpush("applyq:co:example-co", "not json")


def _unattributable(entry):
    def setup(stores):
        stores.tracking.r.sadd("applyq:companies", "example-co")
        stores.tracking.r.rpush("applyq:co:example-co", json.dumps(entry))
    return setup


def _review_evidence(**fields):
    def setup(stores):
        _replace_row(stores, **fields)
    return setup


@pytest.mark.parametrize("setup,result,reason", [
    (_hold, "failed", "uncertain"),
    (_possible_submission, "failed", "uncertain"),
    (_confirmation, "failed", "uncertain"),
    (_review, "gated", "submit_review"),
    (_lease, "already_running", "apply_in_flight"),
    (_claim_held, "already_running", None),
    (_pending, "refused", "apply_queue_pending"),
    (_pending_other_company_list, "refused", "apply_queue_pending"),
    (_pending_malformed, "refused", "apply_queue_unknown"),
    (_unattributable({"company": "example-co", "requested": True}),
     "refused", "apply_queue_unknown"),
    (_unattributable({"pk": None}), "refused", "apply_queue_unknown"),
    (_unattributable({"pk": ""}), "refused", "apply_queue_unknown"),
    (_unattributable({"pk": 7}), "refused", "apply_queue_unknown"),
    (_unattributable(["not", "an", "object"]), "refused", "apply_queue_unknown"),
    # review / submission evidence refuses even though gate_reason stays "approval"
    (_review_evidence(review_state="pending", gate_generation="g" * 32),
     "gated", "submit_review"),
    (_review_evidence(review_state="claimed"), "gated", "submit_review"),
    (_review_evidence(review_state="unrecognised-state"), "gated", "submit_review"),
    (_review_evidence(review_state="rejected", gate_generation="g" * 32),
     "gated", "submit_review"),
    (_review_evidence(gate_generation="g" * 32), "gated", "submit_review"),
    (_review_evidence(submit_hold=True), "failed", "uncertain"),
    (_review_evidence(submit_hold_state="held"), "failed", "uncertain"),
])
def test_protected_or_unproven_state_refuses_without_any_effect(
        stores, monkeypatch, forbidden, setup, result, reason):
    _refused_row(stores, monkeypatch)
    forbidden.armed = True
    setup(stores)
    queue_before = {k: stores.tracking.r.lrange(k, 0, -1)
                    for k in stores.tracking.r.scan_iter(match="applyq:*")
                    if stores.tracking.r.type(k) == "list"}
    row_before = _snapshot(stores)
    calls = _fake_reads(monkeypatch, [])
    monkeypatch.setattr(run, "_session_service",
                        lambda: pytest.fail("no session work on refusal"))

    outcome = run.retry_job(PK, stores)
    gate = run.retry_precheck(PK, stores)

    assert outcome["result"] == result
    assert outcome.get("reason") == reason
    assert gate is not None and gate["result"] == result
    assert calls == []
    assert _snapshot(stores) == row_before
    assert queue_before == {k: stores.tracking.r.lrange(k, 0, -1)
                            for k in queue_before}
    assert forbidden == []


@pytest.mark.parametrize("fields", [
    {"fail_kind": ""}, {"fail_kind": "no_jd"}, {"status": "needs_human"},
])
def test_other_rows_are_not_a_general_tailored_restart(stores, monkeypatch, fields):
    _refused_row(stores, monkeypatch)
    _replace_row(stores, **fields)
    calls = _fake_reads(monkeypatch, [])
    before = _snapshot(stores)
    outcome = run.retry_job(PK, stores)
    assert outcome["result"] == "already_done"
    assert calls == [] and _snapshot(stores) == before


# --- fail closed after admission, refusal restored ---------------------------

@pytest.mark.parametrize("read,reason", [
    (jd.PostingReadUnavailable("synthetic outage"), "posting_source_unavailable"),
    (_source(CHANGED, url="https://jobs.ashbyhq.com/other/posting"),
     "posting_source_unavailable"),
    (_source(TEXT), "posting_guard_generation_unchanged"),
])
def test_unusable_current_source_restores_the_closed_refusal(
        stores, monkeypatch, agent_doubles, forbidden, read, reason):
    _refused_row(stores, monkeypatch)
    forbidden.armed = True
    before = _row(stores)
    calls = _fake_reads(monkeypatch, [read])

    outcome = run.retry_job(PK, stores)

    assert outcome["result"] == "refused" and outcome["reason"] == reason
    assert calls == [(URL, "jd")]
    assert agent_doubles.agents == []
    after = _row(stores)
    assert after["status"] == "tailored"
    assert after["fail_kind"] == "posting_guard_review_required"
    assert after["resume_s3_key"] == before["resume_s3_key"]
    assert after["posting_guard"]["generation"] == before["posting_guard"]["generation"]
    assert forbidden == []
    assert not stores.tracking.r.exists(f"lock:job:{PK}")


@pytest.mark.parametrize("failing", ["invalidate", "publish"])
def test_post_read_conflict_never_leaves_fresh_posting_beside_old_pdf_or_request(
        stores, monkeypatch, agent_doubles, forbidden, failing):
    _refused_row(stores, monkeypatch)
    forbidden.armed = True
    before = _row(stores)
    _fake_reads(monkeypatch, [_source(CHANGED)])
    if failing == "invalidate":
        real = run._reader_patch

        def conflicting(pk, row, stores_, changes):
            if "apply_requested_at" in changes:
                return False, row
            return real(pk, row, stores_, changes)

        monkeypatch.setattr(run, "_reader_patch", conflicting)
    else:
        monkeypatch.setattr(run, "_commit_posting_meta", lambda *a, **kw: None)

    outcome = run.retry_job(PK, stores)

    assert outcome["result"] == "conflict"
    after = _row(stores)
    # the fresh JD / guard were never published onto the old row
    assert after["jd_text"] == before["jd_text"]
    assert after["posting_guard"] == before["posting_guard"]
    if failing == "invalidate":
        assert after["apply_requested_at"] == before["apply_requested_at"]
        assert after["resume_s3_key"] == before["resume_s3_key"]
    else:
        # old PDF and request were invalidated BEFORE any publication
        assert not after.get("apply_requested_at") and not after["resume_s3_key"]
    assert agent_doubles.agents == [] and forbidden == []
    assert not stores.tracking.r.exists(f"lock:job:{PK}")


def test_prepared_row_with_old_request_still_enqueues_nothing(
        stores, monkeypatch, agent_doubles, forbidden):
    _refused_row(stores, monkeypatch)
    forbidden.armed = True
    _fake_reads(monkeypatch, [_source(CHANGED)])
    # a request arrives while the review-only agent runs
    original = agent_doubles.run_async

    async def late_request(self, **kw):
        _replace_row(stores, apply_requested_at="2026-02-02T00:00:00+00:00")
        async for event in original(self, **kw):
            yield event

    monkeypatch.setattr(agent_doubles, "run_async", late_request)
    outcome = run.retry_job(PK, stores)
    assert outcome["result"] == "prepared"
    assert forbidden == []
    assert _row(stores)["status"] == "tailored"


def test_unavailable_queue_inspection_refuses_before_any_state_change(
        stores, monkeypatch, forbidden):
    _refused_row(stores, monkeypatch)
    forbidden.armed = True

    class BrokenInspector:
        def in_flight(self):
            return set()

        def has_pending(self, pk):
            raise RuntimeError("synthetic inspection outage")

    stores.queue = BrokenInspector()
    before = _snapshot(stores)
    calls = _fake_reads(monkeypatch, [])
    outcome = run.retry_job(PK, stores)
    assert outcome["result"] == "refused" and outcome["reason"] == "apply_queue_unknown"
    assert run.retry_precheck(PK, stores)["reason"] == "apply_queue_unknown"
    assert calls == [] and _snapshot(stores) == before and forbidden == []


def test_backend_without_inspectable_hold_or_queue_is_not_absence_proof(monkeypatch):
    """`submit_hold.blocked` is False with no Redis client; that is not proof."""
    row = {"pk": PK, "company": "example-co", "job_id": "job-1", "ats": "ashby",
           "jd_url": URL, "status": "tailored", "jd_text": TEXT,
           "fail_kind": "posting_guard_review_required", "gate_reason": "approval"}

    class NoRedis:
        r = None

        def get(self, pk):
            return dict(row)

        get_current = get

        def update_if_status(self, *a, **kw):
            pytest.fail("no write is allowed on an unprovable backend")

        def set_status(self, *a, **kw):
            pytest.fail("no write is allowed on an unprovable backend")

    monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
    stores = SimpleNamespace(tracking=NoRedis())
    calls = _fake_reads(monkeypatch, [])
    outcome = run.retry_job(PK, stores)
    assert outcome["result"] == "refused"
    assert outcome["reason"] == "apply_lease_unknown"
    assert run.retry_precheck(PK, stores)["reason"] == "apply_lease_unknown"
    assert calls == []


# --- HTTP ---------------------------------------------------------------------

def _route(monkeypatch, stores):
    import server

    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    return next(r.endpoint for r in server.create_app().routes
                if getattr(r, "path", None) == "/actions/retry/{pk}")


def test_http_reports_actual_refusal_and_schedules_only_eligible(
        stores, monkeypatch):
    from fastapi import BackgroundTasks

    _refused_row(stores, monkeypatch)
    route = _route(monkeypatch, stores)

    background = BackgroundTasks()
    ok = route(PK, background)
    assert ok == {"ok": True, "status": "retrying"}
    assert [t.func for t in background.tasks] == [run.retry_job]

    _pending(stores)
    background = BackgroundTasks()
    refused = route(PK, background)
    assert refused["ok"] is False and refused["status"] == "refused"
    assert refused["reason"] == "apply_queue_pending"
    assert "retrying" not in json.dumps(refused)
    assert background.tasks == []

    _replace_row(stores, fail_kind="")
    background = BackgroundTasks()
    done = route(PK, background)
    assert done["ok"] is False and done["status"] == "already_done"
    assert background.tasks == []


# --- read-only pending inspector ---------------------------------------------

def test_has_pending_is_exact_uncapped_and_read_only():
    r = fakeredis.FakeRedis(decode_responses=True)
    q = ApplyQueue(r)
    for i in range(5100):
        assert q.put(f"co{i % 3}#job-{i}", f"co{i % 3}")
    assert q.put(PK, "example-co", requested=True)
    assert PK not in {item["pk"] for item in q.pending()}  # capped view misses it
    snapshot = {k: r.lrange(k, 0, -1) for k in r.scan_iter(match="applyq:co:*")}
    assert q.has_pending(PK) is True
    assert q.has_pending("example-co#absent") is False
    assert snapshot == {k: r.lrange(k, 0, -1) for k in r.scan_iter(match="applyq:co:*")}
    r.rpush("applyq:co:example-co", "garbage")
    assert q.has_pending("example-co#absent") is None


@pytest.mark.parametrize("entry", [
    {"company": "example-co", "requested": True}, {"pk": None}, {"pk": ""},
    {"pk": 5}, ["list"], "string", 7, None,
])
def test_has_pending_fails_closed_on_unattributable_entries(entry):
    r = fakeredis.FakeRedis(decode_responses=True)
    q = ApplyQueue(r)
    r.sadd("applyq:companies", "example-co")
    r.rpush("applyq:co:example-co", json.dumps(entry))
    before = r.lrange("applyq:co:example-co", 0, -1)
    assert q.has_pending(PK) is None
    # a definite match still wins over an unattributable neighbour
    r.rpush("applyq:co:example-co", json.dumps({"pk": PK}))
    assert q.has_pending(PK) is True
    assert r.lrange("applyq:co:example-co", 0, -1)[:1] == before
