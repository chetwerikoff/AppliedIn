"""Issue #139 r06: adversarial raw-IPC boundary and preparation classification.

Every BrowserSkill/CLI operation is an in-memory stub. No browser, network,
queue, owner file, real résumé, application or submit is used.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools.browser_skill_dom import PAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    ("click", "#submit"),
    ("press", "Enter"),
    ("upload", "#resume", "--file", "/synthetic/resume.pdf"),
    ("navigate", "https://jobs.ashbyhq.com/example-co/finalize"),
    ("evaluate", "document.forms[0]?.requestSubmit()"),
    ("evaluate", "window.location='https://example.test/submit'"),
])
async def test_raw_application_effects_never_reach_cli(monkeypatch, args):
    """Terminal r05 finding 3 / r06: raw executable evaluation is an effect."""
    calls = []

    async def fake_command(*argv, **kwargs):
        calls.append((argv, kwargs))
        return {"value": {}}

    monkeypatch.setattr(bsk, "command", fake_command)
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    with pytest.raises(bsk.Unavailable, match="Raw application IPC"):
        await session.call(*args)
    assert calls == []


@pytest.mark.asyncio
async def test_code_owned_static_reader_is_still_allowed(monkeypatch):
    """Terminal r05 finding 3: fixed observations are not executable model JS."""
    calls = []

    async def fake_command(*argv, **kwargs):
        calls.append((argv, kwargs))
        return {"value": {"url": "https://jobs.ashbyhq.com/example-co/job"}}

    monkeypatch.setattr(bsk, "command", fake_command)
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    assert (await session.call("evaluate", PAGE))["value"]["url"].startswith("https://")
    assert len(calls) == 1
    assert calls[0][0][:2] == ("evaluate", PAGE)


@pytest.mark.asyncio
async def test_non_application_session_can_still_evaluate(monkeypatch):
    """The Issue forbids changing the JD/discovery raw transport."""
    calls = []

    async def fake_command(*argv, **kwargs):
        calls.append(argv)
        return {"value": 7}

    monkeypatch.setattr(bsk, "command", fake_command)
    session = bsk.Session("jd")
    session.id = "synthetic-session"
    assert (await session.call("evaluate", "1 + 6"))["value"] == 7
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_application_effect_without_bound_controller_refuses_before_cli(monkeypatch):
    calls = []

    async def fake_command(*argv, **kwargs):
        calls.append(argv)

    monkeypatch.setattr(bsk, "command", fake_command)
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    with pytest.raises(bsk.Unavailable, match="Unmediated"):
        await session.application_effect("click", "#submit")
    assert calls == []


def _controller(*, resume_path=""):
    return forms.ApplicationIPCController(
        "example-co#synthetic", "example-co",
        "https://jobs.ashbyhq.com/example-co/synthetic",
        resume_path, {}, {}, [], {"url": ""}, lambda: None)


def _control(*, label="Apply now", kind="button", private=False, submit=False):
    return {
        "selector": "#next", "label": label, "type": kind,
        "tag": "button", "submit": submit, "disabled": False,
        "formaction": "", "href": "", "in_form": True,
        "form_selector": "#app", "form_action": "https://jobs.ashbyhq.com/example-co/synthetic",
        "value": "private" if private else "", "has_value": private,
    }


async def _setup(monkeypatch, page, controller):
    calls = []

    async def fake_command(*argv, **kwargs):
        calls.append((argv, kwargs))
        return {}

    async def fake_page(_session):
        return page

    monkeypatch.setattr(bsk, "command", fake_command)
    monkeypatch.setattr(controller, "_fresh_preparation", lambda _url: None)
    monkeypatch.setattr(controller, "_page", fake_page)
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    session.bind_application_controller(controller)
    return session, calls


@pytest.mark.asyncio
async def test_preprivate_js_apply_on_code_owned_ats_host_is_preparation(monkeypatch):
    """ARCH01:58: before disclosure, ordinary JS Apply is reversible preparation."""
    target = _control()
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {"url": controller.url, "controls": [target]}, controller)
    await session.application_effect("click", "#next")
    assert len(calls) == 1
    assert calls[0][0][:2] == ("click", "#next")


@pytest.mark.asyncio
@pytest.mark.parametrize("private,explicit_submit", [(True, False), (False, True)])
async def test_commit_candidate_calls_same_final_guard_before_cli(
        monkeypatch, private, explicit_submit):
    """r06: a private JS continuation or known native Submit cannot bypass hold."""
    target = _control(private=private, label="Submit application" if explicit_submit else "Continue",
                      kind="submit" if explicit_submit else "button",
                      submit=explicit_submit)
    controls = [target]
    if private:
        controls.append({"selector": "#name", "label": "Full name",
                         "type": "text", "has_value": True, "value": "Test User",
                         "disabled": False})
    page = {"url": "https://jobs.ashbyhq.com/example-co/synthetic",
            "controls": controls}
    controller = _controller()

    async def refuse(_session, _page, _target):
        raise forms.Gate("synthetic final guard refused")

    monkeypatch.setattr(controller, "authorize_final_submit", refuse)
    session, calls = await _setup(monkeypatch, page, controller)
    with pytest.raises(forms.Gate, match="synthetic final guard refused"):
        await session.application_effect("click", "#next")
    assert calls == []


@pytest.mark.asyncio
async def test_row_pdf_upload_detects_staged_byte_change_even_after_cli(monkeypatch, tmp_path):
    """Terminal r05 finding 1: upload is prep; altered bytes fail closed."""
    path = tmp_path / "synthetic-row.pdf"
    path.write_bytes(b"%PDF-1.4\nsynthetic initial bytes\n")
    controller = _controller(resume_path=str(path))
    page = {"url": controller.url, "controls": [
        {"selector": "#resume", "label": "Resume", "type": "file",
         "disabled": False, "files": []},
        {"selector": "#name", "label": "Full name", "type": "text",
         "disabled": False, "has_value": True, "value": "Test User"},
    ]}
    session, calls = await _setup(monkeypatch, page, controller)

    def pdf_identity():
        st = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != controller.expected_digest:
            raise forms.Gate("The staged PDF bytes changed during upload")
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, digest

    monkeypatch.setattr(controller, "_row_pdf", pdf_identity)

    async def fake_command(*argv, **kwargs):
        calls.append((argv, kwargs))
        path.write_bytes(b"%PDF-1.4\nchanged bytes after upload\n")
        return {}

    monkeypatch.setattr(bsk, "command", fake_command)
    with pytest.raises(forms.Gate, match="staged PDF bytes changed"):
        await session.application_effect("upload", "#resume", "--file",
                                         str(path), timeout=130)
    assert len(calls) == 1
    assert controller.upload_completed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "cancelled"])
async def test_interrupted_upload_never_creates_completion_from_unchanged_bytes(
        monkeypatch, tmp_path, outcome):
    """An unchanged staged digest is not proof of a completed CLI attachment."""
    path = tmp_path / "Test User synthetic.pdf"
    path.write_bytes(b"%PDF-1.4\nsynthetic row-only upload\n")
    controller = _controller(resume_path=str(path))
    page = {"url": controller.url, "controls": [
        {"selector": "#resume", "type": "file", "tag": "input",
         "label": "Resume", "disabled": False, "files": []},
    ]}
    controller._row_pdf = lambda: ("synthetic row PDF bytes", controller.expected_digest)
    session, calls = await _setup(monkeypatch, page, controller)
    # A prior success receipt must not survive an interrupted replacement.
    controller.upload_completed = True

    async def interrupted(*argv, **kwargs):
        calls.append((argv, kwargs))
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        raise bsk.Unavailable("synthetic interrupted file upload")

    monkeypatch.setattr(bsk, "command", interrupted)
    error_type = asyncio.CancelledError if outcome == "cancelled" else bsk.Unavailable
    with pytest.raises(error_type):
        await session.application_effect(
            "upload", "#resume", "--file", str(path), timeout=130)
    assert len(calls) == 1
    assert controller.upload_pending is False
    assert controller.upload_completed is False
    with pytest.raises(forms.Gate, match="already attempted"):
        await session.application_effect(
            "upload", "#resume", "--file", str(path), timeout=130)
    assert len(calls) == 1


@pytest.mark.parametrize("url,approved", [
    ("https://jobs.ashbyhq.com/example-co/job", True),
    ("https://jobs.lever.co/example-co/job", True),
    ("https://job-boards.greenhouse.io/other-tenant/jobs/123", True),
    ("https://job-boards.greenhouse.io.attacker.test/other/jobs/123", False),
    ("http://job-boards.greenhouse.io/other/jobs/123", False),
    ("https://jobs.invalid.test/other/jobs/123", False),
    ("https://example.test/submit", False),
])
def test_prep_host_only_not_a_tenant_pin(url, approved):
    """Terminal r05 finding 2: Greenhouse origin admission is host-only."""
    assert forms._preparation_origin(
        url, "https://jobs.ashbyhq.com/example-co/synthetic") is approved


# r06 ARCH01:58: receiving approved form data is preparation, not Submit.
@pytest.mark.asyncio
async def test_postprivate_approved_selection_and_pdf_upload_are_preparation(
        monkeypatch, tmp_path):
    pdf = tmp_path / "Test User Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nsynthetic row-only document\\n")
    controller = _controller(resume_path=str(pdf))
    page = {"url": controller.url, "controls": [
        {"selector": "#name", "tag": "input", "type": "text",
         "label": "Full name", "has_value": True, "value": "Test User"},
        {"selector": "#location", "tag": "select", "type": "select",
         "label": "Work location", "disabled": False,
         "options": [{"value": "remote", "label": "Remote"}]},
        {"selector": "#resume", "type": "file", "tag": "input",
         "label": "Resume", "disabled": False, "files": []},
    ]}
    accepted_values = []
    monkeypatch.setattr(controller, "_approved",
                        lambda _page, _target, value: accepted_values.append(value))
    identity = ("row-bound", controller.expected_digest)
    monkeypatch.setattr(controller, "_row_pdf", lambda: identity)

    async def no_early_submit(*_args):
        pytest.fail("The full final guard must not run for an approved change/upload")

    monkeypatch.setattr(controller, "authorize_final_submit", no_early_submit)
    session, calls = await _setup(monkeypatch, page, controller)
    await session.application_effect("select", "#location", "--value", "remote")
    await session.application_effect("upload", "#resume", "--file",
                                     str(pdf), timeout=130)
    assert accepted_values == ["Remote"]
    assert [c[0][0] for c in calls] == ["select", "upload"]
    assert controller.upload_completed is True


def _assent_target(question, *, value="on", in_form=True):
    return {
        "selector": "#agree", "question": question, "label": "I agree",
        "tag": "input", "type": "checkbox", "required": True,
        "checked": False, "disabled": False, "in_form": in_form,
        "form_selector": "#application" if in_form else "",
        "form_action": "https://example.test/application",
        "value": value,
    }


@pytest.mark.parametrize("question,expected", [
    ("I acknowledge processing of personal data for this application",
     "standard_privacy"),
    ("I consent to processing personal data for recruitment and marketing",
     "owner"),
    ("I consent to processing personal data for application and sharing with partners",
     "owner"),
    ("I accept a privacy policy", "owner"),
    ("I certify all statements are correct", "owner"),
    ("I am legally authorized to work", "fact"),
    ("I agree to arbitration", "owner"),
])
def test_generic_assent_scope_has_no_ats_or_widget_exception(question, expected):
    """ARCH21:26: only narrow standard privacy acknowledgement is delegated."""
    assert forms._assent_scope(_assent_target(question)) == expected


def test_standard_privacy_acknowledgement_uses_standing_delegation(monkeypatch):
    """No per-control yes grant; still require the real form receiver."""
    pk = "example-co#synthetic"
    monkeypatch.setattr(forms, "_row", lambda _pk, **_kw: {
        "pk": pk, "status": "submitting", "human_approved_answers": {},
    })
    question = "I acknowledge processing of personal data for this application"
    target = _assent_target(question)
    page = {"url": "https://example.test/application", "controls": [target]}
    assert forms._approved_native_choice(page, target, pk) == ("yes", target)
    forms.exact_approval(pk, question, "yes", target=target, url=page["url"])
    for bad in (_assent_target(question, value=""),
                _assent_target(question, in_form=False)):
        with pytest.raises(forms.Gate, match="verified form receiver"):
            forms.exact_approval(pk, question, "yes", target=bad, url=page["url"])


def test_beyond_standard_privacy_has_no_implicit_grant(monkeypatch):
    pk = "example-co#synthetic"
    monkeypatch.setattr(forms, "_row", lambda _pk, **_kw: {
        "pk": pk, "status": "submitting", "human_approved_answers": {},
    })
    question = "I consent to processing personal data for recruitment and marketing"
    target = _assent_target(question)
    page = {"url": "https://example.test/application", "controls": [target]}
    with pytest.raises(forms.Gate, match="owner decision"):
        forms._approved_native_choice(page, target, pk)
    with pytest.raises(forms.Gate, match="human-approved answer"):
        forms.exact_approval(pk, question, "yes", target=target, url=page["url"])


@pytest.mark.parametrize("question,expected", [
    ("Expected salary", "owner_question"),
    ("I certify my application is accurate", "owner_question"),
    ("I consent to processing personal data for recruitment and unrelated third-party marketing", "owner_question"),
    ("Work location preference", "agent_review_unavailable"),
])
def test_unresolved_fact_routing_is_owner_only_for_three_exceptions(question, expected):
    assert forms._missing_answer_reason(_assent_target(question)) == expected


def test_per_attempt_pdf_copy_is_readonly_with_stable_bytes(tmp_path):
    """The upload snapshot is protected locally; no remote-byte proof is claimed."""
    import shutil

    original = tmp_path / "Test User Resume.pdf"
    content = b"%PDF-1.4\\nsynthetic offline file\\n"
    original.write_bytes(content)
    snapshot = __import__("pathlib").Path(
        forms._protected_resume_snapshot(str(original)))
    try:
        assert snapshot.name == original.name
        assert snapshot.read_bytes() == content
        assert snapshot.stat().st_mode & 0o222 == 0
        assert snapshot.parent.stat().st_mode & 0o222 == 0
    finally:
        snapshot.parent.chmod(0o700)
        snapshot.chmod(0o600)
        shutil.rmtree(snapshot.parent)


@pytest.mark.asyncio
async def test_one_final_guard_rejects_independent_contradictory_job_id(
        monkeypatch, tmp_path):
    """Even an exact display title cannot authorize a different visible job ID."""
    good = "12345678-1234-4234-8234-123456789abc"
    bad = "22345678-1234-4234-8234-123456789abc"
    company = "example-co"
    pk = company + "#" + good
    url = "https://jobs.ashbyhq.com/example-co/" + good
    pdf = tmp_path / "Test User Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nrow-specific synthetic bytes\\n")
    controller = forms.ApplicationIPCController(
        pk, company, url, str(pdf), {}, {}, [], {}, lambda: None)
    row = {"pk": pk, "company": company, "job_id": good, "jd_url": url,
           "ats": "ashby", "title": "Synthetic Role"}
    monkeypatch.setattr(controller, "_row", lambda: row)
    monkeypatch.setattr(forms, "check_dispatch", lambda *_a, **_kw: None)
    monkeypatch.setattr(forms, "_configured_alias",
                        lambda *_a, **_kw: object())
    monkeypatch.setattr(forms, "_alias_current", lambda *_a, **_kw: True)
    monkeypatch.setattr(forms, "navigation_allowed",
                        lambda *_a, **_kw: True)

    def forbid_hold(*_a, **_kw):
        pytest.fail("Wrong-row click must refuse before any hold or CLI command")

    monkeypatch.setattr(forms, "hold_possible_submission", forbid_hold)
    final_page = {"url": "https://jobs.ashbyhq.com/example-co/" + bad
                  + "/application", "title": "Synthetic Role"}
    with pytest.raises(forms.Gate, match="contradictory job ID"):
        await controller.authorize_final_submit(None, final_page, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("href", [
    "https://jobs.ashbyhq.com/example-co/submitApplication",
    "https://jobs.ashbyhq.com/example-co/one-click",
    "https://jobs.ashbyhq.com/example-co/job?source=submitApplication",
    "https://jobs.ashbyhq.com/example-co/job?source=%2573ubmitApplication",
    "https://job-boards.greenhouse.io/example-co/jobs/123/finalize",
])
async def test_raw_application_get_commit_bypass_refuses_before_cli(monkeypatch, href):
    """r06 raw executable-navigation finding: no guessed reversible GET."""
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {"url": controller.url, "controls": []}, controller)
    with pytest.raises(forms.Gate, match="committing GET navigation"):
        await session.application_effect("navigate", href)
    assert calls == []


@pytest.mark.asyncio
async def test_code_issued_form_get_stays_reversible(monkeypatch):
    """A normal /application GET is not a Submit just because of its path name."""
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {"url": controller.url, "controls": []}, controller)
    await session.application_effect(
        "navigate", "https://jobs.ashbyhq.com/example-co/job/application")
    assert [c[0][0] for c in calls] == ["navigate"]


@pytest.mark.parametrize("checked,form_value,approved,valid", [
    (True, "on", "yes", True),
    (False, "on", "yes", False),
    (True, "", "yes", False),
    (True, "on", "no", False),
    (True, "other", "yes", False),
])
def test_final_standard_acknowledgement_checks_display_and_form_receiver(
        checked, form_value, approved, valid):
    """r06 accepted privacy delegation: no separate owner yes, no unchecked send."""
    target = _assent_target(
        "I acknowledge processing of personal data for this application")
    target["checked"] = checked
    target["value"] = form_value
    target["in_form"] = True
    pdf = {"selector": "#pdf", "tag": "input", "type": "file",
           "label": "Resume", "files": ["Test User Resume.pdf"],
           "has_value": True}
    page = {"url": "https://example.test/application", "inventory_verified": True,
            "controls": [target, pdf]}
    filled = {"#agree": "on"}
    receipts = [{"action": "choose", "selector": "#agree",
                 "approved_value": approved}]
    if valid:
        forms.check_form(page, filled, True, "Test User Resume.pdf", receipts)
    else:
        with pytest.raises(forms.Gate):
            forms.check_form(page, filled, True, "Test User Resume.pdf", receipts)


@pytest.mark.parametrize("approved,actual,valid", [
    ("Remote", "remote", True),
    ("On-site", "remote", False),
    ("Remote", "unexpected", False),
])
def test_native_select_receives_exact_option_value_and_approved_caption(
        approved, actual, valid):
    """ARCH01:58: native select's opaque submitted value maps to approved text."""
    target = {"selector": "#location", "tag": "select", "type": "select",
              "label": "Work location", "required": True, "disabled": False,
              "value": actual, "has_value": True,
              "options": [{"label": "Remote", "value": "remote"},
                          {"label": "On-site", "value": "office"}]}
    pdf = {"selector": "#pdf", "tag": "input", "type": "file",
           "label": "Resume", "files": ["Test User Resume.pdf"]}
    page = {"url": "https://example.test/application",
            "inventory_verified": True, "controls": [target, pdf]}
    receipts = [{"selector": "#location", "action": "select",
                 "approved_value": approved}]
    if valid:
        forms.check_form(page, {"#location": "remote"}, True,
                         "Test User Resume.pdf", receipts)
    else:
        with pytest.raises(forms.Gate):
            forms.check_form(page, {"#location": "remote"}, True,
                             "Test User Resume.pdf", receipts)


@pytest.mark.asyncio
async def test_session_closure_removes_only_protected_attempt_snapshot(
        monkeypatch, tmp_path):
    """No row PDF or unrelated workspace artifact is removed on session stop."""
    from pathlib import Path

    original = tmp_path / "Test User Resume.pdf"
    original.write_bytes(b"%PDF-1.4\\nsynthetic authoritative source\\n")
    controller = forms.ApplicationIPCController(
        "example-co#synthetic", "example-co",
        "https://jobs.ashbyhq.com/example-co/synthetic",
        str(original), {}, {}, [], {}, lambda: None, seal_stage=True)
    snapshot = Path(controller.resume_path)
    assert snapshot.is_file() and snapshot != original
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    session.bind_application_controller(controller)
    calls = []

    async def fake_command(*args, **kwargs):
        calls.append(args)
        return {}

    monkeypatch.setattr(bsk, "command", fake_command)
    await session.close()
    assert calls == [("session", "stop", "synthetic-session")]
    assert original.is_file()
    assert not snapshot.exists()
    assert not snapshot.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["checkbox", "combobox"])
async def test_submission_marked_choice_uses_one_final_guard_before_ipc(
        monkeypatch, kind):
    """A choice role cannot disguise a known application commit."""
    target = _control(label="Choose", kind=kind, submit=True)
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {"url": controller.url, "controls": [target]}, controller)
    checked = []

    async def refuse(_session, _page, _target):
        checked.append(kind)
        raise forms.Gate("synthetic final guard refused")

    monkeypatch.setattr(controller, "authorize_final_submit", refuse)
    with pytest.raises(forms.Gate, match="synthetic final guard refused"):
        await session.application_effect("click", "#next")
    assert checked == [kind]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "select", "fill"])
async def test_submit_marked_preparation_receivers_refuse_before_cli(
        monkeypatch, tmp_path, operation):
    """A known committing receiver is not ordinary form data transmission."""
    pdf = tmp_path / "synthetic-row.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nsynthetic payload\\n")
    controller = _controller(resume_path=str(pdf))
    target = _control(label="Resume" if operation == "upload" else "Work location",
                      kind="file" if operation == "upload" else
                      "select" if operation == "select" else "text")
    target["submit"] = True
    if operation == "select":
        target.update(tag="select", options=[{"value": "remote", "label": "Remote"}])
    page = {"url": controller.url, "controls": [target]}
    session, calls = await _setup(monkeypatch, page, controller)
    args = (("upload", "#next", "--file", str(pdf)) if operation == "upload"
            else ("select", "#next", "--value", "remote") if operation == "select"
            else ("fill", "#next", "--value", "Remote"))
    with pytest.raises(forms.Gate, match="submission-capable"):
        await session.application_effect(
            *args, **({"timeout": 130} if operation == "upload" else {}))
    assert calls == []


def test_canonical_ats_posting_authority_is_not_board_pin_authority(monkeypatch):
    """Only literal tracked-row identity, never configured board, admits prep."""
    from core.ids import make_pk

    company = "example-co"
    job = "12345678-1234-4234-8234-123456789abc"
    pk = make_pk(company, job)
    posting = f"https://jobs.ashbyhq.com/{company}/{job}"
    row = {"pk": pk, "company": company, "ats": "ashby", "job_id": job,
           "jd_url": posting, "status": "submitting"}
    monkeypatch.setattr(forms, "_row", lambda *_a, **_kw: dict(row))
    assert forms._tracked_preparation_job(pk, company, posting) is None
    for bad_url in (posting + "?new=1", posting.replace("https:", "http:")):
        row["jd_url"] = bad_url
        with pytest.raises(forms.Gate):
            forms._tracked_preparation_job(pk, company, bad_url)
    # ARCH21:58 / #139 AC14: a foreign but host-allowlisted tenant is not
    # malformed posting spelling. It prepares, then the final pin check denies.
    from discovery.watchlist import CompanyConfig
    monkeypatch.setattr('discovery.watchlist.load_watchlist', lambda path: [
        CompanyConfig(name=company, ats='ashby', board=company)])
    foreign = posting.replace('/example-co/', '/other-co/')
    row['jd_url'] = foreign
    forms._tracked_preparation_job(pk, company, foreign)
    assert forms._configured_alias(pk, company, foreign) is None
    row["jd_url"] = posting
    row["job_id"] = "different-job"
    with pytest.raises(forms.Gate, match="tracked job/dispatch identity"):
        forms._tracked_preparation_job(pk, company, posting)


@pytest.mark.asyncio
async def test_new_confirmation_needs_exact_route_and_independent_witness(monkeypatch):
    """A success phrase from a sibling route or contradictory fixed read is not applied."""
    from unittest.mock import AsyncMock

    provenance = forms.ApplicationEntryProvenance(
        "synthetic", "example-co", "ashby", "synthetic",
        "https://jobs.ashbyhq.com/example-co/synthetic",
        "example-co", "https://jobs.ashbyhq.com/example-co/synthetic/application")
    before = {"url": provenance.url, "text": "Application form",
              "controls": [{"label": "Submit application"}]}
    after = {"url": provenance.url, "text": "Thank you for applying", "controls": []}
    reader = AsyncMock(return_value=({**after, "url": provenance.url + "/other-job"}, {}))
    monkeypatch.setattr(forms, "_observe_controls", reader)
    assert await forms._fresh_confirmed_outcome(
        object(), before, after, provenance, provenance.wrapper) == ""
    reader.assert_awaited_once()
    reader.reset_mock()
    foreign = {**after, "url": provenance.url + "/other-job"}
    assert await forms._fresh_confirmed_outcome(
        object(), before, foreign, provenance, provenance.wrapper) == ""
    reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_formless_upload_producer_binds_dropzone_to_exact_file_input(
        monkeypatch, tmp_path):
    """The structural chooser IPC names a dropzone, never an arbitrary file target."""
    from tests.tools.test_browser_formless import observed

    page, _ = observed()
    pdf = tmp_path / "Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nsynthetic row document\\n")
    controller = _controller(resume_path=str(pdf))
    identity = ("row", "seed", controller.expected_digest)
    monkeypatch.setattr(controller, "_row_pdf", lambda: identity)
    session, calls = await _setup(monkeypatch, page, controller)

    with pytest.raises(forms.Gate, match="exact protected PDF file input"):
        await session.application_effect(
            "upload", "#untrusted-drop", "--file", str(pdf), timeout=130)
    assert calls == []
    await session.application_effect(
        "upload", "#dropzone", "--file", str(pdf), timeout=130)
    assert [entry[0][:2] for entry in calls] == [("upload", "#dropzone")]
    assert controller.upload_completed is True


@pytest.mark.parametrize(("required", "value", "has_value", "receipt", "reason"), [
    (True, "", True, {"#answer": "Approved answer"}, "Required field is empty"),
    (False, "Unapproved injected value", False, {}, "readback disagree"),
    (False, "", True, {}, "readback disagree"),
])
def test_final_native_form_readback_disagreement_denies_submit(
        required, value, has_value, receipt, reason):
    """The actual form-own native value, not a has_value flag, is authoritative."""
    page = {
        "url": "https://example-co.test/job/1",
        "inventory_verified": True, "truncated": False,
        "opaque_controls": False, "shadow_roots": False,
        "unsupported_frames": [],
        "controls": [{
            "selector": "#answer", "tag": "input", "type": "text",
            "in_form": True, "form_selector": "#application",
            "form_action": "https://example-co.test/job/1",
            "label": "Synthetic answer", "required": required,
            "value": value, "has_value": has_value,
        }],
    }
    with pytest.raises(forms.Gate, match=reason):
        forms.check_form(page, receipt, uploaded=True)


def test_final_optional_native_value_requires_current_exact_readback():
    page = {
        "url": "https://example-co.test/job/1",
        "inventory_verified": True, "truncated": False,
        "opaque_controls": False, "shadow_roots": False,
        "unsupported_frames": [],
        "controls": [{
            "selector": "#answer", "tag": "input", "type": "text",
            "in_form": True, "form_selector": "#application",
            "form_action": "https://example-co.test/job/1",
            "label": "Synthetic answer", "required": False,
            "value": "Approved answer", "has_value": True,
        }],
    }
    forms.check_form(page, {"#answer": "Approved answer"}, uploaded=True)
    with pytest.raises(forms.Gate, match="Unverified or changed form value"):
        forms.check_form(page, {}, uploaded=True)


@pytest.mark.parametrize("status,gate_reason", [
    ("applied_manual", ""),
    ("applied", ""),
    ("needs_human", ""),
    ("submitting", "submit_uncertain"),
])
def test_manual_or_duplicate_outcome_prevents_acquiring_a_new_hold(
        monkeypatch, status, gate_reason):
    """The production pre-hold reread must preserve an existing human outcome."""
    import fakeredis
    from types import SimpleNamespace

    pk = "example-co#synthetic"
    row = {"pk": pk, "company": "example-co", "status": status,
           "gate_reason": gate_reason, "possible_submission": False}
    client = fakeredis.FakeRedis(decode_responses=True)

    def forbid_status_overwrite(*_args, **_kwargs):
        pytest.fail("A preexisting manual outcome must never be overwritten")

    tracking = SimpleNamespace(
        r=client, get_current=lambda read_pk: dict(row) if read_pk == pk else None,
        set_status=forbid_status_overwrite)
    monkeypatch.setattr(
        "core.stores.make_stores", lambda: SimpleNamespace(tracking=tracking))

    with pytest.raises(forms.Gate, match="manual, duplicate or unreviewed"):
        forms.hold_possible_submission(
            pk, {"last_button": "Submit application",
                 "url": "https://example-co.test/job/1"})
    assert client.get(f"submit_hold:{pk}") is None
    assert row["status"] == status


def test_manual_confirmation_during_hold_does_not_reach_click(monkeypatch):
    """A concurrent manual outcome wins over this attempt's conditional hold."""
    import fakeredis
    from types import SimpleNamespace

    pk = "example-co#synthetic"
    row = {"pk": pk, "company": "example-co", "status": "submitting",
           "gate_reason": "", "possible_submission": False}
    client = fakeredis.FakeRedis(decode_responses=True)
    calls = []

    def interleaved_status(_pk, expected, updates, *, expected_reader):
        assert _pk == pk and getattr(expected, "value", expected) == "submitting"
        assert updates["status"].value == "needs_human"
        assert expected_reader == {}
        assert client.get(f"submit_hold:{pk}") is not None
        # The owner reconciles the job before the WATCH-backed transition.
        # A failed conditional update must never restore needs_human here.
        row["status"] = "applied_manual"
        row["confirmation_id"] = "Synthetic manual confirmation"
        client.delete(f"submit_hold:{pk}")
        calls.append("human reconciled")
        return False

    tracking = SimpleNamespace(
        r=client,
        get_current=lambda _pk: dict(row),
        get=lambda _pk: dict(row),
        update_if_status=interleaved_status)
    monkeypatch.setattr(
        "core.stores.make_stores", lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(
        "tools.browser_apply._duplicate_refusal", lambda _pk: False)
    monkeypatch.setattr(
        "tools.posting_guard.authorised", lambda _row: True)

    with pytest.raises(forms.Gate, match="submitting row changed.*no click is safe"):
        forms.hold_possible_submission(
            pk, {"last_button": "Submit application",
                 "url": "https://example-co.test/job/1"})
    with pytest.raises(forms.Gate, match="Human outcome changed or submission hold"):
        forms.check_before_committing_click(pk)
    assert calls == ["human reconciled"]
    assert row["status"] == "applied_manual"
    assert row["confirmation_id"] == "Synthetic manual confirmation"
    assert client.get(f"submit_hold:{pk}") is None


def test_distinct_native_forms_never_share_approval_ownership(monkeypatch):
    """Other form purposes cannot block a safe write or steal its exact grant."""
    url = "https://example-co.test/job/1"

    def control(selector, owner):
        return {
            "selector": selector, "label": "Email",
            "question": "Contact details", "tag": "input",
            "type": "email", "in_form": True, "form_selector": owner,
            "form_action": url, "formaction": "", "disabled": False,
            "value": "", "has_value": False, "required": True,
        }

    main = control("#main-email", "#application")
    unrelated = control("#other-email", "#other-form")
    page = {"url": url, "controls": [unrelated, main]}
    assert forms._form_subject(page, main) == [main]
    assert forms._approval_question_collisions(page, main) == []
    assert forms._key(page, main) == "Contact details"

    pk, answer = "example-co#synthetic", "test@example-co.test"
    grant = {key: main[key] for key in (
        "selector", "label", "question", "tag", "type",
        "form_selector", "form_action")}
    grant.update(url=url, value=answer)
    row = {"pk": pk, "status": "submitting",
           "possible_submission": False,
           "human_approved_answers": {"Contact details": grant}}
    monkeypatch.setattr(forms, "_row", lambda _pk: row)

    forms.exact_approval(pk, "Contact details", answer, target=main, url=url)
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        forms.exact_approval(
            pk, "Contact details", answer, target=unrelated, url=url)

    # Two real competing answers in the SAME form are still ambiguous.
    same_form = control("#second-email", "#application")
    both = {"url": url, "controls": [main, same_form]}
    with pytest.raises(forms.Gate, match="same approval question"):
        forms._key(both, main)


@pytest.mark.asyncio
async def test_native_choice_with_inline_requestsubmit_never_bypasses_final_guard(monkeypatch):
    """A granted native checkbox's onclick requestSubmit is a commit, not autosave."""
    target = _control(label="I agree", kind="checkbox", submit=False)
    target.update(tag="input", required=True, checked=False, value="on",
                  question="I acknowledge application data processing",
                  commits_application=True)
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {"url": controller.url, "controls": [target]}, controller)
    final_calls, marked = [], []

    async def refuse(_session, _page, _target):
        final_calls.append("same final guard")
        raise forms.Gate("synthetic final guard refused")

    monkeypatch.setattr(controller, "authorize_final_submit", refuse)
    controller.mark_click_started = lambda: marked.append("possible submission")
    with pytest.raises(forms.Gate, match="synthetic final guard refused"):
        await session.application_effect("click", "#next")
    assert final_calls == ["same final guard"]
    assert marked == [] and calls == []
    assert controller.committed is False


@pytest.mark.parametrize("files,second_file,valid", [
    (["Tailored.pdf"], [], True),
    (["Tailored.pdf", "Unapproved.pdf"], [], False),
    (["Tailored.pdf"], ["Other-document.pdf"], False),
    (["Unapproved.pdf"], [], False),
])
def test_native_final_form_refuses_extra_file_attachments(files, second_file, valid):
    """Native receivers must obey the same exact single-row-PDF boundary as formless."""
    page = {
        "url": "https://example-co.test/jobs/123",
        "inventory_verified": True, "truncated": False,
        "opaque_controls": False, "unsupported_frames": [], "shadow_roots": False,
        "controls": [
            {"selector": "#resume", "tag": "input", "type": "file",
             "label": "Resume", "in_form": True, "form_selector": "#application",
             "files": files, "required": False},
            {"selector": "#other-file", "tag": "input", "type": "file",
             "label": "Additional attachment", "in_form": True,
             "form_selector": "#application", "files": second_file, "required": False},
        ],
    }
    if valid:
        forms.check_form(page, {}, uploaded=True, attachment="Tailored.pdf")
    else:
        with pytest.raises(forms.Gate, match="résumé attachment|exact staged résumé"):
            forms.check_form(page, {}, uploaded=True, attachment="Tailored.pdf")


@pytest.mark.asyncio
async def test_completion_receives_only_redacted_model_projection(monkeypatch):
    """Assert the actual LiteLLM completion messages, not only a helper's dict."""
    import json
    import sys
    from types import SimpleNamespace

    secrets = {
        "text": "SYNTHETIC_PRIVATE_NAME_4ba2",
        "file": "SYNTHETIC_PRIVATE_RESUME_68e1.pdf",
        "receiver": "SYNTHETIC_PRIVATE_RECEIVER_79da",
        "approved": "SYNTHETIC_APPROVED_VALUE_9e4f",
    }
    page = {"url": "https://jobs.ashbyhq.com/example-co/job",
            "text": secrets["text"], "description": secrets["text"],
            "inventory_verified": True, "controls": [
                {"selector": "#name", "label": "Full name", "type": "text",
                 "has_value": True, "value": secrets["text"], "files": []},
                {"selector": "#resume", "label": "Resume", "type": "file",
                 "files": [secrets["file"]]},
                {"selector": "#picker", "label": "Work location",
                 "type": "combobox", "combobox": {
                     "selected_text": secrets["approved"],
                     "displayed_text": secrets["approved"],
                     "selected_value": secrets["receiver"],
                     "hidden_value": secrets["receiver"],
                     "options": [{"selector": "#remote", "label": "Remote",
                                  "value": secrets["receiver"]}]}}
            ]}
    receipts = [{"action": "fill", "selector": "#name",
                 "approved_value": secrets["approved"],
                 "label": "Full name"}]
    model_page, model_history = forms._model_projection(page, receipts)
    captured = []

    def fake_completion(*, model, messages, response_format):
        captured.append(json.dumps(messages, ensure_ascii=False))
        assert response_format == {"type": "json_object"}
        return {"choices": [{"message": {"content": '{"action":"wait"}'}}]}

    monkeypatch.setitem(sys.modules, "litellm",
                        SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr("core.steering.messages",
                        lambda prompt: [{"role": "user", "content": prompt}])
    assert await bsk.decision("Use only fact keys", model_page, model_history,
                              model="synthetic-model") == {"action": "wait"}
    assert len(captured) == 1
    assert all(secret not in captured[0] for secret in secrets.values())
    # The producer has not corrupted the original values used by code validation.
    assert page["controls"][0]["value"] == secrets["text"]
    assert page["controls"][1]["files"] == [secrets["file"]]
    assert receipts[0]["approved_value"] == secrets["approved"]


@pytest.mark.asyncio
async def test_verified_vendor_redirect_preserves_exact_raw_session_route(monkeypatch):
    """Production-shaped apply Session allows only a row-bound typed vendor redirect."""
    from core.ids import make_pk

    company, job_id = "example-co", "job-123"
    pk = make_pk(company, job_id)
    wrapper = "https://example-co.teamtailor.com/jobs/job-123"
    target = "https://careers.example-co.com/jobs/job-123"
    foreign = "https://unapproved.example.net/jobs/job-123"
    row = {"pk": pk, "company": company, "job_id": job_id,
           "jd_url": wrapper, "ats": "teamtailor",
           "status": "submitting", "possible_submission": False}
    proof = forms.redirect_provenance(row, pk, target)
    assert isinstance(proof, forms.VendorRedirectProvenance)
    # Exact typed redirect, not a broadened global preparation allowlist.
    assert forms._preparation_origin(target, wrapper) is False
    monkeypatch.setattr(forms, "_row", lambda _pk, **_kw: row)
    monkeypatch.setattr("tools.submit_hold.is_held", lambda *a, **kw: False)
    from core import stores
    monkeypatch.setattr(stores, "make_stores", lambda: SimpleNamespace(tracking=object()))
    effects = []

    async def fake_command(*argv, **kwargs):
        effects.append(argv)
        return {}

    from types import SimpleNamespace
    monkeypatch.setattr(bsk, "command", fake_command)
    controller = forms.ApplicationIPCController(
        pk, company, wrapper, "", {}, {}, [], {}, lambda: None,
        verified_redirect=proof)
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    session.bind_application_controller(controller)
    await session.application_effect("navigate", target)
    assert len(effects) == 1 and effects[0][:2] == ("navigate", target)
    with pytest.raises(forms.Gate, match="Unknown, executable or committing GET"):
        await session.application_effect("navigate", foreign)
    assert len(effects) == 1
    row["job_id"] = "different-job"
    with pytest.raises(forms.Gate):
        await session.application_effect("navigate", target)
    assert len(effects) == 1


@pytest.mark.parametrize('role', ['button', 'checkbox'])
@pytest.mark.parametrize('reader', [
    bsk.PAGE, forms._FIXED_CONTROL_WITNESS,
    forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS,
])
def test_native_submit_role_does_not_mask_trusted_reader_html_type(role, reader):
    """Synthetic DOM read: role is accessibility, input type is native effect."""
    from tests.tools.test_browser_application_entry import dom, entry
    marker = "  const query = 'input,textarea,select,button,a[href]"
    assert marker in reader
    source = reader.replace(
        marker,
        "  document.querySelector('#submit').setAttribute('role','" + role + "');\n" + marker,
        1)
    page = dom({'url': entry('ashby'), 'marker': 'value',
                'actionAttr': 'explicit'}, source)
    target = next(c for c in page['controls'] if c['selector'] == '#submit')
    assert target['type'] == role
    assert target['native_type'] == 'submit'
    assert target['submit'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['button', 'checkbox'])
async def test_native_submit_input_masked_as_aria_role_never_reaches_cli(monkeypatch, role):
    """A native committing input cannot become an approved reversible choice."""
    target = _control(label='Continue', kind=role, submit=False)
    target.update(tag='input', native_type='submit',
                  native_form_owner_selector='#app')
    controller = _controller()
    session, calls = await _setup(
        monkeypatch, {'url': controller.url, 'controls': [target]}, controller)
    visited = []

    async def refuse(_session, _page, _target):
        visited.append('single final guard')
        raise forms.Gate('synthetic final guard refused')

    monkeypatch.setattr(controller, 'authorize_final_submit', refuse)
    with pytest.raises(forms.Gate, match='synthetic final guard refused'):
        await session.application_effect('click', '#next')
    assert visited == ['single final guard']
    assert calls == [] and controller.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(('identity','expected'), [
    ({'job_ids': ['other-job'], 'employers': []}, 'contradictory visible job ID'),
    ({'job_ids': [], 'employers': ['foreign-co']}, 'contradictory visible employer'),
    ({'job_ids': [], 'employers': []}, 'protected tailored PDF'),
])
async def test_visible_application_identity_is_scoped_at_only_final_ipc(
        monkeypatch, identity, expected):
    """Identical title is insufficient for a labelled foreign root identity.

    A foreign ID in unrelated page prose or a different form is not authority.
    The matching-root positive reaches the normal PDF check, not the hold.
    """
    from core.ids import make_pk
    company, job = 'example-co', 'synthetic-job-42'
    pk = make_pk(company, job)
    url = 'https://example-co.test/jobs/synthetic-job-42'
    controller = forms.ApplicationIPCController(
        pk, company, url, '', {}, {}, [], {}, lambda: None)
    row = {'pk': pk, 'company': company, 'job_id': job,
           'jd_url': url, 'ats': 'other', 'title': 'Synthetic Role'}
    monkeypatch.setattr(controller, '_row', lambda: row)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **k: None)
    monkeypatch.setattr(forms, 'navigation_allowed', lambda *a, **k: True)
    monkeypatch.setattr(forms, 'hold_possible_submission',
                        lambda *a, **k: pytest.fail('identity/PDF refusal must precede hold'))
    target = _control(label='Submit application', kind='submit', submit=True)
    target.update(selector='#submit', native_type='submit', tag='button',
                  native_form_owner_selector='#app', form_action=url)
    page = {
        'url': url, 'title': 'Synthetic Role',
        'text': 'Related postings: Employer: foreign-co; Job ID: other-job',
        'controls': [target],
        'application_identity': [
            {'selector': '#other-form', 'job_ids': ['other-job'],
             'employers': ['foreign-co']},
            {'selector': '#app', **identity},
        ],
    }
    session, calls = await _setup(monkeypatch, page, controller)
    with pytest.raises(forms.Gate, match=expected):
        await session.application_effect('click', '#submit')
    assert calls == []
    assert controller.committed is False


@pytest.mark.parametrize(('visible','expected'), [
    ('Job ID: other-job', 'job ID'),
    ('Employer: foreign-co', 'employer'),
    ('', ''),
])
@pytest.mark.parametrize('reader', [
    bsk.PAGE, forms._FIXED_CONTROL_WITNESS,
    forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS,
])
def test_both_raw_readers_scope_labelled_identity_to_application_root(reader, visible, expected):
    """Real trusted JS reader and synthetic DOM; the page footer has no authority."""
    from tests.tools.test_browser_application_entry import dom, entry
    from tools.browser_skill_dom import scoped_identity_conflict
    marker = "  const query = 'input,textarea,select,button,a[href]"
    assert marker in reader
    preparation = (
        "  Object.defineProperty(document.querySelector('#body'), 'innerText', "
        "{value:'Job ID: other-job; Employer: foreign-co'});\n"
    )
    if visible:
        preparation += (
            "  Object.defineProperty(document.querySelector('#application-form'), "
            "'innerText', {value:'" + visible + "', configurable:true});\n"
        )
    source = reader.replace(marker, preparation + marker, 1)
    page = dom({'url': entry('ashby'), 'actionAttr': 'explicit'}, source)
    assert scoped_identity_conflict(page, '#application-form',
                                    'synthetic-job-42', 'example-co') == expected
