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
