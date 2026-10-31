"""BrowserSkill 0.4 cooperation context: offline, against a fake `bsk.command`.

Protocol facts these tests encode (observed on 0.4.0 with a harmless fixture):
a `cooperate sync` context is single use and only the latest is valid, every
shared-tab action spends it, and the stale-context refusal is code
permission_denied with data.reason cooperation_sync_required and
data.effect_state none."""
from __future__ import annotations

import json

import pytest
from tools import browser_skill as bsk
from tools.browser_skill_dom import PAGE

REAL_COOPERATION = True  # opt out of the legacy-argv fixture in conftest.py


def refusal(effect_state: str | None = "none", reason: str = "cooperation_sync_required",
            code: str = "permission_denied") -> bsk.Unavailable:
    data = {"reason": reason, "recovery": "sync"}
    if effect_state is not None:
        data["effect_state"] = effect_state
    raw = json.dumps({"code": code, "message": "needs sync", "exit_code": 1, "data": data})
    try:
        bsk._result(raw, 1)
    except bsk.Unavailable as exc:
        return exc
    raise AssertionError("refusal must raise")


class FakeBsk:
    """Single-use latest context, like the real daemon; scripted action outcomes."""

    def __init__(self, outcomes=None):
        self.calls: list[tuple] = []
        self.outcomes = list(outcomes or [])
        self.latest = None
        self.issued = 0
        self.latest_issued = None
        self.tab = False  # a fresh session has no shared tab until its first navigation

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("session", "start"):
            return {"session_id": "s1", "browser_instance_id": "dedicated"}
        if args[:2] == ("session", "stop"):
            return {"ok": True}
        if args[0] == "cooperate":
            assert args[1] == "sync" and "--context" not in args
            if not self.tab:
                raise refusal()  # real bsk: "Receiving end does not exist"
            self.issued += 1
            self.latest = self.latest_issued = f"ctx-{self.issued}"
            return {"context_id": self.latest, "kind": "sync"}
        given = args[args.index("--context") + 1] if "--context" in args else None
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, bsk.Unavailable):
            raise outcome
        first_navigation = args[0] == "navigate" and not self.tab and given is None
        if not first_navigation and (given is None or given != self.latest):
            raise refusal()
        self.latest = None  # spent
        if args[0] in {"navigate", "navigate-back", "navigate-forward", "reload"}:
            self.tab = True
        return outcome if outcome is not None else {"ok": True, "value": {}}

    def actions(self, verb=None):
        return [c for c in self.calls
                if c[0] not in {"session", "cooperate"} and (verb is None or c[0] == verb)]

    def syncs(self):
        return [c for c in self.calls if c[0] == "cooperate"]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(bsk, "available", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_profile.ensure_browser", lambda: (True, ""))
    monkeypatch.setattr("tools.browser_runtime.configuration", lambda *a, **k: {"browser": "dedicated"})
    bsk_fake = FakeBsk()
    monkeypatch.setattr(bsk, "command", bsk_fake)
    return bsk_fake


def context_of(call):
    return call[call.index("--context") + 1]


class Controller:
    def __init__(self):
        self.before, self.after = [], []

    async def before_ipc(self, session, args, kwargs):
        self.before.append(args)

    async def after_ipc(self, session, args, kwargs, *, succeeded):
        self.after.append((args, succeeded))

    def release_snapshot(self):
        pass


async def open_tab(session, fake):
    fake.outcomes.insert(0, None)
    await session._run(("navigate", "http://x.test/"), {})


async def test_start_does_not_sync_and_first_navigation_needs_no_context(fake):
    async with bsk.Session("jd") as session:
        assert fake.syncs() == []  # no shared tab yet; real bsk refuses a sync here
        await open_tab(session, fake)
    assert "--context" not in fake.actions("navigate")[0]
    assert len(fake.syncs()) == 1  # one sync right after the navigation


async def test_every_action_carries_the_latest_single_use_context(fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")
        await session.call("evaluate", "2")
        await session.call("click", "#a")
    contexts = [context_of(c) for c in fake.actions() if c[0] != "navigate"]
    assert contexts == ["ctx-1", "ctx-2", "ctx-3"]  # navigation context, then one sync per action
    assert all(c[-2:] == ("--session", "s1") for c in fake.actions())
    # Lifecycle commands never carry a context.
    assert all("--context" not in c for c in fake.calls if c[0] in {"session", "cooperate"})


@pytest.mark.parametrize("verb", ["navigate", "navigate-back", "navigate-forward", "reload"])
async def test_navigation_verbs_resync_and_next_action_reuses_that_context(fake, verb):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        before = len(fake.syncs())
        await session._run((verb, *(["http://y.test/"] if verb == "navigate" else [])), {})
        assert len(fake.syncs()) == before + 1
        await session.call("evaluate", "1")
        assert len(fake.syncs()) == before + 1  # the post-navigation context fed the read
        assert context_of(fake.actions("evaluate")[0]) == fake.latest_issued


async def test_session_navigate_reads_the_page_under_the_navigation_context(fake):
    async with bsk.Session("jd") as session:
        fake.outcomes = [None, {"ok": True, "value": {"url": "http://x.test/", "text": "t" * 500}}]
        page = await session.navigate("http://x.test/")
        assert page["url"] == "http://x.test/"
        order = [c[0] for c in fake.calls if c[0] in {"navigate", "cooperate", "evaluate"}]
        assert order == ["navigate", "cooperate", "evaluate"]


async def test_failed_sync_after_navigation_fails_closed(fake, monkeypatch):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        real = fake.__call__

        async def broken(*args, **kwargs):
            if args[0] == "cooperate":
                fake.calls.append(args)
                return {"kind": "sync"}  # no context_id
            return await real(*args, **kwargs)
        monkeypatch.setattr(bsk, "command", broken)
        with pytest.raises(bsk.Unavailable, match="cooperation_sync_failed"):
            await session._run(("navigate", "http://y.test/"), {})
        monkeypatch.setattr(bsk, "command", fake)


async def test_refusal_with_effect_none_syncs_and_retries_exactly_once(fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [refusal("none"), {"ok": True, "value": {"url": "u", "x": 1}}]
        result = await session.call("evaluate", PAGE)
    assert result["value"]["x"] == 1
    attempts = fake.actions("evaluate")
    assert len(attempts) == 2 and context_of(attempts[0]) != context_of(attempts[1])
    assert len(fake.syncs()) == 2  # after navigation, then the one repair sync


async def test_first_action_without_a_tab_is_refused_synced_once_and_fails_closed(fake):
    async with bsk.Session("jd") as session:
        with pytest.raises(bsk.Unavailable, match="permission_denied"):
            await session.call("evaluate", PAGE)  # no tab: the repair sync is refused too
    assert len(fake.actions("evaluate")) == 1 and len(fake.syncs()) == 1


async def test_second_refusal_is_final(fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [refusal("none"), refusal("none"), {"ok": True}]
        with pytest.raises(bsk.Unavailable, match="permission_denied"):
            await session.call("evaluate", PAGE)
    assert len(fake.actions("evaluate")) == 2


@pytest.mark.parametrize("effect_state", ["unknown", "partial", "committed", "", None, "None "])
async def test_non_none_effect_state_is_never_retried(fake, effect_state):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [refusal(effect_state), {"ok": True}]
        with pytest.raises(bsk.Unavailable):
            await session.call("click", "#a")
    assert len(fake.actions("click")) == 1
    assert len(fake.syncs()) == 1  # after navigation only, never a repair sync


@pytest.mark.parametrize("problem", [
    refusal("none", reason="something_else"),
    refusal("none", code="timeout"),
    refusal("none", reason="cooperation_sync_required", code="page_crashed"),
])
async def test_other_failures_are_never_retried_even_with_effect_none(fake, problem):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [problem, {"ok": True}]
        with pytest.raises(bsk.Unavailable):
            await session.call("evaluate", PAGE)
    assert len(fake.actions("evaluate")) == 1


async def test_effect_path_carries_context_and_retries_only_on_effect_none(fake):
    controller = Controller()
    async with bsk.Session("apply") as session:
        await open_tab(session, fake)
        session._application_controller = controller
        fake.outcomes = [refusal("none"), {"ok": True}]
        await session.application_effect("click", "#next")
        assert [c[0] for c in fake.actions("click")] == ["click", "click"]
        assert all("--context" in c for c in fake.actions("click"))
        # One producer-side bracket around the whole retried effect.
        assert controller.before == [("click", "#next")]
        assert controller.after == [(("click", "#next"), True)]

        fake.outcomes = [refusal("unknown"), {"ok": True}]
        with pytest.raises(bsk.Unavailable):
            await session.application_effect("fill", "#name", "--value", "Test User")
        assert len(fake.actions("fill")) == 1
        assert controller.after[-1] == (("fill", "#name", "--value", "Test User"), False)
        session._application_controller = None


async def test_effect_path_keeps_original_argv_for_producer_checks(fake):
    controller = Controller()
    async with bsk.Session("apply") as session:
        await open_tab(session, fake)
        session._application_controller = controller
        await session.application_effect("press", "Enter")
        session._application_controller = None
    assert controller.before == [("press", "Enter")]  # context is a transport detail
    assert fake.actions("press")[0][:2] == ("press", "Enter")
    assert "--context" in fake.actions("press")[0]


async def test_raw_application_allowlist_is_enforced_before_any_cli_or_sync(fake):
    async with bsk.Session("apply") as session:
        await open_tab(session, fake)
        before = len(fake.calls)
        for args in (("click", "#submit"), ("evaluate", "document.forms[0].submit()"),
                     ("evaluate", PAGE, "extra"), ("fill", "#a", "--value", "x")):
            with pytest.raises(bsk.Unavailable, match="Raw application IPC refused"):
                await session.call(*args)
        assert len(fake.calls) == before
        await session.call("evaluate", PAGE)  # a fixed code-owned reader still passes
        assert "--context" in fake.actions("evaluate")[0]


@pytest.mark.parametrize("payload", [{}, {"context_id": ""}, {"context_id": 7},
                                     {"context_id": "bad value;rm"}, {"data": {"context_id": None}}])
async def test_missing_or_malformed_context_fails_closed(monkeypatch, fake, payload):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")  # spends the post-navigation context
        calls = fake.calls

        async def command(*args, **kwargs):
            if args[0] == "cooperate":
                calls.append(args)
                return payload
            return await FakeBsk.__call__(fake, *args, **kwargs)
        monkeypatch.setattr(bsk, "command", command)
        with pytest.raises(bsk.Unavailable, match="cooperation_sync_failed"):
            await session.call("evaluate", PAGE)
    assert len(fake.actions("evaluate")) == 1  # only the setup read; the unsynced one never ran


async def test_nested_context_id_is_accepted(monkeypatch, fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")

        async def command(*args, **kwargs):
            if args[0] == "cooperate":
                fake.calls.append(args)
                return {"data": {"context_id": "nested-1"}}
            return await FakeBsk.__call__(fake, *args, **kwargs)
        monkeypatch.setattr(bsk, "command", command)
        fake.latest = "nested-1"
        await session.call("evaluate", PAGE)
    assert context_of(fake.actions("evaluate")[-1]) == "nested-1"
