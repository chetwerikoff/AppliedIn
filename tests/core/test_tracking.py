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
