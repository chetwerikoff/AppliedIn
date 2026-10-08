# AppliedIn chat executor rules

**Status:** repository policy for chat-based implementers

## 1. Scope

These rules apply to implementation, review, specification, CI, and merge work performed through chat-based environments.

They supplement `AGENTS.md`. GitHub Issues remain the normative task scope. When these rules and `AGENTS.md` conflict, stop and report the conflict instead of inventing an exception.

The normal path should stay simple:

```text
read current task/rules
    -> work
    -> publish
    -> read back
    -> current-head CI/review
    -> report or merge
```

Rare failures are handled when they actually occur. Do not add a lock, lease, receipt, capability ledger, ownership protocol, state machine, or other control-plane mechanism merely to cover a hypothetical race.

## 2. Required start

Before substantive work:

1. read the live default-branch `AGENTS.md`;
2. read the live default-branch `docs/chat-executor-rules.md`;
3. read the live binding GitHub Issue completely.

Read additional sources only when they are relevant to the task:

- `README.md` for repository/product context;
- `CLAUDE.md` for the rules that outrank the rest;
- `docs/browser-skill.md` for browser engine, discovery and application work;
- the relevant architecture or contract document when the Issue depends on it.

Do not rely on remembered copies while live GitHub reading is available. If a source needed for a concrete decision cannot be read completely, say so and do not make the safety-dependent change by guess.

No routine policy snapshot, Issue-body SHA-256 ledger, environment fingerprint, capability-expiry ledger, or publication canary is required.

## 3. Issue binding and scope

The live GitHub Issue is the normal implementation contract and scope authority.

When the Issue explicitly incorporates another repository file as a binding contract, read that file as well. Re-read the Issue before publication or ready-for-review reporting when there is reason to believe its scope changed.

Do not broaden the Issue merely because an adjacent cleanup is easy. If the requested scope becomes ambiguous or materially changes, obtain the user's decision before adding new behavior.

Existing PR continuation may proceed from the current remote PR state, but do not invent missing contract details or silently add features outside the Issue.

## 4. Live tools and remote publication

Use the tools that are actually available in the current session. Do not require a separate capability profile before ordinary work.

For important remote writes:

1. read the relevant current remote state when the tool exposes it;
2. perform the scoped write using the transport's normal safety checks;
3. read the resulting authoritative state back before claiming success.

For branch publication, prefer ordinary non-force Git/ref updates or file updates guarded by the current file/blob state. If a branch head, file SHA, non-fast-forward check, or resulting tree shows a real conflict, stop blind overwrite/retry, inspect the intervening change, and reconcile from current remote state.

Do not treat Issue comments, labels, assignees, execution IDs, or chat-local state as branch locks.

History rewrite is exceptional. Use force push or force ref update only when the user or binding Issue explicitly requires it, and obtain fresh CI/review for the resulting head.

Checkpoint after a meaningful recoverable slice or before genuinely risky work when useful. Do not create comments or commits merely because a timer elapsed.

## 5. Default-branch movement

Notice whether the default branch moved while the task was in progress, especially before publication, ready-for-review reporting, or merge.

If intervening changes materially overlap the task's files, contracts, interfaces, migrations, tests, or CI assumptions, update or revalidate the affected work and rerun the checks whose assumptions changed.

If the changes clearly do not overlap, continue. A formal comparison report is not required merely because the base moved.

## 6. Long-running commands

For long tests or similar commands:

- do not accidentally launch duplicate copies of the same work;
- retain enough output or status to know what happened;
- a tool-response timeout does not by itself prove the process failed;
- before retrying, check whether the earlier process is still running when that is possible;
- do not terminate unrelated processes by broad command-name matching.

Use stronger supervision only when a concrete task or failure mode actually needs it.

For GitHub Actions, keep evidence tied to the exact head SHA and the relevant run/job. Do not combine CI evidence from different heads.

For GitHub Actions diagnostics, the available GitHub transport can fetch decoded job logs directly by job ID. A practical path is `run -> jobs -> failed job ID -> decoded job log`; the returned log includes step stdout/stderr. This can be used to inspect failures such as pytest output without requiring a separate diagnostic artifact solely to capture the command output.

## 7. Personal data and application safety

`AGENTS.md` owns the full policy ("Personal data in public surfaces", "Invariants" and the orchestration rules); do not duplicate it here. The following protections remain mandatory in every chat:

- the repository, its Issues, PRs, comments, commit messages, branch names and CI logs are public: never write the owner's personal data, anything from `.local/`, `resume/` or `output/`, the owner's job board contents, or any credential into them; use "the owner" and synthetic fixtures;
- the chat has no access to the owner's runtime data and must not ask for it to be pasted; a fix is designed from code, tests and a synthetic reproduction;
- never move a guard from code into a prompt; the invariants in `AGENTS.md` are enforced by the tool layer;
- no real job application is ever submitted, approved or queued as part of an implementation, smoke or review; form-filling checks use a local fixture page with synthetic data;
- tests stay offline: no test may reach the network, a real browser, the ChatGPT subscription or the owner's Redis;
- `upstream` (`sayantan94/AppliedIn`) is read-only: never open Issues or PRs there and never push to it.

Live verification that needs the running daemon, the owner's data or the application browser is operator or firefighter work in the primary checkout. Report it as pending when the Issue requires it; do not claim it.

## 8. CI and review binding

CI and review claims are valid only for the current PR head SHA.

After a new commit or history rewrite:

- old-head CI conclusions are stale;
- old-head clean-review conclusions are stale;
- obtain fresh required checks and review for the new head.

Do not call missing, cancelled, stale, or failing CI green.

A material review finding remains unresolved until it is fixed and verified on the current head, made inapplicable by the resulting change, or explicitly rejected with evidence and accepted by the responsible reviewer or user. Resolve current GitHub review threads consistently with the reported review state.

### Review evidence feasibility

Before introducing or strengthening a blocking review requirement that depends on correlation, causality, identity, provenance, parentage, turn/session context, transport metadata, or another witness, first establish that the required evidence is actually observable on the exact production execution or transport path being constrained.

Identify the evidence producer and the exact observation surface that supplies it. Ground the evidence's existence in an authoritative contract or an observed production shape. A mock, fixture, inferred schema, or evidence available only on a different path does not prove that the constrained path provides it.

If the required evidence is absent, do not demand it as though it exists. Use weaker available evidence that still proves the needed property, add scoped instrumentation when the task permits it, or state that the desired invariant cannot be proven at that boundary and adjust the design or specification accordingly.

A blocking finding that depends on an impossible or unproven witness must be withdrawn or explicitly adjudicated; it must not generate another implementation round whose only purpose is to manufacture evidence the production path does not supply.

## 9. Merge

Immediately before merge:

1. re-read the open PR and note its exact current head SHA;
2. require the repository's required CI and material review findings to be green/clean for that head;
3. use that freshly observed head as `expected_head_sha` or an equivalent exact-head guard when the merge transport supports it;
4. perform the merge;
5. read the merge result and resulting base-branch state back before reporting success.

If the PR head changes before merge, recheck CI and review for the new head.

Merge-time adoption in the primary checkout (fast-forward, `uv sync`, daemon restart) follows `AGENTS.md` and is operator or firefighter work, not part of the chat merge.

After merge, delete the task branch when that is supported, safe, and useful. Otherwise report that it remains.

## 10. Definition of Done for "implement the Issue"

A chat implementation is complete when all applicable outcomes are true:

1. the intended changes are published in the PR and important published state was read back;
2. the PR diff is within the binding Issue scope;
3. required CI is green for the current PR head;
4. current material review findings are resolved for that head;
5. the PR text, commit messages and diff contain no personal data (`AGENTS.md`, "Personal data in public surfaces");
6. the PR is ready for review, and any merge result claimed as successful was confirmed from authoritative remote state.

Local tests, local commits, or uncommitted work alone are not completion evidence.

## 11. Reporting

Use ordinary human-readable status. Report what changed, what was verified, the current PR/head when relevant, and any specific operation that could not be completed.

Never claim an action succeeded unless the authoritative remote state confirms it. Do not replace a concrete limitation with a vague state label or a new process abstraction.
