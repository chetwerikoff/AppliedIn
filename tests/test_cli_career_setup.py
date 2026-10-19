"""Each instance installs its own scanner after selecting and resetting its paths."""

from contextlib import contextmanager
from importlib import reload
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cli
from discovery import career_ops_setup


@pytest.mark.parametrize("fresh", [False, True])
def test_port_is_resolved_before_install_even_for_an_existing_daemon(
    tmp_path, monkeypatch, fresh
 ):
    with _cli_with_configured_port(monkeypatch, tmp_path, 8787):
        monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", "default-private-data")
        monkeypatch.setenv("APPLIEDIN_REDIS_URL", "redis://localhost:6379/0")
        calls = []
        monkeypatch.setattr(cli, "_wipe_instance", lambda: calls.append("reset") or {})
        monkeypatch.setattr(career_ops_setup, "ensure_checkout", lambda p: calls.append(p))
        monkeypatch.setattr(cli, "_live_pid", lambda: 123)
        try:
            cli.main(
                ["start", "--port", "8788"]
                + (["--fresh"] if fresh else [])
            )
            assert calls == (["reset"] if fresh else []) + [Path(".local-8788")]
            assert not (tmp_path / "default-private-data").exists()
        finally:
            cli.get_settings.cache_clear()


def test_failed_install_does_not_launch_a_daemon_with_a_broken_scanner(monkeypatch):
    monkeypatch.setattr(
        career_ops_setup, "ensure_checkout", Mock(side_effect=ValueError("missing"))
    )
    launch = Mock()
    monkeypatch.setattr(cli, "_live_pid", launch)
    assert cli.start()["status"] == "setup failed"
    launch.assert_not_called()


@contextmanager
def _cli_with_configured_port(monkeypatch, tmp_path, port):
    original_configured_port = cli.CONFIGURED_PORT
    with monkeypatch.context() as scoped:
        scoped.chdir(tmp_path)
        scoped.setenv("APPLIEDIN_LOCAL_DIR", str(tmp_path / "configured-default"))
        scoped.setenv("APPLIEDIN_REDIS_URL", "redis://synthetic.invalid/0")
        if port is None:
            scoped.delenv("APPLIEDIN_WEB_PORT", raising=False)
        else:
            scoped.setenv("APPLIEDIN_WEB_PORT", str(port))
        cli.get_settings.cache_clear()
        reload(cli)
        try:
            yield cli
        finally:
            cli.get_settings.cache_clear()
    cli.get_settings.cache_clear()
    reload(cli)
    cli.CONFIGURED_PORT = original_configured_port


def test_configured_9123_equal_port_and_fresh(monkeypatch, tmp_path, capsys):
    default_dir = str(tmp_path / "configured-default")
    default_redis = "redis://synthetic.invalid/0"
    with _cli_with_configured_port(monkeypatch, tmp_path, 9123):
        monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", default_dir)
        monkeypatch.setenv("APPLIEDIN_REDIS_URL", default_redis)
        cli.get_settings.cache_clear()
        assert cli.CONFIGURED_PORT == 9123

        monkeypatch.setattr(cli, "status", lambda: {"board": {"synthetic": 1}})
        cli.main(["status", "--port", "9123"])
        selected = json.loads(capsys.readouterr().out)
        assert selected["board"] == {"synthetic": 1}
        assert selected["instance"] == {
            "port": 9123,
            "instance": "default",
            "local_dir": default_dir,
            "redis": default_redis,
        }

        rmtree = Mock()
        mkdir = Mock()
        redis_from_url = Mock()
        start = Mock()
        stores = Mock()
        monkeypatch.setattr(shutil, "rmtree", rmtree)
        monkeypatch.setattr(Path, "mkdir", mkdir)
        monkeypatch.setitem(
            sys.modules,
            "redis",
            SimpleNamespace(Redis=SimpleNamespace(from_url=redis_from_url)),
        )
        monkeypatch.setattr(cli, "start", start)
        monkeypatch.setattr(cli, "make_stores", stores)
        cli.main(["start", "--port", "9123", "--fresh"])
        refusal = json.loads(capsys.readouterr().out)
        assert refusal["status"] == "refused"
        assert refusal["error"] == (
            "--fresh will not wipe the default instance on port 9123; "
            "give it a --port of its own"
        )
        rmtree.assert_not_called()
        mkdir.assert_not_called()
        redis_from_url.assert_not_called()
        start.assert_not_called()
        stores.assert_not_called()

        with pytest.raises(SystemExit) as help_exit:
            cli.main(["start", "--help"])
        assert help_exit.value.code == 0
        help_text = capsys.readouterr().out
        assert "default instance on 9123" in help_text
        assert "web port (default 9123)" in help_text


def test_different_9124_isolated_assignments(monkeypatch, tmp_path, capsys):
    with _cli_with_configured_port(monkeypatch, tmp_path, 9123):
        monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", "inherited-default-directory")
        monkeypatch.setenv("APPLIEDIN_REDIS_URL", "redis://synthetic.invalid/4")
        cli.get_settings.cache_clear()
        monkeypatch.setattr(cli, "status", lambda: {"board": {"synthetic": 1}})

        cli.main(["status", "--port", "9124"])

        selected = json.loads(capsys.readouterr().out)
        expected_redis = f"redis://localhost:6379/{cli._instance_db(9124)}"
        assert cli.CONFIGURED_PORT == 9123
        assert selected["instance"] == {
            "port": 9124,
            "instance": "port-9124",
            "local_dir": ".local-9124",
            "redis": expected_redis,
        }
        assert os.environ["APPLIEDIN_LOCAL_DIR"] == ".local-9124"
        assert os.environ["APPLIEDIN_REDIS_URL"] == expected_redis
        assert not (tmp_path / ".local-9124").exists()


def test_unconfigured_8787_legacy_selection(monkeypatch, tmp_path, capsys):
    with _cli_with_configured_port(monkeypatch, tmp_path, None):
        monkeypatch.delenv("APPLIEDIN_WEB_PORT", raising=False)
        default_dir = str(tmp_path / "configured-default")
        default_redis = "redis://synthetic.invalid/0"
        monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", default_dir)
        monkeypatch.setenv("APPLIEDIN_REDIS_URL", default_redis)
        cli.get_settings.cache_clear()
        assert cli.CONFIGURED_PORT == cli.DEFAULT_PORT == 8787

        implicit = cli._apply_instance(None)
        assert implicit["port"] == 8787
        assert implicit["instance"] == "default"
        assert implicit["local_dir"] == default_dir
        assert implicit["redis"] == default_redis

        monkeypatch.setattr(cli, "status", lambda: {"board": {"synthetic": 1}})
        cli.main(["status", "--port", "8787"])
        explicit = json.loads(capsys.readouterr().out)
        assert explicit["instance"]["instance"] == "default"
        assert explicit["instance"]["local_dir"] == default_dir
        assert explicit["instance"]["redis"] == default_redis

        alternate = cli._apply_instance(8788)
        assert alternate["port"] == 8788
        assert alternate["instance"] == "port-8788"
        assert alternate["local_dir"] == ".local-8788"
        assert alternate["redis"] == "redis://localhost:6379/14"

        with pytest.raises(SystemExit) as help_exit:
            cli.main(["start", "--help"])
        assert help_exit.value.code == 0
        assert "web port (default 8787)" in capsys.readouterr().out
