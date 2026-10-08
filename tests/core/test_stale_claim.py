"""A job lock that outlives its run must not make a `found` row unrunnable.

Retry on a job mid-run resets it to `found` and then bounces off the run's own
lock. When a restart then kills that run, startup recovery only looked at
`tailoring` rows, so the row sat at `found`, scored 8, refusing every Apply as
"already being processed" for the lock's full half hour — silently."""

from __future__ import annotations

import fakeredis
import pytest

import daemon
from agent import run as run_mod


class _Tracking:
    def __init__(self, rows, r):
        self.rows = {row["pk"]: dict(row) for row in rows}
        self.r = r

    def all(self):
        return [dict(row) for row in self.rows.values()]

    def get(self, pk):
        row = self.rows.get(pk)
        return dict(row) if row else None

    def set_status(self, pk, status, **kw):
        self.rows[pk]["status"] = getattr(status, "value", status)
        self.rows[pk].update(kw)


class _Queue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, *_a, **_k):
        self.enqueued.append((_a, _k))


class _Stores:
    def __init__(self, rows, r):
        self.tracking = _Tracking(rows, r)
        self.queue = _Queue()
        self.apply_queue = "apply"


@pytest.fixture
def stores():
    r = fakeredis.FakeRedis(decode_responses=True)
    s = _Stores([{"pk": "openai deploy co#1", "company": "OpenAI Deploy Co",
                  "status": "found", "match_score": 8}], r)
    assert run_mod._claim("openai deploy co#1", s)
    return s


def test_startup_frees_a_claim_left_on_a_found_row(stores):
    daemon._recover_orphans(stores)
    assert run_mod._claim("openai deploy co#1", stores)


def test_retry_does_not_wipe_a_row_someone_is_still_running(stores, monkeypatch):
    stores.tracking.rows["openai deploy co#1"]["status"] = "tailoring"
    said = []
    monkeypatch.setattr("core.events.emit", lambda kind, **kw: said.append((kind, kw)))
    out = run_mod.retry_job("openai deploy co#1", stores)
    assert out["result"] == "already_running"
    assert stores.tracking.rows["openai deploy co#1"]["status"] == "tailoring"
    assert any("still working" in (kw.get("detail") or "") for _k, kw in said)


def test_a_refused_run_tells_the_board_why(stores, monkeypatch):
    said = []
    monkeypatch.setattr("core.events.emit", lambda kind, **kw: said.append((kind, kw)))
    out = run_mod.run_job("openai deploy co#1", stores)
    assert out["result"] == "already_running"
    assert said and said[-1][0] == "response"


@pytest.mark.parametrize("marked", [False, True])
def test_startup_orphan_recovery_keeps_a_possible_submission_held(marked):
    client = fakeredis.FakeRedis(decode_responses=True)
    pk = "example-co#synthetic-1"
    stores = _Stores([{"pk": pk, "company": "example-co",
                       "status": "submitting",
                       "last_button": "Submit",
                       "last_url": "https://example.test/job/1"}], client)
    if marked:
        from tools.submit_hold import mark
        mark(pk, tracking=stores.tracking)
    assert run_mod._claim(pk, stores)
    daemon._recover_orphans(stores)

    row = stores.tracking.get(pk)
    assert not client.exists(f"lock:job:{pk}")
    if marked:
        assert row["status"] == "needs_human"
        assert row["gate_reason"] == "submit_uncertain"
        assert row["fail_kind"] == "uncertain"
        assert row["fail_reason"] == (
            "possible submission; check the employer portal before retrying")
        assert stores.queue.enqueued == []
        assert row["last_button"] == "Submit"
    else:
        assert row["status"] == "tailored"
        assert len(stores.queue.enqueued) == 1
        assert stores.queue.enqueued[0][0][1] == {"pk": pk}
