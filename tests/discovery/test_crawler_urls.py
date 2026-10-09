"""Trusted discovery hrefs: fake real HTTP and browser listing seams, no live services."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from core.models import DiscoveryMode, JobRecord
from discovery import chrome_crawl, crawler
from discovery.watchlist import CompanyConfig, Preferences


class _Tracking:
    r = None

    def __init__(self):
        self.rows = {}
        self.put_calls = []

    def put_new(self, job):
        self.put_calls.append(job.model_copy(deep=True))
        if job.pk in self.rows:
            return False
        self.rows[job.pk] = job.model_copy(deep=True)
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
    monkeypatch.setattr(events, "emit", lambda *args, **kw: None)

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
    return SimpleNamespace(screened=screened, urls=urls, marks=marks)


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
