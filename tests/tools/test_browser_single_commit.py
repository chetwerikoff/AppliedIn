"""Issue #139 r06: adversarial raw-IPC boundary and preparation classification.

Every BrowserSkill/CLI operation is an in-memory stub. No browser, network,
queue, owner file, real résumé, application or submit is used.
"""
from __future__ import annotations

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
    page = {"url": "https://jobs.ashbyhq.com/example-co/synthetic",
            "controls": [target]}
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
