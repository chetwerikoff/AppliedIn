"""Offline scorer cap checks: quote membership, not semantic eligibility.

Test User and example-co are synthetic. Real ADK fake models exercise both
normal and prepare-only graphs; no browser, Redis, queue, or submission runs.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy

import pytest
from google.adk.sessions import InMemorySessionService

from agent import graph as G
from agent import run as R
from core.models import Status
from tools.schema import MatchScore

from tests.agent.test_scorer_invalid_output import (
    JD, SEED, SYNTHETIC_STATE, _material, _response, harness,
)

GOOD_QUOTE = "design APIs, validate integration behavior, and document release gates."
TITLE_ONLY = "Principal Remote Platform Engineer with Distributed Services Ownership"
TITLE_QUOTE = "Principal Remote Platform Engineer with Distributed Services Ownership"
GENERIC_AUTH_JD = (
    JD + " Applicants should confirm their right to work in their local region. "
    "example-co reviews applications based on skills and relevant experience."
)
AUTH_QUOTE = "Applicants should confirm their right to work in their local region."


def _payload(score=8, *, kind=None, quote=None, include_quote=True):
    result = {"score": score, "reasoning": "synthetic engineering fit"}
    if kind is not None:
        result["dealbreaker"] = {"kind": kind}
        if include_quote:
            result["dealbreaker"]["quote"] = quote
    return json.dumps(result)


def _fresh_scorer_state(monkeypatch, jd_text, **state_changes):
    """Recreate a scorer session; the separate current-row JD is not evidence."""
    sessions = InMemorySessionService()
    state = {**SYNTHETIC_STATE, "jd_text": jd_text, **state_changes}
    monkeypatch.setattr(R, "_session_service", lambda: sessions)
    monkeypatch.setattr(R, "_session_state", lambda row, jd: dict(state))


def _run(h):
    return R.run_job(h.pk, h.stores, prepare_only=h.prep)


def test_schema_optional_quote_and_legacy_score():
    for payload in (
        {"score": 8, "reasoning": "fit"},
        {"score": 8, "reasoning": "fit", "dealbreaker": None},
        {"score": 8, "reasoning": "fit", "dealbreaker": {"kind": "location"}},
        {"score": 8, "reasoning": "fit",
         "dealbreaker": {"kind": "other", "quote": None}},
        {"score": 8, "reasoning": "fit",
         "dealbreaker": {"kind": "authorization", "quote": "   "}},
        {"score": 8, "reasoning": "fit",
         "dealbreaker": {"kind": "location", "quote": GOOD_QUOTE}},
    ):
        assert MatchScore.model_validate(payload).score == 8
    for bad in (
        {"score": 8, "dealbreaker": {}},
        {"score": 8, "dealbreaker": {"kind": "unknown"}},
        {"score": 8, "dealbreaker": {"kind": "location", "quote": 17}},
    ):
        with pytest.raises(ValueError):
            MatchScore.model_validate(bad)


def test_rendered_instruction_order_and_binding_exceptions(harness, monkeypatch):
    h = harness
    first = ("Target stack: service integration. "
             "LOCATION REQUIRED: example regions. "
             "REMOTE ONLY: on-site roles are disallowed.")
    second = "HARD: no overnight travel for Test User."
    _fresh_scorer_state(monkeypatch, JD, prefs_brief=first, prefs_notes=second)
    h.fake.scripted = [_response(_payload())]
    _run(h)
    text = _material(h.fake.requests[0])
    assert "SOFT PREFERENCES" in text
    assert "HARD CONSTRAINTS (additional notes)" in text
    assert text.index("SOFT PREFERENCES") < text.index(first)
    assert text.index(first) < text.index("HARD CONSTRAINTS (additional notes)")
    assert text.index("HARD CONSTRAINTS (additional notes)") < text.index(second)
    assert "LOCATION REQUIRED and REMOTE ONLY clauses" in text
    assert "binding HARD exceptions" in text
    assert "Exclude all inferred work authorization" in text
    assert "BEFORE any dealbreaker cap" in text
    assert "score 1-2" not in text.lower()
    assert "caps at 3" not in text.lower()
    assert "real dealbreaker caps" not in text.lower()
    assert SEED in text
    assert len(h.fake.requests) == 1


@pytest.mark.parametrize("kind", ["location", "other"])
@pytest.mark.parametrize("source,quote", [
    (JD, GOOD_QUOTE),
    (TITLE_ONLY, TITLE_QUOTE),
])
def test_verified_quote_projects_effective_score_after_raw_witness(
    harness, monkeypatch, kind, source, quote,
):
    h = harness
    _fresh_scorer_state(monkeypatch, source)
    h.stores.tracking.row["score_override"] = True
    witnessed = []
    original = G._SafeScorer._run_async_impl

    async def capture(self, ctx):
        async for event in original(self, ctx):
            if event.author == "scorer" and event.is_final_response():
                text = "".join(p.text or "" for p in event.content.parts)
                witnessed.append((text, deepcopy(
                    event.actions.state_delta.get("match_score")
                )))
            yield event

    monkeypatch.setattr(G._SafeScorer, "_run_async_impl", capture)
    h.fake.scripted = [_response(_payload(kind=kind, quote=quote.swapcase()
                                          .replace(" ", " \n  ")))]
    result = _run(h)
    assert result["result"] != "skipped"
    assert len(h.fake.requests) == 1
    assert len(witnessed) == 1
    raw_text, forwarded = witnessed[0]
    assert json.loads(raw_text)["score"] == 8  # unchanged ADK model witness
    assert forwarded["score"] == 3              # after validation, before yield
    assert forwarded["dealbreaker"]["kind"] == kind
    assert h.next.observed == [3]  # the next ADK agent sees effective score
    assert h.stores.tracking.row["match_score"] == 3
    assert h.saved[0]["match_score"] == 3  # existing persisted score


@pytest.mark.parametrize("kind", ["location", "other"])
def test_code_cap_reaches_ordinary_shortlist_threshold(harness, kind):
    h = harness
    h.fake.scripted = [_response(_payload(kind=kind, quote=GOOD_QUOTE))]
    result = _run(h)
    assert result["result"] == "skipped"
    assert h.stores.tracking.row["skip_reason"] == "low_score"
    assert h.stores.tracking.row["match_score"] == 3
    assert h.next.observed == []  # skip before tailoring or approvals
    assert len(h.fake.requests) == 1


@pytest.mark.parametrize("claim", [
    {"kind": "location"},
    {"kind": "location", "quote": None},
    {"kind": "location", "quote": ""},
    {"kind": "other", "quote": "    "},
    {"kind": "location", "quote": "API"},
    {"kind": "location", "quote": "synthetic platform engineer"},  # 3 words
    {"kind": "other", "quote": "fabricated full-time engineering office location"},
])
def test_insufficient_or_false_quotes_are_non_capping(
    harness, caplog, claim,
):
    h = harness
    h.fake.scripted = [_response(json.dumps(
        {"score": 8, "reasoning": "fit", "dealbreaker": claim}
    ))]
    with caplog.at_level(logging.WARNING):
        _run(h)
    assert len(h.fake.requests) == 1  # optional missing quote is not malformed
    assert h.next.observed == [8]
    assert h.stores.tracking.row["match_score"] == 8
    assert h.saved[0]["match_score"] == 8
    warnings = [r.getMessage() for r in caplog.records
                if "scorer dealbreaker cap ignored" in r.getMessage()]
    assert warnings
    assert all(GOOD_QUOTE not in message and JD not in message for message in warnings)


@pytest.mark.parametrize("location", ["runner-title", "preferences", "current-row"])
def test_quote_outside_scorer_visible_jd_never_caps(
    harness, monkeypatch, location,
):
    h = harness
    phrase = "Remote engineering collaboration across multiple example regions"
    _fresh_scorer_state(monkeypatch, "A synthetic role with distributed systems experience.")
    if location == "runner-title":
        h.stores.tracking.row["title"] = phrase  # in free-form user message
    elif location == "preferences":
        _fresh_scorer_state(monkeypatch,
                            "A synthetic role with distributed systems experience.",
                            prefs_notes=phrase)
    else:
        # The runner reads a new JD, but the reused scorer session still has
        # an old jd_text; this Issue explicitly does not refresh it.
        async def current_jd(_row):
            return JD
        monkeypatch.setattr(R, "_jd_text", current_jd)
        phrase = GOOD_QUOTE
    h.fake.scripted = [_response(_payload(kind="location", quote=phrase))]
    _run(h)
    assert len(h.fake.requests) == 1
    assert h.next.observed == [8]
    assert h.stores.tracking.row["match_score"] == 8


@pytest.mark.parametrize("score,expected_next", [(8, [8]), (2, [])])
def test_authorization_claim_never_adds_code_cap(
    harness, monkeypatch, score, expected_next,
):
    h = harness
    _fresh_scorer_state(monkeypatch, GENERIC_AUTH_JD)
    async def generic_jd(_row):
        return GENERIC_AUTH_JD
    monkeypatch.setattr(R, "_jd_text", generic_jd)
    assert not R._no_sponsorship(GENERIC_AUTH_JD)
    h.fake.scripted = [_response(_payload(
        score=score, kind="authorization", quote=AUTH_QUOTE
    ))]
    result = _run(h)
    assert h.stores.tracking.row["match_score"] == score
    assert h.next.observed == expected_next
    assert len(h.fake.requests) == 1
    if score == 2:
        # A low raw model fit still skips; the code has added no further cap.
        assert result["result"] == "skipped"
        assert h.stores.tracking.row["skip_reason"] == "low_score"
    else:
        assert h.saved[0]["match_score"] == 8


def test_substantive_quote_can_be_misclassified_by_model(harness, monkeypatch):
    # This is an acknowledged limitation, not semantic proof of incompatibility.
    source = JD + " Our teams collaborate across multiple international locations."
    _fresh_scorer_state(monkeypatch, source)
    h = harness
    h.fake.scripted = [_response(_payload(
        kind="other", quote="Our teams collaborate across multiple international locations."
    ))]
    _run(h)
    assert h.stores.tracking.row["match_score"] == 3


def test_lower_raw_fit_is_never_raised_by_a_cap(harness):
    h = harness
    h.fake.scripted = [_response(_payload(
        score=2, kind="location", quote=GOOD_QUOTE
    ))]
    _run(h)
    assert h.stores.tracking.row["match_score"] == 2


def test_explicit_sponsorship_refusal_stops_before_scorer(harness, monkeypatch):
    h = harness
    refusal_jd = JD + " example-co will not sponsor visa applicants."
    assert R._no_sponsorship(refusal_jd)

    async def denied(_row):
        return refusal_jd

    monkeypatch.setattr(R, "_jd_text", denied)
    result = _run(h)
    assert result["reason"] == "no_sponsorship"
    assert h.fake.requests == []  # no model invoked
    assert h.next.observed == []
    assert h.stores.tracking.row["status"] == Status.FAILED.value
