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
