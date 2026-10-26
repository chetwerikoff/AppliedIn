"""Handler unit tests: watermark, backfill cap, and dedup-aware enqueue.

The adapter and Queue are faked so the test exercises discovery logic, not HTTP.
"""

from __future__ import annotations

import httpx
import pytest
from core.models import JobRecord
from core.storage.tracking import TrackingStore
from discovery import handler as h
from discovery.watchlist import CompanyConfig, Preferences
from tools import seen as _seen


@pytest.fixture(autouse=True)
def _isolated_seen(tmp_path, monkeypatch):
    """Keep the seen-URL ledger out of the developer's real .local/.

    These tests call discover_company, which marks every enqueued URL as seen.
    Pointed at the real file it wrote the fixtures' placeholder URL there
    permanently, after which every run filtered all its own jobs and the suite
    failed for good — while also mutating live pipeline state.
    """
    monkeypatch.setattr(_seen, "_path", lambda: tmp_path / "seen.json")


@pytest.fixture(autouse=True)
def _offline_relevance(monkeypatch):
    """Pass every fetched job through the relevance screen without a model call.

    discover_company runs the real screen, which calls the configured LLM. With
    no reachable model that call failed, and the failure path recorded an
    llm_error flag in the developer's real Redis, so the running dashboard
    showed a "Missing credentials" banner that no live request had caused.
    These tests cover caps and dedup, not screening.
    """
    monkeypatch.setattr(h, "relevant", lambda jobs, prefs, **_: list(jobs))


class FakeQueue:
    def __init__(self):
        self.messages = []

    def enqueue(self, url, body):
        self.messages.append((url, body))
        return "mid"


def _jobs(n, start=0):
    return [
        JobRecord(
            company="Acme", job_id=(jid := f"00000000-0000-4000-8000-{i:012x}"),
            title="Engineer", jd_url=f"https://jobs.ashbyhq.com/acme/{jid}",
            jd_text="python", location="Remote", ats="ashby",
        )
        for i in range(start, start + n)
    ]


def _company():
    return CompanyConfig(name="Acme", ats="ashby", board="acme",
                         careers_url="https://jobs.ashbyhq.com/acme")


def test_first_run_applies_backfill_cap(applications_table, monkeypatch):
    monkeypatch.setattr(h, "ADAPTERS", {"ashby": type("A", (), {"fetch": lambda self, c, cl: _jobs(30)})()})
    tracking = TrackingStore(applications_table)
    queue = FakeQueue()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    # max_new_per_run=0 disables the per-run top-N cap, so this exercises the
    # first-run BACKFILL cap on its own.
    prefs = Preferences(max_new_per_run=0)
    enqueued = h.discover_company(_company(), prefs, tracking, queue, client, "tailor-url")

    assert enqueued == h.BACKFILL_CAP == 25
    assert len(queue.messages) == 25


def test_top_n_cap_limits_what_one_run_tailors(applications_table, monkeypatch):
    """Tailoring is the expensive stage; a big board must not hand over dozens."""
    monkeypatch.setattr(h, "ADAPTERS", {"ashby": type("A", (), {"fetch": lambda self, c, cl: _jobs(30)})()})
    tracking = TrackingStore(applications_table)
    queue = FakeQueue()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    enqueued = h.discover_company(_company(), Preferences(max_new_per_run=5),
                                  tracking, queue, client, "tailor-url")

    assert enqueued == 5
    assert len(queue.messages) == 5


def test_dedup_prevents_reenqueue_on_second_poll(applications_table, monkeypatch):
    adapter = type("A", (), {"fetch": lambda self, c, cl: _jobs(10)})()
    monkeypatch.setattr(h, "ADAPTERS", {"ashby": adapter})
    tracking = TrackingStore(applications_table)
    queue = FakeQueue()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    # No top-N cap here — this test is about dedup across polls, not the cap.
    prefs = Preferences(max_new_per_run=0)
    first = h.discover_company(_company(), prefs, tracking, queue, client, "tailor-url")
    second = h.discover_company(_company(), prefs, tracking, queue, client, "tailor-url")

    assert first == 10
    assert second == 0  # all already in the table -> conditional writes block them
    assert len(queue.messages) == 10


@pytest.mark.parametrize("ats,board,host", [
    ("greenhouse", "example-board", "job-boards.greenhouse.io"),
    ("greenhouse", "example-board", "boards.greenhouse.io"),
    ("ashby", "example.co", "jobs.ashbyhq.com"),
])
def test_existing_consumer_veto_precedes_all_new_row_seen_and_queue_effects(
    applications_table, monkeypatch, ats, board, host,
):
    from discovery.canonical_job_url import admit_feed
    jid = "731205" if ats == "greenhouse" else "123e4567-e89b-42d3-a456-426614174000"
    url = (f"https://{host}/{board}/jobs/{jid}" if ats == "greenhouse"
           else f"https://{host}/{board}/{jid}")
    co = CompanyConfig(name="example-co", ats=ats, board=board,
                       careers_url=f"https://{host}/{board}")
    fixture = JobRecord(company=co.name, ats=ats, job_id=jid,
                        title="Test Engineer", jd_url=url, jd_text="")
    monkeypatch.setattr(h, "ADAPTERS", {ats: type("Adapter", (), {
        "fetch": lambda self, c, client: [fixture]})()})
    monkeypatch.setattr(h, "admit_feed", admit_feed)
    tracking = TrackingStore(applications_table)
    queue = FakeQueue()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200)))
    result = h.discover_company(
        co, Preferences(max_new_per_run=0), tracking, queue, client, "tailor")
    assert result == 0 and tracking.get(fixture.pk) is None
    assert queue.messages == []
    assert not _seen.load()
    assert tracking.get(h._watermark_pk(co.name))["last_poll"] == "done"
