"""A title exception is one consumed active, uniquely identified seed anchor.

All content is synthetic. Tests call the actual normalizer/Counter guard, not
a new parser or a mocked copy of the validation rule.
"""

import pytest

from core.role_titles import BASENAME, load_approved_titles
from tools.truthfulness import eligible_title_options, validate

A = r"\role{example-co}{Role A}{2022--2024}"
B = r"\role{example-co}{Role Z}{2019--2021}"
NEIGHBOR = r"\role{example-co-lab}{Role X}{2017--2018}"
PRE = r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n"


def doc(*lines):
    return PRE + "\n".join((*lines, r"\resumeItem{Synthetic work.}")) + "\n" + r"\end{document}"


@pytest.fixture
def options(tmp_path):
    (tmp_path / BASENAME).write_text(
        "example-co: [Role B, R&D Role]\nunrelated-co: [Role X]\n",
        encoding="utf-8",
    )
    return load_approved_titles(tmp_path)


def test_one_exact_role_title_change_is_the_only_exception(options):
    seed = doc(A, NEIGHBOR)
    changed = doc(A.replace("Role A", "Role B"), NEIGHBOR)
    assert validate(seed, changed) == [A]
    assert validate(seed, changed, {}) == [A]
    assert validate(seed, changed, options) == []
    assert validate(seed, seed, options) == []
    assert len(eligible_title_options(seed, options)) == 1
    assert eligible_title_options(seed, options)[0].seed_line == A


def test_two_different_roles_same_employer_each_have_independent_candidates(options):
    seed = doc(A, B, NEIGHBOR)
    eligible = eligible_title_options(seed, options)
    assert [o.seed_line for o in eligible] == [A, B]
    assert all(o.approved == ("Role B", r"R\&D Role") for o in eligible)
    assert validate(seed, doc(A.replace("Role A", "Role B"), B, NEIGHBOR), options) == []
    assert validate(seed, doc(A, B.replace("Role Z", "Role B"), NEIGHBOR), options) == []
    assert validate(seed, doc(A.replace("Role A", "Role B"),
                              B.replace("Role Z", "Role B"), NEIGHBOR), options) == []


def test_literal_ampersand_must_use_exact_approved_encoding(options):
    seed = doc(A)
    assert validate(seed, doc(A.replace("Role A", r"R\&D Role")), options) == []
    assert validate(seed, doc(A.replace("Role A", "R&D Role")), options) == [A]


@pytest.mark.parametrize("replacement", [
    A.replace("Role A", "Role C"),
    A.replace("Role A", "Role X"),  # only approved for unrelated-co
    A.replace("example-co", "example-co-lab").replace("Role A", "Role B"),
    A.replace("example-co", "unrelated-co").replace("Role A", "Role B"),
    A.replace("2022", "2023").replace("Role A", "Role B"),
    A.replace("Role A", r"\textbf{Role B}"),
    A.replace("Role A", "Role B%hidden"),
    A.replace("Role A", "Role {B}"),
    A.replace("Role A", "Role B") + r"\resumeSubheadingSingle{Synthetic Project}{2024}",
])
def test_unapproved_other_employer_tex_and_non_title_edits_cannot_pass(options, replacement):
    assert validate(doc(A), doc(replacement), options)


def test_repeated_or_ambiguous_same_employer_signatures_fail_closed(options):
    repeated = doc(A, A)
    ambiguous = doc(A, A.replace("Role A", "Role C"))
    for seed, tailored in [
        (repeated, doc(A.replace("Role A", "Role B"), A)),
        (ambiguous, doc(A.replace("Role A", "Role B"),
                        A.replace("Role A", "Role C"))),
    ]:
        assert not eligible_title_options(seed, options)
        assert validate(seed, tailored, options)


def test_no_cross_anchor_witness_reuse_or_additional_anchor(options):
    seed = doc(A, B)
    # Changing B's title AND borrowing A's date is not a B witness.
    borrowed = doc(A, B.replace("Role Z", "Role B").replace("2019--2021", "2022--2024"))
    assert validate(seed, borrowed, options)
    # A legitimate replacement cannot also add a structural call.
    extra = doc(A.replace("Role A", "Role B"), B, r"\role{example-co}{Role C}{2016}")
    assert validate(seed, extra, options)
    assert validate(doc(A, NEIGHBOR),
                    doc(A.replace("Role A", "Role B"),
                        NEIGHBOR.replace("Role X", "Role B")), options)


@pytest.mark.parametrize("seed_line,changed_line", [
    (r"\role{example-co}{2022--2024}", r"\role{example-co}{Role B}"),
    (r"\role{example-co}{Role A}", r"\role{example-co}{Role B}"),
    (r"\role{example-co}{Role A}{2022}{Remote}",
     r"\role{example-co}{Role B}{2022}{Remote}"),
    (r"\resumeSubheading{example-co}{Remote}{Role A}",
     r"\resumeSubheading{example-co}{Remote}{Role B}"),
    (r"\resumeSubheadingSingle{example-co}{Role A}",
     r"\resumeSubheadingSingle{example-co}{Role B}"),
    (r"\role{example-co}{Role A}{2022}\role{example-co}{Role A}{2021}",
     r"\role{example-co}{Role B}{2022}\role{example-co}{Role A}{2021}"),
    (r"\role{example-co}{Role A}{2022", r"\role{example-co}{Role B}{2022"),
])
def test_unsupported_arity_project_multicall_and_unbalanced_slots(options, seed_line, changed_line):
    assert eligible_title_options(doc(seed_line), options) == ()
    assert validate(doc(seed_line), doc(changed_line), options)


def test_supported_four_argument_heading_requires_same_non_title_fields(options):
    seed = doc(r"\resumeSubheading{example-co}{Remote}{Role A}{2022--2024}")
    allowed = doc(r"\resumeSubheading{example-co}{Remote}{Role B}{2022--2024}")
    changed_loc = doc(r"\resumeSubheading{example-co}{Hybrid}{Role B}{2022--2024}")
    assert validate(seed, allowed, options) == []
    assert validate(seed, changed_loc, options)


def test_inert_seed_role_is_not_an_eligible_witness(options):
    seed = doc(r"% \role{example-co}{Role A}{2022}", NEIGHBOR)
    assert eligible_title_options(seed, options) == ()
