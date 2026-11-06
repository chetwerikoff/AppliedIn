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
def _save_offline(monkeypatch, tmp_path):
    """No rendering, external stores, browser, network or owner runtime inputs."""
    from types import SimpleNamespace
    from agent.graph import save_tailored_resume
    from agent import graph as graph_module
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

    # Tests use only an isolated temporary directory, never the private runtime file.
    monkeypatch.setattr(graph_module, "get_settings",
                        lambda: SimpleNamespace(local_dir=str(tmp_path)))
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


def test_issue27_within_role_rewording_and_reordering_pass_inherited_guard(_save_offline):
    """#26 guards roles and total bullets; #27 prompts own factual attribution.

    Both roles retain their own bullet facts but change wording and relevance
    order, including the older role. A structural guard must permit this; the
    adjacent refusal regressions already cover an altered date and a loss of
    base-used bullets before any side effect.
    """
    base = _save_doc("\n".join([
        r"\role{example-co}{Engineer}{2022--2024}",
        r"\item Maintained Python queue consumers.",
        r"\item Documented queue retries.",
        r"\role{example-co-lab}{Technician}{2020--2022}",
        r"\item Coordinated synthetic QA runs.",
        r"\item Maintained SQL reporting.",
    ]))
    tailored = _save_doc("\n".join([
        r"\role{example-co}{Engineer}{2022--2024}",
        r"\item Documented retry behavior for queue consumers.",
        r"\item Kept Python queue consumers running.",
        r"\role{example-co-lab}{Technician}{2020--2022}",
        r"\item Supported SQL reports.",
        r"\item Coordinated QA execution for synthetic runs.",
    ]))
    result, state, effects = _save_offline(base, tailored)
    assert result["ok"] is True
    assert state["tailored"] == tailored
    assert effects == {"make_stores": 1, "render": 1, "artifact": 2, "tracking": 1}


@pytest.fixture
def pdf_world(monkeypatch, tmp_path):
    """Real save + run hand-offs with fake ADK, compiler, artifacts and stores."""
    from copy import deepcopy
    from agent import graph as G, run as R
    from core import profiles, steering, events
    from discovery.watchlist import Preferences
    from google.adk.sessions import InMemorySessionService
    from tests.agent.test_scorer_invalid_output import _StrictTracking, JD
    from tools import posting_guard as pg, render

    pk = "example-co#731205"
    tracking = _StrictTracking(dict(
        pk=pk, company="example-co", job_id="731205", ats="custom",
        title="Test Engineer", status="found", jd_url="https://example.test/jobs/731205",
        jd_text=JD, resume_s3_key="resumes/old.pdf", tailored_at="2000-01-01"))
    class Artifacts:
        def __init__(self):
            self.data = {"resumes/old.pdf": b"%PDF-old"}
        def put(self, category, name, data, content_type):
            key = category + "/" + name
            self.data[key] = data
            return key
        def get(self, key):
            return self.data[key]
    class Queue:
        def __init__(self):
            self.calls, self.waiting, self.leased = [], set(), set()
            self.lose_removal = False
        def enqueue(self, name, data):
            self.calls.append((name, data))
        def in_flight(self):
            return set(self.leased)
        def has_pending(self, pk):
            return pk in self.waiting
        def remove(self, pk):
            if self.lose_removal:
                self.leased.add(pk)
                return False
            self.waiting.discard(pk)
            return True
    queue = Queue()
    artifacts = Artifacts()
    stores = SimpleNamespace(tracking=tracking, artifacts=artifacts, queue=queue,
                             apply_queue="synthetic-apply", tailor_queue="synthetic-tailor")
    world = SimpleNamespace(pk=pk, stores=stores, script=["ok"], auto=False,
                            renders=0, before_render=None, after_save=None, results=[])
    seed = _save_doc(_SAVE_HEAD + "\n" + r"\item One")
    monkeypatch.setattr(G, "get_settings", lambda: SimpleNamespace(local_dir=str(tmp_path)))
    monkeypatch.setattr(profiles, "resolve_for", lambda row: None)
    monkeypatch.setattr(profiles, "apply_to_latex", lambda source, profile: source)
    monkeypatch.setattr(R, "seed_fingerprint", lambda: "synthetic-seed")
    monkeypatch.setattr(R, "_effective_prefs", lambda company: Preferences())
    monkeypatch.setattr(steering, "read", lambda: {"content": ""})
    monkeypatch.setattr(events, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(R, "_save_output", lambda *a: None)
    monkeypatch.setattr(R, "_min_score", lambda: 7)
    monkeypatch.setattr(R, "_claim", lambda *a: True)
    monkeypatch.setattr(R, "_release", lambda *a: None)
    monkeypatch.setattr(R, "_auto_decision", lambda *a: "go" if world.auto else "")
    monkeypatch.setattr(R, "_session_state", lambda row, jd: dict(
        pk=row["pk"], company=row["company"], base_latex=seed, jd_text=jd, tailor_note=row.get("tailor_note", "")))
    monkeypatch.setattr(R, "_session_service", lambda: InMemorySessionService())
    async def jd(row, **kw):
        return pg.analyze(JD)
    monkeypatch.setattr(R, "_jd_meta", jd)
    monkeypatch.setattr(render, "sanitize_latex", lambda source: source.replace("One", "One "))

    def compiler(source):
        world.renders += 1
        if world.before_render:
            world.before_render()
        if world.current == "compile":
            raise RuntimeError("synthetic compile error")
        return b"%PDF-synthetic-current"
    monkeypatch.setattr(render, "render_pdf", compiler)

    class Event:
        author = "tailor"
        content = None
        def __init__(self, response=None, gate=False):
            self.response, self.gate = response, gate
        def get_function_calls(self):
            return [SimpleNamespace(name="ask_human", id="test-gate", args={
                "question": "Ready to apply?"})] if self.gate else []
        def get_function_responses(self):
            return [SimpleNamespace(name="save_tailored_resume", response=self.response)] if self.response is not None else []

    class Runner:
        def __init__(self, agent=None, **kw):
            self.agent = agent
        async def run_async(self, **kw):
            for action in world.script:
                world.current = action
                if action == "runner-error":
                    raise RuntimeError("synthetic runner failure")
                if action == "none":
                    continue
                if action == "tex-only":
                    response = {"ok": True, "format": "tex", "resume_s3_key": "resumes/old.pdf"}
                elif action == "false":
                    response = {"ok": False, "format": "pdf"}
                else:
                    response = G.save_tailored_resume(seed, SimpleNamespace(state={
                        "pk": world.pk, "base_latex": seed}))
                world.results.append(deepcopy(response))
                if world.after_save:
                    world.after_save(response)
                yield Event(response)
            # The review agent has no applier or approval tool.
            if (getattr(world, "force_gate", False)
                    or (self.agent is not G.review_agent and self.agent is not G.tailor)):
                yield Event(gate=True)
    monkeypatch.setattr(R, "Runner", Runner)
    # _tailor_only imports Runner directly, so exercise its real event loop too.
    monkeypatch.setattr("google.adk.runners.Runner", Runner)
    world.runner = Runner
    world.seed = seed
    return world


@pytest.mark.parametrize("mode", ["normal", "prepare", "auto", "requested"])
@pytest.mark.parametrize("script", [["compile"], ["false"], ["tex-only"]])
def test_current_pdf_failure_cannot_be_replaced_by_old_pdf(pdf_world, mode, script):
    from agent import run as R
    w = pdf_world
    w.script, w.auto = script, mode == "auto"
    if mode == "requested":
        w.stores.tracking.row["apply_requested_at"] = "synthetic-request"
    before = dict(w.stores.artifacts.data)
    out = R.run_job(w.pk, w.stores, prepare_only=mode in ("prepare", "requested"))
    assert out["result"] == "error"
    row = w.stores.tracking.row
    assert row["status"] == "error" and row["fail_kind"] == "pdf_save_failed"
    assert "PDF" in row["error"] and "retry" in row["error"]
    assert row["resume_s3_key"] == "resumes/old.pdf"
    assert w.stores.artifacts.data == before
    assert not row["apply_requested_at"] and not row["gate_pending"]
    assert w.stores.queue.calls == []
    if script == ["compile"]:
        assert w.renders == 2  # both initial compilation and sanitizer retry fail


@pytest.mark.parametrize("mode", ["normal", "prepare", "auto", "requested"])
def test_no_save_is_distinct_from_pdf_compile_failure(pdf_world, mode):
    from agent import run as R
    w = pdf_world
    w.script, w.auto = ["none"], mode == "auto"
    if mode == "requested":
        w.stores.tracking.row["apply_requested_at"] = "synthetic-request"
    out = R.run_job(w.pk, w.stores, prepare_only=mode in ("prepare", "requested"))
    assert out["reason"] == "no_resume" and out["result"] == "failed"
    assert w.renders == 0 and w.stores.queue.calls == []


@pytest.mark.parametrize("script", [["ok"], ["compile", "ok"]])
def test_later_real_save_supersedes_failed_attempt_in_same_invocation(pdf_world, script):
    from agent import run as R
    w = pdf_world
    w.script = script
    out = R.run_job(w.pk, w.stores, prepare_only=True)
    assert out["result"] == "prepared"
    row = w.stores.tracking.row
    assert row["status"] == "tailored"
    assert row["resume_s3_key"] != "resumes/old.pdf"
    assert w.stores.artifacts.get(row["resume_s3_key"]) == b"%PDF-synthetic-current"
    assert w.stores.artifacts.get("resumes/old.pdf") == b"%PDF-old"
    assert w.results[-1]["ok"] is True and w.results[-1]["format"] == "pdf"


def test_committed_artifact_must_match_current_successful_tool_result(pdf_world):
    from agent import run as R
    w = pdf_world
    def tamper(response):
        w.stores.artifacts.data[response["resume_s3_key"]] = b"%PDF-old"
    w.after_save = tamper
    assert R.run_job(w.pk, w.stores, prepare_only=True)["result"] == "error"
    assert w.stores.queue.calls == []


@pytest.mark.parametrize("concurrent", ["applied", "submitting", "needs_human"])
def test_pdf_commit_and_failure_never_overwrite_competing_generation(pdf_world, concurrent):
    from agent import run as R
    w = pdf_world
    def compete():
        w.stores.tracking.row.update(status=concurrent, jd_read_revision=100,
                                    confirmation_id="synthetic" if concurrent == "applied" else "")
    w.before_render = compete
    out = R.run_job(w.pk, w.stores, prepare_only=True)
    assert out["result"] in ("conflict", "refused")
    assert w.stores.tracking.row["status"] == concurrent
    assert w.stores.tracking.row["resume_s3_key"] == "resumes/old.pdf"
    assert w.stores.artifacts.get("resumes/old.pdf") == b"%PDF-old"
    assert w.stores.queue.calls == []


@pytest.mark.parametrize("success", [False, True])
def test_retailor_withdraws_old_pending_authority_before_start(pdf_world, success):
    from agent import run as R
    w = pdf_world
    w.script = ["ok"] if success else ["compile"]
    row = w.stores.tracking.row
    row.update(status="tailored", apply_requested_at="old-request", gate_reason="approval")
    w.stores.queue.waiting.add(w.pk)
    out = R.retailor(w.pk, "Synthetic emphasis", w.stores)
    assert out["result"] == ("ok" if success else "error")
    assert row["status"] == ("tailored" if success else "error")
    assert not row["apply_requested_at"] and not row["gate_pending"]
    assert not w.stores.queue.waiting and not w.stores.queue.calls
    assert w.stores.artifacts.get("resumes/old.pdf") == b"%PDF-old"


@pytest.mark.parametrize("leased", [False, True])
def test_retailor_refuses_without_mutation_when_withdrawal_is_unsafe(pdf_world, leased):
    from agent import run as R
    w = pdf_world
    row = w.stores.tracking.row
    row.update(status="tailored", apply_requested_at="old-request")
    w.stores.queue.waiting.add(w.pk)
    if leased:
        w.stores.queue.leased.add(w.pk)
    else:
        w.stores.queue.lose_removal = True
    before = dict(row)
    out = R.retailor(w.pk, "Synthetic change", w.stores)
    assert out["result"] == "refused" and row == before
    assert w.renders == 0 and w.results == []


@pytest.mark.parametrize("gate", [False, True])
def test_board_preparation_cannot_inherit_request_or_create_approval(pdf_world, gate):
    from agent import run as R
    w = pdf_world
    w.force_gate, w.auto = gate, True
    row = w.stores.tracking.row
    row.update(discovery_source="greenhouse_board_listing",
               apply_requested_at="old-request", gate_reason="approval",
               gate_call_id="old-call", gate_source="old-agent")
    out = R.run_job(w.pk, w.stores)
    assert out["result"] == "prepared" and row["status"] == "tailored"
    assert not any(row.get(key) for key in (
        "apply_requested_at", "gate_reason", "gate_pending", "gate_call_id", "gate_source"))
    assert w.stores.queue.calls == []


def test_changed_row_pdf_key_is_not_the_saved_current_artifact(pdf_world):
    from agent import run as R
    w = pdf_world
    w.after_save = lambda response: w.stores.tracking.row.update(resume_s3_key="resumes/old.pdf")
    out = R.run_job(w.pk, w.stores, prepare_only=True)
    assert out["result"] == "conflict" and out["reason"] == "pdf_artifact_changed"
    assert w.stores.tracking.row["status"] != "tailored"
    assert w.stores.queue.calls == []


@pytest.mark.parametrize("script", [["runner-error"], ["ok", "runner-error"]])
def test_retailor_runner_failure_never_becomes_compile_failure_or_readiness(pdf_world, script):
    from agent import run as R
    w = pdf_world
    w.script = script
    w.stores.tracking.row.update(status="tailored")
    out = R.retailor(w.pk, stores=w.stores)
    assert out["result"] == "error" and out["reason"] == "retailor_failed"
    assert w.stores.tracking.row["status"] == "error"
    assert w.stores.queue.calls == []
