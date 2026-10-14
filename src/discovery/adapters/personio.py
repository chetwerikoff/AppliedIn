"""Personio native XML jobs. The same incremental XML safety reader serves RSS."""

from __future__ import annotations

import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import SplitResult, urlsplit

import httpx

from core.models import JobRecord

from ..watchlist import CompanyConfig


_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_ACCOUNT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_NUMERIC_ID = re.compile(r"[1-9][0-9]{0,127}\Z")
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./~-]*\Z")
_XML_MEDIA = {"application/xml", "text/xml", "application/rss+xml"}
_JSON_MEDIA = {"application/json", "text/json"}


def _board_token(value: str, *, account: bool = False) -> str:
    """Reject manual override injection before URL construction and HTTP I/O."""
    rule = _ACCOUNT if account else _DNS_LABEL
    if not isinstance(value, str) or not rule.fullmatch(value):
        raise ValueError("invalid ATS board/account token")
    return value if account else value.lower()


def _url_parts(value: object) -> SplitResult | None:
    """Parse without allowing urlsplit to erase traversal, controls or delimiters."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    # Posting grammars are ASCII. Reject percent escapes outright rather than
    # decoding paths after a normalizer has already erased dangerous segments.
    if any(c in value for c in ("%", "\\", "#", "?")):
        return None
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value):
        return None
    try:
        p = urlsplit(value)
        host = p.hostname
        port = p.port
        if (p.scheme != "https" or not p.netloc or not p.netloc.isascii()
                or p.username is not None or p.password is not None
                or not host or port not in (None, 443)
                or p.query or p.fragment or not _SAFE_PATH.fullmatch(p.path or "/")
                or "//" in p.path or any(part in (".", "..") for part in p.path.split("/"))):
            return None
        if not all(_DNS_LABEL.fullmatch(label) for label in host.split(".")):
            return None
    except ValueError:
        return None
    return p


def _careers_base(company: CompanyConfig, board: str, ats: str,
                  fallback: str) -> str:
    """Accept unrelated *safe* seeds, never a conflicting vendor tenant."""
    value = company.careers_url
    if not value:
        return fallback
    p = _url_parts(value)
    if p is None:
        raise ValueError("unsafe careers URL")
    host = p.hostname or ""
    if ats == "personio":
        vendor = host.endswith(".jobs.personio.de") or host.endswith(".jobs.personio.com")
        allowed = p.path in ("", "/", "/xml") or bool(
            re.fullmatch(r"/job/[1-9][0-9]{0,127}", p.path))
        matching = host in (f"{board}.jobs.personio.de", f"{board}.jobs.personio.com")
    elif ats == "teamtailor":
        vendor = host.endswith(".teamtailor.com")
        allowed = p.path in ("", "/", "/jobs", "/jobs.rss") or bool(
            re.fullmatch(r"/jobs/[A-Za-z0-9][A-Za-z0-9_-]{0,127}", p.path))
        matching = host == f"{board}.teamtailor.com"
    elif ats == "recruitee":
        vendor = host.endswith(".recruitee.com")
        allowed = p.path in ("", "/", "/careers", "/careers/",
                             "/api/offers", "/api/offers/") or bool(
            re.fullmatch(r"/o/[A-Za-z0-9][A-Za-z0-9_-]{0,127}", p.path))
        matching = host == f"{board}.recruitee.com"
    else:
        vendor = host == "apply.workable.com"
        allowed = p.path in (f"/{board}", f"/{board}/",
                             f"/api/v1/widget/accounts/{board}") or bool(
            re.fullmatch(rf"/{re.escape(board)}/j/[A-Za-z0-9][A-Za-z0-9_-]{{0,127}}", p.path))
        matching = allowed
    if vendor and (not matching or not allowed):
        raise ValueError("conflicting or unsupported ATS careers URL")
    return fallback


def _posting_url(candidate: object, host: str, path_pattern: str, base: str) -> str | None:
    """Raw check, existing normalizer, independent normalized destination guard."""
    raw = _url_parts(candidate)
    if raw is None or raw.hostname != host or not re.fullmatch(path_pattern, raw.path):
        return None
    from ..crawler import _trusted_posting_url

    result = _trusted_posting_url(candidate, base)
    checked = _url_parts(result)
    if (checked is None or checked.hostname != host or checked.path != raw.path
            or not re.fullmatch(path_pattern, checked.path)):
        return None
    # Stable canonical identity: DNS case and :443 do not affect the job key;
    # the job-path case is significant and must not be lowercased.
    return f"https://{host}{checked.path}"


def _feed(client: httpx.Client, url: str, *, xml: bool = False) -> httpx.Response:
    response = client.get(url, timeout=20, follow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f"ATS feed returned HTTP {response.status_code}")
    media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media not in (_XML_MEDIA if xml else _JSON_MEDIA) and not (
            not xml and media.endswith("+json") and media.startswith("application/")):
        raise ValueError("ATS feed has missing or unexpected content type")
    return response


def _json_rows(response: httpx.Response, key: str) -> list:
    try:
        doc = response.json()
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid ATS JSON") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get(key), list):
        raise ValueError("unexpected ATS JSON feed shape")
    # A nested extension may be malicious even when individual jobs are valid.
    stack = [(doc, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > 32:
            raise ValueError("ATS JSON depth exceeded")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values()
                         if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node
                         if isinstance(v, (dict, list)))
    rows = doc[key]
    if len(rows) > 2000:
        raise ValueError("ATS feed row count exceeded")
    return rows


def _clean(value: object, limit: int, *, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None if required else ("" if value is None else None)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    text = "".join(" " if unicodedata.category(ch).startswith("C") else ch
                   for ch in value)
    text = " ".join(text.split())
    if len(text) > limit or (required and not text):
        return None
    return text


def _locations(value: object) -> list[str]:
    """Return only disclosed places, never source-hidden geographical details."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_locations(item))
        return out
    if isinstance(value, dict):
        if value.get("hidden") is True:
            return []
        name = value.get("name")
        if isinstance(name, str) and name:
            return [name]
        parts = [value.get(k) for k in ("city", "region", "country")]
        if any(isinstance(x, str) and x for x in parts):
            return [", ".join(x for x in parts if isinstance(x, str) and x)]
        return _locations(value.get("location"))
    return []


def _remote_cues(row: dict) -> tuple[str, ...]:
    cues: list[str] = []
    for key, word in (("remote", "Remote"), ("is_remote", "Remote"),
                      ("hybrid", "Hybrid"), ("telecommuting", "Telecommuting")):
        v = row.get(key)
        if v is True or (isinstance(v, str) and v.lower() == word.lower()):
            if word not in cues:
                cues.append(word)
    for key in ("remote_status", "workplace_type"):
        v = row.get(key)
        if isinstance(v, str) and v.lower() in {"remote", "hybrid", "telecommuting"}:
            word = v.capitalize()
            if word not in cues:
                cues.append(word)
    return tuple(cues)


def _location_text(locations: list[str], cues: tuple[str, ...] = ()) -> str | None:
    parts: list[str] = []
    for value in [*locations, *cues]:
        clean = _clean(value, 2048)
        if clean is None:
            return None
        if clean and clean not in parts:
            parts.append(clean)
    combined = "; ".join(parts)
    return combined if len(combined) <= 2048 else None


def _published(value: object, *, rfc822: bool = False) -> str:
    """Keep only actual time-resolved source publication evidence."""
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip()
    try:
        if rfc822:
            parsed = parsedate_to_datetime(raw)
        elif raw.endswith(" UTC"):
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S UTC").replace(
                tzinfo=timezone.utc)
        else:
            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
                r"(?:Z|[+-]\d{2}:?\d{2})", raw):
                return ""
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo is not None else ""
    except (ValueError, TypeError, OverflowError):
        return ""


def _nonempty_or_error(rows: list, jobs: list[JobRecord]) -> list[JobRecord]:
    if rows and not jobs:
        raise ValueError("nonempty ATS feed had no valid jobs")
    return jobs



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
