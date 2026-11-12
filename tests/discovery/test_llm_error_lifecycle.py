"""Offline lifecycle of the shared LLM failure banner.

A successful stage may clear only its own error. A racing failure from a
different stage must survive even when the clearing stage saw the old value.
"""

import json

import litellm
import pytest

from core import flags
from core.models import JobRecord
from discovery import career_fit, career_ops, relevance
from discovery.watchlist import Preferences


@pytest.fixture
def screen(monkeypatch):
    """Synthetic relevance responses: no provider, browser or runtime data."""
    monkeypatch.setattr(relevance, "messages", lambda _: [])
    jobs = [
        JobRecord(company="example-co", job_id="role-1",
                  title="Platform Engineer", jd_url="https://example.com/1",
                  jd_text="software delivery"),
        JobRecord(company="example-co", job_id="role-2",
                  title="Sales Representative", jd_url="https://example.com/2",
                  jd_text="sales"),
    ]
    prefs = Preferences(titles=["Platform Engineer"])

    def run(*, error=None, payload='{"fit_indices": [0]}', empty=False):
        def complete(**_kwargs):
            if error:
                raise RuntimeError(error)
            return {"choices": [{"message": {"content": payload}}]}

        monkeypatch.setattr(litellm, "completion", complete)
        return relevance.relevant([] if empty else jobs, prefs, model="test/relevance")

    return run


@pytest.fixture
def career(monkeypatch):
    """Two synthetic batches; a failed second one must not clear the banner."""
    monkeypatch.setattr(career_ops, "report_progress", lambda _: None)
    monkeypatch.setattr(career_fit, "BATCH", 1)
    monkeypatch.setattr(
        career_fit, "_pending", lambda _: [{"id": "test-1"}, {"id": "test-2"}]
    )
    monkeypatch.setattr(career_fit, "_store", lambda _search_id, labels: len(labels))

    def run(*, fail_second=False):
        calls = []

        def label(batch):
            identity = batch[0]["id"]
            calls.append(identity)
            if fail_second and identity == "test-2":
                raise RuntimeError("synthetic classification timeout")
            return [(identity, "it", "synthetic")]

        monkeypatch.setattr(career_fit, "_label_batch", label)
        return career_fit.classify_search("test-search"), calls

    return run


def test_error_then_success_clears_same_where(screen, career):
    """Both screens recover their own transient errors after mocked completions."""
    flags.note_llm_error("relevance screen", "temporary outage")
    assert flags.llm_error()["where"] == "relevance screen"
    assert [job.title for job in screen()] == ["Platform Engineer"]
    assert flags.llm_error() is None

    flags.note_llm_error("career domain", "temporary outage")
    result, calls = career()
    assert result == {"classified": 2, "batches": 2, "failed": False}
    assert calls == ["test-1", "test-2"]
    assert flags.llm_error() is None


def test_error_then_error_keeps_banner_and_keyword_fallback(screen, career):
    """A second failure never signals recovery or releases excluded titles."""
    flags.note_llm_error("relevance screen", "first timeout")
    assert [job.title for job in screen(error="second timeout")] == [
        "Platform Engineer"
    ]
    assert flags.llm_error()["where"] == "relevance screen"
    assert "second timeout" in flags.llm_error()["msg"]

    flags.note_llm_error("career domain", "first timeout")
    result, calls = career(fail_second=True)
    assert calls == ["test-1", "test-2"]
    assert result == {"classified": 1, "batches": 2, "failed": True}
    assert flags.llm_error()["where"] == "career domain"
    assert "classification timeout" in flags.llm_error()["msg"]


def test_success_for_other_where_leaves_existing_error(screen, career):
    """One stage's success cannot conceal an outstanding different-stage error."""
    flags.note_llm_error("career domain", "still failing")
    screen()
    assert flags.llm_error()["where"] == "career domain"

    flags.note_llm_error("relevance screen", "still failing")
    career()
    assert flags.llm_error()["where"] == "relevance screen"


def test_concurrent_replacement_survives_atomic_clear(monkeypatch):
    """A newer different-stage write between WATCH/read and EXEC wins the race."""
    flags.note_llm_error("relevance screen", "old timeout")
    client = flags._redis()
    newer = json.dumps({"where": "career domain", "msg": "new timeout", "at": "test"})
    injected = []

    class RacingPipeline:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def multi(self):
            # Another client writes after the watched value was inspected.
            injected.append(True)
            client.hset(flags._KEY, "llm_error", newer)
            return self.inner.multi()

    class RacingRedis:
        def pipeline(self):
            return RacingPipeline(client.pipeline())

    monkeypatch.setattr(flags, "_redis", lambda: RacingRedis())
    assert flags.clear_llm_error("relevance screen") is False
    assert injected == [True]
    assert client.hget(flags._KEY, "llm_error") == newer


def test_parse_fallback_and_empty_screen_do_not_clear(screen):
    flags.note_llm_error("relevance screen", "pending retry")
    assert [job.title for job in screen(payload="not JSON")] == [
        "Platform Engineer"
    ]
    assert flags.llm_error()["where"] == "relevance screen"
    assert screen(empty=True) == []
    assert flags.llm_error()["where"] == "relevance screen"


def test_no_pending_career_work_does_not_clear(career, monkeypatch):
    flags.note_llm_error("career domain", "pending retry")
    monkeypatch.setattr(career_fit, "_pending", lambda _: [])
    result, calls = career()
    assert result == {"classified": 0, "batches": 0}
    assert calls == []
    assert flags.llm_error()["where"] == "career domain"


def test_redis_error_and_malformed_state_fail_closed(monkeypatch):
    """Unparseable state and Redis faults never authorize a deletion."""
    client = flags._redis()
    client.hset(flags._KEY, "llm_error", "not JSON")
    assert flags.clear_llm_error("relevance screen") is False
    assert client.hget(flags._KEY, "llm_error") == "not JSON"

    flags.note_llm_error("relevance screen", "still failing")
    before = client.hget(flags._KEY, "llm_error")

    def unavailable():
        raise RuntimeError("synthetic Redis outage")

    monkeypatch.setattr(flags, "_redis", unavailable)
    assert flags.clear_llm_error("relevance screen") is False
    assert client.hget(flags._KEY, "llm_error") == before
