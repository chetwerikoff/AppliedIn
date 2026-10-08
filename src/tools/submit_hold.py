"""Independent submission evidence: stale tracking-row SETs cannot erase it.

The key survives worker/daemon death, not loss of the Redis dataset. Callers
clear only after recording a resolved outcome or proving refusal before IPC;
unresolved uncertainty always retains the key for explicit human reconciliation.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

REASON = "possible submission; check the employer portal before retrying"


def _client(tracking=None):
    if tracking is None:
        from core.stores import make_stores
        tracking = make_stores().tracking
    client = getattr(tracking, "r", None)
    if client is None:
        raise RuntimeError("Submission hold requires the tracking Redis client; human inspection required")
    return client


def mark(pk: str, *, tracking=None) -> None:
    if not pk:
        raise ValueError("A tracked application is required for a submission hold")
    if not _client(tracking).set(f"submit_hold:{pk}", json.dumps({
            "timestamp": datetime.now(UTC).isoformat(), "reason": REASON})):
        raise RuntimeError("Submission hold write failed; no click is safe")


def is_held(pk: str, *, tracking=None) -> bool:
    return bool(_client(tracking).get(f"submit_hold:{pk}"))


def clear(pk: str, *, tracking=None) -> None:
    """Only resolved recorded outcomes or verified pre-click refusals authorize this."""
    _client(tracking).delete(f"submit_hold:{pk}")


def blocked(pk: str, row: dict, *, tracking=None) -> bool:
    """One replay decision for dispatch, recovery, queue and manual entry points.

    Legacy row evidence remains conservative during adoption. Cloud has no Redis
    hold support; the BrowserSkill pre-click mark therefore gates there.
    """
    if row.get("possible_submission") or row.get("fail_kind") == "uncertain" or row.get("gate_reason") == "submit_uncertain":
        return True
    if tracking is None:
        from core.stores import make_stores
        tracking = make_stores().tracking
    if getattr(tracking, "r", None) is None:
        return False
    try:
        return is_held(pk, tracking=tracking)
    except Exception:
        return True  # unreadable evidence never authorizes replay


def any_held(*, tracking=None) -> bool:
    return next(iter(_client(tracking).scan_iter(match="submit_hold:*")), None) is not None
