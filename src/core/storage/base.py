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



# Only the existing tracking row stores the guard witness and the current owner's
# reviewed generation. Comparing every guard-sensitive field here keeps the two
# storage adapters consistent without a new permission ledger.
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
                source_witness: bool = False) -> dict | None:
    """Return a safe *partial* patch for a guarded compare-and-update.

    The backend must atomically compare the original witness before committing.
    Source witness means the caller has matched the original ATS posting and
    examined all applicable HTML variants, not merely flattened scanner text.
    """
    from tools import posting_guard as pg

    if current.get("confirmation_id") or current.get("possible_submission"):
        return None
    if reviewed_generation is not None:
        meta = current.get("posting_guard")
        if (not pg.valid(current.get("jd_text") or "", meta)
                or reviewed_generation != meta["generation"]
                or current.get("status") not in ("tailored", "needs_human")
                or current.get("gate_reason") == "submit_uncertain"):
            return None
        return {"reviewed_guard_generation": reviewed_generation}
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
