"""Offline CAPTCHA classification: only positive visible challenges get a typed Gate.

Every page, route, applicant, job and status store in these tests is synthetic.
No BrowserSkill process, provider, real browser, Redis or application is contacted.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms


URL = "https://example.test/jobs/synthetic"
PK = "example-co#captcha"
PORTAL = forms.GreenhouseJobAlias(
    pk=PK, company="example-co", ats="greenhouse", job_id="synthetic",
    wrapper="https://careers.example.test/jobs/synthetic",
    careers_url="https://careers.example.test/",
    board="example-co", url=URL,
)


def observation(case):
    page = {
        "url": URL, "controls": [], "inventory_verified": True,
        "truncated": False, "opaque_controls": False,
        "shadow_roots": False, "unsupported_frames": [],
    }
    if case.startswith("greenhouse-"):
        status = case.removeprefix("greenhouse-")
        page["greenhouse"] = {
            "frames": [{"status": status}] if status not in {"response", "malformed"} else [],
            "root": None, "response_controls": status == "response",
        }
        if status == "malformed":
            page["greenhouse"]["frames"] = None
    else:
        entry = {"frames": [], "root": None, "response_error": False,
                 "helper_challenge": False, "responses": []}
        if case.startswith("entry-"):
            status = case.removeprefix("entry-")
            if status == "helper":
                entry["helper_challenge"] = True
            elif status == "response":
                entry["response_error"] = True
            elif status == "malformed":
                entry["frames"] = None
            else:
                entry["frames"] = [{"status": status}]
        page["entry"] = entry
    return page


def classify(page):
    if "greenhouse" in page:
        forms._greenhouse_safety(page, lambda url: url == URL, PORTAL)
    else:
        forms._entry_safety(page, lambda url: url == URL, None)


@pytest.mark.parametrize("case,fragment", [
    ("entry-visible-hcaptcha", "visible hCaptcha challenge;"),
    ("entry-visible-recaptcha", "visible reCAPTCHA Enterprise challenge;"),
    ("entry-helper", "visible hCaptcha challenge control;"),
    ("greenhouse-visible-recaptcha", "visible reCAPTCHA Enterprise challenge;"),
])
def test_only_positive_existing_visible_challenges_are_typed(case, fragment):
    with pytest.raises(forms.CaptchaGate, match=fragment) as raised:
        classify(observation(case))
    assert isinstance(raised.value, forms.Gate)
    assert raised.value.form_question is None


@pytest.mark.parametrize("case", [
    "entry-unknown", "entry-Z", "entry-response", "entry-malformed",
    "greenhouse-unknown", "greenhouse-response", "greenhouse-malformed",
])
def test_ambiguous_untrusted_or_malformed_observations_remain_ordinary_gates(case):
    with pytest.raises(forms.Gate) as raised:
        classify(observation(case))
    assert not isinstance(raised.value, forms.CaptchaGate)


@pytest.mark.parametrize("status", ["H", "R", "W", "X"])
def test_passive_frames_do_not_become_captcha_gates_from_an_empty_response(status):
    page = observation("entry-" + status)
    # A passive provider response is reserved metadata, not proof of a challenge.
    page["entry"]["responses"] = [{"name": "g-recaptcha-response", "value": ""}]
    classify(page)


async def synthetic_controller(monkeypatch, case):
    page = observation(case)
    calls = []

    class Session:
        def __init__(self, purpose):
            assert purpose == "apply"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def navigate(self, address):
            calls.append(("navigate", address))
            assert address == URL
            return deepcopy(page)

        async def call(self, *args):
            pytest.fail("A terminal Gate cannot perform browser mutations")

    async def bootstrap(_session, current, *args, **kwargs):
        if case == "ordinary":
            raise forms.Gate("A synthetic form question requires manual review.",
                             form_question={"label": "Synthetic question"})
        if case == "control":
            raise forms.ControlGate({
                "action": "fill", "selector": "[redacted]",
                "reason": "absent: 0 matches", "caller": "decision-initial",
            })
        classify(current)
        pytest.fail("Test case must hit a terminal safety Gate before model decisions")

    from core import events
    from tools import browser_apply, claude_chrome
    monkeypatch.setattr(forms, "check_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(forms, "_row", lambda pk: {})
    monkeypatch.setattr(forms, "_bootstrap", bootstrap)
    monkeypatch.setattr(bsk, "Session", Session)
    monkeypatch.setattr(bsk, "decision", AsyncMock(side_effect=AssertionError("model called")))
    monkeypatch.setattr(browser_apply, "_site_rules", lambda *a, **k: "")
    monkeypatch.setattr(claude_chrome, "_stage_resume", lambda *a, **k: "/synthetic/resume.pdf")
    monkeypatch.setattr(events, "emit", lambda *a, **k: None)

    result = await forms.apply(
        URL, "example-co", {}, "synthetic", pk=PK, resume_path="/synthetic/resume.pdf")
    assert calls == [("navigate", URL)]
    assert result["status"] == "gate"
    assert result["question"] and URL in result["question"]
    assert result["step"]["url"] == URL
    assert result["step"]["last_button"] == ""
    return result


@pytest.mark.parametrize("case,reason", [
    ("entry-visible-hcaptcha", "captcha"),
    ("entry-visible-recaptcha", "captcha"),
    ("entry-helper", "captcha"),
    ("greenhouse-visible-recaptcha", "captcha"),
    ("entry-unknown", "unknown_field"),
    ("entry-Z", "unknown_field"),
    ("entry-response", "unknown_field"),
    ("greenhouse-unknown", "unknown_field"),
    ("greenhouse-response", "unknown_field"),
    ("ordinary", "unknown_field"),
    ("control", "unknown_field"),
])
async def test_pre_submit_controller_gate_shape_and_reason(monkeypatch, case, reason):
    result = await synthetic_controller(monkeypatch, case)
    assert result["reason"] == reason
    assert result["form_question"] == (
        {"label": "Synthetic question"} if case == "ordinary" else None)
    if case == "control":
        assert result["step"]["caller"] == "decision-initial"
        assert result["step"]["reason"] == "absent: 0 matches"


async def test_unchanged_caller_persists_pre_submit_captcha_gate(monkeypatch):
    from agent import run
    from core import events, profiles, rotation
    from tools import browser_apply, browser_runtime, credentials

    result = await synthetic_controller(monkeypatch, "entry-visible-hcaptcha")

    class Tracking:
        def __init__(self):
            self.row = {"pk": PK, "status": "tailored",
                        "company": "example-co", "jd_url": URL}
            self.writes = []

        def get(self, pk):
            assert pk == PK
            return dict(self.row)

        def set_status(self, pk, status, **kwargs):
            assert pk == PK
            self.writes.append((status, kwargs))
            self.row.update(status=getattr(status, "value", status), **kwargs)

    tracking = Tracking()
    stores = SimpleNamespace(
        tracking=tracking,
        answer_bank=SimpleNamespace(all_facts=lambda company: {}),
        secrets=None,
    )
    emitted = []

    async def forward(*args, **kwargs):
        return deepcopy(result)

    monkeypatch.setattr(run.submit_hold, "blocked", lambda *a, **k: False)
    monkeypatch.setattr(run, "_jd_text", AsyncMock(return_value="Synthetic engineering job. " * 40))
    monkeypatch.setattr(run, "_no_sponsorship", lambda *a, **k: False)
    monkeypatch.setattr(run, "get_settings", lambda: SimpleNamespace(browser_model="synthetic"))
    monkeypatch.setattr(run, "_github_context", lambda: "")
    monkeypatch.setattr(run, "_resume_pdf_path", lambda row: "/synthetic/resume.pdf")
    monkeypatch.setattr(rotation, "ensure", lambda *a, **k: None)
    monkeypatch.setattr(profiles, "resolve_for", lambda row: None)
    monkeypatch.setattr(profiles, "expand_all", lambda original: original)
    monkeypatch.setattr(credentials, "get_login", lambda *a: None)
    monkeypatch.setattr(browser_runtime, "configuration", lambda: {"engine": "browser_skill"})
    monkeypatch.setattr(browser_apply, "apply", forward)
    monkeypatch.setattr(events, "emit", lambda kind, **kw: emitted.append((kind, kw)))

    consumed = await run._apply_direct(PK, stores)
    assert consumed == {"result": "gated", "pk": PK, "question": result["question"]}
    assert tracking.row["status"] == "needs_human"
    assert tracking.row["gate_reason"] == "captcha"
    assert tracking.row["gate_pending"]["question"] == result["question"]
    assert not any(kind == "captcha_wait" for kind, _ in emitted)
