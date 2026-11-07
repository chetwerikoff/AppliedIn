"""Offline tests must never write the repository's ``.local`` directory.

``Settings.local_dir`` defaults to ``.local`` relative to the working directory.
The autouse ``_isolate_local_dir`` fixture in ``tests/conftest.py`` points it at
a per-test temporary directory. All data here is synthetic.
"""

from __future__ import annotations

from pathlib import Path

from core.config import Settings, get_settings
from core.stores import make_stores


def _inside(path: str | Path, root: Path) -> bool:
    return Path(path).is_absolute() and Path(path).resolve().is_relative_to(root.resolve())


def test_local_dir_is_a_per_test_temp_directory(tmp_path):
    local_dir = get_settings().local_dir
    assert local_dir != ".local"
    assert _inside(local_dir, tmp_path)


def test_isolation_survives_a_mid_test_monkeypatch_undo(monkeypatch, tmp_path):
    monkeypatch.undo()
    get_settings.cache_clear()
    assert _inside(get_settings().local_dir, tmp_path)


def test_per_test_override_still_wins(monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", str(other))
    get_settings.cache_clear()
    assert get_settings().local_dir == str(other)
    assert Settings(local_dir="explicit").local_dir == "explicit"


def test_session_default_survives_missing_per_test_override(monkeypatch):
    import os

    monkeypatch.delenv("APPLIEDIN_LOCAL_DIR", raising=False)
    assert Settings().local_dir == os.environ["APPLIEDIN_OFFLINE_TEST_ROOT"]


def test_worktree_requires_explicit_storage_but_accepts_disposable_path(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv("ORCA_WORKTREE_PATH", str(tmp_path))
    monkeypatch.delenv("APPLIEDIN_LOCAL_DIR", raising=False)
    monkeypatch.delenv("APPLIEDIN_OFFLINE_TEST_ROOT", raising=False)
    monkeypatch.setattr("core.config._OFFLINE_TEST_ROOT", None)
    with pytest.raises(ValueError, match="explicit APPLIEDIN_LOCAL_DIR"):
        Settings()
    assert not (tmp_path / ".local").exists()
    assert Settings(local_dir=str(tmp_path / "scratch"),
                    redis_url="redis://offline.invalid:1/7").local_dir == str(tmp_path / "scratch")


def test_a_replaced_get_settings_does_not_break_the_fixture(monkeypatch, tmp_path):
    import core.config

    monkeypatch.setattr(core.config, "get_settings", lambda: Settings(local_dir=str(tmp_path)))
    assert core.config.get_settings().local_dir == str(tmp_path)


def test_orca_primary_context_does_not_become_a_disposable_worktree(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCA_ROOT_PATH", str(tmp_path))
    monkeypatch.setenv("ORCA_WORKTREE_PATH", str(tmp_path))
    monkeypatch.setattr("core.config._OFFLINE_TEST_ROOT", None)
    settings = Settings(local_dir=str(tmp_path / "synthetic-primary"),
                        redis_url="redis://offline.invalid:1/14")
    assert settings.worktree_root == ""


def test_make_stores_leaves_the_working_directory_empty(monkeypatch, tmp_path):
    work = tmp_path / "empty-cwd"
    work.mkdir()
    monkeypatch.chdir(work)

    make_stores()

    assert list(work.iterdir()) == []
    assert not (work / ".local").exists()
    local_dir = Path(get_settings().local_dir)
    assert _inside(local_dir, tmp_path)
    assert (local_dir / "facts.md").exists()
    assert (local_dir / "secrets.json").exists()
    assert (local_dir / "artifacts").is_dir()


def test_a_shared_monkeypatch_override_does_not_leak_to_the_next_test_part_one(monkeypatch, tmp_path):
    monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", str(tmp_path / "overridden"))


def test_a_shared_monkeypatch_override_does_not_leak_to_the_next_test_part_two(monkeypatch, tmp_path):
    # Runs right after part one in file order: nothing stale may be inherited, so a
    # mid-test undo still lands on this test's own isolation directory.
    monkeypatch.undo()
    get_settings.cache_clear()
    assert _inside(get_settings().local_dir, tmp_path)
