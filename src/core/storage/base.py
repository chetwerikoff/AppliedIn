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
        and (field not in row or row[field] == original[field])
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
    if any(row.get(key) not in (None, "", False, 0)
           for key in row if isinstance(key, str)
           and key.startswith(("browser_", "session_", "application_session",
                               "form_", "apply_started_", "last_button",
                               "last_url"))):
        return False
    # A request followed only by the pre-browser tenant-gate denial is allowed;
    # a different human/form gate or browser-session context is not evidence of
    # that narrow failure, even if an old fail_kind still says "defect_tenant".
    if row.get("apply_requested_at"):
        reasons = (row.get("gate_reason"), row.get("fail_kind"))
        tenant_reasons = {"defect_greenhouse_tenant_alias", "defect_tenant"}
        if (not any(reason in tenant_reasons for reason in reasons)
                or any(reason not in (*tenant_reasons, None, "") for reason in reasons)
                or any(row.get(field) not in (None, "", False, 0)
                       for field in ("gate_pending", "gate_call_id", "gate_source"))):
            return False
    return True


class AbstractTracking(ABC):
    """The application tracking store (cloud: DynamoDB `applications`)."""

    @abstractmethod
    def get(self, pk: str) -> dict | None: ...

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
