"""Storage interfaces — the contracts both modes implement.

Cloud backends (DynamoDB / S3 / SQS) and local backends (Redis / filesystem)
each subclass these ABCs, so the pipeline depends on the abstraction, not the
implementation (dependency inversion). The factory in ``core.stores`` picks the
concrete set by mode; nothing downstream knows or cares which it got.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from ..ids import make_pk
from ..models import AnswerScope, JobRecord, Status


# The exact five-field, presence-sensitive witness used by the deferred JD reader.
REPAIR_READER_FIELDS = (
    "jd_read_attempts", "jd_read_retry_at", "jd_read_revision",
    "jd_text", "jd_read_prepare_only",
)

# Existing submission evidence is never cleared or interpreted as reader attention.
# "attempts" is deliberately omitted: it is an application counter to preserve,
# not a reader budget or a standalone proof that a submit was attempted.
REPAIR_PROTECTED_FIELDS = (
    "apply_requested_at", "confirmation_id", "applied_at",
    "possible_submission", "submit_hold", "submit_hold_at",
    "submit_hold_reason", "submit_blocked",
    "submit_attempted_at", "submission_attempted_at",
    "submit_attempts", "submission_attempts",
    "submit_outcome", "submission_outcome",
    "submit_result", "submission_result",
    "submitted_at", "application_submitted_at",
    "submit_receipt", "submission_receipt",
)


def legacy_relative_jd_url(value: object) -> bool:
    """A real, syntactically safe relative href, not merely a non-HTTP URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == "\\"
           for ch in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (not parts.scheme and not parts.netloc and not value.startswith("//")
            and bool(parts.path or parts.query))


def repair_reader_witness(row: dict) -> dict:
    """Snapshot original reader values *and their presence* for the commit."""
    return {field: row[field] for field in REPAIR_READER_FIELDS if field in row}


def legacy_repair_row_allowed(
    row: dict | None, pk: str, company: str, job_id: str, old_url: str,
    expected_status: Status | str, expected_reader: dict,
) -> bool:
    """Closed eligibility predicate. Backend commits must also check it atomically."""
    if set(expected_reader) - set(REPAIR_READER_FIELDS):
        raise ValueError("Unexpected reader witness key")
    status = getattr(expected_status, "value", expected_status)
    if (not isinstance(row, dict) or not isinstance(company, str) or not company
            or not isinstance(job_id, str) or not job_id
            or pk != make_pk(company, job_id)
            or row.get("pk") != pk or row.get("company") != company
            or row.get("job_id") != job_id
            or row.get("jd_url") != old_url
            or not legacy_relative_jd_url(old_url)
            or row.get("status") != status):
        return False
    if (status == "found" and row.get("fail_kind") not in (None, "")):
        return False
    if status == "error" and row.get("fail_kind") != "jd_reader_exhausted":
        return False
    if status not in ("found", "error"):
        return False
    if any(bool(row.get(field)) for field in REPAIR_PROTECTED_FIELDS):
        return False
    if row.get("gate_reason") == "submit_uncertain":
        return False
    if any((field in row) != (field in expected_reader)
           or (field in row and row[field] != expected_reader[field])
           for field in REPAIR_READER_FIELDS):
        return False
    deadline = row.get("jd_read_retry_at")
    if deadline:
        try:
            until = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if until.tzinfo is None or until > datetime.now(UTC):
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    revision = row.get("jd_read_revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, (int, Decimal)):
        return False
    return revision >= 0 and int(revision) == revision



# A pin protects the original stored identity and every known irreversible-state
# witness, including absence versus explicit NULL. The request/attempt counter
# is a witness, not a blocker: a pre-browser tenant failure can be re-derived.
PIN_WITNESS_FIELDS = tuple(dict.fromkeys((
    "pk", "company", "job_id", "jd_url", "ats", "greenhouse_board",
    "status", "fail_kind", "gate_reason", "attempts", "note", "notes",
    "approval", "approved", "approved_at", "approval_at", "approval_status",
    "human_approved", "human_approved_answers",
    # _apply_direct persists these browser-result artifacts without a prefix.
    "fields", "screenshot_s3_key",
    "gate_pending", "gate_call_id", "gate_source", "last_button", "last_url",
    "browser_session", "browser_session_id",
    "application_session", "application_session_id", "possible_submission",
    "submit_hold", "submit_hold_at", "submit_hold_reason",
    "submit_uncertain", "submission_uncertain", "browser_started_at",
    "form_started_at", "form_submitted_at", "apply_started_at",
    "application_attempted_at", "application_submitted_at",
) + REPAIR_READER_FIELDS + REPAIR_PROTECTED_FIELDS))
_PIN_WITNESS_PREFIXES = (
    "apply_", "approval_", "approved_", "human_approved_", "gate_",
    "browser_", "session_", "form_", "submit_", "submission_", "jd_read_",
    "confirmation_", "reader_", "application_", "last_",
)
_PIN_ALLOWED_STATUSES = frozenset(x.value for x in Status) - {
    "submitting", "applied", "applied_manual",
}
_PIN_SUBMISSION_FIELDS = frozenset((
    "confirmation_id", "applied_at", "possible_submission", "submit_blocked",
    "submit_attempted_at", "submission_attempted_at", "submit_attempts",
    "submission_attempts", "submit_outcome", "submission_outcome",
    "submit_result", "submission_result", "submitted_at",
    "application_submitted_at", "submit_receipt", "submission_receipt",
    "submit_uncertain", "submission_uncertain", "form_submitted_at",
))


def tenant_pin_witness_fields(original: dict) -> tuple[str, ...]:
    """Fields whose original presence and value must survive a pin commit."""
    return tuple(dict.fromkeys(
        PIN_WITNESS_FIELDS + tuple(
            key for key in original if isinstance(key, str)
            and key.startswith(_PIN_WITNESS_PREFIXES)
        )
    ))


def tenant_pin_same_witness(row: dict, original: dict) -> bool:
    if not isinstance(row, dict) or not isinstance(original, dict):
        return False
    fields = set(tenant_pin_witness_fields(original))
    # A newly observed guarded field must not be silently accepted, even when
    # its name was not in the original row.
    fields.update(tenant_pin_witness_fields(row))
    return all(
        (field in row) == (field in original)
        # Redis JSON treats booleans and numbers as different persisted data,
        # although Python equates False == 0 and True == 1. A changed
        # protected witness must lose rather than authorize a pin.
        and (field not in row or (
            type(row[field]) is type(original[field])
            and row[field] == original[field]
        ))
        for field in fields
    )


def tenant_pin_row_allowed(row: dict | None, pk: str, board: str) -> bool:
    """Narrow stored-row gate; the caller separately proves URL and fresh feed."""
    import re

    if (not isinstance(row, dict) or not isinstance(pk, str) or not pk
            or not isinstance(board, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", board) is None
            or row.get("pk") != pk or row.get("ats") != "greenhouse"
            or not isinstance(row.get("company"), str) or not row["company"]
            or not isinstance(row.get("job_id"), str)
            or re.fullmatch(r"[0-9]+", row["job_id"]) is None
            or pk != make_pk(row["company"], row["job_id"])
            or not isinstance(row.get("jd_url"), str)
            or "greenhouse_board" in row
            or row.get("status") not in _PIN_ALLOWED_STATUSES):
        return False
    # Any row-local hold attribute is unsafe even when empty or false.
    if any(field in row for field in (
            "submit_hold", "submit_hold_at", "submit_hold_reason")):
        return False
    if row.get("gate_reason") == "submit_uncertain":
        return False
    if any(row.get(field) not in (None, "", False, 0)
           for field in _PIN_SUBMISSION_FIELDS):
        return False
    # Session/interaction attributes are evidence that execution reached a
    # browser. Even an empty/malformed value cannot certify that no session
    # occurred; fail closed instead of inferring a safe pre-browser history.
    if any(isinstance(key, str) and key.startswith((
            "browser_", "session_", "application_session", "form_",
            "apply_started_", "last_button", "last_url"))
           for key in row):
        return False
    # Browser-result artifacts are not tagged browser_ in real tracking rows.
    # Even empty artifacts cannot prove this row remained pre-browser.
    if "fields" in row or "screenshot_s3_key" in row:
        return False
    # Validate gate history independently of request timestamps. A requestless
    # form gate or ambiguous failed application is not a pre-browser tenant
    # denial; the explicit request-only tenant failure remains eligible.
    reasons = (row.get("gate_reason"), row.get("fail_kind"))
    tenant_reasons = {"defect_greenhouse_tenant_alias", "defect_tenant"}
    if (any(reason not in (*tenant_reasons, None, "") for reason in reasons)
            or any(row.get(field) not in (None, "", False, 0)
                   for field in ("gate_pending", "gate_call_id", "gate_source"))
            or (bool(row.get("apply_requested_at"))
                != any(reason in tenant_reasons for reason in reasons))
            or (row.get("status") in ("needs_human", "failed")
                and not row.get("apply_requested_at"))):
        return False
    return True

# Only the existing tracking row stores the guard witness and the current owner's
# reviewed generation. Comparing every guard-sensitive field here keeps the two
# storage adapters consistent without a new permission ledger.
# Both backends pin these presence-sensitive values at the consume commit: the
# gate itself, and every field whose concurrent change would make the winner's
# merged grant/skip map or its "no submission yet" premise stale.
DIRECT_GATE_COMPARE = (
    "status", "gate_call_id", "gate_source", "gate_pending",
    "confirmation_id", "possible_submission",
    "human_approved_answers", "owner_gate_skips",
)


def direct_gate_patch(current: dict | None, expected_pending: dict, disposition: dict,
                      grant: tuple[str, dict] | None,
                      skip: tuple[str, dict] | None) -> dict | None:
    """The one consume patch, or None when the stored gate is not the expected one.

    A generationless pending map predates per-gate generations: it can never be
    consumed, because an identical-looking reissue would be indistinguishable.
    """
    if (not isinstance(current, dict) or not isinstance(expected_pending, dict)
            or not isinstance(expected_pending.get("generation"), str)
            or not expected_pending["generation"]
            or not isinstance(expected_pending.get("form_question"), dict)
            or (grant is not None and skip is not None)
            or disposition.get("kind") not in {"answer", "skip", "refuse"}
            or (disposition["kind"] == "skip") != (skip is not None)
            or (disposition["kind"] == "refuse"
                and (grant is not None or not isinstance(disposition.get("detail"), str)))):
        return None
    if (current.get("status") != Status.NEEDS_HUMAN.value
            or current.get("gate_call_id") != "direct"
            or current.get("gate_source") != "applier"
            or current.get("gate_pending") != expected_pending
            or current.get("confirmation_id")
            or current.get("possible_submission") is True):
        return None
    if disposition["kind"] == "refuse":
        # An owner "no" to a required acknowledgement spends the same gate, but
        # leaves an unbound terminal question instead of a continuation.
        return {"status": Status.NEEDS_HUMAN.value, "gate_reason": "cannot_proceed",
                "gate_pending": {"question": disposition["detail"], "form_question": None},
                "gate_call_id": None, "gate_disposition": disposition}
    patch: dict = {
        "status": Status.TAILORED.value, "gate_pending": {}, "gate_call_id": None,
        "gate_source": None, "gate_reason": "", "gate_disposition": disposition,
    }
    if grant is not None:
        grants = dict(current.get("human_approved_answers") or {})
        grants[grant[0]] = grant[1]
        patch["human_approved_answers"] = grants
    if skip is not None:
        skips = dict(current.get("owner_gate_skips") or {})
        skips[skip[0]] = skip[1]
        patch["owner_gate_skips"] = skips
    return patch


def grants_replace_patch(row: dict | None, expected: dict, new: dict) -> dict | None:
    """The merged approval map for a guarded grant replacement, else None.

    `expected` maps each guarded key to its exact current grant, or None for a
    key that must be absent. `new` maps the only keys that may change; each must
    also be guarded. Any other key of the approval map is carried over untouched.
    """
    if (not isinstance(expected, dict) or not isinstance(new, dict) or not new
            or not set(new) <= set(expected)
            or any(not isinstance(v, dict) for v in new.values())
            or any(v is not None and not isinstance(v, dict) for v in expected.values())):
        raise ValueError("Grant replacement needs guarded keys and dict grants")
    if (not isinstance(row, dict) or row.get("status") != Status.SUBMITTING.value
            or row.get("possible_submission")):
        return None
    grants = row.get("human_approved_answers")
    if grants is None:
        grants = {}
    if not isinstance(grants, dict):
        return None
    for key, grant in expected.items():
        if grant is None:
            if key in grants:
                return None
        elif grants.get(key) != grant:
            return None
    return {**grants, **new}


_GUARD_COMPARE = (
    "pk", "company", "jd_url", "status", "jd_text", "posting_guard",
    "reviewed_guard_generation", "confirmation_id", "possible_submission",
    "submit_hold", "gate_reason",
)


def guard_observation_matches(current: dict | None, observed: dict) -> bool:
    return (isinstance(current, dict) and isinstance(observed, dict)
            and all((key in current) == (key in observed)
                    and (key not in current or current[key] == observed[key])
                    for key in _GUARD_COMPARE))


def guard_patch(current: dict, inspected: dict | None,
                *, reviewed_generation: str | None = None,
                source_witness: bool = False,
                settle_refusal: bool = False) -> dict | None:
    """Return a safe *partial* patch for a guarded compare-and-update.

    The backend must atomically compare the original witness before committing.
    Source witness means the caller has matched the original ATS posting and
    examined all applicable HTML variants, not merely flattened scanner text.
    """
    from tools import posting_guard as pg

    if (current.get("status") in ("applied", "applied_manual")
            or current.get("confirmation_id") or current.get("possible_submission")
            or current.get("submit_hold")):
        return None
    if settle_refusal:
        if (current.get("status") != "submitting"
                or current.get("submit_hold") or current.get("possible_submission")
                or current.get("confirmation_id")
                or current.get("gate_reason") == "submit_uncertain"):
            return None
        return {
            "status": "tailored", "gate_reason": "approval",
            "fail_kind": "posting_guard_review_required",
            "fail_reason": "Posting review is required for the current guard generation.",
            "gate_pending": {"question": "Review the current posting safety signal before applying."},
        }
    if reviewed_generation is not None:
        meta = current.get("posting_guard")
        if (not pg.valid(current.get("jd_text") or "", meta)
                or reviewed_generation != meta["generation"]
                or current.get("status") not in ("tailored", "needs_human")
                or current.get("gate_reason") == "submit_uncertain"):
            return None
        patch = {"reviewed_guard_generation": reviewed_generation}
        # The refusal's failure marker answers exactly this review; left behind,
        # it blocks requeue routes that require an empty fail_kind.
        if current.get("fail_kind") == "posting_guard_review_required":
            patch["fail_kind"] = ""
        return patch
    if not isinstance(inspected, dict):
        return None
    cleaned, meta = inspected.get("text"), inspected.get("posting_guard")
    if not isinstance(cleaned, str) or not pg.valid(cleaned, meta):
        return None
    old_meta = current.get("posting_guard")
    if not pg.valid(current.get("jd_text") or "", old_meta):
        # A missing or corrupt older annotation cannot certify the next
        # unverified plain read as safe. Old raw JD is never merged into text.
        old_meta = pg.projection(current.get("jd_text") or "", old_meta)["posting_guard"]
    merged = pg.merge(cleaned, meta, old_meta, source_witness=source_witness)
    return {"jd_text": merged["text"], "posting_guard": merged["posting_guard"]}



# A separate, stricter one-PK URL replacement may not reuse the legacy #22
# relative recrawl guard. Its frozen observation compares identity, reader,
# guard/review, known browser/submit evidence and even absent named attributes.
CANONICAL_REPAIR_FIELDS = tuple(dict.fromkeys((
    "pk", "company", "job_id", "ats", "jd_url", "greenhouse_board",
    "status", "fail_kind", "attempts", "apply_requested_at",
    "posting_guard", "reviewed_guard_generation", "gate_reason",
) + REPAIR_READER_FIELDS + REPAIR_PROTECTED_FIELDS + PIN_WITNESS_FIELDS
    + _GUARD_COMPARE))


def canonical_repair_witness_fields(original: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        CANONICAL_REPAIR_FIELDS + tuple(
            key for key in original if isinstance(key, str)
            and key.startswith(_PIN_WITNESS_PREFIXES)
        )
    ))


def canonical_repair_same_witness(current: dict | None, original: dict) -> bool:
    if not isinstance(current, dict) or not isinstance(original, dict):
        return False
    keys = set(canonical_repair_witness_fields(original))
    keys.update(canonical_repair_witness_fields(current))
    return all(
        (field in current) == (field in original)
        and (field not in current or (
            type(current[field]) is type(original[field])
            and current[field] == original[field]
        ))
        for field in keys
    )


def canonical_repair_row_allowed(row: dict | None, pk: str) -> bool:
    """One dormant, unreviewed source whose prior JD can be invalidated."""
    if (not isinstance(row, dict) or row.get("pk") != pk
            or not isinstance(row.get("company"), str)
            or not isinstance(row.get("job_id"), str)
            or not row["company"] or not row["job_id"]
            or make_pk(row["company"], row["job_id"]) != pk
            or row.get("ats") not in ("greenhouse", "ashby", "lever")
            or not isinstance(row.get("jd_url"), str)):
        return False
    status = row.get("status")
    if (status == "found" and row.get("fail_kind") not in (None, "")):
        return False
    if (status == "error" and row.get("fail_kind") != "jd_reader_exhausted"):
        return False
    if status not in ("found", "error"):
        return False
    if row.get("apply_requested_at", "") != "":
        return False
    # Presence of a hold, review, gate, or browser/session record is itself
    # history, including empty/NULL/false values.
    if any(k in row for k in (
        "reviewed_guard_generation", "submit_hold", "submit_hold_at",
        "submit_hold_reason", "fields", "screenshot_s3_key",
        "gate_pending", "gate_call_id", "gate_source", "gate_reason",
        "approval", "approved", "approved_at", "approval_at",
        "approval_status", "human_approved", "human_approved_answers",
        "last_button", "last_url", "notes",
    )):
        return False
    if any(k in row for k in REPAIR_PROTECTED_FIELDS
           if k != "apply_requested_at"):
        return False
    if any(isinstance(k, str) and k.startswith((
            "approval_", "approved_", "human_approved_", "gate_",
            "browser_", "session_", "form_", "submit_", "submission_",
            "confirmation_", "application_session", "last_",
            "apply_started_", "application_attempted_", "application_submitted_"))
           for k in row):
        return False
    revision = row.get("jd_read_revision", 0)
    if (isinstance(revision, bool) or not isinstance(revision, (int, Decimal))
            or revision < 0 or revision != int(revision)):
        return False
    retry = row.get("jd_read_retry_at")
    if retry not in (None, ""):
        try:
            until = datetime.fromisoformat(str(retry).replace("Z", "+00:00"))
            if until.tzinfo is None or until > datetime.now(UTC):
                return False
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def canonical_repair_patch(row: dict, new_url: str) -> tuple[dict, tuple[str, ...]]:
    """Rebind no trust: the next read must fetch the *new* URL's source HTML."""
    from tools import posting_guard as pg

    if not isinstance(new_url, str) or not new_url.startswith("https://"):
        raise ValueError("Expected an independently proven literal hosted URL")
    patch = {
        "jd_url": new_url, "status": Status.FOUND.value,
        "jd_read_attempts": 0,
        "jd_read_revision": int(row.get("jd_read_revision", 0)) + 1,
        "jd_text": "",
        "posting_guard": pg.analyze("", source_required=True)["posting_guard"],
    }
    removes = ["jd_read_retry_at", "reviewed_guard_generation"]
    if row["status"] == Status.ERROR.value:
        removes.append("fail_kind")
    return patch, tuple(removes)

class AbstractTracking(ABC):
    """The application tracking store (cloud: DynamoDB `applications`)."""

    @abstractmethod
    def get(self, pk: str) -> dict | None: ...

    def get_current(self, pk: str) -> dict | None:
        """Authoritative base-row read for guarded submission and review."""
        return self.get(pk)

    @abstractmethod
    def update_posting_guard(
        self, pk: str, observed: dict, inspected: dict | None = None, *,
        reviewed_generation: str | None = None, source_witness: bool = False,
        settle_refusal: bool = False,
    ) -> bool:
        """Atomically compare original row/guard witnesses and patch guard fields.

        Never infer review from dispatch intent. A false/conflicting write cannot
        turn stale observations into authorization.
        """

    @abstractmethod
    def put_new(self, job: JobRecord, *, status: Status = Status.FOUND) -> bool:
        """Conditionally create; return False if pk already exists (dedup)."""

    @abstractmethod
    def set_status(self, pk: str, status: Status, **attrs: object) -> None: ...

    @abstractmethod
    def update_if_status(self, pk: str, expected_status: Status | str,
                         updates: dict, *, expected_reader: dict) -> bool:
        """Atomically patch an existing row whose status and exact reader
        witness match. An absent witness key requires attribute absence.
        Conditional mismatch returns False; storage exceptions propagate.
        """

    @abstractmethod
    def replace_grants_if_unchanged(self, pk: str, expected: dict, new: dict) -> bool:
        """Atomically replace approval-map keys only while their premise holds.

        Requires status ``submitting`` and no possible submission. Every key of
        `expected` must still hold exactly the given grant (None: still absent).
        Only the keys of `new` are written; the status and every other approval
        key stay as stored. Any mismatch writes nothing and returns False;
        storage errors propagate.
        """

    @abstractmethod
    def consume_direct_form_gate(
        self, pk: str, *, expected_pending: dict, disposition: dict,
        grant: tuple[str, dict] | None = None,
        skip: tuple[str, dict] | None = None,
    ) -> bool:
        """Spend one exact pending direct owner field gate, once.

        Two dashboard replies can reach one generation. Only the commit that
        still sees status ``needs_human``, the applier's ``direct`` call, the
        ENTIRE original ``gate_pending`` (generation included), no confirmation
        and no durable submit hold may win. The same patch moves the row to
        ``tailored``, clears the gate and records the winner's disposition, its
        one field grant or its one skip record; a ``refuse`` disposition instead
        leaves the row at an unbound ``cannot_proceed`` question. Missing rows and conditional
        conflicts return False; storage errors propagate.
        """

    @abstractmethod
    def repair_legacy_jd_url(
        self, pk: str, company: str, job_id: str, old_url: str,
        new_url: str, *, expected_status: Status | str, expected_reader: dict,
    ) -> bool:
        """Atomically repair only a safe same-pk legacy row.

        The caller supplies an already-normalized absolute URL on the verified
        company origin. Match exact old href/status/reader presence and values,
        refuse submission evidence, and advance the reader generation once.
        Missing rows and conditional conflicts return False; other errors raise.
        """

    def replace_noncanonical_jd_url(
        self, pk: str, new_url: str, *, expected_row: dict,
    ) -> bool:
        """Exact-PK, guarded, partial/no-upsert rebind after external preflight.

        The caller must independently prove old retained source, current feed
        and admission by unchanged Submit route *immediately* before this call.
        No existing current R1/R2/R3 Greenhouse class grants that authority.
        """
        raise NotImplementedError

    def add_missing_greenhouse_pin(
        self, pk: str, board: str, *, expected_row: dict,
    ) -> bool:
        """Conditionally set only an absent board pin at the original PK.

        The caller supplies the first observed row; backends compare its
        presence-sensitive identity and protected witnesses at the commit.
        Redis also watches its independent durable hold key. DynamoDB can
        condition only on application-row attributes.
        """
        raise NotImplementedError

    def application_notes(self) -> dict:
        raise NotImplementedError

    def save_application_note(self, pk: str, note: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_by_jd_hash(self, jd_hash: str) -> str | None: ...

    @abstractmethod
    def query_status(self, status: Status) -> list[dict]: ...

    @abstractmethod
    def all(self) -> list[dict]:
        """Every application row (excludes internal meta rows). For the UI."""

    @abstractmethod
    def try_increment_daily_cap(self, date_str: str, cap: int) -> bool:
        """Atomically reserve one submit slot; False when the cap is reached."""


class AbstractAnswerBank(ABC):
    """The two-scope answer bank (global facts + per-company answers)."""

    @abstractmethod
    def lookup(self, question: str, company: str) -> str | None: ...

    @abstractmethod
    def put(self, question: str, answer: str, scope: AnswerScope, *,
            company: str | None = None, source: str = "", approved_at: str = "") -> None: ...

    @abstractmethod
    def seed_global(self, entries: dict[str, str], *, source: str = "seed") -> None: ...

    @abstractmethod
    def all_facts(self, company: str) -> dict[str, str]:
        """Every approved answer available to this company (global + company),
        as question→answer — handed to the browser agent so it fills from
        approved data only."""


class AbstractArtifactStore(ABC):
    """Binary artifacts: JD snapshots, résumés, screenshots, field maps."""

    @abstractmethod
    def put(self, prefix: str, key: str, data: bytes, content_type: str) -> str: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def presign(self, key: str, *, expires: int = 3600) -> str: ...

    def version(self, key: str) -> str:
        """A token that changes whenever these bytes change, or "" if unknown.

        An artifact key is stable across re-runs, so the URL built from it is too
        — and a viewer that has already rendered that URL may keep showing what
        it rendered. Carrying a version makes a rebuilt document a different
        address. Empty by default: a backend that cannot answer must not invent a
        constant, which would be a URL that never changes again.
        """
        return ""

    def exists(self, key: str) -> bool:
        """Are the bytes for this key actually there?

        A tracking row keeps its artifact key forever; the bytes are not
        guaranteed to. Anything that turns a key into something the owner can
        click has to ask first. Defaults to True so a backend that cannot answer
        cheaply keeps the old behaviour.
        """
        return True


class AbstractQueue(ABC):
    """Work queue (cloud: SQS; local: Redis list)."""

    @abstractmethod
    def enqueue(self, queue_url: str, body: dict) -> str: ...


class AbstractSecrets(ABC):
    """Credential store (cloud: Secrets Manager; local: an env-keyed JSON file)."""

    @abstractmethod
    def get_json(self, name: str) -> dict | None: ...

    @abstractmethod
    def put_json(self, name: str, obj: dict) -> None: ...
