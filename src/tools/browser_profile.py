"""Dedicated Chrome lifecycle. Never reuse the owner's default Chrome directory.

The BrowserSkill instance is pinned by human confirmation, not inferred from a
Chrome process. Process arguments prove only ownership of the directory we launch.
"""
from __future__ import annotations

import fcntl
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

WEB_STORE = 'https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi'
DASHBOARD = 'http://127.0.0.1:8788/'
_STATUS_CACHE: tuple = ()
_STATUS_LOCK = threading.Lock()


def validate_directory(directory: Path, settings) -> None:
    roots = [Path.home() / '.config/google-chrome', Path.home() / '.config/chromium',
             Path(__file__).resolve().parents[2]]
    if directory == Path.home() or directory == Path('/'):
        raise ValueError('Use a dedicated Chrome directory, not the home or root directory')
    for root in roots:
        root = root.resolve()
        if directory.is_relative_to(root) or root.is_relative_to(directory):
            raise ValueError('Chrome user_data_dir must be outside the repository and main profile')


def _config(settings=None) -> dict:
    from tools.browser_runtime import configuration
    return configuration(settings)


def _browsers() -> list[dict]:
    from tools.browser_skill import Unavailable, _sync
    # The CLI has a five-second connection grace period when no browser exists.
    # Allow that grace, but avoid a second waiter for an already-empty snapshot.
    if not _sync('status', timeout=7).get('browsers'):
        return []
    try:
        return _sync('browsers', timeout=7).get('browsers', [])
    except Unavailable:
        return []  # The last extension may disconnect between the two reads.


def _connected(rows: list[dict], instance: str) -> bool:
    return bool(instance) and any(
        b.get('instance_id') == instance and not b.get('unresponsive')
        and not b.get('version_skew') for b in rows)


def _ids(rows: list[dict]) -> set[str]:
    return {b['instance_id'] for b in rows if isinstance(b.get('instance_id'), str)}


def _argv(raw: bytes) -> list[str]:
    args = raw.decode(errors='replace').rstrip('\0').split('\0')
    # Linux Chrome rewrites its process title into one argv entry. Without this,
    # a live owned profile looked absent and could be launched a second time.
    if len(args) == 1:
        try:
            return shlex.split(args[0])
        except ValueError:
            return []
    return args


def _processes():
    # Read argv as NUL-delimited bytes: ps text loses quoting for directories
    # with spaces. Never log argv; a user's launch URL may contain an OAuth code.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = _argv((entry / 'cmdline').read_bytes())
        except OSError:
            continue
        if not args or Path(args[0]).name not in {
                'chrome', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'}:
            continue
        if any(a.startswith('--type=') for a in args):
            continue
        yield int(entry.name), args


def profile_pids(directory: str) -> list[int]:
    target = Path(directory).expanduser().resolve()
    result = []
    for pid, args in _processes():
        for i, arg in enumerate(args):
            value = arg.partition('=')[2] if arg.startswith('--user-data-dir=') else (
                args[i + 1] if arg == '--user-data-dir' and i + 1 < len(args) else '')
            if value and Path(value).expanduser().resolve() == target:
                result.append(pid)
                break
    return result


def _chrome(cfg: dict) -> str:
    selected = cfg['chrome_path']
    found = shutil.which(selected) if selected else (
        shutil.which('google-chrome-stable') or shutil.which('google-chrome'))
    if not found:
        raise ValueError('Chrome executable not found; configure chrome_path in browser.local.yaml')
    return found


@contextmanager
def _launch_lock(directory: str):
    profile = Path(directory)
    profile.parent.mkdir(parents=True, exist_ok=True)
    # Lock beside the profile, so Chrome's own SingletonLock remains untouched.
    with (profile.parent / ('.' + profile.name + '.appliedin-launch.lock')).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _launch(cfg: dict, url: str) -> None:
    Path(cfg['user_data_dir']).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [_chrome(cfg), '--user-data-dir=' + cfg['user_data_dir'],
         '--no-first-run', '--no-default-browser-check', url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def can_start(settings=None) -> tuple[bool, str]:
    """Preflight may accept an offline browser that will start at session creation."""
    try:
        cfg = _config(settings)
        if not cfg['browser']:
            return False, 'BrowserSkill unavailable: run ./appliedin browser-setup first.'
        rows = _browsers()
        if _connected(rows, cfg['browser']):
            return True, ''
        if profile_pids(cfg['user_data_dir']):
            return False, ('BrowserSkill unavailable: profile is running, but BrowserSkill '
                           'is not connected.')
        _chrome(cfg)
        return True, ''
    except (OSError, ValueError, RuntimeError) as exc:
        return False, 'BrowserSkill unavailable: ' + str(exc)


def ensure_browser(settings=None) -> tuple[bool, str]:
    from tools.browser_skill import ensure_daemon
    try:
        cfg = _config(settings)
        if not cfg['browser']:
            return False, 'BrowserSkill unavailable: run ./appliedin browser-setup first.'
        ready, problem = ensure_daemon()
        if not ready:
            return False, problem
        rows = _browsers()
        if _connected(rows, cfg['browser']):
            return True, ''
        with _launch_lock(cfg['user_data_dir']):
            rows = _browsers()
            if _connected(rows, cfg['browser']):
                return True, ''
            if profile_pids(cfg['user_data_dir']):
                return False, ('BrowserSkill unavailable: profile is running, but BrowserSkill '
                               'is not connected. No second Chrome was launched.')
            before = _ids(rows)
            _launch(cfg, DASHBOARD)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                rows = _browsers()
                if _connected(rows, cfg['browser']):
                    return True, ''
                if _ids(rows) - before - {cfg['browser']}:
                    return False, ('BrowserSkill unavailable: a different instance connected; '
                                   'the pinned instance was not used. Run browser-setup.')
                time.sleep(1)
            return False, ('BrowserSkill unavailable: dedicated Chrome started, but the pinned '
                           'BrowserSkill instance did not connect within 45 seconds.')
    except (OSError, ValueError, RuntimeError) as exc:
        return False, 'BrowserSkill unavailable: ' + str(exc)


def connection_status(settings=None) -> dict:
    """Read-only dashboard diagnostics: this never launches a browser."""
    global _STATUS_CACHE
    try:
        cfg = _config(settings)
        key = (cfg['browser'], cfg['user_data_dir'], cfg['chrome_path'], os.getenv('BSK_HOME'))
        with _STATUS_LOCK:
            if _STATUS_CACHE and _STATUS_CACHE[0] == key and _STATUS_CACHE[1] > time.monotonic():
                return dict(_STATUS_CACHE[2])
            if not cfg['browser']:
                state = 'setup_required'
                detail = 'Run ./appliedin browser-setup for the clean profile.'
            elif _connected(_browsers(), cfg['browser']):
                state, detail = 'connected', f'BrowserSkill connected to profile {cfg["browser"]}.'
            elif profile_pids(cfg['user_data_dir']):
                state = 'extension_missing'
                detail = ('Dedicated Chrome is running, but BrowserSkill is not connected. '
                          'Check the extension in this profile.')
            else:
                _chrome(cfg)
                state, detail = 'not_running', ('Dedicated Chrome is not running; it will start '
                                               'automatically for the next browser task.')
            result = {'state': state, 'detail': detail}
            _STATUS_CACHE = (key, time.monotonic() + 10, result)
            return dict(result)
    except (OSError, ValueError, RuntimeError) as exc:
        return {'state': 'unavailable', 'detail': str(exc)}


def _save_instance(instance: str, directory: str, settings=None) -> None:
    from core.config import get_settings
    from tools.browser_runtime import _config_path
    settings = settings or get_settings()
    path = _config_path(settings)
    # Re-read immediately before publication: a long interactive setup must not
    # overwrite unrelated edits made while the human was installing the extension.
    if _config(settings)['user_data_dir'] != directory:
        raise ValueError('user_data_dir changed during setup; instance was not saved')
    values = yaml.safe_load(path.read_text()) if path.exists() else {}
    values = values or {}
    values.update(engine='browser_skill', browser=instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.browser-setup-',
                                     delete=False) as handle:
        temporary = Path(handle.name)
        yaml.safe_dump(values, handle, sort_keys=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def browser_setup(settings=None, *, timeout_s: int = 900) -> dict:
    """The owner installs/signs in and confirms the new extension instance."""
    from tools.browser_skill import ensure_daemon
    cfg = _config(settings)
    ready, problem = ensure_daemon()
    if not ready:
        return {'status': 'blocked', 'error': problem}
    before = _ids(_browsers())
    with _launch_lock(cfg['user_data_dir']):
        if not profile_pids(cfg['user_data_dir']):
            _launch(cfg, WEB_STORE)
        print('Dedicated Chrome profile: ' + cfg['user_data_dir'], flush=True)
        print('Install BrowserSkill manually in the NEW profile; enable Allow access to file URLs. '
              'Sign in to employer portals yourself. No installation/login is automated.',
              flush=True)
        print('Waiting for a NEW instance ID. Verify it in the extension popup.', flush=True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rows = _browsers()
            new = _ids(rows) - before
            if len(new) > 1:
                return {'status': 'blocked',
                        'error': 'Multiple new instances; verify the dedicated profile.'}
            if new:
                instance = next(iter(new))
                if not _connected(rows, instance):
                    time.sleep(1)
                    continue
                print('New instance ID: ' + instance, flush=True)
                answer = input('Confirm the dedicated profile ID [y/N]: ').strip().lower()
                if answer != 'y':
                    return {'status': 'not confirmed', 'saved': False}
                if not _connected(_browsers(), instance):
                    return {'status': 'blocked',
                            'error': 'Confirmed instance disconnected before saving.'}
                _save_instance(instance, cfg['user_data_dir'], settings)
                return {'status': 'configured', 'browser': instance,
                        'user_data_dir': cfg['user_data_dir']}
            time.sleep(1)
    return {'status': 'waiting timed out', 'saved': False,
            'hint': 'Browser remains open. Complete installation and reconnect for setup.'}
