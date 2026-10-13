"""Workday adapter — BEST-EFFORT (HLD premise 1).

The ``/wday/cxs/<tenant>/<site>/jobs`` endpoint is unofficial and per-tenant.
``company.board`` holds the full base URL. Parsing is defensive: a row missing
expected fields is skipped rather than crashing the whole poll.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import httpx

from core.logging import get_logger
from core.models import JobRecord

from ..watchlist import CompanyConfig

log = get_logger(__name__)

_CXS_PATH = re.compile(r"/wday/cxs/([^/]+)/([^/]+)/jobs/?\\Z")
_HOST = re.compile(r"[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)*\\Z")


def _safe_segment(segment: str) -> bool:
    """Avoid URL normalization/decoding turning a path component into a redirect or traversal."""
    decoded = unquote(segment)
    return (
        bool(segment)
        and decoded not in (".", "..")
        and not any(char in decoded for char in ("/", "\\", "?", "#", ":", "%"))
        and not any(char.isspace() for char in segment)
        and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    )


class WorkdayAdapter:
    ats = "workday"

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]:
        try:
            board = urlsplit(company.board)
            match = _CXS_PATH.fullmatch(board.path)
            valid_board = (
                board.scheme == "https"
                and bool(board.hostname)
                and _HOST.fullmatch(board.netloc) is not None
                and board.username is None
                and board.password is None
                and "?" not in company.board
                and "#" not in company.board
                and not any(char.isspace() for char in company.board)
                and match is not None
                and all(_safe_segment(segment) for segment in match.groups())
            )
        except ValueError:
            valid_board = False
        if not valid_board:
            log.warning("workday invalid CXS board for %s", company.name)
            return []

        site = match.group(2)
        base = company.board.rstrip("/")
        jobs: list[JobRecord] = []
        offset, limit = 0, 20
        while True:
            try:
                resp = client.post(
                    base,
                    json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
                    timeout=20,
                )
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("workday fetch failed for %s: %s", company.name, exc)
                break

            postings = payload.get("jobPostings", [])
            for j in postings:
                path = j.get("externalPath")
                if not isinstance(path, str) or not path.startswith("/job/"):
                    continue
                segments = path.split("/")
                if len(segments) != 4 or not all(
                    _safe_segment(segment) for segment in segments[2:]
                ):
                    continue
                jobs.append(
                    JobRecord(
                        company=company.name,
                        job_id=path.rsplit("/", 1)[-1],
                        title=j.get("title", ""),
                        jd_url=f"https://{board.netloc}/{site}{path}",
                        jd_text=j.get("title", ""),  # detail requires a per-job call
                        location=j.get("locationsText", ""),
                        ats=self.ats,
                    )
                )
            offset += limit
            if offset >= payload.get("total", len(postings)) or not postings:
                break
        return jobs
