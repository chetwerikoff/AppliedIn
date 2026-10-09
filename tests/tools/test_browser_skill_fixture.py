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



def test_fresh_daemon_full_synthetic_path_uses_no_owner_io_or_real_sockets(
        tmp_path):
    """Drive the whole CLI -> Session -> controller path with fake BSK transport.

    sitecustomize is loaded by the new interpreter before __main__ or core.config.
    No socket is ever created/bound and no real BSK executable is called.
    """
    from textwrap import dedent

    root = Path(__file__).resolve().parents[2]
    home = tmp_path / "synthetic-home"
    home.mkdir()
    config = home / "config"
    config.mkdir()
    (config / "browser.local.yaml").write_text(
        "engine: browser_skill\nbrowser: synthetic-pin\n"
        f"user_data_dir: {str(home / 'owned-chrome')}\n"
        "chrome_path: /synthetic/chrome\n"
    )
    forbidden = [home / ".env", home / ".local" / "answer-bank.json",
                 home / "resume" / "base.tex", home / "output" / "candidate.pdf",
                 config / "watchlist.yaml", config / "preferences.yaml"]
    for name in forbidden:
        name.parent.mkdir(parents=True, exist_ok=True)
        name.write_text("synthetic forbidden sentinel; never read")
    log = tmp_path / "child-records.jsonl"
    site = tmp_path / "sitecustomize.py"
    site.write_text(dedent(r'''
        import asyncio
        import builtins
        import http.server
        import json
        import os
        import shutil
        import socket
        import subprocess
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        _logfile = os.environ["SYNTHETIC_TEST_LOG"]
        _synthetic_home = Path(os.environ["HOME"])
        _forbidden = {
            str(_synthetic_home / ".env"),
            str(_synthetic_home / ".local" / "answer-bank.json"),
            str(_synthetic_home / "resume" / "base.tex"),
            str(_synthetic_home / "output" / "candidate.pdf"),
            str(_synthetic_home / "config" / "watchlist.yaml"),
            str(_synthetic_home / "config" / "preferences.yaml"),
        }

        def event(kind, **kwargs):
            with open(_logfile, "a", encoding="utf-8") as output:
                output.write(json.dumps({"kind": kind, **kwargs}) + "\n")

        def audit(name, args):
            if name == "open" and args:
                target = os.fspath(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else ""
                if isinstance(target, bytes):
                    target = os.fsdecode(target)
                if target in _forbidden:
                    event("forbidden_read", path=str(Path(target).relative_to(_synthetic_home)))
                    raise AssertionError("Fixture accessed forbidden owner-like synthetic file")
                if target == str(_synthetic_home / "config" / "browser.local.yaml"):
                    event("pinned_config_observed")
        sys.addaudithook(audit)

        def no_socket(*args, **kwargs):
            event("forbidden_socket")
            raise AssertionError("No real socket is permitted in fixture offline test")
        socket.socket.connect = no_socket
        socket.socket.bind = no_socket
        socket.create_connection = no_socket
        socket.getaddrinfo = no_socket

        class OfflineHTTPServer:
            def __init__(self, address, handler):
                assert address == ("127.0.0.1", 18789)
                event("http_handler_selected")
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            timeout = 1
            def serve_forever(self):
                pass
            def shutdown(self):
                pass
        http.server.HTTPServer = OfflineHTTPServer

        original_import = builtins.__import__
        def checked_import(name, *args, **kwargs):
            if name in ("core.config", "litellm"):
                event("config_import_env",
                      module=name,
                      dotenv=os.getenv("PYTHON_DOTENV_DISABLED"),
                      cost=os.getenv("LITELLM_LOCAL_MODEL_COST_MAP"))
                assert os.getenv("PYTHON_DOTENV_DISABLED") == "1"
                assert os.getenv("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
            result = original_import(name, *args, **kwargs)
            if name == "tools.browser_profile":
                # Modify only the fake interpreter, after importing this module.
                # Session.__aenter__ still uses actual ensure_browser in to_thread.
                module = sys.modules.get("tools.browser_profile")
                if module is not None:
                    module.profile_pids = lambda directory: [123]
            return result
        builtins.__import__ = checked_import

        original_which = shutil.which
        def which(command, *args, **kwargs):
            if command == "bsk":
                return "/synthetic/bsk"
            return original_which(command, *args, **kwargs)
        shutil.which = which

        _connection = {"instance_id": "synthetic-pin",
                       "unresponsive": False, "version_skew": False}
        def checked_env(env):
            assert env["BSK_AUTO_START"] == "0"
            assert env["PYTHON_DOTENV_DISABLED"] == "1"
            assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
            for secret in ("OPENAI_API_KEY", "SYNTHETIC_PROVIDER_SECRET",
                           "APPLIEDIN_SECRET_SENTINEL"):
                assert secret not in env
            event("sanitized_bsk_child")

        def fake_run(argv, **kwargs):
            assert argv[0] == "/synthetic/bsk"
            assert argv[1] in ("status", "browsers")
            assert argv[-1] == "--json"
            checked_env(kwargs["env"])
            event("sync_bsk", operation=argv[1])
            return SimpleNamespace(stdout=json.dumps(
                {"ok": True, "browsers": [_connection]}), returncode=0)
        subprocess.run = fake_run

        _url = "http://127.0.0.1:18789/fixture/example-co/job/1"
        _field = {"selector": "#name", "label": "Name", "question": "",
                  "type": "text", "tag": "input", "required": False,
                  "disabled": False, "has_value": False, "value": "",
                  "submit": False, "in_form": True,
                  "form_selector": "#synthetic-form", "form_action": _url,
                  "formaction": "", "files": []}
        _second = dict(_field, selector="#additional", label="Additional question",
                       type="textarea", tag="textarea")
        _submit = dict(_field, selector="#submit", label="Submit application",
                       type="submit", tag="button", submit=True)
        _page = {"url": _url, "title": "Synthetic role",
                 "text": "Synthetic harmless role description. " * 25,
                 "controls": [_field, _second, _submit],
                 "inventory_verified": True, "truncated": False,
                 "opaque_controls": False, "shadow_roots": False,
                 "unsupported_frames": []}

        class Proc:
            returncode = 0
            def __init__(self, payload):
                self.payload = payload
            async def communicate(self):
                return json.dumps(self.payload).encode(), b""
            def kill(self):
                raise AssertionError("No real subprocess may be killed")
            async def wait(self):
                return 0

        async def fake_async(*argv, **kwargs):
            assert argv[0] == "/synthetic/bsk" and argv[-1] == "--json"
            checked_env(kwargs["env"])
            operation = argv[1]
            assert operation in ("session", "navigate", "evaluate", "fill")
            if operation == "session":
                subcommand = argv[2]
                assert subcommand in ("start", "stop")
                if subcommand == "start":
                    assert "--browser" in argv
                    assert argv[argv.index("--browser") + 1] == "synthetic-pin"
                    payload = {"ok": True, "session_id": "fixture-only-session",
                               "browser_instance_id": "synthetic-pin"}
                else:
                    assert "fixture-only-session" in argv
                    payload = {"ok": True}
                event("async_bsk", operation="session_" + subcommand)
            elif operation == "navigate":
                assert argv[2] == _url
                event("async_bsk", operation="navigate")
                payload = {"ok": True}
            elif operation == "evaluate":
                from tools.browser_skill_dom import PAGE
                assert argv[2] == PAGE
                event("async_bsk", operation="evaluate_page")
                payload = {"ok": True, "value": _page}
            else:
                assert argv[2:6] == ("#name", "--value", "Test User", "--session")
                assert argv[6] == "fixture-only-session"
                _field["has_value"], _field["value"] = True, "Test User"
                event("async_bsk", operation="fill_name")
                payload = {"ok": True}
            return Proc(payload)
        asyncio.create_subprocess_exec = fake_async
    '''))
    env = dict(os.environ)
    env.update(PYTHONPATH=os.pathsep.join([str(tmp_path), str(root / "src")]),
               HOME=str(home),
               APPLIEDIN_CONFIG_DIR=str(config),
               SYNTHETIC_TEST_LOG=str(log),
               SYNTHETIC_PROVIDER_SECRET="not-a-real-secret",
               OPENAI_API_KEY="not-a-real-secret",
               APPLIEDIN_SECRET_SENTINEL="not-a-real-secret",
               LITELLM_LOCAL_MODEL_COST_MAP="False",
               PYTHON_DOTENV_DISABLED="0")
    result = subprocess.run(
        [sys.executable, "-m", "daemon", "--synthetic-browser-fixture"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert '"safe_gate": true' in result.stdout.lower()
    events = [json.loads(line) for line in log.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "http_handler_selected" in kinds
    assert "pinned_config_observed" in kinds
    assert not any(k in {"forbidden_read", "forbidden_socket"} for k in kinds)
    assert not any(e["kind"] == "config_import_env"
                   and (e["dotenv"], e["cost"]) != ("1", "True")
                   for e in events)
    actions = [e["operation"] for e in events if e["kind"] == "async_bsk"]
    assert actions.count("session_start") == actions.count("session_stop") == 1
    assert actions.count("navigate") == 1
    assert actions.count("evaluate_page") >= 2
    assert actions.count("fill_name") == 1
    assert not any(a in {"click", "upload", "select", "choose"} for a in actions)
    assert any(e["kind"] == "sanitized_bsk_child" for e in events)



@pytest.mark.parametrize("drift", ["initial_redirect", "later_page"])
async def test_closed_facade_rejects_url_drift_before_any_fill(authority, drift):
    sent = []
    class Raw:
        async def navigate(self, url):
            sent.append(("navigate", url))
            return synthetic_page(url=fixture.URL + "/wrong" if drift == "initial_redirect"
                                  else fixture.URL)
        async def page(self):
            sent.append(("evaluate", "PAGE"))
            return synthetic_page(url=fixture.URL + "?redirect=true")
        async def call(self, *args):
            pytest.fail("A changed fixture URL must never reach form IPC")
    edge = authority.facade(Raw())
    if drift == "initial_redirect":
        with pytest.raises(PermissionError, match="redirected"):
            await edge.navigate(fixture.URL)
        assert sent == [("navigate", fixture.URL)]
    else:
        await edge.navigate(fixture.URL)
        with pytest.raises(PermissionError, match="escaped"):
            await edge.page()
        assert sent == [("navigate", fixture.URL), ("evaluate", "PAGE")]


@pytest.mark.parametrize("drift", ["receiver", "status"])
async def test_native_continue_jit_is_not_hidden_by_missing_pdf_or_approval(
        authority, monkeypatch, drift):
    """Pass all other native-form/attachment evidence before changing one guard.

    This is an offline *controller* test: the facade still forbids every click,
    even if the controller predicates would otherwise allow a native Continue.
    """
    current = synthetic_page()
    current["controls"][0].update(required=True, value="Test User", has_value=True)
    current["controls"][1].update(
        required=True, value="Synthetic answer", has_value=True)
    resume = {
        "selector": "#resume", "label": "Resume", "question": "", "type": "file",
        "tag": "input", "required": True, "disabled": False,
        "in_form": True, "form_selector": "#synthetic-form", "form_action": fixture.URL,
        "formaction": "", "files": ["Synthetic-Resume.pdf"],
    }
    current["controls"].insert(2, resume)
    cont = current["controls"][-1]
    cont.update(selector="#continue", label="Continue", type="submit", tag="button",
                submit=True)
    filled = {"#name": "Test User", "#additional": "Synthetic answer"}
    authority.row["human_approved_answers"]["Additional question"] = {
        "selector": "#additional", "url": fixture.URL, "label": "Additional question",
        "question": "", "value": "Synthetic answer",
    }
    history = [
        {"selector": selector, "label": label, "question": "", "url": fixture.URL,
         "fact": label, "approved_value": val}
        for selector, label, val in (
            ("#name", "Name", "Test User"),
            ("#additional", "Additional question", "Synthetic answer"))]
    # Production first validates the exact receipts in SUBMITTING, then records
    # the possible-submission hold and moves to NEEDS_HUMAN at the JIT edge.
    # Rechecking approval only after the hold would be a wrong-order test.
    forms.check_form(current, filled, True, "Synthetic-Resume.pdf")
    forms.check_approvals(fixture.PK, history, current,
                          fixture_context=authority)
    forms.check_form_destination(current, cont, fixture.URL)
    authority.row["status"], authority.row["gate_reason"], authority.hold = (
        "needs_human", "submit_uncertain", True)
    forms.check_before_committing_click(fixture.PK, fixture_context=authority)

    sent = []
    class Raw:
        async def navigate(self, url):
            sent.append(("navigate", url))
            return current
        async def page(self):
            sent.append(("evaluate", "PAGE"))
            return current
        async def call(self, *args):
            pytest.fail("Even a valid native Continue must not commit via fixture facade")
    edge = authority.facade(Raw())
    await edge.navigate(fixture.URL)
    marks = []
    action = {"action": "click", "selector": "#continue"}
    kwargs = {
        "facts": dict(fixture._FACTS), "filled": filled,
        "resume_path": authority.resume_path, "company": fixture.COMPANY,
        "jd_text": "", "resume_tex": "", "github": "",
        "pk": fixture.PK, "allow_click": True, "job_url": fixture.URL,
        "on_committing_click": lambda: marks.append("reached"),
        "fixture_context": authority,
    }
    with pytest.raises(PermissionError, match="denies"):
        await forms.execute(edge, current, action, **kwargs)
    assert marks == ["reached"] and sent == [("navigate", fixture.URL)]
    marks.clear()
    if drift == "receiver":
        cont["form_action"] = "https://foreign.example.test/submit"
        with pytest.raises(forms.Gate, match="form destination"):
            await forms.execute(edge, current, action, **kwargs)
    else:
        authority.row["status"] = "applied_manual"
        with pytest.raises(forms.Gate, match="Human outcome changed"):
            await forms.execute(edge, current, action, **kwargs)
    assert not marks and sent == [("navigate", fixture.URL)]


def test_fixture_hold_readback_missing_blocks_before_row_or_click(authority, monkeypatch):
    """A synthetic hold SET without its read-back witness is not a safe click."""
    previous = dict(authority.row)
    monkeypatch.setattr(fixture._SyntheticRun, "mark_hold", lambda self, pk: None)
    with pytest.raises(forms.Gate, match="hold could not be confirmed"):
        forms.hold_possible_submission(
            fixture.PK, {"last_button": "Submit", "url": fixture.URL},
            fixture_context=authority)
    assert authority.row == previous and not authority.hold



def test_ordinary_python_module_daemon_still_enters_workers_and_server(tmp_path):
    """The non-fixture -m entry follows original startup, entirely with offline fakes."""
    from textwrap import dedent

    site = tmp_path / "sitecustomize.py"
    site.write_text(dedent(r'''
        import builtins
        import sys
        import threading
        from types import ModuleType, SimpleNamespace

        # Server is the only network-owning foreground entry in main().
        stub_server = ModuleType("server")
        def serve(*, port):
            print("ORDINARY_SERVER:" + str(port), flush=True)
        stub_server.serve = serve
        sys.modules["server"] = stub_server

        # Recovery imports release_claim regardless of whether rows exist.
        stub_run = ModuleType("agent.run")
        stub_run.release_claim = lambda pk, stores: None
        sys.modules["agent.run"] = stub_run

        stores = SimpleNamespace(
            queue=SimpleNamespace(drain=lambda *_a, **_kw: []),
            tracking=SimpleNamespace(all=lambda: []))
        previous_import = builtins.__import__
        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.name = kwargs["name"]
            def start(self):
                print("ORDINARY_THREAD:" + self.name, flush=True)
        def import_hook(name, *args, **kwargs):
            module = previous_import(name, *args, **kwargs)
            if name == "core.stores":
                sys.modules["core.stores"].make_stores = lambda *_a, **_kw: stores
            if name == "server":
                # Install only after the production daemon has imported normally.
                threading.Thread = FakeThread
            return module
        builtins.__import__ = import_hook
    '''))
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    env.update(PYTHONPATH=os.pathsep.join([str(tmp_path), str(root / "src")]),
               APPLIEDIN_DISCOVERY="off", LITELLM_LOCAL_MODEL_COST_MAP="True")
    result = subprocess.run(
        [sys.executable, "-m", "daemon"], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "ORDINARY_SERVER:8787" in result.stdout
    assert result.stdout.count("ORDINARY_THREAD:") == 3
    assert "ORDINARY_THREAD:evaluate" in result.stdout
    assert "ORDINARY_THREAD:apply" in result.stdout
    assert "ORDINARY_THREAD:heartbeat" in result.stdout
    assert "ORDINARY_THREAD:discovery" not in result.stdout


@pytest.mark.parametrize("extra_args", [
    ["--synthetic-browser-fixture", "--help"],
    ["--synthetic-browser-fixture", "--other"],
    ["--help"],
    ["--unknown-option"],
    ["--synthetic-browser-fixture", "--synthetic-browser-fixture"],
])
def test_invalid_daemon_cli_args_refuse_before_any_product_import(
        tmp_path, extra_args):
    """The old fallthrough imported stores/workers before noticing extra argv.

    Start a clean Python interpreter; intercept repository imports and socket
    operations *before* daemon imports. No real workers, BSK, or owner data.
    """
    site = tmp_path / "sitecustomize.py"
    site.write_text(r'''
import builtins
import socket
original_import = builtins.__import__
def checked_import(name, *args, **kwargs):
    if name in {"core.logging", "core.stores", "core.ids",
                "tools.browser_skill_fixture", "server"}:
        raise AssertionError("FORBIDDEN_PRODUCTION_IMPORT:" + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = checked_import
def no_network(*args, **kwargs):
    raise AssertionError("FORBIDDEN_NETWORK_OPERATION")
socket.socket.connect = no_network
socket.socket.bind = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network
''')
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.update(
        PYTHONPATH=os.pathsep.join([str(tmp_path), str(root / "src")]),
        APPLIEDIN_DISCOVERY="on",
        PYTHON_DOTENV_DISABLED="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        OPENAI_API_KEY="synthetic-not-a-real-secret",
    )
    child = subprocess.run(
        [sys.executable, "-m", "daemon", *extra_args],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=25)
    assert child.returncode != 0
    assert "Unsupported daemon arguments" in child.stderr
    assert "FORBIDDEN_PRODUCTION_IMPORT" not in child.stderr
    assert "FORBIDDEN_NETWORK_OPERATION" not in child.stderr
    assert not list(tmp_path.glob("*.lock"))
    assert not (tmp_path / ".local").exists()



@pytest.mark.parametrize("verb", ["submit", "continue"])
@pytest.mark.parametrize("change", [
    "baseline", "form_action", "formaction", "hold_readback", "late_status",
])
async def test_apply_native_submit_and_continue_guard_order_with_staged_pdf(
        authority, monkeypatch, verb, change):
    """Reach apply()'s native Submit/Continue JIT using full valid synthetic evidence.

    The actual fixture cannot upload or fill its ungranted second field. For
    this *late-guard-only* test, a test double supplies verified receipts and a
    previously staged PDF (never BrowserSkill upload IPC). Name still uses the
    real exact-grant controller and the closed facade permits its one fill.
    Every click remains forbidden beneath the controller.
    """
    current = synthetic_page()
    current["controls"][0].update(required=True, has_value=True,
                                  value="Test User")
    current["controls"][1].update(required=True, has_value=True,
                                  value="Synthetic answer")
    document_name = Path(authority.resume_path).name
    resume = {
        "selector": "#resume", "label": "Resume", "question": "",
        "tag": "input", "type": "file", "required": True,
        "disabled": False, "in_form": True,
        "form_selector": "#synthetic-form", "form_action": fixture.URL,
        "formaction": "", "files": [document_name],
    }
    current["controls"].insert(2, resume)
    button = current["controls"][-1]
    if verb == "continue":
        button.update(selector="#continue", label="Continue")
    button_action = {"action": "submit" if verb == "submit" else "click",
                     "selector": button["selector"]}
    # The second control is intentionally granted ONLY in this late-JIT test:
    # the normal native-positive fixture still gates that exact second fill.
    authority.row["human_approved_answers"]["Additional question"] = {
        "selector": "#additional", "label": "Additional question",
        "question": "", "url": fixture.URL, "value": "Synthetic answer",
    }
    observed = []
    underlying = []
    staged = []
    marks = []
    decisions = iter([
        {"action": "fill", "selector": "#name", "fact": "Name"},
        {"action": "fill", "selector": "#additional", "fact": "Additional question"},
        {"action": "upload", "selector": "#resume"},
        button_action,
    ])

    async def synthetic_decision(self, task, page, history, *, model):
        assert self is authority and model == "synthetic-fixture"
        choice = next(decisions)
        observed.append("decision:" + choice["action"])
        return choice

    monkeypatch.setattr(fixture._SyntheticRun, "decision", synthetic_decision)
    monkeypatch.setattr(bsk, "decision", _source_denied)

    actual_execute = forms.execute
    async def prevalidated_receipts(session, page, action, **kwargs):
        selector, kind = action.get("selector"), action.get("action")
        if selector == "#additional" and kind == "fill":
            control = forms.control(page, action)
            value = "Synthetic answer"
            forms.exact_approval(
                fixture.PK, "Additional question", value, target=control,
                url=page["url"], fixture_context=authority)
            forms.guarded_value(control, value)
            kwargs["filled"]["#additional"] = value
            staged.append("preverified_additional_receipt")
            return {"action": "fill", "label": control["label"],
                    "selector": selector, "question": control["question"],
                    "url": page["url"], "fact": "Additional question",
                    "approved_value": value}
        if selector == "#resume" and kind == "upload":
            control = forms.control(page, action)
            assert control["type"] == "file" and document_name in control["files"]
            assert Path(kwargs["resume_path"]).read_bytes().startswith(b"%PDF-")
            staged.append("preverified_attached_pdf")
            # Do not perform an upload, a real BSK call, or a premature
            # receiver check. The *final native button* owns the JIT predicate.
            return {"action": "upload", "label": "Resume", "uploaded": True}
        return await actual_execute(session, page, action, **kwargs)
    monkeypatch.setattr(forms, "execute", prevalidated_receipts)

    class FakeSession:
        def __init__(self, kind):
            assert kind == "apply"
        async def __aenter__(self):
            underlying.append(("session_start",))
            return self
        async def __aexit__(self, *args):
            underlying.append(("session_stop",))
        async def navigate(self, url):
            underlying.append(("navigate", url))
            return current
        async def page(self):
            underlying.append(("evaluate_PAGE",))
            return current
        async def call(self, *args, **kwargs):
            underlying.append(tuple(args))
            if args == ("fill", "#name", "--value", "Test User") and not kwargs:
                return {}
            pytest.fail("Unsafe BrowserSkill IPC escaped the closed fixture facade")
    monkeypatch.setattr(bsk, "Session", FakeSession)

    # Spies call the REAL predicates: their observed order must be meaningful,
    # and a removed guard must make the matching one-variable negative fail.
    checks = [
        ("receiver", "check_form_destination"),
        ("inventory", "check_form"),
        ("receipts", "check_approvals"),
        ("hold", "hold_possible_submission"),
        ("status", "check_before_committing_click"),
    ]
    for marker, attribute in checks:
        original = getattr(forms, attribute)
        def spy(*args, _original=original, _marker=marker, **kwargs):
            observed.append(_marker)
            return _original(*args, **kwargs)
        monkeypatch.setattr(forms, attribute, spy)

    if change in {"form_action", "formaction"}:
        button[change] = "https://foreign.example.test/collect"
    elif change == "hold_readback":
        original_read = fixture._SyntheticRun.is_held
        def fail_readback(self, pk):
            observed.append("hold_readback")
            assert self is authority and pk == fixture.PK
            return False
        monkeypatch.setattr(fixture._SyntheticRun, "is_held", fail_readback)
    elif change == "late_status":
        original_write = fixture._SyntheticRun.set_status
        def raced_status(self, pk, status, **attrs):
            original_write(self, pk, status, **attrs)
            self.row["status"] = "applied_manual"
            observed.append("synthetic_status_race")
        monkeypatch.setattr(fixture._SyntheticRun, "set_status", raced_status)

    # For every scenario the form has complete, independently inspectable
    # required values, exact receipts, native receiver and attached PDF.
    expected_filled = {"#name": "Test User", "#additional": "Synthetic answer"}
    forms.check_form(current, expected_filled, True, document_name)
    for field in ("Name", "Additional question"):
        approval = authority.row["human_approved_answers"][field]
        assert approval["url"] == fixture.URL
    assert Path(authority.resume_path).is_file()

    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS),
        "synthetic-fixture", pk=fixture.PK,
        resume_path=authority.resume_path, fixture_context=authority)
    assert staged == ["preverified_additional_receipt", "preverified_attached_pdf"]
    assert observed.count("decision:" + button_action["action"]) == 1
    assert authority.fills == 1
    assert underlying.count(("fill", "#name", "--value", "Test User")) == 1
    assert underlying.count(("navigate", fixture.URL)) == 1
    assert underlying.count(("session_start",)) == 1
    assert underlying.count(("session_stop",)) == 1
    assert not any(c[0] in {"click", "upload", "select", "choose"}
                   for c in underlying)

    if change == "baseline":
        # All real controller JIT checks passed and attempted a click, but
        # the facade denied it BEFORE raw BSK IPC. No submitted application.
        assert result["status"] == "uncertain", result
        assert "receiver" in observed and "inventory" in observed
        assert "receipts" in observed and "hold" in observed
        assert "status" in observed
        assert observed.index("receiver") < observed.index("inventory")
        assert observed.index("inventory") < observed.index("receipts")
        assert observed.index("receipts") < observed.index("hold")
        assert observed.index("hold") < observed.index("status")
        assert authority.hold and authority.row["status"] == "needs_human"
    elif change in {"form_action", "formaction"}:
        assert result["status"] == "gate" and "form destination" in result["question"]
        assert "receiver" in observed and "hold" not in observed
        assert not authority.hold and authority.row["status"] == "submitting"
    elif change == "hold_readback":
        assert result["status"] == "gate" and "hold could not be confirmed" in result["question"]
        assert "inventory" in observed and "receipts" in observed
        assert "hold" in observed and "hold_readback" in observed
        assert "status" not in observed and authority.row["status"] == "submitting"
    else:
        assert result["status"] == "gate" and "Human outcome changed" in result["question"]
        assert "inventory" in observed and "receipts" in observed
        assert "hold" in observed and "status" in observed
        assert "synthetic_status_race" in observed
        assert authority.row["status"] == "applied_manual"
