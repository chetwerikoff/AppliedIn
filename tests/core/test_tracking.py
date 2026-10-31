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
    assert tracking.put_new(_synthetic_job())
    tracking.set_status(pk, Status.FOUND, attempts=7, company="example-co",
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
    assert tracking.put_new(_synthetic_job())
    tracking.set_status(pk, Status.FOUND, jd_read_revision=4,
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


@pytest.mark.parametrize("field,value", [
    ("jd_text", "Changed unbound synthetic JD"),
    ("posting_guard", {"generation": "forged"}),
    ("reviewed_guard_generation", "unapproved-generation"),
])
def test_generic_status_and_reader_cas_cannot_split_guard(repair_backend, field, value):
    """Neither backend may bypass the atomic guard+JD update contract."""
    store = repair_backend
    job = _synthetic_job("guard-fields")
    assert store.put_new(job)
    before = deepcopy(store.get(job.pk))
    with pytest.raises(ValueError, match="update_posting_guard"):
        store.set_status(job.pk, Status.FOUND, **{field: value})
    with pytest.raises(ValueError, match="update_posting_guard"):
        store.update_if_status(
            job.pk, Status.FOUND, {field: value},
            expected_reader={"jd_text": before["jd_text"]})
    assert store.get(job.pk) == before


def test_redis_status_only_legacy_upsert_preserves_indexes_and_no_guard():
    """An old status-only bookkeeping write is not permission to write JD."""
    store = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = "example-co#synthetic-marker"
    store.set_status(pk, Status.FOUND, note="synthetic")
    assert store.get(pk) == {"pk": pk, "status": "found", "note": "synthetic"}
    assert store.status_counts()["found"] == 1
    store.set_status(pk, Status.TAILORED, note="updated")
    assert store.get(pk)["status"] == "tailored"
    assert store.status_counts().get("found", 0) == 0
    assert store.status_counts()["tailored"] == 1


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
    # This suite deliberately models a pre-guard legacy JD. Construct it in
    # the disposable backend, never through the protected production status API.
    legacy_jd = attrs.pop("jd_text")
    store.set_status(pk, status_value, **attrs)
    _write_competing_row(store, pk, {"jd_text": legacy_jd})
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
    assert store.put_new(_synthetic_job("other-job").model_copy(
        update={"jd_text": "an unrelated synthetic JD"}))
    other_pk = "example-co#other-job"
    unrelated_before = deepcopy(store.get(other_pk))
    initial_indexes = _indexes(store, job)
    assert initial_indexes[1] == pk
    assert _repair_call(store, before)
    assert store.get(other_pk) == unrelated_before
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
    assert counts["found"] == sorted([pk, other_pk])
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
    if field == "pk" and not isinstance(store, RedisTracking):
        # DynamoDB's primary key cannot be inconsistent with its item key.
        # Refuse the caller's wrong bound pk/identity instead.
        wrong = {**before, "company": "different-co"}
        indexes = _indexes(store, job)
        assert not _repair_call(store, wrong)
        assert store.get(pk) == before
        assert _indexes(store, job) == indexes
        return
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


def test_repair_cannot_upgrade_incomplete_placeholder_row(repair_backend):
    store = repair_backend
    pk = "example-co#job-1"
    if isinstance(store, RedisTracking):
        store.r.set(f"app:{pk}", json.dumps({"pk": pk, "jd_url": "/jobs/1"}))
    else:
        store._table.put_item(Item={"pk": pk, "jd_url": "/jobs/1"})
    assert not store.repair_legacy_jd_url(
        pk, "example-co", "job-1", "/jobs/1",
        "https://example.test/jobs/1", expected_status=Status.FOUND,
        expected_reader={},
    )
    assert store.get(pk) == {"pk": pk, "jd_url": "/jobs/1"}


def test_dynamo_found_reader_aba_cycle_defeats_stale_repair(aws, monkeypatch):
    make_applications_table()
    store = TrackingStore("applications")
    pk, before, job = _repair_seed(store, status="found")
    original_update = store._table.update_item
    injected = [False]

    def race_update(**kwargs):
        if not injected[0]:
            injected[0] = True
            # The status comes back to FOUND, but the reader witness cannot.
            _write_competing_row(store, pk, {
                "status": "tailoring", "jd_read_revision": 5})
            _write_competing_row(store, pk, {
                "status": "found", "jd_read_revision": 6})
        return original_update(**kwargs)

    monkeypatch.setattr(store._table, "update_item", race_update)
    assert not _repair_call(store, before)
    assert injected[0]
    assert store.get(pk)["jd_read_revision"] == 6
    assert store.get(pk)["jd_url"] == before["jd_url"]
    assert _indexes(store, job)[0]["found"] == [pk]



def test_dynamo_initial_board_pin_and_strong_alias_only_get(aws, monkeypatch):
    """Only the dedicated alias reader sends ConsistentRead to the base table."""
    make_applications_table()
    job = JobRecord(
        company="example-co", job_id="731205", title="Platform Engineer",
        jd_url="https://careers.example-co.test/openings/engineer?gh_jid=731205",
        jd_text="synthetic", ats="greenhouse", greenhouse_board="example-board",
    )
    store = TrackingStore("applications")
    assert store.put_new(job)
    assert not store.put_new(job.model_copy(update={"greenhouse_board": "foreign-board"}))
    reads = []
    original_get = store._table.get_item

    def spy(**kwargs):
        reads.append(dict(kwargs))
        return original_get(**kwargs)

    monkeypatch.setattr(store._table, "get_item", spy)
    pk = "example-co#731205"
    first = store.get(pk)
    assert first["greenhouse_board"] == "example-board"
    assert first["jd_url"] == job.jd_url and first["ats"] == "greenhouse"
    assert store.get_alias(pk)["greenhouse_board"] == "example-board"
    # Commit a pin change before the next alias read; the witness cannot reuse
    # its previous row even when the key/job URL are unchanged.
    store.set_status(pk, Status.FOUND, greenhouse_board="foreign-board")
    assert store.get_alias(pk)["greenhouse_board"] == "foreign-board"
    assert reads == [
        {"Key": {"pk": pk}},
        {"Key": {"pk": pk}, "ConsistentRead": True},
        {"Key": {"pk": pk}, "ConsistentRead": True},
    ]


def test_redis_initial_board_pin_is_in_conditional_value(monkeypatch):
    import json
    import fakeredis

    from core.storage.local import RedisTracking

    raw = fakeredis.FakeRedis(decode_responses=True)
    original_set = raw.set
    puts = []

    def spy(key, value, **kw):
        if key.startswith("app:"):
            puts.append((key, json.loads(value), dict(kw)))
            assert kw.get("nx") is True
            # The second SET NX may offer a different pin, but the conditional
            # rejection must preserve the initially committed identity.
            if len(puts) == 1:
                assert json.loads(value)["greenhouse_board"] == "example-board"
        return original_set(key, value, **kw)

    monkeypatch.setattr(raw, "set", spy)
    store = RedisTracking(raw)
    job = JobRecord(
        company="example-co", job_id="731205", title="Platform Engineer",
        jd_url="https://careers.example-co.test/openings/engineer?gh_jid=731205",
        jd_text="synthetic", ats="greenhouse", greenhouse_board="example-board",
    )
    assert store.put_new(job)
    assert store.get(job.pk)["greenhouse_board"] == "example-board"
    assert store.get(job.pk)["ats"] == "greenhouse"
    assert store.status_counts().get("found") == 1
    assert not store.put_new(job.model_copy(update={"greenhouse_board": "other-board"}))
    assert store.get(job.pk)["greenhouse_board"] == "example-board"
    assert all(row["pk"] == job.pk and row["job_id"] == "731205" for _, row, _ in puts)
    assert len(puts) == 2
    assert [row["greenhouse_board"] for _, row, _ in puts] == ["example-board", "other-board"]


# Issue #138: missing Greenhouse board pin only, with original row witnesses.
def _pin_seed(store):
    job = JobRecord(
        company="example-co", job_id="731205", title="Staff Platform Engineer",
        jd_url="https://careers.example-co.test/openings/engineer?gh_jid=731205",
        jd_text="synthetic", ats="greenhouse",
    )
    assert store.put_new(job)
    store.set_status(
        job.pk, Status.ERROR, attempts=4,
        apply_requested_at="2026-10-20T01:00:00Z",
        fail_kind="defect_greenhouse_tenant_alias",
        gate_reason="defect_greenhouse_tenant_alias",
        approval_status="pending", approved_at=None, jd_read_revision=3,
        note="Synthetic request stopped before any browser",
    )
    return job, deepcopy(store.get(job.pk))


def test_missing_pin_is_the_only_storage_change_on_both_backends(repair_backend):
    store = repair_backend
    job, before = _pin_seed(store)
    before_indexes = _indexes(store, job)
    assert store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert store.get(job.pk) == {**before, "greenhouse_board": "example-board"}
    assert _indexes(store, job) == before_indexes
    assert not store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert _indexes(store, job) == before_indexes


def test_redis_pin_watches_row_and_empty_independent_hold(monkeypatch):
    raw = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(raw)
    job, before = _pin_seed(store)
    raw.set(f"submit_hold:{job.pk}", "")
    assert not store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert store.get(job.pk) == before
    raw.delete(f"submit_hold:{job.pk}")
    watched = []
    original_pipeline = raw.pipeline

    def spy_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_watch = pipe.watch

        def watched_keys(*keys):
            watched.extend(keys)
            return original_watch(*keys)
        pipe.watch = watched_keys
        return pipe

    monkeypatch.setattr(raw, "pipeline", spy_pipeline)
    assert store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert f"app:{job.pk}" in watched
    assert f"submit_hold:{job.pk}" in watched


@pytest.mark.parametrize("changed", [
    {"status": "submitting"}, {"approval_status": "approved"},
    {"human_approved_answers": {"question": "synthetic"}},
    # Browser artifacts added after the original read are protected witnesses.
    {"fields": [{"label": "Synthetic field", "value": "synthetic"}]},
    {"screenshot_s3_key": "synthetic/example-screen.png"},
    {"jd_read_revision": 7}, {"possible_submission": True},
    {"apply_requested_at": "2026-10-21T00:00:00Z"},
])
def test_redis_pin_original_witness_rejects_race(monkeypatch, changed):
    raw = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(raw)
    job, before = _pin_seed(store)
    pipeline = raw.pipeline
    injected = [False]

    def concurrent_pipeline(*args, **kwargs):
        pipe = pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*args, **kwargs):
            if not injected[0]:
                injected[0] = True
                row = store.get(job.pk)
                row.update(changed)
                raw.set(f"app:{job.pk}", json.dumps(row))
            return original_execute(*args, **kwargs)
        pipe.execute = execute
        return pipe

    monkeypatch.setattr(raw, "pipeline", concurrent_pipeline)
    assert not store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert injected[0]
    assert store.get(job.pk) == {**before, **changed}


def test_redis_status_pre_read_then_late_write_preserves_new_pin(monkeypatch):
    """An earlier set_status read cannot overwrite a committed pin."""
    raw = fakeredis.FakeRedis(decode_responses=True)
    store = RedisTracking(raw)
    job, before = _pin_seed(store)
    original_pipeline = raw.pipeline
    injected = [False]

    def racing_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_get = pipe.get

        def get(key):
            old_value = original_get(key)
            if key == f"app:{job.pk}" and not injected[0]:
                injected[0] = True
                # The proof commits AFTER set_status has read under WATCH,
                # but BEFORE that writer executes its full-row transaction.
                assert store.add_missing_greenhouse_pin(
                    job.pk, "example-board", expected_row=before)
            return old_value
        pipe.get = get
        return pipe

    monkeypatch.setattr(raw, "pipeline", racing_pipeline)
    store.set_status(job.pk, Status.TAILORED, note="status writer updated")
    after = store.get(job.pk)
    assert injected[0]
    assert after["greenhouse_board"] == "example-board"
    assert after["status"] == "tailored"
    assert after["note"] == "status writer updated"
    assert after["apply_requested_at"] == before["apply_requested_at"]
    assert store.status_counts()["tailored"] == 1
    assert store.status_counts().get("error", 0) == 0
    assert store.find_by_jd_hash(job.jd_hash) == job.pk


def test_dynamo_pin_consistent_read_and_row_local_partial_condition(aws, monkeypatch):
    make_applications_table()
    store = TrackingStore("applications")
    job, before = _pin_seed(store)
    reads, writes = [], []
    read = store._table.get_item
    update = store._table.update_item

    def spy_read(**kwargs):
        reads.append(dict(kwargs))
        return read(**kwargs)

    def spy_update(**kwargs):
        writes.append(dict(kwargs))
        return update(**kwargs)

    monkeypatch.setattr(store._table, "get_item", spy_read)
    monkeypatch.setattr(store._table, "update_item", spy_update)
    assert store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert reads == [{"Key": {"pk": job.pk}, "ConsistentRead": True}]
    assert len(writes) == 1
    call = writes[0]
    assert call["UpdateExpression"] == "SET #pin = :board"
    assert "attribute_exists(#pk)" in call["ConditionExpression"]
    assert "attribute_not_exists(#pin)" in call["ConditionExpression"]
    assert "submit_hold" in call["ExpressionAttributeNames"].values()
    assert "approval_status" in call["ExpressionAttributeNames"].values()
    # The first observation lacked these fields: a browser artifact appearing
    # after that observation must make the DynamoDB condition fail.
    for artifact in ("fields", "screenshot_s3_key"):
        aliases = [alias for alias, field in call["ExpressionAttributeNames"].items()
                   if field == artifact]
        assert len(aliases) == 1
        assert f"attribute_not_exists({aliases[0]})" in call["ConditionExpression"]
    assert store.get(job.pk) == {**before, "greenhouse_board": "example-board"}


@pytest.mark.parametrize("changed", [
    {"status": "submitting"}, {"jd_read_revision": 4},
    {"approval_status": "approved"}, {"possible_submission": True},
    {"human_approved_answers": {"question": "synthetic"}},
    {"fields": [{"label": "Synthetic field", "value": "synthetic"}]},
    {"screenshot_s3_key": "synthetic/example-screen.png"},
    {"submit_hold": None},
])
def test_dynamo_pin_rejects_row_local_intervening_witness(aws, monkeypatch, changed):
    make_applications_table()
    store = TrackingStore("applications")
    job, before = _pin_seed(store)
    original_update = store._table.update_item
    injected = [False]

    def compete(**kwargs):
        if not injected[0]:
            injected[0] = True
            store._table.put_item(Item={**store.get(job.pk), **changed})
        return original_update(**kwargs)

    monkeypatch.setattr(store._table, "update_item", compete)
    assert not store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=before)
    assert injected[0]
    assert store.get(job.pk) == {**before, **changed}


@pytest.mark.parametrize("status", [Status.NEEDS_HUMAN, Status.FAILED])
def test_storage_pin_rejects_requestless_browser_gates_on_both_backends(
    repair_backend, status,
):
    """Requestless _apply_direct form evidence must not authorize a tenant pin.

    Both row-local DynamoDB and row+hold Redis reject it without changing
    status/index or history; no application path or real backend runs here.
    """
    store = repair_backend
    job, _before_request = _pin_seed(store)
    store.set_status(
        job.pk, status, apply_requested_at="", fail_kind="",
        gate_reason="unknown_field", gate_call_id="direct",
        gate_source="applier",
        gate_pending={"form_question": {"pk": job.pk, "label": "Synthetic question"}},
        fields=[{"label": "Synthetic field", "value": "synthetic"}],
        screenshot_s3_key="synthetic/example-screen.png",
    )
    original = deepcopy(store.get(job.pk))
    indexes = _indexes(store, job)
    assert not store.add_missing_greenhouse_pin(
        job.pk, "example-board", expected_row=original)
    assert store.get(job.pk) == original
    assert _indexes(store, job) == indexes


# Issue #202: exact direct owner gate compare-and-consume on both backends.

def _gate_seed(store, *, generation="gen-synthetic-1", **extra):
    job = _synthetic_job("job-202")
    store.put_new(job)
    pending = {"question": "Synthetic display name?", "review_route": "owner",
               "form_question": {"pk": job.pk, "label": "Synthetic display name",
                                 "type": "text", "tag": "input",
                                 "route": "ashby-sibling-mismatch",
                                 "requiredness": "unknown"}}
    if generation is not None:
        pending["generation"] = generation
    store.set_status(job.pk, Status.NEEDS_HUMAN, gate_reason="owner_question",
                     gate_call_id="direct", gate_source="applier",
                     gate_pending=pending, note="unrelated", **extra)
    return job, store.get(job.pk)


def _answer(row, value="synthetic"):
    return {"kind": "answer", "identity": "q1_synthetic",
            "generation": row["gate_pending"].get("generation"), "at": "t"}


def _skip(row):
    return {"kind": "skip", "identity": "q1_synthetic",
            "generation": row["gate_pending"].get("generation"), "at": "t"}


def _skip_record(row):
    return ("q1_synthetic", {"identity": "q1_synthetic", "route": "ashby-sibling-mismatch",
                             "kind": "text", "label": "Synthetic display name",
                             "generation": row["gate_pending"]["generation"]})


def test_direct_gate_answer_consumes_once_and_moves_to_tailored(repair_backend):
    store = repair_backend
    job, row = _gate_seed(store)
    grant = ("Synthetic display name", {"value": "synthetic"})
    assert store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_answer(row), grant=grant)
    after = store.get(job.pk)
    assert after["status"] == "tailored"
    assert after["gate_pending"] == {} and after["gate_call_id"] is None
    assert after["gate_source"] is None and after["gate_reason"] == ""
    assert after["gate_disposition"]["kind"] == "answer"
    assert after["human_approved_answers"] == {"Synthetic display name": {"value": "synthetic"}}
    assert after["note"] == "unrelated" and after.get("tailored_at")
    assert "owner_gate_skips" not in after
    assert store.query_status(Status.NEEDS_HUMAN) == []
    assert [r["pk"] for r in store.query_status(Status.TAILORED)] == [job.pk]
    # The same request (and a competing SKIP) can never win a spent gate.
    assert not store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_answer(row), grant=grant)
    assert not store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
        skip=_skip_record(row))
    assert store.get(job.pk) == after


def test_direct_gate_skip_records_only_skip_and_no_grant(repair_backend):
    store = repair_backend
    job, row = _gate_seed(store, human_approved_answers={"Other": {"value": "kept"}})
    assert store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
        skip=_skip_record(row))
    after = store.get(job.pk)
    assert after["status"] == "tailored" and after["gate_disposition"]["kind"] == "skip"
    assert after["owner_gate_skips"] == {"q1_synthetic": _skip_record(row)[1]}
    assert after["human_approved_answers"] == {"Other": {"value": "kept"}}


@pytest.mark.parametrize("drift", [
    "generation", "pending_value", "status", "call", "source", "confirmation",
    "possible_submission", "generationless", "missing_row",
])
def test_direct_gate_consume_refuses_any_non_exact_gate(repair_backend, drift):
    store = repair_backend
    job, row = _gate_seed(store, generation=None if drift == "generationless" else "gen-1")
    expected = deepcopy(row["gate_pending"])
    pk = job.pk
    if drift == "generation":
        expected["generation"] = "gen-older"
    elif drift == "pending_value":
        expected["form_question"]["label"] = "Other synthetic question"
    elif drift == "status":
        store.set_status(pk, Status.TAILORED)
    elif drift == "call":
        store.set_status(pk, Status.NEEDS_HUMAN, gate_call_id="adk-call")
    elif drift == "source":
        store.set_status(pk, Status.NEEDS_HUMAN, gate_source="tailor")
    elif drift == "confirmation":
        store.set_status(pk, Status.NEEDS_HUMAN, confirmation_id="synthetic-confirmation")
    elif drift == "possible_submission":
        store.set_status(pk, Status.NEEDS_HUMAN, possible_submission=True)
    elif drift == "missing_row":
        pk = "example-co#missing"
    before = store.get(pk)
    indexes = _indexes(store, job)
    for disposition, kwargs in ((_answer(row), {"grant": ("Synthetic display name", {})}),
                                (_skip(row), {"skip": ("q1_synthetic", {})})):
        if drift == "generationless":
            disposition = {**disposition, "generation": None}
        assert not store.consume_direct_form_gate(
            pk, expected_pending=expected, disposition=disposition, **kwargs)
    assert store.get(pk) == before
    assert _indexes(store, job) == indexes
    if drift == "missing_row":
        assert store.get(pk) is None


def test_reissued_identical_looking_gate_rejects_stale_generation(repair_backend):
    store = repair_backend
    job, first = _gate_seed(store, generation="gen-first")
    reissued = {**deepcopy(first["gate_pending"]), "generation": "gen-second"}
    store.set_status(job.pk, Status.NEEDS_HUMAN, gate_pending=reissued)
    assert not store.consume_direct_form_gate(
        job.pk, expected_pending=first["gate_pending"], disposition=_answer(first),
        grant=("Synthetic display name", {"value": "stale"}))
    current = store.get(job.pk)
    assert current["status"] == "needs_human" and "human_approved_answers" not in current
    assert store.consume_direct_form_gate(
        job.pk, expected_pending=reissued, disposition=_answer(current),
        grant=("Synthetic display name", {"value": "fresh"}))
    assert store.get(job.pk)["human_approved_answers"]["Synthetic display name"] == {
        "value": "fresh"}


def test_redis_consume_refuses_durable_hold_and_retries_unrelated_watch_collision(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    job, row = _gate_seed(tracking)
    client.set(f"submit_hold:{job.pk}", "synthetic")
    assert not tracking.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
        skip=_skip_record(row))
    client.delete(f"submit_hold:{job.pk}")
    original_pipeline = client.pipeline
    injected = []

    def concurrent_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not injected:
                injected.append(True)
                current = tracking.get(job.pk)
                current["note"] = "concurrent unrelated edit"
                client.set(f"app:{job.pk}", json.dumps(current))
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", concurrent_pipeline)
    assert tracking.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
        skip=_skip_record(row))
    after = tracking.get(job.pk)
    assert injected and after["note"] == "concurrent unrelated edit"
    assert after["status"] == "tailored"


def test_redis_concurrent_answer_and_skip_have_exactly_one_winner(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    job, row = _gate_seed(tracking)
    original_pipeline = client.pipeline
    results = []

    def racing_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*a, **kw):
            if not results:
                # The competing SKIP commits between this answer's read and
                # EXEC; the marker keeps its own EXEC from racing again.
                results.append(None)
                results[0] = RedisTracking(client).consume_direct_form_gate(
                    job.pk, expected_pending=row["gate_pending"],
                    disposition=_skip(row), skip=_skip_record(row))
            return original_execute(*a, **kw)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", racing_pipeline)
    won = tracking.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_answer(row),
        grant=("Synthetic display name", {"value": "loser"}))
    assert results == [True] and won is False
    after = tracking.get(job.pk)
    assert after["gate_disposition"]["kind"] == "skip"
    assert "human_approved_answers" not in after


def test_dynamo_concurrent_write_fails_consume_without_overwrite(aws, monkeypatch):
    make_applications_table()
    store = TrackingStore("applications")
    job, row = _gate_seed(store)
    original_update = store._table.update_item
    injected = []

    def compete(**kwargs):
        if not injected:
            injected.append(True)
            assert store.consume_direct_form_gate(
                job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
                skip=_skip_record(row))
        return original_update(**kwargs)

    monkeypatch.setattr(store._table, "update_item", compete)
    assert not store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_answer(row),
        grant=("Synthetic display name", {"value": "loser"}))
    after = store.get(job.pk)
    assert after["gate_disposition"]["kind"] == "skip"
    assert "human_approved_answers" not in after


def test_dynamo_consume_storage_fault_propagates(aws, monkeypatch):
    make_applications_table()
    store = TrackingStore("applications")
    job, row = _gate_seed(store)

    def fail(**kwargs):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                     "Message": "synthetic outage"}}, "UpdateItem")

    monkeypatch.setattr(store._table, "update_item", fail)
    with pytest.raises(ClientError, match="ProvisionedThroughputExceededException"):
        store.consume_direct_form_gate(
            job.pk, expected_pending=row["gate_pending"], disposition=_skip(row),
            skip=_skip_record(row))


def test_direct_gate_refusal_spends_the_gate_into_an_unbound_terminal_question(repair_backend):
    store = repair_backend
    job, row = _gate_seed(store)
    refuse = {**_answer(row), "kind": "refuse", "detail": "Synthetic cannot proceed"}
    assert store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=refuse)
    after = store.get(job.pk)
    assert after["status"] == "needs_human" and after["gate_reason"] == "cannot_proceed"
    assert after["gate_pending"] == {"question": "Synthetic cannot proceed",
                                     "form_question": None}
    assert after["gate_call_id"] is None and after["gate_disposition"]["kind"] == "refuse"
    assert "human_approved_answers" not in after and not after.get("tailored_at")
    # A refusal carries no grant, and a spent gate admits no later answer.
    assert not store.consume_direct_form_gate(
        job.pk, expected_pending=row["gate_pending"], disposition=_answer(row),
        grant=("Synthetic display name", {"value": "synthetic"}))
    assert store.get(job.pk) == after



# #201's URL replacement is separate from #22's relative recrawl. The
# coordinator currently refuses all historic GH targets at the unchanged
# Submit boundary; these synthetic backend tests check CAS mechanics only.
def _canonical_seed(store, *, status="found", long_text=False):
    job = JobRecord(
        company="example-co", job_id="123e4567-e89b-42d3-a456-426614174000",
        ats="ashby", title="Synthetic Engineer",
        jd_url="https://example.test/legacy-wrapper",
        jd_text=("long synthetic JD " * 40 if long_text else "Synthetic JD"),
    )
    assert store.put_new(job)
    if status == "error":
        store.set_status(job.pk, Status.ERROR, fail_kind="jd_reader_exhausted",
                         jd_read_retry_at=None, jd_read_revision=4,
                         jd_read_attempts=3, jd_read_prepare_only=False)
    else:
        store.set_status(job.pk, Status.FOUND, jd_read_revision=4,
                         jd_read_attempts=1, jd_read_retry_at=None,
                         jd_read_prepare_only=False)
    return job, deepcopy(store.get(job.pk))


_CANONICAL_NEW = (
    "https://jobs.ashbyhq.com/example-board/"
    "123e4567-e89b-42d3-a456-426614174000"
)


@pytest.mark.parametrize("status", ["found", "error"])
def test_canonical_backend_only_partial_patch_fences_stale_jd_guard_and_indexes(
    repair_backend, status,
):
    from tools import posting_guard as pg

    store = repair_backend
    job, original = _canonical_seed(store, status=status, long_text=True)
    before_hash = store.find_by_jd_hash(job.jd_hash)
    assert len(original["jd_text"]) > 400
    assert store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=original)
    after = store.get_current(job.pk)
    assert after["jd_url"] == _CANONICAL_NEW and after["status"] == "found"
    assert after["jd_text"] == ""
    assert after["posting_guard"] == pg.analyze(
        "", source_required=True)["posting_guard"]
    assert after["posting_guard"]["source_verified"] is False
    assert after["jd_read_revision"] == 5
    assert after["jd_read_attempts"] == 0
    assert "jd_read_retry_at" not in after
    assert "reviewed_guard_generation" not in after
    assert after["jd_read_prepare_only"] is False
    assert after["jd_hash"] == original["jd_hash"]
    assert after["attempts"] == original["attempts"]
    assert after["apply_requested_at"] == ""
    assert store.find_by_jd_hash(job.jd_hash) == before_hash == job.pk
    assert [r["pk"] for r in store.query_status(Status.FOUND)] == [job.pk]
    if status == "error":
        assert "fail_kind" not in after
    else:
        assert after.get("fail_kind") == original.get("fail_kind")
    assert not pg.authorised(after)
    # An old reader generation cannot overwrite the new source's contents.
    assert not store.update_if_status(
        job.pk, Status.FOUND, {"jd_read_attempts": 1},
        expected_reader={"jd_text": "", "jd_read_revision": 4})
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=original)


@pytest.mark.parametrize("change", [
    {"reviewed_guard_generation": ""},
    {"reviewed_guard_generation": None},
    {"posting_guard": {"status": "clean", "generation": "different"}},
    {"jd_read_revision": 17},
    {"jd_read_attempts": 4},
    {"jd_read_retry_at": "2026-10-22T00:00:00+00:00"},
    {"jd_text": "a newer guard-bound source"},
    {"jd_read_prepare_only": True},
    {"greenhouse_board": "other-board"},
    {"status": "tailoring"},
    {"apply_requested_at": "2026-10-25T00:00:00Z"},
    {"submit_hold": ""},
    {"gate_pending": {}},
    {"fields": []},
    {"approval_status": ""},
    {"gate_reason": ""},
])
def test_canonical_backend_rejects_interleaved_protected_and_reader_changes(
    repair_backend, change,
):
    store = repair_backend
    job, observed = _canonical_seed(store)
    _write_competing_row(store, job.pk, change)
    actual = deepcopy(store.get(job.pk))
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=observed)
    assert store.get(job.pk) == actual


def test_canonical_backend_reviewed_row_is_never_eligible(repair_backend):
    store = repair_backend
    job, observed = _canonical_seed(store)
    _write_competing_row(store, job.pk, {
        "reviewed_guard_generation": "old-reviewed-generation"})
    before = deepcopy(store.get(job.pk))
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=before)
    assert store.get(job.pk) == before


def test_canonical_redis_independent_empty_hold_blocks_and_never_upserts():
    from core.storage.local import RedisTracking

    store = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    job, observed = _canonical_seed(store)
    store.r.set(f"submit_hold:{job.pk}", "")
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=observed)
    assert store.get(job.pk) == observed
    store.r.delete(f"submit_hold:{job.pk}")
    store.r.delete(f"app:{job.pk}")
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=observed)
    assert store.get(job.pk) is None


def test_canonical_dynamo_condition_catches_guard_change_after_strong_read(
    aws, monkeypatch,
):
    make_applications_table()
    store = TrackingStore("applications")
    job, observed = _canonical_seed(store)
    update = store._table.update_item
    changed = [False]

    def competing_update(**kwargs):
        if not changed[0]:
            changed[0] = True
            _write_competing_row(store, job.pk, {"reviewed_guard_generation": "new-review"})
        return update(**kwargs)

    monkeypatch.setattr(store._table, "update_item", competing_update)
    assert not store.replace_noncanonical_jd_url(
        job.pk, _CANONICAL_NEW, expected_row=observed)
    assert changed[0]
    assert store.get_current(job.pk)["reviewed_guard_generation"] == "new-review"


# Issue #279: guarded grant replacement shared by both tracking backends.
_G_OLD = {"value": "a", "source": "bank-auto", "form_selector": "", "form_action": ""}
_G_NEW = {"value": "a", "source": "bank-auto", "form_selector": "#r", "form_action": "#s"}
_G_OTHER = {"value": "z", "source": "dashboard"}


def _grant_row(store, *, status="submitting", grants=None, **extra):
    job = _synthetic_job("grant-cas")
    assert store.put_new(job)
    attrs = dict(extra)
    if grants is not None:
        attrs["human_approved_answers"] = grants
    store.set_status(job.pk, Status(status), **attrs)
    return job.pk


def test_grant_replace_writes_only_the_new_key(repair_backend):
    store = repair_backend
    pk = _grant_row(store, grants={"k": dict(_G_OLD), "other": dict(_G_OTHER)})
    before = deepcopy(store.get(pk))
    assert store.replace_grants_if_unchanged(pk, {"k": _G_OLD}, {"k": _G_NEW})
    after = store.get(pk)
    assert after["human_approved_answers"] == {"k": _G_NEW, "other": _G_OTHER}
    assert {k: v for k, v in after.items() if k != "human_approved_answers"} == {
        k: v for k, v in before.items() if k != "human_approved_answers"}


def test_grant_replace_creates_an_absent_key_and_map(repair_backend):
    store = repair_backend
    pk = _grant_row(store)
    assert store.replace_grants_if_unchanged(pk, {"k": None}, {"k": _G_NEW})
    assert store.get(pk)["human_approved_answers"] == {"k": _G_NEW}
    assert not store.replace_grants_if_unchanged(pk, {"k": None}, {"k": _G_OLD})
    assert store.get(pk)["human_approved_answers"] == {"k": _G_NEW}


def test_grant_replace_writes_nothing_when_expected_changed(repair_backend):
    store = repair_backend
    pk = _grant_row(store, grants={"k": dict(_G_OLD), "other": dict(_G_OTHER)})
    before = deepcopy(store.get(pk))
    # concurrent change of the guarded key, of a guarded absent key, of a guard-only key
    assert not store.replace_grants_if_unchanged(
        pk, {"k": {**_G_OLD, "value": "b"}}, {"k": _G_NEW})
    assert not store.replace_grants_if_unchanged(
        pk, {"k": _G_OLD, "missing": dict(_G_OTHER)}, {"k": _G_NEW})
    assert not store.replace_grants_if_unchanged(
        pk, {"k": _G_OLD, "other": {**_G_OTHER, "value": "q"}}, {"k": _G_NEW})
    assert not store.replace_grants_if_unchanged(
        pk, {"k": None}, {"k": _G_NEW})
    assert store.get(pk) == before


@pytest.mark.parametrize("extra", [{"status": "tailored"}, {"possible_submission": True}])
def test_grant_replace_needs_submitting_without_possible_submission(repair_backend, extra):
    store = repair_backend
    pk = _grant_row(store, status=extra.get("status", "submitting"),
                    grants={"k": dict(_G_OLD)},
                    **({"possible_submission": True} if "possible_submission" in extra else {}))
    before = deepcopy(store.get(pk))
    assert not store.replace_grants_if_unchanged(pk, {"k": _G_OLD}, {"k": _G_NEW})
    assert store.get(pk) == before


def test_grant_replace_rejects_unguarded_or_malformed_input(repair_backend):
    store = repair_backend
    pk = _grant_row(store, grants={"k": dict(_G_OLD)})
    for expected, new in (({"k": _G_OLD}, {"k": _G_NEW, "x": _G_NEW}), ({"k": _G_OLD}, {}),
                          ({"k": _G_OLD}, {"k": "text"}), ({"k": "text"}, {"k": _G_NEW})):
        with pytest.raises(ValueError):
            store.replace_grants_if_unchanged(pk, expected, new)
    assert store.get(pk)["human_approved_answers"] == {"k": _G_OLD}


def test_grant_replace_missing_row_returns_false(repair_backend):
    assert not repair_backend.replace_grants_if_unchanged(
        "example-co#absent", {"k": None}, {"k": _G_NEW})


def test_redis_grant_replace_loses_to_a_write_between_read_and_commit():
    store = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = _grant_row(store, grants={"k": dict(_G_OLD)})
    original = store.r.pipeline
    fired = []

    def racing(*args, **kwargs):
        pipe = original(*args, **kwargs)
        real_get = pipe.get

        def get(name, *a, **k):
            value = real_get(name, *a, **k)
            if name == f"app:{pk}" and not fired:
                fired.append(1)
                row = json.loads(value)
                row["human_approved_answers"]["k"] = {**_G_OLD, "value": "raced"}
                store.r.set(name, json.dumps(row))
            return value
        pipe.get = get
        return pipe
    store.r.pipeline = racing
    assert not store.replace_grants_if_unchanged(pk, {"k": _G_OLD}, {"k": _G_NEW})
    assert store.get(pk)["human_approved_answers"]["k"]["value"] == "raced"


def test_dynamo_grant_replace_condition_catches_a_change_after_the_read(aws, monkeypatch):
    make_applications_table()
    store = TrackingStore("applications")
    pk = _grant_row(store, grants={"k": dict(_G_OLD)})
    stale = store.get_current(pk)
    _write_competing_row(store, pk, {"human_approved_answers": {
        "k": {**_G_OLD, "value": "raced"}}})
    monkeypatch.setattr(store, "get_current", lambda _pk: stale)
    assert not store.replace_grants_if_unchanged(pk, {"k": _G_OLD}, {"k": _G_NEW})
    assert store.get(pk)["human_approved_answers"]["k"]["value"] == "raced"
    # a status change after the read is refused by the same condition
    _write_competing_row(store, pk, {"human_approved_answers": {"k": dict(_G_OLD)}})
    monkeypatch.undo()
    stale = store.get_current(pk)
    _write_competing_row(store, pk, {"status": "needs_human"})
    monkeypatch.setattr(store, "get_current", lambda _pk: stale)
    assert not store.replace_grants_if_unchanged(pk, {"k": _G_OLD}, {"k": _G_NEW})
    assert store.get(pk)["human_approved_answers"] == {"k": _G_OLD}
