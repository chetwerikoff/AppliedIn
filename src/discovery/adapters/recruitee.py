"""Recruitee public offers API (unauthenticated best effort only)."""

from __future__ import annotations

import re

import httpx

from core.models import JobRecord

from ..watchlist import CompanyConfig
from . import (_board_token, _careers_base, _clean, _feed, _json_rows,
               _location_text, _locations, _nonempty_or_error, _posting_url,
               _published, _remote_cues)


class RecruiteeAdapter:
    ats = "recruitee"

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]:
        board = _board_token(company.board)
        host = f"{board}.recruitee.com"
        base = f"https://{host}/api/offers/"
        _careers_base(company, board, self.ats, base)
        rows = _json_rows(_feed(client, base), "offers")
        jobs: list[JobRecord] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("id")
            if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                continue
            job_id = str(raw_id)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
                continue
            title = _clean(row.get("title"), 512, required=True)
            jd_text = _clean(row.get("description") or "", 20_000)
            location = _location_text(
                _locations(row.get("locations")), _remote_cues(row))
            link = _posting_url(row.get("careers_url"), host,
                                r"/o/[A-Za-z0-9][A-Za-z0-9_-]{0,127}", base)
            if title is None or jd_text is None or location is None or link is None:
                continue
            jobs.append(JobRecord(
                company=company.name, job_id=job_id, title=title,
                jd_url=link, jd_text=jd_text, location=location, ats=self.ats,
                posted_at=_published(row.get("published_at")),
            ))
        return _nonempty_or_error(rows, jobs)
