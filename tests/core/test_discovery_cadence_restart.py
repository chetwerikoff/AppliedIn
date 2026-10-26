"""The scheduled discovery cycle resumes its persisted schedule across a restart.

Offline: flags are an in-memory dict, the clock and sleep are stubbed, and the
discovery runner itself is a stub.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import daemon
from core import flags

INTERVAL = daemon.DISCOVER_INTERVAL
NOW = 1_800_000_000.0


class _StopLoop(Exception):
    pass


@pytest.mark.parametrize("last, expected", [
    (str(NOW - INTERVAL), NOW),                    # exactly one interval ago: due now
    (str(NOW - INTERVAL - 600), NOW - 600),        # overdue: due in the past, runs at once
    (str(NOW - 300), NOW - 300 + INTERVAL),        # five minutes ago: waits the remainder
    ("", NOW + INTERVAL),                          # absent: one interval after boot
    (None, NOW + INTERVAL),
    ("not-a-number", NOW + INTERVAL),
    ("nan", NOW + INTERVAL),
    ("inf", NOW + INTERVAL),
    ("-inf", NOW + INTERVAL),
    (str(NOW + 5 * INTERVAL), NOW + INTERVAL),     # future: clamped to now
])
def test_due_time(last, expected):
    assert daemon._discovery_due(last, NOW) == pytest.approx(expected)
    assert math.isfinite(daemon._discovery_due(last, NOW))


class Harness:
    """Run `_discovery_loop` for `ticks` polls and record what it did."""

    def __init__(self, monkeypatch, persisted=None, paused=False, persist_ok=True,
                 run_raises=False):
        self.store = {} if persisted is None else {"last_discovery_at": persisted}
        self.paused = paused
        self.persist_ok = persist_ok
        self.writes = []
        self.runs = []
        self.clock = NOW
        monkeypatch.setattr(flags, "get_flag", lambda name, default="": self.store.get(name, default))
        monkeypatch.setattr(flags, "set_flag", self._set)
        monkeypatch.setattr(flags, "paused", lambda: self.paused)
        monkeypatch.setattr("discovery.career_ops.scheduled_tick", lambda: None)
        monkeypatch.setattr("discovery.handler.run_discovery", self._run)
        self.run_raises = run_raises
        self.ticks = 0
        monkeypatch.setattr(daemon, "time", SimpleNamespace(
            time=lambda: self.clock, monotonic=lambda: self.clock, sleep=self._sleep))

    def _set(self, name, value):
        self.writes.append((name, value))
        if self.persist_ok:
            self.store[name] = value

    def _run(self):
        self.runs.append(self.clock)
        if self.run_raises:
            raise RuntimeError("crawl failed")
        return {}

    def _sleep(self, seconds):
        self.ticks += 1
        if self.ticks >= self.limit:
            raise _StopLoop
        self.clock += self.step

    def go(self, ticks, step=5):
        self.limit, self.step = ticks, step
        with pytest.raises(_StopLoop):
            daemon._discovery_loop()
        return self


def test_overdue_cycle_runs_on_the_first_tick(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW - INTERVAL)).go(3)
    assert h.runs[0] == NOW and len(h.runs) == 1
    assert float(h.store["last_discovery_at"]) == NOW


def test_recent_cycle_waits_only_for_the_remainder(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW - 300)).go(2)
    assert h.runs == []
    remainder = INTERVAL - 300
    h = Harness(monkeypatch, persisted=str(NOW - 300)).go(int(remainder / 5) + 3)
    assert len(h.runs) == 1
    assert NOW + remainder <= h.runs[0] < NOW + remainder + 10


def test_no_persisted_value_waits_one_full_interval(monkeypatch):
    h = Harness(monkeypatch).go(3)
    assert h.runs == [] and h.writes == []


@pytest.mark.parametrize("bad", ["garbage", "nan", "inf", ""])
def test_damaged_value_behaves_as_absent_and_never_kills_the_thread(monkeypatch, bad):
    h = Harness(monkeypatch, persisted=bad).go(3)
    assert h.runs == []


def test_future_value_is_clamped_to_now(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW + 10 * INTERVAL)).go(3)
    assert h.runs == []  # clamped: due a full interval from now, not never


def test_a_failed_cycle_still_persists_its_start(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW - INTERVAL), run_raises=True).go(3)
    assert len(h.runs) == 1
    assert float(h.store["last_discovery_at"]) == NOW


def test_a_failed_persistence_write_does_not_repeat_the_cycle_every_tick(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW - INTERVAL), persist_ok=False).go(20)
    assert len(h.runs) == 1, "the in-memory due time must advance despite the failed write"


def test_paused_runs_nothing_and_persists_nothing(monkeypatch):
    h = Harness(monkeypatch, persisted=str(NOW - 10 * INTERVAL), paused=True).go(4)
    assert h.runs == [] and h.writes == []
