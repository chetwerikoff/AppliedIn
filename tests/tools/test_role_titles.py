"""A title exception is one consumed active, uniquely identified seed anchor.

All content is synthetic. Tests call the actual normalizer/Counter guard, not
a new parser or a mocked copy of the validation rule.
"""

from types import SimpleNamespace

import pytest

from core.role_titles import BASENAME, load_approved_titles
from tools.truthfulness import eligible_title_options, validate

A = r"\role{example-co}{Role A}{2022--2024}"
B = r"\role{example-co}{Role Z}{2019--2021}"
NEIGHBOR = r"\role{example-co-lab}{Role X}{2017--2018}"
PRE = r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n"


def doc(*lines, preamble=PRE):
    return preamble + "\n".join((*lines, r"\resumeItem{Synthetic work.}")) + "\n" + r"\end{document}"


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
    spacing_only = doc(A, r"\role{example-co} {Role Z} {2022--2024}")
    for seed, tailored in [
        (repeated, doc(A.replace("Role A", "Role B"), A)),
        (ambiguous, doc(A.replace("Role A", "Role B"),
                        A.replace("Role A", "Role C"))),
        (spacing_only, doc(A.replace("Role A", "Role B"),
                           r"\role{example-co} {Role Z} {2022--2024}")),
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


@pytest.fixture
def save_attempt(monkeypatch, tmp_path):
    """Exercise the real save seam without owner data or external effects."""
    from agent import graph, run
    from core import profiles, stores
    from tools import render

    (tmp_path / BASENAME).write_text("example-co: [Role B]\n", encoding="utf-8")
    effects = {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}

    class FakeArtifacts:
        def put(self, *args, **kwargs):
            effects["artifact"] += 1
            return "synthetic-replacement"

    class FakeTracking:
        def get(self, pk):
            return {"status": "tailored", "resume_s3_key": "existing.pdf"}

        def set_status(self, *args, **kwargs):
            effects["tracking"] += 1

    def make_stores():
        effects["make_stores"] += 1
        return SimpleNamespace(artifacts=FakeArtifacts(), tracking=FakeTracking())

    def render_pdf(latex):
        effects["render"] += 1
        return b"%PDF-synthetic"

    # The options fixture writes only a temporary, synthetic YAML file.
    monkeypatch.setattr(graph, "get_settings",
                        lambda: SimpleNamespace(local_dir=str(tmp_path)))
    monkeypatch.setattr(stores, "make_stores", make_stores)
    monkeypatch.setattr(render, "render_pdf", render_pdf)
    monkeypatch.setattr(run, "seed_fingerprint", lambda: "synthetic-seed")
    monkeypatch.setattr(profiles, "resolve_for", lambda row: object())
    monkeypatch.setattr(profiles, "apply_to_latex", lambda latex, profile: latex)

    def attempt(seed, changed):
        ctx = SimpleNamespace(state={"base_latex": seed, "pk": "synthetic"})
        result = graph.save_tailored_resume(changed, ctx)
        return result, ctx.state, dict(effects)

    return attempt


@pytest.mark.parametrize("seed_anchor,extra_anchor", [
    (r"\role{example-co}{Role A}{2022}",
     r"\resumeSubheadingSingle{Synthetic Project}{2024}"),
    (r"\role{example-co}{Role A}{2022}",
     r"\resumeSubheading{other-co}{Remote}{Invented Role}{2025}"),
    (r"\resumeSubheading{example-co}{Remote}{Role A}{2022}",
     r"\role{other-co}{Invented Role}{2025}"),
])
def test_save_rejects_new_supported_anchor_type_during_title_variant(
    save_attempt, seed_anchor, extra_anchor,
):
    seed = doc(seed_anchor)
    changed = doc(seed_anchor.replace("Role A", "Role B"), extra_anchor)
    result, state, effects = save_attempt(seed, changed)

    assert result["ok"] is False
    assert result["missing_facts"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert state == {"base_latex": seed, "pk": "synthetic"}


def test_save_rejects_formatted_same_employer_sibling_with_duplicate_signature(
    save_attempt,
):
    plain = r"\role{example-co}{Role A}{2022}"
    formatted = r"\role{example-co}{\textbf{Role Z}}{2022}"
    seed = doc(plain, formatted)
    changed = doc(plain.replace("Role A", "Role B"), formatted)
    result, state, effects = save_attempt(seed, changed)

    assert result["ok"] is False
    assert result["missing_facts"] == [plain]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert state == {"base_latex": seed, "pk": "synthetic"}


# A one-argument role macro keeps the employer, dates and any additional
# segments inside one braced argument. Only the first segment may vary.
ONE_ARG = r"\role{Role A | example-co | 2022--2024}"
ONE_ARG_EXTRA = r"\role{Role A | example-co | 2022--2024 | Role A}"
ONE_ARG_PRE = (
    r"\documentclass{article}" + "\n"
    + r"\newcommand{\role}[1]{#1}" + "\n"
    + r"\begin{document}" + "\n"
)


def one_arg_doc(*lines):
    return doc(*lines, preamble=ONE_ARG_PRE)


@pytest.mark.parametrize("anchor", [ONE_ARG, ONE_ARG_EXTRA])
def test_save_one_arg_role_changes_only_first_segment(save_attempt, anchor):
    seed = one_arg_doc(anchor)
    changed = one_arg_doc(anchor.replace("Role A", "Role B", 1))
    result, state, effects = save_attempt(seed, changed)

    assert result["ok"] is True
    assert result["format"] == "pdf"
    assert state["tailored"] == changed
    assert effects == {"make_stores": 1, "render": 1, "artifact": 2, "tracking": 1}


@pytest.mark.parametrize("anchor,changed", [
    (ONE_ARG, r"\role{Role B |  | 2022--2024}"),
    (ONE_ARG, r"\role{Role B | example-co | 2023--2024}"),
    (ONE_ARG, r"\role{Role B  | example-co | 2022--2024}"),
    (ONE_ARG_EXTRA, r"\role{Role B | example-co | 2022--2024 | Role B}"),
    (ONE_ARG_EXTRA, r"\role{Role B | example-co | 2022--2024  | Role A}"),
])
def test_save_one_arg_role_rejects_any_non_title_difference(
    save_attempt, anchor, changed,
):
    seed = one_arg_doc(anchor)
    result, state, effects = save_attempt(seed, one_arg_doc(changed))

    assert result["ok"] is False
    assert result["missing_facts"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert state == {"base_latex": seed, "pk": "synthetic"}


def test_save_one_arg_formatted_duplicate_signature_blocks_variant(save_attempt):
    formatted = r"\role{\textbf{Role A} | example-co | 2022--2024}"
    seed = one_arg_doc(ONE_ARG, formatted)
    changed = one_arg_doc(ONE_ARG.replace("Role A", "Role B", 1), formatted)
    result, state, effects = save_attempt(seed, changed)

    assert result["ok"] is False
    assert result["missing_facts"] == [ONE_ARG]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert state == {"base_latex": seed, "pk": "synthetic"}


def test_save_one_arg_raw_suffix_disambiguates_spacing_siblings(save_attempt):
    spaced = r"\role{Role A  | example-co | 2022--2024}"
    seed = one_arg_doc(ONE_ARG, spaced)
    changed = one_arg_doc(ONE_ARG.replace("Role A", "Role B", 1), spaced)
    result, state, effects = save_attempt(seed, changed)

    assert result["ok"] is True
    assert state["tailored"] == changed
    assert effects["artifact"] == 2


# The TeX spelling of the separator is itself part of the non-title signature.
# A short one-argument role is a heading, not a competing employment role.
TEXBAR_ROLE = r"\role{Role A  \textbar{}  example-co  \textbar{}  2022--2024}"
TEXBAR_HEADING = r"\role{CASE STUDY 1 - Example  \textbar{}  Example subtitle}"
TEXBAR_APPROVED = TEXBAR_ROLE.replace("Role A", "Role B", 1)


def test_texbar_role_and_short_heading_offer_one_title_and_save_verbatim(save_attempt):
    seed = one_arg_doc(TEXBAR_ROLE, TEXBAR_HEADING)
    approved = {"example-co": ("Role B",)}
    eligible = eligible_title_options(seed, approved)

    assert [(option.seed_line, option.approved) for option in eligible] == [
        (TEXBAR_ROLE, ("Role B",))
    ]
    tailored = one_arg_doc(TEXBAR_APPROVED, TEXBAR_HEADING)
    result, state, effects = save_attempt(seed, tailored)

    assert result["ok"] is True
    assert state["tailored"] == tailored
    assert effects == {"make_stores": 1, "render": 1, "artifact": 2, "tracking": 1}


@pytest.mark.parametrize("role,heading", [
    (TEXBAR_APPROVED.replace(r"  \textbar{}  ", r" \textbar{}  ", 1), TEXBAR_HEADING),
    (TEXBAR_APPROVED.replace(r"  \textbar{}  ", r"  \textbar{} ", 1), TEXBAR_HEADING),
    (TEXBAR_APPROVED.replace("example-co", "example-co-lab"), TEXBAR_HEADING),
    (TEXBAR_APPROVED.replace("example-co", ""), TEXBAR_HEADING),
    (TEXBAR_APPROVED.replace("2022--2024", "2023--2024"), TEXBAR_HEADING),
    (TEXBAR_APPROVED, TEXBAR_HEADING.replace("CASE STUDY 1", "CASE STUDY 2")),
    (TEXBAR_APPROVED, TEXBAR_HEADING.replace("Example subtitle", "Other subtitle")),
    (TEXBAR_APPROVED, TEXBAR_HEADING.replace(r"  \textbar{}  ", r" \textbar{}  ")),
    (TEXBAR_APPROVED, TEXBAR_HEADING.replace(r"\textbar{}", "|")),
])
def test_texbar_save_refuses_non_title_or_heading_mutations(save_attempt, role, heading):
    seed = one_arg_doc(TEXBAR_ROLE, TEXBAR_HEADING)
    result, state, effects = save_attempt(seed, one_arg_doc(role, heading))

    assert result["ok"] is False
    assert result["missing_facts"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert state == {"base_latex": seed, "pk": "synthetic"}


def test_short_headings_reserve_only_exact_second_segment(options):
    seed = one_arg_doc(TEXBAR_ROLE, TEXBAR_HEADING, r"\role{CASE STUDY SECTION}")
    eligible = eligible_title_options(seed, options)
    assert [option.seed_line for option in eligible] == [TEXBAR_ROLE]

    same_employer = TEXBAR_ROLE.replace("example-co", "Example subtitle")
    assert eligible_title_options(
        one_arg_doc(same_employer, TEXBAR_HEADING),
        {"Example subtitle": ("Role B",)},
    ) == ()


def test_texbar_formatted_sibling_still_blocks_duplicate_signature(options):
    formatted = r"\role{\textbf{Role A}  \textbar{}  example-co  \textbar{}  2022--2024}"
    assert eligible_title_options(one_arg_doc(TEXBAR_ROLE, formatted), options) == ()
