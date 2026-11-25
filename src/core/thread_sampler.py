"""Bounded hot-thread sampler for slow dashboard requests (#427, step 1).

A slow request whose own thread CPU is tiny is waiting for the GIL. This module
names the thread that holds it: when asked, it picks the non-request thread
that used the most CPU since the last logged sample and reports its top frames.

It is a measurement tool only. It reads interpreter state, takes no lock the
workers use, and logs at most one line per ``MIN_INTERVAL_S``. Every source of
time, threads and frames is injectable, so tests run offline and deterministic.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

MAX_FRAMES = 8
MIN_INTERVAL_S = 60.0
# Starlette runs sync endpoints on these threads; they are the requests, not
# the load that starves them.
_REQUEST_THREAD_PREFIX = "AnyIO worker thread"
_MAX_THREADS_LISTED = 12


_CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def parse_task_cpu_seconds(stat_text: str) -> float | None:
    """utime + stime from the text of ``/proc/<pid>/task/<tid>/stat``."""
    # The command name sits in parentheses and may hold spaces or ')'; the
    # fixed fields start after the LAST ')'. After it: state, then field 14
    # (utime) and 15 (stime) are at index 11 and 12.
    try:
        fields = stat_text.rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / _CLOCK_TICKS
    except (IndexError, ValueError):
        return None


def thread_cpu_seconds(thread: threading.Thread) -> float | None:
    """CPU seconds a thread has used, or None when it cannot be read.

    Reads the kernel's own record of the task by its native id. Unlike
    ``pthread_getcpuclockid`` on another thread's id, a task that exits during
    the sample is a failed file read here, never undefined behavior.
    """
    native_id = getattr(thread, "native_id", None)
    if native_id is None:
        return None
    try:
        with open(f"/proc/self/task/{native_id}/stat", encoding="ascii",
                  errors="replace") as handle:
            return parse_task_cpu_seconds(handle.read())
    except OSError:
        return None


def format_frames(frame, limit: int = MAX_FRAMES) -> str:  # noqa: ANN001
    """Innermost-first ``file:line func`` for up to ``limit`` frames."""
    parts: list[str] = []
    while frame is not None and len(parts) < limit:
        code = frame.f_code
        name = code.co_filename.rsplit("/", 2)
        short = "/".join(name[-2:]) if len(name) > 1 else code.co_filename
        parts.append(f"{short}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    return " <- ".join(parts) if parts else "no frames"


@dataclass
class _Reading:
    thread: threading.Thread
    total: float
    delta: float


class HotThreadSampler:
    """Rate-limited sampler. One instance per process."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        threads: Callable[[], list[threading.Thread]] = threading.enumerate,
        frames: Callable[[], dict] = sys._current_frames,
        cpu: Callable[[threading.Thread], float | None] = thread_cpu_seconds,
        current_ident: Callable[[], int] = threading.get_ident,
        min_interval_s: float = MIN_INTERVAL_S,
    ) -> None:
        self._clock = clock
        self._threads = threads
        self._frames = frames
        self._cpu = cpu
        self._current_ident = current_ident
        self._min_interval_s = min_interval_s
        self._last_logged: float | None = None
        # ident -> (thread object, cpu total). The object is part of the key:
        # a replacement thread may reuse an ident and must start fresh.
        self._baseline: dict[int, tuple[threading.Thread, float]] = {}
        self._lock = threading.Lock()

    def _is_request_thread(self, thread: threading.Thread) -> bool:
        return (thread.ident == self._current_ident()
                or thread.name.startswith(_REQUEST_THREAD_PREFIX))

    def _read_cpu(self, thread: threading.Thread) -> float | None:
        try:
            return self._cpu(thread)
        except Exception:  # noqa: BLE001 - a vanished task is "unavailable", never a crash
            return None

    def _same_thread_still_owns(self, thread: threading.Thread) -> bool:
        """True when ``thread`` is still the live owner of its ident."""
        for current in self._threads():
            if current.ident == thread.ident:
                return current is thread
        return False

    def sample(self) -> str | None:
        """Return one log line, or None when rate limited or nothing to report."""
        now = self._clock()
        with self._lock:
            if (self._last_logged is not None
                    and now - self._last_logged < self._min_interval_s):
                return None
            readings: list[_Reading] = []
            totals: dict[int, tuple[threading.Thread, float]] = {}
            for thread in self._threads():
                if thread.ident is None or self._is_request_thread(thread):
                    continue
                total = self._read_cpu(thread)
                if total is None:
                    continue
                totals[thread.ident] = (thread, total)
                seen = self._baseline.get(thread.ident)
                before = seen[1] if seen is not None and seen[0] is thread else 0.0
                readings.append(_Reading(thread, total, total - before))
            if not readings:
                return None
            self._last_logged = now
            self._baseline = totals
        readings.sort(key=lambda r: r.delta, reverse=True)
        hot = readings[0]
        frame = self._frames().get(hot.thread.ident)
        # sys._current_frames is keyed by ident alone. Show the stack only while
        # the sampled thread object still owns that ident.
        stack = (format_frames(frame) if self._same_thread_still_owns(hot.thread)
                 else "unavailable")
        others = ", ".join(
            f"{r.thread.name}={r.delta:.2f}s/{r.total:.1f}s"
            for r in readings[:_MAX_THREADS_LISTED])
        return (f"hot thread: {hot.thread.name} cpu_since_last={hot.delta:.2f}s "
                f"cpu_total={hot.total:.1f}s frames={stack} "
                f"| threads cpu_since_last/cpu_total: {others}")
