"""BrowserSkill 0.4 cooperation context: offline, against a fake `bsk.command`.

Protocol facts these tests encode (observed on 0.4.0 with a harmless fixture):
a `cooperate sync` context is single use and only the latest is valid, every
shared-tab action spends it, and the stale-context refusal is code
permission_denied with data.reason cooperation_sync_required and
data.effect_state none."""
from __future__ import annotations

import json
from types import SimpleNamespace

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


def page_changed(effect_state="none") -> bsk.Unavailable:
    """The live BrowserSkill 0.4.0 refusal of `cooperate sync` right after navigation."""
    raw = json.dumps({
        "code": "permission_denied",
        "message": "Page changed while synchronizing; sync again after it settles",
        "hint": "use `bsk cooperate wait --session SID`, then `bsk cooperate sync --session SID`",
        "exit_code": 1,
        "data": {"effect_state": effect_state, "reason": "cooperation_sync_required",
                 "recovery": "Use cooperate wait, then cooperate sync."}})
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
        self.sync_outcomes: list = []  # scripted refusals for upcoming syncs, then normal

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("session", "start"):
            return {"session_id": "s1", "browser_instance_id": "dedicated"}
        if args[:2] == ("session", "stop"):
            return {"ok": True}
        if args[0] == "cooperate":
            assert args[1] == "sync" and "--context" not in args
            if self.sync_outcomes:
                raise self.sync_outcomes.pop(0)
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
    monkeypatch.setattr(bsk, "_SYNC_POLL", 0)  # never really sleep between settle attempts
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


# --- Sync refused while the page settles (BrowserSkill 0.4 timing) ---


async def test_navigation_succeeds_when_its_own_sync_is_refused_and_next_action_settles(fake):
    async with bsk.Session("jd") as session:
        fake.outcomes = [None]
        fake.sync_outcomes = [page_changed(), page_changed(), page_changed()]
        # Post-navigation sync is refused once: the navigation itself must not fail.
        await session._run(("navigate", "http://x.test/"), {})
        assert session._context == "" and session._tab_ready
        assert len(fake.syncs()) == 1
        # The next action performs the bounded sync: two more refusals, then success.
        result = await session.call("evaluate", PAGE)
    assert result["ok"] is True
    assert len(fake.syncs()) == 4
    assert context_of(fake.actions("evaluate")[0]) == fake.latest_issued


async def test_session_navigate_reads_page_after_refused_post_navigation_sync(fake):
    async with bsk.Session("jd") as session:
        fake.outcomes = [None, {"ok": True, "value": {"url": "http://x.test/", "text": "t" * 500}}]
        fake.sync_outcomes = [page_changed()]
        page = await session.navigate("http://x.test/")
    assert page["url"] == "http://x.test/"
    assert len(fake.actions("evaluate")) == 1


async def test_sync_settle_window_is_bounded(fake, monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(bsk, "time", SimpleNamespace(monotonic=lambda: float(next(ticks))))
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")  # spend the post-navigation context
        fake.sync_outcomes = [page_changed() for _ in range(100)]
        before = len(fake.syncs())
        with pytest.raises(bsk.Unavailable, match="permission_denied"):
            await session.call("evaluate", PAGE)
    attempts = len(fake.syncs()) - before
    assert 2 <= attempts <= int(bsk._SYNC_WINDOW) + 1  # bounded, not forever
    assert len(fake.actions("evaluate")) == 1  # the unsynced action never ran


@pytest.mark.parametrize("effect_state", ["unknown", "partial", "committed", "", "None "])
async def test_sync_refusal_with_an_unsafe_effect_state_is_final(fake, effect_state):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")
        fake.sync_outcomes = [page_changed(effect_state), page_changed()]
        before = len(fake.syncs())
        with pytest.raises(bsk.Unavailable):
            await session.call("evaluate", PAGE)
    assert len(fake.syncs()) - before == 1  # no settle retry
    assert len(fake.actions("evaluate")) == 1


async def test_sync_refusal_without_effect_state_is_final(fake):
    problem = refusal(None)
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")
        fake.sync_outcomes = [problem]
        before = len(fake.syncs())
        with pytest.raises(bsk.Unavailable):
            await session.call("evaluate", PAGE)
    assert len(fake.syncs()) - before == 1


@pytest.mark.parametrize("problem", [
    refusal("none", reason="something_else"),
    refusal("none", code="timeout"),
    refusal("none", code="page_crashed"),
])
async def test_sync_refusal_outside_the_two_identifiers_is_final(fake, problem):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        await session.call("evaluate", "1")
        fake.sync_outcomes = [problem, page_changed()]
        before = len(fake.syncs())
        with pytest.raises(bsk.Unavailable):
            await session.call("evaluate", PAGE)
    assert len(fake.syncs()) - before == 1


async def test_non_refusal_failure_of_the_post_navigation_sync_still_fails_the_navigation(fake):
    async with bsk.Session("jd") as session:
        fake.outcomes = [None]
        fake.sync_outcomes = [page_changed("unknown")]
        with pytest.raises(bsk.Unavailable):
            await session._run(("navigate", "http://x.test/"), {})


async def test_sync_without_a_bound_tab_is_never_settled(fake):
    async with bsk.Session("jd") as session:
        fake.sync_outcomes = [page_changed(), page_changed()]
        with pytest.raises(bsk.Unavailable):
            await session.sync_context()
    assert len(fake.syncs()) == 1


async def test_page_changed_action_refusal_syncs_and_retries_the_action_once(fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [page_changed("none"), {"ok": True, "value": {"url": "u", "x": 2}}]
        before = len(fake.syncs())
        result = await session.call("evaluate", PAGE)  # runs on the navigation context
        assert result["value"]["x"] == 2
    attempts = fake.actions("evaluate")
    assert len(attempts) == 2 and context_of(attempts[0]) != context_of(attempts[1])
    assert len(fake.syncs()) - before == 1  # exactly one repair sync


async def test_repair_sync_after_page_changed_refusal_settles_then_retries_once(fake, monkeypatch):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [page_changed("none"), {"ok": True, "value": {"url": "u", "x": 3}}]
        fake.sync_outcomes = []
        before = len(fake.syncs())
        # Arm the refusals only after the action ran: the repair sync is the next sync.
        original = fake.__call__

        async def arm(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            finally:
                if args[0] == "evaluate" and not fake.sync_outcomes and len(fake.actions("evaluate")) == 1:
                    fake.sync_outcomes = [page_changed(), page_changed()]
        monkeypatch.setattr(bsk, "command", arm)
        result = await session.call("evaluate", PAGE)
    assert result["value"]["x"] == 3
    assert len(fake.actions("evaluate")) == 2
    assert len(fake.syncs()) - before == 3  # two settling refusals + the successful repair sync


async def test_page_changed_action_refusal_with_unsafe_effect_state_is_final(fake):
    async with bsk.Session("jd") as session:
        await open_tab(session, fake)
        fake.outcomes = [page_changed("unknown"), {"ok": True}]
        with pytest.raises(bsk.Unavailable):
            await session.call("click", "#a")
    assert len(fake.actions("click")) == 1
