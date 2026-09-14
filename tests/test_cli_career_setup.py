"""Each instance installs its own scanner after selecting and resetting its paths."""

from pathlib import Path
from unittest.mock import Mock

import pytest

import cli
from discovery import career_ops_setup


@pytest.mark.parametrize("fresh", [False, True])
def test_port_is_resolved_before_install_even_for_an_existing_daemon(tmp_path, monkeypatch, fresh):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APPLIEDIN_LOCAL_DIR", "default-private-data")
    monkeypatch.setenv("APPLIEDIN_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("APPLIEDIN_WEB_PORT", "8787")
    calls = []
    monkeypatch.setattr(cli, "_wipe_instance", lambda: calls.append("reset") or {})
    monkeypatch.setattr(career_ops_setup, "ensure_checkout", lambda p: calls.append(p))
    monkeypatch.setattr(cli, "_live_pid", lambda: 123)
    try:
        cli.main(["start", "--port", "8788"] + (["--fresh"] if fresh else []))
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
