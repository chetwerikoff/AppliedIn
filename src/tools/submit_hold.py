"""Independent submission evidence: stale tracking-row SETs cannot erase it.

The key survives worker/daemon death, not loss of the Redis dataset. Callers
clear only after recording a resolved outcome or proving refusal before IPC;
unresolved uncertainty always retains the key for explicit human reconciliation.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from contextvars import ContextVar

REASON = "possible submission; check the employer portal before retrying"
PRE_SUBMIT = "pre_submit_uncertain"
PRE_SUBMIT_TEXT = "Browser interrupted before any submission action; inspect before a new decision."

# An in-process code-owned witness, never accepted from a row, page or model.
# Only the BrowserSkill controller can create it before returning an uncertain result.
class NeverArmedProof:
    pass


_DIRECT_APPROVAL: ContextVar[str] = ContextVar("appliedin_direct_approval", default="")


def direct_approval() -> str:
    return _DIRECT_APPROVAL.get()


def proof_from_result(result: dict) -> bool:
    return (result.get("_actual_engine") == "browser_skill"
            and type(result.get("_never_armed")) is NeverArmedProof)


def legacy_possible(row: dict) -> bool:
    return (bool(row.get("possible_submission"))
            or row.get("fail_kind") == "uncertain"
            or row.get("gate_reason") == "submit_uncertain"
            or row.get("status") == "uncertain")


def unresolved_pre_submit(row: dict) -> bool:
    return (row.get("status") == "needs_human"
            and (row.get("gate_reason") == PRE_SUBMIT
                 or row.get("fail_kind") == PRE_SUBMIT))


def raw_absent(pk: str, *, tracking=None) -> bool:
    """Only a successful exact GET None, not bool(GET), proves no hold."""
    return _client(tracking).get(f"submit_hold:{pk}") is None


def valid_mark(pk: str, *, tracking=None) -> bool:
    """Verify OUR written hold payload; a present empty/corrupt key is a veto,
    not proof that the mark-before-IPC write was persisted correctly."""
    try:
        raw = _client(tracking).get(f"submit_hold:{pk}")
        marker = json.loads(raw) if raw is not None else {}
        return (isinstance(marker, dict) and marker.get("reason") == REASON
                and isinstance(marker.get("timestamp"), str)
                and bool(marker["timestamp"]))
    except (TypeError, ValueError):
        return False



def possible_state(pk: str, row: dict, *, tracking=None,
                   keyless_cloud: bool = False) -> str:
    """Possible-submission evidence is narrower than replay blocking."""
    if legacy_possible(row):
        return "blocked"
    if keyless_cloud and getattr(tracking, "r", None) is None:
        return "clear"
    try:
        return "clear" if raw_absent(pk, tracking=tracking) else "blocked"
    except Exception:
        return "unknown"


def eligible_pre_submit(pk: str, row: dict, *, tracking=None) -> bool:
    return (row.get("status") == "needs_human"
            and row.get("gate_reason") == PRE_SUBMIT
            and row.get("fail_kind") == PRE_SUBMIT
            and row.get("pre_submit_never_armed") is True
            and row.get("pre_submit_engine") == "browser_skill"
            and not row.get("pre_submit_retry_used")
            and not row.get("confirmation_id")
            and not legacy_possible(row)
            and possible_state(pk, row, tracking=tracking) == "clear")


def classify_new_pre_submit(pk: str, previous: dict, result: dict, *,
                            tracking=None) -> bool:
    """Fresh BrowserSkill producer proof plus independent row/key veto."""
    return (proof_from_result(result) and not legacy_possible(previous)
            and not unresolved_pre_submit(previous)
            and possible_state(pk, previous, tracking=tracking) == "clear")



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
    return not raw_absent(pk, tracking=tracking)


def clear(pk: str, *, tracking=None) -> None:
    """Only resolved recorded outcomes or verified pre-click refusals authorize this."""
    _client(tracking).delete(f"submit_hold:{pk}")


def blocked(pk: str, row: dict, *, tracking=None) -> bool:
    """Replay gate: uncertain/held and unresolved typed triage cannot run."""
    if legacy_possible(row) or unresolved_pre_submit(row):
        return True
    if tracking is None:
        from core.stores import make_stores
        tracking = make_stores().tracking
    if getattr(tracking, "r", None) is None:
        # Cloud is keyless and cannot prove the new pre-submit branch, but
        # existing cloud admission still relies on its legacy row guard.
        return False
    return possible_state(pk, row, tracking=tracking) != "clear"


def any_held(*, tracking=None) -> bool:
    return next(iter(_client(tracking).scan_iter(match="submit_hold:*")), None) is not None
