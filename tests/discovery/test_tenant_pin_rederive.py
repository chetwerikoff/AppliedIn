"""Offline single-key Greenhouse tenant data proof; no application path is called."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.models import JobRecord
from core.storage.base import tenant_pin_row_allowed, tenant_pin_same_witness
from discovery import tenant_pin
from discovery.adapters.ashby import AshbyAdapter
from discovery.adapters.greenhouse import GreenhouseAdapter
from discovery.adapters.lever import LeverAdapter

PK = "example-co#731205"
CAREERS = "https://careers.example-co.test/openings"
WRAPPER = CAREERS + "/platform-engineer?gh_jid=731205"
HOSTED = "https://job-boards.greenhouse.io/example-board/jobs/731205"
UUID = "01234567-89ab-cdef-0123-456789abcdef"


class _Tracking:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.writes = 0

    def get(self, pk):
        return deepcopy(self.row) if self.row and self.row.get("pk") == pk else None

    def add_missing_greenhouse_pin(self, pk, board, *, expected_row):
        if (not tenant_pin_row_allowed(self.row, pk, board)
                or not tenant_pin_same_witness(self.row, expected_row)):
            return False
        self.row["greenhouse_board"] = board
        self.writes += 1
        return True


def _row(**overrides):
    record = dict(
        pk=PK, company="example-co", job_id="731205", ats="greenhouse",
        jd_url=WRAPPER, status="error", fail_kind="defect_greenhouse_tenant_alias",
        gate_reason="defect_greenhouse_tenant_alias",
        apply_requested_at="2026-10-20T01:00:00Z", attempts=3,
        approval_status="pending", approved_at=None,
        jd_read_attempts=1, jd_read_revision=7,
        note="Synthetic pre-browser tenant failure",
    )
    record.update(overrides)
    return record


def _feed(ats="greenhouse", job_id="731205", url=HOSTED):
    return JobRecord(company="example-co", job_id=job_id,
                     title="Staff Platform Engineer", jd_url=url,
                     jd_text="Synthetic JD", location="Remote",
                     posted_at="2026-10-18T00:00:00Z", ats=ats)


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.yaml"
    path.write_text("""companies:
  - name: example-co
    careers_url: https://careers.example-co.test/openings
    ats: greenhouse
    board: example-board
    discovery: crawl
    mode: gated
""")
    state = SimpleNamespace(path=path, rows=[_feed()], tracking=_Tracking(_row()))
    monkeypatch.setattr(GreenhouseAdapter, "fetch",
                        lambda self, cfg, client: deepcopy(state.rows))
    return state


def _once(world, **kwargs):
    return tenant_pin.rederive_one(
        PK, tracking=world.tracking, watchlist_path=world.path, **kwargs)


def test_original_request_only_gate_failure_is_eligible_but_no_approval(world):
    original = deepcopy(world.tracking.row)
    preview = _once(world)
    assert preview["status"] == "dry_run_eligible"
    assert preview["would_pin"] and not preview["committed"]
    assert world.tracking.row == original and world.tracking.writes == 0
    assert _once(world, commit=True)["status"] == "authorization_required"
    assert world.tracking.row == original
    committed = _once(world, commit=True, operator_authorized=True)
    assert committed["status"] == "committed" and committed["committed"]
    assert world.tracking.row == {**original, "greenhouse_board": "example-board"}
    assert world.tracking.writes == 1
    assert _once(world)["status"] == "already_pinned"
    assert world.tracking.writes == 1


@pytest.mark.parametrize("value", ["", None, "wrong-board"])
def test_present_board_is_not_a_missing_board(world, value):
    world.tracking.row["greenhouse_board"] = value
    original = deepcopy(world.tracking.row)
    assert _once(world)["status"] == "conflicting_pin"
    assert world.tracking.row == original and world.tracking.writes == 0


@pytest.mark.parametrize("changed", [
    {"status": "submitting"}, {"status": "applied_manual"},
    {"possible_submission": True}, {"submit_hold": ""},
    {"browser_session_id": "synthetic-browser-session"},
    {"submission_attempted_at": "2026-10-20"},
    {"gate_reason": "submit_uncertain"},
    {"gate_reason": "owner_question"},
    {"last_button": "Submit"},
    {"browser_started_at": ""},
    {"gate_pending": {"question": "synthetic form question"}},
    {"apply_requested_at": "2026-10-21", "gate_reason": "owner_question",
     "fail_kind": "other"},
])
def test_unsafe_original_history_is_never_patched(world, changed):
    world.tracking.row.update(changed)
    original = deepcopy(world.tracking.row)
    assert _once(world)["status"] == "unsafe_history"
    assert world.tracking.row == original and world.tracking.writes == 0


@pytest.mark.parametrize("url", [
    "https://careers.example-co.test/openings/engineer?gh_jid=731205&x=1",
    "https://careers.example-co.test/openings/engineer?gh_jid=731205&gh_jid=731205",
    "https://careers.example-co.test/openings/engineer?gh_jid=%37%33",
    "https://careers.example-co.test/openings/job/731205",
    "http://careers.example-co.test/openings/engineer?gh_jid=731205",
    "https://careers.example-co.test/other/engineer?gh_jid=731205",
])
def test_noncanonical_original_url_needs_manual_identity_review(world, url):
    world.tracking.row["jd_url"] = url
    assert _once(world)["status"] == "noncanonical_wrapper"
    assert world.tracking.writes == 0


@pytest.mark.parametrize("target", [
    "", WRAPPER,
    "https://boards.greenhouse.io/other-board/jobs/731205",
    "https://job-boards.greenhouse.io/example-board/jobs/731206",
    "https://job-boards.greenhouse.io:443/example-board/jobs/731205",
    "https://user@job-boards.greenhouse.io/example-board/jobs/731205",
    "https://job-boards.greenhouse.io/example-board/jobs/731205?x=1",
    "https://fake-job-boards.greenhouse.io/example-board/jobs/731205",
])
def test_missing_or_noncanonical_feed_target_cannot_pin(world, target):
    world.rows = [_feed(url=target)]
    assert _once(world)["status"] == "feed_not_proved"
    assert world.tracking.writes == 0


@pytest.mark.parametrize("jobs", [[], [_feed(job_id="731206")],
                                  [_feed(), _feed()]])
def test_missing_duplicate_or_wrong_feed_identity_fails_closed(world, jobs):
    world.rows = jobs
    assert _once(world)["status"] == "feed_not_proved"
    assert world.tracking.writes == 0


def test_deployed_tuple_must_be_explicit_unique_and_unchanged(world):
    data = world.path.read_text()
    world.path.write_text(data + """
  - name: EXAMPLE-CO
    careers_url: https://careers.example-co.test/openings
    ats: greenhouse
    board: example-board
    discovery: crawl
    mode: gated
""")
    assert _once(world)["status"] == "unproved_tuple"
    assert world.tracking.writes == 0
    world.path.write_text(data.replace("    board: example-board\n", ""))
    assert _once(world)["status"] == "unproved_tuple"


@pytest.mark.parametrize("ats,url", [
    ("ashby", "https://jobs.ashbyhq.com/example-board/" + UUID),
    ("lever", "https://jobs.lever.co/example-board/" + UUID),
])
def test_canonical_ashby_and_lever_are_read_only(world, monkeypatch, ats, url):
    world.path.write_text(world.path.read_text().replace("greenhouse", ats))
    provider = {"ashby": AshbyAdapter, "lever": LeverAdapter}[ats]
    monkeypatch.setattr(provider, "fetch",
                        lambda self, cfg, client: [_feed(ats=ats, job_id=UUID, url=url)])
    pk = "example-co#" + UUID
    original = dict(pk=pk, company="example-co", job_id=UUID, status="found",
                    jd_url=url, ats=ats, attempts=9, approval_status="unanswered")
    tracking = _Tracking(original)
    report = tenant_pin.rederive_one(
        pk, tracking=tracking, watchlist_path=world.path)
    assert report["status"] == "verified_read_only"
    assert tracking.row == original and tracking.writes == 0


def test_provider_identity_and_url_drift_fail_without_writes(world):
    world.tracking.row["pk"] = "example-co#old-slug"
    assert _once(world)["status"] == "missing_row"
    assert world.tracking.writes == 0


