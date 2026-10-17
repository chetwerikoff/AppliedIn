import pytest

from core.ids import (
    jd_hash, make_pk, normalize_label, normalize_text, strip_label_decoration,
)


def test_normalize_text_collapses_ws_and_edge_punct():
    assert normalize_text("Build  things.\n") == normalize_text("build things")


def test_normalize_label_matches_synonym_spacing():
    assert normalize_label("Notice period?") == normalize_label("notice  period")


def test_jd_hash_is_deterministic_under_cosmetic_edits():
    assert jd_hash("Build  things.\n") == jd_hash("build things.")


def test_make_pk_lowercases():
    assert make_pk("Acme", "123") == "acme#123"


@pytest.mark.parametrize("variant", [
    "Email", "Email *", "Email ✱", "Email ✳", "✳ Email †",
    " ? email！ ", " *  EMAIL:; ", "Email 。",
])
def test_bounded_required_markers_and_sentence_edges(variant):
    assert normalize_label(variant) == "email"


@pytest.mark.parametrize("question,expected", [
    ("First Name*", "First Name"), ("Email ✱", "Email"),
    ("✳ Email †", "Email"), ("First* Name", "First* Name"),
    ("*", ""), (" ？！ ", ""), ("C++", "C++"),
    ("C#", "C#"), ("$ Salary", "$ Salary"), ("€ Salary", "€ Salary"),
])
def test_canonical_raw_only_strips_finite_edge_decoration(question, expected):
    assert strip_label_decoration(question) == expected


def test_important_signs_do_not_alias_approved_facts():
    assert len({normalize_label(item) for item in ("C", "C++", "C#")}) == 3
    assert normalize_label("C++ experience") != normalize_label("C experience")
    assert normalize_label("$ Salary") != normalize_label("€ Salary")
    assert normalize_label("First* Name") != normalize_label("First Name")
    assert normalize_label("") == normalize_label("*") == ""


def test_original_synonym_separators_are_supported():
    assert normalize_label("Resume/CV") == "resume cv"
    assert normalize_label("Notice period?") == normalize_label("notice period")

def test_unicode_case_comparison_preserves_legacy_sharp_s_identity():
    # Prior cloud SKs used lower(), not casefold(): a semantic expansion to "ss"
    # would make the old row unreachable by the bounded old/new key probes.
    assert normalize_label("Straße") == normalize_label("straße") == "straße"
    assert normalize_label("Strasse") == normalize_label("STRASSE") == "strasse"
    assert normalize_label("Straße") != normalize_label("Strasse")
