"""Inherited Python startup guard: offline children must never open a connection.

Audit hooks sit below redis-py's constructor aliases, pools and sync/async
transports. Unlike a monkeypatch, test teardown cannot accidentally undo one.
"""

import os
import runpy
import subprocess
import sys
from importlib.machinery import PathFinder
from pathlib import Path


def _offline_connection_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Offline test isolation forbids real network connections")


sys.addaudithook(_offline_connection_guard)

# Some offline harnesses intentionally construct a minimal child environment.
# Carry the boundary through that seam too, rather than relying on env.copy().
_popen_init = subprocess.Popen.__init__
_startup = str(Path(__file__).resolve().parent)
_root = os.environ["APPLIEDIN_OFFLINE_TEST_ROOT"]
# Fixture entrypoints sanitize os.environ before importing product modules.
# Keep the synthetic default available without requiring them to retain secrets.
sys._appliedin_offline_test_root = _root


def _offline_popen_init(self, *args, **kwargs):
    positional = list(args)
    env = positional[10] if len(positional) > 10 else kwargs.get("env")
    env = dict(os.environ if env is None else env)
    env["APPLIEDIN_OFFLINE_TEST_ROOT"] = _root
    env.setdefault("APPLIEDIN_LOCAL_DIR", _root)
    env.setdefault("APPLIEDIN_REDIS_URL", "redis://offline.invalid:1/0")
    env["PYTHONPATH"] = os.pathsep.join([_startup, env.get("PYTHONPATH", "")])
    if len(positional) > 10:
        positional[10] = env
    else:
        kwargs["env"] = env
    _popen_init(self, *positional, **kwargs)


subprocess.Popen.__init__ = _offline_popen_init

# Synthetic subprocess harnesses install their own sitecustomize to stub the
# daemon before import. Preserve that hook: shadowing it would run real startup.
_other_paths = [p for p in sys.path if str(Path(p).resolve()) != _startup]
_next_startup = PathFinder.find_spec("sitecustomize", _other_paths)
if _next_startup is not None and _next_startup.origin:
    runpy.run_path(_next_startup.origin)
