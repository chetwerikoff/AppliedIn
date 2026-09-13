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
