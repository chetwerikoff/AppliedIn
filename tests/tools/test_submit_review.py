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



@pytest.fixture
def local_review_guard(monkeypatch):
    import server

    monkeypatch.setattr(server, "get_settings",
                        lambda: SimpleNamespace(mode="local"))
    return server._require_submit_review_first_party


def _review_request(method="GET", *, host="localhost:8000",
                    peer="127.0.0.1", origins=(), fetch_sites=(),
                    extra_headers=()):
    from starlette.requests import Request

    headers = [(b"host", host.encode())]
    headers.extend((b"origin", value.encode()) for value in origins)
    headers.extend((b"sec-fetch-site", value.encode()) for value in fetch_sites)
    headers.extend((name.encode(), value.encode())
                   for name, value in extra_headers)
    return Request({"type": "http", "method": method, "scheme": "http",
                    "path": "/applications/example-co%231/submit-review",
                    "root_path": "", "query_string": b"", "headers": headers,
                    "client": (peer, 40000), "server": ("127.0.0.1", 8000)})


@pytest.mark.parametrize(("fetch_sites", "allowed"), [
    ((), True),
    (("same-origin",), True),
    (("none",), True),
    (("same-site",), False),
    (("cross-site",), False),
    (("invalid",), False),
    (("",), False),
])
def test_review_packet_get_without_origin_uses_fetch_metadata(
        local_review_guard, fetch_sites, allowed):
    from fastapi import HTTPException

    request = _review_request("GET", fetch_sites=fetch_sites)
    if allowed:
        local_review_guard(request)
    else:
        with pytest.raises(HTTPException) as error:
            local_review_guard(request)
        assert error.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000",
                                  "[::1]:8000"])
def test_review_first_party_exact_origin_allows_loopback(
        local_review_guard, method, host):
    local_review_guard(_review_request(method, host=host,
                                       origins=("http://" + host,)))


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("origins", [
    ("chrome-extension://synthetic",),
    ("moz-extension://synthetic",),
    ("https://localhost:8000",),
    ("http://elsewhere.test:8000",),
    ("http://localhost:8001",),
    ("http://localhost",),
    ("http://localhost:8000/path",),
    ("http://localhost:8000?query=1",),
    ("http://localhost:8000#fragment",),
    ("http://test@localhost:8000",),
    ("http://LOCALHOST:8000",),
    ("null",),
    ("",),
    ("http://localhost:8000", "http://localhost:8000"),
    ("http://localhost:8000", "http://elsewhere.test:8000"),
])
def test_review_rejects_nonexact_duplicate_or_foreign_origin(
        local_review_guard, method, origins):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        local_review_guard(_review_request(method, origins=origins))
    assert error.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("changes", [
    {"host": "elsewhere.test:8000"},
    {"host": "localhost:8001"},
    {"host": "127.0.0.1:8000"},
    {"peer": "192.0.2.1"},
    {"peer": "nonlocal"},
    {"extra_headers": (("host", "localhost:8000"),)},
    {"fetch_sites": ("same-origin", "same-origin")},
    {"fetch_sites": ("same-origin", "cross-site")},
])
def test_review_rejects_foreign_host_peer_or_duplicate_headers(
        local_review_guard, method, changes):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        local_review_guard(_review_request(
            method, origins=("http://localhost:8000",), **changes))
    assert error.value.status_code == 403


@pytest.mark.parametrize("fetch_sites", [(), ("same-origin",), ("none",)])
def test_review_post_requires_origin_even_with_fetch_metadata(
        local_review_guard, fetch_sites):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        local_review_guard(_review_request("POST", fetch_sites=fetch_sites))
    assert error.value.status_code == 403


def test_review_rejects_nonlocal_mode_before_any_read(monkeypatch):
    from fastapi import HTTPException
    import server

    monkeypatch.setattr(server, "get_settings",
                        lambda: SimpleNamespace(mode="cloud"))
    with pytest.raises(HTTPException) as error:
        server._require_submit_review_first_party(_review_request(
            origins=("http://localhost:8000",)))
    assert error.value.status_code == 403


@pytest.fixture
def local_review_client(monkeypatch):
    from fastapi.testclient import TestClient
    import server

    monkeypatch.setattr(server, "get_settings",
                        lambda: SimpleNamespace(mode="local"))
    monkeypatch.setattr(submit_review, "recover_orphans", lambda: None)
    with TestClient(server.create_app(), base_url="http://127.0.0.1:8000",
                    client=("127.0.0.1", 45000)) as client:
        yield client


def test_review_packet_get_without_javascript_origin_reaches_private_route(
        monkeypatch, local_review_client):
    reads = []
    def packet_for(pk):
        reads.append(pk)
        return {"packet": {"job": {"company": "example-co"},
                           "controls": []},
                "screenshot": "data:image/png;base64,iVBORw0KGgo="}

    monkeypatch.setattr(submit_review, "packet_for", packet_for)
    path = "/applications/example-co%231/submit-review"
    for fetch_site in (None, "same-origin", "none"):
        headers = {} if fetch_site is None else {"sec-fetch-site": fetch_site}
        response = local_review_client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["packet"]["job"]["company"] == "example-co"
    assert reads == [PK] * 3

    for fetch_site in ("same-site", "cross-site"):
        response = local_review_client.get(
            path, headers={"sec-fetch-site": fetch_site})
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
    assert reads == [PK] * 3


@pytest.mark.parametrize(("path", "body"), [
    ("/actions/submit-review-mode", {"enabled": False}),
    ("/actions/submit-review/example-co%231",
     {"decision": "reject", "expected_gate_generation": "synthetic-generation"}),
])
@pytest.mark.parametrize("headers", [
    (),
    (("sec-fetch-site", "same-origin"),),
    (("sec-fetch-site", "none"),),
    (("origin", "chrome-extension://synthetic"),),
    (("origin", "moz-extension://synthetic"),),
    (("origin", "https://127.0.0.1:8000"),),
    (("origin", "http://localhost:8000"),),
    (("origin", "http://127.0.0.1:8001"),),
    (("origin", "http://127.0.0.1:8000"),
     ("origin", "http://127.0.0.1:8000")),
    (("origin", "http://127.0.0.1:8000"),
     ("origin", "http://elsewhere.test:8000")),
])
def test_review_post_routes_refuse_before_flag_or_decision_effect(
        monkeypatch, local_review_client, path, body, headers):
    effects = []
    monkeypatch.setattr(flags, "set_submit_review_enabled",
                        lambda on: effects.append(("flag", on)) or True)
    monkeypatch.setattr(submit_review, "invalidate_all",
                        lambda: effects.append(("invalidate",)))
    monkeypatch.setattr(submit_review, "claim",
                        lambda *args: effects.append(("claim", args)))

    response = local_review_client.post(path, json=body, headers=list(headers))
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert effects == []  # No flag change, review generation consumption or cleanup.


def test_review_post_routes_accept_exact_origin_with_synthetic_state(
        monkeypatch, local_review_client):
    from tools import browser_skill

    state = {"enabled": False, "review_state": "pending", "effects": []}
    monkeypatch.setattr(browser_runtime, "backend", lambda: browser_skill)
    def set_mode(on):
        state["enabled"] = on
        state["effects"].append("mode")
        return True
    monkeypatch.setattr(flags, "set_submit_review_enabled", set_mode)
    monkeypatch.setattr(flags, "submit_review_enabled", lambda: state["enabled"])
    def claim(pk, generation, decision, reason):
        assert (pk, generation, decision, reason) == (
            PK, "synthetic-generation", "reject", "")
        state["review_state"] = "rejected"
        state["effects"].append("decision")
        result = Future()
        result.set_result({"ok": True, "review_state": "rejected"})
        return result
    monkeypatch.setattr(submit_review, "claim", claim)

    headers = {"origin": "http://127.0.0.1:8000"}
    mode = local_review_client.post("/actions/submit-review-mode",
                                    json={"enabled": True}, headers=headers)
    assert mode.status_code == 200
    assert mode.headers["cache-control"] == "no-store"
    assert mode.json()["submit_review"] is True
    decision = local_review_client.post(
        "/actions/submit-review/example-co%231",
        json={"decision": "reject",
              "expected_gate_generation": "synthetic-generation"},
        headers=headers)
    assert decision.status_code == 200
    assert decision.headers["cache-control"] == "no-store"
    assert decision.json()["review_state"] == "rejected"
    assert state == {"enabled": True, "review_state": "rejected",
                     "effects": ["mode", "decision"]}


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


@pytest.mark.asyncio
async def test_approved_submit_keeps_independent_confirmation_readback(monkeypatch):
    """A consumed one-shot Submit grant must not block fixed passive reads."""
    from tools import browser_skill as bsk
    from tools import browser_skill_apply as apply_mod

    session = bsk.Session("apply")
    session.id = "synthetic-application-tab"
    calls = []
    before = {"url": URL, "text": "Complete application",
              "controls": [{"type": "submit", "disabled": False}]}
    after = {"url": URL, "text": "Thank you for applying", "controls": []}

    class ApprovedController:
        review_holder = object()
        review_effect_args = None
        pending_commit_args = None
        review_raw_effect_started = False
        committed = False

        async def before_ipc(self, owner, args, kwargs):
            assert owner is session and kwargs == {}
            assert not self.committed
            self.review_effect_args = tuple(args)
            self.committed = True

        def final_review_effect_guard(self, owner, args):
            assert owner is session and tuple(args) == ("click", "#submit")

        async def after_ipc(self, *args, **kwargs):
            assert kwargs["succeeded"]

    controller = ApprovedController()
    session._application_controller = controller

    async def cli(*args, **kwargs):
        calls.append(args[0])
        if args[0] == "click":
            return {"ok": True}
        if args[0] == "evaluate":
            return {"value": after}
        raise AssertionError("Unexpected synthetic raw IPC")

    async def fixed_observe(owner, observed, **kwargs):
        return await owner.page(), {}

    finished = []
    monkeypatch.setattr(bsk, "command", cli)
    monkeypatch.setattr(submit_review, "finish", lambda holder: finished.append(holder))
    monkeypatch.setattr(apply_mod, "_observe_controls", fixed_observe)

    await session.application_effect("click", "#submit")
    assert finished == [controller.review_holder]
    assert controller.review_effect_args is None
    assert await session.page() == after
    confirmed = await apply_mod._fresh_confirmed_outcome(
        session, before, after, None, URL)
    assert "thank you for applying" in confirmed.lower()
    assert calls == ["click", "evaluate", "evaluate"]
    with pytest.raises(AssertionError):
        await session.application_effect("click", "#submit")
    # A raw runner that bypasses application_effect must not revive a spent
    # authority. Read-only PAGE works, but no second committing IPC can run.
    with pytest.raises(bsk.Unavailable, match="after committing attempt"):
        await session._run(("click", "#submit"), {})
    with pytest.raises(bsk.Unavailable, match="after committing attempt"):
        await session._run(("evaluate", "(() => fetch('/submit'))()"), {})
    assert calls.count("click") == 1


@pytest.mark.asyncio
async def test_submit_review_on_during_awaited_precheck_never_reaches_raw_ipc(monkeypatch):
    """An OFF snapshot cannot authorize a Submit after an awaited flag toggle."""
    from tools import browser_skill as bsk

    _ctrl, page, target = world()
    controller = ApplicationIPCController(
        PK, "example-co", URL, "", {}, {}, [], {"last_button": ""},
        lambda: None, submit_review_at_start=False)
    session = bsk.Session("apply")
    session.id = "synthetic-tab"
    session._application_controller = controller
    mode = {"enabled": False}
    called = []

    async def page_read(owner):
        return page

    async def precheck(owner, fresh, receiver, **kwargs):
        assert receiver["selector"] == target["selector"]
        assert not mode["enabled"]
        await asyncio.sleep(0)
        mode["enabled"] = True

    async def forbidden(*args, **kwargs):
        called.append(args)
        raise AssertionError("Toggled-on Submit must not reach raw CLI")

    monkeypatch.setattr(controller, "_page", page_read)
    monkeypatch.setattr(controller, "check_pre_submit", precheck)
    monkeypatch.setattr(flags, "submit_review_enabled", lambda: mode["enabled"])
    monkeypatch.setattr(browser_runtime, "backend", lambda: bsk)
    monkeypatch.setattr(bsk, "command", forbidden)

    with pytest.raises(Gate, match="became enabled before raw IPC"):
        await session.application_effect("click", "#submit")
    assert mode["enabled"]
    assert not called
    assert controller.review_effect_args is None
    assert controller.pending_commit_args is None


def test_explicit_retry_http_route_denies_active_review(monkeypatch):
    """Existing Retry endpoint, not queue/resume, is the human recovery entry."""
    from fastapi import BackgroundTasks
    import server

    row = {"pk": PK, "status": "needs_human", "gate_reason": "submit_review",
           "review_state": "rejected", "gate_generation": "synthetic-generation"}
    tracking = SimpleNamespace(
        r=None, get=lambda pk: dict(row), get_current=lambda pk: dict(row))
    monkeypatch.setattr(server, "make_stores",
                        lambda *args, **kwargs: SimpleNamespace(tracking=tracking))
    allowed = {"rejected", "expired", "restart", "session_lost"}
    monkeypatch.setattr(submit_review, "manual_restart_allowed",
                        lambda pk, current, *, tracking:
                        current.get("review_state") in allowed)
    route = next(r.endpoint for r in server.create_app().routes
                 if getattr(r, "path", None) == "/actions/retry/{pk}")
    for state in ("rejected", "expired", "restart", "session_lost",
                  "pending", "claimed"):
        row["review_state"] = state
        background = BackgroundTasks()
        response = route(PK, background)
        if state in allowed:
            assert response["ok"] and response["fresh_preparation"]
            assert len(background.tasks) == 1
            assert background.tasks[0].kwargs == {"explicit_review_restart": True}
        else:
            assert not response["ok"]
            assert background.tasks == []


@pytest.mark.asyncio
async def test_new_packet_after_explicit_restart_has_new_generation(monkeypatch, tmp_path):
    """Consumed reviewer tokens do not survive a fresh synthetic preparation."""
    from tools import browser_skill as bsk

    controller, page, target = world()
    old_generation = "synthetic-generation-previous"
    row = {
        "pk": PK, "company": "example-co", "job_id": "1",
        "jd_url": URL, "status": "needs_human", "gate_reason": "submit_review",
        "review_state": "rejected", "gate_generation": old_generation,
        "possible_submission": False, "confirmation_id": None,
    }
    tracking = SimpleNamespace(r=None, get_current=lambda pk: dict(row))
    monkeypatch.setattr(submit_review, "_HOLDERS", {})
    monkeypatch.setattr(submit_review, "_tracking", lambda: tracking)
    monkeypatch.setattr(submit_review, "_private_root", lambda: tmp_path)
    monkeypatch.setattr(submit_review, "_live_lease", lambda *a: True)
    monkeypatch.setattr(flags, "submit_review_enabled", lambda: True)
    monkeypatch.setattr(browser_runtime, "backend", lambda: bsk)

    def conditional_patch(pk, *, states, status, generation, changes):
        if (pk != PK or row["status"] != status
                or row["review_state"] not in states
                or (generation is not None
                    and row["gate_generation"] != generation)):
            return False
        row.update(changes)
        return True

    monkeypatch.setattr(submit_review, "_patch", conditional_patch)
    assert submit_review.begin_manual_restart(PK, dict(row), {
        "status": "tailoring", "gate_reason": "",
        "review_state": None, "gate_generation": None}, tracking=tracking)
    assert row["gate_generation"] is None
    # A new tailored job enters a separate BrowserSkill attempt; it must
    # independently publish a new private packet, not revive the old one.
    row["status"] = "submitting"
    packet = submit_review.form_snapshot(controller, page, target)
    holder = submit_review.publish(
        pk=PK, company="example-co", controller=controller,
        session=SimpleNamespace(id="synthetic-tab"), packet=packet,
        screenshot=b"\x89PNG\r\n\x1a\nsynthetic")
    assert holder.generation != old_generation
    assert row["gate_generation"] == holder.generation
    assert row["review_state"] == "pending"
    assert submit_review.packet_for(PK)["packet"]["gate_generation"] == holder.generation
    with pytest.raises(submit_review.Refused):
        submit_review.claim(PK, old_generation, "approve")
    submit_review.invalidate(holder, "rejected")
