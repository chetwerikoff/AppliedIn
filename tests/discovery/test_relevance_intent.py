"""Prompt-text regressions for stage-1 relevance using synthetic preferences.

These tests intentionally do not run a model: the title/location screen cannot
prove facts from unseen job descriptions or the model's eventual decisions.
"""

from __future__ import annotations

import pytest

from discovery.relevance import _intent
from discovery.watchlist import Preferences


def test_intent_management_targets_and_avoid_list_come_from_preferences():
    brief = _intent(
        Preferences(
            titles=["Technical Program Manager", "Delivery Manager"],
            include_keywords=["cross-team coordination"],
            seniority=["Senior"],
            exclude_keywords=["outbound sales"],
        )
    )
    assert brief.splitlines()[0].startswith(
        "TARGET ROLE: Technical Program Manager, Delivery Manager."
    )
    assert "PREFERRED (raises fit, not required): cross-team coordination, Senior." in brief
    assert "AVOID (never a fit): outbound sales." in brief
    assert "NOT A FIT" not in brief
    assert "Software Engineer" not in brief
    assert "Software Development Engineer" not in brief
    assert "SDE/SWE" not in brief
    for not_configured in (
        "Program Manager (TPM)",
        "Project Manager",
        "Product Manager",
        "people-management role",
    ):
        assert not_configured not in brief


def test_intent_engineering_targets_are_not_an_injected_role_family():
    brief = _intent(
        Preferences(
            titles=["Backend Engineer", "Platform Engineer"],
            include_keywords=["distributed systems"],
            exclude_keywords=["outbound sales"],
        )
    )
    assert brief.splitlines()[0].startswith(
        "TARGET ROLE: Backend Engineer, Platform Engineer."
    )
    assert "PREFERRED (raises fit, not required): distributed systems." in brief
    assert "AVOID (never a fit): outbound sales." in brief
    for unconfigured in (
        "Software Engineer",
        "SDE/SWE",
        "Machine Learning",
        "NOT A FIT",
        "Program Manager",
        "Project Manager",
        "Product Manager",
    ):
        assert unconfigured not in brief


def test_intent_without_titles_has_no_mandatory_role_or_soft_filter():
    brief = _intent(
        Preferences(include_keywords=["coordination"], seniority=["AVP"])
    )
    assert brief.startswith("TARGET ROLE: unspecified (no target titles configured).")
    assert "Do not infer a target job function or require a title/function match." in brief
    assert "Screen using configured hard constraints only;" in brief
    assert "optional and never mandatory role filters." in brief
    assert "PREFERRED (raises fit, not required): coordination, AVP." in brief
    assert "Software Engineer" not in brief
    assert "NOT A FIT" not in brief
    assert "AVOID (never a fit)" not in brief


def test_intent_empty_preferences_does_not_invent_a_function_or_avoids():
    brief = _intent(Preferences())
    assert brief.startswith("TARGET ROLE: unspecified (no target titles configured).")
    assert "Do not infer a target job function or require a title/function match." in brief
    assert "PREFERRED (raises fit, not required)" not in brief
    assert "AVOID (never a fit)" not in brief


@pytest.mark.parametrize(
    "titles",
    [
        ["Technical Program Manager", "Delivery Manager"],
        ["Backend Engineer", "Platform Engineer"],
    ],
)
def test_intent_assesses_title_function_not_corporate_rank(titles):
    brief = _intent(Preferences(titles=titles))
    assert "ROLE FUNCTION VS RANK:" in brief
    assert "functional evidence in each posting title, not corporate-rank tokens" in brief
    assert "Vice President and AVP alone establish neither a job function nor inferred seniority." in brief
    assert "Rank alone is neither a positive function match nor a categorical reason to reject." in brief
    assert "omit rank-only titles from fit_indices" in brief
    assert "a match to the target function is unestablished due to missing functional evidence" in brief
    assert "not because the rank is disallowed" in brief
    assert "For rank-plus-functional-descriptor titles, assess the descriptor" in brief
    assert "against configured target roles, not the corporate rank." in brief
    assert "An explicitly configured rank in PREFERRED remains a soft cue, not proof of function." in brief
    assert "AVOID (never a fit)" not in brief


def test_intent_explicit_rank_seniority_stays_soft_not_a_function_match():
    brief = _intent(
        Preferences(titles=["Backend Engineer"], seniority=["AVP"])
    )
    assert brief.splitlines()[0].startswith("TARGET ROLE: Backend Engineer.")
    assert "PREFERRED (raises fit, not required): AVP." in brief
    assert "An explicitly configured rank in PREFERRED remains a soft cue" in brief
    assert "AVOID (never a fit)" not in brief


def test_intent_location_remote_and_hard_notes_wording_is_unchanged():
    brief = _intent(
        Preferences(
            titles=["Technical Program Manager"],
            locations=["Region A", "Region B"],
            remote_only=True,
            notes="  No around-the-clock on-call rotation.  ",
        )
    )
    location = (
        "LOCATION — REQUIRED: Region A, Region B. Reject any posting whose on-site "
        "location is clearly OUTSIDE of: Region A, Region B. Remote within those "
        "counts. A blank or unknown location is allowed (don't reject on a "
        "missing location alone)."
    )
    assert location in brief.splitlines()
    assert "REMOTE ONLY: reject any posting that is clearly on-site or hybrid." in brief.splitlines()
    assert (
        "HARD CONSTRAINTS (reject any posting that violates these):\n"
        "No around-the-clock on-call rotation."
    ) in brief


def test_intent_empty_titles_still_observes_explicit_hard_avoids():
    brief = _intent(Preferences(exclude_keywords=["outbound sales"]))
    assert brief.startswith("TARGET ROLE: unspecified (no target titles configured).")
    assert "AVOID (never a fit): outbound sales." in brief
    assert "PREFERRED (raises fit, not required)" not in brief
