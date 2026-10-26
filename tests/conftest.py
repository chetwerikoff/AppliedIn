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

import pytest


@pytest.fixture(autouse=True)
def _isolate_owner_config(monkeypatch, tmp_path):
    """Tests must describe the SHIPPED defaults, not this machine's .env.

    Settings reads APPLIEDIN_* from the environment, so a developer who has
    pointed a stage at a different model, or raised their daily cap, silently
    changes what the suite asserts — four tests were failing for exactly that
    reason, describing config nobody else would ever see.
    """
    import os

    for key in [k for k in os.environ if k.startswith("APPLIEDIN_")]:
        monkeypatch.delenv(key, raising=False)
    # get_settings() is cached, so a stale instance would outlive the cleanup.
    try:
        from core.config import get_settings

        get_settings.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    # A private browser profile must never cause offline tests to drive Chrome.
    from tools import browser_runtime
    monkeypatch.setattr(browser_runtime, "_config_path",
                        lambda settings: tmp_path / "browser.local.yaml")
    yield
    try:
        from core.config import get_settings

        get_settings.cache_clear()
    except Exception:  # noqa: BLE001
        pass


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Leave ``APPLIEDIN_LOCAL_DIR`` as the run found it once a test is fully torn down.

    A test may set that variable through the shared ``monkeypatch`` after
    ``_isolate_local_dir`` has set it; the shared fixture is torn down after this
    one and would restore the isolation value it saw, leaving a stale temporary
    path in ``os.environ`` for the next test to inherit.
    """
    import os

    before = os.environ.get("APPLIEDIN_LOCAL_DIR")
    try:
        return (yield)
    finally:
        if before is None:
            os.environ.pop("APPLIEDIN_LOCAL_DIR", None)
        else:
            os.environ["APPLIEDIN_LOCAL_DIR"] = before


@pytest.fixture(autouse=True)
def _isolate_local_dir(_isolate_owner_config, tmp_path):
    """Point ``Settings.local_dir`` at a per-test temporary directory.

    ``local_dir`` defaults to ``.local`` relative to the current directory, so any
    offline test that reaches ``core.stores.make_stores()`` used to create
    ``.local/facts.md``, ``secrets.json`` and ``artifacts/`` in the checkout.
    In a disposable task worktree those empty copies were later refused as owner
    data. The directory is not created here; only code that writes it does.

    The patch uses its own ``MonkeyPatch`` rather than the shared ``monkeypatch``
    fixture: a test that calls ``monkeypatch.undo()`` mid-test must not lose the
    isolation for the rest of that test. A test that sets ``APPLIEDIN_LOCAL_DIR``
    itself, or builds ``Settings(local_dir=...)``, still wins.
    """

    def _clear_settings_cache():
        try:
            from core.config import get_settings

            get_settings.cache_clear()
        except Exception:  # noqa: BLE001 — a test may have replaced get_settings
            pass

    with pytest.MonkeyPatch.context() as private:
        private.setenv("APPLIEDIN_LOCAL_DIR", str(tmp_path / "local"))
        _clear_settings_cache()
        yield
    _clear_settings_cache()


@pytest.fixture(autouse=True)
def _isolate_owner_state(tmp_path, monkeypatch):
    # 1) Activity feed — emit() publishes to Redis and appends to the history the
    #    dashboard renders. Point it at an in-memory server instead.
    try:
        import fakeredis
        from core import events

        fake = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(events, "_redis", lambda: fake)
    except Exception:  # noqa: BLE001 — never block a run on the isolation itself
        pass

    from types import SimpleNamespace

    from core import steering

    monkeypatch.setattr(steering, "get_settings", lambda: SimpleNamespace(local_dir=tmp_path))

    # 2) Memory diary — a durable markdown log of real outcomes.
    try:
        from core import memory

        monkeypatch.setattr(memory, "_path", lambda: tmp_path / "memory.md")
    except Exception:  # noqa: BLE001
        pass

    # 3) Seen-URL ledger — discovery marks every enqueued URL here, and a fixture
    #    URL written into it silently filters real jobs on later runs.
    try:
        from tools import seen

        monkeypatch.setattr(seen, "_path", lambda: tmp_path / "seen.json")
    except Exception:  # noqa: BLE001
        pass

    yield
