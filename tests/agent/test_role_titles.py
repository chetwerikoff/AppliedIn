"""Issue #133: real save/prompt seams, with fake offline runtime collaborators."""

from types import SimpleNamespace

import pytest
from google.adk.agents.readonly_context import ReadonlyContext

from agent import graph
from core.role_titles import BASENAME

PRE = r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n"
A = r"\role{example-co}{Role A}{2022--2024}"
B = r"\role{example-co}{Role Z}{2019--2021}"


def doc(*anchors, bullet=r"\resumeItem{Synthetic work.}"):
    return PRE + "\n".join((*anchors, bullet)) + "\n" + r"\end{document}"


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from agent import run as run_module
    from core import profiles, stores as stores_module
    from tools import render

    (tmp_path / BASENAME).write_text(
        "example-co: [Role B, R&D Role]\nunrelated-co: [Role X]\n",
        encoding="utf-8",
    )
    writes = {}
    row = {"status": "tailored"}

    class Tracking:
        def get(self, pk):
            return dict(row)

        def set_status(self, pk, status, **changes):
            row.update(status=status, **changes)

    class Artifacts:
        def put(self, bucket, name, contents, media_type):
            key = f"{bucket}/{name}"
            writes[key] = contents
            return key

    stores = SimpleNamespace(tracking=Tracking(), artifacts=Artifacts())
    monkeypatch.setattr(stores_module, "make_stores", lambda: stores)
    monkeypatch.setattr(graph, "get_settings",
                        lambda: SimpleNamespace(local_dir=str(tmp_path)))
    monkeypatch.setattr(profiles, "resolve_for", lambda row: object())
    monkeypatch.setattr(profiles, "apply_to_latex", lambda tex, profile: tex)
    monkeypatch.setattr(run_module, "seed_fingerprint", lambda: "synthetic-seed")
    monkeypatch.setattr(render, "render_pdf", lambda tex: b"%PDF-synthetic")

    def save(base, tailored):
        context = SimpleNamespace(state={"base_latex": base, "pk": "synthetic"})
        return graph.save_tailored_resume(tailored, context), context.state

    return SimpleNamespace(save=save, row=row, writes=writes, path=tmp_path, render=render)


def test_single_and_two_same_employer_roles_save_without_exposing_other_anchors(harness):
    for seed, tailored in [
        (doc(A), doc(A.replace("Role A", "Role B"))),
        (doc(A, B), doc(A.replace("Role A", "Role B"), B)),
        (doc(A, B), doc(A, B.replace("Role Z", "Role B"))),
    ]:
        harness.writes.clear()
        result, state = harness.save(seed, tailored)
        assert result["ok"] is True and result["format"] == "pdf"
        assert state["tailored"] == tailored
        assert harness.writes["resumes/synthetic.tex"].decode() == tailored
        assert harness.writes["resumes/synthetic.pdf"] == b"%PDF-synthetic"
        assert harness.row["resume_seed"] == "synthetic-seed"


def test_bad_title_non_title_and_missing_file_never_save(harness):
    seed = doc(A, B)
    bad = [
        doc(A.replace("Role A", "Role C"), B),
        doc(A.replace("Role A", "Role X"), B),
        doc(A.replace("example-co", "example-co-lab").replace("Role A", "Role B"), B),
        doc(A.replace("2022", "2021").replace("Role A", "Role B"), B),
        doc(A.replace("Role A", "Role B"), B.replace("2019", "2018")),
    ]
    for tailored in bad:
        result, _ = harness.save(seed, tailored)
        assert result["ok"] is False
        assert result["missing_facts"]
        assert harness.writes == {}
    (harness.path / BASENAME).unlink()
    result, _ = harness.save(seed, doc(A.replace("Role A", "Role B"), B))
    assert result["ok"] is False and result["missing_facts"]
    assert harness.writes == {}
    unchanged, _ = harness.save(seed, seed)
    assert unchanged["ok"] is True  # no-config default never rejects the seed


def test_ambiguous_same_employer_and_borrowed_witness_fail_before_write(harness):
    seed = doc(A, A.replace("Role A", "Role Z"))
    result, _ = harness.save(
        seed, doc(A.replace("Role A", "Role B"), A.replace("Role A", "Role Z"))
    )
    assert not result["ok"] and harness.writes == {}
    seed = doc(A, B)
    result, _ = harness.save(
        seed, doc(A, B.replace("Role Z", "Role B").replace("2019--2021", "2022--2024"))
    )
    assert not result["ok"] and harness.writes == {}


def test_compile_repair_that_changes_a_guarded_field_cannot_replace_good_pdf(harness, monkeypatch):
    harness.row["resume_s3_key"] = "resumes/previous.pdf"
    seed = doc(A)
    tailored = doc(A.replace("Role A", "Role B"))
    def fail(_):
        raise RuntimeError("synthetic compile failure")
    monkeypatch.setattr(harness.render, "render_pdf", fail)
    for unsafe_repair in [
        lambda tex: tex.replace("Role B", "Role C"),
        lambda tex: tex.replace("2022--2024", "2020--2024"),
    ]:
        monkeypatch.setattr(harness.render, "sanitize_latex", unsafe_repair)
        result, state = harness.save(seed, tailored)
        assert not result["ok"]
        assert harness.row["resume_s3_key"] == "resumes/previous.pdf"
        assert "tailored" not in state and "resume_s3_key" not in state
        assert harness.writes == {}


def test_compile_repair_of_unguarded_bullet_is_checked_then_persisted(harness, monkeypatch):
    seed = doc(A, bullet=r"\resumeItem{A & B}")
    tailored = doc(A.replace("Role A", "Role B"), bullet=r"\resumeItem{A & B}")
    def render(tex):
        if r"\resumeItem{A & B}" in tex:
            raise RuntimeError("synthetic bare special")
        return b"%PDF-repaired"
    monkeypatch.setattr(harness.render, "render_pdf", render)
    result, state = harness.save(seed, tailored)
    assert result["ok"] is True
    assert state["tailored"] != tailored
    assert r"\resumeItem{A \& B}" in state["tailored"]
    assert harness.writes["resumes/synthetic.tex"].decode() == state["tailored"]
    assert harness.writes["resumes/synthetic.pdf"] == b"%PDF-repaired"


def test_compile_failure_keeps_previous_good_artifact_without_writes(harness, monkeypatch):
    harness.row["resume_s3_key"] = "resumes/previous.pdf"
    monkeypatch.setattr(harness.render, "render_pdf",
                        lambda tex: (_ for _ in ()).throw(RuntimeError("synthetic fail")))
    seed = doc(A)
    result, _ = harness.save(seed, seed)
    assert result["ok"] is False
    assert harness.writes == {}
    assert harness.row["resume_s3_key"] == "resumes/previous.pdf"


@pytest.mark.asyncio
async def test_normal_and_retailor_prompts_expose_only_filtered_read_only_options(harness):
    seed = doc(A, B)
    state = {"base_latex": seed, "jd_text": "Role B", "tailored": doc(A, B)}
    ctx = ReadonlyContext(SimpleNamespace(session=SimpleNamespace(state=state)))
    # Both entrypoints use the same cloned prompt builders.
    normal = (graph.tailor, graph.critic)
    review = graph.review_agent.sub_agents[1].sub_agents
    for tailor_agent, critic_agent in (normal, review):
        for agent in (tailor_agent, critic_agent):
            assert callable(agent.instruction)
            prompt = await agent.instruction(ctx)
            assert "Role B" in prompt
            assert "Role X" not in prompt
            assert "unrelated-co" not in prompt
            assert "role-titles.yaml" not in prompt
            assert str(harness.path) not in prompt
            assert "example-co: [Role B" not in prompt
            assert "Permitted encoded title-slot replacements" in prompt
            assert A in prompt and B in prompt
            assert "Role A" in prompt
            if agent.name == "critic":
                assert "Do not demand the original seed" in prompt


@pytest.mark.asyncio
async def test_missing_file_does_not_propose_title_edit(harness):
    (harness.path / BASENAME).unlink()
    seed = doc(A)
    ctx = ReadonlyContext(SimpleNamespace(
        session=SimpleNamespace(state={"base_latex": seed, "jd_text": "Role B"})
    ))
    prompt = await graph.tailor.instruction(ctx)
    assert "APPROVED EMPLOYMENT TITLE OPTIONS: none for this seed" in prompt
    assert "Permitted encoded title-slot replacements" not in prompt
