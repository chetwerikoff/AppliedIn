# BrowserSkill in this fork

The browser engine is selected once for **all** in-process browser work: single
and batch job-description reads, browser discovery, and approved applications.
Installing a skill in OpenCode alone does not change the server's JD reader.

## Local configuration

Use a **clean, dedicated Chrome user-data directory** for application work.
The default is `~/.local/share/appliedin/chrome`, outside the repository. The
launcher refuses the main Chrome/Chromium directory and paths inside the repo.
Existing personal profiles are never copied or migrated.

Install the official `bsk` CLI, then run:

```bash
./appliedin browser-setup
```

This launches the separate Chrome with the extension's Web Store page. **Stop
and install BrowserSkill yourself**, enable file-URL access, and inspect its
Instance ID in the extension popup. Setup snapshots existing browser IDs before
launch and waits for a new ID. Confirm `y` in the terminal only after checking
that this is the dedicated profile; the default answer leaves the config untouched.
Multiple new instances or a lost connection require explicit correction.

`config/browser.local.yaml` holds the confirmed mapping and optional overrides:

```yaml
engine: browser_skill
browser: "<the human-confirmed extension instance ID>"
user_data_dir: "~/.local/share/appliedin/chrome"  # optional
chrome_path: "/path/to/google-chrome-stable"   # optional; otherwise PATH lookup
```

`*.local.yaml` is gitignored. Unknown keys and invalid types are rejected. The
exact instance is pinned on every session; there is no fallback to another
browser or profile. An extension reinstall may change its ID and needs a new
human-confirmed mapping.

Before each new browser session, a missing instance triggers `ensure_browser()`.
It uses a file lock beside the directory, checks live Chrome argv for the exact
canonical `--user-data-dir`, and launches at most once. The only added flags are
`--user-data-dir`, `--no-first-run`, and `--no-default-browser-check`; there is no
CDP port, headless mode, enable-automation flag, or masking flag. Startup waits up
to 45 seconds for the pinned extension ID. A live owned process without its
extension produces an error rather than a second Chrome.

Dashboard/Check setup show three lifecycle states: stopped (will auto-start),
running without BrowserSkill, and connected. Status checks are read-only and
cached briefly; they never launch Chrome merely because the dashboard is open.
A missing/invalid initial mapping directs the owner to `browser-setup`.

+ Before using employer portals, sign in manually in this **new** browser:
  Workday/Oracle accounts, Google/Apple sign-in when required by their career
  sites, and whichever ATS accounts a selected employer requests. Saved sessions
  stay in this directory. No passwords, OTPs or account setup are automated.
+ Chrome/Google sync is optional. Browser model access still uses the separate
  LiteLLM credential/subscription path described below.

### Optional Hyprland workspace example

Current Lua window-rule syntax (see the official Hyprland window-rules guide):

```lua
-- Example only: this class-based rule affects matching Chrome windows.
hl.window_rule({
  match = { class = "^google-chrome$" },
  workspace = "5 silent",
})
```

Use `hyprctl clients` to inspect your actual class/title and narrow the match
if needed. Profile directories are not a window-rule matcher, and the launcher
does not add a custom class flag. This documentation does not edit the owner's
Hyprland configuration. Syntax reference:
https://wiki.hypr.land/Configuring/Basics/Window-Rules/

PDF uploads also require Chrome's **Allow access to file URLs** permission for
the BrowserSkill extension in this profile. A connected extension can still lack
this permission: Chrome then returns CDP `Not allowed` during file upload. The
controller surfaces this as a human gate with the setup action, not an upload
retry loop. Unknown/committed upload effects also require inspection before any
repeat; the adapter does not silently switch upload mechanisms.

Browser decisions use `APPLIEDIN_BROWSER_MODEL` through the existing LiteLLM
model-access path. For subscription-only operation set it to the same supported
`chatgpt/…` model used by orchestration and sign in with
`./appliedin login-chatgpt`. The old `APPLIEDIN_CHROME_MODEL` is used only by the
original Claude engine, never by BrowserSkill. Plain JD reads are deterministic
and do not need a model call at all.

Start normally:

```bash
./appliedin start
```

Startup reuses the shared BrowserSkill daemon. If none exists, it starts one in a
separate process group with a 30-day idle ceiling; restarting/stopping AppliedIn
does not terminate it or another worker's sessions. It preserves the existing
OS user, `BSK_HOME`, and extension connection settings. If an existing daemon is
unreachable, startup reports the outage rather than deleting its runtime files
or replacing it. Run `bsk doctor` for its diagnosis. `start` can restore a missing
BrowserSkill daemon even while AppliedIn itself is already running.

Use **Check setup** to verify the engine and selected profile connection. A code
change still requires an AppliedIn restart; the daemon does not hot reload.

## Greenhouse careers-page wrappers (operator verified)

A custom employer-origin careers page is **not** a Greenhouse board merely
because an iframe, page text, redirect, a similar board name or a successful API
probe mentions Greenhouse. A redirect for one posting also does not prove a
company-wide tenant. Independently verify that one Greenhouse board covers the
configured careers-page scope, review the change to the **tracked,
repo-versioned** `config/watchlist.yaml`, and deploy/restart AppliedIn so
discovery and apply both reload the deployed configuration. Do not let discovery
edit the watchlist or silently repair existing jobs.

Synthetic example (not a real watchlist entry):

```yaml
companies:
  - name: example-co
    careers_url: https://careers.example-co.test/openings
    ats: greenhouse
    board: example-board
    discovery: browser
    mode: gated
```

Under #118, only postings returned by the *explicit* `discovery: browser`
listing are considered for **new query-form** Greenhouse wrapper normalization.
The browser path still requires an exact HTTPS employer URL containing only
`?gh_jid=<digits>` and exactly one matching configured-board feed job.
Its initial tracking row preserves the employer URL and stores the board pin.
Unconfigured browser wrappers, ordinary query-form sitemap results and custom
crawl/browser fallback are **not** promoted. Native feeds are unchanged.

Issue #138 adds only two independently reviewed **data** exceptions to #118:
an operator-only, one-exact-PK old query-form proof/pin, and a uniquely
configured `greenhouse` / `discovery: crawl` numeric-terminal
employer-sitemap posting. Neither changes application-entry code or makes a
numeric-terminal posting eligible for the existing application route.


### Issue #138: strictly verified data-only tenant pin and sitemap evidence

A one-shot `discovery.tenant_pin.rederive_one(pk)` reads exactly one
**original** tracking PK through the existing store factory and reads the
deployed watchlist via its loader. It defaults to a **write-free dry-run** and
reports the proof, conflict or next permitted action. An optional commit
requires a separately approved operator invocation (`commit=True,
operator_authorized=True`) **after** reviewed deployment and an idle-only
restart; fleet work does not execute it on live data. It never scans or rekeys
historic rows, clears history, requeues, approves or opens a browser.

Before even considering a pin, independently review a **single** deployed
company-wide ATS/board/HTTPS careers-scope mapping (not a resolver guess,
redirect, iframe or successful board probe) and prove the **same original job
ID** in exactly one *current* board feed row. For Greenhouse the feed must
supply a **nonempty exact canonical hosted** `absolute_url` with
`https://boards.greenhouse.io/<board>/jobs/<numeric-id>` or
`https://job-boards.greenhouse.io/<board>/jobs/<numeric-id>`.
A missing URL, employer-wrapper URL, different board, alternate host, port,
query, encoded path or conflicting ID cannot establish that job witness.

An old canonical-PK `ats=greenhouse` row with an exact employer-origin
`?gh_jid=<digits>` URL can gain **only a genuinely absent**
`greenhouse_board` at the same PK, conditionally against the original
row and safety witnesses. Present, empty or conflicting pins are never
overwritten. A stable `apply_requested_at` with attempts and the
**pre-browser** tenant-gate denial is not itself disqualifying: the request,
approval fields, attempts, reader revisions, status and history stay
byte-identical. Any possible submit, submitted/submitting/manual-applied state,
hold, browser session or changed witness is a no-mutation manual review.
Canonical Ashby/Lever UUID, ATS and strict hosted URL identities are only
verified against their **own** exact current board jobs, with **zero writes**
and no vendor pin schema.

Redis atomically watches both the original `app:<pk>` row and the
independent `submit_hold:<pk>` key (even a present empty hold blocks);
its status writer merges against the current row so a later stale status
update cannot erase a pin. DynamoDB separately observes the application row
strongly consistently and uses a no-upsert, conditionally partial
`UpdateItem` on row-local identity, status and protected witnesses.
DynamoDB does **not** promise atomicity with Redis's independent hold key.

The uniquely deployed gated Greenhouse/crawl exception reuses existing bounded
sitemap XML discovery to keep a raw URL before title extraction loses a numeric
terminal path such as `/openings/job/731205`. Only an exact HTTPS
careers-host/path, posting marker and final decimal job ID are accepted. The
verified hosted feed supplies the actual title, location and
`first_published`; sitemap `lastmod` never makes an old job
fresh. Normal company preferences, relevance, title screening, age filtering,
conditional `put_new`, seen and enqueue still apply. This pass also
runs alongside nonempty partial HTTP listings. Same-canonical-PK collisions
never overwrite or repair a prior row, **including an old relative href**:
both seen-ledger and conditional-create conflicts skip duplicate URL repair.
Competing observed identities are reported for manual review. The URL seen
ledger is not a cross-PK identity index; no universal different-PK uniqueness
or automatic historical merge is claimed.

**Operator dry-run and adoption interpretation:** (a) a dry-run changes no
data; a separately authorized **committed** Greenhouse pin supplies tenant
evidence to the existing query-form admission at #139's one final Submit
guard, (b) a pin is tenant evidence, **never** human approval: existing
approval, duplicate, hold, provenance, navigation and Submit protections
remain. #138 itself does not change application-entry code; #139 uses the
tenant evidence at the single final decision while allowing only its approved
reversible preparation. (c) Changing the Databricks watchlist to a
**nonempty independently verified board** changes still-unpinned rows'
diagnostic reason from `defect_greenhouse_tenant_alias` to `defect_tenant` —
**not** to successful tenant admission. Numeric-terminal suffix URLs are not
asserted application-route eligible by #138.

If Databricks company-wide board ownership is not independently proved from an
official public employer-to-board link or equivalent source, leave its tracked
watchlist tuple unchanged. The permitted next action is for the operator to
verify that company-wide binding publicly, review the single-entry YAML diff,
deploy the reviewed tuple and only then perform a fresh dry-run; a board
probe, redirect or guessed slug is not enough. For any unsafe old row or
observed different-PK conflict, the next action is manual identity/disposition
review, not a repair. After a later merge the operator records the adopted
`main` SHA and performs an idle-only daemon restart; run
`uv sync --locked` only if dependency manifests changed. Any live
row re-derivation and any requeue require separate operator authorization.
Tests and smoke use only synthetic offline data, never a real feed, record,
browser, application, Redis instance or submission.


### Issue #139: application preparation and final Submit boundary

At application preparation, the code-owned approved HTTPS employer/ATS host
restriction allows `jobs.ashbyhq.com`, `jobs.lever.co` and host-only
`job-boards.greenhouse.io` without requiring an already-current board pin,
legacy wrapper certificate or tenant equality before reversible work.
This permits approved field writes, accessible-choice changes and upload of
the exact seed-valid row PDF under their per-action protections; it does not
authorize application submission.

The one final code-owned raw-click guard re-reads the current tracked row,
configured/pinned tenant, final job and origin, approvals, duplicate/hold
state, posting generation and seed-bound PDF. A missing, stale or foreign
board/pin refuses that irreversible click, even when approved fields,
accessible options and the correct PDF were prepared. Re-verify the board
and row through the existing operator mechanism; do not rewrite legacy
tracking rows or infer tenant membership from page hints.

## Operation and guarantees

Every job runs in its own Agent Window. Only its own session is cleaned up;
user tabs are never borrowed, and shared daemons are never stopped by reset.
Independent readers can coexist with an application without stealing its tab.

The adapter's form controller executes only a finite set of typed actions:
models cannot run shell, arbitrary JavaScript, borrow tabs, or choose another
profile. The model selects approved fact **keys**, and the server substitutes and
checks their values before writing. Native select and radio/checkbox options
must match both the page and an approved answer. Required decision-only
acknowledgements may be accepted, but facts and self-identification cannot be
inferred from an acknowledgement.
An exact approved textarea answer takes precedence over an unnecessary essay
request; matching is literal, not inferred. The model is told that the server
already owns the staged PDF and supplies it to the upload action.

Application navigation is limited to the job/direct-board hosts and known ATS
domains. The check also covers clicked links, so clicking cannot bypass the
navigation restriction. Unrelated and lookalike hosts produce a human gate.

Before final Submit, the controller rechecks the tracked dispatch, duplicate
status, résumé seed, attachment and current form values. Protected-characteristic
affirmations, unsafe restricted-country answers, placeholders, unverified
resume-parser autofill and incomplete form inventories become human gates.
Missing facts, login/CAPTCHA and unsupported embedded/shadow controls also gate;
there is no attempt to bypass them or silently change browser backends.
The DOM guard distinguishes the extension's own `BROWSER-SKILL-OVERLAY` containing
only Interrupt buttons from site shadow controls. A site shadow form or iframe
still gates. File inputs are checked through their verified attachment receipt,
not through Chrome's synthetic `C:\fakepath` text value.

A submit-command timeout or disconnect is **uncertain**, never retryable. A
submission is recorded as applied only after observing a new employer
confirmation, with no remaining active Submit control. A phrase already present
before the click is not confirmation. Site-specific structures that cannot be
verified require review rather than a guessed submission.
Gate and uncertain reports include the last attempted button and reached URL
for manual completion. OAuth query tickets and URL fragments are stripped from
that handoff context. Next/Continue remain submission-capable steps: an
interruption after them never authorizes an automatic retry.

Scoring, tailoring, critic and PDF rendering still use the upstream graph. The
scorer explicitly requests its existing JSON schema to avoid Markdown responses
from subscription models; strict schema validation and score thresholds remain.
Career Ops `Score & tailor` remains prepare-only; neither engine selection nor
retry grants permission to apply. Preparation errors leave an actionable error
card rather than an unclaimed card permanently marked WORKING.

To explicitly return all browser operations to the original engine:

```yaml
# config/browser.local.yaml
engine: chrome
```

## Maintaining upstream updates

The existing remote `upstream` and `./appliedin update` workflow are retained.
Most integration code is in new `src/tools/browser_skill*.py` and
`src/tools/browser_runtime.py` files. Existing caller changes are small dispatch
imports, startup/health hooks and generic infrastructure classification. No
upstream code is overwritten with generated patches after a merge, and the
original Claude engine is retained.

Before updating, commit or stash working-tree changes as the existing updater
requires. It merges `upstream/main`, aborts conflicts for explicit review, and
runs the offline suite including this fork's BrowserSkill contract tests. Private
`browser.local.yaml`, résumés and `.local*` data are not part of that merge.
Resolve future caller conflicts by preserving the `browser_runtime` dispatch;
never restore a hardcoded Claude call in only one browser path.

Verification after an update:

```bash
.venv/bin/python -m pytest -q
./appliedin start
bsk doctor
```

The regression suite uses fake CLI/model calls and covers profile binding,
engine dispatch, before-write guards, duplicate/stale-resume checks, source URL
validation and uncertain submits. It never drives a real browser or sends an
application. A live smoke check should read a public posting and prepare one role
without approval; actual sending requires the normal explicit apply decision.
