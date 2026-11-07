from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.adk.sessions import DatabaseSessionService

from agent import run as agent_run
from core import preparation


def test_progress_counts_completed_work_separately_from_ready_resumes(monkeypatch):
    monkeypatch.setattr(preparation, "_BATCHES", {})
    with preparation.track([{"company": "Acme"}] * 3) as batch:
        batch.complete("tailored")
        batch.complete("skipped")
        batch.complete("error")
        state = preparation.snapshot()[0]
        counts = tuple(state[k] for k in ("completed", "prepared", "skipped", "failed"))
        assert counts == (3, 1, 1, 1)
    assert preparation.snapshot()[0]["stage"] == "Finished"


def test_interrupted_preparation_never_claims_all_jobs_finished(monkeypatch):
    monkeypatch.setattr(preparation, "_BATCHES", {})
    with preparation.track([{}, {}]) as batch:
        batch.complete("tailored")
    state = preparation.snapshot()[0]
    assert not state["active"]
    assert state["stage"] == "Stopped"
    assert state["completed"] == 1


@pytest.fixture
def sqlite_session_db(monkeypatch, tmp_path):
    """The real ADK session service must touch only this disposable SQLite file."""
    db_file = tmp_path / "synthetic-sessions.db"
    monkeypatch.setattr(
        agent_run, "get_settings",
        lambda: SimpleNamespace(
            local_dir=str(tmp_path), session_db_url=f"sqlite:///{db_file}",
        ),
    )
    return f"sqlite+aiosqlite:///{db_file}"


@pytest.mark.asyncio
async def test_sqlite_session_service_configures_every_pooled_connection(sqlite_session_db):
    # The original factory passed db_url only, leaving SQLite's five-second
    # wait and DELETE journal mode; both settings must be proven on real DBAPI
    # connections, not just captured in a mocked constructor.
    with patch("google.adk.sessions.DatabaseSessionService", wraps=DatabaseSessionService) as ctor:
        service = agent_run._session_service()
    assert ctor.call_args.kwargs == {
        "db_url": sqlite_session_db, "connect_args": {"timeout": 30},
    }
    try:
        async with service.db_engine.connect() as first:
            async with service.db_engine.connect() as second:
                for connection in (first, second):
                    timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
                    mode = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
                    assert timeout == 30000
                    assert mode == "wal"
    finally:
        await service.db_engine.dispose()


@pytest.mark.asyncio
async def test_parallel_adk_session_creation_uses_one_disposable_sqlite_file(sqlite_session_db):
    # ADK checks the schema per service, even when all tables already exist.
    # Bootstrap once to isolate concurrent checks/writes from first-time DDL.
    bootstrap = agent_run._session_service()
    try:
        await bootstrap.create_session(
            app_name="synthetic", user_id="setup", session_id="setup",
        )
    finally:
        await bootstrap.db_engine.dispose()

    services = [agent_run._session_service() for _ in range(8)]
    try:
        sessions = await asyncio.wait_for(
            asyncio.gather(*(
                service.create_session(
                    app_name="synthetic", user_id=f"test-user-{i}",
                    session_id=f"session-{i}",
                )
                for i, service in enumerate(services)
            )),
            timeout=45,
        )
        assert {session.id for session in sessions} == {
            f"session-{i}" for i in range(len(services))
        }
        assert len({id(service.db_engine) for service in services}) == len(services)
    finally:
        await asyncio.gather(*(service.db_engine.dispose() for service in services))


def test_non_sqlite_session_service_keeps_its_original_construction(monkeypatch, tmp_path):
    """A cloud URL gets neither SQLite connect args nor SQLite event hooks."""
    url = "postgresql+asyncpg://example.invalid/synthetic"
    monkeypatch.setattr(
        agent_run, "get_settings",
        lambda: SimpleNamespace(local_dir=str(tmp_path), session_db_url=url),
    )
    calls = []
    sentinel = object()

    def fake_service(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr("google.adk.sessions.DatabaseSessionService", fake_service)
    assert agent_run._session_service() is sentinel
    assert calls == [{"db_url": url}]