# Setup: "set this repo up and start the server"

This is the most common thing you will be asked, and it is fully scripted, so
follow it rather than improvising. Work through the steps in order and verify each
one before the next. Do not run them all at once and hope.

Steps 3 to 5 need things only the human has: an API key, their résumé, and which
companies they care about. **Ask for those. Never invent them**, and never write a
key into a file you have not read first.

There is exactly one entry point, `./appliedin`. If you find yourself reaching for
another script, you are looking at stale instructions.

### 1. Install

```bash
./appliedin setup
```

Installs uv, Python 3.12, the dependencies, Redis (queues and runtime flags) and
Tectonic (renders the résumé PDF). Idempotent, so re-running is safe.

`./appliedin` is the only entry point. `start` runs this for you when the
dependencies have moved, so this step is really just "get it wrong early rather
than at launch".

Verify: `.venv` exists and `redis-cli ping` answers `PONG`.

### 2. Confirm what actually submits applications

```bash
command -v claude
```

Applications are filled in by a `claude --chrome` subprocess driving the human's
own Chrome. That needs the Claude Code CLI and a Claude **subscription**; it
refuses API-key auth. Discovery, scoring and tailoring all work without it, so
this is a warning rather than a blocker. Say which of the two situations they are
in rather than letting them find out at the first apply.

### 3. The API key

Setup copies `.env.example` to `.env`. It needs one line filled in:

```
OPENAI_API_KEY=sk-...
```

That is the key that gates startup, because orchestration (discovery, scoring,
tailoring, the writer) runs on OpenAI by default.

`ANTHROPIC_API_KEY` is **not** what makes applying work, which is the mistake to
head off: applying goes through the Claude CLI's own subscription session, never
an API key. The commented line in `.env.example` is only for pointing an
individual stage at an Anthropic model through LiteLLM, which is optional.

**Ask the human for the key. Never invent one, and never paste one into a file
you did not read first.**

### 4. The résumé

`resume/base.tex`, in LaTeX, because tailoring edits the source and Tectonic
renders the PDF from it. Without it, tailoring has nothing to work from and the
apply has nothing to attach.

Ask them to save it there. If they have a PDF or Word file, say plainly that it
has to be LaTeX and offer to help convert it; do not fabricate a résumé.

Verify: `.venv/bin/python -c "from tools.render import render_pdf; render_pdf(open('resume/base.tex').read())"`
renders without raising.

### 5. What to look for, and what counts as a match

- `config/watchlist.yaml` — the companies. Tracked in git, so treat it as public.
  Each entry needs a `discovery` mode; see [Adding a company](../AGENTS.md#adding-a-company).
- `config/preferences.yaml` — titles, seniority, locations, the score bar. Also
  tracked, also public.
- `.local/facts.md` — the answers used to fill forms, seeded from
  `facts.seed.example.yaml`. This one is private and gitignored.

These can be edited later in the dashboard, so a first run does not need them
perfect. It does need at least one company, or discovery has nothing to scan.

### 6. Start it

```bash
./appliedin start
```

Installs anything missing, brings up Redis, checks the config, then launches the
daemon in the background. Dashboard on `http://127.0.0.1:8787`.

Verify it is really up before reporting success:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/
```

Expect `200`. Then tell them the first run does nothing on its own: discovery is
on a six hour schedule and does not sweep at boot, so they should press **Discover**
scoped to one company to see the pipeline work end to end.

### When it does not work

| symptom | cause |
|---|---|
| `paste your key into .env` and they already did | the key must be `OPENAI_API_KEY`. `ANTHROPIC_API_KEY` is not used anywhere. |
| dashboard loads, nothing ever happens | check the deck for **paused**. That flag lives in Redis and survives restarts. |
| discovery runs, finds nothing | expected if the watchlist is empty, or the company needs `discovery: browser`. |
| tailoring cannot render a PDF | Tectonic missing, or a LaTeX error in `resume/base.tex`. Render it by hand for the real message. |
| apply does nothing | no `claude` on PATH, or it is authenticated with an API key rather than a subscription. |
