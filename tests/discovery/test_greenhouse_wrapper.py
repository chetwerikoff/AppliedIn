"""Greenhouse employer wrapper admission: synthetic browser and board witnesses only."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.models import DiscoveryMode, JobRecord
from discovery import crawler
from discovery.adapters.greenhouse import GreenhouseAdapter
from discovery.watchlist import CompanyConfig, Preferences
from tools import browser_skill_apply as forms

COMPANY = "example-co"
BOARD = "example-board"
CAREERS = "https://careers.example-co.test/openings"
WRAPPER = CAREERS + "/platform-engineer?gh_jid=731205"
HOSTED = "https://job-boards.greenhouse.io/example-board/jobs/731205"


def config(**updates):
    base = dict(name=COMPANY, careers_url=CAREERS, ats="greenhouse",
                board=BOARD, discovery=DiscoveryMode.BROWSER)
    base.update(updates)
    return CompanyConfig(**base)


def listing(url="/openings/platform-engineer?gh_jid=731205", **extra):
    return dict(title="Platform Engineer", url=url, summary="Test posting",
                score=8, location="Remote", **extra)


def board_job(job_id="731205", url=HOSTED):
    return JobRecord(company=COMPANY, job_id=job_id, title="Platform Engineer",
                     jd_url=url, jd_text="Test job feed", ats="greenhouse")


class Tracking:
    def __init__(self):
        self.rows = {}
        self.put_calls = []
        self.alias_reads = []
        self.r = None

    def put_new(self, job):
        self.put_calls.append(job.model_copy(deep=True))
        if job.pk in self.rows:
            return False
        row = dict(pk=job.pk, company=job.company, job_id=job.job_id,
                   jd_url=job.jd_url, ats=job.ats, status="found",
                   jd_text=job.jd_text)
        if job.greenhouse_board:
            row["greenhouse_board"] = job.greenhouse_board
        self.rows[job.pk] = row
        return True

    def get(self, pk):
        return deepcopy(self.rows.get(pk))

    def get_alias(self, pk):
        self.alias_reads.append(pk)
        return self.get(pk)


class Queue:
    def __init__(self):
        self.calls = []

    def enqueue(self, name, data):
        self.calls.append((name, dict(data)))


@pytest.fixture
def world(monkeypatch, tmp_path):
    from core import config as core_config, events, flags, stores
    from tools import browser_runtime, company_skills, seen

    state = SimpleNamespace(
        root=tmp_path, config=config(), tracking=Tracking(), queue=Queue(),
        browser_rows=[listing()], feed=[board_job()], feed_error=False,
        browser_calls=[], feed_calls=[], seen_urls=set(), marked=[],
    )
    state.stores = SimpleNamespace(tracking=state.tracking, queue=state.queue,
                                   tailor_queue="synthetic-queue")
    state.write = lambda body: (tmp_path / "watchlist.yaml").write_text(body)
    state.write("""companies:
  - name: example-co
    careers_url: https://careers.example-co.test/openings
    ats: greenhouse
    board: example-board
    discovery: browser
    mode: gated
""")
    monkeypatch.setattr(core_config, "get_settings",
                        lambda: SimpleNamespace(config_dir=str(tmp_path), chrome_model=""))
    monkeypatch.setattr(stores, "make_stores", lambda *a, **kw: state.stores)
    monkeypatch.setattr(events, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(flags, "effective_prefs", lambda name, prefs: prefs)
    monkeypatch.setattr(flags, "company_pref", lambda name: {})
    monkeypatch.setattr(flags, "company_filter", lambda name: [])
    monkeypatch.setattr(crawler, "_age_limit", lambda name: 0.0)
    monkeypatch.setattr(crawler, "relevant", lambda jobs, prefs: list(jobs))
    monkeypatch.setattr(crawler, "_render_page",
                        lambda url: pytest.fail("explicit browser read used plain fetch"))
    monkeypatch.setattr(crawler, "sitemap_jobs",
                        lambda *a: pytest.fail("explicit browser read used sitemap"))
    monkeypatch.setattr(seen, "load", lambda: set(state.seen_urls))

    def mark(jobs):
        state.marked.append([j.jd_url for j in jobs])
        state.seen_urls.update(j.jd_url for j in jobs)

    monkeypatch.setattr(seen, "mark", mark)
    monkeypatch.setattr(browser_runtime, "available", lambda: (True, ""))
    monkeypatch.setattr(browser_runtime, "applies_running", lambda: 0)
    monkeypatch.setattr(company_skills, "instructions_for", lambda *a: "")

    async def fake_browser(*args, **kwargs):
        state.browser_calls.append(dict(kwargs))
        return {"jobs": deepcopy(state.browser_rows), "board": "greenhouse",
                "note": "Synthetic browser listing"}, None

    monkeypatch.setattr(browser_runtime, "run_task", fake_browser)

    def feed(self, company, client):
        state.feed_calls.append(company.board)
        if state.feed_error:
            raise RuntimeError("synthetic board outage")
        return [j.model_copy(deep=True) for j in state.feed]

    monkeypatch.setattr(GreenhouseAdapter, "fetch", feed)
    return state


def test_exact_browser_listing_feed_to_fresh_persisted_alias(world):
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    assert len(world.browser_calls) == 1
    assert world.browser_calls[0]["kind"] == "crawl"
    assert world.browser_calls[0]["urls"] == [CAREERS]
    assert world.feed_calls == [BOARD]
    row = world.tracking.rows[f"{COMPANY}#731205"]
    assert (row["pk"], row["job_id"], row["jd_url"], row["ats"],
            row["greenhouse_board"]) == (
                f"{COMPANY}#731205", "731205", WRAPPER, "greenhouse", BOARD)
    assert world.queue.calls == [("synthetic-queue", {"pk": row["pk"]})]
    assert world.marked == [[WRAPPER]]
    proof = forms._configured_alias(row["pk"], COMPANY, WRAPPER)
    assert isinstance(proof, forms.GreenhouseJobAlias)
    assert proof.url == HOSTED
    assert forms._alias_current(proof)
    assert forms.navigation_allowed(HOSTED, WRAPPER, provenance=proof)
    assert world.tracking.alias_reads == [row["pk"], row["pk"]]


@pytest.mark.parametrize("feed", [
    [], [board_job("123")], [board_job(), board_job()],
    [board_job(url="https://job-boards.greenhouse.io/other-board/jobs/731205")],
    [board_job(url="https://job-boards.greenhouse.io/example-board/jobs/999999")],
    [board_job(url="http://job-boards.greenhouse.io/example-board/jobs/731205")],
    [board_job(url="https://foreign.test/example-board/jobs/731205")],
])
def test_feed_witness_missing_duplicate_or_contradictory_stays_crawl(world, feed):
    world.feed = feed
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    assert len(world.tracking.rows) == 1
    row = next(iter(world.tracking.rows.values()))
    assert row["ats"] == "crawl"
    assert row["job_id"] == "platform-engineer?gh_jid=731205"
    assert "greenhouse_board" not in row
    assert forms._configured_alias(row["pk"], COMPANY, WRAPPER) is None


def test_unreadable_feed_stays_ordinary_and_queues(world):
    world.feed_error = True
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    assert next(iter(world.tracking.rows.values()))["ats"] == "crawl"
    assert len(world.queue.calls) == 1


def test_only_matching_posting_promoted_when_second_is_missing_from_board(world):
    world.browser_rows.append(listing("/openings/another?gh_jid=731206"))
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 2
    by_url = {r["jd_url"]: r for r in world.tracking.rows.values()}
    assert by_url[WRAPPER]["greenhouse_board"] == BOARD
    other = by_url[CAREERS + "/another?gh_jid=731206"]
    assert other["ats"] == "crawl"
    assert "greenhouse_board" not in other


def test_ambiguous_duplicate_wrapper_witness_does_not_pick_first(world):
    world.browser_rows.append(listing("/openings/other?gh_jid=731205"))
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 2
    assert all(r["ats"] == "crawl" for r in world.tracking.rows.values())


@pytest.mark.parametrize("url", [
    "https://foreign.test/openings/platform-engineer?gh_jid=731205",
    "https://careers.example-co.test/elsewhere/engineer?gh_jid=731205",
    "http://careers.example-co.test/openings/engineer?gh_jid=731205",
    "https://careers.example-co.test/openings/engineer?board=example-board&gh_jid=731205",
    "https://careers.example-co.test/openings/engineer?gh_jid=731205&x=1",
])
def test_wrong_scope_or_noncanonical_query_is_not_promoted(world, url):
    world.browser_rows = [listing(url)]
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    assert next(iter(world.tracking.rows.values()))["ats"] == "crawl"
    assert not world.feed_calls


def test_missing_deployed_authority_keeps_browser_crawl_not_greenhouse(world):
    world.write("companies:\n  - name: example-co\n    careers_url: " +
                CAREERS + "\n    discovery: browser\n")
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    row = next(iter(world.tracking.rows.values()))
    assert row["ats"] == "crawl"
    assert "greenhouse_board" not in row
    assert not world.feed_calls


def test_duplicate_watchlist_company_cannot_authorize_wrapper(world):
    data = (world.root / "watchlist.yaml").read_text()
    world.write(data + "\n  - name: EXAMPLE-CO\n    ats: greenhouse\n"
                "    board: example-board\n    discovery: browser\n"
                f"    careers_url: {CAREERS}\n")
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 1
    assert next(iter(world.tracking.rows.values()))["ats"] == "crawl"
    assert not world.feed_calls


def test_sitemap_early_return_never_promotes_matching_gh_jid(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "")
    monkeypatch.setattr(crawler, "sitemap_jobs", lambda *a: [
        JobRecord(company=COMPANY, job_id="sitemap-platform-engineer",
                  title="Platform Engineer", jd_url=WRAPPER, jd_text="sitemap",
                  ats="sitemap")])
    monkeypatch.setattr(crawler, "_browser_extract",
                        lambda *a: pytest.fail("sitemap did not early-return"))
    company = config(discovery=DiscoveryMode.CRAWL)
    assert crawler.crawl_company(company, Preferences(), world.stores) == 1
    row = next(iter(world.tracking.rows.values()))
    assert (row["ats"], row["job_id"]) == ("sitemap", "sitemap-platform-engineer")
    assert "greenhouse_board" not in row
    assert world.feed_calls == []


def test_crawl_mode_browser_fallback_never_promotes(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "<html>static</html>")
    monkeypatch.setattr(crawler, "_default_extractor", lambda *a: [])
    monkeypatch.setattr(crawler, "sitemap_jobs", lambda *a: [])
    company = config(discovery=DiscoveryMode.CRAWL)
    assert crawler.crawl_company(company, Preferences(), world.stores) == 1
    row = next(iter(world.tracking.rows.values()))
    assert row["ats"] == "crawl"
    assert "greenhouse_board" not in row
    assert len(world.browser_calls) == 1
    assert world.feed_calls == []


def test_injected_extractor_never_promotes_even_in_browser_mode(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "<html>static</html>")
    fake = lambda *a: [JobRecord(company=COMPANY, job_id="plain-slug",
                                 title="Platform Engineer", jd_url=WRAPPER,
                                 jd_text="plain extraction", ats="custom")]
    assert crawler.crawl_company(world.config, Preferences(), world.stores,
                                 extractor=fake) == 1
    row = next(iter(world.tracking.rows.values()))
    assert row["ats"] == "custom" and "greenhouse_board" not in row
    assert world.feed_calls == []


def test_new_identity_does_not_backfill_or_overwrite_existing_crawl_row(world):
    # Same exact pk as the promoted candidate already exists from an older scan.
    pk = f"{COMPANY}#731205"
    old = dict(pk=pk, company=COMPANY, job_id="731205", ats="crawl",
               jd_url=WRAPPER, status="found")
    world.tracking.rows[pk] = deepcopy(old)
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    assert world.tracking.rows[pk] == old
    assert world.queue.calls == []
