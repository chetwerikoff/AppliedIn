"""Operator-local admission of one agent-found startup into the funded state.

Writes the same ``pending`` + ``deals`` pair the funded source writes for a
proved no-opening employer, so the unchanged ``read_no_opening`` accepts it.
It never enqueues, never sends mail and never touches caps, dedupe or
suppression.

Operator prerequisite (not enforced by a lock): stop the daemon/poller and any
other funded-state writer before invoking, and keep them stopped until exit.

    python -m core.agent_funded_admit --name NAME --homepage https://example.com/ \\
        --contact-url https://example.com/contact --note "no openings on careers page"
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import httpx

from discovery.funding import (
    FundingRefusal,
    GuardedClient,
    State,
    _article,
    _key,
    _preview_verified,
    _safe_url,
    _utc,
    registrable,
)

SOURCE_ID = "agent_funded_admit"
_KEY_NAMESPACE = "agent_funded_admit:v1"
_NEGATIVE_REASONS = frozenset({"no_openings", "no_relevant_openings"})
_BUCKETS = ("pending", "deals", "active")

_NEXT = {
    "mode_or_gate_off": "Run in local mode with funding and outreach enabled.",
    "invalid_input": "Fix the input and run again.",
    "unsafe_state_file": "Replace the symlink or non-regular funded state file.",
    "company_key_occupied": "The company is already tracked; use the existing row or leave it.",
    "identity_collision": "Another pending or active employer uses this domain, name or alias.",
    "homepage_domain_mismatch": "Give the homepage the company owns, not a redirecting alias.",
    "relevant_opening_found": "A matching opening exists; apply through the normal route.",
    "state_changed_during_admission": "Another writer changed the state. Stop writers; rerun.",
    "state_write_failed": "Check disk space and permissions of the local dir, then rerun.",
}
_DEFAULT_NEXT = ("The employer or native board could not be proved. Do not admit by hand; "
                 "add the company to the manual watchlist or drop it.")


def _refuse(reason: str, detail: str = "") -> dict:
    out = {"admitted": False, "reason": reason,
           "next_action": _NEXT.get(reason, _DEFAULT_NEXT)}
    if detail:
        out["detail"] = detail
    return out


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def _collision(data: dict, key: str, domain: str, names: set[str]) -> str | None:
    """Reader semantics: registrable domain or normalized name/alias overlap."""
    if any(key in data[bucket] for bucket in _BUCKETS):
        return "company_key_occupied"
    for bucket in ("pending", "active"):
        for other in data[bucket].values():
            theirs = {_norm(other.get("name"))} | {
                _norm(a) for a in other.get("aliases", []) if isinstance(a, str)}
            if other.get("employer_domain") == domain or not names.isdisjoint(theirs):
                return "identity_collision"
    return None


def admit(name: str, homepage: str, contact_url: str, note: str, *, aliases=(),
          settings=None, now=None, client_factory=None, relevance_fn=None) -> dict:
    """Return ``{"admitted": True, "company_id": ...}`` or a refusal dict."""
    if settings is None:
        from core.config import get_settings
        settings = get_settings()
    if (getattr(settings, "mode", None) != "local"
            or not getattr(settings, "funding_enabled", False)
            or not getattr(settings, "outreach_enabled", False)):
        return _refuse("mode_or_gate_off")

    aliases = list(aliases)
    if (not isinstance(name, str) or not 2 <= len(name.strip()) <= 150
            or len(aliases) > 8
            or any(not isinstance(a, str) or not 2 <= len(a.strip()) <= 150
                   for a in aliases)
            or not isinstance(note, str) or not note.strip() or len(note) > 2000):
        return _refuse("invalid_input", "name, aliases or note out of bounds")
    name = name.strip()
    aliases = list(dict.fromkeys(
        a.strip() for a in aliases if _norm(a) != _norm(name)))
    try:
        _safe_url(homepage)
        _safe_url(contact_url)  # an input only; never evidence and never stored
    except FundingRefusal:
        return _refuse("invalid_input", "homepage and contact URL must be safe https URLs")
    domain = registrable(homepage)
    if not domain:
        return _refuse("invalid_input", "homepage has no registrable domain")

    path = Path(settings.local_dir) / "funded-watchlist.yaml"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return _refuse("unsafe_state_file")
    now = _utc(now)
    company_id = _key(_KEY_NAMESPACE, domain)
    names = {_norm(name)} | {_norm(a) for a in aliases}
    try:
        state = State(path)
        reason = _collision(state.data, company_id, domain, names)
        if reason:
            return _refuse(reason)
        before = {b: copy.deepcopy(state.data[b]) for b in _BUCKETS}
        client = (client_factory(state, settings, now) if client_factory
                  else GuardedClient(state=state, settings=settings, now=now))

        page = client.get(homepage, follow_redirects=True)
        page.raise_for_status()
        final_domain = registrable(str(page.request.url))
        if not final_domain or final_domain != domain:
            return _refuse("homepage_domain_mismatch")
        text = _article(page.text).text[:10000]
        for alias in aliases:
            if not re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I):
                return _refuse("alias_unverified", alias)

        reason, _ = _preview_verified(
            name, homepage, SOURCE_ID, state, settings, client, now,
            relevance_fn=relevance_fn, employer_page=page, aliases=aliases,
            complete_negative=True)
        if reason == "admissible":
            return _refuse("relevant_opening_found")
        if reason not in _NEGATIVE_REASONS:
            return _refuse(reason)

        # Last check before the one atomic write: no interleaved writer, no new
        # collision. Proof accounting already persisted stays as it is.
        fresh = State(path)
        if any(fresh.data[b] != before[b] for b in _BUCKETS):
            return _refuse("state_changed_during_admission")
        reason2 = _collision(state.data, company_id, domain, names)
        if reason2:
            return _refuse(reason2)

        seen = now.isoformat()
        proposed = copy.deepcopy(state.data)
        proposed["pending"][company_id] = {
            "name": name, "homepage": homepage, "employer_domain": domain,
            "source": SOURCE_ID, "aliases": aliases, "reason": reason,
            "seen_at": seen,
            "next_check": (now + timedelta(days=settings.funding_recheck_days)).isoformat(),
            "expires": (now + timedelta(days=settings.funding_pending_days)).isoformat(),
            "last_successful_no_opening_recheck_at": seen,
        }
        proposed["deals"][company_id] = {
            "status": "pending", "reason": reason, "source": SOURCE_ID, "seen_at": seen}
        try:
            state.checkpoint(proposed)
        except OSError as exc:
            return _refuse("state_write_failed", type(exc).__name__)
    except FundingRefusal as exc:
        return _refuse(str(exc))
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _refuse("proof_failed", type(exc).__name__)
    return {"admitted": True, "company_id": company_id, "reason": reason}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.agent_funded_admit",
        description="Admit one agent-found startup into the funded state (operator-local).")
    ap.add_argument("--name", required=True)
    ap.add_argument("--homepage", required=True)
    ap.add_argument("--contact-url", required=True)
    ap.add_argument("--note", required=True, help="agent's no-opening note; not evidence")
    ap.add_argument("--alias", action="append", default=[],
                    help="optional alias; must appear on the homepage")
    args = ap.parse_args(argv)
    result = admit(args.name, args.homepage, args.contact_url, args.note,
                   aliases=args.alias)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["admitted"] else 1


if __name__ == "__main__":
    sys.exit(main())
