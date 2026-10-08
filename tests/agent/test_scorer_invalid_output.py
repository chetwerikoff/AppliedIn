"""A scorer must produce a current-run ADK-validated MatchScore before tailoring.

Only synthetic Test User / example-co data. The fake implements BaseLlm, so
ADK itself still builds the model requests, validates output_schema and runs
the real root/review SequentialAgent boundaries; no network or browser exists.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.adk.agents import BaseAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from agent import graph as G
from agent import run as R
from core.models import Status
from tools.schema import MatchScore


JD = ("example-co seeks a synthetic platform engineer to maintain test services, "
      "design APIs, validate integration behavior, and document release gates. " * 2)
SEED = r"Test User: synthetic engineer; \section{Experience} API engineering."
SYNTHETIC_STATE = {
    "pk": "example-co#synthetic-1", "company": "example-co",
    "jd_text": JD, "base_latex": SEED, "prefs_brief": "remote; backend engineering",
    "prefs_notes": "no travel", "tailor_note": "", "github_context": "",
}


def _response(text, *, partial=False):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]),
                       partial=partial)


class _OfflineModel(BaseLlm):
    scripted: list[object] = Field(default_factory=list, exclude=True)
    requests: list[object] = Field(default_factory=list, exclude=True)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(deepcopy(llm_request))
        if not self.scripted:
            raise AssertionError("scorer exceeded its generation budget")
        response = self.scripted.pop(0)
        if isinstance(response, BaseException):
            raise response
        for item in response if isinstance(response, tuple) else (response,):
            yield item


class _StrictTracking:
    """Cloud storage dereferences Status.value; string statuses must be rejected."""

    def __init__(self, row):
        self.row = dict(row)
        self.writes = []
        self.r = None

    def get(self, pk):
        return dict(self.row)

    def set_status(self, pk, status, **kwargs):
        assert isinstance(status, Status), f"tracking received non-enum: {status!r}"
        self.writes.append((status, dict(kwargs)))
        self.row.update(status=status.value, **kwargs)


class _NextAgent(BaseAgent):
    tracking: object
    observed: list[int] = Field(default_factory=list)

    async def _run_async_impl(self, ctx):
        # This is the next agent on the actual ADK graph. If a stale score
        # advances the graph, this assertion would see it before any side effect.
        current = MatchScore.model_validate(ctx.session.state.get("match_score"))
        self.observed.append(current.score)
        self.tracking.set_status(SYNTHETIC_STATE["pk"], Status.TAILORED,
                                 resume_s3_key="resumes/synthetic-new.pdf")
        if False:
            yield  # No real tailor, approval or applier can execute.


def _material(req):
    """The constructed model request contains expanded seed/JD/preferences."""
    parts = []
    system = getattr(getattr(req, "config", None), "system_instruction", None)
    if isinstance(system, str):
        parts.append(system)
    elif system is not None:
        parts.extend(p.text or "" for p in (getattr(system, "parts", None) or ()))
    for msg in getattr(req, "contents", ()):
        parts.extend(p.text or "" for p in (getattr(msg, "parts", None) or ()))
    return "\n".join(parts)


@pytest.fixture(params=[False, True], ids=["normal", "prepare_only"])
def harness(monkeypatch, request):
    prep = request.param
    pk = SYNTHETIC_STATE["pk"]
    row = {"pk": pk, "company": "example-co", "title": "Synthetic Platform Engineer",
           "jd_url": "https://example.com/jobs/synthetic", "jd_text": JD,
           "status": "found", "match_score": 9,
           "resume_s3_key": "resumes/historical.pdf"}
    tracking = _StrictTracking(row)
    stores = SimpleNamespace(tracking=tracking)
    fake = _OfflineModel(model="offline-scorer", scripted=[])
    graph = G.review_agent if prep else G.root_agent
    scorer = graph.sub_agents[0]
    next_agent = _NextAgent(name="synthetic_next", tracking=tracking)
    next_agent.parent_agent = graph
    monkeypatch.setattr(scorer, "model", fake)
    monkeypatch.setattr(graph, "sub_agents", [scorer, next_agent])
    sessions = InMemorySessionService()
    monkeypatch.setattr(R, "_session_service", lambda: sessions)
    monkeypatch.setattr(R, "_session_state", lambda row, jd: dict(SYNTHETIC_STATE))
    monkeypatch.setattr(R, "_claim", lambda pk, stores: True)
    monkeypatch.setattr(R, "_release", lambda pk, stores: None)
    monkeypatch.setattr(R, "_min_score", lambda: 7)
    monkeypatch.setattr(G._steering, "instructions", lambda: "Synthetic steering: no invented claims")

    async def _jd(_):
        return JD

    monkeypatch.setattr(R, "_jd_text", _jd)
    saved = []
    monkeypatch.setattr(R, "_save_output", lambda pk, row, jd, stores: saved.append(dict(row)))
    # A previous session can contain 9. Neither the runner nor the scorer may
    # treat that stored value as a current terminal validation witness.
    R._run(sessions.create_session(app_name=R._APP, user_id=R._USER,
                                  session_id=pk,
                                  state={**SYNTHETIC_STATE, "match_score": {"score": 9}}))
    return SimpleNamespace(prep=prep, pk=pk, stores=stores, fake=fake,
                           next=next_agent, saved=saved)


def _run(h):
    return R.run_job(h.pk, h.stores, prepare_only=h.prep)


def _assert_same_context(h):
    assert len(h.fake.requests) == 2
    left, right = map(_material, h.fake.requests)
    for field in (SEED, JD, "remote; backend engineering", "no travel",
                  "example-co", "Synthetic steering: no invented claims"):
        assert field in left and field in right
    assert G._SCORER_FORMAT_CORRECTION not in left
    assert G._SCORER_FORMAT_CORRECTION in right
    canon = lambda text: " ".join(text.split())
    assert canon(left) == canon(right.replace(G._SCORER_FORMAT_CORRECTION, ""))
    assert h.fake.requests[0].model == h.fake.requests[1].model


@pytest.mark.parametrize("score", [7, 9])
def test_initial_valid_score_enters_next_agent_once_without_reask(harness, score):
    h = harness
    h.fake.scripted = [_response(f'{"{"}"score":{score},"reasoning":"fit"{"}"}')]
    _run(h)
    assert len(h.fake.requests) == 1
    assert h.next.observed == [score]
    assert h.stores.tracking.row["match_score"] == score
    assert h.saved[0]["match_score"] == score  # never the stale 9


def test_non_json_then_valid_keeps_original_model_and_material(harness):
    h = harness
    h.fake.scripted = [_response("I will not emit JSON"),
                       _response('{"score":8,"reasoning":"backend fit"}')]
    _run(h)
    _assert_same_context(h)
    assert h.next.observed == [8]
    assert h.stores.tracking.row["match_score"] == 8
    assert h.saved[0]["match_score"] == 8


@pytest.mark.parametrize("responses", [
    [_response("not-json"), _response("still not json")],
    [_response("not-json"), _response("  ")],
    [_response("  "), _response("not-json")],
    [LlmResponse(content=None), LlmResponse(content=None)],
    [(_response('{"score":9,"reasoning":"interim"}', partial=True),
      _response("  ")), _response("  ")],
])
def test_terminal_invalid_stops_both_graphs_without_artifacts(harness, responses):
    h = harness
    h.fake.scripted = list(responses)
    result = _run(h)
    assert result["result"] == "skipped"
    assert result["reason"] == "scorer_invalid_output"
    assert h.stores.tracking.row["status"] == Status.SKIPPED.value
    assert h.stores.tracking.row["skip_reason"] == "scorer_invalid_output"
    assert h.stores.tracking.row["match_score"] is None
    assert h.stores.tracking.row["resume_s3_key"] == "resumes/historical.pdf"
    assert h.next.observed == []
    assert h.saved == []  # no diagnostic, tailoring, approval, enqueue, submit
    assert len(h.fake.requests) == 2


@pytest.mark.parametrize("score,override,expected_next", [(2, False, []), (7, False, [7]),
                                                           (2, True, [2])])
def test_threshold_and_explicit_override(harness, score, override, expected_next):
    h = harness
    h.stores.tracking.row["score_override"] = override
    h.fake.scripted = [_response(f'{"{"}"score":{score},"reasoning":"ordinary fit"{"}"}')]
    result = _run(h)
    assert len(h.fake.requests) == 1
    assert h.next.observed == expected_next
    assert h.stores.tracking.row["match_score"] == score
    if not expected_next:
        assert result["result"] == "skipped"
        assert h.stores.tracking.row["skip_reason"] == "low_score"


def test_schema_valid_refusal_remains_error_without_reask(harness):
    h = harness
    h.fake.scripted = [_response('{"score":0,"reasoning":"As an AI, I cannot score this"}')]
    result = _run(h)
    assert result == {"result": "error", "pk": h.pk, "reason": "scorer_refused"}
    assert h.stores.tracking.row["status"] == Status.ERROR.value
    assert h.next.observed == []
    assert len(h.fake.requests) == 1


@pytest.mark.parametrize("failure", [
    RuntimeError("synthetic provider unavailable"),
    LlmResponse(error_code="SERVICE_UNAVAILABLE", error_message="synthetic provider fault"),
])
def test_provider_failures_are_real_errors_even_for_manual_reopen(harness, failure):
    h = harness
    h.fake.scripted = [failure]
    with pytest.raises(G.ScorerModelError):
        _run(h)
    assert len(h.fake.requests) == 1
    assert h.stores.tracking.row["status"] == Status.ERROR.value
    assert h.stores.tracking.row["match_score"] is None
    assert "Scorer model/request failed" in h.stores.tracking.row["error"]
    assert h.next.observed == []
    assert h.saved == []
