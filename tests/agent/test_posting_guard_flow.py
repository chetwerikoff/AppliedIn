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
    assert not run._stale_posting(SimpleNamespace(state=state), row, TEXT)
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
