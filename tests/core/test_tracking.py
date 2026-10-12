from core.models import JobRecord, Status
from core.storage.tracking import TrackingStore

from .conftest import make_applications_table


def _job(job_id="1", jd_text="x"):
    return JobRecord(
        company="Acme", job_id=job_id, title="SWE", jd_url="u",
        jd_text=jd_text, location="R", ats="greenhouse",
    )


def test_put_new_dedups(aws):
    make_applications_table()
    store = TrackingStore("applications")
    job = _job()
    assert store.put_new(job) is True
    assert store.put_new(job) is False


def test_find_by_jd_hash(aws):
    make_applications_table()
    store = TrackingStore("applications")
    job = _job(job_id="1", jd_text="build the thing")
    store.put_new(job)
    assert store.find_by_jd_hash(job.jd_hash) == "acme#1"
    assert store.find_by_jd_hash("deadbeef") is None


def test_set_and_query_status(aws):
    make_applications_table()
    store = TrackingStore("applications")
    store.put_new(_job("1"))
    store.set_status("acme#1", Status.CAPPED)
    rows = store.query_status(Status.CAPPED)
    assert [r["pk"] for r in rows] == ["acme#1"]


def test_daily_cap_atomic(aws):
    make_applications_table()
    store = TrackingStore("applications")
    assert all(store.try_increment_daily_cap("2026-07-16", cap=2) for _ in range(2))
    assert store.try_increment_daily_cap("2026-07-16", cap=2) is False
    # a different day has its own counter
    assert store.try_increment_daily_cap("2026-07-17", cap=2) is True


def _synthetic_job(job_id="job-1"):
    return JobRecord(company="example-co", job_id=job_id, title="Test role",
                     jd_url=f"https://example.test/jobs/{job_id}", jd_text="",
                     location="Test location", ats="test")


def test_dynamo_reader_witness_exact_presence_and_aba(aws):
    make_applications_table()
    store = TrackingStore("applications")
    store.put_new(_synthetic_job())
    pk = "example-co#job-1"
    before = store.get(pk)
    # Legacy attributes are absent; this does not mean they equal zero/null.
    assert store.update_if_status(pk, Status.FOUND,
                                  {"status": Status.TAILORING, "jd_read_revision": 1},
                                  expected_reader={"jd_text": ""})
    current = store.get(pk)
    assert current["status"] == "tailoring"
    assert current["jd_read_revision"] == 1
    assert current["attempts"] == before["attempts"]
    assert [x["pk"] for x in store.query_status(Status.TAILORING)] == [pk]
    assert not store.update_if_status(pk, Status.FOUND, {"status": "failed"},
                                      expected_reader={"jd_text": ""})
    assert not store.update_if_status(pk, Status.TAILORING, {"status": "failed"},
                                      expected_reader={"jd_text": ""})
    assert store.get(pk) == current

    store.set_status(pk, Status.TAILORING, note="synthetic")
    assert store.update_if_status(pk, Status.TAILORING,
                                  {"status": Status.FOUND, "jd_read_revision": 2,
                                   "jd_read_attempts": 0, "jd_read_retry_at": None,
                                   "jd_read_prepare_only": False},
                                  expected_reader={"jd_text": "", "jd_read_revision": 1})
    newer = store.get(pk)
    assert newer["note"] == "synthetic"
    assert not store.update_if_status(pk, Status.FOUND, {"status": "job_gone"},
                                      expected_reader={"jd_text": "", "jd_read_revision": 2})
    assert store.get(pk) == newer
    assert store.update_if_status(pk, Status.FOUND,
                                  {"jd_read_revision": 3}, expected_reader={
                                      "jd_text": "", "jd_read_revision": 2,
                                      "jd_read_attempts": 0, "jd_read_retry_at": None,
                                      "jd_read_prepare_only": False})
    # An old snapshot cannot match a newer generation even when values reset.
    assert not store.update_if_status(pk, Status.FOUND,
                                      {"status": "job_gone"}, expected_reader={
                                          "jd_text": "", "jd_read_revision": 2,
                                          "jd_read_attempts": 0, "jd_read_retry_at": None,
                                          "jd_read_prepare_only": False})
    assert not store.update_if_status("example-co#missing", Status.FOUND,
                                      {"status": "failed"}, expected_reader={})


def test_dynamo_storage_fault_propagates(aws, monkeypatch):
    from botocore.exceptions import ClientError
    import pytest

    make_applications_table()
    store = TrackingStore("applications")
    store.put_new(_synthetic_job("job-2"))

    def fail(**kwargs):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                     "Message": "synthetic outage"}}, "UpdateItem")

    monkeypatch.setattr(store._table, "update_item", fail)
    with pytest.raises(ClientError, match="ProvisionedThroughputExceededException"):
        store.update_if_status("example-co#job-2", Status.FOUND,
                               {"status": "tailoring"}, expected_reader={"jd_text": ""})


def test_redis_atomic_reader_witness_preserves_unrelated_fields_and_indexes():
    import fakeredis
    from core.storage.local import RedisTracking

    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = "example-co#job-1"
    tracking.set_status(pk, Status.FOUND, jd_text="", attempts=7, company="example-co",
                        jd_hash="synthetic-hash")
    assert tracking.update_if_status(pk, Status.FOUND,
                                     {"status": Status.TAILORING, "jd_read_revision": 1},
                                     expected_reader={"jd_text": ""})
    assert tracking.status_counts().get("found", 0) == 0
    assert tracking.status_counts()["tailoring"] == 1
    assert tracking.find_by_jd_hash("synthetic-hash") == pk
    tracking.set_status(pk, Status.TAILORING, note="synthetic")
    assert tracking.update_if_status(pk, Status.TAILORING,
                                     {"status": Status.FOUND, "jd_read_revision": 2,
                                      "jd_read_attempts": 1,
                                      "jd_read_retry_at": "2030-01-01T00:01:00+00:00"},
                                     expected_reader={"jd_text": "", "jd_read_revision": 1})
    newer = tracking.get(pk)
    assert newer["note"] == "synthetic"
    assert newer["attempts"] == 7
    assert tracking.status_counts().get("tailoring", 0) == 0
    assert tracking.status_counts()["found"] == 1
    assert not tracking.update_if_status(pk, Status.FOUND,
                                         {"status": Status.JOB_GONE},
                                         expected_reader={"jd_text": ""})
    assert tracking.get(pk) == newer
    assert not tracking.update_if_status(pk, Status.FOUND, {"status": "failed"},
                                         expected_reader={"jd_text": "", "jd_read_revision": 2,
                                                          "jd_read_attempts": 1})
    assert tracking.get(pk) == newer
    assert not tracking.update_if_status("example-co#missing", Status.FOUND,
                                         {"status": "failed"}, expected_reader={})


def test_redis_watch_collision_retries_without_rebasing_reader_witness(monkeypatch):
    import json
    import fakeredis
    from core.storage.local import RedisTracking

    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    pk = "example-co#job-1"
    tracking.set_status(pk, Status.FOUND, jd_text="", jd_read_revision=4,
                        company="example-co", note="original")
    original_pipeline = client.pipeline
    injected = [False]

    def concurrent_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected[0]:
                injected[0] = True
                current = tracking.get(pk)
                current["note"] = "concurrent unrelated edit"
                # Simulate an unconditional legacy writer during WATCH.
                client.set(f"app:{pk}", json.dumps(current))
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", concurrent_pipeline)
    assert tracking.update_if_status(
        pk, Status.FOUND,
        {"status": Status.TAILORING, "jd_read_revision": 5},
        expected_reader={"jd_text": "", "jd_read_revision": 4})
    row = tracking.get(pk)
    assert injected[0] is True
    assert row["status"] == "tailoring"
    assert row["jd_read_revision"] == 5
    assert row["note"] == "concurrent unrelated edit"
    assert tracking.status_counts()["tailoring"] == 1

    # Presence of false/zero/null is still not the same as absence.
    tracking.set_status(pk, Status.TAILORING, jd_read_attempts=0,
                        jd_read_retry_at=None, jd_read_prepare_only=False)
    last = tracking.get(pk)
    assert not tracking.update_if_status(pk, Status.TAILORING,
                                         {"status": Status.FAILED},
                                         expected_reader={"jd_text": "",
                                                          "jd_read_revision": 5})
    assert tracking.get(pk) == last


# Issue #22: real conditional Redis/fakeredis and Dynamo/moto repair proofs.

import json
from copy import deepcopy

import fakeredis
import pytest
from botocore.exceptions import ClientError

from core.storage.base import repair_reader_witness
from core.storage.local import RedisTracking


@pytest.fixture(params=["redis", "dynamo"])
def repair_backend(request, aws):
    if request.param == "redis":
        return RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    make_applications_table()
    return TrackingStore("applications")


def _repair_seed(store, *, status="error", old_url="/jobs/1?job_id=job-1",
                 **overrides):
    job = _synthetic_job()
    assert store.put_new(job)
    pk = job.pk
    attrs = {
        "jd_url": old_url, "jd_read_attempts": 3,
        "jd_read_retry_at": None, "jd_read_revision": 4,
        "jd_read_prepare_only": False, "jd_read_error": "reader error remains",
        "jd_text": "reader text remains", "error": "error remains",
        "attempts": 7, "note": "other attribute remains",
        "fail_kind": "jd_reader_exhausted" if status == "error" else "",
    }
    attrs.update(overrides)
    status_value = Status(status) if status in {x.value for x in Status} else Status.ERROR
    store.set_status(pk, status_value, **attrs)
    if status_value.value != status:
        _write_competing_row(store, pk, {"status": status})
    return pk, deepcopy(store.get(pk)), job


def _repair_call(store, before, *,
                 old_url=None, new_url="https://example.test/jobs/1?job_id=job-1",
                 expected_reader=None, status=None, company=None, job_id=None):
    return store.repair_legacy_jd_url(
        before["pk"], before["company"] if company is None else company,
        before["job_id"] if job_id is None else job_id,
        before["jd_url"] if old_url is None else old_url, new_url,
        expected_status=before["status"] if status is None else status,
        expected_reader=(repair_reader_witness(before)
                         if expected_reader is None else expected_reader),
    )


def _write_competing_row(store, pk, changes):
    """Synthetic unconditional legacy writer, always inside disposable backends."""
    if isinstance(store, RedisTracking):
        before = store.get(pk)
        current = {**before, **changes}
        store._write(pk, current, prev_status=before.get("status"))
    else:
        current = {**store.get(pk), **changes}
        store._table.put_item(Item=current)


def _indexes(store, job):
    return (
        {s.value: sorted(row["pk"] for row in store.query_status(s)) for s in Status},
        store.find_by_jd_hash(job.jd_hash),
    )


@pytest.mark.parametrize("source_status", ["error", "found"])
def test_repair_atomic_success_exact_patch_and_reader_fencing(
    repair_backend, source_status,
):
    store = repair_backend
    pk, before, job = _repair_seed(store, status=source_status)
    initial_indexes = _indexes(store, job)
    assert initial_indexes[1] == pk
    assert _repair_call(store, before)
    after = store.get(pk)
    expected = {
        **before, "status": "found",
        "jd_url": "https://example.test/jobs/1?job_id=job-1",
        "jd_read_attempts": 0, "jd_read_revision": 5,
    }
    if source_status == "error":
        expected.pop("fail_kind")
    assert after == expected
    assert "jd_reader_exhausted" not in after
    assert after["jd_read_retry_at"] == before["jd_read_retry_at"]
    assert after["jd_read_error"] == before["jd_read_error"]
    assert after["jd_text"] == before["jd_text"]
    assert after["attempts"] == 7
    counts, indexed = _indexes(store, job)
    assert indexed == pk
    assert counts["found"] == [pk]
    assert counts["error"] == []
    # In FOUND->FOUND, even an old reader seeing the same status must lose.
    assert not store.update_if_status(
        pk, Status.FOUND, {"status": Status.JOB_GONE},
        expected_reader=repair_reader_witness(before))
    assert store.get(pk) == after
    assert not _repair_call(store, before)
    assert store.get(pk) == after


def test_repair_legacy_absent_reader_attributes_gain_only_intended_fields(
    repair_backend,
):
    store = repair_backend
    pk, before, _ = _repair_seed(store)
    if isinstance(store, RedisTracking):
        current = store.get(pk)
        for field in ("jd_read_revision", "jd_read_attempts", "jd_read_retry_at",
                      "jd_read_prepare_only"):
            current.pop(field)
        store._write(pk, current, prev_status="error")
    else:
        store._table.update_item(
            Key={"pk": pk},
            UpdateExpression="REMOVE jd_read_revision, jd_read_attempts, jd_read_retry_at, jd_read_prepare_only",
        )
    before = store.get(pk)
    assert _repair_call(store, before)
    after = store.get(pk)
    assert after == {
        **{k: v for k, v in before.items() if k != "fail_kind"},
        "status": "found", "jd_url": "https://example.test/jobs/1?job_id=job-1",
        "jd_read_attempts": 0, "jd_read_revision": 1,
    }
    assert "jd_read_retry_at" not in after
    assert "jd_read_prepare_only" not in after


@pytest.mark.parametrize("status", [
    "applied", "applied_manual", "submitting", "tailoring", "tailored",
    "failed", "job_gone", "needs_human", "skipped", "capped", "unknown",
])
def test_repair_refuses_all_protected_and_unknown_statuses(
    repair_backend, status,
):
    store = repair_backend
    pk, before, job = _repair_seed(store, status=status)
    indexes = _indexes(store, job)
    assert not _repair_call(store, before)
    assert store.get(pk) == before
    assert _indexes(store, job) == indexes


@pytest.mark.parametrize(("field", "value"), [
    ("apply_requested_at", "2030-01-01T00:00:00Z"),
    ("confirmation_id", "synthetic-confirmation"),
    ("applied_at", "2020-01-01"),
    ("possible_submission", True),
    ("submit_hold", True),
    ("submit_hold_at", "2020-01-01"),
    ("submit_hold_reason", "not resolved"),
    ("submit_blocked", True),
    ("submit_attempted_at", "2020-01-01"),
    ("submission_attempted_at", "2020-01-01"),
    ("submit_attempts", 1),
    ("submission_attempts", 1),
    ("submit_outcome", "unknown"),
    ("submission_outcome", "unknown"),
    ("submit_result", "unknown"),
    ("submission_result", "unknown"),
    ("submitted_at", "2020-01-01"),
    ("submission_receipt", "synthetic"),
    ("gate_reason", "submit_uncertain"),
    ("fail_kind", "uncertain"),
    ("jd_read_retry_at", "2099-01-01T00:00:00+00:00"),
    ("jd_read_retry_at", "not a date"),
    ("jd_read_revision", -1),
])
def test_repair_independently_refuses_protected_evidence(
    repair_backend, field, value,
):
    store = repair_backend
    pk, before, job = _repair_seed(store, **{field: value})
    indexes = _indexes(store, job)
    assert not _repair_call(store, before)
    assert store.get(pk) == before
    assert _indexes(store, job) == indexes


@pytest.mark.parametrize("old_url", [
    "", " ", "javascript:alert(1)", "ftp://example.test/jobs/1",
    "http:jobs/1", "//foreign.example.test/jobs/1",
    "//example.test/jobs/1", "/jobs\\1", "/jobs/\n1",
    "http://example.test/jobs/1", "https://example.test/jobs/1",
    "https://boards.example.test/jobs/1",
])
def test_repair_refuses_malformed_or_already_absolute_stored_url(
    repair_backend, old_url,
):
    store = repair_backend
    pk, before, job = _repair_seed(store, old_url=old_url)
    indexes = _indexes(store, job)
    assert not _repair_call(store, before)
    assert store.get(pk) == before
    assert _indexes(store, job) == indexes


@pytest.mark.parametrize(("field", "change"), [
    ("pk", "different#key"), ("company", "wrong-co"),
    ("job_id", "other-job"),
])
def test_repair_rejects_identity_mismatch_or_corrupt_placeholder(
    repair_backend, field, change,
):
    store = repair_backend
    pk, before, job = _repair_seed(store)
    _write_competing_row(store, pk, {field: change})
    current = store.get(pk)
    indexes = _indexes(store, job)
    assert not _repair_call(store, before)
    assert store.get(pk) == current
    assert _indexes(store, job) == indexes


def test_repair_missing_row_cannot_upsert(repair_backend):
    store = repair_backend
    pk = "example-co#missing"
    assert not store.repair_legacy_jd_url(
        pk, "example-co", "missing", "/jobs/missing",
        "https://example.test/jobs/missing", expected_status=Status.ERROR,
        expected_reader={},
    )
    assert store.get(pk) is None


@pytest.mark.parametrize(("field", "value"), [
    ("status", "applied_manual"), ("status", "submitting"),
    ("apply_requested_at", "2030-01-01"),
    ("confirmation_id", "synthetic-confirmation"),
    ("submit_hold", True), ("possible_submission", True),
    ("jd_url", "/a/newer/job"), ("jd_read_revision", 5),
    ("jd_read_attempts", 4), ("jd_text", "new reader output"),
    ("jd_read_retry_at", "2030-01-01T00:00:00+00:00"),
    ("jd_read_prepare_only", True),
])
def test_dynamo_intervening_write_fails_original_condition_without_overwrite(
    aws, monkeypatch, field, value,
):
    make_applications_table()
    store = TrackingStore("applications")
    pk, before, job = _repair_seed(store, status="found")
    original_update = store._table.update_item
    intervened = [False]

    def concurrent_update(**kwargs):
        if not intervened[0]:
            intervened[0] = True
            _write_competing_row(store, pk, {field: value})
        return original_update(**kwargs)

    monkeypatch.setattr(store._table, "update_item", concurrent_update)
    assert not _repair_call(store, before)
    assert intervened[0]
    after = store.get(pk)
    assert after[field] == value
    assert after["jd_url"] == (value if field == "jd_url" else before["jd_url"])
    assert _indexes(store, job)[1] == pk


@pytest.mark.parametrize(("field", "value"), [
    ("status", "applied_manual"), ("status", "submitting"),
    ("apply_requested_at", "2030-01-01"),
    ("confirmation_id", "synthetic-confirmation"),
    ("submit_hold", True), ("possible_submission", True),
    ("jd_url", "/a/newer/job"), ("jd_read_revision", 5),
    ("jd_read_attempts", 4), ("jd_text", "new reader output"),
    ("jd_read_retry_at", "2030-01-01T00:00:00+00:00"),
    ("jd_read_prepare_only", True),
])
def test_redis_watch_race_refuses_protected_state_without_rebasing(
    monkeypatch, field, value,
):
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(client)
    pk, before, job = _repair_seed(store, status="found")
    original_pipeline = client.pipeline
    injected = [False]

    def conflicting_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected[0]:
                injected[0] = True
                _write_competing_row(store, pk, {field: value})
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", conflicting_pipeline)
    assert not _repair_call(store, before)
    assert injected[0]
    after = store.get(pk)
    assert after[field] == value
    assert after["jd_url"] == (value if field == "jd_url" else before["jd_url"])
    assert _indexes(store, job)[1] == pk


def test_redis_durable_hold_existing_and_added_under_watch(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(client)
    pk, before, job = _repair_seed(store, status="found")
    hold_key = f"submit_hold:{pk}"
    client.set(hold_key, "corrupt-hold")
    before_indexes = _indexes(store, job)
    assert not _repair_call(store, before)
    assert store.get(pk) == before
    assert client.get(hold_key) == "corrupt-hold"
    assert _indexes(store, job) == before_indexes

    client.delete(hold_key)
    original_pipeline = client.pipeline
    injected = [False]

    def conflicting_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected[0]:
                injected[0] = True
                client.set(hold_key, "new hold during WATCH")
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", conflicting_pipeline)
    assert not _repair_call(store, before)
    assert injected[0]
    assert store.get(pk) == before
    assert client.get(hold_key) == "new hold during WATCH"
    assert _indexes(store, job) == before_indexes


def test_redis_unrelated_field_wins_watch_contention_and_is_preserved(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(client)
    pk, before, job = _repair_seed(store)
    original_pipeline = client.pipeline
    injected = [False]

    def concurrent_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected[0]:
                injected[0] = True
                row = store.get(pk)
                row["unrelated"] = "concurrent field remains"
                client.set(f"app:{pk}", json.dumps(row))
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", concurrent_pipeline)
    assert _repair_call(store, before)
    after = store.get(pk)
    assert after["unrelated"] == "concurrent field remains"
    assert after["jd_read_revision"] == 5
    assert after["status"] == "found"
    assert _indexes(store, job)[1] == pk


def test_redis_found_reader_aba_cycle_defeats_stale_repair(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(client)
    pk, before, job = _repair_seed(store, status="found")
    original_pipeline = client.pipeline
    injected = [False]

    def race_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected[0]:
                injected[0] = True
                store.set_status(pk, Status.TAILORING, jd_read_revision=5)
                store.set_status(pk, Status.FOUND, jd_read_revision=6)
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", race_pipeline)
    assert not _repair_call(store, before)
    assert injected[0]
    assert store.get(pk)["jd_read_revision"] == 6
    assert store.get(pk)["jd_url"] == before["jd_url"]
    assert _indexes(store, job)[0]["found"] == [pk]


def test_repair_storage_faults_propagate_not_benign_conflicts(aws, monkeypatch):
    make_applications_table()
    dynamo = TrackingStore("applications")
    _, before, _ = _repair_seed(dynamo)

    def outage(**kwargs):
        raise ClientError({"Error": {"Code": "InternalServerError",
                                     "Message": "synthetic storage fault"}}, "UpdateItem")

    monkeypatch.setattr(dynamo._table, "update_item", outage)
    with pytest.raises(ClientError, match="InternalServerError"):
        _repair_call(dynamo, before)

    redis = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    _, before_redis, _ = _repair_seed(redis)
    def error_pipeline(*args, **kwargs):
        raise RuntimeError("synthetic Redis outage")
    monkeypatch.setattr(redis.r, "pipeline", error_pipeline)
    with pytest.raises(RuntimeError, match="synthetic Redis outage"):
        _repair_call(redis, before_redis)
