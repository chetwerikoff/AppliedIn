"""Repo-wide test isolation.

Tests exercise real pipeline functions, and those functions are wired to the
owner's live state: the activity feed, the seen-URL ledger, the memory diary.
Without this, running the suite writes fixture data into the running product —
"Engineer @ Acme" appeared 435 times in the live event feed, and an earlier
version recorded the fixtures' placeholder URL in the real seen ledger, which
then made two tests fail forever.

Nothing here is about making tests pass. It is about a test run never being
visible in, or destructive to, the thing the owner is using.
"""

from __future__ import annotations

import asyncio
import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio


# Establish the session boundary before core.config or any collected module can
# load checkout settings. Children inherit both the safe defaults and the hook.
_SESSION_LOCAL = tempfile.TemporaryDirectory(prefix="appliedin-offline-tests-")
for _key in list(os.environ):
    if _key.startswith("APPLIEDIN_"):
        del os.environ[_key]
os.environ["APPLIEDIN_OFFLINE_TEST_ROOT"] = _SESSION_LOCAL.name
os.environ["APPLIEDIN_MODE"] = "local"
os.environ["APPLIEDIN_LOCAL_DIR"] = _SESSION_LOCAL.name
os.environ["APPLIEDIN_REDIS_URL"] = "redis://offline.invalid:1/0"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
# redis-py 8's maintenance negotiation asks even a fake socket for its DNS
# address. A fake transport has no hostname to resolve (or network to consult).
fakeredis.FakeRedisConnection.get_resolved_ip = lambda self: "127.0.0.1"
_OFFLINE_STARTUP = Path(__file__).parent / "offline"
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(_OFFLINE_STARTUP), str(Path(__file__).resolve().parents[1] / "src"),
     os.environ.get("PYTHONPATH", "")]
)
runpy.run_path(str(_OFFLINE_STARTUP / "sitecustomize.py"))

# Install these guards while conftest is imported, before test collection.
# They never delegate to redis-py: a higher-scope fixture or collection-time
# import can fail visibly, but cannot acquire a real client via these routes.
# Import fakeredis first so its own classes retain their real redis-py base.
_ACTIVE_FAKE_SERVER: fakeredis.FakeServer | None = None
_REDIS_POSITIONALS = ("host", "port", "db")
_REDIS_OPTIONS = frozenset({
    "host", "port", "db", "decode_responses", "encoding", "encoding_errors",
})
_FAKE_CLIENT_OPTIONS = ("db", "decode_responses", "encoding", "encoding_errors")


def _require_fake_server() -> fakeredis.FakeServer:
    if _ACTIVE_FAKE_SERVER is None:
        raise RuntimeError("Redis constructors are disabled outside per-test fakeredis isolation")
    return _ACTIVE_FAKE_SERVER


def _client_options(args: tuple, kwargs: dict) -> dict:
    """Allow only options whose fake semantics we explicitly support."""
    if len(args) > len(_REDIS_POSITIONALS):
        raise TypeError("Unsupported test Redis positional constructor options")
    options = dict(zip(_REDIS_POSITIONALS, args))
    if options.keys() & kwargs.keys():
        raise TypeError("Duplicate test Redis constructor option")
    options.update(kwargs)
    unsupported = set(options) - _REDIS_OPTIONS
    if unsupported:
        raise TypeError(f"Unsupported test Redis constructor options: {sorted(unsupported)}")

    if "db" in options:
        db = options["db"]
        if isinstance(db, bool):
            raise TypeError("Redis db must be a non-negative integer")
        try:
            options["db"] = int(db)
        except (TypeError, ValueError) as exc:
            raise TypeError("Redis db must be a non-negative integer") from exc
        if options["db"] < 0:
            raise ValueError("Redis db must not be negative")
    if "decode_responses" in options and type(options["decode_responses"]) is not bool:
        raise TypeError("decode_responses must be a boolean")
    for key in ("encoding", "encoding_errors", "host"):
        if key in options and not isinstance(options[key], str):
            raise TypeError(f"Redis {key} must be a string")
    if "port" in options:
        if isinstance(options["port"], bool):
            raise TypeError("Redis port must be an integer")
        try:
            port = int(options["port"])
        except (TypeError, ValueError) as exc:
            raise TypeError("Redis port must be an integer") from exc
        if not 0 < port <= 65535:
            raise ValueError("Redis port is out of range")
    # host/port are intentionally transport-only and cannot select a socket.
    return {key: options[key] for key in _FAKE_CLIENT_OPTIONS if key in options}


def _url_options(url: str, kwargs: dict) -> dict:
    if not isinstance(url, str):
        raise TypeError("Redis URL must be a string")
    parsed = urlsplit(url)
    if parsed.scheme != "redis" or not parsed.hostname or parsed.fragment:
        raise ValueError("Only plain redis:// URLs are supported by test isolation")
    if parsed.username is not None or parsed.password is not None:
        raise TypeError("Authenticated Redis URLs are unsupported in offline tests")
    # Validate even though no connection can be opened.
    _ = parsed.port
    options = dict(kwargs)
    if parsed.path not in ("", "/"):
        if not parsed.path.startswith("/") or not parsed.path[1:].isdecimal():
            raise ValueError("Unsupported Redis URL database path")
        options["db"] = int(parsed.path[1:])
    seen = set()
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in seen or key not in {"db", "decode_responses", "encoding", "encoding_errors"}:
            raise TypeError(f"Unsupported Redis URL option: {key}")
        seen.add(key)
        if key == "decode_responses":
            if value.lower() not in {"true", "false", "1", "0"}:
                raise ValueError("Unsupported decode_responses URL value")
            options[key] = value.lower() in {"true", "1"}
        else:
            options[key] = value
    return _client_options((), options)


class _GuardedRedis:
    """Test-only replacement for redis.Redis and redis.Redis.from_url."""

    def __new__(cls, *args, **kwargs):
        server = _require_fake_server()
        return fakeredis.FakeRedis(server=server, **_client_options(args, kwargs))

    @classmethod
    def from_url(cls, url, **kwargs):
        server = _require_fake_server()
        return fakeredis.FakeRedis(server=server, **_url_options(url, kwargs))


def _guarded_async_from_url(url, **kwargs):
    server = _require_fake_server()
    return fakeredis.aioredis.FakeRedis(server=server, **_url_options(url, kwargs))


redis.Redis = _GuardedRedis
redis.asyncio.from_url = _guarded_async_from_url

# Capture the ORIGINAL cached callables, not module attributes that tests may
# monkeypatch. Cache eviction must happen under active fake interception.
from core import events as _events, flags as _flags  # noqa: E402

_ORIGINAL_REDIS_CACHES = (_flags._redis, _events._redis)


@pytest.fixture(autouse=True)
def _isolate_redis():
    server = fakeredis.FakeServer()
    with pytest.MonkeyPatch.context() as private:
        # This independent patch is not affected by a test's monkeypatch.undo().
        # The early constructors remain guarded before/after this active window.
        private.setattr(sys.modules[__name__], "_ACTIVE_FAKE_SERVER", server)
        for factory in _ORIGINAL_REDIS_CACHES:
            factory.cache_clear()
        try:
            yield server
        finally:
            # Owner/state finalizers explicitly depend on this fixture, so they
            # finish before either cache is cleared or fake activation ends.
            for factory in _ORIGINAL_REDIS_CACHES:
                factory.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_owner_config(_isolate_redis, tmp_path):
    """Clear owner-specific settings without sharing a reversible patch stack."""
    from core.config import get_settings
    from tools import browser_runtime

    original_settings_cache = get_settings.cache_clear
    with pytest.MonkeyPatch.context() as private:
        for key in [key for key in os.environ if key.startswith("APPLIEDIN_")
                    and key != "APPLIEDIN_OFFLINE_TEST_ROOT"]:
            private.delenv(key, raising=False)
        private.setattr(browser_runtime, "_config_path",
                        lambda settings: tmp_path / "browser.local.yaml")
        original_settings_cache()
        try:
            yield
        finally:
            original_settings_cache()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Undo a shared per-test env override only after all fixtures finish.

    A shared monkeypatch can be undone mid-test and can otherwise restore a
    stale APPLIEDIN_LOCAL_DIR at teardown. Preserve the pre-test environment.
    """
    before = os.environ.get("APPLIEDIN_LOCAL_DIR")
    try:
        return (yield)
    finally:
        if before is None:
            os.environ.pop("APPLIEDIN_LOCAL_DIR", None)
        else:
            os.environ["APPLIEDIN_LOCAL_DIR"] = before
        if item.config.getoption("redis_isolation_proof"):
            _prove_closed_session_boundary()


@pytest.fixture(autouse=True)
def _isolate_local_dir(_isolate_owner_config, tmp_path):
    """Keep any filesystem store under tmp_path, even after monkeypatch.undo()."""
    from core.config import get_settings

    original_settings_cache = get_settings.cache_clear
    with pytest.MonkeyPatch.context() as private:
        private.setenv("APPLIEDIN_LOCAL_DIR", str(tmp_path / "local"))
        original_settings_cache()
        try:
            yield
        finally:
            original_settings_cache()


@pytest.fixture(autouse=True)
def _isolate_owner_state(_isolate_local_dir, tmp_path):
    """Keep steering, memory and the seen ledger off owner paths."""
    from core import memory, steering
    from tools import seen

    with pytest.MonkeyPatch.context() as private:
        private.setattr(steering, "get_settings",
                        lambda: SimpleNamespace(local_dir=tmp_path))
        private.setattr(memory, "_path", lambda: tmp_path / "memory.md")
        private.setattr(seen, "_path", lambda: tmp_path / "seen.json")
        yield


# The proof is deliberately NOT a test_* function: conftest is not collected.
# It runs only as an opt-in fixture on the two existing, harmless test nodes.
_PROOF_FIRST = (
    "tests/test_local_dir_isolation.py::test_local_dir_is_a_per_test_temp_directory"
)
_PROOF_SECOND = (
    "tests/test_local_dir_isolation.py::test_isolation_survives_a_mid_test_monkeypatch_undo"
)
_PROOF_PREVIOUS_SERVER = None


def pytest_addoption(parser):
    parser.addoption(
        "--redis-isolation-proof", action="store_true", default=False,
        help="Run the two-node, offline Redis isolation and safe reset proof",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("redis_isolation_proof"):
        return
    _prove_closed_session_boundary()
    workers = config.getoption("numprocesses", default=0)
    if workers not in (None, 0, "0"):
        raise pytest.UsageError("Redis isolation proof requires -n 0 (one process)")
    nodes = [item.nodeid for item in items]
    if nodes != [_PROOF_FIRST, _PROOF_SECOND]:
        raise pytest.UsageError(
            "Redis isolation proof requires exactly the two ordered existing nodes: "
            f"{_PROOF_FIRST} {_PROOF_SECOND}"
        )


def _prove_closed_session_boundary():
    from core.config import Settings, get_settings

    get_settings.cache_clear()
    assert Path(Settings().local_dir).resolve() == Path(_SESSION_LOCAL.name).resolve()
    assert Settings().redis_url == "redis://offline.invalid:1/0"
    _prove_alias_connections_closed()


def _prove_alias_connections_closed():
    # These aliases intentionally bypass the convenient fake constructors. The
    # transport guard must reject them before DNS or a listening Redis is used.
    for factory in (redis.StrictRedis, redis.client.Redis, redis.from_url):
        client = (factory("redis://127.0.0.1:1/0") if factory is redis.from_url
                  else factory(host="127.0.0.1", port=1))
        with pytest.raises(RuntimeError, match="Offline test isolation"):
            client.ping()

    async def check():
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff

        # redis-py wraps async transport errors as ConnectionError; its default
        # retry backoff would only delay proving the same forbidden operation.
        client = redis.asyncio.Redis(host="127.0.0.1", port=1, retry=Retry(NoBackoff(), 0))
        try:
            with pytest.raises(redis.ConnectionError, match="Offline test isolation"):
                await client.ping()
        finally:
            await client.aclose()

    asyncio.run(check())


def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("redis_isolation_proof"):
        _prove_closed_session_boundary()


def _prove_factories_flags_events_stores(server, tmp_path):
    """Execute readbacks; a constructor spy or swallowed event error cannot pass."""
    from core import events, flags
    from core.config import get_settings
    from core.models import Status
    from core.storage.local import RedisQueue, RedisTracking
    from core.stores import make_stores

    settings = get_settings()
    assert settings.mode == "local"
    local = Path(settings.local_dir)
    assert local.resolve().is_relative_to(tmp_path.resolve())
    assert local != Path(".local")
    assert not local.is_symlink()

    raw = redis.Redis.from_url(settings.redis_url)
    direct = redis.Redis("localhost", 6379, 0, decode_responses=True)
    assert isinstance(raw, fakeredis.FakeRedis)
    assert isinstance(direct, fakeredis.FakeRedis)
    key = "redis-isolation:shared"
    assert raw.set(key, b"test-data")
    assert raw.get(key) == b"test-data"
    assert direct.get(key) == "test-data"
    assert redis.Redis.from_url(settings.redis_url, decode_responses=True).get(key) == "test-data"
    assert redis.Redis.from_url(settings.redis_url + "?decode_responses=true").get(key) == "test-data"

    async def check_async():
        client = redis.asyncio.from_url(settings.redis_url, decode_responses=True)
        try:
            assert await client.get(key) == "test-data"
            assert await client.set("redis-isolation:async", "from-async")
            assert await client.get("redis-isolation:async") == "from-async"
        finally:
            await client.aclose()

    asyncio.run(check_async())
    _prove_alias_connections_closed()
    child_source = '''
import os, sys
from pathlib import Path
import redis
from core.config import get_settings
s = get_settings()
assert Path(s.local_dir) == Path(sys.argv[1])
assert s.redis_url == "redis://offline.invalid:1/0"
if len(sys.argv) > 2:
    assert os.environ["SYNTHETIC_CHILD_STARTUP"] == "loaded"
try:
    redis.Redis(host="127.0.0.1", port=1).ping()
except RuntimeError as exc:
    assert "Offline test isolation" in str(exc)
else:
    raise AssertionError("child connected to real Redis")
'''
    (tmp_path / "sitecustomize.py").write_text(
        'import os\nos.environ["SYNTHETIC_CHILD_STARTUP"] = "loaded"\n'
    )
    for child_env in (None, {"APPLIEDIN_LOCAL_DIR": settings.local_dir,
                             "PYTHONPATH": str(tmp_path)}):
        child = subprocess.run(
            [sys.executable, "-c", child_source, settings.local_dir,
             *(["custom-startup"] if child_env is not None else [])],
            env=child_env, capture_output=True, text=True, timeout=30,
        )
        assert child.returncode == 0, child.stderr
    assert raw.get("redis-isolation:async") == b"from-async"
    assert direct.get("redis-isolation:async") == "from-async"

    with pytest.raises(TypeError, match="Unsupported"):
        redis.Redis(socket_timeout=1)
    with pytest.raises(TypeError, match="Unsupported"):
        redis.Redis.from_url(settings.redis_url + "?socket_timeout=1")
    with pytest.raises(TypeError, match="Unsupported"):
        redis.asyncio.from_url(settings.redis_url, socket_timeout=1)

    flags.set_flag("redis_isolation_proof", "ok")
    assert flags.get_flag("redis_isolation_proof") == "ok"
    assert direct.hget("appliedin:flags", "redis_isolation_proof") == "ok"
    events.emit("redis_isolation_proof", pk="test-redis-probe", detail="synthetic")
    history = events.recent(1)
    assert len(history) == 1
    assert history[0]["kind"] == "redis_isolation_proof"
    assert history[0]["pk"] == "test-redis-probe"
    assert json.loads(direct.lindex(events.HISTORY, 0))["kind"] == "redis_isolation_proof"

    stores = make_stores(settings)
    assert isinstance(stores.tracking, RedisTracking)
    assert isinstance(stores.queue, RedisQueue)
    assert stores.tracking.r is stores.queue.r
    assert isinstance(stores.tracking.r, fakeredis.FakeRedis)
    stores.tracking.set_status("test-redis-probe", Status.FOUND,
                              title="Test User @ example-co")
    assert stores.tracking.get("test-redis-probe")["title"] == "Test User @ example-co"
    assert json.loads(direct.get("app:test-redis-probe"))["status"] == Status.FOUND.value
    stores.queue.enqueue(settings.tailor_queue_url, {"pk": "test-redis-probe"})
    assert json.loads(direct.lindex(f"queue:{settings.tailor_queue_url}", 0)) == {
        "pk": "test-redis-probe"
    }

    # The reset may remove artifacts, but NEVER a symlink or an owner path.
    artifacts = local / "artifacts"
    assert artifacts.is_dir() and not artifacts.is_symlink()
    assert artifacts.resolve().is_relative_to(tmp_path.resolve())
    _prove_safe_successful_reset(settings, stores, direct, tmp_path, artifacts)
    return server


def _prove_safe_successful_reset(settings, stores, fake_client, tmp_path, artifacts):
    from core import flags
    from core.apply_queue import ApplyQueue
    from server import create_app
    from tools import browser_runtime, browser_skill, seen, submit_hold

    queued = ApplyQueue(stores.tracking.r)
    rows = stores.tracking.all()
    assert not submit_hold.any_held(tracking=stores.tracking)
    assert not queued.in_flight()
    assert all(r.get("status") != "submitting" and
               not submit_hold.blocked(r["pk"], r, tracking=stores.tracking)
               for r in rows)

    # Call the actual registered production handler, without ASGI lifespan or
    # a listening server. All process/browser and seen effects are stub-safe.
    app = create_app()
    endpoints = [
        route.endpoint for route in app.routes
        if getattr(route, "path", "") == "/actions/reset"
        and "POST" in getattr(route, "methods", set())
    ]
    assert len(endpoints) == 1
    key = "redis-isolation:reset-sentinel"
    assert fake_client.set(key, "before")
    assert fake_client.get(key) == "before"

    cleared = []
    def safe_seen_clear():
        path = seen._path()
        assert not path.is_symlink()
        assert path.resolve().is_relative_to(tmp_path.resolve())
        cleared.append(path)
        path.unlink(missing_ok=True)

    with pytest.MonkeyPatch.context() as private:
        private.setattr(browser_runtime, "kill_live_sessions", lambda kind="": 0)
        private.setattr(browser_skill, "applies_running", lambda: 0)
        private.setattr(seen, "clear", safe_seen_clear)
        result = endpoints[0]()

    assert result == {"ok": True, "status": "reset", "sessions_stopped": 0}
    assert len(cleared) == 1
    assert fake_client.get(key) is None
    assert fake_client.get("app:test-redis-probe") is None
    assert fake_client.llen(f"queue:{settings.tailor_queue_url}") == 0
    assert not fake_client.exists("redis-isolation:shared")
    assert not fake_client.exists("redis-isolation:async")
    assert not artifacts.exists()
    epoch = flags.reset_epoch()
    assert epoch.isdecimal() and int(epoch) > 0
    assert fake_client.hget("appliedin:flags", "reset_epoch") == epoch
    assert fake_client.hkeys("appliedin:flags") == ["reset_epoch"]


@pytest.fixture(autouse=True)
def _redis_isolation_proof(request, _isolate_owner_state, _isolate_redis, tmp_path):
    global _PROOF_PREVIOUS_SERVER

    if not request.config.getoption("redis_isolation_proof"):
        yield
        return

    if request.node.nodeid == _PROOF_FIRST:
        assert _PROOF_PREVIOUS_SERVER is None
        _PROOF_PREVIOUS_SERVER = _prove_factories_flags_events_stores(
            _isolate_redis, tmp_path
        )
        yield
        # The first test leaves the post-reset epoch marker in its own server.
        from core import flags
        assert flags.reset_epoch().isdecimal()
    elif request.node.nodeid == _PROOF_SECOND:
        assert _PROOF_PREVIOUS_SERVER is not None
        assert _isolate_redis is not _PROOF_PREVIOUS_SERVER
        old = fakeredis.FakeRedis(server=_PROOF_PREVIOUS_SERVER, decode_responses=True)
        assert old.hget("appliedin:flags", "reset_epoch") is not None
        current = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        assert current.dbsize() == 0
        from core import flags
        assert flags.get_flag("reset_epoch") == ""
        yield
        # The selected test calls the SHARED monkeypatch.undo() mid-test.
        # Redis and owner paths must still be private afterward.
        from core.config import get_settings
        assert Path(get_settings().local_dir).resolve().is_relative_to(tmp_path.resolve())
        assert redis.Redis().dbsize() == 0
        assert flags.get_flag("reset_epoch") == ""
        _PROOF_PREVIOUS_SERVER = None
    else:
        raise AssertionError("Redis isolation proof reached an unselected test")
