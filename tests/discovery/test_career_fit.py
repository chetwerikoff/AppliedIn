"""A network scan can rank AI roles without a model failure burning the board."""

import json
import re

import litellm
import pytest
from fastapi import BackgroundTasks

from core import steering
from discovery import career_fit
from discovery import career_network as network
from discovery import career_ops as co
from discovery import career_ops_api as api
from tests.discovery.test_career_ops import board  # noqa: F401

# ruff: noqa: F811


def test_fit_score_weights_title_over_description_and_ai_terms_extra():
    """Title hits are 3, description hits are 1 and capped, AI terms count double."""
    assert career_fit.fit_score(
        {"title": "Platform engineer", "description": ""}, ["platform"]
    ) == 3
    assert career_fit.fit_score({"title": "", "description": "platform " * 10}, ["platform"]) == 1
    keywords = [f"k{i}" for i in range(10)]
    description = " ".join(keywords)
    # Ten description hits must not outrank one title hit.
    assert career_fit.fit_score({"title": "", "description": description}, keywords) == 3
    assert career_fit.fit_score({"title": "k0", "description": description}, keywords) == 6
    assert career_fit.fit_score({"title": "AI Platform", "description": ""}, ["ai"]) == 6
    assert career_fit.fit_score({"title": "AI Platform", "description": ""}, ["platform"]) == 3
    assert (
        career_fit.fit_score(
            {"title": "AI Platform", "description": "fintech"}, ["ai", "fintech"]
        )
        == 7
    )
    assert career_fit.fit_score({"title": "", "description": "llm systems"}, ["llm"]) == 2
    assert career_fit.fit_score({"title": "Machine Learning TPM"}, ["machine learning"]) == 6
    # Whole words, so short AI terms do not fire inside unrelated words.
    assert career_fit.fit_score(
        {"title": "Email coordinator", "description": "available"}, ["ai"]
    ) == 0
    assert career_fit.fit_score({"title": "HTML developer"}, ["ml"]) == 0
    assert career_fit.fit_score({"title": "Agents"}, ["agent"]) == 0
    assert career_fit.fit_score({"title": "AI Agent"}, ["agent"]) == 6


def test_board_serves_fit_score_and_keeps_the_description_off_the_wire(board, monkeypatch):
    _, rows = board
    monkeypatch.setattr(
        co,
        "search_preferences",
        lambda company="": {"include_keywords": ["ai", "platform"]},
    )
    data = co._read()
    data["jobs"][rows[0]["id"]].update(
        title="AI Platform TPM", description="private posting body"
    )
    co._write(data)
    visible = next(r for r in co.snapshot()["jobs"] if r["id"] == rows[0]["id"])
    stored = co._read()["jobs"][rows[0]["id"]]
    assert "description" not in visible
    assert visible["fit_score"] == career_fit.fit_score(stored, ["ai", "platform"])
    assert visible["fit_score"] == 9


def test_rescan_keeps_a_domain_tier_already_paid_for(board):
    _, rows = board
    data = co._read()
    data["jobs"][rows[0]["id"]].update(
        domain="ai", domain_reason="ml platform", domain_at="2026-10-01T00:00:00+00:00"
    )
    co._write(data)
    receipt = {"errors": [], "sources": [], "companies": 0, "found": 0, "added": 0}
    co._save_results(
        [
            {
                "company": "Acme",
                "provider": "ashby",
                "jobs": [
                    {
                        "title": "Engineer",
                        "url": rows[0]["url"],
                        "search_id": "new",
                        "description": "fresh",
                    }
                ],
            }
        ],
        receipt,
    )
    row = co._read()["jobs"][rows[0]["id"]]
    assert row["domain"] == "ai"
    assert row["domain_reason"] == "ml platform"
    assert row["domain_at"] == "2026-10-01T00:00:00+00:00"
    assert row["search_id"] == "new"
    assert row["description"] == "fresh"


def _row(i, **extra):
    description = extra.pop("description", "body")
    title = extra.pop("title", f"Role {i}")
    search_id = extra.pop("search_id", "latest")
    row = co.normalize(
        {
            "url": f"https://jobs.ashbyhq.com/acme/role-{i}",
            "title": title,
            "description": description,
            "search_id": search_id,
        },
        "Acme",
        "ashby",
    )
    row.update(search_id=search_id, **extra)
    return row


def _install(rows):
    data = co._read()
    data["jobs"] = {row["id"]: row for row in rows}
    data["last_network"] = {"search_id": "latest", "kind": "network"}
    co._write(data)


@pytest.fixture
def offline_model(monkeypatch):
    """The classifier must call steering.messages and must not touch Redis."""
    assert career_fit.messages is steering.messages
    prompts = []

    def fake_messages(task):
        prompts.append(task)
        return [{"role": "system", "content": "STEERING"}, {"role": "user", "content": task}]

    class Settings:
        def agent_model(self, name):
            assert name == "relevance"
            return "test/relevance"

    notes = []
    monkeypatch.setattr(career_fit, "messages", fake_messages)
    monkeypatch.setattr("core.config.get_settings", lambda: Settings())
    monkeypatch.setattr(
        "core.flags.note_llm_error", lambda where, msg: notes.append((where, msg))
    )

    def refuse(*_args, **_kwargs):
        raise AssertionError("domain classification must not write Redis")

    monkeypatch.setattr("core.flags.set_flag", refuse)
    return prompts, notes


def test_classification_batches_caches_and_ignores_other_searches(
    board, offline_model, monkeypatch
):
    prompts, notes = offline_model
    excerpt = "A" * 600
    rows = [
        _row(i, description=excerpt + "ENDMARKER", title="Staff TPM, Machine Learning")
        for i in range(41)
    ]
    cached = _row(41, title="Already labeled", domain="ai", domain_reason="cached", domain_at="t0")
    other = _row(42, search_id="older", title="Senior Program Manager, Health Coaching")
    _install([*rows, cached, other])
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        text = kwargs["messages"][-1]["content"]
        labels = []
        for block in text.split("\n\n"):
            match = re.search(r"(?m)^id: (\S+)$", block)
            if not match:
                continue
            labels.append({"id": match.group(1), "domain": "it", "reason": "software delivery"})
        labels.append({"id": "missing", "domain": "ai", "reason": "not on the board"})
        if labels:
            labels.append({"id": labels[0]["id"], "domain": "other", "reason": "duplicate ignored"})
        return {"choices": [{"message": {"content": json.dumps({"labels": labels})}}]}

    monkeypatch.setattr(litellm, "completion", fake)
    result = career_fit.classify_search("latest")
    assert result["failed"] is False
    assert [len(re.findall(r"(?m)^id: ", call["messages"][-1]["content"])) for call in calls] == [
        40,
        1,
    ]
    assert calls[0]["model"] == "test/relevance"
    assert calls[0]["response_format"] is career_fit.DomainBatch
    assert calls[0]["messages"][0] == {"role": "system", "content": "STEERING"}
    assert "ENDMARKER" not in prompts[0]
    assert excerpt in prompts[0]
    assert cached["id"] not in prompts[0]
    stored = co._read()["jobs"]
    assert stored[cached["id"]]["domain_at"] == "t0"
    assert stored[cached["id"]]["domain_reason"] == "cached"
    assert "domain" not in stored[other["id"]]
    labeled = [stored[row["id"]] for row in rows]
    assert {job["domain"] for job in labeled} == {"it"}
    assert all(job["domain_reason"] == "software delivery" and job["domain_at"] for job in labeled)
    progress = [event["message"] for event in co.progress_snapshot()["events"]]
    assert any("batch 1 of 2" in message for message in progress)
    assert progress[-1] == "Domain tiers saved for 41 roles"
    assert notes == []
    career_fit.classify_search("latest")
    assert len(calls) == 2


def test_llm_failure_leaves_jobs_unclassified_and_does_not_touch_redis(
    board, offline_model, monkeypatch
):
    _, notes = offline_model
    _install([_row(i) for i in range(41)])
    calls = []

    def fake(**_kwargs):
        calls.append(1)
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(litellm, "completion", fake)
    result = career_fit.classify_search("latest")
    assert result["failed"] is True
    assert result["classified"] == 0
    assert calls == [1]
    assert notes == [("career domain", "quota exceeded")]
    assert all("domain" not in job for job in co._read()["jobs"].values())
    assert "left unchanged" in co.progress_snapshot()["events"][-1]["message"]


def test_a_bad_domain_is_skipped_and_a_good_one_in_the_same_batch_is_kept(
    board, offline_model, monkeypatch
):
    _, notes = offline_model
    good = _row(1, title="Platform program")
    bad = _row(2, title="Facilities program")
    _install([good, bad])

    def fake(**_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "labels": [
                                    {"id": good["id"], "domain": "it", "reason": "software"},
                                    {"id": bad["id"], "domain": "marketing", "reason": "no"},
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(litellm, "completion", fake)
    assert career_fit.classify_search("latest")["failed"] is False
    stored = co._read()["jobs"]
    assert stored[good["id"]]["domain"] == "it"
    assert "domain" not in stored[bad["id"]]
    assert notes == []


def test_unusable_model_output_is_recorded_and_does_not_label(board, offline_model, monkeypatch):
    _, notes = offline_model
    _install([_row(1, title="Global Broadcast Strategy")])

    def fake(**_kwargs):
        return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(litellm, "completion", fake)
    result = career_fit.classify_search("latest")
    assert result["failed"] is True
    assert notes and notes[0][0] == "career domain"
    assert "domain" not in co._read()["jobs"][_row(1)["id"]]


def test_network_scan_classifies_before_it_releases_the_lock(board, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "discovery.career_fit.classify_search", lambda search_id: seen.append(search_id)
    )

    def events(_payload, timeout=1800):
        yield {"kind": "done", "stopped": False}

    monkeypatch.setattr(network, "events", events)
    filters = {
        "ats": ["ashby"],
        "days": 30,
        "include_undated": True,
        "positive": [],
        "negative": [],
        "locations": [],
        "limit": 150,
    }
    assert co.reserve_scan("network")
    network.run_reserved(filters)
    assert seen == [co._read()["last_network"]["search_id"]]
    assert not co._RUNNING
    assert not co._SCAN.locked()


def test_a_classifier_bug_still_releases_the_scan_lock(board, monkeypatch):
    def explode(_search_id):
        raise RuntimeError("bug")

    monkeypatch.setattr("discovery.career_fit.classify_search", explode)

    def events(_payload, timeout=1800):
        yield {"kind": "done", "stopped": False}

    monkeypatch.setattr(network, "events", events)
    assert co.reserve_scan("network")
    network.run_reserved(
        {
            "ats": ["ashby"],
            "days": 30,
            "include_undated": True,
            "positive": [],
            "negative": [],
            "locations": [],
            "limit": 150,
        }
    )
    assert co._read()["last_network"]["finished_at"]
    assert not co._RUNNING
    assert not co._SCAN.locked()


def test_classify_endpoint_labels_only_the_latest_search(board, offline_model, monkeypatch):
    latest = _row(1, title="Staff TPM, Machine Learning")
    older = _row(2, search_id="older", title="Global Broadcast Strategy")
    _install([latest, older])

    def fake(**kwargs):
        text = kwargs["messages"][-1]["content"]
        assert older["id"] not in text
        body = json.dumps(
            {"labels": [{"id": latest["id"], "domain": "ai", "reason": "machine learning"}]}
        )
        return {"choices": [{"message": {"content": body}}]}

    monkeypatch.setattr(litellm, "completion", fake)
    background = BackgroundTasks()
    assert api.classify_network(background) == {"running": True, "kind": "classify"}
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    stored = co._read()["jobs"]
    assert stored[latest["id"]]["domain"] == "ai"
    assert stored[latest["id"]]["domain_reason"] == "machine learning"
    assert "domain" not in stored[older["id"]]
    assert not co._RUNNING
    assert not co._SCAN.locked()


def test_classify_endpoint_does_not_start_while_a_scan_holds_the_lock(board):
    assert co.reserve_scan("network")
    background = BackgroundTasks()
    assert api.classify_network(background)["already_running"] is True
    assert background.tasks == []
