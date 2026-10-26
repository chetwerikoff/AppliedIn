"""Synthetic, offline #201 source/provenance and read-only repair contracts."""
from __future__ import annotations

from copy import deepcopy

import pytest

from core.models import JobRecord
from discovery.canonical_job_url import (
    admit_feed, canonical_shape, claim_greenhouse_query,
    consumer_accepts, literal_gh_id, repair_one, source_bound,
)
from discovery.watchlist import CompanyConfig
from tools.browser_skill_apply import _greenhouse_id_query_attempt

UUID = "123e4567-e89b-42d3-a456-426614174000"
GH_ID = "731205"


def company(ats="ashby", board="example-board", source=None):
    host = {"ashby": "jobs.ashbyhq.com", "lever": "jobs.lever.co",
            "greenhouse": "job-boards.greenhouse.io"}[ats]
    return CompanyConfig(name="example-co", ats=ats, board=board,
                         careers_url=source or f"https://{host}/{board}")


def job(ats="ashby", board="example-board", host=None, jid=None, url=None):
    host = host or {"ashby": "jobs.ashbyhq.com",
                    "lever": "jobs.lever.co",
                    "greenhouse": "job-boards.greenhouse.io"}[ats]
    jid = jid or (GH_ID if ats == "greenhouse" else UUID)
    ending = f"/jobs/{jid}" if ats == "greenhouse" else f"/{jid}"
    return JobRecord(company="example-co", ats=ats, job_id=jid,
                     title="Test Engineer", jd_url=url or f"https://{host}/{board}{ending}",
                     jd_text="Synthetic posting")


@pytest.mark.parametrize("ats", ["ashby", "lever"])
def test_raw_native_source_and_unmodified_entry_are_both_required(ats):
    co = company(ats)
    row = job(ats)
    assert source_bound(co, co)
    assert canonical_shape(row, co.board, co.ats)
    assert consumer_accepts(row, co)
    kept, refused = admit_feed(co, co, [row])
    assert [j.pk for j in kept] == [row.pk] and refused == []

    # A resolver-selected board cannot borrow authority from a generic employer URL.
    raw = CompanyConfig(name=co.name, careers_url="https://example.test/careers")
    kept, refused = admit_feed(co, raw, [row])
    assert not kept
    assert refused[0]["status"] == "employer_board_source_unproved"
    assert refused[0]["next_action"]


@pytest.mark.parametrize("host", ["job-boards.greenhouse.io", "boards.greenhouse.io"])
def test_greenhouse_literal_target_is_not_a_new_allowed_start(host):
    co = company("greenhouse")
    row = job("greenhouse", host=host)
    assert canonical_shape(row, co.board, co.ats)
    assert not consumer_accepts(row, co)
    kept, refusals = admit_feed(co, co, [row])
    assert kept == []
    assert refusals[0]["status"] == "canonical_target_not_supported_by_current_guard"


def test_dotted_board_cannot_bypass_existing_entry_board_token():
    co = company(board="example.co")
    row = job(board="example.co")
    assert canonical_shape(row, co.board, co.ats)
    assert not consumer_accepts(row, co)
    kept, refused = admit_feed(co, co, [row])
    assert not kept
    assert refused[0]["status"] == "canonical_target_not_supported_by_current_guard"


@pytest.mark.parametrize("mutate", [
    lambda url: url.replace("https://", "http://"),
    lambda url: url + "?ref=source",
    lambda url: url + "#apply",
    lambda url: url.replace("/example-board/", "/EXAMPLE-BOARD/"),
    lambda url: url.replace("jobs.ashbyhq.com", "jobs.ashbyhq.com:443"),
    lambda url: url.replace("jobs.ashbyhq.com", "somebody@jobs.ashbyhq.com"),
    lambda url: url.replace("/123e", "/%31" + "23e"),
    lambda url: url.replace("a456", "A456"),
])
def test_literal_target_is_not_normalized_into_authority(mutate):
    co = company()
    row = job()
    row.jd_url = mutate(row.jd_url)
    kept, refusal = admit_feed(co, co, [row])
    assert not kept and refusal
    assert refusal[0]["status"] == "nonliteral_or_noncanonical_ats_target"


def test_duplicate_same_id_refuses_both_instead_of_first_wins():
    co = company()
    row = job()
    kept, refused = admit_feed(co, co, [row, row.model_copy()])
    assert not kept
    assert [r["status"] for r in refused] == [
        "ambiguous_same_job_feed_identity"] * 2


@pytest.mark.parametrize("query,expected", [
    ("gh_jid=731205", True),
    ("GH_JID=731205", True),
    ("foo=1&gh_jid=731205", True),
    ("gh%5Fjid=731205", True),
    ("gh%255Fjid=731205", True),
    ("gh%25255Fjid=731205", True),
    ("gh%252525255Fjid=731205", True),
    ("other=source", False),
    ("nothing=gh_jid", True),  # '=' is a downstream parser delimiter
    ("no_gh_jid=731205", False),
])
def test_browser_and_producer_share_bounded_attempt_classification(query, expected):
    url = f"https://example.test/careers?{query}"
    assert claim_greenhouse_query(url) is expected
    assert _greenhouse_id_query_attempt(url) is expected


@pytest.mark.parametrize("query", [
    "gh_jid=731205&ref=1", "GH_JID=731205", "gh_jid=",
    "gh_jid=bogus", "gh_jid=731205&gh_jid=731205",
    "gh%5Fjid=731205", "gh_jid=%37%33%31%32%30%35",
])
def test_gh_attempt_is_not_valid_literal_query_identity(query):
    assert literal_gh_id(f"https://example.test/careers/jobs?{query}") is None


class NeverWrite:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.writes = []

    def get(self, pk):
        return deepcopy(self.row)

    def replace_noncanonical_jd_url(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        raise AssertionError("A currently unsupported integrated repair must not write")


@pytest.fixture
def raw_gh_watchlist(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        "companies:\n"
        "  - name: example-co\n"
        "    careers_url: https://example.test/careers\n"
        "    ats: greenhouse\n"
        "    board: example-board\n"
        "    discovery: crawl\n"
        "    mode: gated\n")
    monkeypatch.setattr(
        "discovery.tenant_pin.current_feed",
        lambda cfg, *, client=None: [job("greenhouse")])
    return path


@pytest.mark.parametrize("old_url,source,old_class", [
    ("https://example.test/careers/jobs?gh_jid=731205", "", "R1_literal_gh_jid"),
    ("https://example.test/careers/jobs/731205", "sitemap_greenhouse_suffix",
     "R2_retained_sitemap_suffix"),
])
def test_retained_gh_evidence_still_refuses_unsupported_repair(
    raw_gh_watchlist, old_url, source, old_class,
):
    record = {"pk": "example-co#731205", "company": "example-co",
              "job_id": GH_ID, "ats": "greenhouse",
              "jd_url": old_url, "greenhouse_board": "example-board",
              "discovery_source": source, "status": "found"}
    store = NeverWrite(record)
    outcome = repair_one(record["pk"], tracking=store,
                         watchlist_path=raw_gh_watchlist)
    assert outcome["old_evidence_class"] == old_class
    assert outcome["status"] == "canonical_target_not_supported_by_current_guard"
    assert outcome["candidate_url"] == job("greenhouse").jd_url
    assert not outcome["committed"] and not store.writes
    authorized = repair_one(record["pk"], commit=True, operator_authorized=True,
                            tracking=store, watchlist_path=raw_gh_watchlist)
    assert authorized["status"] == outcome["status"]
    assert not store.writes


def test_relative_historic_row_has_no_original_native_base(raw_gh_watchlist):
    record = {"pk": "example-co#731205", "company": "example-co",
              "job_id": GH_ID, "ats": "greenhouse", "status": "found",
              "jd_url": "/careers/jobs/731205"}
    store = NeverWrite(record)
    result = repair_one(record["pk"], tracking=store,
                        watchlist_path=raw_gh_watchlist)
    assert result["status"] == "historic_original_posting_base_unavailable"
    assert "original" in result["next_action"]
    assert not result["committed"] and not store.writes


def test_guard_compatible_already_canonical_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        "companies:\n"
        "  - name: example-co\n"
        "    careers_url: https://jobs.ashbyhq.com/example-board\n"
        "    ats: ashby\n"
        "    board: example-board\n"
        "    discovery: feed\n"
        "    mode: gated\n")
    monkeypatch.setattr("discovery.tenant_pin.current_feed",
                        lambda cfg, *, client=None: [job()])
    row = job()
    store = NeverWrite({"pk": row.pk, "company": row.company, "job_id": row.job_id,
                        "ats": row.ats, "jd_url": row.jd_url, "status": "found"})
    result = repair_one(row.pk, tracking=store, watchlist_path=path)
    assert result["status"] == "already_canonical_noop"
    assert result["guard_compatible"] and not store.writes


@pytest.mark.parametrize("old_url,source", [
    ("https://different.test/careers/jobs?gh_jid=731205", ""),
    ("https://example.test/careers-archive/jobs?gh_jid=731205", ""),
    ("https://different.test/careers/job/731205", "sitemap_greenhouse_suffix"),
    ("https://example.test/careers/job/731205?x=1", "sitemap_greenhouse_suffix"),
])
def test_historic_wrong_origin_or_scope_cannot_borrow_new_feed_as_proof(
    raw_gh_watchlist, old_url, source,
):
    record = {"pk": "example-co#731205", "company": "example-co",
              "job_id": GH_ID, "ats": "greenhouse",
              "jd_url": old_url, "greenhouse_board": "example-board",
              "discovery_source": source, "status": "found"}
    store = NeverWrite(record)
    result = repair_one(record["pk"], tracking=store,
                        watchlist_path=raw_gh_watchlist)
    assert result["status"] == "historic_identity_unproved"
    assert "old_evidence_class" not in result
    assert not store.writes and not result["committed"]
