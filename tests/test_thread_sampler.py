"""Offline tests for the bounded hot-thread sampler (#427). Synthetic only."""
from __future__ import annotations

import logging
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from core import thread_sampler
from core.thread_sampler import HotThreadSampler, format_frames


def _thread(name, ident):
    return SimpleNamespace(name=name, ident=ident)


def _frame_chain(depth):
    """A fake frame chain, innermost first: f0 <- f1 <- ... <- f{depth-1}."""
    frame = None
    for index in reversed(range(depth)):
        frame = SimpleNamespace(
            f_code=SimpleNamespace(
                co_filename=f"/repo/src/pkg/mod{index}.py", co_name=f"fn{index}"),
            f_lineno=10 + index, f_back=frame)
    return frame


def _sampler(threads, cpu, frames, clock, current=1):
    return HotThreadSampler(
        clock=clock, threads=lambda: threads, frames=lambda: frames,
        cpu=lambda t: cpu[t.ident], current_ident=lambda: current)


def test_format_frames_keeps_top_eight_innermost_first():
    parts = format_frames(_frame_chain(12)).split(" <- ")
    assert len(parts) == 8
    assert parts[0] == "pkg/mod0.py:10 fn0"
    assert parts[-1] == "pkg/mod7.py:17 fn7"


def test_format_frames_without_a_frame_says_so():
    assert format_frames(None) == "no frames"


def test_names_busiest_non_request_thread_and_skips_request_threads():
    threads = [_thread("MainThread", 1), _thread("AnyIO worker thread", 2),
               _thread("evaluate", 3), _thread("apply", 4)]
    cpu = {1: 90.0, 2: 80.0, 3: 50.0, 4: 1.0}
    frames = {3: _frame_chain(3), 4: _frame_chain(1)}
    sampler = _sampler(threads, cpu, frames, lambda: 0.0)

    line = sampler.sample()

    assert line.startswith("hot thread: evaluate cpu_since_last=50.00s cpu_total=50.0s")
    assert "pkg/mod0.py:10 fn0 <- pkg/mod1.py:11 fn1" in line
    assert "MainThread" not in line and "AnyIO" not in line
    assert "apply=1.00s/1.0s" in line


def test_busiest_is_by_cpu_since_last_sample_not_lifetime_total():
    threads = [_thread("old-hog", 3), _thread("now-hot", 4)]
    cpu = {3: 500.0, 4: 1.0}
    now = [0.0]
    sampler = _sampler(threads, cpu, {3: _frame_chain(1), 4: _frame_chain(1)},
                       lambda: now[0])
    assert sampler.sample().startswith("hot thread: old-hog")

    now[0] = 61.0
    cpu[3], cpu[4] = 500.5, 31.0

    assert sampler.sample().startswith(
        "hot thread: now-hot cpu_since_last=30.00s cpu_total=31.0s")


def test_at_most_one_line_per_minute():
    now = [100.0]
    sampler = _sampler([_thread("evaluate", 3)], {3: 1.0}, {3: _frame_chain(1)},
                       lambda: now[0])

    assert sampler.sample() is not None
    now[0] = 159.9
    assert sampler.sample() is None
    now[0] = 160.0
    assert sampler.sample() is not None


def test_rate_limited_call_does_not_move_the_baseline():
    cpu = {3: 10.0}
    now = [0.0]
    sampler = _sampler([_thread("evaluate", 3)], cpu, {3: _frame_chain(1)},
                       lambda: now[0])
    sampler.sample()
    now[0], cpu[3] = 30.0, 40.0
    assert sampler.sample() is None
    now[0], cpu[3] = 61.0, 70.0

    assert "cpu_since_last=60.00s" in sampler.sample()


def test_no_reportable_thread_returns_none_and_does_not_burn_the_minute():
    threads = [_thread("MainThread", 1), _thread("no-clock", 5)]
    cpu = {1: 5.0, 5: None, 3: 2.0}
    sampler = _sampler(threads, cpu, {3: _frame_chain(1)}, lambda: 0.0)
    assert sampler.sample() is None

    threads.append(_thread("evaluate", 3))
    assert sampler.sample() is not None


def test_parse_task_cpu_handles_names_with_spaces_and_parens():
    stat = "42 (my (odd) name) S " + " ".join(["0"] * 10) + " 300 50 " + "0 " * 5
    assert thread_sampler.parse_task_cpu_seconds(stat) == (
        350 / thread_sampler._CLOCK_TICKS)
    assert thread_sampler.parse_task_cpu_seconds("garbage") is None
    assert thread_sampler.parse_task_cpu_seconds("1 (x) S 1 2") is None


def test_cpu_of_a_task_that_exited_is_unavailable_not_a_crash():
    if not os.path.isdir("/proc/self/task"):
        pytest.skip("platform has no /proc task files")
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join(timeout=2)

    assert thread_sampler.thread_cpu_seconds(worker) is None
    assert thread_sampler.thread_cpu_seconds(SimpleNamespace(native_id=None)) is None


def test_task_vanishing_mid_sample_is_skipped_even_if_reader_raises():
    threads = [_thread("gone", 3), _thread("evaluate", 4)]

    def cpu(thread):
        if thread.ident == 3:
            raise FileNotFoundError("task exited during the sample")
        return 5.0

    sampler = HotThreadSampler(
        clock=lambda: 0.0, threads=lambda: threads,
        frames=lambda: {4: _frame_chain(1)}, cpu=cpu, current_ident=lambda: 1)

    line = sampler.sample()

    assert line.startswith("hot thread: evaluate")
    assert "gone" not in line


def test_replacement_thread_with_a_recycled_ident_starts_a_fresh_baseline():
    first, second = _thread("old-worker", 3), _thread("new-worker", 3)
    live, cpu, now = [first], {3: 500.0}, [0.0]
    sampler = HotThreadSampler(
        clock=lambda: now[0], threads=lambda: list(live),
        frames=lambda: {3: _frame_chain(1)}, cpu=lambda t: cpu[t.ident],
        current_ident=lambda: 1)
    sampler.sample()

    now[0], live[:], cpu[3] = 61.0, [second], 7.0
    line = sampler.sample()

    assert line.startswith("hot thread: new-worker cpu_since_last=7.00s cpu_total=7.0s")


def test_stack_is_omitted_when_the_ident_changed_owner_during_the_sample():
    first, second = _thread("old-worker", 3), _thread("new-worker", 3)
    enumerations = iter([[first], [second]])
    sampler = HotThreadSampler(
        clock=lambda: 0.0, threads=lambda: next(enumerations),
        frames=lambda: {3: _frame_chain(2)}, cpu=lambda t: 4.0,
        current_ident=lambda: 1)

    line = sampler.sample()

    assert line.startswith("hot thread: old-worker")
    assert "frames=unavailable" in line
    assert "mod0.py" not in line


def test_stack_is_omitted_when_the_sampled_thread_is_gone():
    gone = _thread("worker", 3)
    enumerations = iter([[gone], []])
    sampler = HotThreadSampler(
        clock=lambda: 0.0, threads=lambda: next(enumerations),
        frames=lambda: {3: _frame_chain(2)}, cpu=lambda t: 4.0,
        current_ident=lambda: 1)

    assert "frames=unavailable" in sampler.sample()


def test_real_interpreter_names_a_busy_thread():
    if thread_sampler.thread_cpu_seconds(threading.current_thread()) is None:
        pytest.skip("platform has no per-thread CPU clock")
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            pass

    worker = threading.Thread(target=spin, name="spinner", daemon=True)
    worker.start()
    try:
        time.sleep(0.2)
        line = HotThreadSampler().sample()
    finally:
        stop.set()
        worker.join(timeout=2)

    assert line.startswith("hot thread: spinner")
    assert "spin" in line


@pytest.fixture
def slow_client(monkeypatch):
    app = server.FastAPI()

    @app.get("/slow")
    def slow():
        return {"ok": True}

    server._install_request_timing(app)
    return app


def _fake_wall(monkeypatch, *readings):
    values = iter(readings)
    monkeypatch.setattr(server, "_wall_clock", lambda: next(values))


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "server.timing"]


def test_slow_request_logs_one_hot_thread_line(slow_client, monkeypatch, caplog):
    _fake_wall(monkeypatch, 100.0, 101.5)
    monkeypatch.setattr(server, "_hot_sampler", SimpleNamespace(
        sample=lambda: "hot thread: evaluate frames=x"))
    caplog.set_level(logging.WARNING, logger="server.timing")

    assert TestClient(slow_client).get("/slow").status_code == 200

    lines = _lines(caplog)
    assert lines[0].startswith("slow request: GET /slow")
    assert lines[1] == "hot thread: evaluate frames=x"


def test_fast_request_never_samples(slow_client, monkeypatch):
    _fake_wall(monkeypatch, 100.0, 100.1)
    calls = []
    monkeypatch.setattr(server, "_hot_sampler", SimpleNamespace(
        sample=lambda: calls.append(1)))

    assert TestClient(slow_client).get("/slow").status_code == 200

    assert calls == []


def test_sampler_failure_never_breaks_the_request(slow_client, monkeypatch, caplog):
    def boom():
        raise RuntimeError("sampler broke")

    _fake_wall(monkeypatch, 100.0, 101.5)
    monkeypatch.setattr(server, "_hot_sampler", SimpleNamespace(sample=boom))
    caplog.set_level(logging.WARNING, logger="server.timing")

    assert TestClient(slow_client).get("/slow").status_code == 200
    assert len(_lines(caplog)) == 1


def test_rate_limited_sampler_adds_no_second_line(slow_client, monkeypatch, caplog):
    _fake_wall(monkeypatch, 100.0, 101.5)
    monkeypatch.setattr(server, "_hot_sampler", SimpleNamespace(sample=lambda: None))
    caplog.set_level(logging.WARNING, logger="server.timing")

    TestClient(slow_client).get("/slow")

    assert len(_lines(caplog)) == 1
