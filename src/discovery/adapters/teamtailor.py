"""Teamtailor RSS first page only; URL identity is independent of RSS GUID."""

from __future__ import annotations

import hashlib

import httpx

from core.logging import get_logger
from core.models import JobRecord

from ..watchlist import CompanyConfig
from .personio import (_board_token, _careers_base, _clean, _feed, _location_text,
               _nonempty_or_error, _posting_url, _published)
from .personio import _child_text, _tag, _xml_rows

log = get_logger(__name__)


def _rss_locations(item) -> list[str]:
    places: list[str] = []
    for node in item.iter():
        if _tag(node) != "locations":
            continue
        for place in node:
            if place.attrib.get("hidden", "").lower() == "true":
                continue
            # tt:location may contain text or a nested tt:name.
            name = place.attrib.get("name") or "".join(place.itertext()).strip()
            if name:
                places.append(name)
    return places


def _rss_cues(item) -> tuple[str, ...]:
    cues = []
    for node in item.iter():
        key = _tag(node).lower()
        if key in {"remote", "hybrid", "telecommuting"}:
            value = (node.text or "").strip().lower()
            if value in {"true", "yes", key}:
                word = key.capitalize()
                if word not in cues:
                    cues.append(word)
    return tuple(cues)


class TeamtailorAdapter:
    ats = "teamtailor"

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]:
        board = _board_token(company.board)
        host = f"{board}.teamtailor.com"
        base = f"https://{host}/jobs.rss"
        _careers_base(company, board, self.ats, base)
        response = _feed(client, base, xml=True)
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        stats: dict[str, int] = {}
        for item in _xml_rows(response.content, root_name="rss",
                              row_name="item", stats=stats):
            title = _clean(_child_text(item, "title"), 512, required=True)
            if title is None:
                continue
            link = _posting_url(_child_text(item, "link").strip(), host,
                                r"/jobs/[A-Za-z0-9][A-Za-z0-9_-]{0,127}", base)
            if link is None:
                continue
            # Both fields must be safe BEFORE constructing the JobRecord; even
            # repeated URLs from a feed do not authorize unsafe sibling text.
            location = _location_text(_rss_locations(item), _rss_cues(item))
            description = _clean(_child_text(item, "description"), 20_000)
            if location is None or description is None:
                continue
            if link in seen:
                continue
            seen.add(link)
            jobs.append(JobRecord(
                company=company.name,
                job_id=hashlib.sha256(link.encode("utf-8")).hexdigest(),
                title=title, jd_url=link, jd_text=description, location=location,
                ats=self.ats,
                posted_at=_published(_child_text(item, "pubDate"), rfc822=True),
            ))
        if stats.get("rows", 0) == 100:
            log.warning("Teamtailor first RSS page has 100 raw items; results may be truncated")
        return _nonempty_or_error(list(range(stats.get("rows", 0))), jobs)
