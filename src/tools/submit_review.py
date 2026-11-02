"""Private, in-process final-Submit review. Never a durable Submit authority.

The real BrowserSkill application task owns its session and waits here; HTTP
requests only consume its opaque generation and wake that same task. A restart,
lost lease, missing private file, or runtime-flag change is never a click.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.config import get_settings

WINDOW_S = 600
_LOCK = threading.RLock()
_HOLDERS: dict[str, "Holder"] = {}
_STATES = frozenset({"pending", "claimed", "rejected", "expired",
                     "session_lost", "restart", "invalidated"})


class Refused(ValueError):
    """Non-approving review refusal, without private values in its message."""


def raise_review_gate(message: str) -> None:
    """All reviewer-only hold/continuation refusals share one Gate conversion.

    This keeps the original application Gate routing and prevents introducing
    additional per-control Gate sites at the sole final-Submit boundary.
    """
    from tools.browser_skill_apply import Gate
    raise Gate(message, reason="submit_review")


@dataclass
class Holder:
    pk: str
    company: str
    generation: str
    deadline: float
    expires_at: str
    loop: asyncio.AbstractEventLoop
    session: object
    controller: object
    packet: dict
    directory: Path
    decision: Future
    response: Future
    closed: bool = False
    reviewer_reason: str = ""


def guarded_row(row: dict | None) -> bool:
    """Even a corrupted/unknown review state may not enter legacy apply paths.

    The actual possible-submission state is separate and stronger; once its
    hold is acquired the gate_reason changes to submit_uncertain.
    """
    return bool(isinstance(row, dict)
                and row.get("gate_reason") == "submit_review")


_MANUAL_RESTART_STATES = frozenset({
    "rejected", "expired", "restart", "session_lost", "invalidated",
})


def manual_restart_allowed(pk: str, row: dict, *, tracking=None) -> bool:
    """An explicit fresh Retry may recover only an unsent, ownerless review."""
    from tools import submit_hold
    with _LOCK:
        generation = row.get("gate_generation")
        closed_generation = (isinstance(row.get("review_state"), str)
                             and row["review_state"] in _MANUAL_RESTART_STATES
                             and isinstance(generation, str)
                             and 16 <= len(generation) <= 128)
        # A pre-publication refusal can be gated without any reviewer token.
        # Only the exact unissued shape is recoverable; pending/claimed are not.
        unpublished = (row.get("review_state") in (None, "")
                       and generation in (None, "")
                       and row.get("review_expires_at") in (None, ""))
        if (not guarded_row(row) or row.get("status") != "needs_human"
                or not (closed_generation or unpublished)
                or row.get("pk") != pk or pk in _HOLDERS
                or row.get("possible_submission") or row.get("confirmation_id")):
            return False
        try:
            tracking = tracking or _tracking()
            current = tracking.get_current(pk) or {}
            if any(current.get(key) != row.get(key) for key in (
                    "pk", "company", "job_id", "jd_url", "status",
                    "gate_reason", "review_state", "gate_generation",
                    "review_expires_at")):
                return False
            return not submit_hold.blocked(pk, current, tracking=tracking)
        except Exception:
            return False


def begin_manual_restart(pk: str, row: dict, changes: dict, *, tracking=None) -> bool:
    """Consume the closed generation atomically; no old packet can re-authorize."""
    with _LOCK:
        if not manual_restart_allowed(pk, row, tracking=tracking):
            return False
        return _patch(pk, states=(row.get("review_state"),),
                      status="needs_human", generation=row.get("gate_generation"),
                      changes=changes)


def _tracking():
    from core.stores import make_stores
    if get_settings().mode != "local":
        raise Refused("Submit review requires the local tracking store.")
    tracking = make_stores().tracking
    if getattr(tracking, "r", None) is None:
        raise Refused("Submit review tracking is unavailable.")
    return tracking


def _live_lease(pk: str, company: str, tracking) -> bool:
    from core.apply_queue import ApplyQueue
    from core.ids import make_pk
    from tools import submit_hold
    if (not pk or not company or not isinstance(company, str)
            or not isinstance(pk, str)):
        return False
    row = tracking.get_current(pk) or {}
    try:
        if (make_pk(company, str(row.get("job_id") or "")) != pk
                or row.get("pk") != pk or row.get("company") != company
                or row.get("possible_submission") is True
                or submit_hold.blocked(pk, row, tracking=tracking)):
            return False
        queue = ApplyQueue(tracking.r)
        return (pk in queue.in_flight()
                and company.strip().lower() in queue.depth()["running"])
    except Exception:
        return False


def _patch(pk: str, *, states: tuple[str, ...], status: str,
           generation: str | None, changes: dict) -> bool:
    """Redis WATCH is the consume-before-await linearization point.

    The independent actual Submit hold is a stronger authority than this row:
    once that marker exists no review path may edit it or clear uncertainty.
    """
    from redis.exceptions import WatchError
    tracking = _tracking()
    key = f"app:{pk}"
    for _ in range(12):
        try:
            with tracking.r.pipeline() as pipe:
                pipe.watch(key, f"submit_hold:{pk}")
                raw = pipe.get(key)
                if not raw or pipe.get(f"submit_hold:{pk}") is not None:
                    pipe.unwatch()
                    return False
                row = json.loads(raw)
                if (row.get("status") != status
                        or row.get("review_state") not in states
                        or (generation is not None
                            and row.get("gate_generation") != generation)
                        or row.get("possible_submission")
                        or row.get("confirmation_id")
                        or (generation is not None
                            and row.get("gate_reason") != "submit_review")
                        # A tokenless fresh Retry must not clear a competing
                        # reviewer/owner gate if the row changed during admission.
                        or (changes.get("gate_reason") == ""
                            and row.get("gate_reason") != "submit_review")
                        or (changes.get("gate_reason") == "" and generation is None
                            and (row.get("gate_generation") not in (None, "")
                                 or row.get("review_expires_at") not in (None, "")))):
                    pipe.unwatch()
                    return False
                before = row.get("status")
                row.update(changes)
                after = row.get("status")
                pipe.multi()
                pipe.set(key, json.dumps(row, default=str))
                if before != after:
                    pipe.srem(f"status:{before}", pk)
                    pipe.sadd(f"status:{after}", pk)
                pipe.execute()
                return True
        except WatchError:
            continue
    return False


def _private_root() -> Path:
    local = Path(get_settings().local_dir).expanduser().absolute()
    if local.is_symlink():
        raise Refused("Private submit review storage is unavailable.")
    local.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = local / "submit-review"
    if root.is_symlink():
        raise Refused("Private submit review storage is unavailable.")
    root.mkdir(mode=0o700, exist_ok=True)
    # A pre-existing .local parent may be readable; the per-review root must
    # itself exclude every group/other permission, without chmodding user data.
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise Refused("Private submit review directory is not private.")
    return root


def _prune_reasons() -> None:
    """Limit private Reject notes to 100 small files and at most 30 days."""
    directory = _private_root() / "reasons"
    if not directory.exists():
        return
    if (directory.is_symlink() or not directory.is_dir()
            or stat.S_IMODE(directory.stat().st_mode) & 0o077):
        raise Refused("Private Reject reason storage is unsafe.")
    entries = []
    for item in directory.iterdir():
        if (item.is_symlink() or not item.is_file() or item.suffix != ".txt"):
            continue
        info = item.stat()
        entries.append((info.st_mtime, item))
    entries.sort(reverse=True)
    now = time.time()
    for index, (stamp, item) in enumerate(entries):
        if index >= 100 or now - stamp > 30 * 86400:
            item.unlink(missing_ok=True)


def private_directory(pk: str, generation: str) -> Path:
    name = hashlib.sha256(pk.encode()).hexdigest()[:24] + "-" + generation
    return _private_root() / name


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _cleanup(directory: Path) -> None:
    # Never follow a symlink, and never delete outside the dedicated root.
    try:
        if directory.is_symlink() or not directory.is_dir():
            return
        for name in ("packet.json", "screenshot.png"):
            path = directory / name
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        directory.rmdir()
    except OSError:
        pass


def _private_bytes(holder: Holder) -> tuple[dict, bytes]:
    path, image = holder.directory / "packet.json", holder.directory / "screenshot.png"
    for item in (path, image):
        if item.is_symlink() or not item.is_file():
            raise Refused("The current review packet is unavailable.")
        if stat.S_IMODE(item.stat().st_mode) & 0o077:
            raise Refused("Private packet permissions changed.")
    packet = json.loads(path.read_text(encoding="utf-8"))
    png = image.read_bytes()
    if (packet != holder.packet or not png.startswith(b"\x89PNG\r\n\x1a\n")
            or not 0 < len(png) <= 12_000_000):
        raise Refused("The current review packet is unavailable.")
    return packet, png


def publish(*, pk: str, company: str, controller, session,
            packet: dict, screenshot: bytes) -> Holder:
    """Private file write precedes a single atomic pending-row publication."""
    from core import flags
    from tools import browser_runtime, browser_skill
    if (not flags.submit_review_enabled() or browser_runtime.backend() is not browser_skill
            or not screenshot.startswith(b"\x89PNG\r\n\x1a\n")
            or not 0 < len(screenshot) <= 12_000_000):
        raise Refused("Submit review cannot be safely published.")
    with _LOCK:
        if pk in _HOLDERS:
            raise Refused("Another live submit review already owns this job.")
        tracking = _tracking()
        if not _live_lease(pk, company, tracking):
            raise Refused("The original company lease is unavailable.")
        generation = secrets.token_urlsafe(32)
        deadline = time.monotonic() + WINDOW_S
        expires_at = (datetime.now(UTC) + timedelta(seconds=WINDOW_S)).isoformat()
        packet = dict(packet, pk=pk, gate_generation=generation, expires_at=expires_at)
        directory = private_directory(pk, generation)
        directory.mkdir(mode=0o700)
        try:
            _write_private(directory / "packet.json", json.dumps(
                packet, ensure_ascii=False, separators=(",", ":")).encode())
            _write_private(directory / "screenshot.png", screenshot)
            # A harmless review is not a possible submission or a terminal gate.
            if not _live_lease(pk, company, tracking) or not _patch(
                    pk, states=(None, "", "expired", "rejected", "restart",
                                "invalidated", "session_lost"),
                    status="submitting", generation=None, changes={
                        "status": "needs_human", "gate_reason": "submit_review",
                        "gate_generation": generation, "review_state": "pending",
                        "review_expires_at": expires_at,
                        "gate_pending": None, "gate_call_id": None}):
                raise Refused("Tracking changed before review publication.")
            holder = Holder(pk, company, generation, deadline, expires_at,
                            asyncio.get_running_loop(), session, controller,
                            packet, directory, Future(), Future())
            _HOLDERS[pk] = holder
            return holder
        except BaseException:
            _cleanup(directory)
            raise


def _authority(holder: Holder, *, claimed: bool = False, committing: bool = False) -> bool:
    from core import flags
    from tools import browser_runtime, browser_skill
    if (holder.closed or _HOLDERS.get(holder.pk) is not holder
            or time.monotonic() >= holder.deadline
            or not flags.submit_review_enabled()
            or browser_runtime.backend() is not browser_skill
            or not getattr(holder.session, "id", None)):
        return False
    try:
        tracking = _tracking()
        row = tracking.get_current(holder.pk) or {}
        if (row.get("pk") != holder.pk or row.get("company") != holder.company
                or row.get("gate_generation") != holder.generation
                or row.get("review_state") != ("claimed" if claimed else "pending")
                or not _live_lease(holder.pk, holder.company, tracking)):
            # _live_lease deliberately refuses an actual submit hold. The final
            # raw command uses its own stronger post-marker check instead.
            return False
        if committing:
            return row.get("status") == "submitting"
        return (row.get("gate_reason") == "submit_review"
                and row.get("status") == "needs_human")
    except Exception:
        return False


def require_claim(holder: Holder, *, committing: bool = False) -> None:
    with _LOCK:
        if not _authority(holder, claimed=True, committing=committing):
            raise Refused("The original submit review claim or lease is no longer live.")


def activate(holder: Holder) -> None:
    with _LOCK:
        require_claim(holder)
        if not _patch(holder.pk, states=("claimed",), status="needs_human",
                      generation=holder.generation, changes={"status": "submitting"}):
            raise Refused("Review claim changed before final revalidation.")
        require_claim(holder, committing=True)


def _signal(holder: Holder, decision: str) -> None:
    if not holder.decision.done():
        holder.decision.set_result(decision)


def claim(pk: str, expected: str, decision: str, reason: str = "") -> Future:
    """Only this route may consume the review; the task itself performs Submit."""
    if (decision not in {"approve", "reject"}
            or not isinstance(expected, str) or not 16 <= len(expected) <= 128
            or not isinstance(reason, str) or len(reason) > 500):
        raise Refused("Invalid generation, decision or reviewer reason.")
    with _LOCK:
        holder = _HOLDERS.get(pk)
        if (holder is None or holder.generation != expected
                or not _authority(holder)):
            raise Refused("Review generation is stale or no longer live.")
        _private_bytes(holder)
        if not _patch(pk, states=("pending",), status="needs_human",
                      generation=expected, changes={"review_state": "claimed"}):
            raise Refused("Another decision already consumed this review.")
        if decision == "reject":
            holder.reviewer_reason = reason
            # No private answer text is placed in the tracked row or event stream.
            if reason:
                try:
                    reasons = _private_root() / "reasons"
                    if reasons.is_symlink():
                        raise Refused("Reviewer reason storage is unsafe.")
                    reasons.mkdir(mode=0o700, exist_ok=True)
                    if stat.S_IMODE(reasons.stat().st_mode) & 0o077:
                        raise Refused("Reviewer reason directory is not private.")
                    _write_private(reasons / (hashlib.sha256(
                        (pk + expected).encode()).hexdigest() + ".txt"), reason.encode())
                    _prune_reasons()
                except OSError:
                    reason = ""
            if not _patch(pk, states=("claimed",), status="needs_human",
                          generation=expected,
                          changes={"review_state": "rejected",
                                   "review_reject_reason": "reviewer_rejected"}):
                raise Refused("Review state changed during Reject.")
        _signal(holder, decision)
        return holder.response


def packet_for(pk: str) -> dict:
    import base64
    with _LOCK:
        holder = _HOLDERS.get(pk)
        if holder is None or not _authority(holder):
            raise Refused("No approvable live packet for this job.")
        packet, screenshot = _private_bytes(holder)
        return {"packet": packet,
                "screenshot": "data:image/png;base64," + base64.b64encode(
                    screenshot).decode("ascii")}


def settle(holder: Holder, result: dict) -> None:
    with _LOCK:
        if not holder.response.done():
            holder.response.set_result(result)


def invalidate(holder: Holder, state: str = "invalidated") -> None:
    if state not in {"expired", "session_lost", "restart", "invalidated", "rejected"}:
        state = "invalidated"
    with _LOCK:
        if _HOLDERS.get(holder.pk) is holder:
            try:
                # Only a harmless review can be rewritten; a real possible
                # submission is stronger and must retain uncertainty.
                for status in ("needs_human", "submitting"):
                    if _patch(holder.pk, states=("pending", "claimed"),
                              status=status, generation=holder.generation,
                              changes={"status": "needs_human",
                                       "gate_reason": "submit_review",
                                       "review_state": state}):
                        break
            except Exception:
                pass
            holder.closed = True
            _HOLDERS.pop(holder.pk, None)
            _signal(holder, state)
        _cleanup(holder.directory)
        settle(holder, {"ok": False, "error": "Submit review " + state
                        + "; start a fresh application run."})


def require_actual_marker(holder: Holder) -> None:
    """Final synchronous check at the raw BrowserSkill committing IPC edge."""
    from core import flags
    from tools import browser_runtime, browser_skill, submit_hold
    from core.apply_queue import ApplyQueue
    with _LOCK:
        if (holder.closed or _HOLDERS.get(holder.pk) is not holder
                or time.monotonic() >= holder.deadline
                or not getattr(holder.session, "id", None)
                or not flags.submit_review_enabled()
                or browser_runtime.backend() is not browser_skill):
            raise Refused("Submit review authority was revoked before Submit.")
        tracking = _tracking()
        row = tracking.get_current(holder.pk) or {}
        queue = ApplyQueue(tracking.r)
        if (row.get("pk") != holder.pk or row.get("company") != holder.company
                or row.get("gate_generation") != holder.generation
                or row.get("review_state") != "claimed"
                or row.get("gate_reason") != "submit_uncertain"
                or row.get("status") != "needs_human"
                or not submit_hold.is_held(holder.pk, tracking=tracking)
                or holder.pk not in queue.in_flight()
                or holder.company.strip().lower() not in queue.depth()["running"]
                or not getattr(holder.controller, "committed", False)
                or holder.controller.upload_identity != holder.controller._row_pdf()):
            raise Refused("Submit review form/lease/marker is not current at raw IPC.")


def finish(holder: Holder) -> None:
    with _LOCK:
        holder.closed = True
        if _HOLDERS.get(holder.pk) is holder:
            _HOLDERS.pop(holder.pk, None)
        _cleanup(holder.directory)
        settle(holder, {"ok": True, "status": "commit_authorized"})


async def await_decision(holder: Holder) -> str:
    """Keep the original owner task open; periodically verify lease and holder."""
    try:
        while True:
            with _LOCK:
                # A rejected token has already lost pending/claimed authority.
                # That is the intended no-click outcome, not a lost session.
                if holder.decision.done() and holder.decision.result() == "reject":
                    return "reject"
                if not _authority(holder, claimed=holder.decision.done()):
                    raise Refused("Submit review expired or lost its live lease.")
                if holder.decision.done():
                    return holder.decision.result()
            remaining = holder.deadline - time.monotonic()
            if remaining <= 0:
                raise Refused("Submit review expired.")
            await asyncio.sleep(min(2.0, remaining))
    except BaseException:
        invalidate(holder, "expired" if time.monotonic() >= holder.deadline
                   else "session_lost")
        raise


def invalidate_all() -> None:
    """A toggle to OFF revokes holders; it never releases them into auto-Submit."""
    with _LOCK:
        for holder in list(_HOLDERS.values()):
            invalidate(holder, "invalidated")


def recover_orphans() -> None:
    """A restarted process owns no old BrowserSkill task, packet or approval."""
    with _LOCK:
        if _HOLDERS:
            return
        # A filesystem failure must not hide a durable orphaned pending row.
        try:
            root = _private_root()
            for item in root.iterdir():
                if item.is_dir() and item.name != "reasons" and not item.is_symlink():
                    _cleanup(item)
            _prune_reasons()
        except Exception:
            pass
        try:
            tracking = _tracking()
            for status in ("needs_human", "submitting"):
                for row in tracking.query_status(status):
                    if (guarded_row(row)
                            and row.get("review_state") in {"pending", "claimed"}):
                        _patch(row["pk"], states=("pending", "claimed"),
                               status=status, generation=row.get("gate_generation"),
                               changes={"status": "needs_human",
                                        "review_state": "restart"})
        except Exception:
            # A storage outage leaves rows visibly gated, never approvable.
            pass


def _selected_control(control: dict) -> dict:
    """Private exact-value representation, without document/credential bytes."""
    combo = control.get("combobox") or {}
    return {
        "selector": control.get("selector"),
        "label": control.get("label") or control.get("question") or "",
        "question": control.get("question") or control.get("label") or "",
        "tag": control.get("tag"), "type": control.get("type"),
        "name": control.get("name"), "required": control.get("required"),
        "disabled": control.get("disabled"),
        "in_form": control.get("in_form"),
        "form_selector": control.get("form_selector"),
        "form_action": control.get("form_action"),
        "formaction": control.get("formaction"),
        "value": control.get("value"), "checked": control.get("checked"),
        "files": control.get("files"),
        "options": control.get("options"),
        "selected_options": control.get("selected_options"),
        "option_count": control.get("option_count"),
        "options_capped": control.get("options_capped"),
        "combobox": {
            k: combo.get(k) for k in (
                "selected_text", "selected_value", "displayed_text",
                "hidden_selector", "hidden_value", "selected_caption_unreadable",
                "local_selected_proven", "options")},
    }


def form_snapshot(controller, page: dict, target: dict) -> dict:
    """Read-only complete same-receiver inventory; fail closed on opaque items."""
    from tools.browser_skill_apply import _formless_boundary, _formless_root
    if (page.get("inventory_verified") is not True
            or page.get("truncated") or page.get("opaque_controls")
            or page.get("shadow_roots") or page.get("unsupported_frames")
            or not isinstance(page.get("controls"), list)
            or not isinstance(target, dict)
            or not target.get("selector")):
        raise Refused("The visible form inventory is incomplete.")
    root = ""
    if target.get("in_form") is True:
        root = target.get("form_selector")
        if not isinstance(root, str) or not root:
            raise Refused("The application form owner is ambiguous.")
        members = [c for c in page["controls"] if
                   c.get("in_form") is True and c.get("form_selector") == root]
        if len([c for c in members if c.get("selector") == target["selector"]]) != 1:
            raise Refused("The Submit receiver changed.")
    else:
        if not _formless_root(page):
            raise Refused("The formless application root is not proven.")
        members = _formless_boundary(
            page, controller.entry_provenance or controller.greenhouse_provenance,
            submit_selector=target["selector"])
    if not isinstance(members, list):
        raise Refused("The application root is incomplete.")
    visible = []
    for c in members:
        if not isinstance(c, dict):
            raise Refused("An application control is unreadable.")
        # Hidden machine backing is captured inside the linked combobox. Never
        # store independent hidden/credential controls or ignore an account wall.
        kind = str(c.get("type") or "")
        if kind in {"password", "otp"} or (
                "one-time" in str(c.get("label") or "").lower()):
            raise Refused("An account challenge needs separate owner handling.")
        if kind == "hidden" or c.get("visible") is False:
            continue
        if (not isinstance(c.get("selector"), str) or not c["selector"]
                or not isinstance(c.get("label"), str)
                or not isinstance(c.get("question"), str)
                or not isinstance(c.get("checked"), bool)
                or not isinstance(c.get("value"), str)
                or not isinstance(c.get("files"), list)
                or not isinstance(c.get("options"), list)):
            raise Refused("A visible application control is unreadable.")
        combo = c.get("combobox") or {}
        if combo.get("selected_caption_unreadable") is True:
            raise Refused("A selected choice caption is unreadable.")
        if c.get("tag") == "select" and c.get("value"):
            choices = [o for o in c["options"] if o.get("value") == c["value"]]
            if len(choices) != 1:
                raise Refused("The selected option is ambiguous.")
        visible.append(_selected_control(c))
    selectors = [c["selector"] for c in visible]
    if len(set(selectors)) != len(selectors) or not visible:
        raise Refused("Visible application control identities are ambiguous.")
    from pathlib import Path as _Path
    row = controller._row()
    return {
        "job": {"pk": controller.pk, "company": controller.company,
                "job_id": row.get("job_id"), "ats": row.get("ats"),
                "jd_url": controller.url},
        "page": {"url": page.get("url"), "title": page.get("title"),
                 "form_selector": root, "submit_selector": target["selector"],
                 "form_action": target.get("form_action"),
                 "formaction": target.get("formaction"),
                 "application_identity": page.get("application_identity"),
                 "attached_file_displays": [
                     d for d in (page.get("attached_file_displays") or [])
                     if isinstance(d, dict)
                     and ((root and d.get("in_form")
                           and d.get("form_selector") == root)
                          or (not root and not d.get("in_form")
                              and d.get("root_selector")
                              == (page.get("formless_root") or {}).get("selector")))]},
        "attachment": {
            "filename": _Path(controller.resume_path).name,
            "pdf_sha256": controller.expected_digest,
            "identity": list(controller._row_pdf())},
        "controls": visible,
    }


def form_differences(previous: dict, current: dict) -> list[dict]:
    """Bounded private reviewer differences; no values go to logs/tracking."""
    changes: list[dict] = []
    for key in ("job", "page", "attachment"):
        if previous.get(key) != current.get(key):
            changes.append({"control": key, "before": previous.get(key),
                            "after": current.get(key)})
    before, after = previous.get("controls") or [], current.get("controls") or []
    if len(before) != len(after):
        changes.append({"control": "visible control count",
                        "before": len(before), "after": len(after)})
    for i in range(max(len(before), len(after))):
        old = before[i] if i < len(before) else None
        new = after[i] if i < len(after) else None
        if old != new:
            changes.append({"control": (old or new or {}).get("label")
                            or (old or new or {}).get("selector") or str(i),
                            "identity": (old or new or {}).get("selector"),
                            "before": old, "after": new})
        if len(changes) >= 12:
            break
    return changes
