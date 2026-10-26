"""The ``applications`` DynamoDB table — the pipeline's memory.

Dedup and the daily cap are enforced by conditional writes, never
read-then-write, so overlapping poll cycles and concurrent apply tasks
cannot race (HLD premise 5 + guardrail 1).
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import ClientError

from ..models import JobRecord, Status
from .base import (
    AbstractTracking, REPAIR_PROTECTED_FIELDS, REPAIR_READER_FIELDS,
    legacy_repair_row_allowed, tenant_pin_row_allowed,
    tenant_pin_witness_fields, tenant_pin_same_witness,
    guard_observation_matches, guard_patch, _GUARD_COMPARE,
    DIRECT_GATE_COMPARE, direct_gate_patch,
    canonical_repair_row_allowed, canonical_repair_same_witness,
    canonical_repair_witness_fields, canonical_repair_patch,
)
from ..ids import is_internal_pk

JD_HASH_INDEX = "jd_hash-index"
STATUS_INDEX = "status-index"
_CAP_PK = "meta#dailycap"


class TrackingStore(AbstractTracking):
    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put_new(self, job: JobRecord, *, status: Status = Status.FOUND) -> bool:
        """Conditionally create a new row. Returns False if the pk already exists.

        The ``attribute_not_exists(pk)`` condition is the dedup guarantee: a
        second poll cycle that rediscovers the same posting is a no-op.
        """
        from tools import posting_guard as pg

        guarded = ({"text": job.jd_text, "posting_guard": job.posting_guard}
                   if pg.valid(job.jd_text, job.posting_guard)
                   else pg.projection(job.jd_text, job.posting_guard))
        item: dict[str, Any] = {
            "pk": job.pk,
            "jd_hash": job.jd_hash,
            "status": status.value,
            "company": job.company,
            "job_id": job.job_id,
            "title": job.title,
            "jd_url": job.jd_url,
            "location": job.location,
            "ats": job.ats,
            "attempts": 0,
            "discovery_source": job.discovery_source,
            "apply_requested_at": job.apply_requested_at,
            "jd_text": guarded["text"],
            "posting_guard": guarded["posting_guard"],
        }
        if job.greenhouse_board:
            item["greenhouse_board"] = job.greenhouse_board
        if job.discovery_source == "sitemap_greenhouse_suffix":
            # Preserve feed first_published for this new data producer; ordinary
            # feed and generic discovery persistence retain their prior shape.
            item["posted_at"] = job.posted_at
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get(self, pk: str) -> dict | None:
        return self._table.get_item(Key={"pk": pk}).get("Item")

    def get_current(self, pk: str) -> dict | None:
        return self._table.get_item(
            Key={"pk": pk}, ConsistentRead=True).get("Item")

    def update_posting_guard(
        self, pk: str, observed: dict, inspected: dict | None = None, *,
        reviewed_generation: str | None = None, source_witness: bool = False,
        settle_refusal: bool = False,
    ) -> bool:
        """Read the base table consistently, then compare at UpdateItem commit."""
        current = self.get_current(pk)
        if not guard_observation_matches(current, observed):
            return False
        patch = guard_patch(current, inspected,
                            reviewed_generation=reviewed_generation,
                            source_witness=source_witness,
                            settle_refusal=settle_refusal)
        if patch is None:
            return False
        names, values, conditions = {"#pk": "pk"}, {}, ["attribute_exists(#pk)"]
        for i, key in enumerate(_GUARD_COMPARE):
            field, param = f"#g{i}", f":g{i}"
            names[field] = key
            if key in observed:
                values[param] = observed[key]
                conditions.append(f"{field} = {param}")
            else:
                conditions.append(f"attribute_not_exists({field})")
        sets = []
        for i, (key, value) in enumerate(patch.items()):
            field, param = f"#u{i}", f":u{i}"
            names[field] = key
            values[param] = value
            sets.append(f"{field} = {param}")
        try:
            self._table.update_item(
                Key={"pk": pk}, UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def get_alias(self, pk: str) -> dict | None:
        """Read alias-critical identity from the base table after committed changes.

        This is a dedicated path: ordinary gets keep their existing eventual
        consistency. It cannot cover concurrent writes after this observation.
        """
        return self._table.get_item(
            Key={"pk": pk}, ConsistentRead=True).get("Item")

    def set_status(self, pk: str, status: Status, **attrs: Any) -> None:
        if set(attrs) & {"jd_text", "posting_guard", "reviewed_guard_generation"}:
            raise ValueError("Use update_posting_guard for bound JD/guard changes")
        names = {"#s": "status"}
        values = {":s": status.value}
        sets = ["#s = :s"]
        from datetime import datetime, timezone

        stamp = {"tailored": "tailored_at", "applied": "applied_at",
                 "applied_manual": "applied_at"}.get(status.value)
        if stamp and stamp not in attrs:
            names["#date"] = stamp
            values[":date"] = datetime.now(timezone.utc).isoformat()
            sets.append("#date = if_not_exists(#date, :date)")
        for i, (key, value) in enumerate(attrs.items()):
            names[f"#a{i}"] = key
            values[f":a{i}"] = value
            sets.append(f"#a{i} = :a{i}")
        self._table.update_item(
            Key={"pk": pk},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def update_if_status(self, pk: str, expected_status: Status | str,
                         updates: dict, *, expected_reader: dict) -> bool:
        """One conditional partial UpdateItem, matching reader presence exactly.

        Only ConditionalCheckFailedException means a benign conflict. All other
        Dynamo errors retain their original cause for the caller to diagnose.
        """
        if set(updates) & {"jd_text", "posting_guard", "reviewed_guard_generation"}:
            raise ValueError("Use update_posting_guard for bound JD/guard changes")
        fixed = ("jd_read_attempts", "jd_read_retry_at", "jd_read_revision",
                 "jd_text", "jd_read_prepare_only")
        if set(expected_reader) - set(fixed):
            raise ValueError("Unexpected reader witness key")
        names = {"#pk": "pk", "#s": "status"}
        values = {":old_status": getattr(expected_status, "value", expected_status)}
        conditions = ["attribute_exists(#pk)", "#s = :old_status"]
        for i, field in enumerate(fixed):
            name = f"#r{i}"
            names[name] = field
            if field in expected_reader:
                value = f":r{i}"
                values[value] = expected_reader[field]
                conditions.append(f"{name} = {value}")
            else:
                conditions.append(f"attribute_not_exists({name})")
        sets = []
        for i, (field, value) in enumerate(updates.items()):
            name, token = f"#u{i}", f":u{i}"
            names[name] = field
            values[token] = getattr(value, "value", value) if field == "status" else value
            sets.append(f"{name} = {token}")
        next_status = getattr(updates.get("status"), "value", updates.get("status"))
        if next_status == "tailored" and "tailored_at" not in updates:
            from datetime import datetime, timezone
            names["#date"] = "tailored_at"
            values[":date"] = datetime.now(timezone.utc).isoformat()
            sets.append("#date = if_not_exists(#date, :date)")
        if not sets:
            raise ValueError("Conditional patch must not be empty")
        try:
            self._table.update_item(
                Key={"pk": pk},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def consume_direct_form_gate(
        self, pk: str, *, expected_pending: dict, disposition: dict,
        grant: tuple[str, dict] | None = None,
        skip: tuple[str, dict] | None = None,
    ) -> bool:
        """One conditional partial UpdateItem pinned to the consistent read.

        Every compared attribute must still hold its read value (or still be
        absent), so the merged grant/skip map cannot drop a concurrent write.
        """
        current = self.get_current(pk)
        patch = direct_gate_patch(current, expected_pending, disposition, grant, skip)
        if patch is None:
            return False
        names, values, conditions = {"#pk": "pk"}, {}, ["attribute_exists(#pk)"]
        for i, field in enumerate(DIRECT_GATE_COMPARE):
            name, token = f"#c{i}", f":c{i}"
            names[name] = field
            if field in current:
                values[token] = current[field]
                conditions.append(f"{name} = {token}")
            else:
                conditions.append(f"attribute_not_exists({name})")
        sets = []
        for i, (field, value) in enumerate(patch.items()):
            name, token = f"#u{i}", f":u{i}"
            names[name] = field
            values[token] = value
            sets.append(f"{name} = {token}")
        if patch["status"] == "tailored":
            names["#date"] = "tailored_at"
            from datetime import datetime, timezone
            values[":date"] = datetime.now(timezone.utc).isoformat()
            sets.append("#date = if_not_exists(#date, :date)")
        try:
            self._table.update_item(
                Key={"pk": pk}, UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def repair_legacy_jd_url(
        self, pk: str, company: str, job_id: str, old_url: str,
        new_url: str, *, expected_status: Status | str, expected_reader: dict,
    ) -> bool:
        """Conditional no-upsert partial UpdateItem with exact original witness.

        A strongly consistent observation lets us reject invalid old rows and
        capture all protected-field presence/values. The conditional write
        checks that complete observation at commit, so an intervening request,
        confirmation, hold, status change or reader generation always loses.
        Unrelated attributes remain untouched by the partial UpdateItem.
        """
        row = self._table.get_item(Key={"pk": pk}, ConsistentRead=True).get("Item")
        if not legacy_repair_row_allowed(
                row, pk, company, job_id, old_url,
                expected_status, expected_reader):
            return False

        names = {
            "#pk": "pk", "#co": "company", "#job": "job_id",
            "#url": "jd_url", "#s": "status", "#count": "jd_read_attempts",
            "#rev": "jd_read_revision",
        }
        values = {
            ":pk": pk, ":co": company, ":job": job_id, ":old": old_url,
            ":status": getattr(expected_status, "value", expected_status),
            ":new": new_url, ":found": Status.FOUND.value,
            ":zero": 0, ":one": 1,
        }
        conditions = [
            "attribute_exists(#pk)", "#pk = :pk", "#co = :co",
            "#job = :job", "#url = :old", "#s = :status",
        ]

        # The original five-field witness, including absence versus explicit
        # NULL/zero/False, must survive until the conditional UpdateItem.
        for i, field in enumerate(REPAIR_READER_FIELDS):
            name, value = f"#r{i}", f":r{i}"
            names[name] = field
            if field in expected_reader:
                values[value] = expected_reader[field]
                conditions.append(f"{name} = {value}")
            else:
                conditions.append(f"attribute_not_exists({name})")

        # Explicitly compare every protected field's original presence/value.
        # A new hold/request/evidence flag cannot appear between read and write.
        for i, field in enumerate(("fail_kind", "gate_reason") + REPAIR_PROTECTED_FIELDS):
            name, value = f"#p{i}", f":p{i}"
            names[name] = field
            if field in row:
                values[value] = row[field]
                conditions.append(f"{name} = {value}")
            else:
                conditions.append(f"attribute_not_exists({name})")

        updates = ("SET #url = :new, #s = :found, #count = :zero, "
                   "#rev = if_not_exists(#rev, :zero) + :one")
        if row["status"] == Status.ERROR.value:
            updates += " REMOVE #p0"  # exhausted fail_kind; never clear other kinds
        try:
            self._table.update_item(
                Key={"pk": pk},
                UpdateExpression=updates,
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def replace_noncanonical_jd_url(
        self, pk: str, new_url: str, *, expected_row: dict,
    ) -> bool:
        """Strong read then partial, no-upsert, presence-sensitive UpdateItem.

        A conditional expression can see row-local holds and reviewed guard
        changes, never the Redis submit_hold:<pk> key in the other backend.
        """
        if not canonical_repair_row_allowed(expected_row, pk):
            return False
        row = self._table.get_item(
            Key={"pk": pk}, ConsistentRead=True).get("Item")
        if (not canonical_repair_same_witness(row, expected_row)
                or not canonical_repair_row_allowed(row, pk)):
            return False
        patch, removes = canonical_repair_patch(row, new_url)
        names, values = {"#pk": "pk"}, {}
        conditions = ["attribute_exists(#pk)"]
        for i, field in enumerate(canonical_repair_witness_fields(expected_row)):
            attr, val = f"#w{i}", f":w{i}"
            names[attr] = field
            if field in expected_row:
                values[val] = expected_row[field]
                conditions.append(f"{attr} = {val}")
            else:
                conditions.append(f"attribute_not_exists({attr})")
        sets = []
        for i, (field, value) in enumerate(patch.items()):
            attr, val = f"#s{i}", f":s{i}"
            names[attr], values[val] = field, value
            sets.append(f"{attr} = {val}")
        remove_attrs = []
        for i, field in enumerate(removes):
            attr = f"#d{i}"
            names[attr] = field
            remove_attrs.append(attr)
        try:
            self._table.update_item(
                Key={"pk": pk},
                UpdateExpression=("SET " + ", ".join(sets) + " REMOVE "
                                  + ", ".join(remove_attrs)),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def add_missing_greenhouse_pin(
        self, pk: str, board: str, *, expected_row: dict,
    ) -> bool:
        """Strong row observation followed by a row-local, no-upsert pin patch.

        The independent Redis hold key does not exist in this backend. Only
        original application-row attributes are conditionable in DynamoDB.
        """
        if not tenant_pin_row_allowed(expected_row, pk, board):
            return False
        row = self._table.get_item(
            Key={"pk": pk}, ConsistentRead=True).get("Item")
        if (not tenant_pin_same_witness(row, expected_row)
                or not tenant_pin_row_allowed(row, pk, board)):
            return False
        fields = tenant_pin_witness_fields(expected_row)
        names = {"#pk": "pk", "#pin": "greenhouse_board"}
        values = {":board": board}
        conditions = ["attribute_exists(#pk)", "attribute_not_exists(#pin)"]
        for i, field in enumerate(fields):
            name, value = f"#w{i}", f":w{i}"
            names[name] = field
            if field in expected_row:
                conditions.append(f"{name} = {value}")
                values[value] = expected_row[field]
            else:
                conditions.append(f"attribute_not_exists({name})")
        try:
            self._table.update_item(
                Key={"pk": pk},
                UpdateExpression="SET #pin = :board",
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    def application_notes(self) -> dict:
        out, kwargs = {}, {}
        while True:
            response = self._table.scan(
                FilterExpression="begins_with(pk, :prefix)",
                ExpressionAttributeValues={":prefix": "meta#tracker#"}, **kwargs)
            for row in response.get("Items", []):
                out[row["pk"][13:]] = row.get("note", {})
            if "LastEvaluatedKey" not in response:
                return out
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def save_application_note(self, pk: str, note: dict) -> None:
        self._table.put_item(Item={"pk": f"meta#tracker#{pk}", "note": note})

    def find_by_jd_hash(self, jd_hash: str) -> str | None:
        """Return the pk of an existing row with this jd_hash, or None."""
        resp = self._table.query(
            IndexName=JD_HASH_INDEX,
            KeyConditionExpression=boto3.dynamodb.conditions.Key("jd_hash").eq(jd_hash),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0]["pk"] if items else None

    def query_status(self, status: Status) -> list[dict]:
        resp = self._table.query(
            IndexName=STATUS_INDEX,
            KeyConditionExpression=boto3.dynamodb.conditions.Key("status").eq(status.value),
        )
        return resp.get("Items", [])

    def all(self) -> list[dict]:
        rows, kwargs = [], {}
        while True:
            resp = self._table.scan(**kwargs)
            rows += [r for r in resp.get("Items", []) if not is_internal_pk(r.get("pk", ""))]
            if "LastEvaluatedKey" not in resp:
                return rows
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def try_increment_daily_cap(self, date_str: str, cap: int) -> bool:
        """Atomically reserve one submit slot for ``date_str``.

        Returns True if the increment stayed under ``cap`` (slot reserved),
        False if the cap is already reached. Called immediately before setting
        ``submitting`` so concurrent apply tasks cannot overshoot.
        """
        try:
            self._table.update_item(
                Key={"pk": f"{_CAP_PK}#{date_str}"},
                UpdateExpression="SET #c = if_not_exists(#c, :zero) + :one",
                ConditionExpression="attribute_not_exists(#c) OR #c < :cap",
                ExpressionAttributeNames={"#c": "count"},
                ExpressionAttributeValues={":zero": 0, ":one": 1, ":cap": cap},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
