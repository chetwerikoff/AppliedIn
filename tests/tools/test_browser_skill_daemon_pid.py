"""A dead daemon's stale record permits a launch; a live or unprovable one never does.

Offline: `bsk status`, `os.kill`, `Popen` and the clock are stubbed, `BSK_HOME` is a
tmp directory, and nothing is signalled.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import browser_skill as bsk

REFUSAL = "BrowserSkill unavailable: existing daemon cannot be reached; run bsk doctor."


class Env:
    def __init__(self, monkeypatch, tmp_path):
        self.home = tmp_path / "bsk"
        self.home.mkdir()
        self.signals = []
        self.launches = []
        self.status = []          # queue of outcomes: True = reachable, False = Unavailable
        self.kill = lambda pid, sig: None
        monkeypatch.setenv("BSK_HOME", str(self.home))
        monkeypatch.setattr(bsk, "get_settings", lambda: SimpleNamespace(local_dir=tmp_path / "local"))
        monkeypatch.setattr(bsk, "_executable", lambda: "bsk")
        monkeypatch.setattr(bsk, "_env", lambda: {})
        monkeypatch.setattr(bsk, "_sync", self._sync)
        monkeypatch.setattr(bsk.os, "kill", self._kill)
        monkeypatch.setattr(bsk.time, "sleep", lambda s: None)
        monkeypatch.setattr(bsk.subprocess, "Popen", self._popen)

    def record(self, text):
        (self.home / "daemon.json").write_text(text)
        self.before = text

    def _sync(self, *args, **kwargs):
        ok = self.status.pop(0) if self.status else False
        if not ok:
            raise bsk.Unavailable("BrowserSkill unavailable: no daemon")
        return {"ok": True}

    def _kill(self, pid, sig):
        self.signals.append((pid, sig))
        return self.kill(pid, sig)

    def _popen(self, *args, **kwargs):
        self.launches.append(args)
        self.status[:] = [True]   # the launched daemon answers its next probe
        return SimpleNamespace(poll=lambda: None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def _dead(pid, sig):
    raise ProcessLookupError


def _denied(pid, sig):
    raise PermissionError


def test_dead_pid_reaches_the_launch_path(env):
    env.record(json.dumps({"pid": 4242}))
    env.kill = _dead
    assert bsk.ensure_daemon() == (True, "")
    assert len(env.launches) == 1
    assert env.signals == [(4242, 0)]
    assert (env.home / "daemon.json").read_text() == env.before


@pytest.mark.parametrize("kill", [lambda pid, sig: None, _denied], ids=["live", "permission"])
def test_live_or_unprovable_pid_refuses(env, kill):
    env.record(json.dumps({"pid": 4242}))
    env.kill = kill
    assert bsk.ensure_daemon() == (False, REFUSAL)
    assert env.launches == []
    assert all(sig == 0 for _, sig in env.signals)
    assert (env.home / "daemon.json").read_text() == env.before


@pytest.mark.parametrize("text", [
    json.dumps({}), json.dumps({"pid": 0}), json.dumps({"pid": -5}),
    json.dumps({"pid": "4242"}), json.dumps({"pid": 4242.0}), json.dumps({"pid": True}),
    json.dumps([4242]), "{not json", "",
])
def test_missing_or_malformed_pid_refuses_without_signalling(env, text):
    env.record(text)
    env.kill = _dead  # would say "dead" if it were ever asked
    assert bsk.ensure_daemon() == (False, REFUSAL)
    assert env.launches == []
    assert env.signals == []
    assert (env.home / "daemon.json").read_text() == env.before


def test_an_out_of_range_pid_refuses(env):
    def overflow(pid, sig):
        raise OverflowError("signed integer is greater than maximum")

    env.record(json.dumps({"pid": 10 ** 40}))
    env.kill = overflow
    assert bsk.ensure_daemon() == (False, REFUSAL)
    assert env.launches == []


def test_no_record_launches_as_before(env):
    assert bsk.ensure_daemon() == (True, "")
    assert len(env.launches) == 1
    assert env.signals == []


def test_a_daemon_that_became_reachable_after_the_dead_pid_check_is_reused(env):
    env.record(json.dumps({"pid": 4242}))
    env.kill = _dead
    env.status = [False, True]   # first probe fails, the re-probe succeeds
    assert bsk.ensure_daemon() == (True, "")
    assert env.launches == []


def test_reachable_daemon_is_reused_without_reading_the_record(env):
    env.record(json.dumps({"pid": 4242}))
    env.status = [True]
    assert bsk.ensure_daemon() == (True, "")
    assert env.signals == [] and env.launches == []
