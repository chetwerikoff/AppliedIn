"""Postings that can only be read in a browser are read a batch at a time.

Meta's postings are unreadable over HTTP — 400 to a disguised request, a JS
shell to a bare one — so each one needs a real browser. Reading them one
session per posting cost ~5 minutes × 55 rows, serialised, and every session
had to wait for any application in flight. The cost is session start-up, not
page reads, so one session reads several.

Two rules keep a batch honest, both in code:
  * an entry is accepted only if its URL was one we asked for — a session that
    attributes one posting's text to another's URL is worse than one that fails;
  * a batch that comes back malformed loses only that batch, never the sweep.
"""

from __future__ import annotations

import asyncio

from agent.run import _unreadable, prefetch_browser_jds
from tools import jd

URLS = [f"https://www.metacareers.com/profile/job_details/{i}" for i in range(1, 9)]
GOOD = "Responsibilities: build distributed systems. " * 12


def _fake_run_task(reports):
    calls = []

    async def run_task(task, *, report_key, timeout_s, kind, **kw):
        calls.append({"task": task, "kind": kind})
        return reports.pop(0), ""

    return run_task, calls


def test_batches_are_bounded_and_yield_to_applies(monkeypatch):
    run_task, calls = _fake_run_task([
        {"postings": [{"url": u, "title": "SWE", "description": GOOD} for u in URLS[:6]]},
        {"postings": [{"url": u, "title": "SWE", "description": GOOD} for u in URLS[6:]]},
    ])
    # A fake session does not bypass the separate CLI availability precondition.
    monkeypatch.setattr(jd, "available", lambda: (True, ""))
    monkeypatch.setattr(jd, "run_task", run_task)

    got = jd.read_postings(URLS, batch=6)

    assert len(calls) == 2
    assert all(c["kind"] == "jd_sweep" for c in calls)
    assert set(got) == set(URLS)
    assert all(u in calls[0]["task"] for u in URLS[:6])


def test_an_unrequested_url_is_dropped(monkeypatch):
    run_task, _ = _fake_run_task([{"postings": [
        {"url": URLS[0], "description": GOOD},
        {"url": "https://www.metacareers.com/profile/job_details/999", "description": GOOD},
    ]}])
    # A fake session does not bypass the separate CLI availability precondition.
    monkeypatch.setattr(jd, "available", lambda: (True, ""))
    monkeypatch.setattr(jd, "run_task", run_task)

    got = jd.read_postings(URLS[:2], batch=6)

    assert set(got) == {URLS[0]}


def test_a_thin_description_is_not_accepted(monkeypatch):
    run_task, _ = _fake_run_task([{"postings": [
        {"url": URLS[0], "description": "Sorry, something went wrong."},
        {"url": URLS[1], "description": GOOD},
    ]}])
    # A fake session does not bypass the separate CLI availability precondition.
    monkeypatch.setattr(jd, "available", lambda: (True, ""))
    monkeypatch.setattr(jd, "run_task", run_task)

    got = jd.read_postings(URLS[:2], batch=6)

    assert set(got) == {URLS[1]}


def test_a_broken_batch_loses_only_itself(monkeypatch):
    async def run_task(task, **kw):
        if URLS[0] in task:
            return {}, "The Chrome session ended without a structured result"
        return {"postings": [{"url": u, "description": GOOD} for u in URLS[6:]]}, ""

    # A fake session does not bypass the separate CLI availability precondition.
    monkeypatch.setattr(jd, "available", lambda: (True, ""))
    monkeypatch.setattr(jd, "run_task", run_task)

    got = jd.read_postings(URLS, batch=6)

    assert set(got) == set(URLS[6:])


class _Tracking:
    def __init__(self, rows):
        self.rows = {r["pk"]: dict(r) for r in rows}

    def get(self, pk):
        return dict(self.rows[pk])

    def set_status(self, pk, status, **kw):
        self.rows[pk]["status"] = getattr(status, "value", status)
        self.rows[pk].update(kw)

    def update_if_status(self, pk, expected_status, updates, *, expected_reader):
        fixed = ("jd_read_attempts", "jd_read_retry_at", "jd_read_revision",
                 "jd_text", "jd_read_prepare_only")
        row = self.rows.get(pk)
        if row is None or row.get("status") != getattr(expected_status, "value", expected_status):
            return False
        if any((field in row) != (field in expected_reader) or
               (field in expected_reader and row[field] != expected_reader[field])
               for field in fixed):
            return False
        row.update({k: getattr(v, "value", v) if k == "status" else v
                    for k, v in updates.items()})
        return True


class _Stores:
    def __init__(self, rows):
        self.tracking = _Tracking(rows)


def test_prefetch_fills_only_the_rows_that_need_it(monkeypatch):
    rows = [
        {"pk": "meta#1", "company": "Meta", "status": "found", "jd_url": URLS[0], "jd_text": ""},
        {"pk": "meta#2", "company": "Meta", "status": "found", "jd_url": URLS[1], "jd_text": GOOD},
        {"pk": "stripe#1", "company": "Stripe", "status": "found",
         "jd_url": "https://stripe.com/jobs/1", "jd_text": ""},
    ]
    stores = _Stores(rows)
    asked = []

    def read_postings(urls, **kw):
        asked.extend(urls)
        return {u: GOOD for u in urls}, set()

    monkeypatch.setattr(jd, "read_postings", read_postings)
    monkeypatch.setattr("agent.run._browser_companies", lambda: {"meta"})

    n = prefetch_browser_jds(rows, stores)

    assert asked == [URLS[0]], "only the Meta row with no text should be read"
    assert n == 1
    assert not _unreadable(stores.tracking.rows["meta#1"]["jd_text"])
    assert stores.tracking.rows["meta#1"]["status"] == "found"
    assert stores.tracking.rows["stripe#1"]["jd_text"] == ""


def test_the_sweep_prefetches_before_evaluating():
    import inspect

    import daemon

    src = inspect.getsource(daemon.process_backlog_once)
    assert "prefetch_browser_jds(found, stores)" in src
    assert src.index("prefetch_browser_jds(found, stores)") < src.index("for row in found:")


def test_a_removed_posting_is_reported_as_gone(monkeypatch):
    """The session read Meta's "this job is no longer available" page. That is
    an answer, not a failure — the row should close as job_gone rather than be
    re-read on every sweep."""
    run_task, _ = _fake_run_task([{"postings": [
        {"url": URLS[0], "gone": True, "description": "Sorry, this job is no longer available."},
        {"url": URLS[1], "description": GOOD},
    ]}])
    # A fake session does not bypass the separate CLI availability precondition.
    monkeypatch.setattr(jd, "available", lambda: (True, ""))
    monkeypatch.setattr(jd, "run_task", run_task)

    got, gone = jd.read_postings(URLS[:2], batch=6, with_gone=True)

    assert set(got) == {URLS[1]}
    assert gone == {URLS[0]}


def test_prefetch_closes_gone_rows(monkeypatch):
    rows = [
        {"pk": "meta#1", "company": "Meta", "status": "found", "jd_url": URLS[0], "jd_text": ""},
        {"pk": "meta#2", "company": "Meta", "status": "found", "jd_url": URLS[1], "jd_text": ""},
    ]
    stores = _Stores(rows)
    monkeypatch.setattr(jd, "read_postings",
                        lambda urls, **kw: ({URLS[1]: GOOD}, {URLS[0]}))
    monkeypatch.setattr("agent.run._browser_companies", lambda: {"meta"})

    prefetch_browser_jds(rows, stores)

    assert stores.tracking.rows["meta#1"]["status"] == "job_gone"
    assert stores.tracking.rows["meta#2"]["status"] == "found"
    assert stores.tracking.rows["meta#2"]["jd_text"] == GOOD


def test_prefetch_uses_persisted_eligibility_not_stale_input(monkeypatch):
    from datetime import UTC, datetime, timedelta

    pk = "example-co#job-1"
    url = "https://example.test/jobs/1"
    rows = [{"pk": pk, "company": "example-co", "status": "found",
             "jd_url": url, "jd_text": ""}]
    stores = _Stores(rows)
    supplied = dict(rows[0])
    stores.tracking.rows[pk]["jd_read_attempts"] = 1
    stores.tracking.rows[pk]["jd_read_retry_at"] = (
        datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    monkeypatch.setattr("agent.run._browser_companies", lambda: {"example-co"})
    monkeypatch.setattr(jd, "read_postings",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("pending read")))
    assert prefetch_browser_jds([supplied], stores) == 0
    assert stores.tracking.rows[pk]["jd_text"] == ""


def test_prefetch_loses_both_old_fill_and_gone_to_new_found_generation(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import fakeredis

    from agent import run
    from core.models import Status
    from core.storage.local import RedisTracking

    for outcome in ("fill", "gone"):
        pk = "example-co#job-1"
        url = "https://example.test/jobs/1"
        tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
        tracking.set_status(pk, Status.FOUND, company="example-co",
                            jd_url=url, jd_text="Test role", attempts=0)
        stores = SimpleNamespace(tracking=tracking)
        supplied = tracking.get(pk)
        monkeypatch.setattr(run, "_browser_companies", lambda: {"example-co"})
        monkeypatch.setattr("core.events.emit", lambda *a, **kw: None)
        monkeypatch.setattr(run, "_session_service",
                            lambda: (_ for _ in ()).throw(AssertionError("no ADK")))
        from tools.jd import PostingReadUnavailable

        async def unavailable(row, **kw):
            raise PostingReadUnavailable("synthetic reader unavailable")

        monkeypatch.setattr(run, "_jd_text", unavailable)

        def delayed_batch(urls, *, with_gone):
            # Actual runner wins a full FOUND -> TAILORING -> FOUND cycle.
            assert run.run_job(pk, stores)["result"] == "deferred"
            if outcome == "fill":
                return {url: GOOD}, set()
            return {}, {url}

        monkeypatch.setattr(jd, "read_postings", delayed_batch)
        assert prefetch_browser_jds([supplied], stores) == 0
        current = tracking.get(pk)
        assert current["status"] == "found"
        assert current["jd_read_attempts"] == 1
        assert current["jd_read_revision"] == 2
        assert current["jd_read_retry_at"]
        assert current["jd_text"] == "Test role"
        assert tracking.status_counts().get("job_gone", 0) == 0


def test_prefetch_storage_error_does_not_close_or_fill(monkeypatch, caplog):
    pk = "example-co#job-1"
    url = "https://example.test/jobs/1"
    stores = _Stores([{"pk": pk, "company": "example-co", "status": "found",
                       "jd_url": url, "jd_text": ""}])
    monkeypatch.setattr("agent.run._browser_companies", lambda: {"example-co"})
    monkeypatch.setattr(jd, "read_postings", lambda urls, **kw: ({url: GOOD}, set()))

    def storage_failure(*args, **kwargs):
        raise RuntimeError("synthetic reader storage fault")

    monkeypatch.setattr(stores.tracking, "update_if_status", storage_failure)
    assert prefetch_browser_jds([stores.tracking.get(pk)], stores) == 0
    assert stores.tracking.get(pk)["status"] == "found"
    assert "storage" in caplog.text.lower()
