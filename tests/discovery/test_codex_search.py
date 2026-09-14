"""Codex discovery must have actual search/open events and a ChatGPT login."""

import io
import json
import subprocess
from unittest.mock import MagicMock, Mock

import pytest

from discovery import codex_search as codex


def item(parser, value):
    parser.accept({"type": "item.completed", "item": value})


def test_only_completed_web_opens_ground_leads_not_model_urls():
    parser = codex.SearchEvents(lambda _: None)
    good = "https://jobs.ashbyhq.com/acme/123"
    invented = "https://jobs.ashbyhq.com/acme/made-up"
    item(parser, {"type": "web_search", "query": "Software Engineer", "action": {"type": "search"}})
    # Live CLI's open-page representation; its action schema uses "other".
    item(parser, {"type": "web_search", "query": good, "action": {"type": "other"}})
    item(
        parser,
        {"type": "agent_message", "text": json.dumps({"jobs": [{"url": good}, {"url": invented}]})},
    )
    parser.accept({"type": "turn.completed"})
    result = parser.finish()
    assert result["source_urls"] == {good}
    assert result["queries"] == ["Software Engineer"]


def test_failed_or_searchless_turn_never_imports_a_plausible_list():
    parser = codex.SearchEvents(lambda _: None)
    item(parser, {"type": "agent_message", "text": '{"jobs":[]} '})
    parser.accept({"type": "turn.completed"})
    with pytest.raises(ValueError, match="did not complete"):
        parser.finish()
    item(parser, {"type": "web_search", "query": "engineer", "action": {"type": "search"}})
    parser.accept({"type": "turn.failed"})
    with pytest.raises(ValueError, match="did not complete"):
        parser.finish()


def test_api_key_auth_is_refused_and_key_env_is_removed(monkeypatch):
    monkeypatch.setattr(codex.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-use")
    monkeypatch.setenv("CODEX_API_KEY", "do-not-use")
    login = Mock(return_value=subprocess.CompletedProcess([], 0, "", "Logged in using API key"))
    monkeypatch.setattr(codex.subprocess, "run", login)
    with pytest.raises(ValueError, match="ChatGPT"):
        codex.run_search("search", {}, lambda _: None)
    env = login.call_args.kwargs["env"]
    assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env


def test_subscription_worker_receives_instance_steering_with_read_only_permissions(monkeypatch):
    from core import steering

    steering.save("Prefer platform teams", steering.read()["revision"])
    monkeypatch.setattr(codex.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(
        codex.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", "")),
    )
    proc = MagicMock()
    proc.__enter__.return_value = proc
    proc.stdout = io.StringIO("")
    proc.wait.return_value = proc.poll.return_value = 0
    launch = Mock(return_value=proc)
    monkeypatch.setattr(codex.subprocess, "Popen", launch)
    monkeypatch.setattr(codex.SearchEvents, "finish", lambda _: {})
    codex.run_search("Find roles", {}, lambda _: None)
    command = launch.call_args.args[0]
    assert "Prefer platform teams" in command[-1]
    assert "Find roles" in command[-1]
    assert command[command.index("--sandbox") + 1] == "read-only"
