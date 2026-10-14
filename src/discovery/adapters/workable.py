"""Workable widget jobs grouped by verified account-scoped shortcode."""

from __future__ import annotations

import re

import httpx

from core.logging import get_logger
from core.models import JobRecord

from ..watchlist import CompanyConfig
from ._feed import (_SEGMENT, _board_token, _careers_base, _clean, _feed,
               _json_rows, _location_text, _locations, _nonempty_or_error,
               _posting_url, _published, _remote_cues, _url_parts)

log = get_logger(__name__)


def _source_links_match(row: dict, host: str, account: str, code: str) -> bool:
    """Every populated link is evidence and must agree with the account feed."""
    paths = (f"/j/{code}", f"/{account}/j/{code}")
    for key in ("url", "application_url", "shortlink", "link"):
        raw_link = row.get(key)
        if raw_link is None or raw_link == "":
            continue
        parsed = _url_parts(raw_link)
        if (parsed is None or parsed.hostname != host
                or parsed.path not in paths):
            return False
    return True


class WorkableAdapter:
    ats = "workable"

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]:
        account = _board_token(company.board, account=True)
        host = "apply.workable.com"
        base = f"https://{host}/api/v1/widget/accounts/{account}"
        _careers_base(company, account, self.ats, base)
        rows = _json_rows(_feed(client, base), "jobs")

        groups: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = row.get("shortcode")
            if not isinstance(code, str) or not _SEGMENT.fullmatch(code):
                continue

            # The global /j/<shortcode> link carries no tenant identity. The
            # validated account endpoint is the only authority for re-homing it.
            if not _source_links_match(row, host, account, code):
                continue
            candidate = f"https://{host}/{account}/j/{code}"
            link = _posting_url(
                candidate, host,
                rf"/{re.escape(account)}/j/[A-Za-z0-9][A-Za-z0-9_-]{{0,127}}",
                base)
            if link is None:
                continue

            title = _clean(row.get("title"), 512, required=True)
            description = _clean(row.get("description") or "", 20_000)
            raw_locations = _locations(
                row.get("locations") if row.get("locations") is not None
                else row.get("location"))
            cues = _remote_cues(row)
            if (title is None or description is None
                    or _location_text(raw_locations, cues) is None):
                continue
            publication = row.get("published_on")
            group = groups.get(code)
            if group is None:
                groups[code] = {
                    "title": title, "description": description, "publication": publication,
                    "cues": cues, "link": link, "places": raw_locations,
                    "invalid": False,
                }
            elif (group["title"] != title or group["publication"] != publication
                  or group["cues"] != cues):
                group["invalid"] = True
                log.warning("Workable group skipped: conflicting title/publication/remote metadata")
            else:
                # Per-row valid URLs cannot conflict after re-homing. Locations
                # may differ across duplicate rows; retain their first-seen order.
                group["places"].extend(raw_locations)

        jobs: list[JobRecord] = []
        for code, group in groups.items():
            if group["invalid"]:
                continue
            location = _location_text(group["places"], group["cues"])
            if location is None:
                log.warning("Workable group skipped: public location overflow")
                continue
            jobs.append(JobRecord(
                company=company.name, job_id=code, title=group["title"],
                jd_url=group["link"], jd_text=group["description"],
                location=location, ats=self.ats,
                posted_at=_published(group["publication"]),
            ))
        return _nonempty_or_error(rows, jobs)
