"""Orca setup must never inherit the operator checkout's environment."""

import os
import subprocess
from pathlib import Path

import yaml
import pytest
from dotenv import dotenv_values, load_dotenv

import cli
import core.config
from core.config import get_settings


ORCA_YAML = Path(__file__).resolve().parents[1] / "orca.yaml"
ROOT_SENTINEL = b"COPIED_SECRET_SENTINEL=fixture-only\n"
STALE_SENTINEL = b"STALE_SENTINEL=fixture-only\n"


@pytest.fixture(autouse=True)
def _product_settings_boundary(_isolate_local_dir, monkeypatch, tmp_path):
    # Exercise product identity validation with synthetic declarations only;
    # the process transport guard and temporary paths remain active.
    monkeypatch.setattr(core.config, "_offline_test_root", lambda: None)
    monkeypatch.setattr(core.config, "_SETUP_ENV_FILE", tmp_path / "absent.env")


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
    port = _selected_port(worktree)
    values = dotenv_values(env_file)
    assert values == {
        "APPLIEDIN_WORKTREE_ROOT": str(worktree),
        "APPLIEDIN_WEB_PORT": str(port),
        "APPLIEDIN_LOCAL_DIR": str(worktree / f".local-{port}"),
        "APPLIEDIN_REDIS_URL": f"redis://localhost:6379/{1 + port % 15}",
    }
    with pytest.MonkeyPatch.context() as private:
        private.setattr(core.config, "_SETUP_ENV_FILE", env_file)
        for key, value in values.items():
            private.setenv(key, value)
        private.delenv("ORCA_WORKTREE_PATH", raising=False)
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


@pytest.mark.parametrize("directory,database", [
    ("outside", "7"), ("symlink", "7"), ("root", "7"),
    ("inside", "0"), ("inside", "14"), ("inside", "7?db=14"), ("inside", ""),
])
def test_inherited_storage_cannot_escape_declared_worktree(
        monkeypatch, tmp_path, directory, database):
    root, worktree = tmp_path / "synthetic-root", tmp_path / "task-worktree"
    root.mkdir()
    worktree.mkdir()
    _run_setup(tmp_path, root, worktree)
    primary = tmp_path / "synthetic-primary-runtime"
    primary.mkdir()
    sentinel = primary / "tracking-sentinel"
    sentinel.write_text("preserve")
    linked = worktree / "linked-runtime"
    linked.symlink_to(primary, target_is_directory=True)
    local = {"outside": primary, "symlink": linked, "root": worktree,
             "inside": worktree / ".local-test"}[directory]
    monkeypatch.delenv("ORCA_WORKTREE_PATH", raising=False)
    monkeypatch.delenv("APPLIEDIN_WORKTREE_ROOT", raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", str(local))
    monkeypatch.setenv("APPLIEDIN_REDIS_URL", f"redis://offline.invalid:1/{database}")
    # Match normal dotenv precedence: inherited storage wins over the file,
    # while the setup-persisted worktree identity still binds the process.
    load_dotenv(worktree / ".env", override=False)
    monkeypatch.setattr(core.config, "_SETUP_ENV_FILE", worktree / ".env")
    monkeypatch.setattr(cli, "CONFIGURED_PORT", _selected_port(worktree))
    monkeypatch.setattr(cli, "_wipe_instance", lambda: pytest.fail("wipe reached invalid storage"))
    expected = "APPLIEDIN_LOCAL_DIR" if directory != "inside" else "reserved Redis"
    with pytest.raises(ValueError, match=expected):
        cli._apply_instance(None, fresh=True)
    assert sentinel.read_text() == "preserve"
    assert not (worktree / ".local").exists()


def test_explicit_fresh_port_cannot_select_reserved_worktree_database(monkeypatch, tmp_path):
    worktree = tmp_path / "task-worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("APPLIEDIN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setenv("ORCA_WORKTREE_PATH", str(worktree))
    monkeypatch.setattr(cli, "CONFIGURED_PORT", 9000)
    monkeypatch.setattr(cli, "_wipe_instance", lambda: pytest.fail("reserved DB reached wipe"))
    assert cli._instance_db(8788) == 14
    with pytest.raises(ValueError, match="reserved Redis"):
        cli._apply_instance(8788, fresh=True)
    assert not (worktree / ".local-8788").exists()


def test_explicit_safe_fresh_instance_still_uses_disposable_stores(monkeypatch, tmp_path):
    worktree = tmp_path / "task-worktree"
    worktree.mkdir()
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("APPLIEDIN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setenv("ORCA_WORKTREE_PATH", str(worktree))
    monkeypatch.setattr(cli, "CONFIGURED_PORT", 9000)
    instance = cli._apply_instance(9001, fresh=True)
    assert instance["wiped"]["flushed"] is True  # The per-test fake Redis is usable.
    assert (worktree / ".local-9001").is_dir()
    assert not (worktree / ".local").exists()


@pytest.mark.parametrize("orca_context", [False, True])
@pytest.mark.parametrize("inherited", ["", "other-worktree", "copied-declaration"])
def test_empty_or_stale_export_cannot_replace_actual_setup_identity(
        monkeypatch, tmp_path, orca_context, inherited):
    root, worktree = tmp_path / "synthetic-root", tmp_path / "task-worktree"
    root.mkdir()
    worktree.mkdir()
    _run_setup(tmp_path, root, worktree)
    other = tmp_path / "other-worktree"
    other.mkdir()
    sentinel = other / "untouched"
    sentinel.write_text("preserve")
    if inherited == "copied-declaration":
        (worktree / ".env").write_text(f'APPLIEDIN_WORKTREE_ROOT="{other}"\n')
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(core.config, "_SETUP_ENV_FILE", worktree / ".env")
    monkeypatch.setenv("APPLIEDIN_WORKTREE_ROOT", str(other) if inherited else "")
    monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", str(other / ".local-test"))
    monkeypatch.setenv("APPLIEDIN_REDIS_URL", "redis://offline.invalid:1/7")
    if orca_context:
        monkeypatch.setenv("ORCA_WORKTREE_PATH", str(worktree))
    else:
        monkeypatch.delenv("ORCA_WORKTREE_PATH", raising=False)
    load_dotenv(worktree / ".env", override=False)
    monkeypatch.setattr(cli, "CONFIGURED_PORT", _selected_port(worktree))
    monkeypatch.setattr(cli, "_wipe_instance", lambda: pytest.fail("identity bypass reached wipe"))
    expected = "Invalid worktree setup" if inherited == "copied-declaration" else "Worktree root conflicts"
    with pytest.raises(ValueError, match=expected):
        cli._apply_instance(8788, fresh=True)
    assert sentinel.read_text() == "preserve"
    assert not (worktree / ".local-8788").exists()
