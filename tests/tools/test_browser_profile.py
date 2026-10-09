"""Cold Chrome starts must be owned, serialized, and pinned to a human-confirmed ID."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from tools import browser_profile as profile
from tools import browser_runtime as runtime
from tools import browser_skill as bsk


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    cfgfile = tmp_path / 'browser.local.yaml'
    directory = tmp_path / 'owned chrome'
    cfgfile.write_text(yaml.safe_dump({'engine': 'browser_skill', 'browser': 'pinned',
                                     'user_data_dir': str(directory),
                                     'chrome_path': '/fake/chrome'}))
    monkeypatch.setattr(profile, '_chrome', lambda cfg: '/fake/chrome')
    monkeypatch.setattr(bsk, 'ensure_daemon', lambda: (True, ''))
    monkeypatch.setattr(profile, '_STATUS_CACHE', ())
    monkeypatch.setattr(profile, 'profile_pids', lambda directory: [])
    monkeypatch.setattr(profile, '_browsers', lambda: [])
    monkeypatch.setattr(profile.subprocess, 'Popen',
                        lambda *a, **kw: pytest.fail('Unexpected real Chrome launch'))
    return cfgfile, directory


def test_connected_id_without_dedicated_process_is_refused(sandbox, monkeypatch):
    monkeypatch.setattr(profile, '_browsers', lambda: [{'instance_id': 'pinned'}])
    ready, why = profile.ensure_browser()
    assert not ready and 'absent or ambiguous' in why
    assert profile.connection_status()['state'] == 'unavailable'


@pytest.mark.parametrize('pids', [[123], [123, 456]])
def test_connected_id_requires_exactly_one_owned_chrome_process(sandbox, monkeypatch, pids):
    monkeypatch.setattr(profile, '_browsers', lambda: [{'instance_id': 'pinned'}])
    monkeypatch.setattr(profile, 'profile_pids', lambda directory: pids)
    ready, why = profile.ensure_browser()
    assert ready is (len(pids) == 1)
    if len(pids) != 1:
        assert 'ambiguous' in why
    else:
        assert profile.can_start() == (True, '')


def test_parallel_cold_starts_launch_once_with_only_approved_flags(sandbox, monkeypatch):
    _, directory = sandbox
    launched = []
    state = {'started': False}
    def spawn(args, **kwargs):
        launched.append((args, kwargs))
        state['started'] = True
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(profile.subprocess, 'Popen', spawn)
    monkeypatch.setattr(profile, '_browsers', lambda: (
        [{'instance_id': 'pinned'}] if state['started'] else []))
    monkeypatch.setattr(profile, 'profile_pids',
                        lambda directory: [123] if state['started'] else [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: profile.ensure_browser(), range(6)))
    assert results == [(True, '')] * 6
    assert len(launched) == 1
    argv, kwargs = launched[0]
    assert argv == ['/fake/chrome', '--user-data-dir=' + str(directory),
                    '--no-first-run', '--no-default-browser-check', profile.DASHBOARD]
    assert kwargs['start_new_session'] is True
    assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == -3


def test_running_owned_profile_without_extension_never_launches_a_second(sandbox, monkeypatch):
    monkeypatch.setattr(profile, 'profile_pids', lambda directory: [123])
    ready, why = profile.ensure_browser()
    assert not ready and 'profile is running' in why and 'No second' in why


def test_new_foreign_instance_is_rejected_after_one_launch(sandbox, monkeypatch):
    state = {'launched': False}
    def spawn(*a, **kw):
        state['launched'] = True
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(profile.subprocess, 'Popen', spawn)
    monkeypatch.setattr(profile, '_browsers', lambda: (
        [{'instance_id': 'foreign'}] if state['launched'] else []))
    ready, why = profile.ensure_browser()
    assert not ready and 'different instance' in why


def test_timeout_is_bounded_without_relaunch(sandbox, monkeypatch):
    launches = []
    clock = {'now': 0}
    monkeypatch.setattr(profile.subprocess, 'Popen',
                        lambda *a, **kw: (launches.append(a) or SimpleNamespace(pid=123)))
    monkeypatch.setattr(profile.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(profile.time, 'sleep',
                        lambda seconds: clock.update(now=clock['now'] + seconds))
    ready, why = profile.ensure_browser()
    assert not ready and '45 seconds' in why
    assert len(launches) == 1 and clock['now'] == 45


@pytest.mark.parametrize('value', [[], False, 123, ''])
def test_directory_requires_a_real_nonempty_path(sandbox, value):
    path, _ = sandbox
    path.write_text(yaml.safe_dump({'engine': 'browser_skill', 'user_data_dir': value}))
    with pytest.raises(ValueError, match='user_data_dir'):
        runtime.configuration()


@pytest.mark.parametrize('value', [[], False, 123])
def test_chrome_path_requires_a_string(sandbox, value):
    path, _ = sandbox
    path.write_text(yaml.safe_dump({'engine': 'browser_skill', 'chrome_path': value}))
    with pytest.raises(ValueError, match='chrome_path'):
        runtime.configuration()


@pytest.mark.parametrize('directory', ['~/.config/google-chrome',
                                     '~/.config/google-chrome/synthetic-profile',
                                     str(Path(__file__).resolve().parents[2] / '.local/chrome')])
def test_main_profile_and_repository_paths_are_rejected(sandbox, directory):
    path, _ = sandbox
    path.write_text(yaml.safe_dump({'engine': 'browser_skill', 'user_data_dir': directory}))
    with pytest.raises(ValueError, match='outside'):
        runtime.configuration()


def test_unknown_config_keys_still_fail(sandbox):
    path, _ = sandbox
    path.write_text('engine: browser_skill\nremote_debugging_port: 9222\n')
    with pytest.raises(ValueError, match='Invalid browser configuration'):
        runtime.configuration()


def test_process_detection_matches_exact_directory_and_split_flag(tmp_path, monkeypatch):
    directory = tmp_path / 'owned chrome'
    monkeypatch.setattr(profile, '_processes', lambda: iter([
        (1, ['chrome', '--user-data-dir=' + str(directory)]),
        (2, ['chrome', '--user-data-dir=' + str(directory) + '-other']),
        (3, ['chrome', '--user-data-dir', str(directory)]),
        (4, ['chrome']),
    ]))
    assert profile.profile_pids(str(directory)) == [1, 3]


@pytest.mark.parametrize(('connected', 'pids', 'expected'), [
    (True, [123], 'connected'), (True, [], 'unavailable'),
    (False, [123], 'extension_missing'), (False, [], 'not_running'),
])
def test_status_distinguishes_connection_states_without_launch(sandbox, monkeypatch,
                                                               connected, pids, expected):
    monkeypatch.setattr(profile, '_browsers',
                        lambda: [{'instance_id': 'pinned'}] if connected else [])
    monkeypatch.setattr(profile, 'profile_pids', lambda directory: pids)
    status = runtime.browser_status()
    assert status['state'] == expected
    check = runtime.setup_check()
    assert check['state'] == ('ready' if expected == 'connected' else
                              'check' if expected == 'not_running' else 'action')


@pytest.mark.parametrize('answer', ['', 'n', 'y'])
def test_setup_saves_only_after_y_and_keeps_optional_settings(sandbox, monkeypatch, answer):
    path, directory = sandbox
    # Cold initial setup has no saved pin; a previously saved different ID
    # must never be silently overwritten by a newly visible instance.
    settings = yaml.safe_load(path.read_text())
    settings['browser'] = ''
    path.write_text(yaml.safe_dump(settings))
    before = path.read_text()
    calls = []
    monkeypatch.setattr(profile, '_browsers', lambda: (
        [{'instance_id': 'new-instance'}] if calls else []))
    monkeypatch.setattr(profile.subprocess, 'Popen',
                        lambda args, **kw: (calls.append(args) or SimpleNamespace(pid=123)))
    monkeypatch.setattr(profile, 'profile_pids',
                        lambda directory: [123] if calls else [])
    monkeypatch.setattr('builtins.input', lambda prompt: answer)
    result = profile.browser_setup(timeout_s=2)
    assert calls[0][-1] == profile.WEB_STORE
    if answer == 'y':
        assert result['status'] == 'configured'
        cfg = yaml.safe_load(path.read_text())
        assert cfg['browser'] == 'new-instance'
        assert cfg['user_data_dir'] == str(directory) and cfg['chrome_path'] == '/fake/chrome'
    else:
        assert result['saved'] is False and path.read_text() == before


async def test_session_checks_auto_start_before_starting_bsk(sandbox, monkeypatch):
    monkeypatch.setattr(bsk, 'available', lambda: (True, ''))
    ensured = []
    monkeypatch.setattr(profile, 'ensure_browser', lambda: (ensured.append(True) or True, ''))
    command = AsyncMock(side_effect=[
        {'session_id': 'ours', 'browser_instance_id': 'pinned'}, {},
    ])
    monkeypatch.setattr(bsk, 'command', command)
    async with bsk.Session('jd'):
        assert ensured == [True]
    assert command.call_args_list[0].args[:2] == ('session', 'start')
    assert command.call_args_list[-1].args == ('session', 'stop', 'ours')


def test_cli_setup_does_not_start_daemon_or_discovery(sandbox, monkeypatch, capsys):
    from cli import main
    monkeypatch.setattr(profile, 'browser_setup', lambda: {'status': 'waiting'})
    main(['browser-setup'])
    assert 'waiting' in capsys.readouterr().out


def test_empty_browser_snapshot_does_not_call_the_five_second_waiter(monkeypatch):
    calls = []
    def sync(*args, **kwargs):
        calls.append(args)
        assert args == ('status',)
        return {'browsers': []}
    monkeypatch.setattr(bsk, '_sync', sync)
    assert profile._browsers() == []
    assert calls == [('status',)]


@pytest.mark.parametrize('raw', [
    b'/opt/google/chrome/chrome\0--user-data-dir=/tmp/owned\0--no-first-run\0',
    b'/opt/google/chrome/chrome --user-data-dir=/tmp/owned --no-first-run\0',
])
def test_native_and_rewritten_chrome_argv_identify_the_same_owned_process(monkeypatch, raw):
    args = profile._argv(raw)
    assert Path(args[0]).name == 'chrome'
    monkeypatch.setattr(profile, '_processes', lambda: iter([(123, args)]))
    assert profile.profile_pids('/tmp/owned') == [123]
    assert profile.profile_pids('/tmp/other') == []


def test_rewritten_quoted_profile_path_preserves_spaces(monkeypatch):
    args = profile._argv(b'/opt/google/chrome/chrome --user-data-dir="/tmp/owned chrome"\0')
    monkeypatch.setattr(profile, '_processes', lambda: iter([(123, args)]))
    assert profile.profile_pids('/tmp/owned chrome') == [123]


def test_launched_pid_cannot_be_claimed_without_observable_lineage(sandbox, monkeypatch):
    _, directory = sandbox
    monkeypatch.setattr(profile, 'profile_pids', lambda directory: [234])
    monkeypatch.setattr(profile, '_spawned_lineage', lambda launcher, observed: False)
    with pytest.raises(ValueError, match='lineage'):
        profile._owned_profile_pid({'user_data_dir': str(directory)}, launched_pid=123)


async def test_connected_session_checks_process_before_browser_ipc(sandbox, monkeypatch):
    monkeypatch.setattr(profile, '_browsers', lambda: [{'instance_id': 'pinned'}])
    monkeypatch.setattr(bsk, 'available', lambda: (True, ''))
    calls = AsyncMock()
    monkeypatch.setattr(bsk, 'command', calls)
    with pytest.raises(bsk.Unavailable, match='absent or ambiguous'):
        async with bsk.Session('apply'):
            pytest.fail('No session should open without Chrome argv proof')
    calls.assert_not_awaited()


def test_setup_refuses_pin_without_a_process(sandbox, monkeypatch):
    path, _directory = sandbox
    cfg = yaml.safe_load(path.read_text())
    cfg['browser'] = ''
    path.write_text(yaml.safe_dump(cfg))
    original = path.read_text()
    state = {'started': False}
    monkeypatch.setattr(profile.subprocess, 'Popen',
                        lambda *a, **kw: (state.update(started=True)
                                           or SimpleNamespace(pid=123)))
    monkeypatch.setattr(profile, '_browsers', lambda: (
        [{'instance_id': 'new-instance'}] if state['started'] else []))
    monkeypatch.setattr('builtins.input', lambda _prompt: 'y')
    outcome = profile.browser_setup(timeout_s=2)
    assert outcome['status'] == 'blocked'
    assert 'absent or ambiguous' in outcome['error']
    assert path.read_text() == original


@pytest.mark.parametrize('flags', [
    ['--user-data-dir=/tmp/owned', '--user-data-dir=/tmp/other'],
    ['--user-data-dir', '/tmp/owned', '--user-data-dir=/tmp/other'],
    ['--user-data-dir=/tmp/owned', '--user-data-dir=/tmp/owned'],
])
def test_conflicting_or_repeated_profile_flags_are_not_process_proof(monkeypatch, flags):
    monkeypatch.setattr(profile, '_processes', lambda: iter([(123, ['chrome', *flags])]))
    assert profile.profile_pids('/tmp/owned') == []


def test_setup_valid_connected_pin_is_idempotent_without_launch_or_write(
        sandbox, monkeypatch):
    path, directory = sandbox
    old = path.read_bytes()
    monkeypatch.setattr(profile, '_browsers', lambda: [{'instance_id': 'pinned'}])
    monkeypatch.setattr(profile, 'profile_pids', lambda _directory: [123])
    monkeypatch.setattr(profile, '_launch_lock',
                        lambda *a: pytest.fail('Valid rerun may not create a lock'))
    monkeypatch.setattr('builtins.input',
                        lambda *a: pytest.fail('Pinned rerun needs no new consent'))
    result = profile.browser_setup(timeout_s=2)
    assert result == {'status': 'ready', 'browser': 'pinned',
                      'user_data_dir': str(directory), 'saved': False}
    assert path.read_bytes() == old


def test_setup_unpinned_connected_candidate_can_be_human_verified(
        sandbox, monkeypatch):
    path, directory = sandbox
    cfg = yaml.safe_load(path.read_text())
    cfg['browser'] = ''
    path.write_text(yaml.safe_dump(cfg))
    old = path.read_bytes()
    monkeypatch.setattr(profile, '_browsers', lambda: [{'instance_id': 'existing'}])
    monkeypatch.setattr(profile, 'profile_pids', lambda _directory: [123])
    seen = []
    monkeypatch.setattr('builtins.input',
                        lambda prompt: (seen.append(prompt) or 'y'))
    result = profile.browser_setup(timeout_s=2)
    assert result['status'] == 'configured' and result['browser'] == 'existing'
    assert 'Verify' in seen[0]
    assert yaml.safe_load(path.read_text()) == {**cfg, 'browser': 'existing'}
    assert old != path.read_bytes()


@pytest.mark.parametrize('changed,new_value', [
    ('browser', 'competing-pin'),
    ('engine', 'chrome'),
    ('user_data_dir', '/tmp/another-synthetic-profile'),
    ('chrome_path', '/opt/another-synthetic-chrome'),
])
def test_setup_drift_during_confirmation_never_overwrites(
        sandbox, monkeypatch, changed, new_value):
    path, _directory = sandbox
    cfg = yaml.safe_load(path.read_text())
    cfg['browser'] = ''
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(profile, '_browsers',
                        lambda: [{'instance_id': 'existing'}])
    monkeypatch.setattr(profile, 'profile_pids', lambda _: [123])
    def competitor(_prompt):
        values = yaml.safe_load(path.read_text())
        values[changed] = new_value
        path.write_text(yaml.safe_dump(values))
        return 'y'
    monkeypatch.setattr('builtins.input', competitor)
    result = profile.browser_setup(timeout_s=2)
    assert result['status'] == 'blocked' and result.get('status') != 'ready'
    assert yaml.safe_load(path.read_text())[changed] == new_value
    assert yaml.safe_load(path.read_text())['browser'] == (
        new_value if changed == 'browser' else '')


def test_setup_saved_pin_rejects_foreign_candidate_without_repin(
        sandbox, monkeypatch):
    path, _ = sandbox
    initial = path.read_bytes()
    monkeypatch.setattr(profile, '_browsers',
                        lambda: [{'instance_id': 'foreign'}])
    monkeypatch.setattr(profile, 'profile_pids', lambda _: [123])
    outcome = profile.browser_setup(timeout_s=2)
    assert outcome['status'] == 'blocked' and 'saved pin' in outcome['error']
    assert path.read_bytes() == initial



@pytest.mark.parametrize("snapshot", ["connected_at_entry", "reconnecting"])
def test_setup_retained_pin_with_chrome_engine_never_reports_ready(
        sandbox, monkeypatch, snapshot):
    """A connected BSK ID is not readiness while the Claude Chrome engine is selected."""
    from contextlib import nullcontext

    path, _ = sandbox
    saved = yaml.safe_load(path.read_text())
    saved["engine"] = "chrome"
    path.write_text(yaml.safe_dump(saved))
    initial = path.read_bytes()
    snapshots = []
    def browsers():
        snapshots.append(True)
        if snapshot == "reconnecting" and len(snapshots) == 1:
            return []
        return [{"instance_id": "pinned"}]
    monkeypatch.setattr(profile, "_browsers", browsers)
    monkeypatch.setattr(profile, "profile_pids", lambda _: [123])
    # For comparison with the old reconnect path, never create a real lock.
    monkeypatch.setattr(profile, "_launch_lock", lambda _: nullcontext())
    monkeypatch.setattr(profile, "_save_instance",
                        lambda *a, **kw: pytest.fail("Wrong engine may not rewrite pin"))
    monkeypatch.setattr("builtins.input",
                        lambda *_a: pytest.fail("A saved pin needs no new consent"))
    result = profile.browser_setup(timeout_s=2)
    assert result["status"] == "blocked" and result["saved"] is False
    assert "engine: browser_skill" in result["error"]
    assert "rerun" in result["error"]
    assert path.read_bytes() == initial
    assert not snapshots  # Refuse before even consulting an unrelated backend.


def test_first_time_setup_still_allows_explicit_engine_selection_with_consent(
        sandbox, monkeypatch):
    """Do not break the unpinned, human-confirmed chrome -> BSK migration."""
    path, _ = sandbox
    cfg = yaml.safe_load(path.read_text())
    cfg["engine"], cfg["browser"] = "chrome", ""
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(profile, "_browsers", lambda: [{"instance_id": "new-synthetic"}])
    monkeypatch.setattr(profile, "profile_pids", lambda _: [123])
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    result = profile.browser_setup(timeout_s=2)
    assert result["status"] == "configured"
    updated = yaml.safe_load(path.read_text())
    assert updated["browser"] == "new-synthetic"
    assert updated["engine"] == "browser_skill"
