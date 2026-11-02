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


def test_owner_answer_consumes_once_and_banks_only_after_winning(world):
    stores, queue = world
    row = _gate(stores)
    _, results = _post({"disposition": "answer", "answer": " Synthetic Name ", **_tokens(row)})
    assert [r["result"] for r in results] == ["queued"]
    after = stores.tracking.get(PK)
    assert after["status"] == "tailored" and after["gate_disposition"]["kind"] == "answer"
    # The owner answer still consumes a single gate and reaches the ordinary
    # answer bank, but no BrowserSkill per-field grant is minted.
    assert "human_approved_answers" not in after
    assert after["gate_disposition"]["identity"] == _tokens(row)["expected_question_identity"]
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


def _checkbox(stores):
    return _gate(stores, _question(
        type="checkbox", label="Synthetic acknowledgement", question="Synthetic acknowledgement",
        control_label="Synthetic acknowledgement", visible_text="Synthetic acknowledgement",
        requiredness="required"))


@pytest.mark.parametrize("winner", ["yes", "no"])
def test_checkbox_yes_and_no_for_one_generation_have_one_winner(world, winner):
    stores, queue = world
    row = _checkbox(stores)
    tokens = _tokens(row)
    loser = "no" if winner == "yes" else "yes"
    first = run.resume_job(PK, winner, stores, disposition="answer", **tokens)
    won = deepcopy(stores.tracking.get(PK))
    second = run.resume_job(PK, loser, stores, disposition="answer", **tokens)
    assert first["result"] == ("queued" if winner == "yes" else "gated")
    assert second["result"] == "gated" and second["reason"] == "invalid_answer"
    # The losing reply leaves the winner's row exactly as it committed.
    assert stores.tracking.get(PK) == won
    if winner == "yes":
        assert won["status"] == "tailored" and _queued(queue) == [PK]
        assert won["gate_disposition"]["kind"] == "answer"
        assert "human_approved_answers" not in won
    else:
        assert first["reason"] == "cannot_proceed"
        assert won["status"] == "needs_human" and won["gate_reason"] == "cannot_proceed"
        assert won["gate_call_id"] is None and won["gate_disposition"]["kind"] == "refuse"
        assert "human_approved_answers" not in won and _queued(queue) == []
    # A consumed affirmative answer is now an ordinary reusable owner fact,
    # not a selector-bound BrowserSkill grant. The losing reply never banks.
    assert stores.answer_bank.puts == (
        [('Synthetic acknowledgement', 'yes')] if winner == 'yes' else [])


def test_checkbox_no_that_loses_the_consume_has_no_row_effect(world, monkeypatch):
    stores, queue = world
    row = _checkbox(stores)
    tokens = _tokens(row)
    original = stores.tracking.consume_direct_form_gate

    def racing(pk, **kwargs):
        # A competing "yes" commits after this "no" passed validation.
        assert original(pk, expected_pending=kwargs["expected_pending"],
                        disposition={**kwargs["disposition"], "kind": "answer"},
                        grant=("Synthetic acknowledgement", {"value": "yes"}))
        return original(pk, **kwargs)

    monkeypatch.setattr(stores.tracking, "consume_direct_form_gate", racing)
    won_status = []
    result = run.resume_job(PK, "no", stores, disposition="answer", **tokens)
    after = stores.tracking.get(PK)
    assert result["reason"] == "invalid_answer", won_status
    assert after["status"] == "tailored" and after["gate_disposition"]["kind"] == "answer"
    assert after["human_approved_answers"] == {"Synthetic acknowledgement": {"value": "yes"}}


def test_tokened_answer_never_approves_a_now_current_non_form_gate(world, monkeypatch):
    """Issue #202 AC6: a formerly valid tokened Answer that arrives after the
    pending gate became a non-form "Ready to apply?" approval names a question
    that no longer exists. It never approves, banks or enqueues."""
    stores, queue = world
    row = _gate(stores)
    tokens = _tokens(row)
    stores.tracking.set_status(PK, Status.NEEDS_HUMAN, gate_reason="approval",
                               gate_call_id="call_x", gate_source="applier",
                               gate_pending={"question": "Ready to apply? Synthetic."})
    before = deepcopy(stores.tracking.get(PK))
    enqueued = []
    monkeypatch.setattr(run, "_enqueue_apply", lambda *a, **k: enqueued.append(a) or {})
    for kwargs in ({"disposition": "answer", **tokens}, tokens):
        result = run.resume_job(PK, "Synthetic Name", stores, **kwargs)
        assert result["result"] == "gated" and result["reason"] == "invalid_answer"
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and enqueued == [] and _queued(queue) == []
    # The ordinary untokened approval still works.
    monkeypatch.undo()
    monkeypatch.setattr(run, "make_stores", lambda *a, **kw: stores)
    assert run.resume_job(PK, "approved", stores)["result"] == "queued"


def test_a_skip_does_not_survive_its_question_being_reasked_at_another_selector(world):
    """Issue #202 AC6: SKIP at selector A, the same question moves to B and is
    asked again, then returns to A. The old SKIP(A) record was spent by the
    re-ask at B and never suppresses the field at A."""
    stores, _ = world
    at_a = _question()
    at_b = _question(selector="body > div:nth-of-type(2) > input:nth-of-type(1)")
    row = _gate(stores, at_a)
    assert run.resume_job(PK, "", stores, disposition="skip", **_tokens(row))[
        "result"] == "queued"
    old = forms.owner_question_identity(PK, at_a)
    assert old in stores.tracking.get(PK)["owner_gate_skips"]
    assert forms.owner_question_identity(PK, at_b) != old
    assert run._owner_skip_retention(PK, stores, at_b) == {"owner_gate_skips": {}}
    # Another question, or the same text as another kind, keeps the record.
    assert run._owner_skip_retention(
        PK, stores, _question(label="Other synthetic", question="Other synthetic")) == {}
    assert run._owner_skip_retention(PK, stores, _question(
        selector="body > textarea", tag="textarea", type="textarea")) == {}


# Issue #300: a background refusal is observational, never a second answer gate.
def _record_refusals(world, monkeypatch):
    from core import events, memory

    stores, _ = world
    redis = stores.tracking.r
    monkeypatch.setattr(events, "_redis", lambda: redis)
    monkeypatch.setattr(events, "get_settings", lambda: SimpleNamespace(mode="local"))
    monkeypatch.setattr(memory, "remember", lambda *args, **kwargs: None)
    original = events.emit
    attempts = []

    def emit(kind, **kwargs):
        attempts.append((kind, kwargs))
        return original(kind, **kwargs)

    monkeypatch.setattr(events, "emit", emit)
    return attempts, redis


def _refusal_event(attempts):
    assert len(attempts) == 1, "one report attempt, not guaranteed delivery"
    kind, payload = attempts[0]
    assert kind == "response"
    assert set(payload) == {
        "pk", "detail", "resume_answer_refused", "refusal_reason",
        "same_pending_gate_at_emit",
    }
    assert payload["pk"] == PK
    assert payload["detail"] == "Owner reply not accepted."
    assert payload["resume_answer_refused"] is True
    assert payload["refusal_reason"] == "invalid_answer"
    assert type(payload["same_pending_gate_at_emit"]) is bool
    # No page/answer/question/bearer or internal gate correlation token.
    assert not any(x in str(payload) for x in (
        "Synthetic display", "Synthetic Name", "nth-of-type", FORM_URL,
        "expected_gate_generation", "expected_question_identity",
        "q1_", "generation", "secret",
    ))
    return payload


@pytest.mark.parametrize("case", [
    "stale_generation", "foreign_identity", "generationless",
    "invalid_checkbox", "invalid_radio", "invalid_group",
    "ineligible_skip", "missing_gate",
])
def test_background_refusal_emits_once_without_any_application_effect(world, monkeypatch, case):
    from core import events

    stores, queue = world
    attempts, redis = _record_refusals(world, monkeypatch)
    question = _question()
    if case == "invalid_checkbox":
        question = _question(type="checkbox", control_label="Synthetic required assent",
                             requiredness="required")
    if case == "invalid_radio":
        question = _question(type="radio", radio_members=[
            {"caption": "Yes", "value": "yes"},
            {"caption": "No", "value": "no"}])
    if case == "invalid_group":
        question = _question(type="checkbox_group", options=["Synthetic A", "Synthetic B"])
    if case == "ineligible_skip":
        question = _question(requiredness="required")
    row = _gate(stores, question, generation=case != "generationless")
    tokens = _tokens(row)
    body = {"disposition": "answer", "answer": "Synthetic Name", **tokens}
    if case == "stale_generation":
        body["expected_gate_generation"] = "stale-synthetic-generation"
    if case == "foreign_identity":
        body["expected_question_identity"] = forms.owner_question_identity(
            PK, _question(selector="body > synthetic-other"))
    if case == "generationless":
        body["expected_gate_generation"] = "synthetic-pre-upgrade"
        body["expected_question_identity"] = forms.owner_question_identity(PK, question)
    if case == "invalid_checkbox":
        body["answer"] = "maybe"
    if case == "invalid_radio":
        body["answer"] = "Neither"
    if case == "invalid_group":
        body["answer"] = '["Synthetic A", "Synthetic A"]'
    if case == "ineligible_skip":
        body = {"disposition": "skip", **tokens}
    if case == "missing_gate":
        stores.tracking.set_status(PK, Status.NEEDS_HUMAN,
                                   gate_reason="owner_question", gate_pending={},
                                   gate_call_id=None, gate_source=None)
    before = deepcopy(stores.tracking.get(PK))
    ack, results = _post(body)
    assert ack == {"ok": True, "status": "resuming"}
    assert len(results) == 1 and results[0]["result"] == "gated"
    assert results[0]["reason"] == "invalid_answer"
    payload = _refusal_event(attempts)
    assert payload["same_pending_gate_at_emit"] is (case in (
        "invalid_checkbox", "invalid_radio", "invalid_group", "ineligible_skip"))
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and _queued(queue) == []
    delivered = events.recent(400)
    assert len(delivered) == 1
    assert delivered[0]["kind"] == "response"
    assert delivered[0]["resume_answer_refused"] is True
    assert delivered[0]["same_pending_gate_at_emit"] is payload["same_pending_gate_at_emit"]
    assert "Synthetic Name" not in str(delivered[0])


def test_background_wrapper_keeps_original_return_and_one_resume_invocation(world, monkeypatch):
    stores, queue = world
    attempts, _ = _record_refusals(world, monkeypatch)
    row = _gate(stores)
    original = run.resume_job
    calls = []

    def exactly_once(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(run, "resume_job", exactly_once)
    body = {"disposition": "answer", "answer": "Synthetic Name",
            **_tokens(row)}
    body["expected_gate_generation"] = "stale-synthetic-generation"
    background = BackgroundTasks()
    assert _endpoint()(PK, body, background) == {"ok": True, "status": "resuming"}
    assert len(background.tasks) == 1
    result = background.tasks[0].func(*background.tasks[0].args,
                                      **background.tasks[0].kwargs)
    assert len(calls) == 1
    assert result == {"result": "gated", "pk": PK, "reason": "invalid_answer",
                      "detail": result["detail"]}
    assert len(attempts) == 1 and not _queued(queue)
    assert stores.answer_bank.puts == []

    # The immediate syntax gate never schedules or reports anything.
    background = BackgroundTasks()
    assert _endpoint()(PK, {"disposition": "answer", "answer": ""}, background) == {
        "ok": False, "error": "invalid_answer"}
    assert len(background.tasks) == 0 and len(calls) == 1 and len(attempts) == 1


def test_late_generation_a_cas_loser_reports_history_not_new_question(world, monkeypatch):
    stores, queue = world
    attempts, _ = _record_refusals(world, monkeypatch)
    a = _gate(stores)
    tokens = _tokens(a)
    original = run.resume_job
    won = []
    b_rows = []

    def race(pk, answer, **kwargs):
        # Another actor wins the one-winner CAS, then a different owner gate B
        # is issued before the losing background task can report its result.
        won.append(original(pk, "", stores, disposition="skip", **tokens))
        b_rows.append(_gate(stores, _question(
            label="Synthetic next question", question="Synthetic next question")))
        return original(pk, answer, stores, **kwargs)

    monkeypatch.setattr(run, "resume_job", race)
    ack, results = _post({"disposition": "answer", "answer": "Synthetic Name", **tokens})
    assert ack["status"] == "resuming"
    assert len(won) == 1 and won[0]["result"] == "queued"
    assert len(results) == 1 and results[0]["reason"] == "invalid_answer"
    assert _refusal_event(attempts)["same_pending_gate_at_emit"] is False
    assert stores.tracking.get(PK) == b_rows[0]
    assert b_rows[0]["gate_pending"]["generation"] != a["gate_pending"]["generation"]
    assert stores.answer_bank.puts == [] and _queued(queue) == [PK]


def test_telemetry_failure_cannot_replace_result_or_retry(world, monkeypatch):
    from core import events

    stores, queue = world
    row = _gate(stores)
    body = {"disposition": "answer", "answer": "Synthetic Name",
            **_tokens(row)}
    body["expected_gate_generation"] = "stale-synthetic-generation"
    before = deepcopy(stores.tracking.get(PK))
    attempted = []
    original = run.resume_job
    resumed = []

    def one_resume(*args, **kwargs):
        resumed.append(True)
        return original(*args, **kwargs)

    def failed_emit(*args, **kwargs):
        attempted.append((args, kwargs))
        raise RuntimeError("synthetic telemetry failure")

    monkeypatch.setattr(run, "resume_job", one_resume)
    monkeypatch.setattr(events, "emit", failed_emit)
    _, results = _post(body)
    assert len(results) == 1 and results[0]["reason"] == "invalid_answer"
    assert len(resumed) == len(attempted) == 1
    assert stores.tracking.get(PK) == before
    assert stores.answer_bank.puts == [] and _queued(queue) == []


@pytest.mark.parametrize("case", ["answer", "skip", "safe_decline", "legacy",
                                   "not_gated", "post_consume_queue_error"])
def test_non_pre_effect_refusals_never_acquire_diagnostic_marker(world, monkeypatch, case):
    stores, queue = world
    attempts, _ = _record_refusals(world, monkeypatch)
    if case == "not_gated":
        ack, results = _post({"answer": "approved"})
        assert ack["ok"] and results[0]["result"] == "not_gated"
    elif case == "legacy":
        stores.tracking.set_status(PK, Status.NEEDS_HUMAN, gate_reason="approval",
                                   gate_call_id="synthetic-call", gate_source="applier",
                                   gate_pending={"question": "Ready to apply? Synthetic."})
        _, results = _post({"answer": "approved"})
        assert results[0]["result"] == "queued"
    else:
        question = _question(
            type="checkbox", control_label="Synthetic required assent",
            requiredness="required") if case == "safe_decline" else _question()
        old = _gate(stores, question)
        body = {"disposition": "skip" if case == "skip" else "answer", **_tokens(old)}
        if case != "skip":
            body["answer"] = "no" if case == "safe_decline" else "Synthetic Name"
        if case == "post_consume_queue_error":
            def broken(self, *args, **kwargs):
                raise ConnectionError("synthetic queue failure")
            monkeypatch.setattr(ApplyQueue, "put", broken)
        _, results = _post(body)
        assert len(results) == 1
        assert results[0]["result"] == ("gated" if case == "safe_decline"
                                         else "error" if case == "post_consume_queue_error"
                                         else "queued")
        if case == "safe_decline":
            assert results[0]["reason"] == "cannot_proceed"
    assert attempts == [], "success, consumed safe decline and queue errors are not refusals"


def test_global_400_sse_replay_does_not_expand_to_2000_job_log(world, monkeypatch):
    from core import events

    stores, _ = world
    _, redis = _record_refusals(world, monkeypatch)
    refusal = {"kind": "response", "pk": PK, "detail": "Owner reply not accepted.",
               "at": "2026-01-01T00:00:00+00:00", "resume_answer_refused": True,
               "refusal_reason": "invalid_answer", "same_pending_gate_at_emit": False}
    redis.lpush(events.HISTORY, json.dumps(refusal))
    for n in range(399):
        redis.lpush(events.HISTORY, json.dumps({
            "kind": "step", "pk": "example-co#other", "detail": str(n)}))
    assert any(e.get("resume_answer_refused") for e in events.recent(400))
    redis.lpush(events.HISTORY, json.dumps({
        "kind": "step", "pk": "example-co#other", "detail": "400"}))
    assert not any(e.get("resume_answer_refused") for e in events.recent(400))
    job_log = next(r.endpoint for r in server.create_app().routes
                   if getattr(r, "path", "") == "/job-log/{pk:path}")
    assert job_log(PK)["events"] == [refusal]
    for n in range(1601):
        redis.lpush(events.HISTORY, json.dumps({
            "kind": "step", "pk": "example-co#other", "detail": str(n)}))
    assert not any(e.get("resume_answer_refused") for e in job_log(PK)["events"])
