"""Personio native XML jobs. The same incremental XML safety reader serves RSS."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx

from core.models import JobRecord

from ..watchlist import CompanyConfig
from . import (_NUMERIC_ID, _board_token, _careers_base, _clean, _feed,
               _location_text, _nonempty_or_error, _posting_url, _url_parts)


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def _child_text(element: ET.Element, tag: str) -> str:
    for child in element:
        if _tag(child) == tag:
            return child.text or ""
    return ""


def _xml_rows(raw: bytes, *, root_name: str, row_name: str,
              stats: dict[str, int]) -> Iterator[ET.Element]:
    """Validate UTF-8/DTD before ElementTree; incrementally bound all XML nodes.

    Do not clear inside a job before its fields have been extracted. Finished
    siblings can be cleared because XML extension metadata is not job data.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff",
                       b"\xff\xfe\x00\x00")):
        raise ValueError("unsupported XML encoding")
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("ATS XML must be UTF-8") from exc
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("unsafe XML DTD, entity or encoding")
    declaration = re.match(r"\s*<\?xml\b(.*?)\?>", text, re.I | re.S)
    if declaration:
        enc = re.search(r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]",
                        declaration.group(1), re.I)
        if enc and enc.group(1).lower() != "utf-8":
            raise ValueError("non-UTF-8 XML declaration")

    stack: list[ET.Element] = []
    seen_channel = False
    starts = 0
    count = 0
    try:
        for event, node in ET.iterparse(io.BytesIO(body), events=("start", "end")):
            if event == "start":
                stack.append(node)
                starts += 1
                if starts > 100_000 or len(stack) > 32:
                    raise ValueError("XML element/depth limit exceeded")
                if len(stack) == 1 and _tag(node) != root_name:
                    raise ValueError("unexpected ATS XML root")
                if (root_name == "rss" and len(stack) == 2
                        and _tag(stack[1]) == "channel"):
                    seen_channel = True
                if _tag(node) == row_name:
                    count += 1
                    if count > 2000:
                        raise ValueError("ATS XML row limit exceeded")
                continue

            parent_path = [_tag(x) for x in stack[:-1]]
            direct_row = (
                _tag(node) == row_name
                and (parent_path == [root_name] if root_name == "workzag-jobs"
                     else parent_path == ["rss", "channel"])
            )
            if direct_row:
                yield node
                node.clear()
            elif row_name not in parent_path:
                node.clear()
            stack.pop()
            # Clear the parent after completed direct children; otherwise many
            # extension siblings remain attached despite their emptied content.
            if len(stack) == 1 and _tag(stack[0]) == root_name:
                stack[0].clear()
            elif (len(stack) == 2 and root_name == "rss"
                  and _tag(stack[1]) == "channel"):
                stack[1].clear()
    except ET.ParseError as exc:
        raise ValueError("malformed ATS XML") from exc

    if root_name == "rss" and not seen_channel:
        raise ValueError("RSS channel is missing")
    stats["rows"] = count


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
