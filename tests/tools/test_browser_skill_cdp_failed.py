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


def cdp_failed(**data) -> bsk.Unavailable:
    body = {"code": "cdp_failed", "message": "debugger attach failed", "exit_code": 1}
    if data:
        body["data"] = data
    try:
        bsk._result(json.dumps(body), 1)
    except bsk.Unavailable as exc:
        return exc
    raise AssertionError("must raise")


class Fake:
    def __init__(self):
        self.calls: list[tuple] = []
        self.sync_failures: list = []
        self.action_failures: list = []
        self.n = 0

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("session", "start"):
            return {"session_id": "s1", "browser_instance_id": "dedicated"}
        if args[:2] == ("session", "stop"):
            return {"ok": True}
        if args[0] == "cooperate":
            if self.sync_failures:
                raise self.sync_failures.pop(0)
            self.n += 1
            return {"context_id": f"ctx-{self.n}"}
        if self.action_failures:
            raise self.action_failures.pop(0)
        return {"ok": True, "value": {"url": "u"}}

    def verbs(self):
        return [c[0] for c in self.calls if c[0] != "session"]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(bsk, "available", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_profile.ensure_browser", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_runtime.configuration", lambda *a, **k: {"browser": "dedicated"})
    monkeypatch.setattr(bsk, "_SYNC_POLL", 0)
    f = Fake()
    monkeypatch.setattr(bsk, "command", f)
    return f


async def test_navigation_survives_cdp_failed_from_its_own_sync(fake):
    async with bsk.Session("apply") as session:
        fake.sync_failures = [cdp_failed()]
        await session._run(("navigate", "https://x.test/"), {})  # must not raise
        await session._run(("evaluate", PAGE), {})  # next action syncs and runs
    assert fake.verbs() == ["navigate", "cooperate", "cooperate", "evaluate"]


async def test_next_action_sync_retries_cdp_failed_within_the_window(fake):
    async with bsk.Session("apply") as session:
        # One failure for the sync after navigation, two more for the next action's.
        fake.sync_failures = [cdp_failed(), cdp_failed(), cdp_failed()]
        await session._run(("navigate", "https://x.test/"), {})
        await session._run(("evaluate", PAGE), {})
    assert fake.verbs().count("evaluate") == 1
    assert fake.verbs().count("cooperate") == 4


async def test_persistent_cdp_failed_is_still_final(fake, monkeypatch):
    monkeypatch.setattr(bsk, "_SYNC_WINDOW", 0)
    async with bsk.Session("apply") as session:
        fake.sync_failures = [cdp_failed(), cdp_failed()]
        await session._run(("navigate", "https://x.test/"), {})
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run(("evaluate", PAGE), {})
    assert "evaluate" not in fake.verbs()


@pytest.mark.parametrize("data", [{"reason": "target_gone"}, {"effect_state": "unknown"},
                                  {"effect_state": "partial"}])
async def test_cdp_failed_with_a_reason_or_effect_state_is_never_tolerated(fake, data):
    async with bsk.Session("apply") as session:
        fake.sync_failures = [cdp_failed(**data)]
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run(("navigate", "https://x.test/"), {})


async def test_cdp_failed_from_an_action_is_never_retried(fake):
    async with bsk.Session("apply") as session:
        await session._run(("navigate", "https://x.test/"), {})
        fake.action_failures = [cdp_failed(), None]
        with pytest.raises(bsk.Unavailable, match="cdp_failed"):
            await session._run(("evaluate", PAGE), {})
    assert sum(1 for c in fake.calls if c[0] == "evaluate") == 1


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


class _Proc:
    returncode = 1

    async def communicate(self):
        body = {"code": "cdp_failed", "message": f"page said {SECRET}",
                "data": {"reason": "Bad Reason!", "effect_state": f"x {SECRET}"}}
        return json.dumps(body).encode(), b""


async def test_failure_log_has_verb_code_reason_effect_and_no_values(monkeypatch, caplog):
    async def spawn(*args, **kwargs):
        return _Proc()
    monkeypatch.setattr(bsk, "_executable", lambda: "bsk")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level(logging.INFO, logger=bsk.log.name):
        with pytest.raises(bsk.Unavailable):
            await bsk.command("navigate", f"https://x.test/?q={SECRET}")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "verb=navigate code=cdp_failed reason=? effect_state=?" in text
    assert SECRET not in text and "x.test" not in text


def test_failure_log_reports_clean_identifiers(caplog):
    problem = cdp_failed(reason="target_gone", effect_state="unknown")
    with caplog.at_level(logging.INFO, logger=bsk.log.name):
        bsk._log_unavailable(("cooperate", "sync", "--session", "s1"), problem)
    assert ("verb=cooperate sync code=cdp_failed reason=target_gone effect_state=unknown"
            in caplog.text)
