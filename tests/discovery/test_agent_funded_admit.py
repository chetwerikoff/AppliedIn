"""Offline checks for agent-found startup admission into the funded state (#422).

Temporary state, synthetic responses only. No network, SMTP, browser, daemon or
operator data. The accepted rows are read back through the real, unchanged
``read_no_opening``.
"""
from __future__ import annotations

import ast
import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from core import agent_funded_admit as admit_mod
from core.agent_funded_admit import SOURCE_ID, admit
from core.outreach_sources import SourceHold, read_no_opening
from discovery import funding
from discovery.funding import FundingRefusal, State, _key, _preview_verified

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
NAME = "example-co"
HOME = "https://example-co.co.uk/"
CONTACT = "https://example-co.co.uk/contact"
NOTE = "agent saw no engineering openings on the careers page"
BOARD = "https://jobs.ashbyhq.com/example-co"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/example-co?includeCompensation=true"
HOME_HTML = (
    "<!doctype html><html><body><h1>example-co</h1>"
    "<p>example-co builds fintech systems in London. Also known as Example.</p>"
    '<a href="https://jobs.ashbyhq.com/example-co">Open jobs</a></body></html>'
)
BOARD_HTML = ("<!doctype html><html><body><h1>example-co Careers</h1>"
              "<p>example-co Software Engineering positions</p></body></html>")
COMPANY_ID = _key("agent_funded_admit:v1", "example-co.co.uk")


def job(n: int, tenant: str = "example-co") -> dict:
    jid = f"123e4567-e89b-42d3-a456-4266141740{n:02d}"
    return {"id": jid, "title": "Software Engineer",
            "jobUrl": f"https://jobs.ashbyhq.com/{tenant}/{jid}",
            "descriptionPlain": "Build fintech payments software.",
            "location": "London", "publishedAt": "2026-11-18T12:00:00+00:00"}


def feed(*jobs: dict) -> bytes:
    return json.dumps({"jobs": list(jobs)}).encode()


def settings(tmp_path, *, mode="local", funding=True, outreach=True, **over):
    (tmp_path / "watchlist.yaml").write_text("companies: []\n")
    (tmp_path / "preferences.yaml").write_text("titles: [Software Engineer]\n")
    base = dict(
        mode=mode, funding_enabled=funding, outreach_enabled=outreach,
        funding_sec_user_agent="", funding_daily_http=120, funding_max_http_per_pass=60,
        funding_max_postings_per_pass=12, funding_daily_relevance_calls=12,
        funding_recheck_days=7, funding_pending_days=60, funding_active_days=90,
        config_dir=str(tmp_path), local_dir=str(tmp_path / "local"))
    base.update(over)
    return SimpleNamespace(**base)


def state_path(cfg) -> Path:
    return Path(cfg.local_dir) / "funded-watchlist.yaml"


def routes(api=None, home=HOME_HTML, board=BOARD_HTML):
    return {HOME: home, BOARD: board, ASHBY_API: feed() if api is None else api}


class OfflineClient:
    def __init__(self, routes_, *, final_url=None):
        self.routes, self.final_url, self.calls = routes_, final_url or {}, []

    def get(self, url, *, params=None, follow_redirects=False, **_):
        from urllib.parse import urlencode
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        self.calls.append(url)
        data = self.routes.get(url)
        if data is None:
            raise FundingRefusal("synthetic_route_missing")
        status = 200
        if isinstance(data, tuple):
            status, data = data
        if isinstance(data, str):
            data = data.encode()
        return httpx.Response(status, content=data, request=httpx.Request(
            "GET", self.final_url.get(url, url)))


def run(cfg, routes_, *, relevance=lambda jobs, prefs: [], aliases=(), **kw):
    client = OfflineClient(routes_, final_url=kw.pop("final_url", None))
    result = admit(NAME, HOME, CONTACT, NOTE, aliases=aliases, settings=cfg, now=NOW,
                   client_factory=lambda *_: client, relevance_fn=relevance, **kw)
    return result, client


def buckets(cfg) -> dict:
    data = State(state_path(cfg)).data if state_path(cfg).exists() else {}
    return {b: copy.deepcopy(data.get(b, {})) for b in ("pending", "deals", "active")}


def seed(cfg, *, pending=None, deals=None, active=None):
    state = State(state_path(cfg))
    state.data["pending"].update(pending or {})
    state.data["deals"].update(deals or {})
    state.data["active"].update(active or {})
    state.save()


def other_pending(name="Other Co", domain="other-co.com", aliases=()):
    return {"name": name, "homepage": f"https://{domain}/", "employer_domain": domain,
            "source": "agent_other", "aliases": list(aliases), "reason": "no_openings",
            "seen_at": NOW.isoformat(),
            "next_check": (NOW + timedelta(days=7)).isoformat(),
            "expires": (NOW + timedelta(days=60)).isoformat()}


def other_active(name="Active Co", domain="active-co.com", aliases=()):
    return {"name": name, "careers_url": "https://jobs.ashbyhq.com/active-co",
            "ats": "ashby", "board": "active-co", "mode": "gated", "discovery": "feed",
            "employer_domain": domain, "homepage": f"https://{domain}/",
            "aliases": list(aliases), "source_id": "feed", "admitted_at": NOW.isoformat(),
            "last_relevant_at": NOW.isoformat()}


def assert_refused(result, reason):
    assert result["admitted"] is False and result["reason"] == reason, result
    assert result["next_action"]
    assert "company_id" not in result


# --- acceptance through the unchanged reader --------------------------------

def test_explicit_empty_feed_is_accepted_by_unchanged_reader(tmp_path):
    cfg = settings(tmp_path)
    result, client = run(cfg, routes(api=b'{"jobs":[]}'))
    assert result == {"admitted": True, "company_id": COMPANY_ID, "reason": "no_openings"}
    lead = read_no_opening(cfg, result["company_id"], now=NOW)
    assert (lead.name, lead.domain, lead.homepage) == (NAME, "example-co.co.uk", HOME)
    assert lead.reason == "no_openings" and lead.source_id == SOURCE_ID
    data = State(state_path(cfg)).data
    row, deal = data["pending"][COMPANY_ID], data["deals"][COMPANY_ID]
    assert deal["status"] == "pending" and deal["reason"] == row["reason"] == "no_openings"
    assert row["employer_domain"] == funding.registrable(HOME)
    assert row["aliases"] == [] and row["source"] == SOURCE_ID
    for field in ("seen_at", "next_check", "expires", "last_successful_no_opening_recheck_at"):
        assert datetime.fromisoformat(row[field]).tzinfo is not None
    assert datetime.fromisoformat(row["next_check"]) - NOW == timedelta(days=7)
    assert datetime.fromisoformat(row["expires"]) - NOW == timedelta(days=60)
    assert CONTACT not in json.dumps(data)
    assert row["agent_note"] == NOTE and NOTE not in json.dumps(data["deals"])
    assert not any("contact" in c for c in client.calls)
    assert data["active"] == {}


def test_fully_screened_feed_without_match_gives_no_relevant_openings(tmp_path):
    cfg = settings(tmp_path)
    screened = []
    result, _ = run(cfg, routes(api=feed(*(job(n) for n in range(5)))),
                    relevance=lambda jobs, prefs: screened.append(len(jobs)) or [])
    assert result["admitted"] and result["reason"] == "no_relevant_openings"
    assert screened == [5]
    assert read_no_opening(cfg, COMPANY_ID, now=NOW).reason == "no_relevant_openings"
    assert State(state_path(cfg)).data["deals"][COMPANY_ID]["reason"] == "no_relevant_openings"


def test_alias_must_appear_on_homepage_and_is_persisted(tmp_path):
    cfg = settings(tmp_path)
    result, _ = run(cfg, routes(api=b'{"jobs":[]}'), aliases=["Example"])
    assert result["admitted"]
    assert State(state_path(cfg)).data["pending"][COMPANY_ID]["aliases"] == ["Example"]
    assert read_no_opening(cfg, COMPANY_ID, now=NOW).aliases == ("Example",)
    (tmp_path / "b").mkdir()
    cfg2 = settings(tmp_path / "b")
    result, _ = run(cfg2, routes(api=b'{"jobs":[]}'), aliases=["Nowhere Inc"])
    assert_refused(result, "alias_unverified")
    assert buckets(cfg2) == {"pending": {}, "deals": {}, "active": {}}


def test_existing_rows_survive_admission_and_id_is_deterministic(tmp_path):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()},
         deals={"a" * 32: {"status": "pending", "reason": "no_openings",
                           "source": "agent_other", "seen_at": NOW.isoformat()}},
         active={"b" * 32: other_active()})
    before = buckets(cfg)
    result, _ = run(cfg, routes(api=b'{"jobs":[]}'))
    assert result["company_id"] == COMPANY_ID == _key("agent_funded_admit:v1", "example-co.co.uk")
    after = buckets(cfg)
    for bucket in before:
        for key, row in before[bucket].items():
            assert after[bucket][key] == row
    assert set(after["pending"]) == {"a" * 32, COMPANY_ID}
    assert set(after["deals"]) == {"a" * 32, COMPANY_ID}
    assert read_no_opening(cfg, COMPANY_ID, now=NOW).company_id == COMPANY_ID


# --- refusals ---------------------------------------------------------------

def test_final_url_domain_mismatch_refuses(tmp_path):
    cfg = settings(tmp_path)
    result, _ = run(cfg, routes(api=b'{"jobs":[]}'),
                    final_url={HOME: "https://parked-elsewhere.com/"})
    assert_refused(result, "homepage_domain_mismatch")
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


def test_matching_opening_refuses_and_preserves_existing_rows(tmp_path):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()}, active={"b" * 32: other_active()})
    before = buckets(cfg)
    result, _ = run(cfg, routes(api=feed(job(1))), relevance=lambda jobs, prefs: jobs)
    assert_refused(result, "relevant_opening_found")
    assert buckets(cfg) == before
    with pytest.raises(SourceHold):
        read_no_opening(cfg, COMPANY_ID, now=NOW)


def test_feed_truncation_by_postings_limit_refuses(tmp_path):
    cfg = settings(tmp_path)
    called = []
    result, _ = run(cfg, routes(api=feed(*(job(n) for n in range(13)))),
                    relevance=lambda jobs, prefs: called.append(1) or [])
    assert_refused(result, "postings_limit")
    assert called == [] and buckets(cfg)["pending"] == {}


def test_sixth_eligible_posting_beyond_relevance_sample_refuses(tmp_path):
    cfg = settings(tmp_path)
    seen = []
    result, _ = run(cfg, routes(api=feed(*(job(n) for n in range(6)))),
                    relevance=lambda jobs, prefs: seen.append(jobs) or [])
    assert_refused(result, "relevance_sample_incomplete")
    assert seen == []  # no relevance spend, sample never enlarged
    data = State(state_path(cfg)).data
    assert data["budgets"].get("day_relevance", 0) == 0
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


@pytest.mark.parametrize("body", [b"{}", b'{"jobs": null}', b'{"jobs": {}}',
                                  b'{"results": []}', b"[]", b"not json"])
def test_malformed_ashby_feed_is_not_an_empty_board(tmp_path, body):
    cfg = settings(tmp_path)
    result, _ = run(cfg, routes(api=body))
    assert result["admitted"] is False
    assert result["reason"] in {"feed_incomplete", "proof_failed"}
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


def test_off_tenant_posting_refuses_even_beside_a_good_one(tmp_path):
    cfg = settings(tmp_path)
    for api in (feed(job(1, tenant="someone-else")), feed(job(1), job(2, tenant="someone-else"))):
        result, _ = run(cfg, routes(api=api))
        assert_refused(result, "consumer_guard_refused")
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


@pytest.mark.parametrize("override,reason", [
    ({ASHBY_API: (500, b"boom")}, "proof_failed"),
    ({HOME: (404, b"missing")}, "proof_failed"),
])
def test_failed_or_unproved_pages_refuse(tmp_path, override, reason):
    cfg = settings(tmp_path)
    result, _ = run(cfg, {**routes(api=b'{"jobs":[]}'), **override})
    assert_refused(result, reason)
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


def test_oversize_feed_refuses(tmp_path, monkeypatch):
    cfg = settings(tmp_path)

    class Oversize(OfflineClient):
        def get(self, url, **kw):
            if "api.ashbyhq.com" in url:
                raise FundingRefusal("response_too_large")
            return super().get(url, **kw)

    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW,
                   client_factory=lambda *_: Oversize(routes()),
                   relevance_fn=lambda j, p: [])
    assert_refused(result, "response_too_large")
    assert buckets(cfg)["pending"] == {}


def test_manual_watchlist_name_precedence_refuses(tmp_path):
    cfg = settings(tmp_path)
    (tmp_path / "watchlist.yaml").write_text(
        "companies:\n- name: EXAMPLE-CO\n  careers_url: https://jobs.ashbyhq.com/manual\n"
        "  ats: ashby\n  board: manual\n")
    result, _ = run(cfg, routes(api=b'{"jobs":[]}'))
    assert result["admitted"] is False
    assert buckets(cfg)["pending"] == {}


@pytest.mark.parametrize("kind", ["pending_domain", "active_domain", "pending_name",
                                  "active_alias", "pending_alias_of_ours", "key_in_deals",
                                  "key_in_pending", "key_in_active"])
def test_collisions_refuse_without_mutating_buckets(tmp_path, kind):
    cfg = settings(tmp_path)
    kw = {}
    aliases = ()
    if kind == "pending_domain":
        kw["pending"] = {"a" * 32: other_pending(domain="example-co.co.uk")}
    elif kind == "active_domain":
        kw["active"] = {"b" * 32: other_active(domain="example-co.co.uk")}
    elif kind == "pending_name":
        kw["pending"] = {"a" * 32: other_pending(name=" EXAMPLE-CO ")}
    elif kind == "active_alias":
        kw["active"] = {"b" * 32: other_active(aliases=["Example-Co"])}
    elif kind == "pending_alias_of_ours":
        kw["pending"] = {"a" * 32: other_pending(aliases=["Example"])}
        aliases = ("Example",)
    elif kind == "key_in_deals":
        kw["deals"] = {COMPANY_ID: {"status": "rejected", "reason": "x",
                                    "source": "s", "seen_at": NOW.isoformat()}}
    elif kind == "key_in_pending":
        kw["pending"] = {COMPANY_ID: other_pending()}
    else:
        kw["active"] = {COMPANY_ID: other_active()}
    seed(cfg, **kw)
    before, raw = buckets(cfg), state_path(cfg).read_bytes()
    result, client = run(cfg, routes(api=b'{"jobs":[]}'), aliases=aliases)
    assert result["admitted"] is False
    assert result["reason"] in {"company_key_occupied", "identity_collision"}
    assert result["next_action"]
    assert buckets(cfg) == before
    assert client.calls == []  # collision checked before any network work
    assert state_path(cfg).read_bytes() == raw


def test_collision_appearing_during_proof_is_caught_before_commit(tmp_path):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()})

    def interleaved(jobs, prefs):  # another writer slips in during the proof
        s = State(state_path(cfg))
        s.data["pending"]["c" * 32] = other_pending(name="Late", domain="example-co.co.uk")
        s.save()
        return []

    result, _ = run(cfg, routes(api=feed(job(1))), relevance=interleaved)
    assert_refused(result, "state_changed_during_admission")
    assert COMPANY_ID not in State(state_path(cfg)).data["pending"]
    assert COMPANY_ID not in State(state_path(cfg)).data["deals"]


@pytest.mark.parametrize("kw", [dict(mode="cloud"), dict(funding=False), dict(outreach=False)])
def test_gates_refuse_before_any_state_or_network_work(tmp_path, kw):
    cfg = settings(tmp_path, **kw)
    result, client = run(cfg, routes(api=b'{"jobs":[]}'))
    assert_refused(result, "mode_or_gate_off")
    assert client.calls == [] and not state_path(cfg).exists()


@pytest.mark.parametrize("args", [
    dict(name="x"), dict(name=""), dict(homepage="http://example-co.co.uk/"),
    dict(homepage="https://localhost/"), dict(contact_url="ftp://example-co.co.uk/c"),
    dict(note="  "), dict(aliases=["a"] * 9)])
def test_invalid_inputs_refuse(tmp_path, args):
    cfg = settings(tmp_path)
    call = dict(name=NAME, homepage=HOME, contact_url=CONTACT, note=NOTE, aliases=())
    call.update(args)
    client = OfflineClient(routes())
    result = admit(call["name"], call["homepage"], call["contact_url"], call["note"],
                   aliases=call["aliases"], settings=cfg, now=NOW,
                   client_factory=lambda *_: client, relevance_fn=lambda j, p: [])
    assert_refused(result, "invalid_input")
    assert client.calls == [] and not state_path(cfg).exists()


def test_symlinked_state_file_is_refused(tmp_path):
    cfg = settings(tmp_path)
    Path(cfg.local_dir).mkdir()
    target = tmp_path / "elsewhere.yaml"
    target.write_text(yaml.safe_dump(funding._new_state()))
    state_path(cfg).symlink_to(target)
    result, client = run(cfg, routes(api=b'{"jobs":[]}'))
    assert_refused(result, "unsafe_state_file")
    assert client.calls == []
    assert yaml.safe_load(target.read_text()) == funding._new_state()


# --- atomicity and accounting -----------------------------------------------

def test_failed_atomic_write_leaves_admission_buckets_untouched(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()})
    before = buckets(cfg)
    real = os.replace

    def failing(src, dst, *a, **kw):
        if COMPANY_ID in Path(src).read_text():
            raise OSError("synthetic disk failure")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(funding.os, "replace", failing)
    result, _ = run(cfg, routes(api=b'{"jobs":[]}'))
    assert_refused(result, "state_write_failed")
    assert buckets(cfg) == before
    assert not list(Path(cfg.local_dir).glob(".funded-*"))
    with pytest.raises(SourceHold):
        read_no_opening(cfg, COMPANY_ID, now=NOW)


class _Conn:
    """Stands in for the guarded socket; GuardedClient accounting stays real."""
    table: dict = {}
    seen: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host

    def request(self, method, path, headers=None):
        self.url = f"https://{self.host}{path}"
        _Conn.seen.append(self.url)

    def getresponse(self):
        item = _Conn.table[self.url]
        if isinstance(item, Exception):
            raise item
        data = item if isinstance(item, bytes) else item.encode()
        return SimpleNamespace(status=200, read=lambda n=None: data if n is None else data[:n],
                               getheaders=lambda: [("Content-Type", "text/html")])

    def close(self):
        pass


def _real_client(monkeypatch, table):
    _Conn.table, _Conn.seen = table, []
    monkeypatch.setattr(funding, "_SafeTLS", _Conn)


def test_guarded_http_and_relevance_accounting_persists_on_refusal(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()})
    before = buckets(cfg)
    _real_client(monkeypatch, routes(api=feed(job(1))))
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW,
                   relevance_fn=lambda jobs, prefs: jobs)
    assert_refused(result, "relevant_opening_found")
    data = State(state_path(cfg)).data
    assert len(_Conn.seen) == 3  # homepage (reused as employer page), board, feed
    assert data["budgets"]["day_admission_http"] == 3
    assert data["budgets"].get("day_http", 0) == 0
    assert data["budgets"]["day_relevance"] == 1
    assert data["report"]["counts"]["source_and_consumer_proved"] == 1
    assert {b: data[b] for b in before} == before


def test_guarded_failure_still_consumes_reservation(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    _real_client(monkeypatch, {HOME: FundingRefusal("tls_or_connect_failed")})
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW,
                   relevance_fn=lambda jobs, prefs: [])
    assert_refused(result, "tls_or_connect_failed")
    data = State(state_path(cfg)).data
    assert data["budgets"]["day_admission_http"] == 1
    assert data["budgets"].get("day_http", 0) == 0
    assert (data["pending"], data["deals"], data["active"]) == ({}, {}, {})


def test_http_budget_exhaustion_refuses_without_admission(tmp_path, monkeypatch):
    cfg = settings(tmp_path, funding_daily_http=1)
    _real_client(monkeypatch, routes(api=b'{"jobs":[]}'))
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW,
                   relevance_fn=lambda jobs, prefs: [])
    assert result["admitted"] is False
    assert result["reason"] == "http_day_budget"
    assert State(state_path(cfg)).data["budgets"]["day_admission_http"] == 1
    assert buckets(cfg)["pending"] == {}


def test_real_guarded_client_happy_path_is_accepted_by_reader(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    _real_client(monkeypatch, routes(api=b'{"jobs":[]}'))
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW)
    assert result["admitted"] and result["reason"] == "no_openings"
    assert read_no_opening(cfg, result["company_id"], now=NOW).reason == "no_openings"
    assert State(state_path(cfg)).data["budgets"]["day_admission_http"] == 3


# --- admission mode, #424 -----------------------------------------------------

def _sub(tmp_path) -> Path:
    path = tmp_path / "second"
    path.mkdir()
    return path


NO_BOARD_HTML = ("<!doctype html><html><body><h1>example-co</h1>"
                 "<p>example-co builds fintech systems.</p></body></html>")


def test_homepage_without_ats_board_is_admitted_as_no_openings_with_note(tmp_path):
    cfg = settings(tmp_path)
    result, client = run(cfg, {HOME: NO_BOARD_HTML})
    assert result == {"admitted": True, "company_id": COMPANY_ID, "reason": "no_openings"}
    assert client.calls == [HOME]  # no board, no feed, no relevance call
    row = State(state_path(cfg)).data["pending"][COMPANY_ID]
    assert row["agent_note"] == NOTE and row["reason"] == "no_openings"
    assert read_no_opening(cfg, COMPANY_ID, now=NOW).reason == "no_openings"


def test_linked_but_unproved_board_still_refuses(tmp_path):
    cfg = settings(tmp_path)
    # The board link exists but its page is unreachable: not "no board".
    result, _ = run(cfg, {HOME: HOME_HTML})
    assert_refused(result, "guard_not_proven")
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}
    # A link past the first 150 links is still a link, never silently "no board".
    many = ("<h1>example-co</h1>" + "".join(f'<a href="/p{i}">x</a>' for i in range(200))
            + '<a href="https://jobs.lever.co/example-co">jobs</a>')
    result, _ = run(cfg, {HOME: many})
    assert_refused(result, "guard_not_proven")


def test_found_board_with_relevant_opening_still_refuses(tmp_path):
    cfg = settings(tmp_path)
    result, _ = run(cfg, routes(api=feed(job(1))), relevance=lambda jobs, prefs: jobs)
    assert_refused(result, "relevant_opening_found")
    assert buckets(cfg) == {"pending": {}, "deals": {}, "active": {}}


def test_owner_proof_is_the_final_registrable_domain_only(tmp_path):
    cfg = settings(tmp_path)
    # Neither the domain label nor any heading names the company.
    page = "<html><body><h1>Welcome</h1><p>We make payments.</p></body></html>"
    result = admit("Totally Different Name", HOME, CONTACT, NOTE, settings=cfg, now=NOW,
                   client_factory=lambda *_: OfflineClient({HOME: page}),
                   relevance_fn=lambda j, p: [])
    assert result["admitted"] is True and result["reason"] == "no_openings"
    # A redirect to another registrable domain is still refused.
    cfg2 = settings(_sub(tmp_path))
    result2, _ = run(cfg2, {HOME: page}, final_url={HOME: "https://elsewhere.example.com/"})
    assert_refused(result2, "homepage_domain_mismatch")


def test_homepage_over_poller_cap_is_read_up_to_2mb(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    pad = "<!-- " + "x" * 400_000 + " -->"
    _real_client(monkeypatch, {HOME: NO_BOARD_HTML + pad})
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW)
    assert result["admitted"] and result["reason"] == "no_openings"
    # Past the 2 MB admission cap it still refuses, never half-reads.
    cfg2 = settings(_sub(tmp_path))
    _real_client(monkeypatch, {HOME: NO_BOARD_HTML + "<!-- " + "x" * 2_100_000 + " -->"})
    assert_refused(admit(NAME, HOME, CONTACT, NOTE, settings=cfg2, now=NOW),
                   "response_too_large")
    assert buckets(cfg2)["pending"] == {}


def test_admission_spends_its_own_day_http_counter(tmp_path, monkeypatch):
    cfg = settings(tmp_path, funding_daily_http=5)
    state = State(state_path(cfg))
    assert all(state.reserve(NOW, "day_http", 1, 5) for _ in range(5))
    assert not state.reserve(NOW, "day_http", 1, 5)  # the poller's budget is spent
    _real_client(monkeypatch, {HOME: NO_BOARD_HTML})
    result = admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW)
    assert result["admitted"] is True
    budgets = State(state_path(cfg)).data["budgets"]
    assert budgets["day_http"] == 5 and budgets["day_admission_http"] == 1
    # The admission counter has its own limit and day rollover.
    cfg2 = settings(_sub(tmp_path), funding_daily_http=1)
    seed_state = State(state_path(cfg2))
    assert seed_state.reserve(NOW, "day_admission_http", 1, 1)
    assert_refused(admit(NAME, HOME, CONTACT, NOTE, settings=cfg2, now=NOW),
                   "http_day_budget")
    assert State(state_path(cfg2)).reserve(NOW + timedelta(days=1), "day_admission_http", 1, 1)


def test_exhausted_admission_budget_is_not_read_as_no_board(tmp_path, monkeypatch):
    cfg = settings(tmp_path, funding_daily_http=1)
    _real_client(monkeypatch, routes(api=b'{"jobs":[]}'))
    assert_refused(admit(NAME, HOME, CONTACT, NOTE, settings=cfg, now=NOW),
                   "http_day_budget")
    assert buckets(cfg)["pending"] == {}


@pytest.mark.parametrize("reason", [
    "http_day_budget", "guard_not_proven", "response_too_large",
    "employer_homepage_owner_unproved", "tls_or_connect_failed"])
def test_next_action_is_specific_for_recoverable_refusals(reason):
    nxt = admit_mod._refuse(reason)["next_action"]
    assert nxt != admit_mod._DEFAULT_NEXT
    assert "drop" not in nxt.casefold().replace("do not drop", "")
    assert "manual watchlist" not in nxt or reason == "guard_not_proven"


def test_http_day_budget_next_action_says_retry_after_utc_reset_not_drop():
    nxt = admit_mod._refuse("http_day_budget")["next_action"]
    assert "retry" in nxt.casefold() and "UTC day reset" in nxt
    assert "do not drop" in nxt.casefold()
    assert "could not be proved" not in nxt


def test_default_next_action_stays_for_identity_failures():
    assert admit_mod._refuse("consumer_guard_refused")["next_action"] == admit_mod._DEFAULT_NEXT


# --- shared proof defaults are unchanged ------------------------------------

def _preview(cfg, routes_, relevance=lambda jobs, prefs: [], **kw):
    state = State(state_path(cfg))
    return _preview_verified(NAME, HOME, "src", state, cfg, OfflineClient(routes_), NOW,
                             relevance_fn=relevance, **kw)


def test_default_preview_semantics_are_unchanged(tmp_path):
    cfg = settings(tmp_path)
    # A body with no ``jobs`` key still reads as an empty board by default.
    assert _preview(cfg, routes(api=b"{}"))[0] == "no_openings"
    # A truncated feed is sampled, not refused.
    many = feed(*(job(n) for n in range(13)))
    assert _preview(cfg, routes(api=many), relevance=lambda j, p: j)[0] == "admissible"
    # Only the first five approved postings reach relevance.
    shown = []
    six = feed(*(job(n) for n in range(6)))
    assert _preview(cfg, routes(api=six),
                    relevance=lambda j, p: shown.append(len(j)) or [])[0] == "no_relevant_openings"
    assert shown == [5]
    # A partly refused feed still passes when something approved screens in.
    mixed = feed(job(1), job(2, tenant="someone-else"))
    assert _preview(cfg, routes(api=mixed), relevance=lambda j, p: j)[0] == "admissible"
    # Only admission opts in to the stricter proof.
    assert _preview(cfg, routes(api=b"{}"), complete_negative=True)[0] == "feed_incomplete"
    assert _preview(cfg, routes(api=mixed), relevance=lambda j, p: j,
                    complete_negative=True)[0] == "consumer_guard_refused"


# --- CLI and import guards ---------------------------------------------------

def _patch_cli(monkeypatch, cfg, routes_):
    import core.config
    monkeypatch.setattr(core.config, "get_settings", lambda: cfg)
    client = OfflineClient(routes_)
    monkeypatch.setattr(admit_mod, "GuardedClient", lambda **kw: client)
    monkeypatch.setattr(admit_mod, "_utc", lambda now=None: NOW)
    return client


def test_cli_prints_json_and_exits_zero_on_admission(tmp_path, monkeypatch, capsys):
    cfg = settings(tmp_path)
    _patch_cli(monkeypatch, cfg, routes(api=b'{"jobs":[]}'))
    argv = ["--name", NAME, "--homepage", HOME, "--contact-url", CONTACT, "--note", NOTE]
    # The CLI exposes no relevance hook; an explicit empty feed needs none.
    assert admit_mod.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"admitted": True, "company_id": COMPANY_ID, "reason": "no_openings"}
    assert read_no_opening(cfg, out["company_id"], now=NOW).company_id == COMPANY_ID


def test_cli_prints_refusal_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    cfg = settings(tmp_path, mode="cloud")
    _patch_cli(monkeypatch, cfg, routes())
    argv = ["--name", NAME, "--homepage", HOME, "--contact-url", CONTACT, "--note", NOTE]
    assert admit_mod.main(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["admitted"] is False and out["reason"] == "mode_or_gate_off"
    assert out["next_action"] and "company_id" not in out


def test_cli_requires_all_inputs(capsys):
    with pytest.raises(SystemExit) as exc:
        admit_mod.main(["--name", NAME])
    assert exc.value.code == 2


def test_module_never_enqueues_or_sends_and_changes_no_out_of_scope_file():
    tree = ast.parse(Path(admit_mod.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
    forbidden = ("smtplib", "email", "outreach", "enqueue", "redis", "boto3", "subprocess")
    assert not [i for i in imported if any(f in i for f in forbidden)], imported
    src = Path(admit_mod.__file__).read_text()
    assert "enqueue_first" not in src.replace("never enqueues", "")
    assert "smtplib" not in src


# --- review round 1 regressions ---------------------------------------------

def _untitled(n, title):
    j = job(n)
    if title is None:
        del j["title"]
    else:
        j["title"] = title
    return j


@pytest.mark.parametrize("title", [None, "", "   "])
def test_blank_or_missing_title_is_not_a_complete_negative(tmp_path, title):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending()})
    before = buckets(cfg)
    screened = []
    api = feed(_untitled(1, title))
    result, _ = run(cfg, routes(api=api),
                    relevance=lambda jobs, prefs: screened.append(jobs) or [])
    assert_refused(result, "posting_title_missing")
    assert screened == []  # refused before relevance, no relevance spend
    assert buckets(cfg) == before
    with pytest.raises(SourceHold):
        read_no_opening(cfg, COMPANY_ID, now=NOW)
    # Scheduled/default proof semantics are untouched.
    assert _preview(cfg, routes(api=api))[0] == "no_relevant_openings"


def test_non_string_alias_in_existing_row_collides_like_the_reader(tmp_path):
    cfg = settings(tmp_path)
    seed(cfg, pending={"a" * 32: other_pending(aliases=[123, None])})
    before, raw = buckets(cfg), state_path(cfg).read_bytes()
    for name in ("123", "None"):
        client = OfflineClient(routes(api=b'{"jobs":[]}'))
        result = admit(name, "https://fresh-co.com/", CONTACT, NOTE, settings=cfg, now=NOW,
                       client_factory=lambda *_, c=client: c, relevance_fn=lambda j, p: [])
        assert_refused(result, "identity_collision")
        assert client.calls == []
    assert buckets(cfg) == before and state_path(cfg).read_bytes() == raw
