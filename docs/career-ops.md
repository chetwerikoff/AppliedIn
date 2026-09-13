# Career Ops discovery inside AppliedIn

Open **Find jobs → Career Ops search** (`/#career-ops`). There is one search form:
choose **Claude or Codex**, select job boards, and edit roles, locations, and the
posting-date window. An optional brief describes the kind of work you want.
**More preferences** contains excluded titles, undated postings, and scan depth.

**Search jobs** uses the selected client to discover leads on the selected boards,
then expands through Career Ops' public company directories and HTTP readers.
Greenhouse, Lever, Ashby, Workday, and iCIMS are supported. Directory expansion
uses no model tokens and does not require a curated company watchlist. AI search
supplements it with up to 30 leads and 12 search/open calls; the directory scan
has no job-result cap.

The default batch checks 150 companies per selected source. **Continue scan**
checks the next companies using the same filters and original date cutoff.
Changing filters requires a new search. A directory change invalidates its old
position and starts that source again, avoiding skipped employers. **Stop scan**
preserves completed matches and progress. Each invocation stops after 30 minutes;
an entire directory may take multiple invocations. Results stream to the same
board as company reads finish and survive refreshes and daemon restarts.

Coverage reports attempted boards, including unreachable ones. Cached directories
are refreshed after 24 hours; stale/unavailable directories are reported.
Provider pagination limits, unreachable boards, and excluded undated postings
are visible. Reaching the end of a directory is not a guarantee that every job
was reachable or matched. Restart a search to retry previously attempted boards.

Select roles and choose **Apply selected** to authorize scoring, tailoring, and
final submission for those exact roles. **Score & tailor selected** prepares
them and stops for review instead. Both actions use AppliedIn's existing pipeline;
**View pipeline** shows their progress. Manual Apply works while automation is
paused and does not approve other roles at the same company. A low score,
unanswered question, or missing résumé stops submission. Successful application
status still requires confirmation from the employer's page.

After **Apply selected**, **Your applications** stays above the search results.
It immediately acknowledges the request and refreshes each role's scoring,
tailoring, browser, and final status every six seconds. Confirmed applications
show their application date; blocked roles show the reason and a pipeline link.
The panel survives page reloads and search-filter changes. A failed request or
lost connection is shown explicitly and never treated as a successful submission.

No second Career Ops application tracker is created. Discovery alone never
authorizes submitting an application. Existing tracking rows, handled URLs, and dismissed jobs retain
their state when rediscovered. Detailed hard constraints are checked in the
existing scoring pipeline, before tailoring or approval.

## Search clients

Claude uses Claude Code's subscription login (`claude auth login`). Codex uses
its ChatGPT login (`codex login`). The chosen client is remembered; missing login
or usage failures are reported, and public board scanning can still continue.
Neither worker silently falls back to API-key billing. API-key environment
variables are removed; client authentication is checked before search. Workers
run in temporary directories, without application tools or Chrome sessions.
Codex runs read-only with shell, apps, and delegation disabled.

Only posting URLs observed in completed web-tool events are eligible. Codex's
JSONL stream exposes opened URLs but not search-result source lists, so its
worker must open every returned posting. Common ATS links are checked against
employer feeds; confirmed missing roles are dropped. Search snippets never
become job descriptions. Other leads must be read before tailoring.

[Codex non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

## Setup and storage

`./appliedin start` and `./appliedin setup` check Git, Node.js 18+, the pinned
Career Ops checkout, and its scanner runtime dependencies. npm installs run
with lifecycle scripts disabled: no upstream browser installation or agent setup
is launched. The existing provider-only bridge also supports selected-company
feeds for Workable, SmartRecruiters, Recruitee, and Oracle Recruiting Cloud.

The pin is `da8c6f9193ac3d7a48a583f815b7d0feab742b81`. The checkout lives in
`.local/integrations/career-ops`; local edits are preserved and reported at setup.
Board data and checkpoints live in `.local/career-ops-board.json`; directory
caches live in `.local/career-ops-cache`. `APPLIEDIN_LOCAL_DIR` is respected.
No preview web server, iframe, or separate Next.js app is needed.

**Tracked companies & automation** retains the existing selected-company feed
and additional web-search schedules. These are separate from manual directory
continuation. Manual searches work while paused. Preparation remains opt-in;
the main search only adds discoveries to the board.

## Implementation

- `scripts/integrations/career-network.mjs`: imports the pinned upstream source
  definitions, providers, and title/location filters; emits completed companies
  and progress with bounded concurrency.
- `src/discovery/career_network.py`: selected-client search, persistence,
  cancellation, resumable directory positions, and coverage receipts.
- `src/discovery/career_ops.py`: the shared discovery inbox and idempotent handoff.
- `src/discovery/career_apply.py`: exact-selection preparation and submission
  through the existing application queue and company leases.
- `src/discovery/career_ops_api.py`: validated local routes and progress stream.
- `web/career-board.js`: the embedded native dashboard, with stable forms during
  polling and streaming.

The adopted pipeline patterns are a discovery inbox, selected-job evaluation,
durable incremental results, explicit partial coverage, and resumable work.
AppliedIn's existing safety and approval invariants remain the submission boundary.

Upstream: [Career Ops](https://github.com/career-ops-hq/career-ops), MIT licensed.
Its checkout retains the upstream license. No upstream UI or application workflow
is copied into AppliedIn.
