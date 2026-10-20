"""Who may take work off the apply queue.

The tailor now queues every finished job, which is the point of having a queue.
That makes this the load-bearing check: if the worker drained the queue in gated
mode, tailoring a job would apply to it, and the rule that an application only
goes out when the owner says so would be gone — silently, with no code that looks
like it removed a gate.
"""

import pytest

from daemon import auto_dispatch_allowed


def test_gated_never_dispatches_by_itself():
    """The queue is a waiting room; Process empties it, per company."""
    assert not auto_dispatch_allowed("gated")


def test_auto_dispatches():
    assert auto_dispatch_allowed("auto")


def test_assisted_does_not():
    """Those are finished by hand in the owner's own browser."""
    assert not auto_dispatch_allowed("assisted")


@pytest.mark.parametrize("mode", ["", "GATED", "nonsense", None])
def test_anything_unrecognised_refuses(mode):
    """Fail closed. A typo in a flag must not start submitting applications."""
    assert not auto_dispatch_allowed(mode)


def test_recovery_is_not_gated_behind_dispatch():
    """Orphan recovery must run in EVERY mode.

    The loop used to reclaim orphans only on the idle path, after the dispatch
    checks. Adding the gated guard above it stranded every orphan in gated mode: a
    job whose worker was killed stayed leased, left the queue, and nothing put it
    back — which reads as a company quietly disappearing from the queue.

    The mode check now lives inside `next_dispatchable`, so that is what recovery
    has to come before. The guarantee is unchanged: nothing about which jobs this
    mode may start can decide whether an orphan gets put back.

    This pins the ORDER by reading the source, because the behaviour lives in a
    `while True` that cannot be called directly.
    """
    import inspect

    import daemon

    src = inspect.getsource(daemon._apply_loop)
    reclaim = src.index("_reclaim_orphans(stores, q)")
    gate = src.index("next_dispatchable(q, flags.apply_mode())")
    assert reclaim < gate, "recovery must run before the dispatch mode check"
    assert "auto_dispatch_allowed" not in src, \
        "the mode check belongs in next_dispatchable, where gated mode can still " \
        "let an answered question through"

def _posting_review_surface(monkeypatch):
    """Call local route closures directly: no HTTP, browser, Redis server or worker."""
    from types import SimpleNamespace

    import fakeredis
    import server
    from core.models import JobRecord, Status
    from core.storage.local import RedisTracking
    from tools import posting_guard as pg

    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    body = "Build useful synthetic systems for distributed teams. " * 20
    versions = {}
    for number in (1, 2):
        ident = f"job-{number}"
        pk = f"example-co#{ident}"
        guarded = pg.analyze(body, source_required=True)
        assert tracking.put_new(JobRecord(
            company="example-co", job_id=ident, title="Test role",
            jd_url=f"https://jobs.ashbyhq.com/example-co/{ident}",
            jd_text=guarded["text"], posting_guard=guarded["posting_guard"]))
        tracking.set_status(pk, Status.TAILORED, gate_reason="approval")
        versions[pk] = tracking.get_current(pk)["posting_guard"]["generation"]

    monkeypatch.setattr(server, "get_settings", lambda: SimpleNamespace(
        mode="local", config_dir="/synthetic", local_dir="/synthetic"))
    monkeypatch.setattr(server, "make_stores", lambda *args: SimpleNamespace(tracking=tracking))

    from core import apply_queue

    class FakeQueue:
        started = []
        inserted = []
        running = []

        def __init__(self, client):
            assert client is tracking.r

        def depth(self):
            return {"running": list(self.running)}

        def start_flush(self, company):
            self.started.append(company)
            return True

        def stop_flush(self, company):
            self.started.remove(company)

        def put(self, pk, company):
            self.inserted.append((pk, company))

    monkeypatch.setattr(apply_queue, "ApplyQueue", FakeQueue)
    app = server.create_app()
    routes = {item.path: item.endpoint for item in app.routes
              if getattr(item, "path", "").startswith("/actions/")}
    return SimpleNamespace(routes=routes, tracking=tracking,
                           versions=versions, queue=FakeQueue)


def test_batch_review_is_all_preflighted_before_grant_and_queue(monkeypatch):
    from fastapi import BackgroundTasks

    env = _posting_review_surface(monkeypatch)
    pks = list(env.versions)
    valid_first_invalid_second = {
        pks[0]: env.versions[pks[0]], pks[1]: "superseded-generation"}
    background = BackgroundTasks()
    result = env.routes["/actions/apply-selection"]({
        "company": "example-co", "pks": pks,
        "guard_generations": valid_first_invalid_second,
    }, background)
    assert result["ok"] is False
    assert not env.queue.started and not env.queue.inserted
    assert background.tasks == []
    assert all(not env.tracking.get_current(pk).get("reviewed_guard_generation")
               for pk in pks)

    completed = BackgroundTasks()
    result = env.routes["/actions/apply-selection"]({
        "company": "example-co", "pks": pks,
        "guard_generations": env.versions,
    }, completed)
    assert result["ok"] and result["queued"] == 2
    assert env.queue.inserted == [(pk, "example-co") for pk in pks]
    assert len(completed.tasks) == 1  # not executed in an offline endpoint test
    for pk in pks:
        row = env.tracking.get_current(pk)
        assert row["reviewed_guard_generation"] == env.versions[pk]
        assert row["status"] == "tailored"


def test_busy_company_does_not_persist_displayed_review_grant(monkeypatch):
    from fastapi import BackgroundTasks

    env = _posting_review_surface(monkeypatch)
    env.queue.running = ["example-co"]
    pk = next(iter(env.versions))
    response = env.routes["/actions/apply-selection"]({
        "company": "example-co", "pks": [pk],
        "guard_generations": {pk: env.versions[pk]},
    }, BackgroundTasks())
    assert response["ok"] is False
    assert not env.tracking.get_current(pk).get("reviewed_guard_generation")
    assert not env.queue.started and not env.queue.inserted


@pytest.mark.parametrize("supplied", [None, "", "old-review-token"])
def test_single_apply_now_requires_visible_exact_generation(monkeypatch, supplied):
    from fastapi import BackgroundTasks

    env = _posting_review_surface(monkeypatch)
    pk = next(iter(env.versions))
    response = env.routes["/actions/apply-now/{pk}"](
        pk, BackgroundTasks(), {"posting_guard_generation": supplied})
    assert response["ok"] is False
    assert "posting" in response["error"].lower()
    assert not env.tracking.get_current(pk).get("reviewed_guard_generation")
    assert not env.queue.inserted
