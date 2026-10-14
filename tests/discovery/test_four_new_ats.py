"""Issue #43: offline, synthetic fixtures for four fixed-authority ATS feeds."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from discovery.adapters import ADAPTERS
from discovery import freshness
from discovery.resolver import detect_from_url
from discovery.watchlist import CompanyConfig, Preferences


def company(ats, board="example-co", careers_url=""):
    return CompanyConfig(name="Test User", ats=ats, board=board, careers_url=careers_url)


def xml_response(body, status=200, content_type="application/xml"):
    return httpx.Response(status, content=body, headers={"Content-Type": content_type})


def personio_xml(positions=None):
    if positions is None:
        positions = ["<position><id>12345</id><name>Backend Engineer</name>"
                     "<office>Berlin</office><createdAt>2026-10-01T12:00:00+0200</createdAt>"
                     "</position>"]
    return ("<workzag-jobs>" + "".join(positions) + "</workzag-jobs>").encode()


def rss_item(link="https://example-co.teamtailor.com/jobs/backend-123",
             title="Backend Engineer", guid="duplicate"):
    return (f"<item><title>{title}</title><link>{link}</link><guid>{guid}</guid>"
            "<pubDate>Tue, 13 Oct 2026 09:00:00 +0000</pubDate>"
            "<tt:locations><tt:location>Berlin</tt:location>"
            "<tt:location>Remote - EU</tt:location></tt:locations>"
            "<tt:remote>true</tt:remote></item>")


def rss(items=None):
    if items is None:
        items = [rss_item()]
    return ('<rss xmlns:tt="https://example.invalid/tt"><channel>' +
            "".join(items) + "</channel></rss>").encode()


def recruitee_row(**overrides):
    return dict(id=42, title="Backend Engineer",
                careers_url="https://example-co.recruitee.com/o/backend-123",
                published_at="2026-10-13 09:00:00 UTC",
                locations=[{"name": "London"}, {"name": "Remote - EU"}],
                remote=True, **overrides)


def workable_row(**overrides):
    return dict(shortcode="SHORT1", title="Backend Engineer",
                url="https://apply.workable.com/j/SHORT1",
                published_on="2026-10-13",
                locations=[{"name": "London"}, {"name": "Secret", "hidden": True}],
                remote=True, **overrides)


def fixture_for(ats):
    if ats == "personio":
        return "https://example-co.jobs.personio.de/xml", xml_response(personio_xml())
    if ats == "teamtailor":
        return ("https://example-co.teamtailor.com/jobs.rss",
                xml_response(rss(), content_type="application/rss+xml"))
    if ats == "recruitee":
        return ("https://example-co.recruitee.com/api/offers/",
                httpx.Response(200, json={"offers": [recruitee_row()]}))
    return ("https://apply.workable.com/api/v1/widget/accounts/example-co",
            httpx.Response(200, json={"jobs": [workable_row()]}))


def fetch_fixture(ats, response=None, *, board="example-co", careers_url="",
                  fail_if_called=False):
    expected, default_response = fixture_for(ats)
    calls = []

    def callback(request):
        calls.append(request)
        if fail_if_called:
            pytest.fail("HTTP should not have been called")
        assert str(request.url) == expected
        return response if response is not None else default_response

    with httpx.Client(transport=httpx.MockTransport(callback),
                      follow_redirects=True) as client:
        original_get = client.get
        args = []

        def get_spy(*a, **kw):
            args.append((a, kw))
            return original_get(*a, **kw)

        client.get = get_spy
        result = ADAPTERS[ats].fetch(company(ats, board, careers_url), client)
    assert len(calls) == len(args) == 1
    assert args[0][1]["follow_redirects"] is False
    assert args[0][1]["timeout"] == 20
    assert str(calls[0].url) == expected
    return result


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
def test_registration_manual_empty_careers_and_fixed_unauthenticated_get(ats):
    assert ats in ADAPTERS
    got = fetch_fixture(ats)
    assert len(got) == 1
    job = got[0]
    assert (job.company, job.ats, job.title) == ("Test User", ats, "Backend Engineer")
    assert job.job_id and len(job.job_id) <= 128 and job.jd_url.startswith("https://")
    assert "example-co" in job.jd_url
    assert job.location and "\n" not in job.title and "Berlin" in job.location if ats in (
        "personio", "teamtailor") else "London" in job.location


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
@pytest.mark.parametrize("token", [
    "", "/", "\\", "../foo", "foo.bar", "foo:443", "foo?x=1",
    "a#fragment", "user@host", "%2f", "%2e", "white space",
    "foo\nbar", "a" * 64, "examрle-co", "ｅxample-co",
])
def test_untrusted_manual_token_is_rejected_before_any_request(ats, token):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("invalid board made an HTTP request")))
    with client, pytest.raises(ValueError, match="board/account"):
        ADAPTERS[ats].fetch(company(ats, token), client)


@pytest.mark.parametrize("ats,other_vendor_url", [
    ("personio", "https://other.jobs.personio.com/xml"),
    ("teamtailor", "https://other.teamtailor.com/jobs.rss"),
    ("recruitee", "https://other.recruitee.com/api/offers/"),
    ("workable", "https://apply.workable.com/other"),
])
def test_conflicting_vendor_careers_urls_are_rejected_without_io(ats, other_vendor_url):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("conflicting origin caused HTTP I/O")))
    with client, pytest.raises(ValueError, match="conflicting"):
        ADAPTERS[ats].fetch(company(ats, careers_url=other_vendor_url), client)


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
@pytest.mark.parametrize("bad", [
    "http://careers.example.com/jobs", "https://user@careers.example.com/jobs",
    "https://careers.example.com:8443/jobs", "https://careers.example.com/a/../jobs",
    "https://careers.example.com/%2e%2e/jobs", "https://careers.example.com/jobs?q=hi",
])
def test_unsafe_unrelated_careers_url_does_not_trigger_fallback(ats, bad):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("unsafe origin caused HTTP I/O")))
    with client, pytest.raises(ValueError, match="unsafe"):
        ADAPTERS[ats].fetch(company(ats, careers_url=bad), client)


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
def test_safe_unrelated_careers_origin_uses_pinned_vendor_base(ats):
    assert len(fetch_fixture(ats, careers_url="https://careers.example.org/roles")) == 1


def test_personio_de_and_com_suffix_manual_default_and_created_at_is_not_publication():
    assert (fetch_fixture("personio")[0].posted_at == "")
    body = personio_xml()
    reqs = []

    def response(request):
        reqs.append(str(request.url))
        return xml_response(body)

    url = "https://EXAMPLE-CO.jobs.personio.com:443/job/12345"
    with httpx.Client(transport=httpx.MockTransport(response)) as c:
        jobs = ADAPTERS["personio"].fetch(company("personio", careers_url=url), c)
    assert reqs == ["https://example-co.jobs.personio.com/xml"]
    assert jobs[0].jd_url == "https://example-co.jobs.personio.com/job/12345"
    assert jobs[0].posted_at == ""
    invalid_rows = [
        "<position><name>Title</name></position>",
        "<position><id>../123</id><name>Title</name></position>",
        f"<position><id>{'9'*129}</id><name>Title</name></position>",
    ]
    assert len(fetch_fixture("personio", xml_response(personio_xml(
        invalid_rows + ["<position><id>1</id><name>Valid</name></position>"])))) == 1


@pytest.mark.parametrize("url,match", [
    ("https://EXAMPLE-CO.jobs.personio.de/xml", ("personio", "example-co")),
    ("https://example-co.jobs.personio.com/job/1234", ("personio", "example-co")),
    ("https://example-co.teamtailor.com/jobs/backend-123", ("teamtailor", "example-co")),
    ("https://example-co.recruitee.com/o/backend-123", ("recruitee", "example-co")),
    ("https://apply.workable.com/Account_X", ("workable", "Account_X")),
    ("https://apply.workable.com/Account_X/j/SHORT1", ("workable", "Account_X")),
    ("https://apply.workable.com/api/v1/widget/accounts/Account_X", ("workable", "Account_X")),
])
def test_strict_direct_resolver_positive(url, match):
    m = detect_from_url(url)
    assert (m.ats, m.board) == match


@pytest.mark.parametrize("url", [
    "https://apply.workable.com/j/SHORT1",
    "https://apply.workable.com/api/v1/widget/accounts/",
    "https://apply.workable.com/api",
    "https://example-co.teamtailor.com.invalid.example/jobs/a",
    "https://user@example-co.teamtailor.com/jobs/a",
    "http://example-co.recruitee.com/o/a",
    "https://example-co.recruitee.com:8443/o/a",
    "https://example-co.jobs.personio.de/a/../job/1234",
    "https://example-co.teamtailor.com/jobs/%2e%2e/a",
    "https://example-co.recruitee.com/o/../o/a",
    "https://apply.workable.com/acme/j/SHORT?foo",
    "https://apply.workable.com/Account%2fmore",
    "https://example-co.teamtailor.com/jobs/a#anchor",
    "https://example-co.recruitee.com/o/a/extra",
])
def test_new_resolver_rejects_unsafe_direct_urls(url):
    assert detect_from_url(url) is None


def test_teamtailor_url_identity_and_raw_100_warning(monkeypatch):
    from discovery.adapters import teamtailor
    warnings = []
    monkeypatch.setattr(teamtailor.log, "warning", lambda *args: warnings.append(args))
    items = [
        rss_item("https://EXAMPLE-CO.teamtailor.com:443/jobs/Backend-123", guid="same"),
        rss_item("https://example-co.teamtailor.com/jobs/Backend-123", guid="other"),
        rss_item("https://example-co.teamtailor.com/jobs/Backend-234", guid="same"),
    ]
    results = fetch_fixture("teamtailor", xml_response(rss(items)))
    assert len(results) == 2
    assert len({r.job_id for r in results}) == 2
    assert results[0].jd_url == "https://example-co.teamtailor.com/jobs/Backend-123"
    assert results[0].job_id == fetch_fixture("teamtailor", xml_response(rss(
        [rss_item("https://example-co.teamtailor.com/jobs/Backend-123",
                  guid="")])))[0].job_id
    assert not warnings
    fetch_fixture("teamtailor", xml_response(rss(
        [rss_item(f"https://example-co.teamtailor.com/jobs/post-{n}") for n in range(100)])))
    assert any("truncated" in args[0] for args in warnings)


@pytest.mark.parametrize("ats,raw,ctype", [
    ("personio", b"<workzag-jobs/>", "application/xml"),
    ("teamtailor", b"<rss><channel/></rss>", "application/rss+xml"),
    ("recruitee", {"offers": []}, "application/json"),
    ("workable", {"jobs": []}, "application/json"),
])
def test_valid_empty_feeds_are_distinguishable_from_drift(ats, raw, ctype):
    response = (xml_response(raw, content_type=ctype) if isinstance(raw, bytes)
                else httpx.Response(200, json=raw))
    assert fetch_fixture(ats, response) == []


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
def test_nonempty_wholly_invalid_feed_raises(ats):
    if ats == "personio":
        response = xml_response(personio_xml(
            ["<position><id>bad</id><name>Engineer</name></position>"]))
    elif ats == "teamtailor":
        response = xml_response(rss([rss_item(link="https://other.teamtailor.com/jobs/a")]))
    elif ats == "recruitee":
        response = httpx.Response(200, json={"offers": [{"id": 2, "title": ""}]})
    else:
        response = httpx.Response(200, json={"jobs": [{"shortcode": "bad/foo"}]})
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as c:
        with pytest.raises(ValueError, match="no valid jobs"):
            ADAPTERS[ats].fetch(company(ats), c)


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_status_failures_are_not_empty_success_and_do_not_redirect(ats, status):
    requests = []

    def response(req):
        requests.append(req)
        return httpx.Response(status, headers={"Location": "https://other.example/"})

    with httpx.Client(transport=httpx.MockTransport(response),
                      follow_redirects=True) as c:
        with pytest.raises(ValueError, match="HTTP"):
            ADAPTERS[ats].fetch(company(ats), c)
    assert len(requests) == 1


@pytest.mark.parametrize("ats", ["personio", "teamtailor", "recruitee", "workable"])
def test_wrong_media_or_schema_is_error(ats):
    body = b"<html><body>Wrong page</body></html>"
    for content_type in ("text/html", "", "application/json" if ats in (
            "personio", "teamtailor") else "application/xml"):
        with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=body,
                                       headers={"Content-Type": content_type}))) as c:
            with pytest.raises(ValueError):
                ADAPTERS[ats].fetch(company(ats), c)


@pytest.mark.parametrize("ats", ["teamtailor", "recruitee"])
@pytest.mark.parametrize("bad_path", [
    "/jobs/../jobs/backend-123", "/o/../o/backend-123",
    "/jobs/%2e%2e/a", "/o/%2f/a",
    "/jobs/a/more", "/o/a/more", "/jobs.rss", "/api/offers/",
    "/login", "/",
])
def test_teamtailor_and_recruitee_non_posting_paths(ats, bad_path, monkeypatch):
    from discovery import crawler
    def should_not_normalize(*args, **kwargs):
        pytest.fail("unsafe raw source reached the normalizer")
    monkeypatch.setattr(crawler, "_trusted_posting_url", should_not_normalize)
    link = f"https://example-co.{ats}.com{bad_path}"
    if ats == "teamtailor":
        response = xml_response(rss([rss_item(link=link)]))
    else:
        row = recruitee_row()
        row["careers_url"] = link
        response = httpx.Response(200, json={"offers": [row]})
    with httpx.Client(transport=httpx.MockTransport(lambda req: response)) as c:
        with pytest.raises(ValueError, match="no valid jobs"):
            ADAPTERS[ats].fetch(company(ats), c)


@pytest.mark.parametrize("ats", ["teamtailor", "recruitee"])
@pytest.mark.parametrize("source", [
    "/jobs/backend-123", "//example-co.teamtailor.com/jobs/backend-123",
    "https://evil.example/jobs/backend-123",
    "https://user@example-co.teamtailor.com/jobs/backend-123",
    "https://example-co.teamtailor.com:8443/jobs/backend-123",
    "https://example-co.teamtailor.com/jobs/backend-123?x=y",
])
def test_relative_foreign_and_unsafe_raw_source_rejected_before_normalizer(
        ats, source, monkeypatch):
    from discovery import crawler
    monkeypatch.setattr(crawler, "_trusted_posting_url",
                        lambda *args, **kwargs: pytest.fail("unsafe source normalized"))
    if ats == "teamtailor":
        body = xml_response(rss([rss_item(source)]))
    else:
        row = recruitee_row()
        row["careers_url"] = source
        body = httpx.Response(200, json={"offers": [row]})
    with httpx.Client(transport=httpx.MockTransport(lambda req: body)) as c:
        with pytest.raises(ValueError, match="no valid jobs"):
            ADAPTERS[ats].fetch(company(ats), c)


def test_workable_accountless_and_bound_url_union_hidden_and_siblings():
    a = workable_row()
    b = workable_row()
    b["url"] = "https://apply.workable.com/example-co/j/SHORT1"
    b["locations"] = [{"name": "Berlin"}, {"name": "Secret", "hidden": True}]
    bad = workable_row()
    bad["url"] = "https://apply.workable.com/foreign/j/SHORT1"
    valid = workable_row()
    valid["shortcode"] = "SHORT2"
    valid["url"] = ""
    valid["title"] = "Another Engineer"
    results = fetch_fixture("workable", httpx.Response(
        200, json={"jobs": [bad, a, b, valid]}))
    assert len(results) == 2
    assert results[0].job_id == "SHORT1"
    assert results[0].jd_url == "https://apply.workable.com/example-co/j/SHORT1"
    assert results[0].location == "London; Berlin; Remote"
    assert "Secret" not in results[0].location


def test_workable_conflict_and_overflow_groups_skip_but_sibling_survives(monkeypatch):
    from discovery.adapters import workable
    warnings = []
    monkeypatch.setattr(workable.log, "warning", lambda *a: warnings.append(a[0]))
    conflict_a = workable_row()
    conflict_b = workable_row()
    conflict_b["title"] = "Different Engineer"
    survivor = workable_row()
    survivor["shortcode"] = "SAFE2"
    survivor["url"] = "https://apply.workable.com/j/SAFE2"
    survivor["title"] = "Safe Engineer"
    result = fetch_fixture("workable", httpx.Response(
        200, json={"jobs": [conflict_a, conflict_b, survivor]}))
    assert [j.job_id for j in result] == ["SAFE2"]
    assert any("conflicting" in m for m in warnings)

    overflow_a = workable_row()
    overflow_a["locations"] = [{"name": "A" * 1030}]
    overflow_b = workable_row()
    overflow_b["locations"] = [{"name": "B" * 1030}]
    result = fetch_fixture("workable", httpx.Response(
        200, json={"jobs": [overflow_a, overflow_b, survivor]}))
    assert [j.job_id for j in result] == ["SAFE2"]
    assert any("overflow" in m for m in warnings)


@pytest.mark.parametrize("cause", ["conflict", "overflow"])
def test_workable_wholly_rejected_groups_do_not_advance_handler_watermark(
        cause, monkeypatch):
    from discovery import handler
    a, b = workable_row(), workable_row()
    if cause == "conflict":
        b["title"] = "Other Engineer"
    else:
        a["locations"] = [{"name": "A" * 1030}]
        b["locations"] = [{"name": "B" * 1030}]
    warnings = []
    from discovery.adapters import workable
    monkeypatch.setattr(workable.log, "warning", lambda *v: warnings.append(v[0]))
    payload = httpx.Response(200, json={"jobs": [a, b]})

    class Tracking:
        def get(self, *args):
            pytest.fail("failed feed advanced to the tracking watermark")
        def put_new(self, *args):
            pytest.fail("failed feed produced a persisted job")

    with httpx.Client(transport=httpx.MockTransport(lambda req: payload)) as client:
        count = handler.discover_company(
            company("workable"), Preferences(), Tracking(), object(), client, "offline")
    assert count == 0
    assert warnings and cause[:5] in warnings[0]


@pytest.mark.parametrize("ats", ["personio", "teamtailor"])
@pytest.mark.parametrize("body", [
    b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]><rss/>',
    b'<!DOCTYPE workzag-jobs SYSTEM "file:///etc/passwd"><workzag-jobs/>',
    '<?xml version="1.0" encoding="UTF-16"?><rss/>'.encode("utf-16"),
    '<?xml version="1.0" encoding="UTF-32"?><rss/>'.encode("utf-32"),
    b'<?xml version="1.0" encoding="ISO-8859-1"?><rss/>',
])
def test_xml_forbidden_encoding_and_dtd_never_reach_elementtree(ats, body, monkeypatch):
    from discovery.adapters import personio
    monkeypatch.setattr(personio.ET, "iterparse",
                        lambda *args, **kwargs: pytest.fail("forbidden XML reached parser"))
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: xml_response(body))) as client:
        with pytest.raises(ValueError):
            ADAPTERS[ats].fetch(company(ats), client)


@pytest.mark.parametrize("ats", ["personio", "teamtailor"])
def test_xml_parser_limits_nonjob_extension_nodes_depth_rows(ats):
    from discovery.adapters.personio import _xml_rows
    root = "workzag-jobs" if ats == "personio" else "rss"
    row = "position" if ats == "personio" else "item"
    start = f"<{root}>" + ("<channel>" if ats == "teamtailor" else "")
    end = ("</channel>" if ats == "teamtailor" else "") + f"</{root}>"
    for body in (
        start + "<extra/>" * 100001 + end,
        start + "<deep>" * 33 + "</deep>" * 33 + end,
        start + f"<{row}/>" * 2001 + end,
    ):
        with pytest.raises(ValueError, match="limit"):
            list(_xml_rows(body.encode(), root_name=root, row_name=row, stats={}))


@pytest.mark.parametrize("ats", ["personio", "teamtailor"])
def test_xml_utf8_bom_is_supported(ats):
    feed = personio_xml() if ats == "personio" else rss()
    assert len(fetch_fixture(ats, xml_response(b"\xef\xbb\xbf" + feed))) == 1


@pytest.mark.parametrize("ats,key", [("recruitee", "offers"), ("workable", "jobs")])
def test_json_depth_and_raw_rows_are_bounded(ats, key):
    deep = {}
    node = deep
    for _ in range(33):
        node["nested"] = {}
        node = node["nested"]
    for doc in ({key: [], "extension": deep}, {key: [{}] * 2001}):
        with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=doc))) as client:
            with pytest.raises(ValueError, match="depth|count"):
                ADAPTERS[ats].fetch(company(ats), client)


def test_personio_invalid_text_and_newline_title_preserve_good_siblings():
    rows = [
        "<position><id>1</id><name>" + "A" * 513 + "</name></position>",
        "<position><id>2</id><name>Good&#10;1. Fake job</name><office>Paris</office></position>",
    ]
    jobs = fetch_fixture("personio", xml_response(personio_xml(rows)))
    assert [j.job_id for j in jobs] == ["2"]
    assert jobs[0].title == "Good 1. Fake job"


def test_workable_invalid_surrogate_blank_and_newline_rows_are_not_emitted():
    a, b, c, d = (workable_row() for _ in range(4))
    a["title"] = "\ud800"
    b["title"] = "  \n  "
    c["locations"] = [{"name": "\ud800"}]
    d.update(shortcode="GOOD2", url="", title="Backend\n1. Spoof",
             locations=[{"name": "Berlin\n2. Spoof"}])
    results = fetch_fixture("workable", httpx.Response(
        200, json={"jobs": [a, b, c, d]}))
    assert len(results) == 1
    assert results[0].title == "Backend 1. Spoof"
    assert results[0].location == "Berlin 2. Spoof; Remote"


def test_relevance_actual_numbered_listing_cannot_be_forged(monkeypatch):
    from discovery import relevance
    from sys import modules
    captured = {}

    def completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"choices": [{"message": {"content": '{"fit_indices":[0]}'}}]}

    monkeypatch.setitem(modules, "litellm", SimpleNamespace(completion=completion))
    data = httpx.Response(200, json={"jobs": [dict(
        shortcode="GOOD2", title="Backend\n1. Fake", published_on="2026-10-13",
        locations=[{"name": "Berlin\n2. Fake"}], remote=True)]})
    jobs = fetch_fixture("workable", data)
    assert relevance.relevant(jobs, Preferences(locations=["Berlin"], remote_only=True),
                              model="offline") == jobs
    msg = str(captured["messages"])
    assert "0. Backend 1. Fake  [location: Berlin 2. Fake; Remote]" in msg
    assert "\n1. Fake" not in msg and "\n2. Fake" not in msg


def test_publication_dates_and_fixed_clock_unknown_hour():
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    personio = fetch_fixture("personio")[0]
    teamtailor = fetch_fixture("teamtailor")[0]
    recruitee = fetch_fixture("recruitee")[0]
    workable = fetch_fixture("workable")[0]
    assert personio.posted_at == workable.posted_at == ""
    assert freshness.age_hours(workable.posted_at, now=now) is None
    assert freshness.is_fresh(workable.posted_at, 6, now=now)
    assert freshness.age_hours(teamtailor.posted_at, now=now) == 3
    assert freshness.is_fresh(teamtailor.posted_at, 6, now=now)
    assert freshness.age_hours(recruitee.posted_at, now=now) == 3

    row = workable_row()
    row["published_on"] = "2026-10-13T09:00:00+00:00"
    assert freshness.age_hours(fetch_fixture("workable", httpx.Response(
        200, json={"jobs": [row]}))[0].posted_at, now=now) == 3
    rec = recruitee_row()
    rec["published_at"] = "2026-10-13T11:00:00+02:00"
    assert freshness.age_hours(fetch_fixture("recruitee", httpx.Response(
        200, json={"offers": [rec]}))[0].posted_at, now=now) == 3
