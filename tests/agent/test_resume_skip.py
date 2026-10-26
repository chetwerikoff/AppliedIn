"""Issue #202: exact owner Answer/SKIP for a bound direct field gate.

The owner's reply names one exact pending question (an opaque identity) and
one freshly minted gate generation. Only one reply can consume that gate, in
one atomic row commit, before any answer-bank write, grant or queue effect.

Everything here is synthetic and offline: fakeredis tracking, a recording
answer bank, the real apply queue on fakeredis and the real FastAPI endpoint
whose background task is run by hand so the EVENTUAL outcome is asserted.
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi import BackgroundTasks

import server
from agent import run
from core.apply_queue import ApplyQueue
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tools import browser_skill_apply as forms

JOB = "00000000-0000-4000-8000-000000000202"
PK = f"example-co#{JOB}"
JD_URL = f"https://jobs.ashbyhq.com/example-co/{JOB}"
FORM_URL = JD_URL + "/application"


def _question(**changes):
    question = {
        "pk": PK, "label": "Synthetic display name", "question": "Synthetic display name",
        "control_label": "Synthetic preferred name",
        "selector": "body > div:nth-of-type(1) > input:nth-of-type(1)",
        "url": FORM_URL, "tag": "input", "type": "text",
        "form_selector": "", "form_action": "",
        "owner_kind": "ashby-reversible-entry",
        "route": "ashby-sibling-mismatch", "requiredness": "unknown",
    }
    question.update(changes)
    return {k: v for k, v in question.items() if v is not None}


class _Bank:
    def __init__(self):
        self.puts = []

    def put(self, question, answer, scope, **kw):
        self.puts.append((question, answer))


@pytest.fixture
def world(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    assert tracking.put_new(JobRecord(
        company="example-co", job_id=JOB, title="Synthetic role", jd_url=JD_URL,
        jd_text="", location="Synthetic location", ats="ashby"))
    stores = SimpleNamespace(tracking=tracking, answer_bank=_Bank())
    monkeypatch.setattr(run, "make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr(server, "make_stores", lambda *a, **kw: stores)
    monkeypatch.setattr("core.stores.make_stores", lambda *a, **kw: stores)
    return stores, ApplyQueue(client)


def _gate(stores, question=None, *, generation=True):
    question = _question() if question is None else question
    pending = run._direct_gate_pending("Synthetic owner question", question, "owner")
    if not generation:
        pending.pop("generation")
    stores.tracking.set_status(PK, Status.NEEDS_HUMAN, gate_reason="owner_question",
                               gate_pending=pending, gate_call_id="direct",
                               gate_source="applier")
    return stores.tracking.get(PK)


def _tokens(row):
    view = server._owner_gate_tokens(row)
    return {"expected_gate_generation": view["gate_generation"],
            "expected_question_identity": view["gate_question_identity"]}


def _endpoint():
    return next(r.endpoint for r in server.create_app().routes
                if getattr(r, "path", "") == "/actions/resume/{pk}")


def _post(body):
    """The HTTP acknowledgement plus the eventual background results."""
    background = BackgroundTasks()
    ack = _endpoint()(PK, body, background)
    return ack, [task.func(*task.args, **task.kwargs) for task in background.tasks]


def _queued(queue):
    return [item["pk"] for item in queue.pending()]


def test_each_issued_gate_gets_a_fresh_generation_and_legacy_gates_none():
    question = _question()
    first = run._direct_gate_pending("Same text", question, "owner")
    second = run._direct_gate_pending("Same text", deepcopy(question), "owner")
    assert first["generation"] != second["generation"]
    assert len(first["generation"]) >= 32
    assert {k: v for k, v in first.items() if k != "generation"} == {
        k: v for k, v in second.items() if k != "generation"}
    assert "generation" not in run._direct_gate_pending("Prose gate", None, "owner")
    assert "generation" not in run._direct_gate_pending("Hard refusal", question, None)


def test_projection_is_opaque_and_offers_skip_only_where_eligible(world):
    stores, _ = world
    row = _gate(stores)
    view = server._owner_gate_tokens(row)
    assert view["gate_skip_eligible"] is True
    assert view["gate_generation"] == row["gate_pending"]["generation"]
    identity = view["gate_question_identity"]
    assert identity.startswith("q1_") and len(identity) == 35
    for private in ("Synthetic", "nth-of-type", "ashbyhq", "display"):
        assert private not in identity
    # The token names the exact bound question: any change is another question.
    assert forms.owner_question_identity(PK, _question(selector="body > input")) != identity
    for ineligible in (_question(requiredness="required"), _question(type="checkbox"),
                       _question(route=None, requiredness=None),
                       _question(tag="button", type="button")):
        assert server._owner_gate_tokens(_gate(stores, ineligible))["gate_skip_eligible"] is False
    stale = _gate(stores, generation=False)
    assert server._owner_gate_tokens(stale) == {
        "gate_question_identity": "", "gate_generation": "", "gate_skip_eligible": False}


def test_explicit_skip_consumes_once_without_bank_or_grant_then_continues(world):
    stores, queue = world
    row = _gate(stores)
    ack, results = _post({"disposition": "skip", **_tokens(row)})
    assert ack == {"ok": True, "status": "resuming"}, "acknowledgement only"
    assert [r["result"] for r in results] == ["queued"]
    after = stores.tracking.get(PK)
    assert after["status"] == "tailored" and after["gate_pending"] == {}
    assert after["gate_call_id"] is None and after["gate_source"] is None
    identity = _tokens(row)["expected_question_identity"]
    assert after["gate_disposition"]["kind"] == "skip"
    assert after["owner_gate_skips"] == {identity: {
        "identity": identity, "route": "ashby-sibling-mismatch", "kind": "text",
        "label": "Synthetic display name", "generation": row["gate_pending"]["generation"]}}
    assert "human_approved_answers" not in after
    assert stores.answer_bank.puts == []
    assert _queued(queue) == [PK]


def test_answer_grant_rides_the_consume_and_banks_only_after_winning(world):
    stores, queue = world
    row = _gate(stores)
    _, results = _post({"disposition": "answer", "answer": " Synthetic Name ", **_tokens(row)})
    assert [r["result"] for r in results] == ["queued"]
    after = stores.tracking.get(PK)
    assert after["status"] == "tailored" and after["gate_disposition"]["kind"] == "answer"
    grant = after["human_approved_answers"]["Synthetic display name"]
    assert grant["value"] == "Synthetic Name" and grant["selector"] == row[
        "gate_pending"]["form_question"]["selector"]
    assert stores.answer_bank.puts == [("Synthetic display name", " Synthetic Name ")]
    assert _queued(queue) == [PK]


def test_two_replies_for_one_generation_have_one_winner_and_no_loser_effects(world):
    stores, queue = world
    row = _gate(stores)
    tokens = _tokens(row)
    first = run.resume_job(PK, "", stores, disposition="skip", **tokens)
    second = run.resume_job(PK, "Synthetic Name", stores, disposition="answer", **tokens)
    assert first["result"] == "queued"
    assert second == {"result": "gated", "pk": PK, "reason": "invalid_answer",
                      "detail": second["detail"]}
    after = stores.tracking.get(PK)
    assert after["gate_disposition"]["kind"] == "skip"
    assert "human_approved_answers" not in after and stores.answer_bank.puts == []
    assert _queued(queue) == [PK]


def test_cas_loser_after_validation_has_no_bank_grant_or_enqueue(world, monkeypatch):
    stores, queue = world
    row = _gate(stores)
    tokens = _tokens(row)
    original = stores.tracking.consume_direct_form_gate

    def racing(pk, **kwargs):
        # A competing SKIP commits after this answer passed validation.
        assert original(pk, expected_pending=kwargs["expected_pending"],
                        disposition={**kwargs["disposition"], "kind": "skip"},
                        skip=("q1_other", {"identity": "q1_other"}))
        return original(pk, **kwargs)

    monkeypatch.setattr(stores.tracking, "consume_direct_form_gate", racing)
    enqueued = []
    monkeypatch.setattr(run, "_enqueue_apply", lambda *a, **k: enqueued.append(a) or {})
    result = run.resume_job(PK, "Synthetic Name", stores, disposition="answer", **tokens)
    assert result["result"] == "gated" and result["reason"] == "invalid_answer"
    assert stores.answer_bank.puts == [] and enqueued == []
    assert "human_approved_answers" not in stores.tracking.get(PK)


@pytest.mark.parametrize("body", [
    {"answer": ""}, {"disposition": "answer", "answer": "   "},
    {"disposition": "maybe", "answer": "x"},
    {"disposition": "skip"},
    {"disposition": "skip", "expected_gate_generation": "", "expected_question_identity": "q"},
])
def test_syntactically_invalid_requests_refuse_before_scheduling(world, body):
    stores, queue = world
    row = _gate(stores)
    before = deepcopy(row)
    ack, results = _post(body)
    if body == {"answer": ""}:
        # Legacy shape: accepted for processing, then refused by exact validation.
        assert ack["ok"] is True
        assert [r["reason"] for r in results] == ["invalid_answer"]
    else:
        assert ack == {"ok": False, "error": "invalid_answer"} and results == []
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and _queued(queue) == []


@pytest.mark.parametrize("change", ["generation", "identity", "no-disposition", "literal-skip"])
def test_stale_foreign_or_untokened_replies_refuse_before_effects(world, change):
    stores, queue = world
    row = _gate(stores)
    before = deepcopy(row)
    tokens = _tokens(row)
    kwargs = {"disposition": "answer", **tokens}
    answer = "Synthetic Name"
    if change == "generation":
        kwargs["expected_gate_generation"] = "an-older-generation"
    elif change == "identity":
        kwargs["expected_question_identity"] = forms.owner_question_identity(
            PK, _question(label="Other", question="Other"))
    elif change == "no-disposition":
        kwargs = {}
    else:
        # A literal "skip" is never a SKIP command.
        kwargs, answer = {}, "skip"
    result = run.resume_job(PK, answer, stores, **kwargs)
    assert result["result"] == "gated" and result["reason"] == "invalid_answer"
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and _queued(queue) == []


@pytest.mark.parametrize("question", [
    _question(requiredness="required"), _question(type="checkbox", visible_text="x"),
    _question(route=None, requiredness=None),
])
def test_known_required_assent_or_root_proven_unknown_never_skips(world, question):
    stores, queue = world
    row = _gate(stores, question)
    before = deepcopy(row)
    result = run.resume_job(PK, "", stores, disposition="skip", **_tokens(row))
    assert result["result"] == "gated" and result["reason"] == "invalid_answer"
    assert stores.tracking.get(PK) == before and _queued(queue) == []


def test_generationless_direct_form_gate_refuses_every_caller_until_reissued(world):
    stores, queue = world
    row = _gate(stores, generation=False)
    before = deepcopy(row)
    identity = forms.owner_question_identity(PK, row["gate_pending"]["form_question"])
    for kwargs, answer in (({}, "Synthetic Name"),
                           ({"disposition": "answer", "expected_gate_generation": "direct",
                             "expected_question_identity": identity}, "Synthetic Name"),
                           ({"disposition": "skip", "expected_gate_generation": "direct",
                             "expected_question_identity": identity}, "")):
        result = run.resume_job(PK, answer, stores, **kwargs)
        assert result["result"] == "gated" and result["reason"] == "invalid_answer"
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and _queued(queue) == []
    # A later bounded pass re-issues the same question with a fresh generation.
    fresh = _gate(stores)
    assert run.resume_job(PK, "", stores, disposition="skip", **_tokens(fresh))[
        "result"] == "queued"


def test_post_consume_enqueue_failure_is_truthful_and_never_reconsumed(world, monkeypatch):
    stores, queue = world
    row = _gate(stores)
    tokens = _tokens(row)

    def broken(self, *a, **kw):
        raise ConnectionError("synthetic queue outage")

    monkeypatch.setattr(ApplyQueue, "put", broken)
    result = run.resume_job(PK, "", stores, disposition="skip", **tokens)
    assert result == {"result": "error", "pk": PK, "reason": "apply_queue_tracking_error"}
    after = stores.tracking.get(PK)
    assert after["status"] == "tailored" and after["gate_pending"] == {}
    assert after["gate_call_id"] is None
    again = run.resume_job(PK, "", stores, disposition="skip", **tokens)
    # The spent gate is gone: the same reply cannot consume or enqueue again.
    assert again["result"] == "gated" and again["reason"] == "invalid_answer"
    assert stores.tracking.get(PK)["gate_disposition"] == after["gate_disposition"]


def test_legacy_non_form_approval_keeps_its_semantics_and_cannot_skip(world):
    stores, queue = world
    stores.tracking.set_status(PK, Status.TAILORED, gate_reason="approval",
                               gate_call_id="call_x", gate_source="applier",
                               gate_pending={"question": "Ready to apply? Synthetic."})
    refused = run.resume_job(PK, "", stores, disposition="skip")
    assert refused["reason"] == "invalid_answer"
    assert run.resume_job(PK, "approved", stores)["result"] == "queued"
    assert stores.answer_bank.puts == []


def test_skip_records_clear_on_root_gate_and_on_regeneration_of_that_question(world):
    stores, _ = world
    row = _gate(stores)
    assert run.resume_job(PK, "", stores, disposition="skip", **_tokens(row))[
        "result"] == "queued"
    skipped = stores.tracking.get(PK)["owner_gate_skips"]
    identity = next(iter(skipped))
    other = _question(label="Other synthetic", question="Other synthetic")
    assert run._owner_skip_retention(PK, stores, other) == {}
    assert run._owner_skip_retention(PK, stores, _question()) == {"owner_gate_skips": {}}
    rooted = _question(route=None, requiredness=None)
    assert run._owner_skip_retention(PK, stores, rooted) == {"owner_gate_skips": {}}
    assert run._owner_skip_retention(PK, stores, None) == {"owner_gate_skips": {}}
    assert identity == forms.owner_question_identity(PK, _question())
    assert json.dumps(skipped)  # plain, storable record
