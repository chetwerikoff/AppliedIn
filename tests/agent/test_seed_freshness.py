"""The tailored résumé must be built from TODAY's base.tex.

An ADK session stores the seed résumé when it is first created, and that same copy
is what the truthfulness check validates against. So a row first seen before an
edit to base.tex tailors from the old résumé AND is checked against the old
résumé: a project added to base.tex silently never reaches an employer, and
nothing objects.
"""

from types import SimpleNamespace

from agent.run import _stale_seed

BASE = "\\resumeSubheadingSingle{AppliedIn}{gh}\n\\resumeItem{one}"
OLD = "\\resumeItem{one}"


def test_a_session_seeded_from_an_older_resume_is_stale():
    assert _stale_seed(SimpleNamespace(state={"base_latex": OLD}), BASE)


def test_a_session_seeded_from_the_current_resume_is_not():
    assert not _stale_seed(SimpleNamespace(state={"base_latex": BASE}), BASE)


def test_a_session_with_no_seed_is_stale():
    """It cannot have been seeded from the current résumé, so it is rebuilt."""
    assert _stale_seed(SimpleNamespace(state={}), BASE)


def test_an_unreadable_session_is_treated_as_stale():
    """Rebuilding costs one tailor run; trusting it costs a wrong application."""
    class Broken:
        @property
        def state(self):
            raise RuntimeError("corrupt")
    assert _stale_seed(Broken(), BASE)


def test_an_empty_current_base_never_forces_a_rebuild():
    """If base.tex cannot be read, that is a separate failure. Wiping every
    session over it would re-tailor the whole backlog against nothing."""
    assert not _stale_seed(SimpleNamespace(state={"base_latex": OLD}), "")


# Issue #26: exercise the real save boundary with synthetic, offline collaborators.
import pytest

_SAVE_HEAD = r"\role{Test User, example-co}{2022--2024}"
_SAVE_OTHER = r"\role{Test User, example-co}{2024--2026}"
_SAVE_PRE = (r"\documentclass{article}" "\n"
             r"\newcommand{\resumeItem}[1]{\item #1}" "\n"
             r"\begin{document}" "\n")


def _save_doc(body):
    return _SAVE_PRE + body + "\n" + r"\end{document}"


@pytest.fixture
def _save_offline(monkeypatch):
    """No rendering, external stores, browser, network or owner runtime inputs."""
    from types import SimpleNamespace
    from agent.graph import save_tailored_resume
    from agent import run as run_module
    from core import profiles, stores as stores_module
    from tools import render

    effects = {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}

    class Tracking:
        def get(self, pk):
            return {"status": "tailored"}

        def set_status(self, *args, **kwargs):
            effects["tracking"] += 1

    class Artifacts:
        def put(self, *args, **kwargs):
            effects["artifact"] += 1
            return "synthetic-artifact"

    fake = SimpleNamespace(tracking=Tracking(), artifacts=Artifacts())

    def make_stores():
        effects["make_stores"] += 1
        return fake

    def render_pdf(latex):
        effects["render"] += 1
        return b"%PDF-synthetic"

    monkeypatch.setattr(stores_module, "make_stores", make_stores)
    monkeypatch.setattr(render, "render_pdf", render_pdf)
    monkeypatch.setattr(profiles, "resolve_for", lambda row: object())
    monkeypatch.setattr(profiles, "apply_to_latex", lambda latex, profile: latex)
    monkeypatch.setattr(run_module, "seed_fingerprint", lambda: "synthetic-seed")

    def run(base, tailored):
        ctx = SimpleNamespace(state={"base_latex": base, "pk": "synthetic"})
        result = save_tailored_resume(tailored, ctx)
        return result, ctx.state, dict(effects)

    return run


@pytest.mark.parametrize("seed,tailored", [
    (_SAVE_HEAD + "\n" + _SAVE_OTHER + "\n" + r"\item One" + "\n" + r"\item Two",
     _SAVE_OTHER + "\n" + _SAVE_HEAD + "\n" + r"\item Reworded" + "\n" + r"\item Two"),
    (r"\role{X}" + "\n" + r"\item One", r"\role{X}" + "\n" + r"\item Reworded"),
    (r"\resumeSubheading{example-co}{2022}{Tester}{Remote}" + "\n" + r"\resumeItem{One}",
     r"\resumeSubheading{example-co}{2022}{Tester}{Remote}" + "\n" + r"\resumeItem{Reworded}"),
    (r"\resumeSubheadingSingle{Synthetic Project}{2024}" + "\n" + r"\resumeItem{One}",
     r"\resumeSubheadingSingle{Synthetic Project}{2024}" + "\n" + r"\resumeItem{Reworded}"),
    (_SAVE_HEAD + "\n" + r"\item One" + "\n" + r"\resumeItem{Two}",
     _SAVE_HEAD + "\n" + r"\resumeItem{One}" + "\n" + r"\resumeItem{Two}"),
])
def test_save_accepts_complete_synthetic_legacy_role_and_mixed_fragments(_save_offline, seed, tailored):
    out, state, effects = _save_offline(seed, tailored)
    assert out["ok"] is True
    assert out["format"] == "pdf"
    assert effects == {"make_stores": 1, "render": 1, "artifact": 2, "tracking": 1}
    assert state["tailored"] == tailored


@pytest.mark.parametrize("base,tailored,reason", [
    (_SAVE_HEAD + "\n" + _SAVE_OTHER + "\n" + r"\item One",
     _SAVE_HEAD + "\n" + r"\item One", "facts"),
    (_SAVE_HEAD + "\n" + r"\item One",
     _SAVE_HEAD.replace("2022", "2021") + "\n" + r"\item One", "facts"),
    (_SAVE_HEAD + "\n" + r"\item One",
     "% " + _SAVE_HEAD + "\n" + r"\item One", "facts"),
    (_SAVE_HEAD + "\n" + _SAVE_HEAD + "\n" + r"\item One",
     _SAVE_HEAD + "\n" + r"\item One", "facts"),
    (r"\role{X}" + "\n" + r"\item One", r"\roles{X}" + "\n" + r"\item One", "facts"),
    (_SAVE_HEAD + "\n" + r"\item One" + "\n" + r"\item Two",
     _SAVE_HEAD + "\n" + r"\item One", "bullets"),
    (_SAVE_HEAD + "\n" + r"\item One",
     _SAVE_HEAD + "\n" + r"% \item One", "bullets"),
    (r"\resumeSubheadingSingle{Synthetic Project}{2024}" + "\n" + r"\resumeItem{One}",
     r"\resumeSubheadingSingle{Synthetic Project}{2024}" + "\n" + r"\resumeItemExtra{One}", "bullets"),
    (_SAVE_HEAD + "\n" + r"\item One",
     _SAVE_HEAD + "\n" + r"\itemize" + "\n" + r"\itemsep", "bullets"),
    (r"\roles{X}" + "\n" + r"\item One", r"\roles{X}", "format"),
    (_SAVE_HEAD + "\n" + r"\itemize" + "\n" + r"\itemsep",
     _SAVE_HEAD + "\n" + r"\item One", "format"),
    (r"\item One" + "\n" + r"\item Two", r"\item One", "format"),
    ("", r"\role{X}" + "\n" + r"\item One", "missing"),
])
def test_save_refuses_unsafe_seed_or_change_before_side_effects(_save_offline, base, tailored, reason):
    result, state, effects = _save_offline(base, tailored)
    assert result["ok"] is False
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert set(state) == {"base_latex", "pk"}
    message = result["message"].lower()
    if reason == "facts":
        assert result["missing_facts"]
    elif reason == "bullets":
        assert "you dropped 1 bullet(s)" in message
        assert "restore" in message and "re-save" in message
    elif reason == "format":
        assert "unrecognized" in message and "format" in message
        assert "missing_facts" not in result
    else:
        assert "missing" in message


@pytest.mark.parametrize("base_body,tailored_body,expected", [
    (_SAVE_HEAD, _SAVE_HEAD, "format"),
    (_SAVE_HEAD + "\n" + r"\resumeItem{One}", _SAVE_HEAD + "\n" + r"\item Replacement", "bullets"),
    (_SAVE_HEAD + "\n" + r"\item One", _SAVE_HEAD + "\n" + r"\resumeItem{Replacement}", "bullets"),
    (_SAVE_HEAD + "\n" + r"\resumeItem{One}" + "\n" + r"% \item not active" + "\n" + r"\resumeItemExtra{not active}",
     _SAVE_HEAD + "\n" + r"\item Replacement" + "\n" + r"\itemize" + "\n" + r"\itemsep", "bullets"),
    (r"\roles{X}" + "\n" + r"\resumeItem{One}", r"\roles{X}", "format"),
])
def test_save_full_documents_exclude_preamble_and_non_base_macros(_save_offline, base_body, tailored_body, expected):
    result, _, effects = _save_offline(_save_doc(base_body), _save_doc(tailored_body))
    assert result["ok"] is False
    assert ("unrecognized" in result["message"].lower()) is (expected == "format")
    if expected == "bullets":
        assert "You dropped 1 bullet(s)" in result["message"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}


def test_save_full_document_preserves_valid_reordered_roles(_save_offline):
    base = _save_doc(_SAVE_HEAD + "\n" + _SAVE_OTHER + "\n" + r"\item One" + "\n" + r"\item Two")
    tailored = _save_doc(_SAVE_OTHER + "\n" + _SAVE_HEAD + "\n" + r"\item Two" + "\n" + r"\item One")
    result, _, effects = _save_offline(base, tailored)
    assert result["ok"] is True
    assert effects["tracking"] == 1


def test_save_unrecognized_format_wins_over_dropped_bullet(_save_offline):
    base = _save_doc(r"\roles{X}" + "\n" + r"\item One" + "\n" + r"\item Two")
    tailored = _save_doc(r"\roles{X}" + "\n" + r"\item One")
    result, _, effects = _save_offline(base, tailored)
    assert result["ok"] is False
    assert "unrecognized" in result["message"].lower()
    assert "You dropped" not in result["message"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}



@pytest.mark.parametrize("opening,closing", [
    (r"\iffalse", r"\fi"),
    (r"\begin{verbatim}", r"\end{verbatim}"),
    (r"\begin{comment}", r"\end{comment}"),
])
@pytest.mark.parametrize("hidden_kind", ["role", "bullet"])
def test_save_rejects_inactive_tex_witness_without_side_effects(
    _save_offline, opening, closing, hidden_kind
):
    """Review regression: unchanged source text must represent active content."""
    base = _save_doc(_SAVE_HEAD + "\n" + r"\item One")
    if hidden_kind == "role":
        body = "\n".join([opening, _SAVE_HEAD, closing, r"\item One"])
    else:
        body = "\n".join([_SAVE_HEAD, opening, r"\item One", closing])
    result, state, effects = _save_offline(base, _save_doc(body))
    assert result["ok"] is False
    if hidden_kind == "role":
        assert result["missing_facts"] == [_SAVE_HEAD]
    else:
        assert "You dropped 1 bullet(s)" in result["message"]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert set(state) == {"base_latex", "pk"}


def test_save_rejects_nested_false_branch_hiding_anchor(_save_offline):
    base = _save_doc(_SAVE_HEAD + "\n" + r"\item One")
    tailored = _save_doc("\n".join([
        r"\iffalse", r"\iftrue", r"\fi", _SAVE_HEAD, r"\fi", r"\item One",
    ]))
    result, state, effects = _save_offline(base, tailored)
    assert result["ok"] is False
    assert result["missing_facts"] == [_SAVE_HEAD]
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert set(state) == {"base_latex", "pk"}


def test_save_rejects_unverifiable_conditional_seed_as_unknown_format(_save_offline):
    base = _save_doc("\n".join([
        r"\iffalse", r"\role{Ignored}", r"\else", _SAVE_HEAD, r"\item One", r"\fi",
    ]))
    result, state, effects = _save_offline(base, base)
    assert result["ok"] is False
    assert "unrecognized" in result["message"].lower()
    assert effects == {"make_stores": 0, "render": 0, "artifact": 0, "tracking": 0}
    assert set(state) == {"base_latex", "pk"}
