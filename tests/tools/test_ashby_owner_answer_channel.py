"""Issue #262: rooted Ashby questions must use the exact direct owner reply.

Offline Airwallex/OpenAI DOM shapes flow through the real form controller,
applier gate persistence and resume gate consumption. Nothing is submitted.
"""
from __future__ import annotations

from types import SimpleNamespace

import fakeredis
import pytest

import server
from agent import run
from core.apply_queue import ApplyQueue
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tests.tools.test_browser_application_entry import COMPANY, JOB, PK, posting
from tests.tools.test_browser_ashby_survey import _ATTESTATIONS, _FACTS, _LIVE, _LOCATION
from tests.tools.test_browser_formless import no_mutations, root_world
from tools import browser_skill_apply as forms


class _Bank:
    def __init__(self, facts):
        self.facts = dict(facts)
        self.writes = []

    def all_facts(self, company):
        assert company == COMPANY
        return dict(self.facts)

    def _load(self):
        # Production bootstrap inspects only the global bank partition.
        return {"global": list(self.facts.items())}

    def put(self, label, answer, scope, **kwargs):
        self.writes.append((label, answer.strip()))
        self.facts[label] = answer.strip()


def _world(monkeypatch, shape, facts, *, name_field=None, stale=False,
           attestations=False):
    spec = ({**_LIVE[shape], **_ATTESTATIONS}
            if attestations else dict(_LIVE[shape]))
    _, raw, decision, _ = root_world(monkeypatch, **spec)
    if name_field is not None:
        label, kind = name_field
        # Change both independent synthetic readers consistently. The original
        # Ashby root/selector and form identity are not relaxed.
        for snapshot in (raw.current, raw.fixed):
            for item in snapshot["controls"] + snapshot["entry"]["root_evidence"]:
                if item.get("selector") == "#_systemfield_name":
                    item["label"] = label
                    item["type"] = kind
                    item["tag"] = "textarea" if kind == "textarea" else "input"

    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    assert tracking.put_new(JobRecord(
        company=COMPANY, job_id=JOB, title="Synthetic role",
        jd_url=posting("ashby"), jd_text="Synthetic role text",
        location="Synthetic city", ats="ashby"))
    bank = _Bank(facts)
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank, secrets=None)
    if stale:
        # An older grant for the exact question key but a different receiver
        # must be re-asked, never replayed or silently treated as approval.
        tracking.set_status(
            PK, Status.TAILORED, human_approved_answers={
                "Name": {"value": "Old synthetic value", "selector": "#prior"}})

    # Exercise the actual `_apply_direct` -> BrowserSkill -> gate row path.
    # The unrelated JD/rotation/PDF transport is synthetic and offline.
    async def jd_meta(_row, *, yielding=True):
        return {"text": "Synthetic role text"}

    monkeypatch.setattr(run, "_jd_meta", jd_meta)
    monkeypatch.setattr(
        run, "_commit_posting_meta",
        lambda pk, row, meta, current: current.tracking.get(pk))
    monkeypatch.setattr("tools.posting_guard.authorised", lambda *a, **k: True)
    monkeypatch.setattr("core.rotation.ensure", lambda *a, **k: None)
    monkeypatch.setattr("core.profiles.resolve_for", lambda *a, **k: None)
    monkeypatch.setattr("core.profiles.expand_all", lambda values: values)
    monkeypatch.setattr("tools.credentials.get_login", lambda *a, **k: None)
    monkeypatch.setattr("core.events.emit", lambda *a, **k: None)
    monkeypatch.setattr(run, "get_settings",
                        lambda: SimpleNamespace(browser_model="synthetic"))
    monkeypatch.setattr(run, "_resume_pdf_path", lambda row: "Resume.pdf")
    monkeypatch.setattr(run, "_github_context", lambda: "")
    monkeypatch.setattr("tools.browser_runtime.configuration",
                        lambda: {"engine": "browser_skill"})
    monkeypatch.setattr("tools.browser_apply.apply", forms.apply)
    monkeypatch.setattr("core.stores.make_stores", lambda: stores)
    monkeypatch.setattr(forms, "_row", lambda *a, **k: tracking.get(PK) or {})
    return stores, raw, decision


async def _gate(stores, expected_label=None):
    result = await run._apply_direct(PK, stores)
    assert result["result"] == "gated", result
    row = stores.tracking.get(PK)
    assert row["status"] == "needs_human"
    assert row["gate_reason"] == "owner_question"
    assert row["gate_call_id"] == "direct"
    assert row["gate_source"] == "applier"
    question = row["gate_pending"]["form_question"]
    assert question["label"]
    if expected_label is not None:
        assert question["label"] == expected_label
    tokens = server._owner_gate_tokens(row)
    assert tokens["gate_generation"]
    assert tokens["gate_question_identity"] == forms.owner_question_identity(PK, question)
    return row, tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["airwallex", "openai"])
@pytest.mark.parametrize("case,bank_facts,name_field,answer,first", [
    ("salary", {"Desired salary": "Synthetic range"},
     ("Desired salary", "text"), "Approved synthetic range", "Desired salary"),
    ("free-text", {"Cover letter": "Bank prose"},
     ("Cover letter", "textarea"), "Exact owner prose", "Cover letter"),
    ("date", {"Available start date": "2026-11-15"},
     ("Available start date", "date"), "2026-11-16", "Available start date"),
    ("no-bank-fact", {}, None, "Test User", "Name"),
    ("stale-approval", {"Name": "Test User"}, None, "Updated Test User", "Name"),
])
async def test_rooted_owner_question_exact_resume_reaches_next_field(
        monkeypatch, shape, case, bank_facts, name_field, answer, first):
    stores, raw, decision = _world(
        monkeypatch, shape, bank_facts, name_field=name_field,
        stale=case == "stale-approval")
    row, tokens = await _gate(stores, first)

    # No bank-refused candidate is auto-granted or filled, including when
    # the bank already holds its value. A stale grant stays untouched.
    grants = row.get("human_approved_answers") or {}
    if case == "stale-approval":
        assert grants["Name"]["selector"] == "#prior"
    else:
        assert first not in grants
    assert stores.answer_bank.writes == []
    assert decision.await_count == 0
    no_mutations(raw)

    wrong = run.resume_job(
        PK, answer, stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"] + "-wrong",
        expected_question_identity=tokens["gate_question_identity"])
    assert wrong["reason"] == "invalid_answer"
    assert stores.tracking.get(PK)["gate_pending"] == row["gate_pending"]
    assert stores.answer_bank.writes == []
    assert ApplyQueue(stores.tracking.r).pending() == []

    resumed = run.resume_job(
        PK, answer, stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert resumed["result"] == "queued", resumed
    grant = stores.tracking.get(PK)["human_approved_answers"][first]
    assert grant["value"] == answer
    assert grant["selector"] == row["gate_pending"]["form_question"]["selector"]
    assert stores.answer_bank.writes == [(first, answer)]
    # The exact owner approval permits this field on the second run; it does
    # not approve any sibling, submit the form or borrow a bank-auto grant.
    second, next_tokens = await _gate(stores, "Email")
    assert second["gate_pending"]["form_question"]["selector"] != grant["selector"]
    assert next_tokens["gate_generation"] != tokens["gate_generation"]
    assert next_tokens["gate_question_identity"] != tokens["gate_question_identity"]
    assert decision.await_count == 0
    no_mutations(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["airwallex", "openai"])
async def test_voluntary_survey_bank_refusal_has_direct_reply(monkeypatch, shape):
    facts = {**_FACTS, "Synthetic voluntary survey question": "Bank-only response"}
    stores, raw, decision = _world(monkeypatch, shape, facts)
    row, tokens = await _gate(stores, "Synthetic voluntary survey question")
    assert row["gate_pending"]["form_question"]["ashby_survey"] is True
    assert "Synthetic voluntary survey question" not in (
        row.get("human_approved_answers") or {})
    refused = run.resume_job(
        PK, "Exact survey response", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity="q1_wrong")
    assert refused["reason"] == "invalid_answer"
    assert stores.answer_bank.writes == []
    assert decision.await_count == 0
    no_mutations(raw)


@pytest.mark.asyncio
async def test_ashby_hidden_attestation_needs_exact_owner_yes(monkeypatch):
    facts = {**_FACTS, _LOCATION: "Synthetic City"}
    stores, raw, decision = _world(
        monkeypatch, "openai", facts, attestations=True)
    row, tokens = await _gate(stores)
    question = row["gate_pending"]["form_question"]
    assert question["type"] == "checkbox"
    assert question["ashby_hidden_choice"] is True
    assert question["visible"] is False
    assert question["selector"] == "#attestation-0"
    assert run.resume_job(
        PK, "maybe", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])["reason"] == "invalid_answer"
    assert stores.answer_bank.writes == []
    approved = run.resume_job(
        PK, "yes", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert approved["result"] == "queued", approved
    grant = stores.tracking.get(PK)["human_approved_answers"][question["label"]]
    assert grant["value"] == "yes"
    assert grant["selector"] == "#attestation-0"
    # Material assent is never written to the reusable bank.
    assert stores.answer_bank.writes == []
    second, next_tokens = await _gate(stores)
    assert second["gate_pending"]["form_question"]["selector"] == "#attestation-1"
    assert next_tokens["gate_generation"] != tokens["gate_generation"]
    assert decision.await_count == 0
    no_mutations(raw)
