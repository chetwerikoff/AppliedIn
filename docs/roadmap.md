# AppliedIn roadmap

Goal: this fork finds relevant roles (AI and agent work first, ordinary software roles kept),
prepares a tailored résumé per role, and applies through a guarded browser engine in a dedicated
browser profile, using a ChatGPT subscription for model calls, while staying mergeable with
upstream `sayantan94/AppliedIn`.

The rule that outranks the rest (CLAUDE.md): an application is sent under a real person's name
and cannot be recalled. Every phase keeps the safety guards in code, never in prompts.
Personal constraints (location, work authorization, salary, demographics) live only in the
owner's private files and never appear in this repository.

## Done

| Item | Where |
|---|---|
| Upstream sync: `./appliedin update` (merge, abort on conflict, run tests) | PR #1 |
| Model access through a ChatGPT subscription (`chatgpt/` models, login command, stream shim) | PR #1 |
| Career Ops search: plain-keyword locations, wider titles and exclusions, 5-hour scan limit | PR #1 |
| Domain tiers (AI / IT / not IT), "Best match" sort, classify endpoint | PR #1 |
| Discovery tests no longer write to the real Redis | PR #1 |
| orchestrator-pack adoption, personal-data rules, CI `CI / checks`, Node 24 | PR #1 |
| BrowserSkill engine: runtime selector, JD reader, discovery, guarded apply controller (local fixture smoke passed, no real submission) | branch `feat/browser-skill-runtime` |
| Dedicated application browser: own Chrome user-data-dir launched on demand, pinned BrowserSkill instance, `./appliedin browser-setup` | branch `feat/browser-skill-runtime` |

## Phase 0 — foundation on `main`

1. Issue #2: make the offline test suite pass without the Claude CLI and without a Redis service.
2. Merge PR #1. Then set the card `roadmap` key to `docs/roadmap.md`.

## Phase 1 — browser engine lands safely

Depends on Phase 0.

1. BrowserSkill engine and dedicated browser PR (`feat/browser-skill-runtime`). Required: green CI and an independent pack review
   focused on the apply controller (value guard on every write, duplicate re-check before
   submit, confirmation detection, uncertain-after-submit, navigation allow-list, upload
   receipt). No real application before this PR is merged.
2. Review findings are fixed in that PR, never by moving a guard into a prompt.

## Phase 2 — pipeline reliability

May run in parallel with Phase 1 except where noted.

1. Scorer output robustness: a scorer reply that is not valid `MatchScore` JSON must not turn
   the job into a pipeline error. Use the provider's structured output or one bounded
   re-ask; the schema check and the score threshold stay. Offline tests with a fake model.
2. Time-zone-independent web tests: the two date-grouping tests in
   `tests/web/preferences.test.cjs` pass in any `TZ` without the `TZ=UTC` pin; then drop the pin
   from CI and the card.
3. Re-run jobs that failed on the old scorer (operator operation in the primary checkout after
   item 1 is merged and the daemon restarted; a small batch first). Not a code task.

## Phase 3 — first applications (owner-driven)

Depends on Phase 1 item 1 merged and Phase 2 item 1 merged.

1. Shortlist from the latest search: AI tier first, then IT, filtered by the owner's private
   constraints. Owner and local agents only; nothing from the board is published.
2. Score and tailor the shortlist; the owner reviews each résumé.
3. The owner approves applications in the dashboard. Agents never approve, queue or submit.
4. Multi-step forms: after real attempts, add support for the form patterns that gated
   (for example Workday steps), each with a synthetic local fixture test.

## Deferred

- A second browser engine (Grok Bot). Not before Phase 3 shows the BrowserSkill path works.
- A GLM API key as an alternative model provider. The owner chose ChatGPT only for now.
- Periodic upstream sync as its own task after Phase 1 (one `./appliedin update` PR at a time).
