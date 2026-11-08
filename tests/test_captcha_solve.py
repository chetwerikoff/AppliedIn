"""Offline tests for scripts/captcha_solve.py (no network, no browser)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "captcha_solve.py"
_spec = importlib.util.spec_from_file_location("captcha_solve", _PATH)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


# ---------------------------------------------------------------- guard

@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/123",
    "https://linkedin.com/checkpoint/challenge",
    "https://lnkd.in/abc",
    "https://boards.greenhouse.io/acme/login",
    "https://acme.wd3.myworkdayjobs.com/en-US/careers/SignIn",
    "https://careers.acme.com/create-account?next=/apply",
    "https://jobs.acme.com/forgot-password",
    "file:///tmp/x.html",
])
def test_guard_refuses(url):
    with pytest.raises(cs.CaptchaError) as exc:
        cs.guard(url, False)
    assert exc.value.code == cs.EXIT_REFUSED


def test_guard_refuses_password_field():
    with pytest.raises(cs.CaptchaError) as exc:
        cs.guard("https://jobs.ashbyhq.com/acme/123/application", True)
    assert exc.value.code == cs.EXIT_REFUSED


@pytest.mark.parametrize("url", [
    "https://jobs.ashbyhq.com/openai/ca7e4019/application",
    "https://boards.greenhouse.io/acme/jobs/42",
    "https://www.databricks.com/company/careers/open-positions/job?gh_jid=1",
])
def test_guard_allows_application_forms(url):
    cs.guard(url, False)


# ---------------------------------------------------------------- challenge choice

def test_invisible_badge_is_not_a_challenge():
    found = [{"kind": "recaptcha", "sitekey": "K", "enterprise": True, "frame": "anchor",
              "size": "invisible", "visible": True}]
    with pytest.raises(cs.CaptchaError) as exc:
        cs.pick_challenge(found)
    assert exc.value.code == cs.EXIT_NONE


def test_checkbox_challenge_enterprise():
    found = [
        {"kind": "recaptcha", "sitekey": "K", "size": "", "visible": True},
        {"kind": "recaptcha", "sitekey": "K", "enterprise": True, "frame": "anchor", "size": "normal", "visible": True},
    ]
    chosen = cs.pick_challenge(found)
    task = cs.build_task(chosen, "https://x.example/apply")
    assert task == {"type": "ReCaptchaV2EnterpriseTaskProxyLess", "websiteURL": "https://x.example/apply",
                    "websiteKey": "K"}


def test_popped_image_challenge_is_invisible_task():
    found = [
        {"kind": "recaptcha", "sitekey": "K", "frame": "anchor", "size": "invisible", "visible": True},
        {"kind": "recaptcha", "sitekey": "K", "frame": "bframe", "size": "", "visible": True},
    ]
    task = cs.build_task(cs.pick_challenge(found), "https://x.example/a")
    assert task["type"] == "ReCaptchaV2TaskProxyLess"
    assert task["isInvisible"] is True


def test_hidden_bframe_is_not_a_challenge():
    found = [{"kind": "recaptcha", "sitekey": "K", "frame": "bframe", "visible": False}]
    with pytest.raises(cs.CaptchaError) as exc:
        cs.pick_challenge(found)
    assert exc.value.code == cs.EXIT_NONE


def test_turnstile_task_with_metadata():
    found = [{"kind": "turnstile", "sitekey": "0x4AAA", "visible": True, "action": "apply", "cdata": ""}]
    task = cs.build_task(cs.pick_challenge(found), "https://x.example/a")
    assert task == {"type": "AntiTurnstileTaskProxyLess", "websiteURL": "https://x.example/a",
                    "websiteKey": "0x4AAA", "metadata": {"action": "apply"}}


def test_hcaptcha_is_unsupported():
    with pytest.raises(cs.CaptchaError) as exc:
        cs.pick_challenge([{"kind": "hcaptcha", "sitekey": "H", "visible": True}])
    assert exc.value.code == cs.EXIT_UNSUPPORTED


def test_two_different_widgets_are_ambiguous():
    found = [{"kind": "recaptcha", "sitekey": "A", "size": "", "visible": True},
             {"kind": "turnstile", "sitekey": "0xB", "visible": True}]
    with pytest.raises(cs.CaptchaError) as exc:
        cs.pick_challenge(found)
    assert exc.value.code == cs.EXIT_UNSUPPORTED


# ---------------------------------------------------------------- CapSolver client

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(replies, calls):
    def opener(req, timeout):
        calls.append((req.full_url, json.loads(req.data)))
        return _Resp(json.dumps(replies.pop(0)).encode())
    return opener


def test_solve_polls_until_ready():
    calls = []
    replies = [{"errorId": 0, "taskId": "T1"},
               {"errorId": 0, "status": "processing"},
               {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "TOKEN"}}]
    task_id, token = cs.solve({"type": "X"}, "KEY", opener=_opener(replies, calls), sleep=lambda s: None)
    assert (task_id, token) == ("T1", "TOKEN")
    assert calls[0][0].endswith("/createTask") and calls[0][1]["clientKey"] == "KEY"
    assert calls[1][1] == {"clientKey": "KEY", "taskId": "T1"}


def test_solve_reports_api_error():
    calls = []
    replies = [{"errorId": 1, "errorCode": "ERROR_KEY_DENIED_ACCESS", "errorDescription": "bad key"}]
    with pytest.raises(cs.CaptchaError) as exc:
        cs.solve({"type": "X"}, "KEY", opener=_opener(replies, calls), sleep=lambda s: None)
    assert exc.value.code == cs.EXIT_ERROR and "KEY_DENIED" in exc.value.reason


def test_solve_times_out():
    calls = []
    replies = [{"errorId": 0, "taskId": "T"}] + [{"errorId": 0, "status": "processing"}] * 10
    ticks = iter(range(0, 10_000, 100))
    with pytest.raises(cs.CaptchaError) as exc:
        cs.solve({"type": "X"}, "KEY", opener=_opener(replies, calls), sleep=lambda s: None,
                 clock=lambda: next(ticks))
    assert "timeout" in exc.value.reason


# ---------------------------------------------------------------- key loading

def test_key_from_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    (tmp_path / "secrets.json").write_text(json.dumps({"capsolver": {"api_key": "CAP-1"}}))
    assert cs.load_api_key(tmp_path) == "CAP-1"


def test_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    with pytest.raises(cs.CaptchaError):
        cs.load_api_key(tmp_path)


# ---------------------------------------------------------------- end to end with fake bsk

def test_run_refuses_linkedin_without_solver(tmp_path, monkeypatch):
    page = {"__cs": 1, "url": "https://www.linkedin.com/jobs/view/1", "password": False,
            "found": [{"kind": "recaptcha", "sitekey": "K", "size": "", "visible": True}]}

    def fake_eval(session, tab, expr, runner=None):
        return page

    def no_solve(*a, **k):
        raise AssertionError("solver must not be called")

    monkeypatch.setattr(cs, "evaluate", fake_eval)
    monkeypatch.setattr(cs, "solve", no_solve)
    code, log = cs.run("S", None, False, tmp_path)
    assert code == cs.EXIT_REFUSED and log["result"] == "refused"


def test_run_injects_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "KEY")
    page = {"__cs": 1, "url": "https://jobs.ashbyhq.com/acme/1/application?utm=x", "password": False,
            "found": [{"kind": "recaptcha", "sitekey": "K", "frame": "anchor", "size": "normal",
                       "enterprise": True, "visible": True}]}
    seen = []

    def fake_eval(session, tab, expr, runner=None):
        seen.append(expr)
        if len(seen) == 1:
            return page
        return {"__cs": 1, "fields": 1, "callbacks": 1, "called": 1, "cbError": ""}

    monkeypatch.setattr(cs, "evaluate", fake_eval)
    monkeypatch.setattr(cs, "solve", lambda task, key: ("T9", "TOKEN-XYZ"))
    code, log = cs.run("S", None, False, tmp_path)
    assert code == cs.EXIT_OK and log["result"] == "injected"
    assert '"TOKEN-XYZ"' in seen[1] and '"recaptcha"' in seen[1]
    assert log["url"] == "https://jobs.ashbyhq.com/acme/1/application"
    assert "TOKEN" not in json.dumps(log)


def test_run_several_callbacks_is_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "KEY")
    page = {"__cs": 1, "url": "https://x.example/apply", "password": False,
            "found": [{"kind": "turnstile", "sitekey": "0xA", "visible": True}]}
    replies = iter([page, {"__cs": 1, "fields": 1, "callbacks": 2, "called": 0, "cbError": ""}])
    monkeypatch.setattr(cs, "evaluate", lambda *a, **k: next(replies))
    monkeypatch.setattr(cs, "solve", lambda task, key: ("T", "TOK"))
    code, _ = cs.run("S", None, False, tmp_path)
    assert code == cs.EXIT_UNSUPPORTED


def test_evaluate_passes_sync_context():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        if argv[1:3] == ["cooperate", "sync"]:
            out = {"context_id": "C1", "observation_id": "O1", "observation": {"observation_id": "R1"}}
        else:
            out = {"result": {"value": json.dumps({"__cs": 1, "url": "u"})}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    res = cs.evaluate("S", "TAB", "1+1", runner=runner)
    assert res == {"__cs": 1, "url": "u"}
    ev = calls[1]
    assert ev[ev.index("--context") + 1] == "C1"
    assert ev[ev.index("--observation") + 1] == "O1"
    assert ev[ev.index("--tab-id") + 1] == "TAB"


def test_save_key_keeps_other_secrets(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"imap": {"host": "h"}}))
    cs.save_api_key(tmp_path, "  CAP-2\n")
    data = json.loads(path.read_text())
    assert data == {"imap": {"host": "h"}, "capsolver": {"api_key": "CAP-2"}}
    assert (path.stat().st_mode & 0o777) == 0o600


def test_save_key_refuses_empty(tmp_path):
    with pytest.raises(cs.CaptchaError):
        cs.save_api_key(tmp_path, "   ")
    assert not (tmp_path / "secrets.json").exists()


def test_balance_parses():
    calls = []
    opener = _opener([{"errorId": 0, "balance": 12.5}], calls)
    assert cs.balance("KEY", opener=opener) == 12.5
    assert calls[0][0].endswith("/getBalance")
