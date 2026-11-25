"""The Career Ops schedule check does not re-parse an unchanged board file (#427).

Offline: a small board file in tmp_path, no network, no browser, no daemon.
"""

from __future__ import annotations

import json
import os

import pytest

from discovery import career_ops as co


@pytest.fixture
def board_file(tmp_path, monkeypatch):
    path = tmp_path / "board.json"
    monkeypatch.setattr(co, "_path", lambda: path)
    co._SCHEDULE_CACHE.clear()
    reads = []
    real_read = co._read

    def counting_read():
        reads.append(1)
        return real_read()

    monkeypatch.setattr(co, "_read", counting_read)
    path.write_text(json.dumps({
        "jobs": {"a": {"x": 1}},
        "settings": {"scheduled": True},
        "last_scan": {"finished_at": "2026-01-01T00:00:00+00:00"},
    }))
    yield path, reads
    co._SCHEDULE_CACHE.clear()


def test_unchanged_file_is_parsed_once_across_many_ticks(board_file):
    _, reads = board_file
    views = [co._schedule_view() for _ in range(50)]
    assert len(reads) == 1
    assert all(v["last_scan"]["finished_at"] == "2026-01-01T00:00:00+00:00" for v in views)
    assert "jobs" not in views[0]  # only the small schedule fields are kept


def test_caller_cannot_change_the_cached_view(board_file):
    _, reads = board_file
    first = co._schedule_view()
    first["settings"]["scheduled"] = False
    first["last_scan"]["finished_at"] = "tampered"
    second = co._schedule_view()
    assert second["settings"]["scheduled"] is True
    assert second["last_scan"]["finished_at"] == "2026-01-01T00:00:00+00:00"
    assert len(reads) == 1


def test_write_invalidates_the_cache(board_file):
    _, reads = board_file
    co._schedule_view()
    co._write({"jobs": {}, "settings": {"scheduled": False}})
    view = co._schedule_view()
    assert len(reads) == 2
    assert view["settings"] == {"scheduled": False}
    co._schedule_view()
    assert len(reads) == 2


def test_mtime_change_reads_again(board_file):
    path, reads = board_file
    co._schedule_view()
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    co._schedule_view()
    assert len(reads) == 2


def test_size_change_reads_again(board_file):
    path, reads = board_file
    co._schedule_view()
    stat = path.stat()
    path.write_text(json.dumps({"settings": {"scheduled": False}, "pad": "x" * 40}))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same mtime, new size
    view = co._schedule_view()
    assert len(reads) == 2
    assert view["settings"] == {"scheduled": False}


def test_missing_file_gives_defaults(board_file):
    path, _ = board_file
    path.unlink()
    view = co._schedule_view()
    assert view["settings"]["scheduled"] is True


def test_scheduled_tick_parses_unchanged_board_once(board_file, monkeypatch):
    _, reads = board_file
    monkeypatch.setattr("core.flags.paused", lambda: False)
    monkeypatch.setattr(co, "checkout", lambda: board_file[0].parent)
    started = []
    monkeypatch.setattr(co, "reserve_scan", lambda kind, **kw: started.append(kind) or False)
    monkeypatch.setattr(co, "now", lambda: "2026-01-01T01:00:00+00:00")  # not yet due
    # Only the missing network receipt makes the board due; the reservation refuses.
    for _ in range(20):
        co.scheduled_tick()
    assert len(reads) == 1
    assert started == ["network"] * 20
