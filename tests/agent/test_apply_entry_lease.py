"""A model-invoked ADK tool never inherits the queue worker's BrowserSkill lease.

Only synthetic Test User/example-co rows and fakeredis. The real ADK tool
function is called with a fake ToolContext; browser and submission paths fail
if reached, so a Gate after SUBMITTING or Session entry cannot pass.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import fakeredis
import pytest

from agent import graph
from core.apply_queue import ApplyQueue
from core.models import Status
from core.storage.local import RedisTracking
from tools import browser_skill_apply as forms

PK = "example-co#job-1"
URL = "https://example.test/jobs/job-1"


@pytest.fixture
def world(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    tracking.set_status(PK, Status.TAILORED, company="example-co", jd_url=URL)
    stores = SimpleNamespace(
        tracking=tracking,
        answer_bank=SimpleNamespace(
            all_facts=lambda company: pytest.fail("ADK loaded private facts")),
    )
    monkeypatch.setattr("core.stores.make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr("tools.browser_runtime.configuration",
                        lambda: {"engine": "browser_skill"})
    monkeypatch.setattr("tools.browser_apply.apply",
                        lambda *a, **kw: pytest.fail("browser apply reached"))
    monkeypatch.setattr("tools.browser_runtime.apply",
                        lambda *a, **kw: pytest.fail("browser runtime reached"))
    monkeypatch.setattr("tools.browser_skill.Session",
                        lambda *a, **kw: pytest.fail("BrowserSkill Session/IPC reached"))
    monkeypatch.setattr("core.rotation.ensure",
                        lambda *a, **kw: pytest.fail("profile rotation reached"))
    monkeypatch.setattr("core.profiles.resolve_for",
                        lambda *a, **kw: pytest.fail("profile selection reached"))
    monkeypatch.setattr("tools.credentials.get_login",
                        lambda *a, **kw: pytest.fail("credentials accessed"))
    return stores, ApplyQueue(client)


async def _refused_without_effect(world, monkeypatch):
    stores, queue = world
    row_before = deepcopy(stores.tracking.get(PK))
    pending_before = deepcopy(queue.pending())
    in_flight_before = queue.in_flight()
    writes = []
    original_set = stores.tracking.set_status

    def forbidden_status(*args, **kwargs):
        writes.append((args, kwargs))
        pytest.fail("unleased ADK mutated tracking before refusal")

    monkeypatch.setattr(stores.tracking, "set_status", forbidden_status)
    try:
        result = await graph.apply_to_job(SimpleNamespace(state={
            "pk": PK, "company": "example-co", "jd_url": URL,
            "person": "Test User", "jd_text": "Synthetic role",
        }))
    finally:
        monkeypatch.setattr(stores.tracking, "set_status", original_set)
    assert result["status"] == "gate"
    assert result["reason"] == "leased_dispatch_required"
    assert "ApplyQueue.next -> run_queued -> _apply_direct" in result["question"]
    assert "explicit" in result["question"].lower()
    assert "no application was queued or submitted" in result["detail"].lower()
    assert writes == []
    assert stores.tracking.get(PK) == row_before
    assert queue.pending() == pending_before
    assert queue.in_flight() == in_flight_before


@pytest.mark.asyncio
async def test_no_lease_and_no_inflight_pk_adk_cannot_enter_browser(world, monkeypatch):
    """No queue item, no lease and no pk membership: zero status/Session/IPC."""
    stores, queue = world
    assert not queue.in_flight()
    assert not queue.pending()
    assert not stores.tracking.r.smembers("applyq:inflight")
    await _refused_without_effect(world, monkeypatch)


@pytest.mark.asyncio
async def test_same_pk_real_queue_worker_lease_cannot_be_borrowed(world, monkeypatch, tmp_path):
    """Even the exact in-flight pk is the worker's, never the ADK caller's."""
    stores, queue = world
    assert queue.put(PK, "example-co")
    item = queue.next(only="example-co")
    assert item and item["pk"] == PK
    try:
        assert queue.in_flight() == {PK}
        assert stores.tracking.r.smembers("applyq:inflight") == {"example-co"}
        await _refused_without_effect(world, monkeypatch)
        # The rejection cannot drop/reassign the worker's company lease.
        assert queue.next(only="example-co") is None
        assert queue.in_flight() == {PK}
        assert stores.tracking.r.smembers("applyq:inflight") == {"example-co"}

        # The real queue-owned path can still pass existing production
        # duplicate/status/hold/seed/PDF dispatch checks.
        pdf = tmp_path / "Test-User.pdf"
        pdf.write_bytes(b"%PDF-1.4 synthetic offline document")
        stores.tracking.set_status(
            PK, Status.SUBMITTING, resume_tex_key="resumes/synthetic.tex",
            resume_seed="synthetic-seed", gate_reason="")
        monkeypatch.setattr("agent.run.seed_fingerprint", lambda: "synthetic-seed")
        monkeypatch.setattr("tools.browser_apply._duplicate_refusal", lambda pk: None)
        forms.check_dispatch(PK, str(pdf))
    finally:
        queue.done(item)
    assert not queue.in_flight()
    assert not stores.tracking.r.smembers("applyq:inflight")


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_state", [
    "pk_only", "company_only", "stale_submitting", "unreadable_store",
])
async def test_unverifiable_or_stale_dispatch_does_not_enable_adk(
        world, monkeypatch, lease_state):
    """Inconsistent or unreadable witness never upgrades direct ADK authority."""
    stores, queue = world
    if lease_state == "pk_only":
        queue.r.sadd("applyq:inflight:pks", PK)
    elif lease_state == "company_only":
        queue.r.sadd("applyq:inflight", "example-co")
    elif lease_state == "stale_submitting":
        stores.tracking.set_status(PK, Status.SUBMITTING)
    else:
        monkeypatch.setattr(
            "core.stores.make_stores",
            lambda *a, **kw: pytest.fail("unleased ADK consulted unreadable store"))
    await _refused_without_effect(world, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [None, "unsupported", "configuration_error"])
async def test_unreadable_or_unknown_engine_fails_closed_before_effects(
        world, monkeypatch, engine):
    if engine == "configuration_error":
        def broken():
            raise OSError("Synthetic configuration unavailable")
        monkeypatch.setattr("tools.browser_runtime.configuration", broken)
    else:
        monkeypatch.setattr("tools.browser_runtime.configuration",
                            lambda: {"engine": engine})
    stores, queue = world
    before = deepcopy(stores.tracking.get(PK))
    result = await graph.apply_to_job(SimpleNamespace(state={
        "pk": PK, "company": "example-co", "jd_url": URL,
    }))
    assert result["status"] == "gate"
    assert result["reason"] == "browser_engine_unverified"
    assert "explicitly approving" in result["question"]
    assert stores.tracking.get(PK) == before
    assert not queue.pending() and not queue.in_flight()


@pytest.mark.asyncio
async def test_existing_chrome_held_route_retains_uncertain_refusal(world, monkeypatch):
    """The original engine still uses the existing durable uncertainty guard."""
    stores, queue = world
    monkeypatch.setattr("tools.browser_runtime.configuration",
                        lambda: {"engine": "chrome"})
    from tools import submit_hold
    submit_hold.mark(PK, tracking=stores.tracking)
    before = deepcopy(stores.tracking.get(PK))
    result = await graph.apply_to_job(SimpleNamespace(state={
        "pk": PK, "company": "example-co", "jd_url": URL,
    }))
    assert result["status"] == "uncertain"
    assert stores.tracking.get(PK) == before
    assert not queue.pending() and not queue.in_flight()
