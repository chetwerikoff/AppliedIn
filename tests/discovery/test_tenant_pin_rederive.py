"""Unchanged #118/#138 tenant pin and application-entry scopes (Issue #201).

Synthetic tests only: successful feed discovery may not rewrite these consumers.
"""
from __future__ import annotations

import pytest

from core.ids import make_pk
from core.models import DiscoveryMode
from discovery.tenant_pin import (
    greenhouse_query_id, greenhouse_suffix_id, reviewed_tuple,
)
from discovery.watchlist import CompanyConfig
from tools.browser_skill_apply import _application_entry
from tools.claude_chrome import verified_greenhouse_alias


NAME = "example-co"
JID = "731205"
CAREERS = "https://careers.example-co.test/openings"
POSTING = "https://job-boards.greenhouse.io/example-board/jobs/731205"
UUID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.mark.parametrize("url,query_id,suffix_id", [
    (CAREERS + "?gh_jid=" + JID, None, None),
    (CAREERS + "/staff-platform?gh_jid=" + JID, JID, None),
    (CAREERS + "/staff-platform/job/" + JID, None, JID),
    (CAREERS + "/job/" + JID, None, JID),
    (CAREERS + "/job/" + JID + "?ref=1", None, None),
    (CAREERS + "/staff-platform?GH_JID=" + JID, None, None),
    ("https://careers.example-co.test/openings-archive/job/" + JID, None, None),
    ("http://careers.example-co.test/openings/job/" + JID, None, None),
    ("https://careers.example-co.test:443/openings/job/" + JID, None, None),
    ("https://careers.example-co.test/openings/job/%3731205", None, None),
    ("https://careers.example-co.test/openings/job/" + JID + "/extra", None, None),
])
def test_original_tenant_pin_query_and_suffix_grammars_remain_distinct(
    url, query_id, suffix_id,
):
    assert greenhouse_query_id(url, CAREERS) == query_id
    assert greenhouse_suffix_id(url, CAREERS) == suffix_id


def test_tenant_pin_pathless_source_never_becomes_a_reviewed_proof():
    root = "https://careers.example-co.test"
    assert greenhouse_query_id(root + "/?gh_jid=" + JID, root) is None
    assert greenhouse_suffix_id(root + "/job/" + JID, root) is None


@pytest.mark.parametrize("source,allowed", [
    (CAREERS, True),
    ("https://careers.example-co.test", False),
    (CAREERS + "/", False),
])
def test_original_deployed_tuple_requires_exact_nonpathless_source(
    tmp_path, source, allowed,
):
    manifest = tmp_path / "watchlist.yaml"
    manifest.write_text(
        "companies:\n"
        "  - name: example-co\n"
        f"    careers_url: {source}\n"
        "    ats: greenhouse\n"
        "    board: example-board\n"
        "    discovery: crawl\n"
        "    mode: gated\n"
    )
    cfg = CompanyConfig(
        name=NAME, careers_url=source, ats="greenhouse",
        board="example-board", discovery=DiscoveryMode.CRAWL)
    proof = reviewed_tuple(NAME, path=manifest, expected=cfg, crawl_only=True)
    assert (proof is not None) is allowed


@pytest.mark.parametrize("careers,wrapper", [
    (CAREERS, CAREERS + "?gh_jid=" + JID),
    (CAREERS, CAREERS + "/staff-platform?gh_jid=" + JID),
    ("https://careers.example-co.test",
     "https://careers.example-co.test/?gh_jid=" + JID),
])
def test_existing_greenhouse_guard_keeps_old_pinned_wrapper_but_refuses_canonical_start(
    careers, wrapper,
):
    cfg = CompanyConfig(name=NAME, careers_url=careers,
                        ats="greenhouse", board="example-board")
    pk = make_pk(NAME, JID)
    old = dict(pk=pk, company=NAME, job_id=JID, ats="greenhouse",
               greenhouse_board="example-board", jd_url=wrapper)
    proof = verified_greenhouse_alias(
        old, pk=pk, company=NAME, url=wrapper, configs=[cfg])
    assert proof is not None and proof.url == POSTING
    canonical = dict(old, jd_url=POSTING)
    assert verified_greenhouse_alias(
        canonical, pk=pk, company=NAME, url=POSTING, configs=[cfg]) is None


def test_current_application_entry_refuses_dotted_ashby_without_widening_board_token():
    pk = make_pk(NAME, UUID)
    url = f"https://jobs.ashbyhq.com/example.co/{UUID}"
    cfg = CompanyConfig(name=NAME, ats="ashby", board="example.co",
                        careers_url="https://jobs.ashbyhq.com/example.co")
    row = dict(pk=pk, company=NAME, ats="ashby", job_id=UUID, jd_url=url)
    assert _application_entry(row, pk, NAME, url, [cfg]) is None
    allowed_url = f"https://jobs.ashbyhq.com/example-co/{UUID}"
    accepted = CompanyConfig(name=NAME, ats="ashby", board="example-co",
                             careers_url="https://jobs.ashbyhq.com/example-co")
    assert _application_entry(
        dict(row, jd_url=allowed_url), pk, NAME, allowed_url, [accepted]) is not None
