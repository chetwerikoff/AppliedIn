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
    # The safety controller first verifies a real tracked preparation row.
    # Exercise challenge classification *after* that invariant, not against
    # an intentionally missing dispatch identity.
    tracked = {"pk": PK, "company": "example-co", "job_id": "captcha",
               "jd_url": URL, "status": "submitting",
               "possible_submission": False, "ats": "custom"}
    monkeypatch.setattr(forms, "_row", lambda pk: tracked if pk == PK else {})
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
    if case == "control":
        # ControlGate clears stale button metadata, retaining code-owned diagnostics.
        assert result["step"]["last_button"] == ""
        assert "action=fill" in result["question"]
        assert "selector=[redacted]" in result["question"]
    else:
        # Ordinary Gates retain the handoff's sanitized missing-button marker.
        assert result["step"]["last_button"] == "(unavailable)"
        assert "Last button: (unavailable)." in result["question"]
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


@pytest.mark.parametrize("case", ["entry-visible-hcaptcha", "entry-response"])
async def test_unchanged_caller_persists_pre_submit_captcha_gate(monkeypatch, caplog, case):
    from agent import run
    from core import events, profiles, rotation
    from tools import browser_apply, browser_runtime, credentials

    result = await synthetic_controller(monkeypatch, case)
    if case == "entry-response":
        assert result["captcha_artifact_diagnostic"] is True
        # Owner-only synthetic text must survive storage but not direct logging.
        result["question"] += " OWNER_GATE_ONLY_SENTINEL"

    class Tracking:
        def __init__(self):
            from tools import posting_guard as pg
            checked = pg.analyze("Synthetic engineering job. " * 40)
            self.row = {**{"pk": PK, "status": "tailored",
                        "company": "example-co", "jd_url": URL}, "jd_text": checked["text"],
                        "posting_guard": checked["posting_guard"]}
            self.writes = []

        def get(self, pk):
            assert pk == PK
            return dict(self.row)

        def get_current(self, pk):
            return self.get(pk)

        def update_posting_guard(self, pk, observed, inspected=None, *,
                                 reviewed_generation=None, source_witness=False,
                                 settle_refusal=False):
            from core.storage.base import guard_observation_matches, guard_patch
            current = self.get_current(pk)
            if not guard_observation_matches(current, observed):
                return False
            patch = guard_patch(current, inspected,
                                reviewed_generation=reviewed_generation,
                                source_witness=source_witness,
                                settle_refusal=settle_refusal)
            if patch is None:
                return False
            self.row.update(patch)
            return True

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
    from tools import posting_guard as pg
    monkeypatch.setattr(run, "_jd_meta",
                        AsyncMock(return_value=pg.analyze("Synthetic engineering job. " * 40)))
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

    import logging
    with caplog.at_level(logging.INFO):
        consumed = await run._apply_direct(PK, stores)
    expected = {"result": "gated", "pk": PK, "question": result["question"]}
    if case == "entry-response":
        expected["captcha_artifact_diagnostic"] = True
    assert consumed == expected
    assert tracking.row["status"] == "needs_human"
    assert tracking.row["gate_reason"] == (
        "unknown_field" if case == "entry-response" else "captcha")
    assert tracking.row["gate_pending"]["question"] == result["question"]
    assert not any(kind == "captcha_wait" for kind, _ in emitted)
    if case == "entry-response":
        assert "OWNER_GATE_ONLY_SENTINEL" not in caplog.text
        assert "OWNER_GATE_ONLY_SENTINEL" not in repr(emitted)
        assert any(kind == "gate" and details["detail"] ==
                   "captcha-artifact: human inspection required"
                   for kind, details in emitted)



@pytest.mark.parametrize("reader", ["page", "witness"])
@pytest.mark.parametrize("scenario,expected_issue", [
    ("orphan", "provider-unproven"),
    ("duplicate", "duplicate-name"),
    ("linked", "linked-receiver"),
])
def test_production_reader_identifies_ambiguous_response_without_value(reader, scenario,
                                                                         expected_issue):
    from tests.tools.test_browser_application_entry import dom
    script = forms.ENTRY_PAGE if reader == "page" else forms._ENTRY_FIXED_CONTROL_WITNESS
    spec = {
        "url": URL, "frames": ["H"] if scenario == "orphan" else ["R"],
        "tokens": (["g-recaptcha-response"] * (2 if scenario == "duplicate" else 1)),
        "tokenBadge": True, "textarea": True, "claim": scenario == "linked",
        "tokenEdit": {
            "id": "g-recaptcha-response-1234",
            "data-private": "RESPONSE_ATTRIBUTE_SENTINEL",
            "title": "RESPONSE_TITLE_SENTINEL",
        },
    }
    page = dom(spec, script)
    assert page["entry"]["response_error"] is True
    assert all(c["selector"] != "#g-recaptcha-response-1234"
               for c in page["controls"])
    observed = page["entry"]["responses"][0]["diagnostic"]
    assert observed["tag"] == "textarea"
    assert observed["name_pattern"] == "g-recaptcha-response"
    assert observed["id_pattern"] == "g-recaptcha-response[-digits]"
    assert observed["ancestor"] in {"none", "root-unknown", "entry-root"}
    assert observed["owner"] in {"none", "root-unknown", "entry-root"}
    assert observed["provider"] == "recaptcha"
    assert observed["badge"] == "yes"
    assert expected_issue in observed["issues"]
    with pytest.raises(forms.CaptchaArtifactGate, match="untrusted captcha response control") as raised:
        forms._entry_safety(page, lambda url: url == URL, None)
    detail = str(raised.value)
    assert "control[1] tag=textarea" in detail
    assert "name_pattern=g-recaptcha-response" in detail
    assert "id_pattern=g-recaptcha-response[-digits]" in detail
    assert "ancestor=" in detail and "owner=" in detail
    assert "provider=recaptcha" in detail and "provider_frame=" in detail
    assert "badge=yes" in detail and f"issue={expected_issue}" in detail
    for secret in ("RESPONSE_ATTRIBUTE_SENTINEL", "RESPONSE_TITLE_SENTINEL",
                   "PROTECTED TOKEN VALUE READ", "hidden_value", "selected_value"):
        assert secret not in repr(page["entry"]["responses"])
        assert secret not in detail


@pytest.mark.parametrize("reader", ["page", "witness"])
def test_response_id_and_owner_injection_never_reaches_owner_detail(reader):
    from tests.tools.test_browser_application_entry import dom
    script = forms.ENTRY_PAGE if reader == "page" else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom({
        "url": URL, "frames": [], "tokens": ["g-recaptcha-response"],
        "tokenEdit": {"id": "PRIVATE_ID_SENTINEL", "data-secret": "PRIVATE_ATTR_SENTINEL"},
    }, script)
    assert page["entry"]["response_error"] is True
    diagnostic = page["entry"]["responses"][0]["diagnostic"]
    assert diagnostic["id_pattern"] == "other-id"
    # Even a manipulated metadata response cannot turn a page string into text.
    diagnostic.update(tag="PRIVATE_TAG_SENTINEL",
                      name_pattern="PRIVATE_NAME_SENTINEL",
                      owner="PRIVATE_OWNER_SENTINEL",
                      issues=["provider-unproven", "PRIVATE_ISSUE_SENTINEL"])
    with pytest.raises(forms.CaptchaArtifactGate) as raised:
        forms._entry_safety(page, lambda url: url == URL, None)
    detail = str(raised.value)
    for secret in ("PRIVATE_ID_SENTINEL", "PRIVATE_ATTR_SENTINEL",
                   "PRIVATE_TAG_SENTINEL", "PRIVATE_NAME_SENTINEL",
                   "PRIVATE_OWNER_SENTINEL", "PRIVATE_ISSUE_SENTINEL"):
        assert secret not in detail
    assert "tag=unknown" in detail and "name_pattern=unknown" in detail
    assert "owner=unknown" in detail and "id_pattern=other-id" in detail


def test_queued_captcha_artifact_logs_only_neutral_detail(monkeypatch, caplog):
    import logging
    from agent import run

    sentinel = "QUEUE_PRIVATE_OWNER_ONLY_SENTINEL"
    record = {"result": "gated", "pk": PK,
              "question": "Synthetic CAPTCHA response " + sentinel,
              "captcha_artifact_diagnostic": True}

    async def fake_direct(pk, stores):
        assert pk == PK
        return deepcopy(record)

    class Queue:
        def __init__(self):
            self.done_items = []

        def done(self, item):
            self.done_items.append(item)

        def retry(self, *_):
            pytest.fail("Terminal owner Gate cannot be automatically retried")

    monkeypatch.setattr(run, "make_stores", lambda: object())
    monkeypatch.setattr(run, "_apply_direct", fake_direct)
    q = Queue()
    with caplog.at_level(logging.INFO):
        result = run.run_queued({"pk": PK}, q)
    assert result == record
    assert q.done_items == [{"pk": PK}]
    assert sentinel not in caplog.text
    assert "captcha-artifact: human inspection required" in caplog.text
