"""ATS feed adapters and their shared untrusted-input boundary.

All new feed providers use the same strict outbound-request and posting-authority
guards. Keep these enforced in Python: an agent prompt cannot validate a URL.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
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


class ATSAdapter(Protocol):
    ats: str

    def fetch(self, company: CompanyConfig, client: httpx.Client) -> list[JobRecord]: ...


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
    if checked is None or checked.hostname != host or not re.fullmatch(path_pattern, checked.path):
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


from .ashby import AshbyAdapter  # noqa: E402
from .greenhouse import GreenhouseAdapter  # noqa: E402
from .lever import LeverAdapter  # noqa: E402
from .personio import PersonioAdapter  # noqa: E402
from .recruitee import RecruiteeAdapter  # noqa: E402
from .smartrecruiters import SmartRecruitersAdapter  # noqa: E402
from .teamtailor import TeamtailorAdapter  # noqa: E402
from .workable import WorkableAdapter  # noqa: E402
from .workday import WorkdayAdapter  # noqa: E402

ADAPTERS: dict[str, ATSAdapter] = {
    a.ats: a
    for a in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        SmartRecruitersAdapter(),
        WorkdayAdapter(),
        PersonioAdapter(),
        TeamtailorAdapter(),
        RecruiteeAdapter(),
        WorkableAdapter(),
    )
}

__all__ = ["ATSAdapter", "ADAPTERS"]
