"""Operator-invoked, exact-PK ATS identity re-derivation.

This module has no startup hook, crawl scan, application entry or queue effect.
Synthetic callers inject watchlist, feed client and tracking store; an operator
may separately authorize a commit after adoption. Dry-run is the default.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from core.ids import is_internal_pk, make_pk
from core.models import ApplyMode, DiscoveryMode, JobRecord
from core.storage.base import tenant_pin_row_allowed
from .adapters import ADAPTERS
from .watchlist import CompanyConfig, load_watchlist

_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_HOSTED = {"greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
           "ashby": {"jobs.ashbyhq.com"}, "lever": {"jobs.lever.co"}}
_POSTING_MARKERS = {
    "job", "jobs", "position", "positions", "posting", "postings",
    "opening", "openings", "requisition", "requisitions", "role", "roles",
}
_NEXT_PROOF = (
    "Independently verify one company-wide employer careers scope and ATS "
    "board, review and deploy that single watchlist tuple, restart only while "
    "idle, establish this same stored job in a fresh canonical hosted board "
    "feed, then operator-run a new explicit dry-run."
)
_NEXT_MANUAL = (
    "Operator must manually review the original tracking identity/history; "
    "do not rekey, overwrite, approve or automatically requeue."
)


def _parts(url: object):
    """Literal HTTPS origin and unencoded path; no implicit URL normalization."""
    if (not isinstance(url, str) or not url or not url.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\"
                   for c in url)
            or "%" in url):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or not p.hostname
                or p.netloc != p.hostname or p.port != 443 and p.port is not None
                or p.hostname.endswith(".") or not re.fullmatch(
                    r"[a-z0-9.-]+", p.hostname)):
            return None
    except ValueError:
        return None
    if (not p.path.startswith("/") or "//" in p.path
            or any(segment in (".", "..") for segment in p.path.split("/"))
            or not all(re.fullmatch(r"[A-Za-z0-9._~-]*", s)
                       for s in p.path.split("/"))):
        return None
    # A redundant explicit :443 is prohibited by p.netloc != p.hostname.
    return p


def _scope_parts(url: str, careers_url: str):
    scope, p = _parts(careers_url), _parts(url)
    if (scope is None or p is None or scope.query or scope.fragment
            or not scope.path.strip("/") or scope.path.endswith("/")
            or p.netloc != scope.netloc):
        return None
    prefix = scope.path + "/"
    if not p.path.startswith(prefix) or p.path == prefix:
        return None
    return p


def greenhouse_query_id(url: str, careers_url: str) -> str | None:
    """Exact scoped ?gh_jid identity: bare-host root or Waymo listing included."""
    if isinstance(careers_url, str) and re.fullmatch(
            r"https://[a-z0-9.-]+", careers_url):
        source, p = _parts(careers_url + "/"), _parts(url)
        if (source is None or p is None or p.netloc != source.netloc
                or p.path != "/"):
            return None
    else:
        p = _scope_parts(url, careers_url)
        if p is None and careers_url == "https://careers.withwaymo.com/jobs":
            # This reviewed feed links to the listing page itself, not a child.
            source, target = _parts(careers_url), _parts(url)
            if (source is not None and target is not None
                    and target.netloc == source.netloc
                    and target.path == source.path):
                p = target
    if p is None or p.fragment or not re.fullmatch(r"gh_jid=([0-9]+)", p.query):
        return None
    return p.query.split("=", 1)[1]


def greenhouse_suffix_id(url: str, careers_url: str) -> str | None:
    """Only a numeric terminal segment directly after a posting-path marker."""
    p = _scope_parts(url, careers_url)
    if p is None or p.query or p.fragment or p.path.endswith("/"):
        return None
    parts = p.path.split("/")
    if (len(parts) < 3 or parts[-2].lower() not in _POSTING_MARKERS
            or not _NUMBER.fullmatch(parts[-1])):
        return None
    return parts[-1]


def greenhouse_feed_identity(job: JobRecord, config: CompanyConfig) -> bool:
    """Literal hosted, query-wrapper or same-host numeric terminal feed URL.

    A feed's employer apply path can differ from the careers listing path.
    This shape alone grants nothing: the one exact board-feed absolute_url,
    job ID and reviewed tuple must separately agree.
    """
    jid = job.job_id
    if (config.ats != "greenhouse" or not isinstance(jid, str)
            or not _NUMBER.fullmatch(jid)):
        return False
    if canonical_hosted(job, "greenhouse", config.board, jid):
        return True
    if greenhouse_query_id(job.jd_url, config.careers_url) == jid:
        return True
    bare = (isinstance(config.careers_url, str) and
            re.fullmatch(r"https://[a-z0-9.-]+", config.careers_url))
    source = _parts(config.careers_url + "/" if bare else config.careers_url)
    target = _parts(job.jd_url)
    return bool(
        source and target and source.netloc == target.netloc
        and not target.query and not target.fragment
        and re.fullmatch(r"/[A-Za-z0-9._~/-]+/" + re.escape(jid),
                         target.path)
    )


def canonical_hosted(job: JobRecord, ats: str, board: str, job_id: str) -> bool:
    """Exact hosted feed target, never a wrapper, redirect, guess or board probe."""
    if not isinstance(board, str) or _BOARD.fullmatch(board) is None:
        return False
    if ats == "greenhouse":
        if _NUMBER.fullmatch(job_id) is None:
            return False
        suffix = f"/{board}/jobs/{job_id}"
    elif ats in ("ashby", "lever"):
        if _UUID.fullmatch(job_id) is None:
            return False
        suffix = f"/{board}/{job_id}"
    else:
        return False
    p = _parts(job.jd_url)
    return bool(
        p and p.netloc in _HOSTED[ats] and p.path == suffix
        and not p.query and not p.fragment
    )


def reviewed_tuple(name: str, *, path: str | Path | None = None,
                   expected: CompanyConfig | None = None,
                   crawl_only: bool = False) -> CompanyConfig | None:
    """One explicitly deployed, reviewed tuple; parsed defaults are not proof."""
    from core.config import get_settings

    path = Path(path) if path is not None else (
        Path(get_settings().config_dir) / "watchlist.yaml"
    )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        entries = raw.get("companies", [])
        parsed = load_watchlist(path)
        if not isinstance(entries, list) or len(entries) != len(parsed):
            return None
        matches = [(entry, cfg) for entry, cfg in zip(entries, parsed)
                   if isinstance(entry, dict)
                   and str(entry.get("name", "")).lower() == name.lower()]
        if len(matches) != 1:
            return None
        entry, cfg = matches[0]
        required = {"name", "careers_url", "ats", "board", "discovery", "mode"}
        bare_gh = (cfg.ats == "greenhouse"
                   and isinstance(cfg.careers_url, str)
                   and re.fullmatch(r"https://[a-z0-9.-]+", cfg.careers_url))
        source_url = cfg.careers_url + "/" if bare_gh else cfg.careers_url
        if (not required.issubset(entry)
                or cfg.name != name or cfg.mode is not ApplyMode.GATED
                or cfg.ats not in _HOSTED
                or not isinstance(cfg.board, str)
                or _BOARD.fullmatch(cfg.board) is None
                or _parts(source_url) is None
                or urlsplit(cfg.careers_url).query
                or urlsplit(cfg.careers_url).fragment
                or (not bare_gh and (
                    not urlsplit(cfg.careers_url).path.strip("/")
                    or urlsplit(cfg.careers_url).path.endswith("/")))
                or str(entry["discovery"]) != cfg.discovery.value
                or str(entry["mode"]) != cfg.mode.value
                or any(entry.get(k) != getattr(cfg, k)
                       for k in ("name", "careers_url", "ats", "board"))
                or (crawl_only and cfg.discovery is not DiscoveryMode.CRAWL)):
            return None
        if expected is not None and any(
                getattr(cfg, key) != getattr(expected, key)
                for key in ("name", "careers_url", "ats", "board", "mode", "discovery")):
            return None
        return cfg
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        return None


def exact_feed_match(config: CompanyConfig, job_id: str,
                     rows: list[JobRecord], *,
                     stored_url: str | None = None) -> JobRecord | None:
    """One current ID row with exact unchanged feed absolute_url."""
    if not isinstance(rows, list):
        return None
    matches = [job for job in rows if isinstance(job, JobRecord)
               and job.job_id == job_id]
    if len(matches) != 1:
        return None
    job = matches[0]
    route = (greenhouse_feed_identity(job, config) if config.ats == "greenhouse"
             else canonical_hosted(job, config.ats, config.board, job_id))
    return job if (
        job.ats == config.ats and job.company == config.name
        and route and (stored_url is None or stored_url == job.jd_url)
    ) else None


def current_feed(config: CompanyConfig, *,
                 client: httpx.Client | None = None) -> list[JobRecord]:
    """Existing adapter only; tests inject an offline HTTP client."""
    adapter = ADAPTERS[config.ats]
    own = client is None
    client = client or httpx.Client()
    try:
        result = adapter.fetch(config, client)
        if not isinstance(result, list):
            raise ValueError("malformed board feed")
        return result
    finally:
        if own:
            client.close()


def rederive_one(
    pk: str, *, commit: bool = False, operator_authorized: bool = False,
    tracking=None, watchlist_path: str | Path | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """One exact original stored key. Never scans rows or queues applications."""
    from core.stores import make_stores

    result = {"pk": pk, "committed": False, "would_pin": False,
              "next_action": "", "status": ""}
    if commit and not operator_authorized:
        return {**result, "status": "authorization_required",
                "next_action": "Only the operator may separately authorize the commit."}
    if not isinstance(pk, str) or not pk or is_internal_pk(pk):
        return {**result, "status": "invalid_pk", "next_action": _NEXT_MANUAL}
    store = tracking if tracking is not None else make_stores().tracking
    original = deepcopy(store.get(pk))
    if not isinstance(original, dict):
        return {**result, "status": "missing_row", "next_action": _NEXT_MANUAL}
    if (original.get("pk") != pk or not isinstance(original.get("company"), str)
            or not isinstance(original.get("job_id"), str)
            or make_pk(original["company"], original["job_id"]) != pk):
        return {**result, "status": "identity_conflict", "next_action": _NEXT_MANUAL}
    cfg = reviewed_tuple(original["company"], path=watchlist_path)
    if cfg is None:
        return {**result, "status": "unproved_tuple", "next_action": _NEXT_PROOF}
    ats, job_id, url = original.get("ats"), original["job_id"], original.get("jd_url")
    if ats != cfg.ats or ats not in _HOSTED:
        return {**result, "status": "ats_conflict", "next_action": _NEXT_MANUAL}
    if ats == "greenhouse":
        candidate = JobRecord(company=cfg.name, job_id=job_id, title="",
                              jd_url=str(url), jd_text="", ats=ats)
        if not greenhouse_feed_identity(candidate, cfg):
            return {**result, "status": "noncanonical_wrapper", "next_action": _NEXT_MANUAL}
    elif not canonical_hosted(
            JobRecord(company=cfg.name, job_id=job_id, title="",
                      jd_url=str(url), jd_text="", ats=ats),
            ats, cfg.board, job_id):
        return {**result, "status": "noncanonical_identity", "next_action": _NEXT_MANUAL}
    try:
        matches = current_feed(cfg, client=client)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {**result, "status": "feed_unavailable",
                "next_action": _NEXT_PROOF, "detail": type(exc).__name__}
    feed = exact_feed_match(cfg, job_id, matches,
                            stored_url=url if ats == "greenhouse" else None)
    if feed is None:
        return {**result, "status": "feed_not_proved", "next_action": _NEXT_PROOF}
    result["evidence"] = {"ats": ats, "board": cfg.board,
                          "hosted_target": feed.jd_url,
                          "original_url": url, "feed_job_id": job_id}
    if ats in ("ashby", "lever"):
        return {**result, "status": "verified_read_only",
                "next_action": "No mutation is required; preserve this canonical row."}
    if "greenhouse_board" in original:
        if original["greenhouse_board"] == cfg.board:
            return {**result, "status": "already_pinned",
                    "next_action": "No write; the original board pin is unchanged."}
        return {**result, "status": "conflicting_pin", "next_action": _NEXT_MANUAL}
    if not tenant_pin_row_allowed(original, pk, cfg.board):
        return {**result, "status": "unsafe_history", "next_action": _NEXT_MANUAL}
    if not commit:
        return {**result, "status": "dry_run_eligible", "would_pin": True,
                "next_action": (
                    "After adoption, a separately authorized operator may re-check "
                    "the same key and commit; tenant evidence is not human approval.")}
    changed = store.add_missing_greenhouse_pin(
        pk, cfg.board, expected_row=original,
    )
    if not changed:
        return {**result, "status": "commit_conflict", "would_pin": True,
                "next_action": "Re-observe the original row and hold; run a new dry-run."}
    # A dedicated strongly consistent alias read exists for DynamoDB. Ordinary
    # get stays unchanged in that backend.
    reader = getattr(store, "get_alias", store.get)
    after = reader(pk)
    if not isinstance(after, dict) or after.get("greenhouse_board") != cfg.board:
        return {**result, "status": "readback_unconfirmed",
                "next_action": "Operator must verify current row before further action."}
    return {**result, "status": "committed", "committed": True,
            "next_action": (
                "Pin evidence is immediately relevant to existing query-form "
                "tenant admission, not approval. No automatic requeue or submit.")}

