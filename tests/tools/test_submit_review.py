"""Issue #301: fully offline final-Submit review boundary fixtures.

Every value and employer is synthetic. No real session, Redis service,
browser, network, applicant data, application or live Submit is used.
"""
from __future__ import annotations

import asyncio
import copy
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import flags
from tools import browser_runtime, submit_review
from tools.browser_skill_apply import ApplicationIPCController, Gate

# Exercise the real BrowserSkill cooperation-context transport guard. Other
# modules retain the pre-cooperation synthetic command shim.
REAL_COOPERATION = True


PK = "example-co#1"
URL = "https://example.test/jobs/1"


def control(selector, label, *, value="", checked=False, options=None,
            combo=None, kind="text"):
    return {
        "selector": selector, "label": label, "question": label,
        "visible": True, "tag": "input", "type": kind, "name": selector[1:],
        "required": False, "disabled": False, "checked": checked,
        "value": value, "files": [], "options": options or [],
        "option_count": len(options or []), "options_capped": False,
        "combobox": combo, "in_form": True, "form_selector": "#form",
        "form_action": URL, "formaction": "",
    }


def world():
    submit = control("#submit", "Submit application", kind="submit")
    email = control("#email", "Email", value="test@example.test")
    other = control("#extra", "Additional field")  # unselected but visible
    choice = control("#choice", "Location", value="", kind="combobox",
                     combo={"selected_text": "Remote",
                            "selected_value": "remote",
                            "displayed_text": "Remote",
                            "hidden_selector": "#location_id",
                            "hidden_value": "location_remote"})
    page = {
        "url": URL, "title": "Test User application", "inventory_verified": True,
        "opaque_controls": False, "shadow_roots": False,
        "unsupported_frames": False, "truncated": False,
        "attached_file_displays": [],
        "controls": [email, other, choice, submit],
    }
    controller = SimpleNamespace(
        pk=PK, company="example-co", url=URL, resume_path="/synthetic/Test User.pdf",
        expected_digest="a" * 64, entry_provenance=None, greenhouse_provenance=None,
        _row=lambda: {"pk": PK, "company": "example-co",
                      "job_id": "1", "ats": "test"},
        _row_pdf=lambda: (1, 2, "synthetic"),
    )
    return controller, page, submit


def test_complete_private_snapshot_contains_unselected_control_and_machine_choice():
    controller, page, target = world()
    packet = submit_review.form_snapshot(controller, page, target)
    assert len(packet["controls"]) == 4
    assert packet["controls"][1]["label"] == "Additional field"
    assert packet["controls"][1]["value"] == ""
    assert packet["controls"][2]["combobox"]["selected_text"] == "Remote"
    assert packet["controls"][2]["combobox"]["hidden_value"] == "location_remote"
    assert packet["attachment"]["filename"] == "Test User.pdf"
    assert packet["job"]["company"] == "example-co"


def test_same_caption_with_different_hidden_value_is_not_approved():
    controller, page, target = world()
    before = submit_review.form_snapshot(controller, page, target)
    page["controls"][2]["combobox"]["hidden_value"] = "location_other"
    after = submit_review.form_snapshot(controller, page, target)
    differences = submit_review.form_differences(before, after)
    assert any(d["control"] == "Location" for d in differences)
    assert differences[0]["before"] != differences[0]["after"]


@pytest.mark.asyncio
async def test_native_raw_submit_rechecks_current_generation_after_context_await(monkeypatch):
    """Revoking a generation while sync_context awaits cannot reach raw IPC."""
    from tools import browser_skill as bsk
    session = bsk.Session("apply")
    session.id = "synthetic-session"
    session._tab_ready = True
    state = {"current": True}
    calls = []

    def guard(_session, args):
        calls.append(("guard", tuple(args)))
        if not state["current"]:
            raise Gate("revoked during synthetic context sync", reason="submit_review")

    session._application_controller = SimpleNamespace(
        review_effect_args=("click", "#submit"),
        final_review_effect_guard=guard,
        review_raw_effect_started=False,
    )

    async def sync_context(*, settle=True):
        await asyncio.sleep(0)
        session._context = "synthetic-fresh-context"
        state["current"] = False
        return session._context

    async def forbidden(*args, **kwargs):
        calls.append(("raw", args))
        raise AssertionError("revoked reviewer generation reached raw CLI")

    monkeypatch.setattr(session, "sync_context", sync_context)
    monkeypatch.setattr(bsk, "command", forbidden)
    with pytest.raises(Gate, match="revoked during synthetic context sync"):
        await session._run(("click", "#submit"), {})
    assert calls == [
        ("guard", ("click", "#submit")),
        ("guard", ("click", "#submit")),
    ]
    assert session._application_controller.review_raw_effect_started is False


def test_native_selected_option_identity_and_multi_select_drift_are_private_and_exact():
    """Keep the legacy option shape but compare the full selected value set.

    The first 64 options alone cannot prove a selected choice beyond the cap,
    and a multiselect may change another value without changing its first one.
    """
    controller, page, target = world()
    native = control("#native", "Availability", value="keep",
                     options=[{"value": "keep", "label": "Keep"},
                              {"value": "skip", "label": "Prefer not to answer"}],
                     kind="select")
    native["tag"] = "select"
    native["selected_options"] = [
        {"value": "keep", "label": "Keep"},
        {"value": "skip", "label": "Prefer not to answer"},
    ]
    page["controls"].insert(2, native)
    before = submit_review.form_snapshot(controller, page, target)
    selected = next(c for c in before["controls"] if c["selector"] == "#native")
    assert selected["selected_options"] == native["selected_options"]
    assert all(set(option) == {"value", "label"} for option in selected["options"])
    native["selected_options"] = [{"value": "keep", "label": "Keep"}]
    after = submit_review.form_snapshot(controller, page, target)
    assert any(d["control"] == "Availability" for d in
               submit_review.form_differences(before, after))


def test_added_unselected_control_and_duplicate_labels_still_differ():
    controller, page, target = world()
    before = submit_review.form_snapshot(controller, page, target)
    page["controls"].insert(1, control("#other", "Email"))
    after = submit_review.form_snapshot(controller, page, target)
    differences = submit_review.form_differences(before, after)
    assert differences
    assert any(d["control"] == "visible control count" for d in differences)
    page["controls"].insert(1, control("#other", "Email"))
    with pytest.raises(submit_review.Refused, match="ambiguous"):
        submit_review.form_snapshot(controller, page, target)


@pytest.mark.parametrize("mutation", [
    lambda page: page.update(inventory_verified=False),
    lambda page: page.update(shadow_roots=True),
    lambda page: page["controls"].append(control("#duplicate", "Password", kind="password")),
    lambda page: page["controls"].append(control("#otp", "One-time code", kind="otp")),
    lambda page: page["controls"][0].update(selector=""),
    lambda page: page["controls"][0].update(value=None),
])
def test_incomplete_and_account_only_packets_refuse_before_publication(mutation):
    controller, page, target = world()
    mutation(page)
    with pytest.raises(submit_review.Refused):
        submit_review.form_snapshot(controller, page, target)


def test_single_winner_atomic_decision_and_no_second_claim(monkeypatch, tmp_path):
    row = {"status": "needs_human", "review_state": "pending"}
    generation = "synthetic-generation-" + "g" * 32
    holder = submit_review.Holder(
        PK, "example-co", generation, time.monotonic() + 300,
        "2099-01-01T00:00:00+00:00", None, SimpleNamespace(id="tab"),
        object(), {}, tmp_path, Future(), Future())
    monkeypatch.setattr(submit_review, "_HOLDERS", {PK: holder})
    monkeypatch.setattr(submit_review, "_private_bytes", lambda h: ({}, b"png"))
    monkeypatch.setattr(submit_review, "_authority", lambda h, **kw:
                        row["review_state"] == ("claimed" if kw.get("claimed") else "pending"))

    def cas(pk, *, states, status, generation, changes):
        if row["status"] != status or row["review_state"] not in states:
            return False
        row.update(changes)
        return True

    monkeypatch.setattr(submit_review, "_patch", cas)
    response = submit_review.claim(PK, generation, "approve")
    assert row["review_state"] == "claimed"
    assert holder.decision.result() == "approve"
    assert response is holder.response
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, generation, "approve")
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, generation, "reject")


@pytest.mark.asyncio
async def test_reject_consumes_generation_without_commit(monkeypatch, tmp_path):
    row = {"status": "needs_human", "review_state": "pending"}
    generation = "synthetic-generation-" + "g" * 32
    holder = submit_review.Holder(
        PK, "example-co", generation, time.monotonic() + 300,
        "2099-01-01T00:00:00+00:00", asyncio.get_running_loop(),
        SimpleNamespace(id="tab"), object(), {}, tmp_path, Future(), Future())
    monkeypatch.setattr(submit_review, "_HOLDERS", {PK: holder})
    monkeypatch.setattr(submit_review, "_private_bytes", lambda h: ({}, b"png"))
    monkeypatch.setattr(submit_review, "_authority", lambda h, **kw:
                        row["review_state"] == ("claimed" if kw.get("claimed") else "pending"))

    def cas(pk, *, states, status, generation, changes):
        if row["status"] != status or row["review_state"] not in states:
            return False
        row.update(changes)
        return True

    monkeypatch.setattr(submit_review, "_patch", cas)
    submit_review.claim(PK, generation, "reject")
    assert row["review_state"] == "rejected"
    assert row["review_reject_reason"] == "reviewer_rejected"
    assert await submit_review.await_decision(holder) == "reject"
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, generation, "approve")


@pytest.mark.asyncio
async def test_unsupported_engine_refuses_before_start(monkeypatch):
    class Unsupported:
        started = False
        async def apply_chrome(self, *args, **kwargs):
            self.started = True
            raise AssertionError("unsupported browser must never start")

    other = Unsupported()
    monkeypatch.setattr(browser_runtime, "backend", lambda: other)
    monkeypatch.setattr(flags, "submit_review_enabled", lambda: True)
    result = await browser_runtime.apply("synthetic job")
    assert result["status"] == "gate"
    assert result["reason"] == "submit_review requires the browser_skill engine"
    assert not other.started


@pytest.mark.parametrize("value", ["", "true", "yes", "1", "ON", "off", "on "])
def test_default_off_and_malformed_flag_are_not_on(monkeypatch, value):
    monkeypatch.setattr(flags, "get_flag", lambda key, default="": value)
    assert flags.submit_review_enabled() is False


def test_expired_holder_does_not_authorize_any_new_decision(monkeypatch, tmp_path):
    holder = submit_review.Holder(
        PK, "example-co", "synthetic-generation-12345678",
        time.monotonic() - 1, "2026-01-01T00:00:00+00:00",
        None, SimpleNamespace(id="tab"), object(), {}, tmp_path,
        Future(), Future())
    monkeypatch.setattr(submit_review, "_HOLDERS", {PK: holder})
    monkeypatch.setattr(submit_review, "_authority", lambda h, **kwargs:
                        time.monotonic() < h.deadline)
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, holder.generation, "approve")



@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(("origin", "host", "peer"), [
    (None, "localhost:8000", "127.0.0.1"),
    ("chrome-extension://synthetic", "localhost:8000", "127.0.0.1"),
    ("moz-extension://synthetic", "localhost:8000", "127.0.0.1"),
    ("http://elsewhere.test:8000", "localhost:8000", "127.0.0.1"),
    ("http://localhost:8001", "localhost:8000", "127.0.0.1"),
    ("http://localhost:8000", "elsewhere.test:8000", "127.0.0.1"),
    ("http://localhost:8000", "localhost:8000", "192.0.2.1"),
])
def test_review_first_party_route_policy_rejects_external_origin_host_or_peer(
        monkeypatch, method, origin, host, peer):
    from fastapi import HTTPException
    from starlette.requests import Request
    from server import _require_submit_review_first_party
    import server

    monkeypatch.setattr(server, "get_settings",
                        lambda: SimpleNamespace(mode="local"))
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = Request({"type": "http", "method": method, "scheme": "http",
                       "path": "/applications/example-co%231/submit-review",
                       "root_path": "", "query_string": b"", "headers": headers,
                       "client": (peer, 40000), "server": ("127.0.0.1", 8000)})
    with pytest.raises(HTTPException) as error:
        _require_submit_review_first_party(request)
    assert error.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_review_first_party_allows_exact_loopback_origin_only(monkeypatch, method):
    from starlette.requests import Request
    from server import _require_submit_review_first_party
    import server

    monkeypatch.setattr(server, "get_settings",
                        lambda: SimpleNamespace(mode="local"))
    request = Request({"type": "http", "method": method, "scheme": "http",
                       "path": "/actions/submit-review/example-co%231",
                       "root_path": "", "query_string": b"",
                       "headers": [(b"host", b"127.0.0.1:8000"),
                                   (b"origin", b"http://127.0.0.1:8000")],
                       "client": ("127.0.0.1", 45000),
                       "server": ("127.0.0.1", 8000)})
    _require_submit_review_first_party(request)



def test_restart_disposes_orphaned_private_packet_and_revokes_stored_token(
        monkeypatch, tmp_path):
    orphan = tmp_path / "synthetic-orphan"
    orphan.mkdir(mode=0o700)
    (orphan / "packet.json").write_text('{"synthetic":true}')
    (orphan / "screenshot.png").write_bytes(b"synthetic-no-real-png")
    row = {
        "pk": PK, "company": "example-co", "status": "needs_human",
        "gate_reason": "submit_review", "gate_generation": "synthetic-generation",
        "review_state": "claimed", "possible_submission": False,
    }
    monkeypatch.setattr(submit_review, "_HOLDERS", {})
    monkeypatch.setattr(submit_review, "_private_root", lambda: tmp_path)
    monkeypatch.setattr(submit_review, "_tracking", lambda: SimpleNamespace(
        query_status=lambda status: [row] if status == "needs_human" else []))
    transitions = []

    def change(pk, *, states, status, generation, changes):
        transitions.append((pk, status, generation))
        row.update(changes)
        return True

    monkeypatch.setattr(submit_review, "_patch", change)
    submit_review.recover_orphans()
    assert row["review_state"] == "restart"
    assert transitions == [(PK, "needs_human", "synthetic-generation")]
    assert not orphan.exists()
    # A restart cannot magically reconstruct the former BrowserSkill owner.
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, "synthetic-generation", "approve")


def test_orphan_recovery_is_asgi_startup_only(monkeypatch):
    """App construction stays offline; actual ASGI startup revokes orphan holders."""
    from fastapi.testclient import TestClient
    from server import create_app

    calls = []
    monkeypatch.setattr(submit_review, "recover_orphans",
                        lambda: calls.append("startup"))
    app = create_app()
    assert calls == []
    with TestClient(app) as client:
        assert calls == ["startup"]
        assert client.get("/config.js").status_code == 200
        assert calls == ["startup"]
    assert calls == ["startup"]
