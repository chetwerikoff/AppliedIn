"""Issue #301 offline queue/agent review recovery gates.

Only synthetic tracker/queue fakes: no browser, Redis, network, credential,
real user, company, job, or application is accessed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import run as agent_run
from tools import submit_review


PK = "example-co#synthetic"


class Queue:
    def __init__(self):
        self.retries = []
        self.done_calls = []

    def retry(self, item, reason):
        self.retries.append((item, reason))

    def done(self, item):
        self.done_calls.append(item)


class Tracking:
    def __init__(self, row):
        self.row = row
        self.status_calls = []

    def get(self, pk):
        assert pk == PK
        return dict(self.row)

    def set_status(self, *args, **kwargs):
        self.status_calls.append((args, kwargs))


@pytest.mark.parametrize("review_state", [
    "pending", "claimed", "rejected", "expired",
    "session_lost", "restart", "invalidated",
])
@pytest.mark.parametrize("exception_path", [False, True])
def test_no_implicit_queue_retry_after_review_publication(
        monkeypatch, review_state, exception_path):
    row = {
        "pk": PK, "company": "example-co", "status": "needs_human",
        "gate_reason": "submit_review", "review_state": review_state,
        "gate_generation": "synthetic-opaque-value",
        "possible_submission": False,
    }
    tracking = Tracking(row)
    queue = Queue()
    monkeypatch.setattr(agent_run, "make_stores",
                        lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(agent_run, "_apply_direct", lambda *a: object())

    if exception_path:
        def attempted(_):
            raise RuntimeError("synthetic injected post-publication crash")
        monkeypatch.setattr(agent_run, "_run", attempted)
    else:
        monkeypatch.setattr(agent_run, "_run",
                            lambda _: {"result": "failed",
                                       "reason": "transient_browser_unavailable"})
    result = agent_run.run_queued({"pk": PK}, queue)
    assert queue.retries == []
    assert queue.done_calls == [{"pk": PK}]
    assert tracking.status_calls == []
    assert submit_review.guarded_row(tracking.get(PK))
    assert result["result"] in ("gated", "failed")
    if exception_path:
        assert result["reason"] == "submit_review"


def test_review_is_not_an_automatic_success_or_real_submission():
    for state in ["pending", "claimed", "rejected", "expired", "session_lost"]:
        row = {"pk": PK, "status": "needs_human",
               "gate_reason": "submit_review", "review_state": state}
        assert submit_review.guarded_row(row)
    for row in [{"gate_reason": "approval"},
                {"gate_reason": "submit_uncertain",
                 "possible_submission": True}]:
        assert not submit_review.guarded_row(row)
    for malformed in [None, "", "unexpected", {"not": "a state"}]:
        assert submit_review.guarded_row(
            {"gate_reason": "submit_review", "review_state": malformed})


def closed_review(state="rejected"):
    return {
        "pk": PK, "company": "example-co", "job_id": "synthetic",
        "jd_url": "https://example.test/jobs/synthetic", "status": "needs_human",
        "gate_reason": "submit_review", "review_state": state,
        "gate_generation": "synthetic-generation-1234567890",
        "possible_submission": False, "confirmation_id": None,
        "resume_s3_key": "obsolete-synthetic.pdf", "resume_tex_key": "obsolete.tex",
        "resume_seed": "old-seed", "jd_read_revision": 1,
    }


@pytest.mark.parametrize("review_state", ["rejected", "expired", "restart", "session_lost"])
def test_explicit_terminal_review_retry_destroys_old_generation(
        monkeypatch, review_state):
    """The human Retry action starts new preparation, never the held browser."""
    from core import events
    row = closed_review(review_state)
    actions = []
    class Store:
        r = None

        def get(self, pk):
            assert pk == PK
            return dict(row)

        get_current = get

    tracking = Store()
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(submit_review, "_HOLDERS", {})
    monkeypatch.setattr(agent_run, "_claim", lambda *a: True)
    monkeypatch.setattr(agent_run, "_claimed", lambda *a: False)
    monkeypatch.setattr(agent_run, "_active_apply_lease", lambda *a: False)
    monkeypatch.setattr(agent_run, "_release", lambda *a: actions.append("release"))
    monkeypatch.setattr(agent_run, "_reset_session", lambda pk: ("reset", pk))
    monkeypatch.setattr(agent_run, "_run_job_async",
                        lambda pk, admitted, stores, *, prepare_only:
                        ("prepare", prepare_only))
    monkeypatch.setattr(events, "emit", lambda *a, **kw: None)

    def fake_patch(pk, *, states, status, generation, changes):
        assert pk == PK and status == "needs_human"
        assert row["review_state"] in states
        assert generation == row["gate_generation"]
        row.update({k: getattr(v, "value", v) for k, v in changes.items()})
        return True

    monkeypatch.setattr(submit_review, "_patch", fake_patch)
    def run_once(operation):
        actions.append(operation)
        if operation == ("prepare", True):
            return {"result": "prepared", "pk": PK}
        return None

    monkeypatch.setattr(agent_run, "_run", run_once)
    refused = agent_run.retry_job(PK, stores)
    assert refused["reason"] == "submit_review"
    assert actions == []
    done = agent_run.retry_job(PK, stores, explicit_review_restart=True)
    assert done["result"] == "prepared"
    assert actions == [("reset", PK), ("prepare", True), "release"]
    assert row["gate_reason"] == ""
    assert row["gate_generation"] is None and row["review_state"] is None
    assert row["resume_s3_key"] == "" and row["resume_tex_key"] == ""
    assert row["resume_seed"] == ""
    assert row["status"] == "tailoring"


@pytest.mark.parametrize("review_state", ["pending", "claimed", "invalidated"])
def test_nonterminal_review_never_admits_manual_retry(monkeypatch, review_state):
    """An owner click cannot consume a pending/claimed or ambiguous generation."""
    row = closed_review(review_state)
    tracking = SimpleNamespace(r=None, get=lambda pk: dict(row),
                               get_current=lambda pk: dict(row))
    monkeypatch.setattr(submit_review, "_HOLDERS", {})
    stores = SimpleNamespace(tracking=tracking)
    assert not submit_review.manual_restart_allowed(PK, row, tracking=tracking)
    assert agent_run.retry_job(PK, stores, explicit_review_restart=True) == {
        "result": "gated", "pk": PK, "reason": "submit_review"}


def test_closed_review_recovery_refuses_active_holder_or_submission_evidence(monkeypatch):
    from tools import submit_hold
    row = closed_review()
    tracking = SimpleNamespace(r=None, get_current=lambda pk: dict(row))
    monkeypatch.setattr(submit_review, "_HOLDERS", {PK: object()})
    assert not submit_review.manual_restart_allowed(PK, row, tracking=tracking)
    monkeypatch.setattr(submit_review, "_HOLDERS", {})
    for key, value in (("possible_submission", True),
                       ("confirmation_id", "synthetic-confirmed")):
        guarded = dict(row, **{key: value})
        assert not submit_review.manual_restart_allowed(
            PK, guarded, tracking=tracking)
    monkeypatch.setattr(submit_hold, "blocked", lambda *a, **kw: True)
    assert not submit_review.manual_restart_allowed(PK, row, tracking=tracking)
