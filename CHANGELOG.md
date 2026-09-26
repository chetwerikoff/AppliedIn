# Release notes

## 2026-09-25 — Cosign job search and Find jobs motion

- Add **Cosign** under Find jobs: a live search of ~71,000 roles indexed by
  cosign.co, with posting URLs and full descriptions on the ATS itself. Searches
  **all of the US** by default; titles matching your excluded keywords are hidden.
- Pick up to five cities, or one-tap your preferred cities. Cosign filters one
  city per search, so each city runs separately: its pill settles with a count as
  it answers, and results merge newest first with each role listed once. "All of
  the US" keeps US postings from an unfiltered search, since Cosign has no country
  filter.
- A search row with a filter rail beneath it, a list of roles beside the selected
  role's details, company logos, salary and deduplicated locations ("Rome, NY"
  keeps its state). On phones the details replace the list.
- Motion modelled on Cosign across Find jobs: an odometer role count, example
  searches flipping through the empty box, a spinner that settles into a drawn
  check, shimmering placeholder rows, a sliding view switcher, board entrances
  when you switch views, and a sliding Career Ops role drawer. Entrances never
  replay on background refreshes, and everything stops under reduced motion.
- Each search fills to about a hundred roles: the first page shows at once, then
  more stream in until it gets there. **Select all** picks every new role on
  screen; Apply and Score & tailor act on one press and stay pinned to the bottom
  of the window while you scroll.
- After Apply, the side pane follows every role live: a Score → Tailor → Check →
  Apply → Submitted stepper, a timeline of what happened in words, and a click
  that opens the job in the pipeline in its own window (`?job=<pk>`).
- Apply asks nothing unless a form needs an answer. It runs through an owner's
  pause, keeps the request through a browser fault, and prepares a large
  selection in turn while the apply worker submits in parallel, one per company.
  Search results survive a daemon restart.
- Picked roles go through the Career Ops path, so they inherit its duplicate
  checks and score gate: only roles clearing your match bar are submitted, one at
  a time. The posting's real ATS is recorded.

Validation: 637 Python and 59 JavaScript tests passed. Browser checks covered
the US default, filling to ~100 roles, select all, multi-city merge, view
switching, dark theme and 390px width.

## 2026-09-14 — Clearer application details and compact workflow

- Add a compact Find → Prepare → Apply guide to Career Ops search, with aligned
  application columns and clearer status badges.
- Show the résumé, match score, submission status, and application date together
  in role details. Keep requested dates separate from confirmed application dates.
- Wrap the Fresh jobs Scan button label within the sidebar at narrow widths.

Validation: 46 JavaScript tests passed. Browser checks covered light and dark
themes, compact application rows, role details, and Scan button sizing from
390px to 1440px wide.

## 2026-09-14 — Shared agent instructions and clearer job review

- Add **Agent instructions** to the top navigation. Edit or import a private
  steering file shared with all agent paths, including Claude and Codex search.
  Each instance keeps its own file; stale saves cannot overwrite newer edits.
- Keep the Career Ops sidebar layout, with compact job rows, a role details
  drawer, and selection actions that remain accessible while scrolling.
- Show the latest search activity with readable elapsed times, grouped repeated
  updates, and filters for matches and issues.
- Hide already-applied jobs from Matching jobs, including roles submitted through
  another pipeline view. Group Your applications by status using compact rows;
  completed applications remain available in collapsed history.
- Install the Career Ops scanner in the selected instance's directory at startup.
  Report scanner failures explicitly instead of presenting them as empty results.

## 2026-09-13 — Career Ops search and application progress

### Find jobs

- Search from one embedded form: choose Claude or Codex, job boards, roles,
  locations, interests, and posting dates. Extra preferences stay tucked away.
- Discover employers beyond your watchlist through Career Ops' company
  directories for Greenhouse, Lever, Ashby, Workday, and iCIMS.
- See search activity and matches as they arrive. Stop a search without losing
  completed results, or continue from the next batch of companies.
- View coverage, unreachable boards, and missing posting dates explicitly.
  Directory scans retain every matching result; a partial scan does not imply
  that every available job has been checked.
- Keep selections and preference drafts stable while results refresh. Search
  controls sit beside the job board on desktop and stack on smaller screens.

### Apply and follow progress

- **Apply selected** authorizes scoring, résumé tailoring, and final application
  for the checked roles. It also works while scheduled automation is paused.
- **Score & tailor selected** prepares résumés and stops for review.
- **Your applications** appears immediately after Apply selected. It shows
  queued, scoring, tailoring, browser application, and final outcomes without
  leaving the search page. Progress refreshes every six seconds and survives
  page reloads and search-filter changes.
- Successful applications show the application date. Roles needing attention
  show the reason and a link to review them in the existing pipeline.
- A lost connection or failed request remains visibly unconfirmed. Applications
  are only marked applied after employer confirmation; existing score gates,
  duplicate protection, résumé checks, and required-answer gates still apply.
- Selecting roles never approves other jobs at the same company.

### Setup and compatibility

- Startup checks and installs missing Career Ops scanner dependencies with npm
  lifecycle scripts disabled. No separate upstream UI server is required.
- Career Ops web search uses the selected client's subscription login, without
  silently falling back to API-key billing. This choice controls search;
  scoring, tailoring, and browser submission use AppliedIn's existing services.
- The default scan checks 150 companies per selected source. Continue a scan to
  check more. AI search supplements directory reads with up to 30 leads; source
  pagination limits and unreachable boards can still limit coverage.
- Restart AppliedIn after updating the Python code, once active applications
  finish, then hard-refresh the dashboard to load the updated UI.

Validation: 592 Python tests and 45 JavaScript tests passed. Isolated browser
checks covered exact selections, immediate feedback, progress after reload,
failed requests, and mobile layout, with application requests mocked.
