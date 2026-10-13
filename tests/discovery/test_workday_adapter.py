"""Workday CXS responses carry relative paths; only the configured board may supply the URL host/site."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from discovery.adapters.workday import WorkdayAdapter
from discovery.watchlist import CompanyConfig

_BOARD = "https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs"
_URL = "https://wd.example.invalid/fake-site/job/Example/Platform_ABC-17"
_PATH = "/job/Example/Platform_ABC-17"


def _company(board: str = _BOARD) -> CompanyConfig:
    return CompanyConfig(name="example-co", ats="workday", board=board)


@pytest.mark.parametrize("board", [_BOARD, _BOARD + "/"])
def test_exact_absolute_url_and_fields_ignore_conflicting_payload_urls(board: str):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            "total": 1,
            "jobPostings": [{
                "externalPath": _PATH,
                "title": "Synthetic platform role",
                "locationsText": "Example City",
                "host": "evil.example.invalid",
                "site": "evil-site",
                "url": "https://evil.example.invalid/job/Elsewhere/X",
            }],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = WorkdayAdapter().fetch(_company(board), client)

    assert len(jobs) == 1
    job = jobs[0]
    assert (job.jd_url, job.job_id) == (_URL, "Platform_ABC-17")
    assert (job.company, job.title, job.jd_text, job.location, job.ats) == (
        "example-co", "Synthetic platform role", "Synthetic platform role", "Example City", "workday",
    )
    assert len(calls) == 1
    assert str(calls[0].url) == _BOARD  # the existing POST endpoint is unchanged
    assert json.loads(calls[0].content) == {
        "appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "",
    }


@pytest.mark.parametrize("board", [
    "http://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs",
    "https:///wday/cxs/fake-tenant/fake-site/jobs",
    "https://wd.example.invalid/wday/cxs/fake-tenant/jobs",
    "https://wd.example.invalid/wday/cxs//fake-site/jobs",
    "https://user:password@wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs",
    "https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs?filter=1",
    "https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs#fragment",
    "https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs?",
    " https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs",
    "https://wd.example.invalid/wday/cxs/fa\nke-tenant/fake-site/jobs",
    "https://wd.example.invalid/wday/cxs/fake-tenant/fake-site/jobs/extra",
    "https://wd.example.invalid/wday/cxs/fake-tenant/../jobs",
    "https://wd.example.invalid/wday/cxs/fake-tenant/%2e%2e/jobs",
    "https://wd.example.invalid:wrong/wday/cxs/fake-tenant/fake-site/jobs",
    "https://[invalid/wday/cxs/fake-tenant/fake-site/jobs",
])
def test_invalid_board_warns_and_returns_before_any_post(board: str):
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json={
            "total": 1, "jobPostings": [{"externalPath": _PATH}],
        })

    with patch("discovery.adapters.workday.log.warning") as warning:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = WorkdayAdapter().fetch(_company(board), client)

    assert jobs == []
    assert posts == []
    warning.assert_called_once()


def test_url_like_and_traversal_paths_cannot_yield_postings():
    bad_paths = [
        "//evil.example.invalid/job/Elsewhere/X",
        "https://evil.example.invalid/job/Elsewhere/X",
        "job/Example/Platform_ABC-17",
        "/job//Platform_ABC-17",
        "/job/Example/",
        "/job/./Platform_ABC-17",
        "/job/../Platform_ABC-17",
        "/job/Example/..",
        "/job/Example/%2e%2e",
        "/job/Example/%252e%252e",
        "/job/Example/%2F%2Fevil.example.invalid",
        "/job/Example/Platform_ABC-17?redirect=1",
        "/job/Example/Platform_ABC-17#fragment",
        "/job/Example\\Platform_ABC-17",
        "/job/Example/https:evil.example.invalid",
        "/job/Example/Platform_ABC-17/extra",
    ]
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json={
            "total": len(bad_paths) + 1,
            "jobPostings": [
                {"externalPath": path, "title": "bad"} for path in bad_paths
            ] + [{"externalPath": _PATH, "title": "good"}],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = WorkdayAdapter().fetch(_company(), client)
    assert [job.jd_url for job in jobs] == [_URL]
    assert [job.title for job in jobs] == ["good"]
    assert len(posts) == 1


def test_pagination_still_uses_twenty_posting_offsets():
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        return httpx.Response(200, json={
            "total": 21,
            "jobPostings": [{"externalPath": f"/job/Example/Platform_{offset}"}],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = WorkdayAdapter().fetch(_company(), client)
    assert offsets == [0, 20]
    assert [job.job_id for job in jobs] == ["Platform_0", "Platform_20"]
    assert [job.jd_url for job in jobs] == [
        "https://wd.example.invalid/fake-site/job/Example/Platform_0",
        "https://wd.example.invalid/fake-site/job/Example/Platform_20",
    ]
