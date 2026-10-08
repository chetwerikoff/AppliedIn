"""Offline adversarial proof of the daemon-exclusive synthetic BrowserSkill fixture.

No test opens a socket, starts Chrome/BSK, reaches owner stores or queues an apply.
The eventual native-positive primary-checkout traversal is operator-only.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_profile as profile
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture


@pytest.fixture
def authority(tmp_path, monkeypatch):
    """A unit-test-only exact-origin stub; other tests check real CLI origin."""
    document = tmp_path / "Synthetic-Resume.pdf"
    document.write_bytes(b"%PDF-1.4\n% synthetic\n%%EOF\n")
    state = fixture._SyntheticRun(str(document))
    monkeypatch.setattr(fixture, "_daemon_origin", lambda: True)
    token = fixture._SESSION_ENTRY.set(state)
    try:
        yield state
    finally:
        fixture._SESSION_ENTRY.reset(token)


def synthetic_page(*, url=fixture.URL, receiver=fixture.URL, extra_granted=False):
    def item(selector, label, typ="text", tag="input", **attrs):
        return {"selector": selector, "label": label, "question": "",
                "type": typ, "tag": tag, "required": False,
                "disabled": False, "has_value": False, "value": "",
                "submit": typ == "submit", "in_form": True,
                "form_selector": "#synthetic-form", "form_action": receiver,
                "formaction": "", "files": [], **attrs}
    controls = [
        item("#name", "Name"),
        item("#additional", "Additional question", typ="textarea", tag="textarea"),
        item("#submit", "Submit application", typ="submit", tag="button"),
    ]
    return {"url": url, "title": "Synthetic role", "text": "Synthetic role " * 40,
            "controls": controls, "truncated": False, "unsupported_frames": [],
            "shadow_roots": False, "opaque_controls": False,
            "inventory_verified": True}


def _source_denied(*_a, **_kw):
    pytest.fail("Fixture reached forbidden production state, secret or browser action")


def test_no_fixture_on_library_import_or_untrusted_python_objects(monkeypatch):
    import daemon
    assert daemon.log and daemon.make_stores and daemon.is_internal_pk
    assert callable(daemon.main) and callable(daemon._recover_orphans)
    assert fixture._SESSION_ENTRY.get() is None
    assert not fixture._read_only_entry()
    for bogus in (True, 1, "fixture", {"fixture_context": True},
                  SimpleNamespace(pk=fixture.PK), fixture._SyntheticRun("/fake/pdf")):
        assert not fixture.authorized(bogus, pk=fixture.PK,
                                      company=fixture.COMPANY, url=fixture.URL)
    with pytest.raises(RuntimeError, match="exclusive daemon"):
        fixture.run()


@pytest.mark.parametrize("context", [True, {"url": fixture.URL}, "synthetic-fixture",
                                      SimpleNamespace(pk=fixture.PK)])
async def test_forged_context_refused_before_store_or_browser(monkeypatch, context):
    monkeypatch.setattr(bsk, "Session", _source_denied)
    monkeypatch.setattr("core.stores.make_stores", _source_denied)
    with pytest.raises(forms.Gate, match="authority"):
        await forms.apply(fixture.URL, fixture.COMPANY, {"Name": "Test User"},
                          "synthetic-fixture", pk=fixture.PK,
                          resume_path="/fake/pdf", fixture_context=context)


async def test_controller_generated_second_field_gate_after_exactly_one_name_fill(
        authority, monkeypatch):
    page = synthetic_page()
    commands = []

    class Raw:
        def __init__(self, kind):
            assert kind == "apply"
        async def __aenter__(self):
            commands.append(("start",))
            return self
        async def __aexit__(self, *_args):
            commands.append(("stop",))
        async def navigate(self, url):
            commands.append(("navigate", url))
            return page
        async def page(self):
            commands.append(("page",))
            return page
        async def call(self, *args, **kwargs):
            commands.append(tuple(args))
            return {}

    monkeypatch.setattr(bsk, "Session", Raw)
    monkeypatch.setattr(bsk, "decision", _source_denied)
    monkeypatch.setattr("core.stores.make_stores", _source_denied)
    monkeypatch.setattr("tools.browser_apply._duplicate_refusal", _source_denied)
    monkeypatch.setattr("tools.claude_chrome._stage_resume", _source_denied)
    monkeypatch.setattr("tools.browser_apply._site_rules", _source_denied)
    monkeypatch.setattr(forms.submit_hold, "blocked", _source_denied)
    monkeypatch.setattr(forms.submit_hold, "is_held", _source_denied)
    monkeypatch.setattr("core.events.emit", _source_denied)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, {"Name": "Test User",
                                      "Additional question": "Synthetic answer"},
        "synthetic-fixture", pk=fixture.PK, resume_path=authority.resume_path,
        fixture_context=authority)
    assert result["status"] == "gate", result
    gate = result["form_question"]
    assert gate == {
        "pk": fixture.PK, "label": "Additional question",
        "selector": "#additional", "url": fixture.URL,
        "control_label": "Additional question", "question": "",
    }
    assert authority.fills == 1 and authority.decisions == 2
    assert commands.count(("fill", "#name", "--value", "Test User")) == 1
    assert not any(c[0] in {"click", "upload", "select", "choose", "evaluate"}
                   for c in commands)
    assert commands[0] == ("start",) and commands[-1] == ("stop",)
    assert commands.count(("navigate", fixture.URL)) == 1


async def test_closed_facade_denies_all_extra_ipc_below_controller(authority):
    forwarded = []
    class Raw:
        async def navigate(self, url):
            forwarded.append(("navigate", url))
            return synthetic_page()
        async def page(self):
            forwarded.append(("evaluate", "PAGE"))
            return synthetic_page()
        async def call(self, *args, **kw):
            forwarded.append(tuple(args))
            return {}
    edge = authority.facade(Raw())
    assert not hasattr(edge, "__dict__") and not hasattr(edge, "session")
    await edge.navigate(fixture.URL)
    await edge.page()
    await edge.call("fill", "#name", "--value", "Test User")
    for args in [
        ("click", "#submit"), ("click", "#continue"), ("click", "#other"),
        ("upload", "#resume", "--file", "Synthetic-Resume.pdf"),
        ("select", "#other", "--value", "Yes"), ("choose", "#other"),
        ("evaluate", "alert('synthetic')"), ("fill", "#additional", "--value", "Synthetic answer"),
        ("fill", "#name", "--value", "Test User"),
        ("fill", "#name", "--value", "Altered"),
    ]:
        with pytest.raises(PermissionError, match="denies"):
            await edge.call(*args)
    for url in ("http://localhost:18789/fixture/example-co/job/1",
                fixture.URL + "?source=apply", fixture.URL + "/other", fixture.URL):
        with pytest.raises(PermissionError, match="one navigate"):
            await edge.navigate(url)
    assert forwarded == [
        ("navigate", fixture.URL), ("evaluate", "PAGE"),
        ("fill", "#name", "--value", "Test User"),
    ]


def test_fixture_handler_rejects_wrong_host_path_method_and_redirects():
    # Construct BaseHTTPRequestHandler without a socket or network transport.
    def request(method, path, host):
        handler = object.__new__(fixture._FixtureHandler)
        handler.path = path
        handler.headers = {"Host": host}
        handler.wfile = io.BytesIO()
        captured = []
        handler.send_response = lambda status: captured.append(("status", status))
        handler.send_header = lambda key, value: captured.append((key, value))
        handler.end_headers = lambda: None
        getattr(handler, "do_" + method)()
        return captured, handler.wfile.getvalue()
    good, body = request("GET", fixture._PATH, "127.0.0.1:18789")
    assert ("status", 200) in good
    assert b'id="name"' in body and b'id="additional"' in body
    assert b'type="submit"' in body and fixture.URL.encode() in body
    for method, path, host in [
        ("GET", fixture._PATH, "localhost:18789"),
        ("GET", fixture._PATH, "[::1]:18789"),
        ("GET", fixture._PATH + "?redirect=/", "127.0.0.1:18789"),
        ("GET", "/other", "127.0.0.1:18789"),
        ("POST", fixture._PATH, "127.0.0.1:18789"),
        ("HEAD", fixture._PATH, "127.0.0.1:18789"),
        ("PUT", fixture._PATH, "127.0.0.1:18789"),
    ]:
        headers, response = request(method, path, host)
        assert headers[0][1] in {404, 405}
        assert not response and not any(key.lower() == "location" for key, _ in headers)


async def test_real_session_thread_context_and_read_only_connected_path(
        authority, monkeypatch):
    cfg = {"engine": "browser_skill", "browser": "synthetic-pin",
           "user_data_dir": "/synthetic/owned-chrome", "chrome_path": "/synthetic/chrome"}
    observed = {"worker_thread": False, "context": False}
    original = profile._fixture_read_only_browser
    def spy(settings=None):
        observed["worker_thread"] = threading.current_thread() is not threading.main_thread()
        observed["context"] = fixture._SESSION_ENTRY.get() is authority
        return original(settings)
    monkeypatch.setattr(profile, "_fixture_read_only_browser", spy)
    monkeypatch.setattr(profile, "_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr("tools.browser_runtime.configuration", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(profile, "profile_pids", lambda *_a: [123])
    def sync(*args, **kwargs):
        if args[0] == "status":
            return {"browsers": [{"instance_id": "synthetic-pin"}]}
        return {"browsers": [{"instance_id": "synthetic-pin"}]}
    monkeypatch.setattr(bsk, "_sync", sync)
    monkeypatch.setattr(bsk, "ensure_daemon", _source_denied)
    monkeypatch.setattr(profile, "_launch", _source_denied)
    monkeypatch.setattr(profile, "_launch_lock", _source_denied)
    monkeypatch.setattr(profile.subprocess, "Popen", _source_denied)
    calls = []
    async def command(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("session", "start"):
            return {"session_id": "synthetic-session",
                    "browser_instance_id": "synthetic-pin"}
        if args[0] == "evaluate":
            return {"value": synthetic_page()}
        return {}
    monkeypatch.setattr(bsk, "command", command)
    async with bsk.Session("apply") as raw:
        edge = authority.facade(raw)
        await edge.navigate(fixture.URL)
        observed_page = await edge.page()
        assert observed_page["url"] == fixture.URL
        await edge.call("fill", "#name", "--value", "Test User")
        with pytest.raises(PermissionError):
            await edge.call("click", "#submit")
    from tools.browser_skill_dom import PAGE
    assert observed == {"worker_thread": True, "context": True}
    assert calls[0][:2] == ("session", "start")
    assert calls[-1] == ("session", "stop", "synthetic-session")
    assert ("navigate", fixture.URL, "--session", "synthetic-session") in calls
    assert ("evaluate", PAGE, "--session", "synthetic-session") in calls
    assert ("fill", "#name", "--value", "Test User", "--session",
            "synthetic-session") in calls
    assert not any(c[0] == "click" for c in calls)


@pytest.mark.parametrize("failure", [
    "daemon", "extension", "foreign", "ambiguous_extension",
    "missing_chrome", "ambiguous_chrome", "late_disconnect",
])
async def test_session_thread_never_autostarts_on_fixture_entry_race(
        authority, monkeypatch, failure, tmp_path):
    cfg = {"engine": "browser_skill", "browser": "synthetic-pin",
           "user_data_dir": str(tmp_path / "profile"), "chrome_path": "/synthetic/chrome"}
    monkeypatch.setattr(profile, "_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr("tools.browser_runtime.configuration", lambda *_a, **_kw: cfg)
    pids = [] if failure == "missing_chrome" else (
        [123, 456] if failure == "ambiguous_chrome" else [123])
    monkeypatch.setattr(profile, "profile_pids", lambda *_a: pids)
    def sync(*args, **kwargs):
        if failure == "daemon":
            raise bsk.Unavailable("Synthetic daemon disconnected")
        if args[0] == "status":
            return {"browsers": []}
        ids = (["foreign"] if failure == "foreign" else
               ["synthetic-pin", "synthetic-pin"] if failure == "ambiguous_extension" else
               [] if failure == "extension" else ["synthetic-pin"])
        return {"browsers": [{"instance_id": x} for x in ids]}
    monkeypatch.setattr(bsk, "_sync", sync)
    if failure == "late_disconnect":
        monkeypatch.setattr(bsk, "available", lambda: (False, "synthetic disconnect"))
    for target in ("_launch", "_launch_lock"):
        monkeypatch.setattr(profile, target, _source_denied)
    monkeypatch.setattr(bsk, "ensure_daemon", _source_denied)
    monkeypatch.setattr(profile.subprocess, "Popen", _source_denied)
    call = AsyncMock(side_effect=_source_denied)
    monkeypatch.setattr(bsk, "command", call)
    with pytest.raises(bsk.Unavailable):
        async with bsk.Session("apply"):
            pytest.fail("Unready fixture must not open any BSK Session")
    call.assert_not_awaited()
    assert not (tmp_path / "profile").exists()
    assert not list(tmp_path.glob("*.lock"))


def test_fixture_manual_jit_and_hold_evidence_never_needs_owner_store(
        authority, monkeypatch):
    monkeypatch.setattr("core.stores.make_stores", _source_denied)
    monkeypatch.setattr("tools.browser_apply._duplicate_refusal", _source_denied)
    monkeypatch.setattr("tools.claude_chrome._stage_resume", _source_denied)
    monkeypatch.setattr(forms.submit_hold, "blocked", _source_denied)
    monkeypatch.setattr(forms.submit_hold, "is_held", _source_denied)
    monkeypatch.setattr(forms.submit_hold, "mark", _source_denied)
    forms.check_dispatch(fixture.PK, authority.resume_path,
                         fixture_context=authority)
    history = [{"action": "fill", "selector": "#name", "label": "Name",
                "question": "", "fact": "Name", "approved_value": "Test User",
                "url": fixture.URL}]
    page = synthetic_page()
    forms.check_approvals(fixture.PK, history, page,
                          fixture_context=authority)
    forms.hold_possible_submission(
        fixture.PK, {"last_button": "Continue", "url": fixture.URL},
        fixture_context=authority)
    assert authority.is_held(fixture.PK) and authority.row["status"] == "needs_human"
    forms.check_before_committing_click(fixture.PK, fixture_context=authority)
    authority.row["status"] = "applied_manual"
    with pytest.raises(forms.Gate, match="Human outcome changed"):
        forms.check_before_committing_click(fixture.PK, fixture_context=authority)


def test_late_receiver_and_form_inventory_checks_are_nonvacuous():
    page = synthetic_page()
    page["controls"][0].update(required=True, value="Test User", has_value=True)
    page["controls"][1].update(required=True, value="Synthetic answer", has_value=True)
    resume = {"selector": "#resume", "type": "file", "tag": "input",
              "label": "Resume", "question": "", "in_form": True,
              "form_selector": "#synthetic-form", "form_action": fixture.URL,
              "formaction": "", "files": ["Synthetic-Resume.pdf"]}
    page["controls"].insert(2, resume)
    filled = {"#name": "Test User", "#additional": "Synthetic answer"}
    submit = page["controls"][-1]
    forms.check_form_destination(page, submit, fixture.URL)
    forms.check_form(page, filled, True, "Synthetic-Resume.pdf")
    altered = synthetic_page(receiver="https://foreign.example.test/collect")
    with pytest.raises(forms.Gate, match="form destination"):
        forms.check_form_destination(altered, altered["controls"][-1], fixture.URL)
    page["controls"][2]["files"] = []
    with pytest.raises(forms.Gate, match="no longer present"):
        forms.check_form(page, filled, True, "Synthetic-Resume.pdf")


def test_exact_daemon_fixture_cli_sanitizes_environment_before_any_product_import(
        tmp_path):
    """A fresh interpreter aborts at fixture import, before sockets or BSK start."""
    site = tmp_path / "sitecustomize.py"
    site.write_text('''
import builtins, json, os, socket
original = builtins.__import__
def deny(*a, **k):
    raise AssertionError("outbound socket is forbidden")
socket.create_connection = deny
socket.getaddrinfo = deny
socket.socket.connect = deny
def checked(name, *args, **kwargs):
    if name == "tools.browser_skill_fixture":
        print("FIXTURE_IMPORT_ENV=" + json.dumps({
            "dotenv": os.getenv("PYTHON_DOTENV_DISABLED"),
            "cost_map": os.getenv("LITELLM_LOCAL_MODEL_COST_MAP"),
            "autostart": os.getenv("BSK_AUTO_START"),
            "secret": os.getenv("SYNTHETIC_PROVIDER_SECRET"),
            "openai": os.getenv("OPENAI_API_KEY"),
            "arbitrary": os.getenv("APPLIEDIN_SECRET_SENTINEL"),
            "config_dir": os.getenv("APPLIEDIN_CONFIG_DIR"),
        }), flush=True)
        raise SystemExit(87)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
''')
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.update(PYTHONPATH=os.pathsep.join([str(tmp_path), str(root / "src")]),
               SYNTHETIC_PROVIDER_SECRET="synthetic-only-not-real",
               OPENAI_API_KEY="synthetic-only-not-real",
               APPLIEDIN_SECRET_SENTINEL="synthetic-only-not-real",
               APPLIEDIN_CONFIG_DIR=str(tmp_path / "safe-config"))
    result = subprocess.run(
        [sys.executable, "-m", "daemon", "--synthetic-browser-fixture"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=25)
    assert result.returncode == 87, result.stderr
    marker = next(s.split("=", 1)[1] for s in result.stdout.splitlines()
                  if s.startswith("FIXTURE_IMPORT_ENV="))
    seen = json.loads(marker)
    assert seen == {"dotenv": "1", "cost_map": "True", "autostart": "0",
                    "secret": None, "openai": None, "arbitrary": None,
                    "config_dir": str(tmp_path / "safe-config")}


def test_normal_daemon_library_import_and_entry_keep_old_import_boundary(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root / "src"),
               LITELLM_LOCAL_MODEL_COST_MAP="True")
    source = ("import daemon; assert daemon.log and daemon.make_stores "
              "and daemon.is_internal_pk and daemon.main and daemon._worker_loop")
    result = subprocess.run(
        [sys.executable, "-c", source], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
