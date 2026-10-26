"""A pause the system set for a disconnected browser lifts itself; nobody else's does.

Everything here is offline: fakeredis stands in for the flags hash, the browser
connection check and the configured engine are monkeypatched, and no daemon,
browser or `bsk` process is involved.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis
import pytest

import daemon
from agent.run import _is_browser_skill_disconnect
from core import flags
from tools import claude_chrome as cc

AUTO = flags.AUTO_PAUSE_MARKER
REASON = flags.DISCONNECT_PAUSE_REASON
SIGNED_OUT = "signed out of the Claude CLI"
KEY = "appliedin:flags"


@pytest.fixture
def fake(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(flags, "_redis", lambda: r)
    return r


@pytest.fixture
def witness(monkeypatch):
    """Stub the engine lookup and the read-only connection check."""
    state = SimpleNamespace(engine="browser_skill", status="connected", calls=0, raises=False)

    def configuration():
        if state.engine is None:
            raise ValueError("bad configuration")
        return {"engine": state.engine}

    def connection_status():
        state.calls += 1
        if state.raises:
            raise OSError("probe failed")
        return {"state": state.status}

    monkeypatch.setattr("tools.browser_runtime.configuration", configuration)
    monkeypatch.setattr("tools.browser_profile.connection_status", connection_status)
    return state


def _hash(r):
    return dict(r.hgetall(KEY))


def _race_before_commit(r, mutate, times=1):
    """Make `mutate(r)` land between a pipeline's read and its EXEC, `times` times."""
    original = r.pipeline
    left = {"n": times}

    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute

        def racing_execute(*a, **k):
            if left["n"] > 0:
                left["n"] -= 1
                mutate(r)
            return execute(*a, **k)

        pipe.execute = racing_execute
        return pipe

    r.pipeline = pipeline


# --- AC1: marked, atomic auto-pause -------------------------------------------------

def test_auto_pause_marks_a_releasable_pause(fake):
    assert flags.auto_pause(releasable=True) is True
    assert _hash(fake) == {"paused": "yes", "paused_auto": AUTO, "paused_reason": REASON}


def test_auto_pause_without_origin_leaves_no_marker_and_drops_a_stale_one(fake):
    fake.hset(KEY, "paused_auto", AUTO)
    assert flags.auto_pause(releasable=False) is True
    assert _hash(fake) == {"paused": "yes", "paused_reason": REASON}


@pytest.mark.parametrize("existing", [
    {"paused": "yes"},
    {"paused": "yes", "paused_reason": SIGNED_OUT},
])
def test_auto_pause_never_relabels_an_existing_pause(fake, existing):
    fake.hset(KEY, mapping=existing)
    assert flags.auto_pause(releasable=True) is False
    assert _hash(fake) == existing


def test_auto_pause_survives_an_unrelated_write_with_a_bounded_retry(fake):
    _race_before_commit(fake, lambda r: r.hset(KEY, "heartbeat", "1"), times=1)
    assert flags.auto_pause(releasable=True) is True
    assert _hash(fake)["paused_auto"] == AUTO


def test_auto_pause_gives_up_after_its_attempts(fake):
    _race_before_commit(fake, lambda r: r.hincrby(KEY, "heartbeat", 1), times=10)
    assert flags.auto_pause(releasable=True) is False
    assert "paused" not in _hash(fake)


# --- AC2: release only when connected, only for the auto reason ---------------------

def _auto_pause(r):
    r.hset(KEY, mapping={"paused": "yes", "paused_auto": AUTO, "paused_reason": REASON})


def test_release_happens_once_connected(fake, witness):
    _auto_pause(fake)
    assert daemon._release_auto_pause_if_connected() is True
    assert _hash(fake) == {"paused": "no"}


@pytest.mark.parametrize("status", [
    "extension_missing", "not_running", "setup_required", "unavailable", ""])
def test_release_waits_for_connected(fake, witness, status):
    _auto_pause(fake)
    witness.status = status
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake)["paused"] == "yes"


def test_a_failing_connection_check_releases_nothing(fake, witness):
    _auto_pause(fake)
    witness.raises = True
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake)["paused"] == "yes"


@pytest.mark.parametrize("engine", ["chrome", "something_else", None])
def test_only_the_browser_skill_engine_has_a_witness(fake, witness, engine):
    _auto_pause(fake)
    witness.engine = engine
    assert daemon._release_auto_pause_if_connected() is False
    assert witness.calls == 0, "the connection check must not even run"
    assert _hash(fake)["paused"] == "yes"


@pytest.mark.parametrize("state", [
    {"paused": "yes", "paused_reason": REASON},                       # no marker
    {"paused": "yes", "paused_auto": AUTO, "paused_reason": SIGNED_OUT},
    {"paused": "yes", "paused_reason": SIGNED_OUT},
    {"paused": "yes", "paused_auto": AUTO},                           # no reason
    {"paused": "no", "paused_auto": AUTO, "paused_reason": REASON},
    {},
])
def test_unmarked_or_mismatched_pauses_are_never_released(fake, witness, state):
    if state:
        fake.hset(KEY, mapping=state)
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake) == state


def test_signed_out_pause_set_by_the_old_path_is_left_alone(fake, witness):
    fake.hset(KEY, mapping={"paused": "yes", "paused_reason": SIGNED_OUT})
    assert daemon._release_auto_pause_if_connected() is False
    assert witness.calls == 0


# --- AC3: a manual pause is never released and keeps the dispatch hold ---------------

def test_manual_pause_over_an_auto_pause_drops_the_marker_and_keeps_the_reason(fake, witness):
    _auto_pause(fake)
    flags.set_manual_pause(True)
    assert _hash(fake) == {"paused": "yes", "paused_reason": REASON}
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake)["paused"] == "yes"


def test_a_requeued_requested_job_stays_held_after_manual_pause_over_auto(fake):
    _auto_pause(fake)
    flags.set_manual_pause(True)

    class Queue:
        def next(self, **kwargs):
            raise AssertionError("a held pause must not consult the queue")

    assert daemon.paused_dispatchable(Queue(), flags.get_flag("paused_reason")) is None


def test_manual_unpause_clears_marker_and_reason(fake):
    _auto_pause(fake)
    flags.set_manual_pause(False)
    assert _hash(fake) == {"paused": "no"}


def test_plain_manual_pause_sets_only_paused(fake):
    flags.set_manual_pause(True)
    assert _hash(fake) == {"paused": "yes"}


def test_manual_pause_is_not_lost_to_an_unrelated_write(fake):
    _auto_pause(fake)
    _race_before_commit(fake, lambda r: r.hset(KEY, "heartbeat", "1"), times=3)
    flags.set_manual_pause(True)
    assert "paused_auto" not in _hash(fake)
    assert _hash(fake)["paused"] == "yes"


def test_release_that_races_a_manual_pause_fails_closed(fake):
    _auto_pause(fake)
    _race_before_commit(fake, lambda r: (r.hdel(KEY, "paused_auto"), r.hset(KEY, "paused", "yes")))
    assert flags.release_auto_pause() is False
    assert _hash(fake) == {"paused": "yes", "paused_reason": REASON}


def test_release_fails_closed_when_redis_raises(monkeypatch):
    def broken():
        raise ConnectionError("down")

    monkeypatch.setattr(flags, "_redis", broken)
    assert flags.release_auto_pause() is False
    assert flags.auto_pause(releasable=True) is False
    flags.set_manual_pause(True)  # best effort: must not raise


# --- origin: the error signature decides, not the config read later -----------------

def test_origin_comes_from_the_error_signature():
    assert _is_browser_skill_disconnect("BrowserSkill unavailable: profile x is not connected.")
    assert not _is_browser_skill_disconnect(cc.DISCONNECT_MESSAGE)
    assert not _is_browser_skill_disconnect("BrowserSkill unavailable: " + cc.DISCONNECT_MESSAGE)
    assert not _is_browser_skill_disconnect("some other failure")
    assert not _is_browser_skill_disconnect("")
    assert not _is_browser_skill_disconnect(None)


def test_config_flipped_while_a_chrome_apply_is_pending_cannot_release_its_pause(fake, witness):
    # The apply began under `chrome`; the config now names a connected BrowserSkill
    # profile; the Chrome operation then returns its own disconnect.
    witness.engine, witness.status = "browser_skill", "connected"
    flags.auto_pause(releasable=_is_browser_skill_disconnect(cc.DISCONNECT_MESSAGE))
    assert _hash(fake) == {"paused": "yes", "paused_reason": REASON}
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake)["paused"] == "yes"


def test_a_browser_skill_disconnect_releases_once_connected(fake, witness):
    flags.auto_pause(releasable=_is_browser_skill_disconnect(
        "BrowserSkill unavailable: profile x is not connected."))
    assert daemon._release_auto_pause_if_connected() is True


# --- upgrade safety: rollback then re-upgrade ----------------------------------------

def test_leftover_marker_beside_the_signed_out_reason_fails_closed(fake, witness):
    # Old code changed the reason to the signed-out text and left the marker.
    fake.hset(KEY, mapping={"paused": "yes", "paused_auto": AUTO, "paused_reason": SIGNED_OUT})
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake)["paused"] == "yes"


def test_leftover_marker_beside_the_same_reason_needs_the_operator_cleanup(fake, witness):
    # Old code's manual unpause + re-pause can leave marker AND disconnect reason on
    # a manual pause. The helper cannot tell it from an auto-pause; the operator
    # clears `paused_auto` once before restarting on the new code.
    fake.hset(KEY, mapping={"paused": "yes", "paused_auto": AUTO, "paused_reason": REASON})
    fake.hdel(KEY, "paused_auto")  # the one-time operator step
    assert daemon._release_auto_pause_if_connected() is False
    assert _hash(fake) == {"paused": "yes", "paused_reason": REASON}


# --- AC4: release cadence is bounded ------------------------------------------------

class _StopLoop(Exception):
    pass


def test_release_check_runs_at_most_once_per_window(monkeypatch):
    clock = {"t": 1000.0}
    ticks = {"n": 0}
    calls = []

    def sleep(seconds):
        ticks["n"] += 1
        if ticks["n"] >= 25:
            raise _StopLoop
        clock["t"] += seconds

    monkeypatch.setattr(daemon, "time", SimpleNamespace(monotonic=lambda: clock["t"], sleep=sleep))

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

        def reset_leases(self):
            return 0

    monkeypatch.setattr("core.apply_queue.ApplyQueue", FakeQueue)
    monkeypatch.setattr(daemon, "make_stores", lambda: SimpleNamespace(
        tracking=SimpleNamespace(r=None), queue=SimpleNamespace(drain=lambda q: []),
        apply_queue=None))
    monkeypatch.setattr(flags, "paused", lambda: True)
    monkeypatch.setattr(flags, "apply_concurrency", lambda: 0)
    monkeypatch.setattr(daemon, "_release_auto_pause_if_connected",
                        lambda: calls.append(clock["t"]) or False)

    with pytest.raises(_StopLoop):
        daemon._apply_loop()

    assert len(calls) == 5
    assert all(b - a >= daemon._RELEASE_CHECK_SECONDS for a, b in zip(calls, calls[1:]))
