"""A Greenhouse embed route answers the sync right after navigation with `cdp_failed`.

Observed live on BrowserSkill 0.4: `navigate` to the embed route succeeds, the
immediate `cooperate sync` returns code cdp_failed (no data.reason, no
data.effect_state), and a sync under a second later succeeds. The navigation used
to fail with the bare "cdp_failed" and no way to tell which call raised it, so
every bsk failure now logs its verb, code, reason and effect_state, never values."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from tools import browser_skill as bsk
from tools.browser_skill_dom import PAGE

REAL_COOPERATION = True  # opt out of the legacy-argv fixture in conftest.py

SECRET = "Jane-Doe-secret-value"


def body(code="cdp_failed", **data) -> dict:
    out = {"code": code, "message": f"page said {SECRET}", "exit_code": 1}
    if data:
        out["data"] = data
    return out


class _Proc:
    def __init__(self, payload, returncode):
        self.payload, self.returncode = payload, returncode

    async def communicate(self):
        return json.dumps(self.payload).encode(), b""


class Fake:
    """Stands in for the bsk subprocess, so failures take the real `command` path."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.sync_failures: list = []    # bodies for upcoming `cooperate sync` calls
        self.action_failures: list = []  # bodies for upcoming non-sync, non-session calls
        self.n = 0

    async def __call__(self, exe, *argv, **kwargs):
        args = tuple(a for a in argv if a != "--json")
        self.calls.append(args)
        if args[:2] == ("session", "start"):
            return _Proc({"session_id": "s1", "browser_instance_id": "dedicated"}, 0)
        if args[0] == "session":
            return _Proc({"ok": True}, 0)
        queue = self.sync_failures if args[:2] == ("cooperate", "sync") else self.action_failures
        if queue:
            return _Proc(queue.pop(0), 1)
        if args[:2] == ("cooperate", "sync"):
            self.n += 1
            return _Proc({"context_id": f"ctx-{self.n}"}, 0)
        return _Proc({"ok": True, "value": {"url": "u"}}, 0)

    def verbs(self):
        return [c[0] for c in self.calls if c[0] != "session"]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(bsk, "available", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_profile.ensure_browser", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_runtime.configuration", lambda *a, **k: {"browser": "dedicated"})
    monkeypatch.setattr(bsk, "_SYNC_POLL", 0)
    monkeypatch.setattr(bsk, "_executable", lambda: "bsk")
    f = Fake()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", f)
    return f


async def test_navigation_survives_cdp_failed_from_its_own_sync(fake):
    async with bsk.Session("apply") as session:
        fake.sync_failures = [body()]
        await session._run(("navigate", "https://x.test/"), {})  # must not raise
        assert session._context == ""  # the next action syncs instead
        await session._run(("evaluate", PAGE), {})
    assert fake.verbs() == ["navigate", "cooperate", "cooperate", "evaluate"]


async def test_next_action_sync_retries_cdp_failed_within_the_window(fake):
    async with bsk.Session("apply") as session:
        # One failure for the sync after navigation, two more for the next action's.
        fake.sync_failures = [body(), body(), body()]
        await session._run(("navigate", "https://x.test/"), {})
        await session._run(("evaluate", PAGE), {})
    assert fake.verbs().count("evaluate") == 1
    assert fake.verbs().count("cooperate") == 4


async def test_persistent_cdp_failed_is_still_final(fake, monkeypatch):
    monkeypatch.setattr(bsk, "_SYNC_WINDOW", 0)
    async with bsk.Session("apply") as session:
        fake.sync_failures = [body(), body()]
        await session._run(("navigate", "https://x.test/"), {})
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run(("evaluate", PAGE), {})
    assert "evaluate" not in fake.verbs()


@pytest.mark.parametrize("data", [{"reason": "target_gone"}, {"effect_state": "unknown"},
                                  {"effect_state": "partial"}])
async def test_cdp_failed_with_a_reason_or_effect_state_is_never_tolerated(fake, data):
    async with bsk.Session("apply") as session:
        fake.sync_failures = [body(**data)]
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run(("navigate", "https://x.test/"), {})


@pytest.mark.parametrize("verb", ["evaluate", "click", "navigate"])
async def test_bare_cdp_failed_from_an_action_stays_final(fake, verb):
    async with bsk.Session("apply") as session:
        await session._run(("navigate", "https://x.test/"), {})
        fake.action_failures = [body()]
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run((verb, "#a") if verb == "click" else (verb, PAGE), {})
    assert sum(1 for c in fake.calls if c[0] == verb) == (2 if verb == "navigate" else 1)


def test_the_error_alone_never_matches_without_the_sync_verb():
    try:
        bsk._result(json.dumps(body()), 1)
    except bsk.Unavailable as exc:
        assert not bsk._sync_transient(exc)  # no verb recorded: not a sync failure
        exc.verb = "evaluate"
        assert not bsk._sync_transient(exc)
        exc.verb = "cooperate sync"
        assert bsk._sync_transient(exc)


@pytest.mark.parametrize("args,verb", [
    (("cooperate", "sync", "--session", "s1"), "cooperate sync"),
    (("session", "start", "--browser", "b"), "session start"),
    (("session", "stop", "s1"), "session stop"),
    (("navigate", f"https://x.test/?name={SECRET}"), "navigate"),
    (("evaluate", f"document.title='{SECRET}'"), "evaluate"),
    (("click", "#selector"), "click"),
    (("session", SECRET), "session"),
    (("cooperate", SECRET), "cooperate"),
    ((), "?"),
    ((SECRET,), "?"),
])
def test_verb_is_never_an_argument(args, verb):
    assert bsk._verb(args) == verb


async def test_failure_log_has_verb_code_reason_effect_and_no_values(monkeypatch, caplog):
    async def spawn(*args, **kwargs):
        return _Proc(body(reason="Bad Reason!", effect_state=f"x {SECRET}"), 1)
    monkeypatch.setattr(bsk, "_executable", lambda: "bsk")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.INFO, logger=bsk.log.name):
        with pytest.raises(bsk.Unavailable):
            await bsk.command("navigate", f"https://x.test/?q={SECRET}")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "verb=navigate code=cdp_failed reason=? effect_state=?" in text
    assert SECRET not in text and "x.test" not in text


def test_failure_log_reports_clean_identifiers(caplog):
    try:
        bsk._result(json.dumps(body(reason="target_gone", effect_state="unknown")), 1)
    except bsk.Unavailable as exc:
        problem = exc
    with caplog.at_level(logging.INFO, logger=bsk.log.name):
        bsk._log_unavailable(("cooperate", "sync", "--session", "s1"), problem)
    assert ("verb=cooperate sync code=cdp_failed reason=target_gone effect_state=unknown"
            in caplog.text)
