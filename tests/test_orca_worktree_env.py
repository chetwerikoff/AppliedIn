"""Orca setup must never inherit the operator checkout's environment."""

import os
import subprocess
from pathlib import Path

import yaml


ORCA_YAML = Path(__file__).resolve().parents[1] / "orca.yaml"
ROOT_SENTINEL = b"COPIED_SECRET_SENTINEL=fixture-only\n"
STALE_SENTINEL = b"STALE_SENTINEL=fixture-only\n"


def _base_port(worktree: Path) -> int:
    # Match the existing shell cksum on the literal worktree path, not Python's CRC32.
    result = subprocess.run(
        ["cksum"], input=os.fsencode(str(worktree)), capture_output=True, check=True
    )
    return 9000 + int(result.stdout.split()[0]) % 900


def _selected_port(worktree: Path) -> int:
    port = _base_port(worktree)
    return port + 1 if port % 15 == 13 else port


def _run_setup(tmp_path: Path, root: Path, worktree: Path) -> None:
    # The hook prepends this directory ahead of the inherited PATH, so a normal
    # test-PATH stub could be shadowed by real uv and fetch dependencies.
    home = tmp_path / "isolated-home"
    shims = home / ".local" / "share" / "mise" / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    stub = shims / "uv"
    marker = tmp_path / "uv-invocations"
    marker.unlink(missing_ok=True)
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_STUB_MARKER"\n', encoding="utf-8")
    stub.chmod(0o700)

    script = yaml.safe_load(ORCA_YAML.read_text(encoding="utf-8"))["scripts"]["setup"]
    result = subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=root,
        env={
            "HOME": str(home),
            "PATH": os.defpath,
            "ORCA_ROOT_PATH": str(root),
            "ORCA_WORKTREE_PATH": str(worktree),
            "UV_STUB_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_bytes() == b"sync --all-groups\n"


def _assert_isolated_env(worktree: Path) -> None:
    env_file = worktree / ".env"
    assert env_file.is_file() and not env_file.is_symlink()
    from dotenv import dotenv_values
    import cli
    import pytest
    from core.config import get_settings

    port = _selected_port(worktree)
    values = dotenv_values(env_file)
    assert values == {
        "APPLIEDIN_WEB_PORT": str(port),
        "APPLIEDIN_LOCAL_DIR": str(worktree / f".local-{port}"),
        "APPLIEDIN_REDIS_URL": f"redis://localhost:6379/{1 + port % 15}",
    }
    with pytest.MonkeyPatch.context() as private:
        for key, value in values.items():
            private.setenv(key, value)
        private.setenv("ORCA_WORKTREE_PATH", str(worktree))
        private.setattr(cli, "CONFIGURED_PORT", port)
        try:
            # This is the previously leaky configured-default CLI branch, not
            # merely a check of the values emitted by the setup script.
            instance = cli._apply_instance(None)
            assert Path(instance["local_dir"]).parent == worktree
            assert instance["redis"].rsplit("/", 1)[1] not in {"0", "14"}
        finally:
            get_settings.cache_clear()
    assert not (worktree / ".local").exists()


def test_replaces_stale_env_without_inheriting_root_and_is_repeatable(tmp_path: Path) -> None:
    root, worktree = tmp_path / "synthetic-root", tmp_path / "task worktree"
    root.mkdir()
    worktree.mkdir()
    root_env = root / ".env"
    root_env.write_bytes(ROOT_SENTINEL)
    (worktree / ".env").write_bytes(STALE_SENTINEL)

    _run_setup(tmp_path, root, worktree)
    _assert_isolated_env(worktree)
    assert root_env.read_bytes() == ROOT_SENTINEL

    # Another setup must not append a duplicate port or preserve stale assignments.
    _run_setup(tmp_path, root, worktree)
    _assert_isolated_env(worktree)
    assert root_env.read_bytes() == ROOT_SENTINEL


def test_replaces_symlink_without_writing_synthetic_root_env(tmp_path: Path) -> None:
    root, worktree = tmp_path / "synthetic-root", tmp_path / "task worktree"
    root.mkdir()
    worktree.mkdir()
    root_env = root / ".env"
    root_env.write_bytes(ROOT_SENTINEL)
    (worktree / ".env").symlink_to(root_env)

    _run_setup(tmp_path, root, worktree)
    _assert_isolated_env(worktree)
    assert root_env.read_bytes() == ROOT_SENTINEL


def test_setup_needs_no_root_env(tmp_path: Path) -> None:
    root, worktree = tmp_path / "synthetic-root", tmp_path / "task worktree"
    root.mkdir()
    worktree.mkdir()
    assert not (root / ".env").exists()

    _run_setup(tmp_path, root, worktree)
    _assert_isolated_env(worktree)
    assert not (root / ".env").exists()


def test_reserved_redis_db_slot_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-root"
    root.mkdir()
    candidates = (tmp_path / f"task-worktree-{i}" for i in range(200))
    worktree = next(path for path in candidates if _base_port(path) % 15 == 13)
    worktree.mkdir()
    base = _base_port(worktree)

    _run_setup(tmp_path, root, worktree)
    _assert_isolated_env(worktree)
    assert _selected_port(worktree) == base + 1
    assert 1 + (_selected_port(worktree) % 15) != 14


def test_missing_worktree_fails_without_changing_fake_root_env(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-root"
    root.mkdir()
    root_env = root / ".env"
    root_env.write_bytes(ROOT_SENTINEL)
    worktree = tmp_path / "missing worktree"

    home = tmp_path / "isolated-home"
    shims = home / ".local" / "share" / "mise" / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    stub = shims / "uv"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o700)

    script = yaml.safe_load(ORCA_YAML.read_text(encoding="utf-8"))["scripts"]["setup"]
    result = subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=root,
        env={
            "HOME": str(home),
            "PATH": os.defpath,
            "ORCA_ROOT_PATH": str(root),
            "ORCA_WORKTREE_PATH": str(worktree),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0, result.stderr
    assert root_env.read_bytes() == ROOT_SENTINEL
