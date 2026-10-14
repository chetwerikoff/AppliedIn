"""Personio native XML jobs."""

from __future__ import annotations

import re

import httpx

from core.models import JobRecord

from ..watchlist import CompanyConfig
from ._feed import (
    _board_token, _careers_base, _child_text, _clean, _feed, _location_text,
    _nonempty_or_error, _posting_url, _tag, _url_parts, _xml_rows,
)


_NUMERIC_ID = re.compile(r"[1-9][0-9]{0,127}\Z")


class PersonioAdapter:
    ats = "personio"

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]:
        board = _board_token(company.board)
        # Only a matching, independently checked direct careers URL chooses .com;
        # bare manual overrides keep the explicit legacy .de behavior.
        suffix = "de"
        parsed = _url_parts(company.careers_url) if company.careers_url else None
        if parsed and parsed.hostname == f"{board}.jobs.personio.com":
            suffix = "com"
        host = f"{board}.jobs.personio.{suffix}"
        base = f"https://{host}/xml"
        _careers_base(company, board, self.ats, base)
        response = _feed(client, base, xml=True)
        jobs: list[JobRecord] = []
        stats: dict[str, int] = {}
        for row in _xml_rows(response.content, root_name="workzag-jobs",
                             row_name="position", stats=stats):
            job_id = _child_text(row, "id").strip()
            if not _NUMERIC_ID.fullmatch(job_id):
                continue
            title = _clean(_child_text(row, "name"), 512, required=True)
            if title is None:
                continue
            places = [
                node.text or "" for node in row.iter()
                if _tag(node) in {"office", "officeName", "location"}
                and node is not row
            ]
            cues: list[str] = []
            for node in row.iter():
                key = _tag(node).lower()
                if key in ("remote", "hybrid", "telecommuting"):
                    value = (node.text or "").strip().lower()
                    if value in ("true", "yes", key):
                        cues.append(key.capitalize())
            location = _location_text(places, tuple(cues))
            if location is None:
                continue
            link = _posting_url(f"https://{host}/job/{job_id}", host,
                                r"/job/[1-9][0-9]{0,127}", base)
            if link is None:
                continue
            description = _child_text(row, "description")
            jd_text = _clean(description, 20_000)
            if jd_text is None:
                continue
            jobs.append(JobRecord(
                company=company.name, job_id=job_id, title=title,
                jd_url=link, jd_text=jd_text, location=location,
                ats=self.ats, posted_at="",  # createdAt is NOT publication
            ))
        return _nonempty_or_error(list(range(stats.get("rows", 0))), jobs)
