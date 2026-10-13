r"""The preamble belongs to the seed, not to the tailor.

The Robinhood résumé of 2026-08-21 went out with every job title reading
"textitSenior System / Software Engineer". The tailor re-emitted the whole
document and doubled the backslashes inside the `\resumeSubheading` macro
definition — `#2 \\\textit{\small#3}` became `#2 \\\\textit{\small#3}`. That is
*valid* LaTeX: two row breaks, then the literal word "textit". So every guard
we had waved it through — `validate()` only anchors on `\resumeSubheading{...}`
call sites and never on the macro's definition, the bullet count was unchanged,
and Tectonic compiled it happily. A PDF with "textit" glued to five headings
was uploaded and queued for an employer.

The rule "leave the preamble untouched, byte-for-byte" lived only in SKILL.md.
A prompt is not a guard. The seed's preamble is authoritative by definition, so
the tool layer puts it back.
"""

from __future__ import annotations

from tools.render import restore_preamble

SEED = "\n".join([
    r"\documentclass[letterpaper,11pt]{article}",
    r"\usepackage{titlesec}",
    r"\newcommand{\resumeSubheading}[4]{\item\textbf{#1} & #2 \\\textit{\small#3} \\",
    r"}",
    r"\begin{document}",
    r"\resumeSubheading{Acme}{2020--2024}{Senior Engineer}{Seattle, WA}",
    r"\resumeItem{Built the thing.}",
    r"\end{document}",
    "",
])


def _swap_body(doc: str, old: str, new: str) -> str:
    return doc.replace(old, new)


def test_a_corrupted_macro_definition_is_replaced_by_the_seeds():
    """The exact Robinhood corruption: `\\\\` doubled inside the macro."""
    tailored = SEED.replace(r"#2 \\\textit", r"#2 \\\\textit")
    out = restore_preamble(SEED, tailored)
    assert r"#2 \\\textit{\small#3}" in out, "the doubled backslashes survived"
    assert r"\\\\textit" not in out


def test_the_tailored_body_is_kept_verbatim():
    """Restoring the preamble must not undo the tailoring itself."""
    tailored = _swap_body(SEED, r"\resumeItem{Built the thing.}",
                          r"\resumeItem{Architected the thing at scale.}")
    out = restore_preamble(SEED, tailored)
    assert r"\resumeItem{Architected the thing at scale.}" in out
    assert r"\resumeItem{Built the thing.}" not in out


def test_an_untouched_preamble_round_trips_byte_for_byte():
    assert restore_preamble(SEED, SEED) == SEED


def test_a_document_without_the_marker_is_left_alone():
    """No \\begin{document} on either side means we cannot tell head from body."""
    assert restore_preamble(SEED, "fragment") == "fragment"
    assert restore_preamble("fragment", SEED) == SEED


# Issue #26: the guards must see calls in résumé content, not declarations.
from tools.truthfulness import MACROS, active_calls, used_macros, validate

_TRUTH_PREAMBLE = (
    r"\documentclass{article}" "\n"
    r"\newcommand{\resumeItem}[1]{\item #1}" "\n"
    r"\newcommand{\role}[2]{\item \textbf{#1} \textit{#2}}" "\n"
    r"\begin{document}" "\n"
)
_TRUTH_ROLE_A = r"\role{Test User, example-co}{2022--2024}"
_TRUTH_ROLE_B = r"\role{Test User, example-co}{2024--2026}"


def _truth_doc(body):
    return _TRUTH_PREAMBLE + body + "\n" + r"\end{document}"


def test_truthfulness_selects_only_active_body_macros_not_preamble_definitions():
    doc = _truth_doc(_TRUTH_ROLE_A)
    assert used_macros(doc) == {"anchors": (r"\role",), "bullets": ()}
    doc = _truth_doc(_TRUTH_ROLE_A + "\n" + r"\resumeItem{One}")
    assert used_macros(doc)["bullets"] == (r"\resumeItem",)
    doc = _truth_doc(_TRUTH_ROLE_A + "\n" + r"\item One")
    assert used_macros(doc)["bullets"] == (r"\item",)
    doc = _truth_doc(_TRUTH_ROLE_A + "\n" + r"\item One" + "\n" + r"\resumeItem{Two}")
    assert used_macros(doc)["bullets"] == (r"\resumeItem", r"\item")


def test_truthfulness_ignores_comments_definitions_and_command_near_names():
    seed = _truth_doc("\n".join([
        _TRUTH_ROLE_A, r"\resumeItem{One}", r"% \resumeItem{ghost}",
        r"\resumeItemExtra{ghost}", r"\itemize", r"\itemsep", r"\roles{ghost}",
        r"\% literal percent % \item ghost",
        r"\newcommand{\testhelper}[1]{\resumeItem{ghost} \item ghost}",
        r"\item Two",
    ]))
    assert len(active_calls(seed, MACROS["anchors"])) == 1
    assert len(active_calls(seed, MACROS["bullets"])) == 2


def test_truthfulness_fragment_definitions_do_not_create_call_sites():
    fragment = (
        r"\newcommand{\resumeItem}[1]{\item #1}" "\n"
        r"\newcommand{\helper}[1]{" "\n"
        r"  \role{Not a role} \resumeItem{Not a bullet}" "\n"
        "}" "\n"
        r"\role{X}" "\n"
        r"\item Real bullet"
    )
    assert used_macros(fragment) == {"anchors": (r"\role",), "bullets": (r"\item",)}


def test_truthfulness_preserves_reordered_roles_and_detects_date_changes():
    base = _truth_doc(_TRUTH_ROLE_A + "\n" + _TRUTH_ROLE_B + "\n" + r"\item One")
    assert validate(base, _truth_doc(_TRUTH_ROLE_B + "\n" + _TRUTH_ROLE_A + "\n" + r"\item One")) == []
    assert validate(base, _truth_doc(_TRUTH_ROLE_A + "\n" + r"\item One")) == [_TRUTH_ROLE_B]
    changed = _TRUTH_ROLE_A.replace("2022", "2021")
    assert validate(base, _truth_doc(_TRUTH_ROLE_B + "\n" + changed + "\n" + r"\item One")) == [_TRUTH_ROLE_A]
    assert validate(base, _truth_doc(_TRUTH_ROLE_B + "\n% " + _TRUTH_ROLE_A + "\n" + r"\item One")) == [_TRUTH_ROLE_A]


def test_truthfulness_short_role_exact_token_and_duplicate_multiplicity():
    assert used_macros(r"\roles{X}" + "\n" + r"\role{X}")["anchors"] == (r"\role",)
    assert validate(r"\role{X}", r"\roles{X}") == [r"\role{X}"]
    base = _truth_doc(_TRUTH_ROLE_A + "\n" + _TRUTH_ROLE_A + "\n" + r"\item One")
    assert validate(base, _truth_doc(_TRUTH_ROLE_A + "\n" + r"\item One")) == [_TRUTH_ROLE_A]
    assert validate(r"\role{Test User}{2024}", r"\role{test    user}{2024}") == []


def test_truthfulness_legacy_subheading_variants_are_independent():
    multi = r"\resumeSubheading{example-co}{2022--2024}{Tester}{Remote}"
    single = r"\resumeSubheadingSingle{Synthetic Project}{2024}"
    assert used_macros(multi + "\n" + single)["anchors"] == (
        r"\resumeSubheading", r"\resumeSubheadingSingle"
    )
    assert validate(multi + "\n" + single, multi) == [single]
    assert validate(multi + "\n" + single, single) == [multi]

