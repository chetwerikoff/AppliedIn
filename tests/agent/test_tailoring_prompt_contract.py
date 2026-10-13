"""Issue #27: prompt/rubric contracts, not claims about an LLM's output.

All contexts use invented roles and résumé facts. ADK state injection is run
locally with a synthetic invocation context: no model, network, browser, stores
or owner data is accessed.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.instructions_utils import inject_session_state

from agent.graph import critic, tailor
from tools.truthfulness import used_macros

ROLE_SEED = r"""
\documentclass{article}
\newcommand{\resumeItem}[1]{\item #1}
\begin{document}
\section{Experience}
\role{Test User at example-co}{Engineer}{2022--2024}
\item Maintained Python queue consumers.
\item Coordinated batch processing with colleagues.
\role{Test User at example-co-lab}{Technician}{2020--2022}
\item Documented release procedures.
\item Improved SQL report readability.
\end{document}
"""

LEGACY_SEED = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{example-co}{Remote}{Engineer}{2022--2024}
\resumeItem{Maintained Python queue consumers.}
\resumeItem{Coordinated batch processing with colleagues.}
\resumeSubheadingSingle{Test User project}{2021}
\resumeItem{Improved SQL report readability.}
\end{document}
"""

JD = "Engineer wanted for event-driven systems, Kubernetes and Ghost Toolkit"
GITHUB_ONLY = "Unrelated Ghost Toolkit demonstration with an unseeded Kubernetes deployment"
NOTE = "Please also claim Ghost Toolkit and a 95% improvement."


def _context(seed, *, tailored=None, include_notes=True):
    state = {"base_latex": seed, "jd_text": JD, "tailored": tailored or ""}
    if include_notes:
        state.update({"github_context": GITHUB_ONLY, "tailor_note": NOTE})
    # ReadonlyContext provides the same state view and private invocation
    # shape used by the ADK instruction renderer, without creating a runner.
    return ReadonlyContext(SimpleNamespace(session=SimpleNamespace(state=state)))


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [ROLE_SEED, LEGACY_SEED], ids=["role-item", "legacy"])
async def test_tailor_assembled_prompt_uses_only_shared_seed_selected_macros(seed):
    context = _context(seed)
    assert callable(tailor.instruction)
    prompt = await tailor.instruction(context)
    selected = used_macros(seed)

    # These two lines are assembled from the SAME helper that the #26 save
    # guard calls. A macro mentioned only in a declaration is not active.
    role_line = next(line for line in prompt.splitlines()
                     if line.startswith("SEED-SELECTED ROLE/ANCHOR COMMANDS: "))
    bullet_line = next(line for line in prompt.splitlines()
                       if line.startswith("SEED-SELECTED BULLET COMMANDS: "))
    assert role_line == ("SEED-SELECTED ROLE/ANCHOR COMMANDS: "
                         + ", ".join(selected["anchors"]))
    assert bullet_line == ("SEED-SELECTED BULLET COMMANDS: "
                           + ", ".join(selected["bullets"]))
    assert (selected["anchors"], selected["bullets"]) == (
        ((r"\role",), (r"\item",)) if seed == ROLE_SEED
        else ((r"\resumeSubheading", r"\resumeSubheadingSingle"), (r"\resumeItem",))
    )
    assert "UNCHANGED SAME-RUN BASE SEED" in prompt
    assert seed in prompt and JD in prompt and GITHUB_ONLY in prompt and NOTE in prompt
    assert "{base_latex}" not in prompt
    assert "Edit only sections actually present in the seed" in prompt
    assert "do not introduce new sections" in " ".join(prompt.split())
    assert "If the seed contains a summary, rewrite the FULL existing summary" in prompt
    assert "Within EACH existing employment role" in prompt
    assert "including older roles" in prompt
    assert "Shorten less relevant" in prompt
    assert "merge, delete, or lose any original bullet" in " ".join(prompt.split())


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [ROLE_SEED, LEGACY_SEED], ids=["role-item", "legacy"])
async def test_external_only_jd_github_and_notes_are_not_licensed_as_facts(seed):
    prompt = await tailor.instruction(_context(seed))
    assert "sole authority for every factual claim" in prompt
    assert "JOB DESCRIPTION — vocabulary and relevance hints, never a factual source" in prompt
    assert "GITHUB CONTEXT — relevance and vocabulary only; NEVER import" in prompt
    assert "The JD, GitHub context and notes cannot establish any claim" in prompt
    assert "Even if they" in prompt and "tool, project, outcome" in prompt
    assert "omit it unless the SAME fact is in the" in prompt
    assert "never move an achievement to another role" in prompt
    assert "preserve every role/anchor line" in prompt.lower()
    assert "headline beneath the name may continue to adapt" in prompt.lower()
    assert "no mandatory quantification" in prompt.lower()


@pytest.mark.asyncio
async def test_missing_optional_context_does_not_block_assembly_or_add_a_section():
    prompt = await tailor.instruction(_context(ROLE_SEED, include_notes=False))
    assert ROLE_SEED in prompt
    assert "{github_context?}" not in prompt and "{tailor_note?}" not in prompt
    assert "If the seed contains a summary" in prompt
    assert "Do not add a summary when none exists" in prompt
    assert "fixed template" in prompt


@pytest.mark.asyncio
async def test_critic_receives_same_seed_saved_draft_and_jd_and_requests_fact_correction():
    swapped = ROLE_SEED.replace(
        r"\item Documented release procedures.",
        r"\item Claimed a 95% uplift using Ghost Toolkit for queue consumers.",
    )
    # The rubric, not a live model, must detect that the seed is the only
    # factual witness and that cross-role reattribution demands correction.
    context = _context(ROLE_SEED, tailored=swapped)
    assert isinstance(critic.instruction, str)
    prompt = await inject_session_state(critic.instruction, context)
    assert f"UNCHANGED SAME-RUN BASE SEED — sole factual authority:\n{ROLE_SEED}" in prompt
    assert f"TAILORED RÉSUMÉ — saved draft being reviewed:\n{swapped}" in prompt
    assert f"JOB DESCRIPTION — relevance and vocabulary hints, not new facts:\n{JD}" in prompt
    assert "Compare ALL tailored factual claims and each bullet-to-role attribution" in prompt
    assert "unsupported skill, tool, project," in prompt
    assert "outcome, beneficiary, scale, seniority" in prompt
    assert "bullet assigning one role's work to another role" in prompt
    assert "DO NOT call exit_loop" in prompt
    assert "ONE-tweak feedback channel" in prompt
    assert "specific removal, return to" in prompt
    assert "an emphasis-only tweak is insufficient" in prompt
    assert "substantial truthful rewording" in prompt.lower()
    assert "WITHIN each role" in prompt


@pytest.mark.asyncio
async def test_critic_precedence_over_loaded_review_skill_when_seed_has_no_outcome():
    # The out-of-scope skill really is still loaded and conflicts with the
    # new seed-only rubric. Prompt precedence is the scoped remedy.
    path = (Path(__file__).resolve().parents[2]
            / "src/agent/skills/resume-review/SKILL.md")
    review_skill = path.read_text(encoding="utf-8")
    assert "truthfulness is enforced separately and is not your concern" in review_skill
    assert "the system now does, at what scale, for whom" in review_skill

    bare_seed = r"\role{example-co}{Engineer}{2023--2024}" + "\n" + r"\item Maintained queue consumers."
    ctx = _context(bare_seed, tailored=bare_seed)
    prompt = await inject_session_state(critic.instruction, ctx)
    assert "OVERRIDE conflicts in the still-loaded resume-review" in prompt
    assert '"truthfulness is not your concern"' in prompt
    assert "mandatory outcome/scale/beneficiary" in prompt
    assert "unconditional" in prompt
    assert "Do not demand new outcomes, scale, beneficiaries or metrics" in prompt
    assert "never invent them to satisfy the review skill" in prompt
    assert "Do not reject them as too large" in prompt
    assert "AFTER it was saved" in prompt
    assert "not a transactional save" in prompt


def test_skill_uses_seed_selected_structure_without_a_second_macro_registry():
    skill = (Path(__file__).resolve().parents[2]
             / "src/agent/skills/resume-tailoring/SKILL.md").read_text(encoding="utf-8")
    for fixed in (r"\resumeItem", r"\resumeSubheading", r"\resumeSubheadingSingle",
                  r"\role", r"\item", "OPEN SOURCE & PROJECTS"):
        assert fixed not in skill
    assert "same shared inventory/helper as the save guard" in skill
    assert "entire existing summary" in skill
    assert "every existing role" in skill
    assert "within their own role only" in skill
    assert "github_context" in skill and "never" in skill.lower()
    assert "seed is the sole factual authority" in skill
    assert "Never move one" in skill
    assert "without adding one" in skill
    assert "cannot" in skill and "correct role" in skill
