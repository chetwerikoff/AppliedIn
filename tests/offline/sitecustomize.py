"""Inherited Python startup guard: offline children must never open a connection.

Audit hooks sit below redis-py's constructor aliases, pools and sync/async
transports. Unlike a monkeypatch, test teardown cannot accidentally undo one.
"""

import os
import subprocess
import sys
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
