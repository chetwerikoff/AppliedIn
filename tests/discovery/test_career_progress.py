"""Discovery progress must never turn browser activity into a successful apply."""
# ruff: noqa: F811

import pytest

from core.models import Status
from discovery import career_ops as co
from discovery.career_progress import application_progress
from tests.discovery.test_career_ops import board  # noqa: F401


@pytest.mark.parametrize(
    "status,agent,flight,phase",
    [
        ("found", "browser", False, "queued"),
        ("tailoring", "scorer", False, "score"),
        ("tailoring", "tailor", False, "tailor"),
        ("tailoring", "critic", False, "tailor"),
        ("tailored", "applier", False, "waiting"),
        ("tailored", "browser", True, "apply"),
        ("submitting", "browser", True, "apply"),
        ("needs_human", "browser", True, "attention"),
        ("failed", "browser", True, "attention"),
        ("skipped", "scorer", False, "attention"),
        ("applied", "browser", True, "applied"),
    ],
)
def test_durable_outcome_wins_over_browser_events_and_releasing_leases(
    status, agent, flight, phase
):
    progress = application_progress({"status": status}, {"agent": agent}, in_flight=flight)
    assert progress["phase"] == phase


def test_progress_survives_refresh_without_exposing_resume_or_raw_model_output(board, monkeypatch):
    stores, rows = board
    pk = co.prepare([rows[0]["id"]], apply_requested=True)["pks"][0]
    co.prepare([rows[1]["id"]])
    stores.tracking.set_status(
        pk,
        Status.NEEDS_HUMAN,
        gate_pending={"question": "Which office do you prefer?"},
        jd_text="private full description",
        fields=["private answers"],
    )
    monkeypatch.setattr(
        "core.events.recent",
        lambda _: [
            {"pk": pk, "agent": "browser", "detail": "private browser output", "at": "2026-09-13"}
        ],
    )
    data = co.snapshot()
    selected = next(r for r in data["jobs"] if r["id"] == rows[0]["id"])
    assert selected["application"]["phase"] == "attention"
    assert selected["application"]["detail"] == "Which office do you prefer?"
    assert selected["application"]["requested_at"]
    assert "private" not in str(selected)
    assert all("application" not in r for r in data["jobs"] if r["id"] != rows[0]["id"])


@pytest.mark.parametrize("status", ["applied", "applied_manual"])
def test_posting_applied_elsewhere_is_resolved_even_when_board_never_requested_apply(board, status):
    stores, rows = board
    pk = co.prepare([rows[0]["id"]])["pks"][0]
    stores.tracking.set_status(pk, status)
    # Simulate a board saved before another flow processed this posting.
    data = co._read()
    data["jobs"][rows[0]["id"]].pop("pk", None)
    data["jobs"][rows[0]["id"]]["state"] = "new"
    co._write(data)
    visible = next(r for r in co.snapshot()["jobs"] if r["id"] == rows[0]["id"])
    assert visible["pipeline_status"] == status
    assert visible["state"] == "pipeline"
    assert stores.tracking.get(pk)["status"] == status
    assert "application" not in visible, "Do not invent a Career Ops apply request"


def test_a_failed_row_shows_why_it_failed_not_an_old_approval_prompt():
    # A Cohere role was refused under the employer's own cap, yet the board said
    # "Ready to apply? Review the résumé first." — the prompt left over from its
    # preparation — which read as a request for approval.
    from discovery.career_progress import application_progress

    got = application_progress({
        "status": "failed", "fail_kind": "application_limit",
        "fail_reason": "This EMPLOYER refused the submission under its own application cap.",
        "gate_pending": {"question": "Ready to apply? Review the résumé first."},
    })
    assert got["label"] == "Company's application limit"
    assert got["detail"].startswith("This EMPLOYER refused")
    asked = application_progress({"status": "needs_human", "gate_pending": {"question": "Which visa?"}})
    assert asked["detail"] == "Which visa?"
