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

def _country(location: str) -> str | None:
    # Compare only known atomic, equal-granularity scopes. A city/state versus
    # a country, a list of scopes, or an unfamiliar label is not disjointness.
    country = location.strip()
    for aliases in (
        ("united states", "united states of america", "us", "usa", "u.s."),
        ("united kingdom", "uk", "gb", "great britain"),
        ("canada",), ("germany",), ("france",), ("australia",), ("india",),
    ):
        if country in aliases:
            return aliases[0]
    return None


def location_conflict(location: str, prefs: Preferences) -> str | None:
    """Only demonstrated work-format or country disjointness is a hard stop."""
    loc = location.strip().casefold()
    if prefs.remote_only and re.search(r"\b(?:on-site|onsite|on site|hybrid)\b", loc):
        return "remote only"
    region = re.sub(r"\b(?:remote|on-site|onsite|on site|hybrid)\b", "", loc).strip(" ,-/()")
    country = _country(region)
    preferred = {_country(p.strip().casefold()) for p in prefs.locations}
    # Non-country keywords must not erase explicit preferred countries.
    preferred.discard(None)
    if country and preferred and country not in preferred:
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
