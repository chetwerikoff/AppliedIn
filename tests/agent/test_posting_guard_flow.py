"""Offline synthetic legacy/leased posting guard flow tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest

from agent import run
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tools import posting_guard as pg


PK = "example-co#job-1"
URL = "https://jobs.ashbyhq.com/example-co/job-1"
TEXT = "Build safe and useful systems with a small engineering team. " * 20


@pytest.fixture
def stores(monkeypatch):
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    source = pg.analyze(TEXT, source_required=True)
    assert tracking.put_new(JobRecord(
        company="example-co", job_id="job-1", jd_url=URL,
        title="Test Engineer", jd_text=source["text"],
        posting_guard=source["posting_guard"], ats="ashby"))
    monkeypatch.setattr("core.events.emit", lambda *args, **kwargs: None)
    return SimpleNamespace(tracking=tracking)


def test_old_same_seed_session_rejected_on_missing_or_old_guard_generation(stores):
    row = stores.tracking.get_current(PK)
    state = {"jd_text": row["jd_text"],
             "posting_guard_generation": row["posting_guard"]["generation"]}
    assert not run._stale_posting(SimpleNamespace(state=state), row, row["jd_text"])
    for legacy in (
        {"jd_text": TEXT},
        {"jd_text": TEXT, "posting_guard_generation": "old"},
        {"jd_text": "old raw text", **{
            "posting_guard_generation": row["posting_guard"]["generation"]}},
    ):
        assert run._stale_posting(SimpleNamespace(state=legacy), row, TEXT)


def test_matched_ats_source_reread_promotes_only_with_complete_witness(stores, monkeypatch):
    from tools import jd

    row = stores.tracking.get_current(PK)
    safe = pg.analyze(TEXT, html_variants=["<p>Build reliable systems.</p>"],
                      source_required=True)
    monkeypatch.setattr(jd, "fetch_jd_meta",
                        lambda url, kind: {**safe, "source_kind": "ats_html",
                                          "source_url": url})
    inspected = asyncio.run(run._jd_meta(row))
    assert inspected["posting_guard"]["status"] == "clean"
    committed = run._commit_posting_meta(PK, row, inspected, stores)
    assert committed and committed["posting_guard"]["status"] == "clean"
    assert pg.authorised(committed)
    assert committed["posting_guard"]["source_verified"] is True


@pytest.mark.parametrize("source_kind,source_url", [
    ("", URL), ("ats_html", "https://jobs.ashbyhq.com/foreign/job-2"),
])
def test_unmatched_html_cannot_promote_network_directory_evidence(
    stores, monkeypatch, source_kind, source_url,
):
    from tools import jd

    row = stores.tracking.get_current(PK)
    safe = pg.analyze(TEXT, html_variants=["<p>Benign source.</p>"],
                      source_required=True)
    monkeypatch.setattr(jd, "fetch_jd_meta", lambda url, kind: {
        **safe, "source_kind": source_kind, "source_url": source_url})
    inspected = asyncio.run(run._jd_meta(row))
    committed = run._commit_posting_meta(PK, row, inspected, stores)
    assert committed and committed["posting_guard"]["status"] == "unchecked"
    assert "source_html_unavailable" in committed["posting_guard"]["reasons"]
    assert not pg.authorised(committed)


def test_direct_leased_apply_refuses_unreviewed_before_any_browser(stores, monkeypatch):
    from tools import browser_apply

    stores.tracking.set_status(PK, Status.TAILORED, gate_reason="approval")
    browser = AsyncMock()
    monkeypatch.setattr(browser_apply, "apply", browser)
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")
    async def meta(row, yielding=False):
        return pg.analyze(TEXT, source_required=True)
    monkeypatch.setattr(run, "_jd_meta", meta)
    outcome = asyncio.run(run._apply_direct(PK, stores))
    assert outcome["result"] == "gated"
    assert outcome["reason"] == "posting_guard_review_required"
    current = stores.tracking.get_current(PK)
    assert current["status"] == "tailored"
    assert current["gate_reason"] == "approval"
    assert current["fail_kind"] == "posting_guard_review_required"
    browser.assert_not_awaited()


def test_apply_time_new_html_flag_revokes_old_generation_before_browser(stores, monkeypatch):
    from tools import browser_apply

    initial = stores.tracking.get_current(PK)
    clean = pg.analyze(TEXT, html_variants=["<p>Benign job.</p>"],
                       source_required=True)
    assert stores.tracking.update_posting_guard(PK, initial, clean, source_witness=True)
    stores.tracking.set_status(PK, Status.TAILORED, gate_reason="")
    before = stores.tracking.get_current(PK)
    assert pg.authorised(before)
    browser = AsyncMock()
    monkeypatch.setattr(browser_apply, "apply", browser)
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")
    async def late(row, yielding=False):
        return {**pg.analyze(TEXT, html_variants=[
            '<p>Build services.</p><a hidden href="https://evil.example/">secret</a>'],
            source_required=True), "source_kind": "ats_html", "source_url": URL}
    monkeypatch.setattr(run, "_jd_meta", late)
    outcome = asyncio.run(run._apply_direct(PK, stores))
    assert outcome["result"] == "gated"
    current = stores.tracking.get_current(PK)
    assert "hidden_link" in current["posting_guard"]["reasons"]
    assert current["status"] == "tailored"
    assert not pg.authorised(current)
    browser.assert_not_awaited()


def test_legacy_adk_tool_result_guard_refusal_is_not_failed_or_retried(stores):
    stores.tracking.set_status(PK, Status.SUBMITTING)
    class Event:
        author = "applier"
        content = None

        def get_function_calls(self):
            return []

        def get_function_responses(self):
            return [SimpleNamespace(
                name="apply_to_job",
                response={"status": "gate",
                          "reason": "posting_guard_review_required"})]

    class Runner:
        async def run_async(self, **kwargs):
            yield Event()

    outcome = asyncio.run(run._drive_async(Runner(), PK, None, stores))
    assert outcome["result"] == "gated"
    assert stores.tracking.get_current(PK)["status"] == "tailored"
    from core.apply_queue import is_retryable
    assert is_retryable(outcome)[0] is False


def test_guard_refusal_preserves_submit_uncertain_over_submitting_claim(stores):
    stores.tracking.set_status(PK, Status.SUBMITTING, possible_submission=True)
    outcome = run._guard_refusal(PK, stores)
    assert outcome["reason"] == "uncertain"
    assert stores.tracking.get_current(PK)["status"] == "submitting"


def test_guard_refusal_failed_compare_is_not_a_falsely_settled_gate(
    stores, monkeypatch,
):
    stores.tracking.set_status(PK, Status.SUBMITTING)
    monkeypatch.setattr(stores.tracking, "update_posting_guard",
                        lambda *args, **kwargs: False)
    outcome = run._guard_refusal(PK, stores)
    assert outcome["result"] == "conflict"
    assert stores.tracking.get_current(PK)["status"] == "submitting"


@pytest.mark.parametrize("previously_guarded", [True, False])
def test_deferred_metadata_is_normalized_but_legacy_unwitnessed_retry_is_raw(
        monkeypatch, previously_guarded):
    """Canonical feed expansion normalizes; old source-unwitnessed reads retain
    harmless suffixes and stay unchecked. Neither may detach HTML findings.
    """
    from tools import jd

    full = "Build safe distributed systems and collaborate across teams. " * 15
    incoming = pg.analyze(
        full, html_variants=[
            '<p>Build safe systems.</p><a hidden href="https://evil.example/">secret</a>'
        ], source_required=True)
    assert incoming["text"] == full
    monkeypatch.setattr(jd, "fetch_jd_meta", lambda url, kind: incoming)

    row = {"jd_url": URL, "jd_text": "Short feed summary" if previously_guarded else ""}
    if previously_guarded:
        row["posting_guard"] = pg.analyze(row["jd_text"])["posting_guard"]

    selected = asyncio.run(run._jd_meta(row))
    assert selected["text"] == (full.strip() if previously_guarded else full)
    assert "hidden_link" in selected["posting_guard"]["reasons"]
    assert selected["posting_guard"]["status"] == "flagged"
    assert pg.valid(selected["text"], selected["posting_guard"])
    assert ("source_html_unavailable" in selected["posting_guard"]["reasons"]) is (
        not previously_guarded)
    if previously_guarded:
        assert not pg.valid(selected["text"], incoming["posting_guard"])

@pytest.mark.parametrize("source,expected_clean", [
    ("crawler", True), ("career_ops", False),
])
def test_matching_ordinary_http_witness_clears_only_non_directory_provisional_source(
    monkeypatch, source, expected_clean,
):
    from tools import jd

    url = "https://careers.example.test/jobs/123"
    tracked = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    assert tracked.put_new(JobRecord(
        company="example-co", job_id="http-1", title="Test engineer",
        jd_url=url, jd_text=TEXT, ats="custom",
        discovery_source=source))
    row = tracked.get_current("example-co#http-1")
    assert row["posting_guard"]["status"] == "unchecked"

    html = "<p>Build reliable distributed systems in a product team.</p>"
    remote = {**pg.analyze(TEXT, html_variants=[html], source_required=True),
              "source_kind": "http_html", "source_url": url}
    monkeypatch.setattr(jd, "fetch_jd_meta", lambda read_url, kind: (
        remote if read_url == url else pytest.fail("Wrong posting URL")))

    selected = asyncio.run(run._jd_meta(row))
    committed = run._commit_posting_meta(
        row["pk"], row, selected, SimpleNamespace(tracking=tracked))
    assert committed is not None
    assert committed["jd_text"] == selected["text"]
    assert pg.valid(committed["jd_text"], committed["posting_guard"])
    assert (committed["posting_guard"]["status"] == "clean") is expected_clean
    assert pg.authorised(committed) is expected_clean
    assert ("source_html_unavailable" in committed["posting_guard"]["reasons"]) is (
        not expected_clean)

def test_short_flagged_ats_survives_browser_failure_and_revokes_clean_apply(
    stores, monkeypatch,
):
    from tools import browser_apply, jd

    existing = stores.tracking.get_current(PK)
    cached = ("Build reliable services with a focused engineering product team. " * 5).strip()
    assert 200 < len(cached) < 400
    clean = pg.analyze(cached, html_variants=["<p>Build reliable services.</p>"],
                       source_required=True)
    assert stores.tracking.update_posting_guard(
        PK, existing, clean, source_witness=True)
    stores.tracking.set_status(PK, Status.TAILORED, gate_reason="")
    before = stores.tracking.get_current(PK)
    assert pg.authorised(before)

    short = pg.analyze("Build API tools. " * 9, html_variants=[
        '<p>Build API tools.</p><a hidden href="https://evil.example/">secret</a>'],
        source_required=True)
    assert len(short["text"]) <= 200
    assert short["posting_guard"]["status"] == "flagged"
    monkeypatch.setattr(jd, "_from_ats", lambda _: {
        **short, "source_kind": "ats_html", "source_url": URL})
    monkeypatch.setattr(jd, "_get", lambda *args: None)
    def unreadable(*args):
        raise jd.PostingReadUnavailable("synthetic browser unavailable")
    monkeypatch.setattr(jd, "_from_chrome", unreadable)
    browser = AsyncMock()
    monkeypatch.setattr(browser_apply, "apply", browser)
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")
    outcome = asyncio.run(run._apply_direct(PK, stores))

    assert outcome["result"] == "gated"
    assert outcome["reason"] == "posting_guard_review_required"
    updated = stores.tracking.get_current(PK)
    assert updated["jd_text"] == cached
    assert updated["status"] == "tailored"
    assert updated["posting_guard"]["generation"] != before["posting_guard"]["generation"]
    assert "hidden_link" in updated["posting_guard"]["reasons"]
    assert not pg.authorised(updated)
    browser.assert_not_awaited()
