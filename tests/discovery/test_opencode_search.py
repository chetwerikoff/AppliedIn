"""Synthetic v2.2.5 event replay: search-result URLs are not posting reads."""

import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from discovery import opencode_search as oc

SCHEMA = {
    "type": "object", "required": ["jobs"], "additionalProperties": False,
    "properties": {"jobs": {"type": "array", "items": {"type": "object"}}},
}
GOOD = "https://jobs.example.com/postings/read"
UNREAD = "https://jobs.example.com/postings/search-result"


def stream():
    # Shape from the bare CLI capture, with public synthetic values and IDs.
    # Search results are separate API data, never model tool completions.
    return [
        {"type": "step_start", "part": {}},
        {"type": "tool_use", "part": {"tool": "webfetch", "state": {
            "status": "running", "input": {"url": UNREAD},
        }}},
        {"type": "tool_use", "part": {"tool": "webfetch", "state": {
            "status": "completed", "input": {"url": GOOD}, "output": UNREAD,
        }}},
        {"type": "step_finish", "part": {"reason": "tool-calls"}},
        {"type": "step_start", "part": {}},
        {"type": "text", "part": {"text": json.dumps({"jobs": [
            {"company": "example-co", "title": "Engineer", "url": GOOD},
            {"company": "example-co", "title": "Engineer", "url": UNREAD},
        ]})}},
        {"type": "step_finish", "part": {"reason": "stop"}},
    ]


def replay(events, api_searches=None):
    parser = oc.SearchEvents(lambda _: None, SCHEMA, "openai/test-model",
                             [{"query": "example-co engineering", "result_count": 2}]
                             if api_searches is None else api_searches)
    for event in events:
        parser.accept(event)
    return parser.finish()


def test_only_completed_fetch_input_urls_ground_interest_results(monkeypatch):
    from discovery import interest_search

    result = replay(stream())
    assert result["source_urls"] == {GOOD}
    assert result["queries"] == ["example-co engineering"]
    monkeypatch.setattr(interest_search, "_verify_lead", lambda row: row)
    admitted = interest_search.search({}, provider="opencode", runner=lambda *a, **kw: result)
    assert [job["url"] for job in admitted["jobs"]] == [GOOD]
    assert admitted["model"] == "openai/test-model"


@pytest.mark.parametrize("failure", ["searchless", "fetchless", "incomplete", "error", "text", "schema", "later-text"])
def test_incomplete_or_invalid_final_answer_never_imports(failure):
    events = stream()
    searches = None
    if failure == "searchless":
        searches = []
    elif failure == "fetchless":
        events = [event for event in events if event["type"] != "tool_use"]
    elif failure == "incomplete":
        events.pop()
    elif failure == "error":
        events.append({"type": "error", "error": {"type": "provider.auth"}})
    elif failure == "text":
        events[-2]["part"]["text"] = "Here are the results: {\"jobs\": []}"
    elif failure == "schema":
        events[-2]["part"]["text"] = '{"jobs": [], "unexpected": true}'
    elif failure == "later-text":
        events.extend([{"type": "step_start"}, {"type": "text", "part": {"text": "done"}},
                       {"type": "step_finish", "part": {"reason": "stop"}}])
    with pytest.raises(ValueError, match="OpenCode did not complete"):
        replay(events, searches)


@pytest.fixture
def cli(monkeypatch):
    from core import config, steering

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(search_model="openai/test-model"))
    monkeypatch.setattr(steering, "prompt", lambda text: text)
    monkeypatch.setattr(oc.shutil, "which", lambda _: "/bin/opencode2")
    proc = MagicMock()
    proc.__enter__.return_value = proc
    proc.stdout = io.StringIO("\n".join(json.dumps({**e, "sessionID": "ses_synthetic"}) for e in stream()))
    proc.wait.return_value = proc.poll.return_value = 0
    def start(command, **kwargs):
        if command[1] == "api":
            api = MagicMock()
            api.__enter__.return_value = api
            api.stdout = io.StringIO(json.dumps({"data": {"providerID": "exa", "results": [
                {"url": GOOD, "content": "Public candidate"}, {"url": UNREAD},
            ]}}))
            api.wait.return_value = api.poll.return_value = 0
            return api
        return proc
    launch = Mock(side_effect=start)
    cleanup = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(oc.subprocess, "Popen", launch)
    monkeypatch.setattr(oc.subprocess, "run", cleanup)
    return proc, launch, cleanup


def test_standalone_model_key_scrub_config_isolation_and_own_session_cleanup(cli, monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AWS_SECRET_ACCESS_KEY",
                "OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_DB"):
        monkeypatch.setenv(key, "synthetic-do-not-inherit")
    progress = []
    result = oc.run_search("Find public example roles", SCHEMA, progress.append)
    _, launch, cleanup = cli
    command = launch.call_args.args[0]
    assert command[:2] == ["/bin/opencode2", "run"]
    assert "--standalone" in command
    assert command[command.index("--model") + 1] == "openai/test-model"
    assert not {"--auto", "--continue", "--session", "--file"}.intersection(command)
    env = launch.call_args.kwargs["env"]
    assert not any(k in env for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                                    "AWS_SECRET_ACCESS_KEY", "OPENCODE_DB", "OPENCODE_CONFIG"))
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    settings = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert settings["permission"] == {"*": "deny", "webfetch": "allow"}
    assert settings["plugin"] == [] and settings["mcp"] == {}
    assert settings["provider"] == {"openai": {
        "options": {"baseURL": "https://chatgpt.com/backend-api/codex"},
        "models": {"test-model": {"limit": {"context": 272000, "input": 272000, "output": 128000}}},
    }}
    assert "apiKey" not in json.dumps(settings)
    assert result["model"] == "openai/test-model"
    api_command = launch.call_args_list[0].args[0]
    assert api_command[1:5] == ["api", "--standalone", "POST", "/api/websearch"]
    assert json.loads(api_command[-1]) == {"query": "software engineering jobs", "providerID": "exa"}
    assert result["api_searches"] == [{"query": "software engineering jobs", "result_count": 2}]
    assert "UNTRUSTED CANDIDATE DATA" in command[-1]
    assert UNREAD in command[-1]
    assert any("openai/test-model" in message for message in progress)
    cleanup.assert_called_once()
    assert cleanup.call_args.args[0] == ["/bin/opencode2", "session", "delete", "--standalone", "ses_synthetic"]
    assert cleanup.call_args.kwargs["env"] == env


@pytest.mark.parametrize("failure", ["exit", "cancel", "invalid", "timeout"])
def test_search_failures_are_clear_and_still_delete_only_own_session(cli, monkeypatch, failure):
    proc, _, cleanup = cli
    if failure == "exit":
        proc.wait.return_value = 1
    elif failure == "invalid":
        proc.stdout = io.StringIO('{"type":"error","sessionID":"ses_synthetic"}\n')
    elif failure == "timeout":
        proc.poll.return_value = None
        timer = MagicMock()
        timer.start.side_effect = lambda: timer.expire()
        def make_timer(_, expire):
            timer.expire = expire
            return timer
        monkeypatch.setattr(oc.threading, "Timer", make_timer)
    with pytest.raises(ValueError, match="OpenCode"):
        oc.run_search("query", SCHEMA, lambda _: None, cancelled=lambda: failure == "cancel")
    if failure == "cancel":
        cleanup.assert_not_called()
    else:
        assert cleanup.call_args.args[0][-1] == "ses_synthetic"


def test_missing_binary_is_one_search_error(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Install OpenCode CLI"):
        oc.run_search("query", SCHEMA, lambda _: None)


def test_search_model_environment_setting(monkeypatch):
    from core.config import Settings

    assert Settings().search_model == "openai/gpt-6.1-sol"
    monkeypatch.setenv("APPLIEDIN_SEARCH_MODEL", "openai/other-model")
    assert Settings().search_model == "openai/other-model"


def test_api_accepts_opencode_and_rejects_unknown_provider():
    from pydantic import ValidationError
    from discovery.career_ops_api import BoardSettings, InterestSearch, NetworkSearch, SearchProvider

    assert BoardSettings(companies=[], scheduled=False, auto_prepare=False, search_provider="opencode").search_provider == "opencode"
    for cls in (InterestSearch, SearchProvider, NetworkSearch):
        fields = {"ats": ["ashby"]} if cls is NetworkSearch else {}
        assert cls(provider="opencode", **fields).provider == "opencode"
        with pytest.raises(ValidationError):
            cls(provider="unknown", **fields)


@pytest.mark.parametrize("network", [False, True])
def test_model_reaches_both_search_receipts_and_dispatches_opencode(tmp_path, monkeypatch, network):
    from discovery import career_fit, career_network, career_ops as co, watchlist

    monkeypatch.setattr(co, "_path", lambda: tmp_path / "board.json")
    monkeypatch.setattr(co, "search_preferences", lambda *a: {})
    monkeypatch.setattr(co, "_save_results", lambda *a: None)
    monkeypatch.setattr(watchlist, "load_watchlist", lambda *a: [])
    monkeypatch.setattr(career_fit, "classify_search", lambda *a: None)
    run = Mock(return_value={"parsed": {"jobs": [], "summary": "Synthetic empty search"},
                             "source_urls": set(), "queries": ["public example jobs"],
                             "model": "openai/test-model",
                             "api_searches": [{"query": "public example jobs", "result_count": 0}]})
    monkeypatch.setattr(oc, "run_search", run)
    assert co.reserve_scan("network" if network else "web")
    if network:
        career_network.STOP.clear()
        monkeypatch.setattr(career_network, "events", lambda _: iter([{"kind": "done"}]))
        career_network.run_reserved({"provider": "opencode", "days": 7, "ats": ["ashby"],
                                     "positive": [], "negative": [], "locations": []})
    else:
        co.search_reserved(provider="opencode")
    receipt = co._read()["last_network" if network else "last_search"]
    assert receipt["model"] == "openai/test-model"
    assert receipt["queries"] == ["public example jobs"]
    assert receipt["api_searches"] == [{"query": "public example jobs", "result_count": 0}]
    assert not receipt["errors"]
    run.assert_called_once()


def test_public_queries_are_bounded_and_do_not_use_personal_preferences():
    assert oc.public_queries("public infrastructure jobs", ["Engineer", "engineer"]) == [
        "public infrastructure jobs", "Engineer",
    ]
    assert len(oc.public_queries("interest", [f"Role {i}" for i in range(20)])) == 8


def test_one_keyless_api_call_per_public_query_before_model(cli):
    _, launch, _ = cli
    result = oc.run_search("public prompt", SCHEMA, lambda _: None,
                           interests="public systems", titles=[f"Role {i}" for i in range(10)])
    api_calls = [call.args[0] for call in launch.call_args_list if call.args[0][1] == "api"]
    assert len(api_calls) == 8
    assert [json.loads(command[-1])["query"] for command in api_calls] == result["queries"]
    assert all(json.loads(command[-1])["providerID"] == "exa" for command in api_calls)


@pytest.mark.parametrize("response", ["not json", '{"data":{"providerID":"other","results":[]}}',
                                     '{"data":{"providerID":"exa","results":null}}'])
def test_invalid_api_result_never_starts_model_or_imports(cli, response):
    _, launch, cleanup = cli
    api = MagicMock()
    api.__enter__.return_value = api
    api.stdout = io.StringIO(response)
    api.wait.return_value = api.poll.return_value = 0
    launch.side_effect = None
    launch.return_value = api
    with pytest.raises(ValueError, match="API completed no successful queries"):
        oc.run_search("public prompt", SCHEMA, lambda _: None)
    launch.assert_called_once()
    cleanup.assert_not_called()


def test_failed_fetch_does_not_discard_other_grounded_postings():
    events = stream()
    events.insert(1, {"type": "tool_use", "part": {"tool": "webfetch", "state": {
        "status": "error", "input": {"url": UNREAD}, "error": "HTTP 403"}}})
    result = replay(events)
    assert result["source_urls"] == {GOOD}
    assert UNREAD not in result["source_urls"]


def test_denied_websearch_does_not_discard_completed_fetches():
    events = stream()
    events.insert(1, {"type": "tool_use", "part": {"tool": "websearch", "state": {
        "status": "error", "input": {"query": "ignored model query"},
        "error": "Permission denied"}}})
    result = replay(events)
    assert result["source_urls"] == {GOOD}
    assert result["queries"] == ["example-co engineering"]


def test_search_config_rejects_non_openai_provider_before_cli(cli, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(search_model="other/example-model"))
    _, launch, cleanup = cli
    with pytest.raises(ValueError, match="supports only openai/<model>"):
        oc.run_search("public prompt", SCHEMA, lambda _: None)
    launch.assert_not_called()
    cleanup.assert_not_called()


@pytest.mark.parametrize("second_response", ["exit", "malformed"])
def test_one_failed_api_query_preserves_successful_query_candidates(cli, second_response):
    _, launch, _ = cli
    start = launch.side_effect
    api_calls = 0
    def mixed(command, **kwargs):
        nonlocal api_calls
        proc = start(command, **kwargs)
        if command[1] == "api":
            api_calls += 1
            if api_calls == 2:
                if second_response == "exit":
                    proc.wait.return_value = 1
                else:
                    proc.stdout = io.StringIO("invalid JSON")
        return proc
    launch.side_effect = mixed
    progress = []
    result = oc.run_search("prompt", SCHEMA, progress.append, interests="query one", titles=["query two"])
    assert result["api_searches"] == [{"query": "query one", "result_count": 2}]
    assert result["source_urls"] == {GOOD}
    assert any("Search query failed: query two" in message for message in progress)
    assert launch.call_args.args[0][1] == "run"


def test_large_unicode_search_results_cannot_overflow_cli_argument(cli):
    _, launch, _ = cli
    start = launch.side_effect
    def large(command, **kwargs):
        proc = start(command, **kwargs)
        if command[1] == "api":
            proc.stdout = io.StringIO(json.dumps({"data": {"providerID": "exa", "results": [
                {"url": GOOD, "title": "界" * 1000, "content": "界" * 22000}
                for _ in range(20)
            ]}}))
        return proc
    launch.side_effect = large
    result = oc.run_search("prompt", SCHEMA, lambda _: None, titles=[f"Role {i}" for i in range(8)])
    command = launch.call_args.args[0]
    assert len(command[-1].encode()) < 64000
    assert all(row["result_count"] == 20 for row in result["api_searches"])
