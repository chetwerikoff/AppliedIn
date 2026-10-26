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


def _no_new_gh_application_effects(world):
    assert world.tracking.put_calls == []
    assert world.tracking.rows == {}
    assert world.queue.calls == []
    assert world.marked == []
    assert world.seen_urls == set()


def test_exact_browser_wrapper_and_fresh_feed_are_observation_not_new_submit_authority(world):
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    assert len(world.browser_calls) == 1
    assert world.browser_calls[0]["kind"] == "crawl"
    assert world.browser_calls[0]["urls"] == [CAREERS]
    assert world.feed_calls == [BOARD]
    _no_new_gh_application_effects(world)


def test_original_historic_wrapper_can_keep_existing_submit_alias_without_new_discovery(world):
    # Existing historic rows are not migrated or retagged. Only *new* GH URLs
    # are refused because the unchanged consumer needs the old wrapper+pin.
    pk = f"{COMPANY}#731205"
    existing = dict(pk=pk, company=COMPANY, job_id="731205",
                    jd_url=WRAPPER, ats="greenhouse",
                    greenhouse_board=BOARD, status="found")
    world.tracking.rows[pk] = deepcopy(existing)
    proof = forms._configured_alias(pk, COMPANY, WRAPPER)
    assert isinstance(proof, forms.GreenhouseJobAlias)
    assert proof.url == HOSTED
    assert forms._alias_current(proof)
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    assert world.tracking.rows[pk] == existing
    assert not world.tracking.put_calls and not world.queue.calls


@pytest.mark.parametrize("feed", [
    [], [board_job("123")], [board_job(), board_job()],
    [board_job(url="https://job-boards.greenhouse.io/other-board/jobs/731205")],
    [board_job(url="https://job-boards.greenhouse.io/example-board/jobs/999999")],
    [board_job(url="http://job-boards.greenhouse.io/example-board/jobs/731205")],
    [board_job(url="https://foreign.test/example-board/jobs/731205")],
])
def test_missing_duplicate_or_contradictory_feed_never_falls_back_to_generic_crawl(world, feed):
    world.feed = feed
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


def test_unreadable_greenhouse_feed_is_not_permission_for_custom_crawl(world):
    world.feed_error = True
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


def test_second_unproved_gh_job_still_cannot_fall_back_to_crawl(world):
    world.browser_rows.append(listing("/openings/another?gh_jid=731206"))
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


def test_duplicate_same_id_query_wrappers_do_not_pick_a_winner(world):
    world.browser_rows.append(listing("/openings/other?gh_jid=731205"))
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


@pytest.mark.parametrize("url", [
    "https://foreign.test/openings/platform-engineer?gh_jid=731205",
    "https://careers.example-co.test/elsewhere/engineer?gh_jid=731205",
    "http://careers.example-co.test/openings/engineer?gh_jid=731205",
    "https://careers.example-co.test/openings/engineer?board=example-board&gh_jid=731205",
    "https://careers.example-co.test/openings/engineer?gh_jid=731205&x=1",
    "https://careers.example-co.test/openings/engineer?gh%255Fjid=731205",
    "https://careers.example-co.test/openings/engineer?gh%25255Fjid=731205",
    "https://careers.example-co.test/openings/engineer?gh%252525255Fjid=731205",
])
def test_wrong_scope_or_encoded_and_duplicate_gh_claims_never_fallback(world, url):
    world.browser_rows = [listing(url)]
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


def test_unreviewed_configured_tuple_cannot_make_gh_wrapper_crawl_safe(world):
    world.write("companies:\n  - name: example-co\n    careers_url: " +
                CAREERS + "\n    discovery: browser\n")
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)
    assert not world.feed_calls


def test_duplicate_raw_watchlist_cannot_grant_gh_source(world):
    data = (world.root / "watchlist.yaml").read_text()
    world.write(data + "\n  - name: EXAMPLE-CO\n    ats: greenhouse\n"
                "    board: example-board\n    discovery: browser\n"
                f"    careers_url: {CAREERS}\n")
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)
    assert not world.feed_calls


def test_sitemap_early_return_never_enqueues_gh_query_as_generic(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "")
    monkeypatch.setattr(crawler, "sitemap_jobs", lambda *a: [
        JobRecord(company=COMPANY, job_id="sitemap-platform-engineer",
                  title="Platform Engineer", jd_url=WRAPPER,
                  jd_text="Synthetic sitemap text", ats="sitemap")])
    monkeypatch.setattr(crawler, "_browser_extract",
                        lambda *a: pytest.fail("sitemap did not early-return"))
    assert crawler.crawl_company(
        config(discovery=DiscoveryMode.CRAWL), Preferences(), world.stores) == 0
    _no_new_gh_application_effects(world)


def test_crawl_mode_browser_fallback_cannot_enque_gh_claim(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "<html>static</html>")
    monkeypatch.setattr(crawler, "_default_extractor", lambda *a: [])
    monkeypatch.setattr(crawler, "sitemap_jobs", lambda *a: [])
    assert crawler.crawl_company(
        config(discovery=DiscoveryMode.CRAWL), Preferences(), world.stores) == 0
    assert len(world.browser_calls) == 1
    _no_new_gh_application_effects(world)


def test_injected_extractor_claimed_gh_query_never_enqueues_as_custom(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "<html>static</html>")
    fake = lambda *a: [JobRecord(company=COMPANY, job_id="plain-slug",
                                 title="Platform Engineer", jd_url=WRAPPER,
                                 jd_text="Synthetic extraction", ats="custom")]
    assert crawler.crawl_company(
        world.config, Preferences(), world.stores, extractor=fake) == 0
    _no_new_gh_application_effects(world)


def test_new_identity_does_not_overwrite_an_existing_old_crawl_row(world):
    pk = f"{COMPANY}#731205"
    old = dict(pk=pk, company=COMPANY, job_id="731205", ats="crawl",
               jd_url=WRAPPER, status="found")
    world.tracking.rows[pk] = deepcopy(old)
    assert crawler.crawl_company(world.config, Preferences(), world.stores) == 0
    assert world.tracking.rows[pk] == old
    assert not world.queue.calls and not world.tracking.put_calls


def test_marker_free_custom_posting_keeps_ordinary_crawl_path(world, monkeypatch):
    monkeypatch.setattr(crawler, "_render_page", lambda *_: "<html>static</html>")
    fake = lambda *a: [JobRecord(company=COMPANY, job_id="normal-slug",
                                 title="Test Engineer",
                                 jd_url=CAREERS + "/normal-slug",
                                 jd_text="Synthetic page", ats="custom")]
    assert crawler.crawl_company(
        world.config, Preferences(), world.stores, extractor=fake) == 1
    assert len(world.tracking.rows) == 1
    assert world.queue.calls and world.marked
