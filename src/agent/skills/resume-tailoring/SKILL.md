---
name: resume-tailoring
description: Tailor an existing LaTeX résumé substantively to a job using only facts in the same-run base seed, preserving role anchors and bullet ownership. Save the full .tex without inventing claims.
---

# Résumé tailoring

Edit the unchanged same-run LaTeX seed supplied in state \`base_latex\`
for the JD in \`jd_text\`; save the complete result using
\`save_tailored_resume\`. The **seed is the sole factual authority**.
The JD, \`github_context\` and owner/tailoring notes can direct relevance,
ordering and vocabulary **only for seed-supported work**. They must never
introduce a project, employment fact, skill, tool, result, metric, outcome,
scale, beneficiary, or seniority not present in the seed — even if the
external context accurately describes a different real project.

## Editing the actual seed format

The tailor instruction names the **active role/anchor and bullet commands in
this exact seed**, using the same shared inventory/helper as the save guard.
Use the selected commands and existing LaTeX structure; do not assume a
particular macro, fixed summary line, section layout, or project section.
Never invent a section that is missing from the seed.

1. Read the JD for relevant responsibilities, skills and vocabulary.
   Distinguish JD-only wishes from facts demonstrably present in the seed.
2. If a summary exists, **rewrite the entire existing summary** for this JD
   with seed-supported statements. Do not add a new summary if it is absent.
   The headline beneath the name retains its existing JD-adapting behavior;
   it is **not** an employment role title.
3. Work through **every existing role**, including older roles. Reorder
   the existing bullets by JD relevance **within their own role only**.
   Rephrase supported accomplishments with accurate JD terminology,
   including meaningful changes rather than cosmetic word swaps. Shorten
   less relevant bullets without deleting or merging any. Never move one
   role's accomplishment to a different role.
4. Reorder seed-supported skills and adjust emphasis in any other **existing**
   section as appropriate. Do not create new sections, projects, positions,
   skills, metrics or achievements from the JD, GitHub or notes.
5. Preserve each role/anchor line, employer, employment role title and date
   verbatim under the existing normalization contract. Keep the preamble,
   section scaffolding, valid commands, formatting structure and braces
   compilable. Reword text without damaging LaTeX markup.

**Truthful example (content only, not a macro template):** a seeded bullet
about coordinating queue consumers may be rephrased with the JD's
"event-driven" vocabulary when it is genuinely the same seeded work.
A JD or GitHub mention of a different tool or project does **not** permit
claiming that tool or project. Do not turn a description of an exercise,
investigation or refactor into an unseeded production result.

For more emphasis techniques, see
\`references/emphasis-techniques.md\`. Its suggestions are subordinate to
the seed-only factual rule, existing sections and role attribution.

## When outcomes are not supported

If the seed does not establish impact, scale, an outcome, a beneficiary or
a metric, describe the work accurately **without adding one**. Do not
force quantification or claim a business result merely to make a bullet
more impressive. An otherwise truthful JD-focused rewrite is valid without
a newly quantified result.

## Save and correct

Call \`save_tailored_resume(tailored_latex=<complete edited .tex>)\`.
Retain every base-used bullet and role anchor. If the tool reports
\`missing_facts\` or dropped bullets, restore the original anchor/bullet
content and re-save. If the seed format is unrecognized, report the need
for a supported seed rather than manufacturing a different format.

The save tool provides code-enforced structural and aggregate bullet-count
checks. It **cannot** validate semantic claims or whether a rewritten bullet
belongs to the correct role. You must preserve both. Do not apply for a job,
browse a live site, or use owner runtime data during tailoring.
