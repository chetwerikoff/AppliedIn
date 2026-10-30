"""Greenhouse #250 source-bound feed and embed admission, offline only.

No live browser, application, external network, approval or submission.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core.ids import make_pk
from core.models import JobRecord
from discovery.adapters.greenhouse import GreenhouseAdapter
from discovery.canonical_job_url import admit_feed, consumer_accepts
from discovery.tenant_pin import (
    exact_feed_match, greenhouse_query_id, rederive_one, reviewed_tuple,
)
from discovery.watchlist import CompanyConfig, load_watchlist
from tools.browser_skill_apply import navigation_allowed, _job_preparation_route
from tools.claude_chrome import verified_greenhouse_alias

NAME, BOARD, JID = "example-co", "example-board", "731205"
PK = make_pk(NAME, JID)
HOSTED = f"https://job-boards.greenhouse.io/{BOARD}/jobs/{JID}"
EMBED = f"https://job-boards.greenhouse.io/embed/job_app?for={BOARD}&token={JID}"

# Six synthetic URL shapes; the names denote URL grammars, not live companies.
SHAPES = [
    ("stripe-apply-path", "https://example-co.test/jobs",
     "https://example-co.test/careers/apply/platform-engineer/731205"),
    ("databricks-apex", "https://example-co.test/company/careers/open-positions",
     "https://example-co.test/company/careers/open-positions/platform?gh_jid=731205"),
    ("nebius-root", "https://careers.example-co.test",
     "https://careers.example-co.test/?gh_jid=731205"),
    ("mongodb-path", "https://www.example-co.test/careers",
     "https://www.example-co.test/careers/platform?gh_jid=731205"),
    ("thoughtworks-path", "https://careers.example-co.test/careers",
     "https://careers.example-co.test/careers/platform?gh_jid=731205"),
    ("scaleai-hosted", "https://example-co.test/careers", HOSTED),
]


def deployed(monkeypatch, tmp_path, careers):
    from core import config
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        f"companies:\n  - name: {NAME}\n    careers_url: {careers}\n"
        f"    ats: greenhouse\n    board: {BOARD}\n"
        "    discovery: feed\n    mode: gated\n"
    )
    monkeypatch.setattr(config, "get_settings",
                        lambda: SimpleNamespace(config_dir=str(tmp_path)))
    return CompanyConfig(name=NAME, careers_url=careers,
                         ats="greenhouse", board=BOARD), path


def job(url, jid=JID):
    return JobRecord(company=NAME, job_id=jid, title="Synthetic Engineer",
                     jd_url=url, jd_text="Synthetic description", ats="greenhouse")


def fetch_rows(config, response):
    def respond(request):
        assert str(request.url) == (
            f"https://boards-api.greenhouse.io/v1/boards/{BOARD}/jobs?content=true")
        return httpx.Response(200, json={"jobs": response})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        return GreenhouseAdapter().fetch(config, client)


@pytest.mark.parametrize("_shape,careers,url", SHAPES)
def test_unique_reviewed_feed_pin_and_exact_alias(monkeypatch, tmp_path,
                                                  _shape, careers, url):
    cfg, path = deployed(monkeypatch, tmp_path, careers)
    assert reviewed_tuple(NAME, path=path, expected=cfg) == cfg
    rows = fetch_rows(cfg, [{"id": int(JID), "absolute_url": url,
                             "title": "Synthetic Engineer"}])
    assert len(rows) == 1 and rows[0].greenhouse_board == BOARD
    assert exact_feed_match(cfg, JID, rows, stored_url=url) is rows[0]
    admitted, refused = admit_feed(cfg, cfg, rows)
    assert len(admitted) == 1 and not refused
    assert admitted[0].greenhouse_board == BOARD
    assert consumer_accepts(admitted[0], cfg)
    proof = verified_greenhouse_alias(
        {"pk": PK, "company": NAME, "job_id": JID, "ats": "greenhouse",
         "jd_url": url, "greenhouse_board": BOARD},
        pk=PK, company=NAME, url=url, configs=[cfg])
    assert proof is not None and proof.url == EMBED
    assert navigation_allowed(EMBED, provenance=proof)
    assert _job_preparation_route(EMBED, url, provenance=proof)
    for bad in (
        url, HOSTED if url != HOSTED else "https://example-co.test/jobs",
        EMBED + "&x=1", EMBED + "&token=731205",
        EMBED.replace("for=example-board", "for=other-board"),
        EMBED.replace("token=731205", "token=731206"),
        EMBED.replace("https://", "http://"),
        EMBED.replace("job-boards.greenhouse.io", "job-boards.greenhouse.io.evil.test"),
        EMBED + "#fragment",
    ):
        assert not navigation_allowed(bad, provenance=proof), bad
        assert not _job_preparation_route(bad, url, provenance=proof), bad


@pytest.mark.parametrize("_shape,careers,url", SHAPES)
def test_feed_mismatch_duplicates_and_missing_review_refuse(monkeypatch, tmp_path,
                                                            _shape, careers, url):
    cfg, path = deployed(monkeypatch, tmp_path, careers)
    rows = fetch_rows(cfg, [{"id": int(JID), "absolute_url": url},
                            {"id": int(JID), "absolute_url": url}])
    assert len(rows) == 2 and not any(j.greenhouse_board for j in rows)
    assert exact_feed_match(cfg, JID, rows, stored_url=url) is None
    admitted, refused = admit_feed(cfg, cfg, rows)
    assert not admitted and len(refused) == 2
    one = job(url)
    assert exact_feed_match(cfg, JID, [one], stored_url=url + "#changed") is None
    assert exact_feed_match(cfg, JID, [job(url, jid="731206")],
                            stored_url=url) is None
    path.write_text("companies: []\n")
    rows = fetch_rows(cfg, [{"id": int(JID), "absolute_url": url}])
    assert not rows[0].greenhouse_board
    admitted, refused = admit_feed(cfg, cfg, rows)
    assert not admitted and refused


def test_waymo_exact_jobs_page_query_requires_feed_pin_and_embed_only(monkeypatch):
    """The /jobs?gh_jid=... form is source-bound, not a redirect permission."""
    from core import config

    watchlist = Path(__file__).resolve().parents[2] / "config" / "watchlist.yaml"
    configs = [item for item in load_watchlist(watchlist) if item.name == "waymo"]
    assert len(configs) == 1
    cfg = configs[0]
    assert (cfg.careers_url, cfg.ats, cfg.board, cfg.discovery.value, cfg.mode.value) == (
        "https://careers.withwaymo.com/jobs", "greenhouse", "waymo", "feed", "gated",
    )
    monkeypatch.setattr(
        config, "get_settings",
        lambda: SimpleNamespace(config_dir=str(watchlist.parent)),
    )
    assert reviewed_tuple(cfg.name, path=watchlist, expected=cfg) == cfg

    # Synthetic ID and a mocked feed: never read a real posting or submit.
    url = f"{cfg.careers_url}?gh_jid={JID}"
    assert greenhouse_query_id(url, cfg.careers_url) == JID
    for invalid in (
        cfg.careers_url + "/?gh_jid=" + JID,
        cfg.careers_url.replace("/jobs", "/other") + "?gh_jid=" + JID,
        cfg.careers_url + "?gh_jid=" + JID + "&other=1",
        cfg.careers_url + "?gh%5Fjid=" + JID,
        cfg.careers_url + "?gh_jid=" + JID + "#fragment",
        "https://foreign.example-co.test/jobs?gh_jid=" + JID,
    ):
        assert greenhouse_query_id(invalid, cfg.careers_url) is None, invalid

    def respond(request):
        assert str(request.url) == (
            "https://boards-api.greenhouse.io/v1/boards/waymo/jobs?content=true"
        )
        return httpx.Response(200, json={"jobs": [
            {"id": int(JID), "absolute_url": url, "title": "Synthetic Engineer"}
        ]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        rows = GreenhouseAdapter().fetch(cfg, client)
    assert len(rows) == 1 and rows[0].greenhouse_board == "waymo"
    assert exact_feed_match(cfg, JID, rows, stored_url=url) is rows[0]
    assert exact_feed_match(cfg, JID, rows, stored_url=url + "&extra=1") is None
    admitted, refused = admit_feed(cfg, cfg, rows)
    assert len(admitted) == 1 and not refused
    assert consumer_accepts(admitted[0], cfg)

    pk = make_pk(cfg.name, JID)
    proof = verified_greenhouse_alias(
        {"pk": pk, "company": cfg.name, "job_id": JID, "ats": "greenhouse",
         "jd_url": url, "greenhouse_board": cfg.board},
        pk=pk, company=cfg.name, url=url, configs=[cfg],
    )
    embed = f"https://job-boards.greenhouse.io/embed/job_app?for=waymo&token={JID}"
    assert proof is not None and proof.url == embed
    assert navigation_allowed(embed, provenance=proof)
    assert _job_preparation_route(embed, url, provenance=proof)
    for invalid in (
        url, f"https://job-boards.greenhouse.io/waymo/jobs/{JID}",
        embed + "&extra=1", embed + "#fragment",
        embed.replace("for=waymo", "for=other"),
        embed.replace(f"token={JID}", "token=731206"),
    ):
        assert not navigation_allowed(invalid, provenance=proof), invalid
        assert not _job_preparation_route(invalid, url, provenance=proof), invalid


def test_root_query_is_not_source_wildcard():
    root = "https://careers.example-co.test"
    assert greenhouse_query_id(root + "/?gh_jid=" + JID, root) == JID
    for invalid in (
        root + "/role?gh_jid=" + JID,
        root + "/?gh_jid=" + JID + "&other=1",
        root + "/?gh%5Fjid=" + JID,
        root + "/?GH_JID=" + JID,
        "https://foreign.example-co.test/?gh_jid=" + JID,
    ):
        assert greenhouse_query_id(invalid, root) is None
    assert greenhouse_query_id(root + "/?gh_jid=" + JID, root + "/") is None


def test_other_board_or_wrong_id_is_never_feed_proof(monkeypatch, tmp_path):
    cfg, _ = deployed(monkeypatch, tmp_path, "https://example-co.test/jobs")
    wrong = [
        "https://job-boards.greenhouse.io/other-board/jobs/731205",
        "https://job-boards.greenhouse.io/example-board/jobs/731206",
        "https://evil.test/?gh_jid=731205",
    ]
    for url in wrong:
        assert exact_feed_match(cfg, JID, [job(url)], stored_url=url) is None
    for field, value in [
        ("greenhouse_board", "wrong-board"), ("job_id", "731206"),
        ("pk", NAME + "#31acd099efc4"), ("ats", "sitemap"),
    ]:
        row = {"pk": PK, "company": NAME, "job_id": JID,
               "jd_url": HOSTED, "ats": "greenhouse",
               "greenhouse_board": BOARD}
        row[field] = value
        assert verified_greenhouse_alias(row, pk=PK, company=NAME,
                                         url=HOSTED, configs=[cfg]) is None


def test_legacy_hash_pk_is_never_rekeyed_or_pinned(monkeypatch, tmp_path):
    _, path = deployed(monkeypatch, tmp_path, "https://careers.example-co.test")
    hashed = NAME + "#31acd099efc4"
    original = {"pk": hashed, "company": NAME, "job_id": JID,
                "jd_url": "https://careers.example-co.test/?gh_jid=731205",
                "ats": "greenhouse", "status": "found"}
    class Tracking:
        def get(self, pk):
            return deepcopy(original) if pk == hashed else None
        def add_missing_greenhouse_pin(self, *_args, **_kwargs):
            pytest.fail("Legacy hash row must never be mutated")
    result = rederive_one(hashed, tracking=Tracking(), watchlist_path=path)
    assert result["status"] == "identity_conflict"
    assert not result["committed"]
