"""Local job-board routes; discovery never authorizes a submission."""

import asyncio
import json
import subprocess
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from discovery import career_ops

router = APIRouter(prefix="/career-ops")


class BoardSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)


class BoardSettings(BaseModel):
    companies: list[str] = Field(max_length=300)
    scheduled: bool
    auto_prepare: bool
    interests: str = Field(default="", max_length=600)
    scheduled_interests: bool = False
    search_provider: Literal["claude", "codex"] = "claude"


class ScanScope(BaseModel):
    company: str = Field(default="", max_length=200)


class InterestSearch(ScanScope):
    interests: str | None = Field(default=None, max_length=600)
    provider: Literal["claude", "codex"] = "claude"


class SearchProvider(BaseModel):
    provider: Literal["claude", "codex"]


@router.post("/provider")
def provider(body: SearchProvider):
    with career_ops._LOCK:
        data = career_ops._read()
        data["settings"]["search_provider"] = body.provider
        career_ops._write(data)
    return {"provider": body.provider}


class NetworkSearch(BaseModel):
    provider: Literal["claude", "codex"] = "claude"
    interests: str = Field(default="", max_length=600)
    positive: list[str] = Field(default_factory=list, max_length=50)
    negative: list[str] = Field(default_factory=list, max_length=50)
    # Locations are matched as whole words, so a city and its country are separate
    # entries; a Europe + Asia + US search runs well past fifty.
    locations: list[str] = Field(default_factory=list, max_length=200)
    ats: list[Literal["greenhouse", "lever", "ashby", "workday", "icims"]] = Field(
        min_length=1, max_length=5
    )
    days: int = Field(default=30, ge=1, le=365)
    include_undated: bool = True
    limit: int = Field(default=150, ge=0, le=10000)
    resume: bool = False


@router.post("/network")
def network(body: NetworkSearch, background: BackgroundTasks):
    from discovery import career_network

    try:
        career_ops.catalog()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, str(exc)) from exc
    filters = body.model_dump(exclude={"resume"})
    filters["ats"] = list(dict.fromkeys(filters["ats"]))
    if body.resume:
        with career_ops._LOCK:
            previous = career_ops._read().get("network_search")
        if not previous or previous["filters"] != filters:
            raise HTTPException(400, "Filters changed. Start a new scan to use these filters.")
    if not career_ops.reserve_scan("network"):
        return {"running": True, "already_running": True}
    career_network.STOP.clear()
    background.add_task(career_network.run_reserved, filters, body.resume)
    return {"running": True, "kind": "network"}


@router.post("/network/stop")
def stop_network():
    from discovery import career_network

    return career_network.stop()


@router.post("/network/classify")
def classify_network(background: BackgroundTasks):
    """Label unclassified jobs from the latest network search. Does not submit."""
    from discovery.career_fit import classify_reserved

    if not career_ops.reserve_scan(
        "classify",
        message="Classifying unclassified roles from the latest search",
    ):
        return {"running": True, "already_running": True}
    background.add_task(classify_reserved)
    return {"running": True, "kind": "classify"}


@router.get("")
def board():
    return career_ops.snapshot()


@router.get("/job/{identity}")
def job_detail(identity: str):
    """Read saved information only; opening a drawer never starts an agent."""
    import html

    from tools.jd import _text

    with career_ops._LOCK:
        job = dict(career_ops._read()["jobs"].get(identity) or {})
    if not job:
        raise HTTPException(404, "This role is no longer on the discovery board.")
    tracked, resume_url = {}, None
    if job.get("pk"):
        from server import _to_ui

        stores = career_ops.make_stores()
        tracked = stores.tracking.get(job["pk"]) or {}
        resume_url = _to_ui(tracked, stores.artifacts)["resume_url"]
    description = tracked.get("jd_text") or job.get("description") or ""
    description = html.unescape(_text(description))
    return {
        "id": identity,
        "description": description,
        "resume_url": resume_url,
        "match_score": tracked.get("match_score"),
        "status": tracked.get("status", ""),
        "applied_at": tracked.get("applied_at", ""),
    }


@router.get("/progress")
async def progress():
    async def updates():
        previous = None
        while True:
            state = career_ops.progress_snapshot()
            payload = json.dumps(state, ensure_ascii=False)
            if payload != previous:
                yield f"data: {payload}\n\n"
                previous = payload
            else:
                yield ": keep-alive\n\n"
            if not state["running"]:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        updates(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/settings")
def settings(body: BoardSettings):
    try:
        return career_ops.configure(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/scan")
def scan(body: ScanScope, background: BackgroundTasks):
    try:
        sources = career_ops.catalog()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    use_web = body.company and body.company not in {s["name"] for s in sources}
    if not career_ops.reserve_scan("company_web" if use_web else "feeds", body.company):
        return {"running": True, "already_running": True}
    background.add_task(
        career_ops.search_reserved if use_web else career_ops.scan_reserved, body.company
    )
    return {"running": True, "kind": "web" if use_web else "feeds"}


@router.post("/search")
def search(body: InterestSearch, background: BackgroundTasks):
    if not career_ops.reserve_scan("company_web" if body.company else "interests", body.company):
        return {"running": True, "already_running": True}
    background.add_task(career_ops.search_reserved, body.company, body.interests, body.provider)
    return {"running": True, "kind": "web"}


@router.post("/prepare")
def prepare(body: BoardSelection):
    try:
        return career_ops.prepare(body.ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/apply")
def apply_selected(body: BoardSelection, background: BackgroundTasks):
    from discovery.career_apply import run_selected

    try:
        result = career_ops.prepare(body.ids, apply_requested=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    background.add_task(run_selected, result["pks"])
    return {**result, "started": result["prepared"]}


@router.post("/dismiss")
def dismiss(body: BoardSelection):
    try:
        return career_ops.dismiss(body.ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
