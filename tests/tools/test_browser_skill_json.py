"""Offline completion-boundary tests: no model service, browser or owner data."""

from __future__ import annotations

import json
import logging

import pytest

from core import flags
from tools import browser_skill as bsk


def _reply(content: str, finish_reason: str | None = "stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


@pytest.fixture
def fake_completions(monkeypatch):
    import litellm

    replies, calls, banners = [], [], []

    def complete(**kwargs):
        calls.append(kwargs)
        return replies.pop(0)

    monkeypatch.setattr(litellm, "completion", complete)
    monkeypatch.setattr(
        "core.steering.messages",
        lambda prompt: [{"role": "system", "content": "Synthetic steering"},
                        {"role": "user", "content": prompt}],
    )
    monkeypatch.setattr(flags, "note_llm_error",
                        lambda where, problem: banners.append((where, problem)))
    return replies, calls, banners


async def test_invalid_then_valid_reasks_with_original_context(fake_completions):
    replies, calls, banners = fake_completions
    replies.extend([_reply('{"BROKEN_COMPLETION_MARKER":'), _reply('{"action":"wait"}')])

    action = await bsk.decision(
        "Choose the next safe action", {"url": "https://example.com/jobs", "text": "Test listing"},
        [{"action": "wait"}], model="test-json-model")

    assert action == {"action": "wait"}
    assert len(calls) == 2
    assert calls[0]["response_format"] == calls[1]["response_format"] == {"type": "json_object"}
    assert calls[0]["model"] == calls[1]["model"] == "test-json-model"
    assert calls[1]["messages"][:len(calls[0]["messages"])] == calls[0]["messages"]
    assert len(calls[1]["messages"]) == len(calls[0]["messages"]) + 1
    assert "ONLY one JSON object" in calls[1]["messages"][-1]["content"]
    assert "BROKEN_COMPLETION_MARKER" not in str(calls[1]["messages"])
    assert banners == []  # Recovery is not a terminal model outage.


async def test_twice_invalid_sets_exact_banner_and_stops(fake_completions):
    replies, calls, banners = fake_completions
    replies.extend([_reply("not JSON"), _reply('{"action":')])

    with pytest.raises(bsk.Unavailable) as failure:
        await bsk.decision("Choose a safe action", {"url": "https://example.com"},
                           [], model="test-json-model")

    assert str(failure.value) == "BrowserSkill model returned invalid JSON"
    assert banners == [("browser", "BrowserSkill model returned invalid JSON")]
    assert len(calls) == 2


async def test_json_error_logs_only_safe_metadata(fake_completions, monkeypatch, caplog):
    replies, calls, banners = fake_completions
    bad = '{"action":"finish","report":{"jobs":"PRIVATE_COMPLETION_MARKER"'
    replies.extend([
        _reply(bad, finish_reason="length"),
        _reply('{"action":"finish","report":{"jobs":[],"board":"none","note":""}}'),
    ])
    monkeypatch.setattr(bsk, "log", logging.getLogger("test.browser_skill.json"))
    task = ("List synthetic example-co jobs. For finish.report use the requested "
            "jobs JSON schema. PRIVATE_PROMPT_MARKER")

    with caplog.at_level(logging.WARNING, logger="test.browser_skill.json"):
        action = await bsk.decision(
            task, {"url": "https://example.com/jobs", "text": "Synthetic board"},
            [], model="test-json-model")

    assert action["action"] == "finish"
    with pytest.raises(json.JSONDecodeError) as invalid:
        json.loads(bad)
    record = caplog.text
    assert "JSONDecodeError" in record
    assert f"position={invalid.value.pos}" in record
    assert "finish_reason=length" in record
    assert f"content_length={len(bad)}" in record
    assert "model=test-json-model" in record
    assert "PRIVATE_COMPLETION_MARKER" not in record
    assert "PRIVATE_PROMPT_MARKER" not in record
    correction = calls[1]["messages"][-1]["content"]
    assert "SHORTER" in correction
    assert "report.jobs" in correction and "report.board" in correction
    assert "report.note" in correction and "outer action object" in correction
    assert banners == []


async def test_parsed_non_object_is_invalid_without_decoder_position(
        fake_completions, monkeypatch, caplog):
    replies, calls, banners = fake_completions
    replies.extend([_reply('["not an action"]'), _reply('{"action":"wait"}')])
    monkeypatch.setattr(bsk, "log", logging.getLogger("test.browser_skill.json"))

    with caplog.at_level(logging.WARNING, logger="test.browser_skill.json"):
        action = await bsk.decision("Choose a safe action", {"url": "https://example.com"},
                                    [], model="test-json-model")

    assert action == {"action": "wait"}
    assert len(calls) == 2
    assert "NonObjectJSON" in caplog.text
    assert "position=None" in caplog.text
    assert banners == []


def test_invalid_json_banner_is_infrastructure_in_apply_fail_reason():
    from agent import run
    from tools.browser_runtime import is_infrastructure

    invalid_json = "BrowserSkill model returned invalid JSON"
    provider_unavailable = "BrowserSkill model unavailable: X"
    assert is_infrastructure(invalid_json)
    assert is_infrastructure(provider_unavailable)
    for detail in (invalid_json, provider_unavailable):
        assert run._fail_reason({"status": "unknown", "detail": detail}) == detail


def test_invalid_json_infrastructure_retry_keeps_attempt_budget():
    from unittest.mock import MagicMock

    from core.apply_queue import ApplyQueue, MAX_ATTEMPTS

    redis = MagicMock()
    redis.get.return_value = None
    redis.lrange.return_value = []
    queue = ApplyQueue(redis)
    item = {"pk": "example-co#json", "company": "example-co",
            "attempts": MAX_ATTEMPTS - 1}
    banner = "BrowserSkill model returned invalid JSON"

    # At the retry ceiling, a misclassified format fault would dead-letter
    # immediately instead of re-queuing without spending the attempt.
    for retry_index in range(MAX_ATTEMPTS + 1):
        assert queue.retry(item, banner) is True
        assert redis.rpush.call_args.args[0] == "applyq:co:example-co"
        item = json.loads(redis.rpush.call_args.args[1])
        assert item["attempts"] == MAX_ATTEMPTS - 1
        assert len(item["history"]) == retry_index + 1

    assert redis.rpush.call_count == MAX_ATTEMPTS + 1
    assert not any(call.args[0] == "applyq:dlq"
                   for call in redis.rpush.call_args_list)
