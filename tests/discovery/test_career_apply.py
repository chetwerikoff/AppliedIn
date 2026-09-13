"""An Apply click authorizes exact rows, never the rest of a company's queue."""
# ruff: noqa: F811

from unittest.mock import Mock

import pytest

from core.apply_queue import ApplyQueue
from core.models import Status
from discovery import career_apply
from discovery import career_ops as co
from tests.discovery.test_career_ops import board  # noqa: F401


def test_apply_request_is_durable_before_evaluation_can_start(board):
    stores, rows = board

    def enqueue(queue, message):
        record = stores.tracking.get(message["pk"])
        assert record["apply_requested_at"]
        assert record["discovery_source"] == "career_ops"
        assert queue == stores.tailor_queue

    stores.queue.enqueue.side_effect = enqueue
    result = co.prepare([rows[0]["id"]], apply_requested=True)
    assert len(result["pks"]) == 1
    assert stores.tracking.get("acme#two") is None


def test_manual_apply_scores_then_dispatches_only_selected_job_while_paused(board, monkeypatch):
    from agent import run

    stores, rows = board
    result = co.prepare([rows[0]["id"]], apply_requested=True)
    pk = result["pks"][0]
    queue = ApplyQueue(stores.tracking.r)
    queue.put("acme#unselected", "Acme")
    monkeypatch.setattr("core.flags.paused", lambda: True)

    def tailor(key, st, **kwargs):
        assert key == pk and kwargs["prepare_only"]
        st.tracking.set_status(pk, Status.TAILORED, resume_s3_key="resume.pdf")

    monkeypatch.setattr(run, "run_job", tailor)

    def submit(item, q):
        assert item["pk"] == pk
        q.done(item)

    submitted = Mock(side_effect=submit)
    monkeypatch.setattr(run, "run_queued", submitted)
    career_apply.run_selected([pk], stores)
    assert submitted.call_count == 1
    assert [item["pk"] for item in queue.pending()] == ["acme#unselected"]


@pytest.mark.parametrize(
    "status,pdf",
    [
        (Status.SKIPPED, ""),
        (Status.NEEDS_HUMAN, "resume.pdf"),
        (Status.FAILED, ""),
        (Status.TAILORED, ""),
    ],
)
def test_rejected_missing_resume_or_unanswered_questions_never_submit(
    board, monkeypatch, status, pdf
):
    from agent import run

    stores, rows = board
    pk = co.prepare([rows[0]["id"]], apply_requested=True)["pks"][0]
    monkeypatch.setattr(
        run, "run_job", lambda *_a, **_k: stores.tracking.set_status(pk, status, resume_s3_key=pdf)
    )
    submit = Mock()
    monkeypatch.setattr(run, "run_queued", submit)
    career_apply.run_selected([pk], stores)
    submit.assert_not_called()
    assert not ApplyQueue(stores.tracking.r).flushing()


def test_prepare_only_selection_does_not_authorize_manual_submit(board, monkeypatch):
    from agent import run

    stores, rows = board
    pk = co.prepare([rows[0]["id"]])["pks"][0]
    execute = Mock()
    monkeypatch.setattr(run, "run_job", execute)
    career_apply.run_selected([pk], stores)
    execute.assert_not_called()


@pytest.mark.parametrize(
    "outcome,authorized,expected",
    [
        ("prepared", True, True),
        ("prepared", False, False),
        ("gated", True, False),
        ("skipped", True, False),
        ("failed", True, False),
    ],
)
def test_completed_pipeline_advances_only_explicit_apply_requests(
    board, monkeypatch, outcome, authorized, expected
):
    from agent import run

    stores, rows = board
    pk = co.prepare([rows[0]["id"]], apply_requested=authorized)["pks"][0]

    async def review(*_args, **kwargs):
        assert kwargs["prepare_only"] is True
        return {"result": outcome}

    monkeypatch.setattr(run, "_run_job_async", review)
    queue = Mock(return_value={"result": "queued"})
    monkeypatch.setattr(run, "_enqueue_apply", queue)
    run.run_job(pk, stores)
    assert queue.called is expected
    if expected:
        queue.assert_called_once_with(pk, stores, priority=True)
