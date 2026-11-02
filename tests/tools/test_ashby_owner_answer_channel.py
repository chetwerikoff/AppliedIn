"""Issue #293: ordinary Ashby owner questions survive without browser grants.

Offline synthetic Ashby pages exercise the direct applier question, an exact
owner reply, requeue and stale-answer refusal. Nothing is submitted.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import fakeredis
import pytest

import server
from agent import run
from core.apply_queue import ApplyQueue
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tests.tools.test_browser_application_entry import COMPANY, JOB, PK, posting
from tests.tools.test_browser_ashby_survey import _LIVE
from tests.tools.test_browser_formless import no_mutations, root_world
from tools import browser_skill_apply as forms
from tools import jd, posting_guard as pg


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
           attestations=False, tmp_path=None):
    spec = ({**_LIVE[shape], 'liveAttestations': True}
            if attestations else dict(_LIVE[shape]))
    _, raw, decision, _ = root_world(monkeypatch, **spec)
    if tmp_path is not None:
        # The production flow prepares the staged PDF before asking for a
        # missing ordinary fact. The offline Ashby facade must represent that
        # reversible upload without touching a browser or inventing a grant.
        pdf = tmp_path / "synthetic-resume.pdf"
        pdf.write_bytes(b"%PDF-1.4\\nsynthetic example-co only\\n")
        original_call = raw.call

        async def synthetic_upload(method, *args, **kwargs):
            if method == "upload":
                assert len(args) == 3 and args[1] == "--file"
                assert Path(args[2]) == pdf
                for snapshot in (raw.current, raw.fixed):
                    matching = [item for item in snapshot["controls"]
                                if item.get("selector") == args[0]
                                and item.get("type") == "file"]
                    assert len(matching) == 1
                    matching[0].update(files=[pdf.name], has_value=True)
                raw.calls.append((method, *args))
                return {}
            return await original_call(method, *args, **kwargs)

        raw.call = synthetic_upload
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
        # Legacy approval entries are inert and cannot authorize a write.
        tracking.set_status(
            PK, Status.TAILORED, human_approved_answers={
                "Name": {"value": "Old synthetic value", "selector": "#prior"}})

    # Keep the source and conditional guard real; only ATS transport is fake.
    def current_source(url, kind):
        assert (url, kind) == (posting("ashby"), "jd")
        text = "Synthetic role text"
        return {**pg.analyze(text, html_variants=[f"<p>{text}</p>"],
                             source_required=True),
                "source_kind": "ats_html", "source_url": url}

    monkeypatch.setattr(jd, "fetch_jd_meta", current_source)
    monkeypatch.setattr("core.rotation.ensure", lambda *a, **k: None)
    monkeypatch.setattr("core.profiles.resolve_for", lambda *a, **k: None)
    monkeypatch.setattr("core.profiles.expand_all", lambda values: values)
    monkeypatch.setattr("tools.credentials.get_login", lambda *a, **k: None)
    monkeypatch.setattr("core.events.emit", lambda *a, **k: None)
    monkeypatch.setattr(run, "get_settings",
                        lambda: SimpleNamespace(browser_model="synthetic"))
    monkeypatch.setattr(run, "_resume_pdf_path",
                        lambda row: str(tmp_path / "synthetic-resume.pdf")
                        if tmp_path is not None else "Resume.pdf")
    monkeypatch.setattr(run, "_github_context", lambda: "")
    monkeypatch.setattr("tools.browser_runtime.configuration",
                        lambda: {"engine": "browser_skill"})
    monkeypatch.setattr("tools.browser_apply.apply", forms.apply)
    monkeypatch.setattr("core.stores.make_stores", lambda: stores)
    monkeypatch.setattr(forms, "_row", lambda *a, **k: tracking.get(PK) or {})
    return stores, raw, decision



async def _owner_gate(stores, decision, raw, label=None, *, selector=None):
    """Use an observed control as the missing owner fact, not model prose."""
    matches = [c for c in raw.current["controls"]
               if not c.get("disabled")
               and (c.get("selector") == selector if selector else c.get("label") == label)]
    assert len(matches) == 1
    decision.return_value = {"action": "gate", "selector": matches[0]["selector"],
                             "question": "Please supply this synthetic form fact"}
    result = await run._apply_direct(PK, stores)
    assert result["result"] == "gated", result
    row = stores.tracking.get(PK)
    assert row["status"] == Status.NEEDS_HUMAN.value
    assert row["gate_reason"] == "owner_question"
    assert row["gate_source"] == "applier"
    assert row["gate_call_id"] == "direct"
    question = row["gate_pending"]["form_question"]
    assert question["label"] == (matches[0].get("question") or
                                  matches[0].get("label") or "Form answer")
    assert question["route"] == "ordinary-owner-question"
    tokens = server._owner_gate_tokens(row)
    assert tokens["gate_generation"]
    assert tokens["gate_question_identity"] == forms.owner_question_identity(PK, question)
    return row, tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["airwallex", "openai"])
async def test_missing_name_is_owner_question_then_fact_and_requeue_without_grant(
        monkeypatch, tmp_path, shape):
    stores, raw, decision = _world(monkeypatch, shape, {}, tmp_path=tmp_path)
    row, tokens = await _owner_gate(stores, decision, raw, "Name")
    assert not row.get("human_approved_answers")
    assert stores.answer_bank.writes == []

    stale = run.resume_job(
        PK, "Test User", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"] + "-stale",
        expected_question_identity=tokens["gate_question_identity"])
    assert stale["reason"] == "invalid_answer"
    assert stores.tracking.get(PK)["gate_pending"] == row["gate_pending"]

    resumed = run.resume_job(
        PK, "Test User", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert resumed["result"] == "queued", resumed
    current = stores.tracking.get(PK)
    assert not current.get("human_approved_answers")
    assert current["status"] == Status.TAILORED.value
    assert stores.answer_bank.writes == [("Name", "Test User")]
    assert ApplyQueue(stores.tracking.r).pending()


@pytest.mark.asyncio
async def test_existing_legacy_grant_cannot_replace_a_fresh_owner_answer(
        monkeypatch, tmp_path):
    stores, raw, decision = _world(
        monkeypatch, "openai", {}, stale=True, tmp_path=tmp_path)
    row, tokens = await _owner_gate(stores, decision, raw, "Name")
    assert row["human_approved_answers"]["Name"]["selector"] == "#prior"
    result = run.resume_job(
        PK, "Updated Test User", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert result["result"] == "queued", result
    current = stores.tracking.get(PK)
    # Upgrade-safety retains legacy data inert, not used as a fresh form grant.
    assert current["human_approved_answers"]["Name"]["selector"] == "#prior"
    assert stores.answer_bank.writes == [("Name", "Updated Test User")]


@pytest.mark.asyncio
async def test_material_checkbox_uses_an_owner_question_not_a_seventh_submit_item(
        monkeypatch, tmp_path):
    stores, raw, decision = _world(
        monkeypatch, "openai", {'Name': 'Test User', 'Email': 'test@example.test'},
        attestations=True, tmp_path=tmp_path)
    row, tokens = await _owner_gate(stores, decision, raw,
                                    selector="#attestation-0")
    question = row["gate_pending"]["form_question"]
    assert question["type"] == "checkbox"
    invalid = run.resume_job(
        PK, "maybe", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert invalid["reason"] == "invalid_answer"
    result = run.resume_job(
        PK, "yes", stores, disposition="answer",
        expected_gate_generation=tokens["gate_generation"],
        expected_question_identity=tokens["gate_question_identity"])
    assert result["result"] == "queued", result
    assert not stores.tracking.get(PK).get("human_approved_answers")
    # Material consent stays company-scoped, not in the global reusable bank.
    assert stores.answer_bank.writes == [(question["label"], "yes")]
