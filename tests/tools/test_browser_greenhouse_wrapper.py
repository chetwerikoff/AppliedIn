"""Issue #139 r06: Greenhouse tenant equality gates final Submit, not preparation."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.ids import make_pk
from discovery.watchlist import CompanyConfig
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools.claude_chrome import greenhouse_alias_candidate, verified_greenhouse_alias

COMPANY = "example-co"
BOARD = "example-board"
JID = "731205"
PK = make_pk(COMPANY, JID)
CAREERS = "https://careers.example-co.test/openings"
URL = CAREERS + "/platform-engineer?gh_jid=731205"
HOSTED = "https://job-boards.greenhouse.io/example-board/jobs/731205"


class Tracking:
    def __init__(self):
        self.rows = {PK: dict(pk=PK, company=COMPANY, job_id=JID, jd_url=URL,
                              ats="greenhouse", greenhouse_board=BOARD,
                              status="submitting")}
        self.reads = []
        self.failure = False

    def get(self, pk):
        return deepcopy(self.rows.get(pk))

    def get_alias(self, pk):
        self.reads.append(("consistent", pk))
        if self.failure:
            raise RuntimeError("synthetic strongly consistent read failure")
        return self.get(pk)


@pytest.fixture
def authority(monkeypatch, tmp_path):
    from core import config, stores
    tracking = Tracking()
    state = SimpleNamespace(path=tmp_path / "watchlist.yaml", tracking=tracking,
                            browser_starts=[], config=CompanyConfig(
                                name=COMPANY, careers_url=CAREERS,
                                ats="greenhouse", board=BOARD))
    state.write = lambda body: state.path.write_text(body)
    state.write(f"""companies:
  - name: {COMPANY}
    careers_url: {CAREERS}
    ats: greenhouse
    board: {BOARD}
    discovery: browser
    mode: gated
""")
    monkeypatch.setattr(config, "get_settings",
                        lambda: SimpleNamespace(config_dir=str(tmp_path)))
    monkeypatch.setattr(stores, "make_stores",
                        lambda *a, **kw: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(forms, "check_dispatch", lambda *a, **kw: None)
    def forbidden_session(*args, **kwargs):
        state.browser_starts.append((args, kwargs))
        raise AssertionError("Application browser must not start")
    monkeypatch.setattr(bsk, "Session", forbidden_session)
    return state


async def attempt(url=URL):
    return await forms.apply(url, COMPANY, {}, "synthetic",
                             pk=PK, resume_path="synthetic.pdf")


async def _must_final_tenant_deny(state, *, url=URL):
    """ARCH01:58: even a corrupt row/pin/config never gains Submit IPC."""
    clicks = []
    controller = forms.ApplicationIPCController(
        PK, COMPANY, url, "synthetic.pdf", {}, {}, [], {},
        lambda: clicks.append("submitted"))
    prior_starts = len(state.browser_starts)
    with pytest.raises(forms.Gate):
        await controller.authorize_final_submit(
            None, {"url": HOSTED, "title": "Synthetic role"},
            {"label": "Submit application"})
    assert clicks == []
    assert len(state.browser_starts) == prior_starts


def test_fresh_alias_uses_persisted_pin_and_rechecks(authority):
    proof = forms._configured_alias(PK, COMPANY, URL)
    assert isinstance(proof, forms.GreenhouseJobAlias)
    assert proof.url == HOSTED
    assert forms._alias_current(proof)
    assert authority.tracking.reads == [("consistent", PK), ("consistent", PK)]
    assert not forms.navigation_allowed(
        "https://job-boards.greenhouse.io/other-board/jobs/731205",
        URL, provenance=proof)


@pytest.mark.parametrize(("field", "bad"), [
    ("pk", "wrong#731205"), ("company", "foreign"), ("job_id", "99999"),
    ("jd_url", CAREERS + "/other?gh_jid=731205"), ("ats", "crawl"),
    ("greenhouse_board", "other-board"), ("greenhouse_board", ""),
])
async def test_bad_persisted_identity_refuses_final_commit(authority, field, bad):
    authority.tracking.rows[PK][field] = bad
    await _must_final_tenant_deny(authority)


@pytest.mark.parametrize("bad", [
    "", "other-board", "Example-Board", "example/board",
    "https://boards.greenhouse.io/example-board",
])
async def test_removed_changed_or_malformed_config_board_refuses_final_commit(authority, bad):
    authority.write(f"""companies:
  - name: {COMPANY}
    careers_url: {CAREERS}
    ats: greenhouse
    board: {bad!r}
    discovery: browser
""")
    await _must_final_tenant_deny(authority)


@pytest.mark.parametrize("careers", [
    "https://other.test/openings",
    "https://careers.example-co.test/different",
    "http://careers.example-co.test/openings",
    "https://careers.example-co.test:443/openings",
])
async def test_careers_host_path_or_scheme_drift_refuses_final_commit(authority, careers):
    authority.write(f"""companies:
  - name: {COMPANY}
    careers_url: {careers}
    ats: greenhouse
    board: {BOARD}
""")
    await _must_final_tenant_deny(authority)


async def test_duplicate_company_config_refuses_final_commit(authority):
    authority.write(authority.path.read_text() +
                    f"\n  - name: EXAMPLE-CO\n    careers_url: {CAREERS}\n"
                    f"    ats: greenhouse\n    board: {BOARD}\n")
    await _must_final_tenant_deny(authority)


@pytest.mark.parametrize("malformed", [
    URL + "&extra=1",
    URL + "&gh_jid=731205",
    URL.replace("gh_jid", "%67h_jid"),
    URL.replace("gh_jid", "%252567h_jid"),
    URL.replace("gh_jid", "GH_JID"),
    URL.replace("731205", "abc"),
    URL.replace("731205", "731%32%30%35"),
    URL.replace("?gh_jid", "?board=other-board&gh_jid"),
    URL.replace("?gh_jid", "?x=gh_jid"),
    URL.replace("https://", "http://"),
    URL.replace("careers.example-co.test", "careers.example-co.test.evil.test"),
    URL.replace("/openings/", "/elsewhere/"),
    URL.replace("https://", "https://user@"),
    URL.replace(".test/openings", ".test:443/openings"),
    URL.replace("/platform-engineer", "/platform%2dengineer"),
    URL + "#fragment",
])
async def test_noncanonical_id_attempt_gates_not_custom_apply(authority, malformed):
    if forms._greenhouse_id_query_attempt(malformed) and not greenhouse_alias_candidate(malformed):
        result = await attempt(malformed)
        assert result["status"] == "gate" and result["reason"] == "defect_tenant"
        assert "Malformed Greenhouse job identity" in result["question"]
        assert authority.browser_starts == []
    else:
        # A syntactically parseable alias with a contradictory tracked route
        # is denied by the one final guard, not by a stale early tenant gate.
        await _must_final_tenant_deny(authority, url=malformed)


def test_attempt_detector_does_not_block_unrelated_custom_urls():
    assert not forms._greenhouse_id_query_attempt(
        "https://careers.example-co.test/openings/custom?filter=remote")
    assert not forms._greenhouse_id_query_attempt(
        "https://careers.example-co.test/openings/custom?job_id=731205")
    assert not greenhouse_alias_candidate(
        "https://careers.example-co.test/openings/custom?job_id=731205")


@pytest.mark.parametrize("change", [
    "https://other.test/openings/platform-engineer?gh_jid=731205",
    "https://careers.example-co.test/elsewhere/platform-engineer?gh_jid=731205",
    "http://careers.example-co.test/openings/platform-engineer?gh_jid=731205",
    "https://careers.example-co.test:443/openings/platform-engineer?gh_jid=731205",
    "https://careers.example-co.test/openings/platform-engineer?gh_jid=731205&board=example-board",
    "https://careers.example-co.test/openings/platform-engineer?gh_jid=731206",
])
def test_validator_rejects_noncanonical_employer_witness(authority, change):
    row = {**authority.tracking.rows[PK], "jd_url": change}
    assert verified_greenhouse_alias(row, pk=PK, company=COMPANY, url=change,
           configs=[authority.config]) is None


async def test_unproven_ordinary_crawl_row_refuses_final_commit(authority):
    row = authority.tracking.rows[PK]
    row["ats"] = "crawl"
    row.pop("greenhouse_board")
    await _must_final_tenant_deny(authority)


async def test_legacy_missing_pin_and_board_refuses_final_commit(authority):
    authority.tracking.rows[PK].pop("greenhouse_board")
    authority.write(f"""companies:
  - name: {COMPANY}
    careers_url: {CAREERS}
    ats: greenhouse
    discovery: browser
""")
    assert forms._configured_alias(PK, COMPANY, URL) is None
    await _must_final_tenant_deny(authority)
    assert len(authority.tracking.reads) >= 1


@pytest.mark.parametrize("pin", ["example-board", "", "another-board"])
async def test_pinned_row_with_removed_board_refuses_final_commit(authority, pin):
    authority.tracking.rows[PK]["greenhouse_board"] = pin
    authority.write(f"""companies:
  - name: {COMPANY}
    careers_url: {CAREERS}
    ats: greenhouse
    discovery: browser
""")
    await _must_final_tenant_deny(authority)


async def test_committed_pin_drift_before_final_commit(authority):
    proof = forms._configured_alias(PK, COMPANY, URL)
    assert isinstance(proof, forms.GreenhouseJobAlias)
    # A committed row change is visible on the next dedicated alias read.
    authority.tracking.rows[PK]["greenhouse_board"] = "other-board"
    assert not forms._alias_current(proof)
    await _must_final_tenant_deny(authority)
    assert len(authority.tracking.reads) >= 3


async def test_consistent_row_failure_refuses_final_commit(authority):
    authority.tracking.failure = True
    await _must_final_tenant_deny(authority)


async def test_stale_pin_may_enter_preparation_but_cannot_submit(authority):
    """Accepted r06 tenant relocation: no pre-browser pin denial, final refusal."""
    authority.tracking.rows[PK]["greenhouse_board"] = "another-board"
    result = await attempt()
    assert result["status"] == "unknown"
    # Offline sentinel aborts as soon as the preparation session is requested.
    assert len(authority.browser_starts) == 1
    await _must_final_tenant_deny(authority)
