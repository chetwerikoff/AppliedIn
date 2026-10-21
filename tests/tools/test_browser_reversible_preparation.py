"""Issue #139 / ARCH 07:34: legacy shape diagnostics are not Submit authority.

Only synthetic DOM, row, PDF, and BrowserSkill command spies are used.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ids import make_pk
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms


COMPANY = "example-co"
JOB = "12345678-1234-4234-8234-123456789abc"
PK = make_pk(COMPANY, JOB)
POSTING = f"https://jobs.ashbyhq.com/{COMPANY}/{JOB}"
FORM_URL = POSTING + "/application"
PRIVACY = "I acknowledge processing of personal data for this application"


def _field(selector, label, kind, **extra):
    return {
        "selector": selector, "label": label, "question": "",
        "type": kind, "tag": "input", "required": True,
        "disabled": False, "in_form": True, "form_selector": "#app",
        "form_action": FORM_URL, "formaction": "", "checked": False,
        "value": "", "has_value": False, "files": [], **extra,
    }


def _page():
    picker = _field(
        "#picker", "Work location", "combobox", name="work_location",
        combobox={"linked": True, "link_id": "listbox",
                  "listbox_selector": "#listbox",
                  "options": [{"selector": "#remote", "label": "Remote",
                               "value": "remote", "unique": True,
                               "safe": True, "submit": False, "navigates": False}],
                  "selected_text": "", "displayed_text": "",
                  "selected_value": "", "hidden_selector": "", "hidden_value": ""})
    controls = [
        _field("#name", "Full name", "text"),
        picker,
        _field("#native", "Work arrangement", "select", tag="select",
               options=[{"value": "hybrid", "label": "Hybrid"}]),
        _field("#privacy", "I agree", "checkbox", question=PRIVACY, value="on"),
        _field("#radio-no", "No", "radio", question="Relocation preference",
               name="mobility", value="no"),
        _field("#radio-yes", "Yes", "radio", question="Relocation preference",
               name="mobility", value="yes"),
        _field("#pdf", "Resume", "file", required=False),
        _field("#custom", "Additional question", "div", tag="div"),
        _field("#submit", "Submit application", "submit", tag="button",
               submit=True, required=False, native_form_owner_selector="#app"),
    ]
    return {"url": FORM_URL, "title": "Synthetic Role", "controls": controls,
            "inventory_verified": False, "truncated": False,
            "opaque_controls": False, "shadow_roots": False,
            "unsupported_frames": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_error,missing_evidence", [
    ("ambiguous application roots", "opaque_controls"),
    ("application root sibling identity mismatch", "unsupported_frames"),
    ("truncated application inventory after bounded wait", "truncated"),
    ("unsupported custom control", None),
])
async def test_legacy_shape_does_not_stop_ordered_preparation_but_final_guard_refuses(
        monkeypatch, tmp_path, legacy_error, missing_evidence):
    """ARCH 07:34: prepare with approved writes, never use shape as Submit proof."""
    pdf = tmp_path / "Test User Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsynthetic row-specific content\n")
    page = _page()
    if missing_evidence == "opaque_controls":
        page["opaque_controls"] = True
    elif missing_evidence == "unsupported_frames":
        page["unsupported_frames"] = ["https://example.test/synthetic-frame"]
    elif missing_evidence == "truncated":
        page["truncated"] = True
    if missing_evidence:
        page["inventory_verified"] = False
    else:
        page["inventory_verified"] = True

    row = {"pk": PK, "company": COMPANY, "job_id": JOB, "jd_url": POSTING,
           "ats": "ashby", "title": "Synthetic Role", "status": "submitting",
           "resume_seed": "synthetic-seed", "human_approved_answers": {
               "Relocation preference": {"value": "No"}}}
    approvals = {"Full name": "Test User", "Work location": "Remote",
                 "Work arrangement": "Hybrid", PRIVACY: "yes",
                 "Relocation preference": "No"}
    monkeypatch.setattr(forms, "_row", lambda *_args, **_kw: row)

    def exact_approval(pk, question, value, **_kw):
        assert pk == PK
        if approvals.get(question) != value:
            raise forms.Gate("The exact synthetic approved answer changed.")

    monkeypatch.setattr(forms, "exact_approval", exact_approval)
    monkeypatch.setattr(forms, "check_dispatch", lambda *_args, **_kw: None)
    monkeypatch.setattr(forms, "_configured_alias", lambda *_args, **_kw: object())
    monkeypatch.setattr(forms, "_alias_current", lambda *_args, **_kw: True)
    monkeypatch.setattr(forms, "navigation_allowed",
                        lambda href, *_args, **_kw: href == FORM_URL)
    # Exercise the real production preparation/row/seed gates using a valid
    # canonical HTTPS tracked row. The ordinary daemon-only fixture capability
    # must never be manufactured by SimpleNamespace in a unit test.
    from core import stores
    from agent import run
    monkeypatch.setattr(stores, "make_stores", lambda: SimpleNamespace(
        tracking=SimpleNamespace(r=SimpleNamespace(get=lambda _key: None))))
    monkeypatch.setattr(run, "_resume_pdf_path", lambda _row: str(pdf))
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")

    effects, committing, transmissions = [], [], []
    nodes = {c["selector"]: c for c in page["controls"]}

    async def fake_command(*args, **_kw):
        op = args[0]
        if op == "evaluate":
            return {"value": page}
        effects.append((op, args[1]))
        # Simulate employer-side autosave and upload transmission without
        # making any network request. Transmitted preparation is not Submit.
        if op == "upload":
            transmissions.append(("upload-endpoint", args[1]))
        elif op in {"fill", "select"} or (op == "click" and args[1] in {
                "#remote", "#privacy", "#radio-no"}):
            transmissions.append(("autosave", args[1]))
        if op == "fill":
            nodes["#name"].update(value="Test User", has_value=True)
        elif (op, args[1]) == ("click", "#remote"):
            nodes["#picker"]["combobox"].update(
                selected_text="Remote", displayed_text="Remote",
                selected_value="remote")
            # A visible caption alone cannot prove the form submits that
            # choice. Reproduce a named native receiver with its exact code.
            nodes["#picker"].update(value="remote", has_value=True)
        elif op == "select":
            nodes["#native"].update(value="hybrid", has_value=True)
        elif (op, args[1]) == ("click", "#privacy"):
            nodes["#privacy"]["checked"] = True
        elif (op, args[1]) == ("click", "#radio-no"):
            nodes["#radio-no"]["checked"] = True
        elif op == "upload":
            nodes["#pdf"]["files"] = [Path(args[3]).name]
        return {}

    async def legacy_entry(*_args, **_kw):
        raise forms.Gate(legacy_error + "; inspect manually.")

    monkeypatch.setattr(bsk, "command", fake_command)
    monkeypatch.setattr(forms, "_stable_application_form", legacy_entry)
    controller = forms.ApplicationIPCController(
        PK, COMPANY, POSTING, str(pdf),
        {"Full name": "Test User", "Work location": "Remote",
         "Work arrangement": "Hybrid"}, {}, [], {},
        lambda: committing.append("commit"),
        seal_stage=True)
    raw = bsk.Session("apply")
    raw.id = "synthetic-session"
    raw.bind_application_controller(controller)
    try:
        selected, observed = await forms._entry_or_reversible_page(
            object(), raw, lambda href: forms._preparation_origin(href, POSTING),
            None, deadline=0)
        assert selected is raw and observed is page
        await raw.application_effect("fill", "#name", "--value", "Test User")
        await raw.application_effect("click", "#picker")
        await raw.application_effect("click", "#remote")
        await raw.application_effect("select", "#native", "--value", "hybrid")
        await raw.application_effect("click", "#privacy")
        await raw.application_effect("click", "#radio-no")
        await raw.application_effect("upload", "#pdf", "--file",
                                     controller.resume_path, timeout=130)
        assert effects == [("fill", "#name"), ("click", "#picker"),
                           ("click", "#remote"), ("select", "#native"),
                           ("click", "#privacy"), ("click", "#radio-no"),
                           ("upload", "#pdf")]
        assert controller.upload_completed
        assert transmissions == [
            ("autosave", "#name"), ("autosave", "#remote"),
            ("autosave", "#native"), ("autosave", "#privacy"),
            ("autosave", "#radio-no"), ("upload-endpoint", "#pdf")]
        # Controller receipts are normally populated by execute(), not by raw
        # producer IPC. Supply the synthetic readbacks to reach the *unchanged*
        # production final verdict rather than manufacturing a second guard.
        controller.filled.update({
            "#name": "Test User", "#picker": "Remote", "#native": "hybrid",
            "#privacy": "on", "#radio-no": "no"})
        controller.history.extend([
            {"action": "select", "selector": "#native",
             "approved_value": "Hybrid"},
            {"action": "choose", "selector": "#privacy",
             "approved_value": "yes"}])
        expected = ("Unsupported custom control" if missing_evidence is None
                    else "form inventory|uninspectable")
        with pytest.raises(forms.Gate, match=expected):
            await raw.application_effect("click", "#submit")
        assert not committing
        assert not any(op == "click" and selector == "#submit"
                       for op, selector in effects)
    finally:
        controller.release_snapshot()


@pytest.mark.asyncio
async def test_legacy_shape_fallback_still_refuses_unknown_landing(monkeypatch):
    """ARCH 07:34 never turns a foreign redirect into preparation authority."""
    async def legacy_entry(*_args, **_kw):
        raise forms.Gate("ambiguous application roots; inspect manually.")

    class ForeignSession:
        async def page(self):
            return {"url": "https://unapproved.example.test/application",
                    "controls": []}

    monkeypatch.setattr(forms, "_stable_application_form", legacy_entry)
    with pytest.raises(forms.Gate, match="approved origin"):
        await forms._entry_or_reversible_page(
            object(), ForeignSession(),
            lambda href: forms._preparation_origin(href, POSTING),
            None, deadline=0)



@pytest.mark.asyncio
async def test_native_ashby_preparation_does_not_require_old_owner_certificate(
        monkeypatch, tmp_path):
    """Only the final guard refuses two native Submit owners; preparation can read."""
    from tools.claude_chrome import GreenhouseJobAlias

    page = _page()
    page.update(inventory_verified=True, entry={
        "root": None, "native_candidates": [], "frames": [],
        "response_error": False, "helper_challenge": False})
    page["controls"] = [c for c in page["controls"]
                        if c["selector"] in {"#name", "#pdf", "#submit"}]
    first = next(c for c in page["controls"] if c["selector"] == "#submit")
    second = dict(first, selector="#other-submit", form_selector="#another-form",
                  native_form_owner_selector="#another-form")
    page["controls"].append(second)
    provenance = forms.ApplicationEntryProvenance(
        PK, COMPANY, "ashby", JOB, POSTING, COMPANY, FORM_URL)
    allowed = lambda href: forms._preparation_origin(href, POSTING)
    # Neither the current native form controls nor their current safe receiver
    # need a historic Ashby-specific raw-action/root admission.
    forms._entry_safety(page, allowed, provenance)
    assert forms._entry_not_ready(page) == ""

    row = {
        "pk": PK, "company": COMPANY, "job_id": JOB, "jd_url": POSTING,
        "ats": "ashby", "title": page["title"], "status": "submitting",
        "possible_submission": False, "resume_seed": "synthetic-seed",
    }
    pdf = tmp_path / "Synthetic Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsynthetic only\n")
    monkeypatch.setattr(forms, "_row", lambda _pk, *args, **kwargs: row)
    monkeypatch.setattr(forms, "_configured_alias", lambda *args, **kwargs: provenance)
    monkeypatch.setattr(forms, "_alias_current", lambda *args, **kwargs: True)
    monkeypatch.setattr(forms, "check_dispatch", lambda *args, **kwargs: None)
    monkeypatch.setattr(forms, "hold_possible_submission",
                        lambda *args, **kwargs: pytest.fail(
                            "Ambiguous native form reached the durable Submit hold"))
    submitted = []
    controller = forms.ApplicationIPCController(
        PK, COMPANY, POSTING, str(pdf), {}, {}, [], {},
        lambda: submitted.append(True))
    with pytest.raises(forms.Gate, match="Competing native Submit owners"):
        await controller.authorize_final_submit(None, page, first)
    assert submitted == []
