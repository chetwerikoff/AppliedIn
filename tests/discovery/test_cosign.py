"""Cosign search: preference seeding, exclusions, and a staging path that never applies."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from core.storage.local import RedisTracking
from discovery import career_ops as co
from discovery import cosign


def posting(pid, title="Senior Software Engineer", **extra):
    return {
        "postingId": pid,
        "title": title,
        "organizationName": "Acme",
        "organizationImageUrl": "https://img.example/acme.png",
        "location": "Seattle, WA",
        "remote": False,
        "team": "Platform",
        "employmentType": "FullTime",
        "salaryMinUsd": 200000,
        "salaryMaxUsd": 260000,
        "postedAt": 1790380774971,
        "firstSeenAt": 1790384170453,
        "url": f"https://jobs.ashbyhq.com/acme/{pid}",
        "description": "Build the platform.",
        "closed": False,
        **extra,
    }


def convex(pages, calls=None):
    """A Convex endpoint that answers each call with the next canned reply."""
    replies = iter(pages)

    def handle(request):
        if calls is not None:
            calls.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    return httpx.Client(transport=httpx.MockTransport(handle))


def ok(jobs, done=True):
    return {"status": "success", "value": {"page": jobs, "isDone": done, "continueCursor": "c2"}}


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_path", lambda: tmp_path / "board.json")
    stores = SimpleNamespace(
        tracking=RedisTracking(fakeredis.FakeRedis(decode_responses=True)),
        queue=Mock(),
        tailor_queue="tailor",
        apply_queue="apply",
    )
    monkeypatch.setattr(co, "make_stores", lambda: stores)
    monkeypatch.setattr(
        co,
        "search_preferences",
        lambda company="": {"exclude_keywords": ["intern", "ios"], "titles": []},
    )
    monkeypatch.setattr("tools.seen.load", lambda: [])
    monkeypatch.setattr("tools.seen.mark", lambda jobs: None)
    monkeypatch.setattr("core.events.emit", lambda *a, **k: None)
    monkeypatch.setattr(cosign.time, "sleep", lambda s: None)
    cosign._SEEN.clear()
    yield stores
    cosign._SEEN.clear()


def test_defaults_search_all_of_the_us_with_the_broadest_title():
    # "Software Engineer" also returns the Senior and Staff roles; the longest
    # title would hide them. The owner asked for all of the US by default; their
    # own cities stay one tap away rather than narrowing the first search.
    d = cosign.defaults(
        {
            "titles": ["Senior Software Engineer", "Software Engineer"],
            "locations": ["Bellevue", "California"],
            "remote_only": True,
        }
    )
    assert d == {
        "term": "Software Engineer",
        "role": "",
        "region": "us",
        "cities": [],
        "preferred": ["seattle", "san-francisco"],
        "remote": True,
        "min_salary": 0,
    }


def test_preferred_places_become_each_city_once_up_to_the_cap():
    # Seattle, Bellevue and Washington all fold into one Seattle search rather
    # than three identical ones.
    d = cosign.defaults(
        {
            "locations": [
                "Seattle",
                "Bellevue",
                "Washington",
                "Austin",
                "Boston",
                "Denver",
                "Chicago",
                "Miami",
            ]
        }
    )
    assert d["preferred"] == ["seattle", "austin", "boston", "denver", "chicago"]


@pytest.mark.parametrize(
    "location, cities, us",
    [
        ("Raleigh", [{"slug": "raleigh"}], True),
        ("Rome, NY", [], True),
        ("Santa Clara, CALIFORNIA, United States", [], True),
        ("Remote-USA", [], True),
        ("Remote US", [], True),
        ("Burlingame, CA - Hybrid; Denver, CO - Hybrid", [], True),
        ("Remote - Multiple Locations · United States · Canada", [], True),
        ("Canada, BC, Vancouver", [{"slug": "vancouver"}], False),
        ("Remote Canada", [], False),
        ("Raanana", [], False),
        ("Mexico City", [{"slug": "mexico-city"}], False),
        # Names no state or country: missed on purpose rather than guessed.
        ("Tysons Corner", [], False),
    ],
)
def test_us_postings_are_recognised_from_what_cosign_gives(location, cities, us):
    assert cosign.is_us(location, cities) is us


@pytest.mark.parametrize(
    "title, hit",
    [
        ("Software Engineer Intern", "intern"),
        ("iOS Engineer", "ios"),
        ("Internal Tools Engineer", ""),  # "intern" inside a word is not an intern
        ("BIOS Firmware Engineer", ""),
    ],
)
def test_exclusions_match_whole_words_only(title, hit):
    assert cosign.excluded_by(title, {"exclude_keywords": ["intern", "ios"]}) == hit


def test_unknown_filters_are_refused_before_reaching_cosign():
    # Cosign answers an unknown city with a bare server error, which would read
    # as an outage; refuse it here with a message that says what to fix.
    with pytest.raises(ValueError):
        cosign._args({"city": "bellevue"}, None)
    with pytest.raises(ValueError):
        cosign._args({"role": "astronaut"}, None)
    assert cosign._args({"term": " x ", "remote": True, "min_salary": 150000}, "cur") == {
        "cursor": "cur",
        "limit": cosign.PAGE,
        "term": "x",
        "remoteOnly": True,
        "minSalaryUsd": 150000,
    }


def test_search_flags_exclusions_and_drops_closed_postings(board):
    client = convex(
        [
            ok(
                [posting("a"), posting("b", "Engineering Intern"), posting("c", closed=True)],
                done=False,
            )
        ]
    )
    page = cosign.search({"term": "engineer"}, client=client)
    assert [j["id"] for j in page["jobs"]] == ["a", "b"]
    assert page["jobs"][1]["excluded"] == "intern"
    assert page["cursor"] == "c2" and not page["done"]


def test_a_cold_shard_server_error_is_retried(board):
    # Cosign's own client retries a bare "Server Error"; without the retry the
    # first search after an idle spell fails for no reason the owner can act on.
    calls = []
    client = convex(
        [{"status": "error", "errorMessage": "[Request ID: x] Server Error"}, ok([posting("a")])],
        calls,
    )
    assert len(cosign.search({}, client=client)["jobs"]) == 1
    assert len(calls) == 2


def test_staging_prepares_only_and_records_the_real_ats(board):
    cosign.search({}, client=convex([ok([posting("a")])]))
    result = cosign.stage(["a"])
    assert result["prepared"] == 1
    row = board.tracking.get(result["pks"][0])
    # The career_ops source is what makes the pipeline stop for approval.
    assert row["discovery_source"] == "career_ops"
    assert row["apply_requested_at"] == ""
    # Cosign is an index; the agent must be told the posting is on Ashby.
    assert row["ats"] == "ashby"
    board.queue.enqueue.assert_called_once()
    # Staging twice never creates a second application.
    assert cosign.stage(["a"])["prepared"] == 0


def test_only_explicit_apply_marks_a_role_for_submission(board):
    cosign.search({}, client=convex([ok([posting("a")])]))
    result = cosign.stage(["a"], apply_requested=True)
    assert board.tracking.get(result["pks"][0])["apply_requested_at"]


def test_staging_refuses_ids_this_server_never_fetched(board):
    # The browser sends ids, never URLs, so a staged role is exactly what Cosign
    # returned and cannot be swapped for another posting on the way back.
    with pytest.raises(ValueError):
        cosign.stage(["forged"])


def test_search_reports_roles_already_in_the_pipeline(board):
    cosign.search({}, client=convex([ok([posting("a")])]))
    cosign.stage(["a"])
    again = cosign.search({}, client=convex([ok([posting("a")])]))
    assert again["jobs"][0]["state"] == "pipeline"


def test_examples_are_phrases_from_the_owners_preferences():
    ex = cosign.examples(
        {
            "titles": ["Staff Software Engineer", "Software Engineer"],
            "include_keywords": ["ai", "ai agents", "platform"],
            "locations": ["Remote (US)", "Seattle"],
        }
    )
    assert ex == [
        "Staff Software Engineer",
        "Software Engineer",
        "Software Engineer, ai agents",
        "Software Engineer in Seattle",
    ]


def test_all_of_the_us_reads_ahead_past_foreign_pages_but_stops(board):
    # A global page can hold no US role at all; showing an empty page with
    # "load more" would look like a dead search. Read on, but only a few pages.
    calls = []
    foreign = [posting(f"f{i}", location="Berlin, Germany") for i in range(30)]
    us = [posting(f"u{i}") for i in range(20)]
    client = convex([ok(foreign, done=False), ok(us, done=False)], calls)
    page = cosign.search({"region": "us"}, client=client)
    assert len(page["jobs"]) == 20 and len(calls) == 2
    assert all("city" not in c["args"] for c in calls)  # Cosign has no country filter
    calls.clear()
    stuck = convex([ok(foreign, done=False)] * 9, calls)
    assert cosign.search({"region": "us"}, client=stuck)["jobs"] == []
    assert len(calls) == 4


def test_a_chosen_city_is_not_also_filtered_to_the_us(board):
    client = convex([ok([posting("t", location="Toronto")])])
    page = cosign.search({"region": "us", "city": "toronto"}, client=client)
    assert [j["id"] for j in page["jobs"]] == ["t"]


def test_select_all_stages_past_the_batch_limit_in_one_call(board):
    # prepare() takes 50 at a time; a "select all" of ~100 must still arrive as
    # one staged set, so a single apply run submits them one after another.
    jobs = [posting(f"p{i}") for i in range(60)]
    client = convex([ok(jobs[:30], done=False), ok(jobs[30:])])
    cosign.search({}, client=client)
    cosign.search({}, "c2", client=client)
    result = cosign.stage([f"p{i}" for i in range(60)], apply_requested=True)
    assert result["prepared"] == 60 and len(set(result["pks"])) == 60
    with pytest.raises(ValueError):
        cosign.stage([f"x{i}" for i in range(cosign.STAGE_MAX + 1)])


def test_staging_reports_the_application_each_role_became(board):
    # Duplicates included: a role already tracked is still one the owner wants
    # to follow in the side pane.
    cosign.search({}, client=convex([ok([posting("a"), posting("b")])]))
    first = cosign.stage(["a"])
    again = cosign.stage(["a", "b"], apply_requested=True)
    assert again["tracked"]["a"] == first["tracked"]["a"]
    assert set(again["tracked"]) == {"a", "b"}
    progress = cosign.progress(list(again["tracked"].values()))
    assert progress[again["tracked"]["b"]]["requested"] is True
    assert progress[again["tracked"]["a"]]["title"] == "Senior Software Engineer"


def test_a_restart_does_not_forget_the_page_the_owner_is_looking_at(board):
    # Every daemon restart used to wipe the in-memory results, so pressing Apply
    # on a page loaded before it failed with "Search again".
    cosign.search({}, client=convex([ok([posting("a")])]))
    cosign._SEEN.clear()  # what a restart does
    assert cosign.stage(["a"])["prepared"] == 1


@pytest.mark.parametrize(
    "prog, row, expected",
    [
        (
            {"phase": "score", "label": "Reading posting & scoring"},
            {},
            {"at": 0, "state": "active"},
        ),
        ({"phase": "tailor", "label": "Checking résumé"}, {}, {"at": 2, "state": "active"}),
        ({"phase": "waiting", "label": ""}, {}, {"at": 3, "state": "waiting"}),
        ({"phase": "applied", "label": ""}, {}, {"at": 4, "state": "done"}),
        # Where it stopped: a low score stops at Score, an open question at Apply.
        ({"phase": "attention", "label": ""}, {"status": "skipped"}, {"at": 0, "state": "stopped"}),
        (
            {"phase": "attention", "label": ""},
            {"status": "needs_human"},
            {"at": 3, "state": "stopped"},
        ),
    ],
)
def test_each_application_reports_the_step_it_reached(prog, row, expected):
    assert cosign._steps(prog, row) == expected


def test_the_timeline_reads_as_words_not_plumbing():
    events = [
        {"kind": "discovered", "detail": "Cosign: Staff SWE @ Replit"},
        {"kind": "step", "agent": "tailor", "detail": "tailor"},
        {"kind": "action", "agent": "tailor", "detail": "list_skills"},
        {
            "kind": "response",
            "agent": "scorer",
            "detail": '{"score": 8, "reasoning": "Strong agent work"}',
        },
        {"kind": "result", "agent": "tailor", "detail": "save_tailored_resume"},
        {"kind": "running", "agent": "browser", "detail": "🌐 applying in your own Chrome"},
        {"kind": "gate", "agent": "applier", "detail": "Which visa do you hold?"},
    ]
    said = [m and m["text"] for m in map(cosign._moment, events)]
    assert said == [
        "Added from Cosign",
        None,
        None,
        "Scored 8/10 — Strong agent work",
        "Résumé tailored and saved",
        "Applying in your own Chrome",
        "Which visa do you hold?",
    ]


def test_the_browsers_raw_report_is_not_a_timeline_line():
    raw = {"kind": "running", "agent": "browser", "detail": '{"outcome": "applied"}'}
    assert cosign._moment(raw) is None
