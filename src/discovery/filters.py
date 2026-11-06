"""Stage-1 deterministic filter.

Cheap keyword/title/location gate that runs inside discovery so slow LLM
relevance scoring never blocks the poller (HLD premise 2). The LLM stage-2
score runs later, in the tailoring worker.
"""

from __future__ import annotations
import re

from core.models import JobRecord

from .watchlist import Preferences


def _contains_any(haystack: str, needles: list[str]) -> bool:
    low = haystack.lower()
    return any(n.lower() in low for n in needles)

def location_conflict(location: str, prefs: Preferences) -> str | None:
    """Unknown remote scope is not a conflict; an explicit region still is."""
    loc = location.strip().casefold()
    if prefs.remote_only and re.search(r"\b(?:on-site|onsite|on site|hybrid)\b", loc):
        return "remote only"
    region = re.sub(r"\b(?:remote|on-site|onsite|on site|hybrid)\b", "", loc).strip(" ,-/()")
    if region in ("", "unknown", "unspecified", "not specified", "anywhere", "worldwide", "global"):
        return None
    if prefs.locations and not any(
            re.search(r"\b" + re.escape(p.casefold()) + r"\b", region)
            for p in prefs.locations):
        return "location"
    return None



def stage1_match(job: JobRecord, prefs: Preferences) -> bool:
    text = f"{job.title}\n{job.jd_text}"

    if prefs.exclude_keywords and _contains_any(text, prefs.exclude_keywords):
        return False

    # include_keywords RAISE fit; they are not a requirement. This filter only
    # runs when the relevance screen fails, and rejecting everything without an
    # AI/agentic keyword there would quietly discard the plain "Senior Software
    # Engineer" roles the owner also asked for — a degraded screen should be
    # blunter, not stricter.

    if prefs.titles and not _contains_any(job.title, prefs.titles):
        return False

    return location_conflict(job.location, prefs) is None
