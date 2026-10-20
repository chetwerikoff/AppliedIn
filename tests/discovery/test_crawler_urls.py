"""Trusted discovery hrefs: fake real HTTP and browser listing seams, no live services."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from core.models import DiscoveryMode, JobRecord
from core.storage.base import legacy_repair_row_allowed
from discovery import chrome_crawl, crawler, sitemap
from discovery.watchlist import CompanyConfig, Preferences


class _Tracking:
    r = None

    def __init__(self):
        self.rows = {}
        self.persisted = {}
        self.put_calls = []
        self.repair_calls = []
        self.before_repair = None

    def put_new(self, job):
        self.put_calls.append(job.model_copy(deep=True))
        if job.pk in self.persisted:
            return False
        self.rows[job.pk] = job.model_copy(deep=True)
        self.persisted[job.pk] = {
            "pk": job.pk, "company": job.company, "job_id": job.job_id,
            "jd_url": job.jd_url, "status": "found", "jd_text": job.jd_text,
            "attempts": 0,
        }
        return True

    def seed(self, row, *, key=None):
        pk = key or row["pk"]
        self.persisted[pk] = deepcopy(row)
        self.rows[pk] = _job(row.get("jd_url", ""), row.get("job_id", ""))

    def get(self, pk):
        return deepcopy(self.persisted.get(pk))

    def repair_legacy_jd_url(
        self, pk, company, job_id, old_url, new_url, *,
        expected_status, expected_reader,
    ):
        self.repair_calls.append((pk, old_url, new_url))
        if self.before_repair:
            self.before_repair(pk)
        row = self.persisted.get(pk)
        if not legacy_repair_row_allowed(
                row, pk, company, job_id, old_url,
                expected_status, expected_reader):
            return False
        row["jd_url"] = new_url
        row["status"] = "found"
        row["jd_read_attempts"] = 0
        row["jd_read_revision"] = int(row.get("jd_read_revision", 0)) + 1
        if expected_status == "error":
            row.pop("fail_kind")
        self.rows[pk].jd_url = new_url
        return True


class _Queue:
    def __init__(self):
        self.calls = []

    def enqueue(self, queue, body):
        self.calls.append((queue, dict(body)))


class _Stores:
    def __init__(self):
        self.tracking = _Tracking()
        self.queue = _Queue()
        self.tailor_queue = "synthetic-tailor"


def _company(source="https://example.test/careers", mode=DiscoveryMode.CRAWL):
    return CompanyConfig(
        name="example-co", careers_url=source, ats="custom", discovery=mode,
    )


def _job(href, job_id="123"):
    return JobRecord(
        company="example-co", job_id=job_id, title="Software Engineer",
        jd_url=href, jd_text="Synthetic listing", ats="custom",
    )


@pytest.fixture
def offline_pipeline(monkeypatch):
    """Nothing in these tests can touch owner Redis, the URL ledger or a model."""
    from core import events, flags
    from tools import seen

    monkeypatch.setattr(flags, "effective_prefs", lambda name, prefs: prefs)
    monkeypatch.setattr(flags, "company_pref", lambda name: {})
    monkeypatch.setattr(flags, "company_filter", lambda name: [])
    monkeypatch.setattr(crawler, "_age_limit", lambda name: 0.0)
    emitted = []
    monkeypatch.setattr(events, "emit", lambda *args, **kw: emitted.append((args, kw)))

    screened = []
    def screen(jobs, prefs):
        screened.extend(j.jd_url for j in jobs)
        return list(jobs)

    monkeypatch.setattr(crawler, "relevant", screen)
    urls, marks = set(), []
    monkeypatch.setattr(seen, "load", lambda: set(urls))

    def mark(jobs):
        marks.append([j.jd_url for j in jobs])
        urls.update(j.jd_url for j in jobs)

    monkeypatch.setattr(seen, "mark", mark)
    return SimpleNamespace(screened=screened, urls=urls, marks=marks, emitted=emitted)


def _static_client(handler=None):
    if handler is None:
        handler = lambda req: httpx.Response(200, text="<html>synthetic careers</html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fake_browser(monkeypatch, rows, calls):
    from core import config
    from tools import browser_runtime, company_skills

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(chrome_model=""))
    monkeypatch.setattr(browser_runtime, "available", lambda: (True, ""))
    monkeypatch.setattr(browser_runtime, "applies_running", lambda: 0)
    monkeypatch.setattr(company_skills, "instructions_for", lambda *args: "")

    async def listing_read(*args, **kwargs):
        calls.append(dict(kwargs))
        return {"jobs": rows, "board": "none", "note": "synthetic"}, None

    monkeypatch.setattr(browser_runtime, "run_task", listing_read)


def test_plain_flow_normalizes_before_screen_seen_store_and_queue(offline_pipeline):
    stores = _Stores()
    original = "/jobs/123?job_id=123&lang=en"
    expected = "https://example.test/jobs/123?job_id=123&lang=en"
    external = "https://boards.example.test/postings/EXT-9?job_id=EXT-9"

    def extract(html, company):
        assert company == "example-co"
        return [_job(original), _job(external, "EXT-9")]

    with _static_client() as client:
        assert crawler.crawl_company(_company(), Preferences(), stores,
                                     client=client, extractor=extract) == 2
        # A second scan must neither change persisted identity nor enqueue again.
        assert crawler.crawl_company(_company(), Preferences(), stores,
                                     client=client, extractor=extract) == 0

    assert offline_pipeline.screened == [expected, external, expected, external]
    assert {j.jd_url for j in stores.tracking.put_calls} == {expected, external}
    assert len(stores.tracking.put_calls) == 2
    assert len(stores.tracking.rows) == len(stores.queue.calls) == 2
    assert offline_pipeline.marks == [[expected, external]]
    plain = next(j for j in stores.tracking.rows.values() if j.job_id == "123")
    assert plain.jd_url == expected
    assert plain.pk == _job(original).pk
    assert next(j for j in stores.tracking.rows.values() if j.job_id == "EXT-9").jd_url == external


def test_verified_same_host_redirect_provides_relative_path_base(offline_pipeline):
    stores = _Stores()
    requested = []

    def handler(req):
        requested.append(str(req.url))
        if req.url.path == "/careers":
            return httpx.Response(302, headers={"Location": "/dept/openings/"})
        return httpx.Response(200, text="<html>listing</html>")

    with _static_client(handler) as client:
        result = crawler.crawl_company(
            _company(), Preferences(), stores, client=client,
            extractor=lambda *_: [_job("123?job_id=123")],
        )
    assert result == 1
    assert requested == ["https://example.test/careers",
                         "https://example.test/dept/openings/"]
    assert stores.tracking.put_calls[0].jd_url == (
        "https://example.test/dept/openings/123?job_id=123"
    )


def test_http_to_https_upgrade_uses_verified_final_path(offline_pipeline):
    stores = _Stores()

    def handler(req):
        if req.url.scheme == "http":
            return httpx.Response(301, headers={
                "Location": "https://example.test/openings/"})
        return httpx.Response(200, text="<html>listing</html>")

    with _static_client(handler) as client:
        assert crawler.crawl_company(
            _company("http://example.test/careers"), Preferences(), stores,
            client=client, extractor=lambda *_: [_job("123?job_id=123")],
        ) == 1
    assert stores.tracking.put_calls[0].jd_url == (
        "https://example.test/openings/123?job_id=123"
    )


def test_foreign_final_redirect_cannot_become_a_relative_base(offline_pipeline):
    stores = _Stores()
    def handler(req):
        if req.url.host == "example.test":
            return httpx.Response(302, headers={
                "Location": "https://foreign.example.test/jobs/"})
        return httpx.Response(200, text="<html>listing</html>")

    with _static_client(handler) as client:
        assert crawler.crawl_company(
            _company(), Preferences(), stores, client=client,
            extractor=lambda *_: [_job("123?job_id=123")],
        ) == 0

    assert offline_pipeline.screened == []
    assert stores.tracking.put_calls == stores.queue.calls == []
    assert offline_pipeline.marks == []


@pytest.mark.parametrize("href", [
    "", " ", "javascript:alert(1)", "data:text/plain,a", "file:///tmp/post",
    "mailto:hr@example.test", "ftp://example.test/jobs/123",
    "https:jobs/123", "http:///jobs/123", "http://[broken/jobs/123",
    "https://example.test:bogus/jobs", "http://example.test:99999/jobs",
    "https://user@example.test/jobs/123", "//foreign.example.test/jobs/123",
    "///foreign.example.test/jobs/123", "//?job_id=123", "/jobs/\n123", "/jobs\\123",
    "/jobs/\u00a0bad", "#fragment-only", "https://example.test/jobs/bad url",
])
def test_plain_rejected_extracted_row_causes_no_fallback_or_writes(
    href, offline_pipeline, monkeypatch,
):
    stores = _Stores()
    # Run the actual default-extractor branch. Rejected model output must NOT
    # launch a second browser session just because nothing was admitted.
    monkeypatch.setattr(crawler, "_default_extractor",
                        lambda html, company: [_job(href)])
    monkeypatch.setattr(crawler, "sitemap_jobs",
                        lambda *args: pytest.fail("invalid row caused sitemap work"))
    monkeypatch.setattr(crawler, "_browser_extract",
                        lambda *args: pytest.fail("invalid row caused browser work"))
    with _static_client() as client:
        assert crawler.crawl_company(_company(), Preferences(), stores,
                                     client=client) == 0
    assert offline_pipeline.screened == []
    assert stores.tracking.put_calls == stores.queue.calls == []
    assert offline_pipeline.marks == []


@pytest.mark.parametrize("source", ["file:///tmp/careers", "/tmp/careers",
                                    "https://user@example.test/careers",
                                    "javascript:alert(1)"])
def test_untrusted_seed_cannot_start_plain_fetch(source, offline_pipeline):
    stores = _Stores()
    with _static_client(lambda req: pytest.fail("untrusted source was fetched")) as client:
        assert crawler.crawl_company(_company(source), Preferences(), stores,
                                     client=client, extractor=lambda *_: [_job("/jobs/123")]) == 0
    assert stores.tracking.put_calls == stores.queue.calls == []


def test_same_host_protocol_relative_does_not_change_tenant(offline_pipeline):
    stores = _Stores()
    with _static_client() as client:
        assert crawler.crawl_company(
            _company(), Preferences(), stores, client=client,
            extractor=lambda *_: [_job("//example.test/jobs/123?job_id=123")],
        ) == 1
    assert stores.tracking.put_calls[0].jd_url == (
        "https://example.test/jobs/123?job_id=123"
    )


def test_browser_listing_to_extractor_to_queue_keeps_valid_jobs(
    offline_pipeline, monkeypatch,
):
    calls = []
    external = "https://boards.example.test/postings/EXT-9?job_id=EXT-9"
    _fake_browser(monkeypatch, [
        {"title": "Software Engineer", "url": "/jobs/123?job_id=123",
         "summary": "Synthetic listing", "score": 8, "location": "Remote"},
        {"title": "Software Engineer", "url": external, "score": 9},
        {"title": "Unsafe", "url": "javascript:alert(1)"},
        {"title": "Foreign", "url": "//foreign.example.test/jobs/4"},
        {"title": "Malformed", "url": "https://example.test:bad/jobs/5"},
    ], calls)
    monkeypatch.setattr(crawler, "_render_page",
                        lambda *_: pytest.fail("browser mode used HTTP fetch"))
    monkeypatch.setattr(crawler, "sitemap_jobs",
                        lambda *_: pytest.fail("browser mode used sitemap"))
    stores = _Stores()
    assert crawler.crawl_company(_company(mode=DiscoveryMode.BROWSER),
                                 Preferences(), stores) == 2

    expected = "https://example.test/jobs/123?job_id=123"
    assert offline_pipeline.screened == [expected, external]
    assert {j.jd_url for j in stores.tracking.put_calls} == {expected, external}
    assert len(stores.queue.calls) == 2
    assert offline_pipeline.marks == [[expected, external]]
    assert len(calls) == 1  # initial fake listing read only: no posting/JD task
    assert calls[0]["kind"] == "crawl"
    assert calls[0]["urls"] == ["https://example.test/careers"]
    relative = next(j for j in stores.tracking.put_calls if j.jd_url == expected)
    assert relative.job_id == "123?job_id=123"
    assert relative.crawl_score == 8
    assert relative.pk in [body["pk"] for _, body in stores.queue.calls]


def test_browser_only_rejected_rows_do_no_downstream_browser_or_store_work(
    offline_pipeline, monkeypatch,
):
    calls = []
    _fake_browser(monkeypatch, [
        {"title": "Reject", "url": "file:///tmp/jobs/1"},
        {"title": "Reject", "url": "//foreign.example.test/jobs/2"},
        {"title": "Reject", "url": "https://example.test:bad/jobs/3"},
        {"title": "Reject", "url": ""},
    ], calls)
    stores = _Stores()
    assert crawler.crawl_company(_company(mode=DiscoveryMode.BROWSER),
                                 Preferences(), stores) == 0
    assert len(calls) == 1
    assert offline_pipeline.screened == []
    assert stores.tracking.put_calls == stores.queue.calls == []
    assert offline_pipeline.marks == []


def test_browser_invalid_source_creates_no_session(monkeypatch):
    calls = []
    _fake_browser(monkeypatch, [{"title": "Role", "url": "/jobs/123"}], calls)
    jobs, board, reason = chrome_crawl.find_jobs_sync(
        "example-co", "file:///tmp/careers", prefs=Preferences(),
    )
    assert (jobs, board) == ([], "")
    assert reason == "Invalid careers listing URL"
    assert calls == []


# Issue #22: legacy relative rows are repaired only at the existing crawl handoff.

def _seed_legacy(stores, *, job_id="123", old_url="/jobs/123?job_id=123",
                 status="error", **overrides):
    job = _job(old_url, job_id)
    row = {
        "pk": job.pk, "company": job.company, "job_id": job_id,
        "jd_url": old_url, "status": status,
        "fail_kind": "jd_reader_exhausted" if status == "error" else "",
        "jd_read_attempts": 3, "jd_read_revision": 4,
        "jd_read_retry_at": None, "jd_read_prepare_only": False,
        "jd_text": "keep JD text", "jd_read_error": "keep reader error",
        "error": "keep error", "attempts": 7, "jd_hash": "keep-hash",
        "query_id": "keep-query", "confirmation_id": "",
    }
    row.update(overrides)
    stores.tracking.seed(row, key=job.pk)
    return job.pk, deepcopy(row)


def _crawl_repair(stores, href="/jobs/123?job_id=123", *,
                  source="https://example.test/careers", handler=None,
                  job_id="123"):
    with _static_client(handler) as client:
        return crawler.crawl_company(
            _company(source), Preferences(), stores, client=client,
            extractor=lambda *_: [_job(href, job_id)],
        )


@pytest.mark.parametrize("seen_hit", [False, True])
def test_plain_recrawl_repairs_exhausted_once_without_new_discovery(
    offline_pipeline, seen_hit,
):
    from agent.run import _reader_eligible

    stores = _Stores()
    pk, before = _seed_legacy(stores)
    candidate = "https://example.test/jobs/123?job_id=123"
    if seen_hit:
        offline_pipeline.urls.add(candidate)
    assert _crawl_repair(stores) == 0
    repaired = stores.tracking.get(pk)
    assert repaired == {
        **{k: v for k, v in before.items() if k != "fail_kind"},
        "jd_url": candidate, "status": "found",
        "jd_read_attempts": 0, "jd_read_revision": 5,
    }
    assert _reader_eligible(repaired)
    assert stores.queue.calls == []
    assert offline_pipeline.marks == offline_pipeline.emitted == []
    assert len(stores.tracking.repair_calls) == 1
    assert len(stores.tracking.put_calls) == (0 if seen_hit else 1)

    snapshot = deepcopy(repaired)
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk) == snapshot
    assert len(stores.tracking.repair_calls) == 1
    assert offline_pipeline.urls == ({candidate} if seen_hit else set())
    assert offline_pipeline.marks == offline_pipeline.emitted == []
    assert stores.queue.calls == []


def test_found_dormant_reader_repairs_without_clearing_other_reader_fields(
    offline_pipeline,
):
    stores = _Stores()
    pk, before = _seed_legacy(
        stores, status="found", fail_kind="", jd_read_attempts=2,
        jd_read_retry_at="2020-01-01T00:00:00+00:00",
    )
    assert _crawl_repair(stores) == 0
    repaired = stores.tracking.get(pk)
    assert repaired == {
        **before, "jd_url": "https://example.test/jobs/123?job_id=123",
        "jd_read_attempts": 0, "jd_read_revision": 5,
    }
    assert repaired["jd_read_retry_at"] == before["jd_read_retry_at"]
    assert repaired["fail_kind"] == ""
    assert stores.queue.calls == offline_pipeline.marks == []


def test_browser_actual_listing_recrawl_keeps_derived_identity(
    offline_pipeline, monkeypatch,
):
    calls = []
    _fake_browser(monkeypatch, [
        {"title": "Software Engineer", "url": "/jobs/123?job_id=123",
         "summary": "Synthetic listing", "score": 8},
    ], calls)
    stores = _Stores()
    browser_id = "123?job_id=123"  # chrome_crawl derives last segment WITH query.
    pk, before = _seed_legacy(stores, job_id=browser_id)
    assert crawler.crawl_company(_company(mode=DiscoveryMode.BROWSER),
                                 Preferences(), stores) == 0
    assert stores.tracking.get(pk) == {
        **{k: v for k, v in before.items() if k != "fail_kind"},
        "jd_url": "https://example.test/jobs/123?job_id=123",
        "status": "found", "jd_read_attempts": 0, "jd_read_revision": 5,
    }
    assert pk == "example-co#123?job_id=123"
    assert len(calls) == 1 and calls[0]["kind"] == "crawl"
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []
    assert crawler.crawl_company(_company(mode=DiscoveryMode.BROWSER),
                                 Preferences(), stores) == 0
    assert len(calls) == 2
    assert len(stores.tracking.repair_calls) == 1


def test_plain_verified_http_https_upgrade_repairs_only_with_response_proof(
    offline_pipeline,
):
    def redirect(req):
        if req.url.scheme == "http":
            return httpx.Response(301, headers={"Location": "https://example.test/careers"})
        return httpx.Response(200, text="<html>synthetic</html>")

    stores = _Stores()
    pk, _ = _seed_legacy(stores)
    assert _crawl_repair(stores, "https://example.test/jobs/123?job_id=123",
                         source="http://example.test/careers") == 0
    assert stores.tracking.get(pk)["jd_url"].startswith("/")
    assert _crawl_repair(stores, "https://example.test/jobs/123?job_id=123",
                         source="http://example.test/careers", handler=redirect) == 0
    assert stores.tracking.get(pk)["jd_url"] == "https://example.test/jobs/123?job_id=123"
    assert stores.queue.calls == offline_pipeline.marks == []


@pytest.mark.parametrize("status", [
    "applied", "applied_manual", "submitting", "tailoring", "tailored",
    "failed", "job_gone", "needs_human", "skipped", "capped", "unknown",
])
def test_recrawl_refuses_protected_and_other_statuses(offline_pipeline, status):
    stores = _Stores()
    pk, before = _seed_legacy(stores, status=status)
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk) == before
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


@pytest.mark.parametrize(("field", "value"), [
    ("apply_requested_at", "2030-01-01T00:00:00Z"),
    ("confirmation_id", "synthetic-confirmation"),
    ("applied_at", "2020-01-01"),
    ("possible_submission", True),
    ("submit_hold", True),
    ("submit_hold_at", "2020-01-01"),
    ("submit_hold_reason", "pending"),
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
    ("submit_receipt", "synthetic"),
    ("gate_reason", "submit_uncertain"),
    ("jd_read_retry_at", "2099-01-01T00:00:00Z"),
    ("jd_read_retry_at", "invalid deadline"),
    ("fail_kind", "uncertain"),
])
def test_recrawl_refuses_each_evidence_marker(offline_pipeline, field, value):
    stores = _Stores()
    pk, before = _seed_legacy(stores, **{field: value})
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk) == before
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


@pytest.mark.parametrize("old_url", [
    "", " ", "javascript:alert(1)", "ftp://example.test/jobs/123",
    "http:jobs/123", "//foreign.example.test/jobs/123",
    "//example.test/jobs/123", "/jobs\\123", "/jobs/\n123",
    "http://example.test/jobs/123", "https://example.test/jobs/123",
    "https://boards.example.test/jobs/123",
])
def test_recrawl_never_overwrites_invalid_or_absolute_stored_url(
    offline_pipeline, old_url,
):
    stores = _Stores()
    pk, before = _seed_legacy(stores, old_url=old_url)
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk) == before
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


@pytest.mark.parametrize("candidate", [
    "javascript:alert(1)", "file:///tmp/post", "ftp://example.test/jobs/123",
    "data:text/plain,xyz", "https://other.test/jobs/123",
    "https://other.example.test/jobs/123",
    "https://example.test:8443/jobs/123", "http://example.test/jobs/123",
    "https://example.test:bogus/jobs/123", "https://example.test/jobs\\123",
    "https://user@example.test/jobs/123", "", "https:///jobs/123",
])
def test_untrusted_absolute_replacement_refused_at_repair_handoff(
    offline_pipeline, candidate,
):
    stores = _Stores()
    pk, before = _seed_legacy(stores)
    # The repair handoff itself must guard even an injected provider which
    # failed to normalize; the normal new-discovery path remains separate.
    assert crawler._enqueue(_company(), [_job(candidate)], stores) == 0
    assert stores.tracking.get(pk) == before
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


@pytest.mark.parametrize(("change", "value"), [
    ("company", "other-co"), ("job_id", "another-id"),
    ("pk", "other-co#123"), ("jd_url", "https://example.test/jobs/old"),
])
def test_recrawl_refuses_inconsistent_existing_identity_or_url(
    offline_pipeline, change, value,
):
    stores = _Stores()
    pk, _ = _seed_legacy(stores)
    # Corrupt only the persisted row at the original primary key. Passing
    # job_id into _seed_legacy changes the job's identity and correctly allows
    # an unrelated *new* discovery; that is not the row-mismatch scenario.
    stores.tracking.persisted[pk][change] = value
    before = stores.tracking.get(pk)
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk) == before
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


def test_recrawl_missing_row_keeps_existing_seen_new_job_contract(offline_pipeline):
    stores = _Stores()
    candidate = "https://example.test/jobs/123?job_id=123"
    offline_pipeline.urls.add(candidate)
    assert _crawl_repair(stores) == 0
    assert stores.tracking.persisted == {}
    assert stores.tracking.put_calls == []
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []
    offline_pipeline.urls.clear()
    assert _crawl_repair(stores) == 1
    assert stores.tracking.get("example-co#123")["jd_url"] == candidate
    assert len(stores.queue.calls) == 1
    assert offline_pipeline.marks == [[candidate]]


@pytest.mark.parametrize(("field", "value"), [
    ("status", "applied_manual"), ("apply_requested_at", "2020-01-01"),
    ("confirmation_id", "synthetic"), ("submit_hold", True),
    ("jd_read_revision", 999), ("jd_url", "/other/posting"),
])
def test_recrawl_repair_refuses_change_between_read_and_commit(
    offline_pipeline, field, value,
):
    stores = _Stores()
    pk, _ = _seed_legacy(stores)
    def competing_write(key):
        stores.tracking.persisted[key][field] = value
    stores.tracking.before_repair = competing_write
    assert _crawl_repair(stores) == 0
    assert stores.tracking.get(pk)[field] == value
    assert stores.queue.calls == offline_pipeline.marks == offline_pipeline.emitted == []


# Issue #138: real synthetic XML producer, never a fabricated normalized record.
_GH_CAREERS = "https://careers.example-co.test/openings"
_GH_SUFFIX = _GH_CAREERS + "/job/731205"
_GH_HOSTED = "https://job-boards.greenhouse.io/example-board/jobs/731205"


def _gh_fixture(monkeypatch, tmp_path, *, urls=None, published="2026-10-18T00:00:00Z"):
    from core import config as core_config

    (tmp_path / "watchlist.yaml").write_text("""companies:
  - name: example-co
    careers_url: https://careers.example-co.test/openings
    ats: greenhouse
    board: example-board
    discovery: crawl
    mode: gated
""")
    monkeypatch.setattr(core_config, "get_settings",
                        lambda: SimpleNamespace(config_dir=str(tmp_path)))
    state = SimpleNamespace(urls=urls if urls is not None else [_GH_SUFFIX],
                            target=_GH_HOSTED, published=published, calls=[])
    def handle(req):
        state.calls.append(str(req.url))
        if req.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{
                "id": 731205, "title": "Staff Platform Engineer",
                "absolute_url": state.target, "content": "Synthetic role",
                "location": {"name": "Remote"}, "first_published": state.published,
            }]})
        if req.url.path == "/sitemap.xml":
            xml = "<urlset>" + "".join(
                f"<url><loc>{url}</loc><lastmod>2035-10-21</lastmod></url>"
                for url in state.urls) + "</urlset>"
            return httpx.Response(200, text=xml)
        if req.url.path == "/openings":
            return httpx.Response(200, text="<html>Partial employer listing</html>")
        return httpx.Response(404)
    company = CompanyConfig(
        name="example-co", careers_url=_GH_CAREERS, ats="greenhouse",
        board="example-board", discovery=DiscoveryMode.CRAWL,
    )
    return company, state, httpx.Client(transport=httpx.MockTransport(handle))


def test_verified_greenhouse_suffix_from_xml_survives_partial_html_and_screens(
    offline_pipeline, monkeypatch, tmp_path,
):
    company, state, client = _gh_fixture(monkeypatch, tmp_path)
    stores = _Stores()
    with client:
        count = crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda html, company: [_job("/openings/job/222", "222")],
        )
    assert count == 2
    assert len(stores.queue.calls) == 2
    exact = next(j for j in stores.tracking.put_calls if j.job_id == "731205")
    assert (exact.pk, exact.jd_url, exact.ats, exact.greenhouse_board) == (
        "example-co#731205", _GH_SUFFIX, "greenhouse", "example-board")
    assert (exact.title, exact.location, exact.posted_at) == (
        "Staff Platform Engineer", "Remote", "2026-10-18T00:00:00Z")
    assert _GH_SUFFIX in offline_pipeline.screened
    assert _GH_SUFFIX in offline_pipeline.urls
    assert any("sitemap.xml" in call for call in state.calls)
    assert any("boards-api.greenhouse.io" in call for call in state.calls)


def test_verified_suffix_first_published_not_sitemap_lastmod_is_freshness(
    offline_pipeline, monkeypatch, tmp_path,
):
    company, _, client = _gh_fixture(
        monkeypatch, tmp_path, published="2018-01-01T00:00:00Z")
    stores = _Stores()
    monkeypatch.setattr(crawler, "_age_limit", lambda _: 48)
    with client:
        assert crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda *_: []) == 0
    assert stores.tracking.put_calls == []
    assert stores.queue.calls == []


@pytest.mark.parametrize("seen_hit", [False, True])
def test_suffix_same_pk_relative_legacy_collision_has_zero_row_repairs(
    offline_pipeline, monkeypatch, tmp_path, seen_hit,
):
    company, _, client = _gh_fixture(monkeypatch, tmp_path)
    stores = _Stores()
    row = dict(pk="example-co#731205", company="example-co", job_id="731205",
               jd_url="/openings/legacy", status="error", jd_read_attempts=8,
               jd_read_revision=12, apply_requested_at="2026-10-20",
               approval_status="pending", attempts=6, note="unchanged")
    stores.tracking.seed(row)
    before = deepcopy(stores.tracking.get(row["pk"]))
    if seen_hit:
        offline_pipeline.urls.add(_GH_SUFFIX)
    with client:
        assert crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda *_: []) == 0
    assert stores.tracking.get(row["pk"]) == before
    assert stores.tracking.repair_calls == []
    assert stores.queue.calls == []
    assert not offline_pipeline.marks


def test_mixed_sources_conflicting_same_pk_or_cross_pk_do_not_choose_winner(
    offline_pipeline, monkeypatch, tmp_path,
):
    company, _, client = _gh_fixture(monkeypatch, tmp_path)
    stores = _Stores()
    with client:
        assert crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda *_: [_job("/openings/other?gh_jid=731205", "731205")],
        ) == 0
    assert stores.tracking.put_calls == []
    assert stores.queue.calls == []
    # A different PK for the same observed employer URL is a manual identity
    # conflict, not a license for cross-PK merge or replacement.
    _, _, client = _gh_fixture(monkeypatch, tmp_path)
    with client:
        assert crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda *_: [_job(_GH_SUFFIX, "old-slug")],
        ) == 0
    assert stores.tracking.put_calls == []


@pytest.mark.parametrize("candidate", [
    "https://foreign.test/openings/job/731205",
    "http://careers.example-co.test/openings/job/731205",
    "https://careers.example-co.test/openings/benefits/731205",
    "https://careers.example-co.test/openings/job/731205/extra",
    "https://careers.example-co.test/openings/job/731205?x=1",
    "https://careers.example-co.test/openings/job/%3731205",
    "https://careers.example-co.test/openings/../job/731205",
    "https://careers.example-co.test:443/openings/job/731205",
    "https://user@careers.example-co.test/openings/job/731205",
])
def test_suffix_rejects_noncanonical_sitemap_identity_without_writes(
    offline_pipeline, monkeypatch, tmp_path, candidate,
):
    company, _, client = _gh_fixture(monkeypatch, tmp_path, urls=[candidate])
    stores = _Stores()
    with client:
        assert crawler.crawl_company(
            company, Preferences(), stores, client=client,
            extractor=lambda *_: []) == 0
    assert stores.tracking.put_calls == []
    assert stores.queue.calls == []


@pytest.mark.parametrize(("browser_id", "browser_url"), [
    ("731205", _GH_SUFFIX),  # same PK, unproved browser ATS
    ("731205", _GH_CAREERS + "/other?gh_jid=731205"),  # same PK, other URL
    ("legacy-slug", _GH_SUFFIX),  # same URL, different PK
])
def test_partial_html_browser_fallback_cannot_replace_verified_suffix(
    offline_pipeline, monkeypatch, tmp_path, browser_id, browser_url,
):
    """Irrelevant feed title must not let a later browser claim win by URL."""
    company, _, client = _gh_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(crawler, "_default_extractor",
                        lambda *_: [_job("/openings/job/999", "999")])
    monkeypatch.setattr(
        crawler, "relevant",
        lambda jobs, prefs: [j for j in jobs if j.title.startswith("Principal")],
    )
    unproved = JobRecord(
        company="example-co", job_id=browser_id, title="Principal Browser Claim",
        jd_url=browser_url, jd_text="Synthetic browser result", ats="crawl",
    )
    unrelated = JobRecord(
        company="example-co", job_id="unrelated", title="Principal Separate Role",
        jd_url=_GH_CAREERS + "/job/unrelated-role",
        jd_text="Synthetic browser result", ats="crawl",
    )
    monkeypatch.setattr(crawler, "_browser_extract",
                        lambda *_: [unproved, unrelated])
    stores = _Stores()
    with client:
        assert crawler.crawl_company(company, Preferences(), stores, client=client) == 1
    assert [j.job_id for j in stores.tracking.put_calls] == ["unrelated"]
    assert list(stores.tracking.persisted) == ["example-co#unrelated"]
    assert len(stores.queue.calls) == 1
    assert stores.tracking.repair_calls == []
    assert offline_pipeline.marks == [[unrelated.jd_url]]


def test_sitemap_same_pk_conflict_invalidates_earlier_screened_suffix(
    offline_pipeline, monkeypatch, tmp_path,
):
    """A later ordinary sitemap collision cannot leave an old screened winner."""
    company, _, client = _gh_fixture(
        monkeypatch, tmp_path,
        urls=[_GH_SUFFIX, _GH_SUFFIX + "/staff-platform-engineer"],
    )
    monkeypatch.setattr(crawler, "_default_extractor", lambda *_: [])
    monkeypatch.setattr(crawler, "sitemap_jobs",
                        lambda url, name: sitemap.sitemap_jobs(url, name, client))
    monkeypatch.setattr(crawler, "_browser_extract", lambda *_: [])
    stores = _Stores()
    with client:
        assert crawler.crawl_company(company, Preferences(), stores, client=client) == 0
    assert stores.tracking.put_calls == stores.queue.calls == []
    assert stores.tracking.persisted == {}
    assert offline_pipeline.marks == offline_pipeline.emitted == []


@pytest.mark.parametrize("browser_id", ["731205", "legacy-slug"])
def test_empty_html_sitemap_conflict_survives_browser_fallback(
    offline_pipeline, monkeypatch, tmp_path, browser_id,
):
    """The early sitemap-to-browser fallback cannot revive a disputed PK."""
    company, _, client = _gh_fixture(
        monkeypatch, tmp_path,
        urls=[_GH_SUFFIX, _GH_SUFFIX + "/staff-platform-engineer"],
    )
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "")
    monkeypatch.setattr(crawler, "sitemap_jobs",
                        lambda url, name: sitemap.sitemap_jobs(url, name, client))
    unproved = JobRecord(
        company="example-co", job_id=browser_id, title="Browser Claim",
        jd_url=_GH_SUFFIX, jd_text="Synthetic browser result", ats="crawl",
    )
    unrelated = JobRecord(
        company="example-co", job_id="unrelated", title="Separate Role",
        jd_url=_GH_CAREERS + "/job/unrelated-role",
        jd_text="Synthetic browser result", ats="crawl",
    )
    monkeypatch.setattr(crawler, "_browser_extract",
                        lambda *_: [unproved, unrelated])
    stores = _Stores()
    with client:
        assert crawler.crawl_company(company, Preferences(), stores, client=client) == 1
    assert [j.job_id for j in stores.tracking.put_calls] == ["unrelated"]
    assert list(stores.tracking.persisted) == ["example-co#unrelated"]
    assert len(stores.queue.calls) == 1
    assert stores.tracking.repair_calls == []
    assert offline_pipeline.marks == [[unrelated.jd_url]]
