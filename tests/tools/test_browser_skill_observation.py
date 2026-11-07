"""Offline argv tests: bsk 0.4.1 shared-tab actions carry the sync observation id."""
import json

import pytest

from tools import browser_skill as bsk

REAL_COOPERATION = True  # opt out of the legacy-argv fixture in conftest.py


class FakeBsk:
    """Fake bsk CLI: one sync per action, refuses an action without both ids."""

    def __init__(self, with_observation=True):
        self.calls: list[tuple] = []
        self.with_observation = with_observation
        self.tab = False
        self.issued = 0

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("session", "start"):
            return {"session_id": "s1", "browser_instance_id": "dedicated"}
        if args[:2] == ("session", "stop"):
            return {"ok": True}
        if args[0] == "cooperate":
            self.issued += 1
            payload = {"context_id": f"ctx-{self.issued}", "kind": "sync"}
            if self.with_observation:
                payload["observation_id"] = f"obs-{self.issued}"
                payload["observation"] = {"observation_id": payload["observation_id"]}
            return payload
        if args[0] == "navigate":
            self.tab = True
        return {"ok": True, "value": {}}

    def action_argv(self, verb):
        return [c for c in self.calls if c[0] == verb]


@pytest.fixture
def make(monkeypatch):
    def build(**kwargs):
        monkeypatch.setattr(bsk, "available", lambda: (True, ""))
        monkeypatch.setattr("tools.browser_profile.ensure_browser", lambda: (True, ""))
        monkeypatch.setattr("tools.browser_runtime.configuration",
                            lambda *a, **k: {"browser": "dedicated"})
        monkeypatch.setattr(bsk, "_SYNC_POLL", 0)
        fake = FakeBsk(**kwargs)
        monkeypatch.setattr(bsk, "command", fake)
        return fake
    return build


def flag(call, name):
    return call[call.index(name) + 1]


@pytest.mark.asyncio
async def test_navigate_and_evaluate_carry_context_and_observation(make):
    fake = make()
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/")
        await session.call("evaluate", "1+1")
    first_nav, = fake.action_argv("navigate")
    assert "--observation" not in first_nav and "--context" not in first_nav  # no tab yet
    evaluate, = fake.action_argv("evaluate")
    assert flag(evaluate, "--context") == "ctx-1"
    assert flag(evaluate, "--observation") == "obs-1"
    assert flag(evaluate, "--session") == "s1"


@pytest.mark.asyncio
async def test_each_action_uses_its_own_fresh_observation(make):
    fake = make()
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/")
        await session.call("evaluate", "1")
        await session.call("evaluate", "2")
    first, second = fake.action_argv("evaluate")
    assert flag(first, "--observation") != flag(second, "--observation")
    assert flag(first, "--context") != flag(second, "--context")


@pytest.mark.asyncio
async def test_second_navigation_carries_both_ids(make):
    fake = make()
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/a")
        await session.call("navigate", "https://example.test/b")
    _, second = fake.action_argv("navigate")
    assert flag(second, "--context").startswith("ctx-")
    assert flag(second, "--observation").startswith("obs-")


@pytest.mark.asyncio
async def test_nested_observation_id_is_accepted(make, monkeypatch):
    fake = make()
    original = fake.__call__

    async def nested(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[0] == "cooperate":
            return {"data": {"context_id": "ctx-n", "observation_id": "obs-n"}}
        return result

    monkeypatch.setattr(bsk, "command", nested)
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/")
        await session.call("evaluate", "1")
    evaluate, = [c for c in fake.calls if c[0] == "evaluate"]
    assert flag(evaluate, "--context") == "ctx-n"
    assert flag(evaluate, "--observation") == "obs-n"


@pytest.mark.asyncio
async def test_missing_observation_keeps_context_only_argv(make):
    fake = make(with_observation=False)
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/")
        await session.call("evaluate", "1")
    evaluate, = fake.action_argv("evaluate")
    assert "--observation" not in evaluate
    assert flag(evaluate, "--context") == "ctx-1"  # --context is never dropped


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [7, "", "bad value;rm", ["x"]])
async def test_malformed_observation_fails_closed(make, monkeypatch, bad):
    fake = make()
    original = fake.__call__

    async def malformed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[0] == "cooperate":
            result["observation_id"] = bad
        return result

    monkeypatch.setattr(bsk, "command", malformed)
    async with bsk.Session("read") as session:
        with pytest.raises(bsk.Unavailable):  # the post-navigation sync is malformed
            await session.call("navigate", "https://example.test/")
        with pytest.raises(bsk.Unavailable):
            await session.call("evaluate", "1")
    assert not [c for c in fake.calls if c[0] == "evaluate"]


@pytest.mark.asyncio
async def test_lifecycle_commands_never_get_observation(make):
    fake = make()
    async with bsk.Session("read") as session:
        await session.call("navigate", "https://example.test/")
        await session.sync_context()
    for call in fake.calls:
        if call[0] in {"session", "cooperate"}:
            assert "--observation" not in call and "--context" not in call


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,refusals,attempts", [
    ("crawl", 1, 2), ("crawl", 2, 2),
    ("apply", 1, 1), ("jd", 1, 1), ("read", 1, 1),
])
async def test_observation_race_has_one_shared_retry_for_crawl_only(
        monkeypatch, kind, refusals, attempts):
    calls = []

    async def cli(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("cooperate", "sync"):
            return {"context_id": "fresh-context", "observation_id": "fresh-observation"}
        if len([c for c in calls if c[0] == "evaluate"]) <= refusals:
            return bsk._result(json.dumps({
                "ok": False, "code": "permission_denied",
                "data": {"reason": "observation_changed", "effect_state": "none"},
            }), 1)
        return {"ok": True}

    monkeypatch.setattr(bsk, "command", cli)
    session = bsk.Session(kind)
    session.id = "synthetic-session"
    session._context, session._observation = "old-context", "old-observation"
    if kind == "crawl" and refusals == 1:
        assert await session._run(("evaluate", "1"), {}) == {"ok": True}
    else:
        with pytest.raises(bsk.Unavailable) as error:
            await session._run(("evaluate", "1"), {})
        assert error.value.reason == "observation_changed"
    actions = [c for c in calls if c[0] == "evaluate"]
    assert len(actions) == attempts
    assert len([c for c in calls if c[0] == "cooperate"]) == attempts - 1
    if attempts == 2:
        assert flag(actions[1], "--context") == "fresh-context"
        assert flag(actions[1], "--observation") == "fresh-observation"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,reason,state", [
    ("permission_denied", "observation_changed", "unknown"),
    ("permission_denied", "observation_changed", "committed"),
    ("permission_denied", "observation_changed", ""),
    ("other_failure", "observation_changed", "none"),
    ("permission_denied", "other_reason", "none"),
])
async def test_crawl_observation_retry_requires_exact_no_effect_refusal(
        monkeypatch, code, reason, state):
    calls = []

    async def cli(*args, **kwargs):
        calls.append(args)
        return bsk._result(json.dumps({
            "ok": False, "code": code,
            "data": {"reason": reason, "effect_state": state},
        }), 1)

    monkeypatch.setattr(bsk, "command", cli)
    with pytest.raises(bsk.Unavailable):
        await bsk.Session("crawl")._run(("evaluate", "1"), {})
    assert len(calls) == 1  # No sync or blind second action after an uncertain effect.
