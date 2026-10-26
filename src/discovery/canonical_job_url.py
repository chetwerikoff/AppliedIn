"""Literal ATS posting admission and one-PK read-only-by-default diagnosis.

Discovery URLs are untrusted until both the original employer/board binding
and the *unchanged* application's starting-route consumer accept them. This
module never broadens final Submit authority or runs an application.
"""
from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit

import httpx

from core.ids import is_internal_pk, make_pk
from core.models import JobRecord
from .watchlist import CompanyConfig

_HOSTS = {
    "ashby": frozenset({"jobs.ashbyhq.com"}),
    "lever": frozenset({"jobs.lever.co"}),
    "greenhouse": frozenset({"job-boards.greenhouse.io", "boards.greenhouse.io"}),
}
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_BOARD_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_CLAIM_KEY = re.compile(r"(?i)(?:^|[&;=])gh_jid(?:=|[&;]|$)")
_SUFFIX = re.compile(
    r"/(?:job|jobs|position|positions|posting|postings|opening|openings|"
    r"requisition|requisitions|role|roles)/([0-9]+)\Z", re.I
)
_NEXT = ("Operator: independently verify the employer careers source, exact "
         "ATS board and same job in its current feed; manually assist if the "
         "unchanged application-entry guard cannot admit the stored route.")
_NEXT_HISTORY = ("Operator: review the original tracking identity and original "
                 "posting source; never infer history from a later watchlist or feed.")


def _literal(url: object):
    """No URL parser rewriting, defaults, encoded authority or path traversal."""
    if (not isinstance(url, str) or not url or not url.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\"
                   for c in url)
            or "%" in url):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or not p.hostname or p.netloc != p.hostname
                or p.hostname.endswith(".") or p.query or p.fragment
                or "//" in p.path or not p.path.startswith("/")
                or any(seg in (".", "..") for seg in p.path.split("/"))):
            return None
    except ValueError:
        return None
    return p


def claim_greenhouse_query(url: object) -> bool:
    """Detect literal, layered or still-escaping tenant-key attempts.

    Decode at most three rounds. An unresolved depth is ambiguous and must
    not bypass the pre-browser/producer boundary as ordinary custom crawl.
    """
    if not isinstance(url, str) or "?" not in url:
        return False
    query = url.partition("?")[2].partition("#")[0]
    for _ in range(3):
        if _CLAIM_KEY.search(query):
            return True
        nxt = unquote_plus(query)
        if nxt == query:
            return False
        query = nxt
    return bool(_CLAIM_KEY.search(query) or unquote_plus(query) != query)


def literal_gh_id(url: object) -> str | None:
    p = _literal(url)
    if p is not None:
        # Query forms deliberately cannot pass _literal, which rejects queries.
        return None
    if not isinstance(url, str) or "?" not in url:
        return None
    origin, _, query = url.partition("?")
    parts = _literal(origin)
    if parts is None or "#" in query or not re.fullmatch(r"gh_jid=([0-9]+)", query):
        return None
    return query.split("=", 1)[1]


def canonical_shape(job: JobRecord, board: str, ats: str) -> bool:
    """Literal exact same-ID hosted *candidate*, not proof of tenant ownership."""
    if ats not in _HOSTS or not isinstance(board, str) or not _BOARD_PATH.fullmatch(board):
        return False
    jid = job.job_id
    if (not isinstance(jid, str) or
            not (_NUMBER.fullmatch(jid) if ats == "greenhouse" else _UUID.fullmatch(jid))):
        return False
    p = _literal(job.jd_url)
    if p is None or p.netloc not in _HOSTS[ats]:
        return False
    tail = f"/{board}/jobs/{jid}" if ats == "greenhouse" else f"/{board}/{jid}"
    return p.path == tail


def source_bound(company: CompanyConfig, raw: CompanyConfig) -> bool:
    """Native hosted board source OR independently deployed explicit tuple.

    An effective resolver board and a successful arbitrary feed probe alone
    never prove which employer selected that board.
    """
    if (raw.name != company.name or not raw.careers_url
            or raw.ats not in ("", company.ats)
            or (raw.board and raw.board != company.board)):
        return False
    p = _literal(raw.careers_url.rstrip("/"))
    if p and company.ats in _HOSTS and p.netloc in _HOSTS[company.ats]:
        return p.path == f"/{company.board}" and bool(company.board)
    # The deployed raw tuple must contain explicit ats, board, mode and source.
    from .tenant_pin import reviewed_tuple
    return reviewed_tuple(company.name, expected=company) is not None


def consumer_accepts(job: JobRecord, company: CompanyConfig) -> bool:
    """Call the existing pure entry guard; do not reimplement its token rule."""
    if job.ats != company.ats or job.company != company.name:
        return False
    if company.ats not in ("ashby", "lever"):
        # Greenhouse canonical hosted routes are not accepted by the existing
        # wrapper/pin-based _configured_alias -> verified_greenhouse_alias.
        return False
    from tools.browser_skill_apply import _application_entry
    row = {"pk": job.pk, "company": job.company, "ats": job.ats,
           "job_id": job.job_id, "jd_url": job.jd_url}
    return _application_entry(row, job.pk, company.name, job.jd_url, [company]) is not None


def candidate_result(job: JobRecord, company: CompanyConfig, *, bound: bool,
                     unique: bool) -> tuple[JobRecord | None, str]:
    if not bound:
        return None, "employer_board_source_unproved"
    if not unique:
        return None, "ambiguous_same_job_feed_identity"
    if (job.company != company.name or job.ats != company.ats
            or job.pk != make_pk(company.name, job.job_id)):
        return None, "feed_identity_conflict"
    if not canonical_shape(job, company.board, company.ats):
        return None, "nonliteral_or_noncanonical_ats_target"
    if not consumer_accepts(job, company):
        return None, "canonical_target_not_supported_by_current_guard"
    return job, ""


def admit_feed(company: CompanyConfig, raw: CompanyConfig,
               fetched: list[JobRecord]) -> tuple[list[JobRecord], list[dict]]:
    """Filter *before* relevance, seen, put_new and queue effects."""
    if company.ats not in _HOSTS:
        return fetched, []
    bound = source_bound(company, raw)
    ids = Counter(j.job_id for j in fetched if isinstance(j, JobRecord))
    approved, refused = [], []
    for job in fetched:
        accepted, reason = candidate_result(
            job, company, bound=bound, unique=ids[job.job_id] == 1)
        if reason:
            refused.append({"pk": job.pk, "status": reason, "ats": company.ats,
                            "next_action": _NEXT})
        else:
            approved.append(accepted)
    return approved, refused


def employer_gh_claim(company: CompanyConfig, job: JobRecord) -> bool:
    """Classify ATS claims observed *within* this employer discovery listing.

    A listing link to a foreign host with a gh_jid marker is still a claimed
    ATS identity; foreign-origin failure is no excuse to queue it as custom.
    Numeric path suffix recognition remains limited to the original source.
    """
    if claim_greenhouse_query(job.jd_url):
        return True
    try:
        c, p = urlsplit(company.careers_url), urlsplit(job.jd_url)
        same_source = (p.scheme in ("http", "https") and
                       p.hostname and p.hostname == c.hostname)
    except ValueError:
        return False
    return bool(same_source and company.ats == "greenhouse"
                and job.discovery_source == "sitemap_greenhouse_suffix"
                and _SUFFIX.search(p.path) is not None)


def diagnostic(job: JobRecord, reason: str) -> dict:
    return {"pk": job.pk, "status": reason, "next_action": _NEXT}


def repair_one(pk: str, *, commit: bool = False, operator_authorized: bool = False,
               tracking=None, watchlist_path: str | Path | None = None,
               client: httpx.Client | None = None) -> dict:
    """Diagnose exactly one retained PK. No present class warrants a GH rewrite.

    In particular a current matching feed cannot reconstruct an original
    relative posting base or grant new final-Submit authority.
    """
    from core.stores import make_stores
    from .tenant_pin import reviewed_tuple, current_feed, exact_feed_match

    out = {"pk": pk, "committed": False, "status": "", "next_action": _NEXT_HISTORY,
           "guard_compatible": False}
    if commit and not operator_authorized:
        return {**out, "status": "authorization_required"}
    if not isinstance(pk, str) or not pk or is_internal_pk(pk):
        return {**out, "status": "invalid_pk"}
    store = tracking if tracking is not None else make_stores().tracking
    row = deepcopy(store.get(pk))
    if not isinstance(row, dict):
        return {**out, "status": "missing_row"}
    out.update({"company": row.get("company"), "ats": row.get("ats"),
                "old_url": row.get("jd_url"),
                "retained_board": row.get("greenhouse_board"),
                "retained_source": row.get("discovery_source")})
    co, jid, url, ats = (row.get("company"), row.get("job_id"),
                         row.get("jd_url"), row.get("ats"))
    if (not isinstance(co, str) or not isinstance(jid, str)
            or not isinstance(url, str) or row.get("pk") != pk
            or make_pk(co, jid) != pk):
        return {**out, "status": "identity_conflict"}
    if not url.startswith(("https://", "http://")):
        return {**out, "status": "historic_original_posting_base_unavailable"}
    cfg = reviewed_tuple(co, path=watchlist_path)
    if cfg is None or cfg.ats != ats:
        return {**out, "status": "unproved_original_source"}
    if ats == "greenhouse":
        from .tenant_pin import greenhouse_suffix_id
        pin = row.get("greenhouse_board")
        if not isinstance(pin, str) or not pin or pin != cfg.board:
            return {**out, "status": "missing_or_conflicting_original_pin"}
        # Do not accept a raw gh_jid at *any* employer origin/path merely
        # because a later feed has the same numeric ID. Bound recognition
        # strictly to the retained literal old URL and reviewed source scope.
        current_source = _literal(cfg.careers_url.rstrip("/"))
        old_parts = _literal(url.partition("?")[0])
        scoped_query = bool(
            current_source and old_parts
            and old_parts.netloc == current_source.netloc
            and (old_parts.path == current_source.path
                 or old_parts.path.startswith(current_source.path.rstrip("/") + "/")))
        if literal_gh_id(url) == jid and scoped_query:
            out["old_evidence_class"] = "R1_literal_gh_jid"
        elif (row.get("discovery_source") == "sitemap_greenhouse_suffix"
              and greenhouse_suffix_id(url, cfg.careers_url) == jid):
            out["old_evidence_class"] = "R2_retained_sitemap_suffix"
        else:
            return {**out, "status": "historic_identity_unproved"}
    elif ats not in ("ashby", "lever"):
        return {**out, "status": "unsupported_old_class"}
    try:
        rows = current_feed(cfg, client=client)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {**out, "status": "feed_unavailable", "error_type": type(exc).__name__}
    proof = exact_feed_match(cfg, jid, rows)
    if proof is None:
        return {**out, "status": "current_feed_identity_unproved"}
    out["candidate_url"] = proof.jd_url
    out["current_feed_board"] = cfg.board
    out["guard_compatible"] = consumer_accepts(proof, cfg)
    if not out["guard_compatible"]:
        return {**out, "status": "canonical_target_not_supported_by_current_guard",
                "next_action": _NEXT}
    if url == proof.jd_url:
        return {**out, "status": "already_canonical_noop",
                "next_action": "No URL change is necessary."}
    # A source-backed, actually retained noncanonical Ashby/Lever old class
    # is not present in the existing schema. Do not invent a positive repair.
    return {**out, "status": "historic_identity_unproved",
            "next_action": _NEXT_HISTORY}
