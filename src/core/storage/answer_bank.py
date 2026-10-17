"""DynamoDB answer bank: two scopes, exact historical identities and approved facts.

New rows use bounded normalized sort keys. Existing rows retain their original
(pk, sk, question_raw), including keys written by the old punctuation normalizer.
No table scans or migrations are performed.
"""

from __future__ import annotations

import re

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from ..ids import make_pk, normalize_label
from ..models import AnswerScope
from .base import AbstractAnswerBank

_GLOBAL_PK = "global"
_WHITESPACE = re.compile(r"\s+")
_LEGACY_PUNCT = re.compile(r"[^\w\s]")
_SERIALIZER = TypeSerializer()


class AnswerBankAmbiguityError(RuntimeError):
    """Distinct rows in one scope represent the same approved fact."""


class AnswerBankConflictError(RuntimeError):
    """A conditional answer-bank write could not preserve stored identity."""


def _company_pk(company: str) -> str:
    return f"company#{company.strip().lower()}"


def _legacy_label(question: str) -> str:
    """Exactly the previous cloud sk algorithm; NOT the revised comparator."""
    return _WHITESPACE.sub(" ", _LEGACY_PUNCT.sub(" ", question.lower())).strip()


def _candidates(question: str) -> list[str]:
    revised, legacy = normalize_label(question), _legacy_label(question)
    return [revised] if revised == legacy or not legacy else [revised, legacy]


def _typed(data: dict) -> dict:
    return {key: _SERIALIZER.serialize(value) for key, value in data.items()}


def _conditional_conflict(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in {
        "ConditionalCheckFailedException", "TransactionCanceledException",
    }


class AnswerBank(AbstractAnswerBank):
    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def _candidate_rows(self, pk: str, question: str) -> dict[str, dict]:
        """Read both possible identities, even when the revised one is occupied."""
        rows = {}
        for sk in _candidates(question):
            item = self._table.get_item(
                Key={"pk": pk, "sk": sk}, ConsistentRead=True,
            ).get("Item")
            if item is not None:
                rows[sk] = item
        return rows

    @staticmethod
    def _matching(rows: dict[str, dict], question: str) -> dict | None:
        expected = normalize_label(question)
        matches = [row for row in rows.values()
                   if isinstance(row.get("question_raw"), str)
                   and normalize_label(row["question_raw"]) == expected]
        if len(matches) > 1:
            raise AnswerBankAmbiguityError(
                "Ambiguous answer bank facts within one scope"
            )
        return matches[0] if matches else None

    def lookup(self, question: str, company: str) -> str | None:
        """Company first; neither scope may resolve an ambiguous fact."""
        for pk in (_company_pk(company), _GLOBAL_PK):
            row = self._matching(self._candidate_rows(pk, question), question)
            if row is not None:
                return row["answer"]
        return None

    def _conditional_update(self, row: dict, *, answer: str, source: str,
                            approved_at: str) -> None:
        """Update value/metadata only, preserving raw key and unrelated fields."""
        names = {
            "#raw": "question_raw", "#answer": "answer",
            "#source": "source", "#approved": "approved_at",
            "#count": "use_count",
        }
        values = {
            ":raw": row["question_raw"], ":previous": row["answer"],
            ":answer": answer, ":source": source,
            ":approved": approved_at, ":zero": 0,
        }
        predicates = [
            "attribute_exists(pk)", "#raw = :raw", "#answer = :previous",
        ]
        for alias, column in (("#source", "source"),
                              ("#approved", "approved_at"),
                              ("#count", "use_count")):
            if column in row:
                prior = ":previous_" + column
                values[prior] = row[column]
                predicates.append(f"{alias} = {prior}")
            else:
                predicates.append(f"attribute_not_exists({alias})")
        try:
            self._table.update_item(
                Key={"pk": row["pk"], "sk": row["sk"]},
                UpdateExpression=(
                    "SET #answer = :answer, #source = :source, "
                    "#approved = :approved, #count = :zero"
                ),
                ConditionExpression=" AND ".join(predicates),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if _conditional_conflict(exc):
                raise AnswerBankConflictError(
                    "Answer bank row changed during conditional update"
                ) from exc
            raise

    def _conditional_insert(self, item: dict, *, legacy_row: dict | None) -> None:
        """Conditionally insert at the revised key, guarding the legacy peer.

        When there are two distinct candidates, a transaction prevents an
        intervening legacy writer from creating a semantic twin. An incompatible
        existing legacy row may coexist only while its identity remains stable.
        """
        legacy = _legacy_label(item["question_raw"])
        revised = item["sk"]
        try:
            if not legacy or legacy == revised:
                self._table.put_item(
                    Item=item, ConditionExpression="attribute_not_exists(pk)",
                )
                return
            check = {
                "TableName": self._table.name,
                "Key": _typed({"pk": item["pk"], "sk": legacy}),
                "ConditionExpression": "attribute_not_exists(pk)",
            }
            if legacy_row is not None:
                check["ConditionExpression"] = "#raw = :raw AND #answer = :old"
                check["ExpressionAttributeNames"] = {
                    "#raw": "question_raw", "#answer": "answer",
                }
                check["ExpressionAttributeValues"] = _typed({
                    ":raw": legacy_row["question_raw"],
                    ":old": legacy_row["answer"],
                })
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {"ConditionCheck": check},
                    {"Put": {
                        "TableName": self._table.name,
                        "Item": _typed(item),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }},
                ],
            )
        except ClientError as exc:
            if _conditional_conflict(exc):
                raise AnswerBankConflictError(
                    "Answer bank candidate was occupied or changed during insert"
                ) from exc
            raise

    def put(
        self,
        question: str,
        answer: str,
        scope: AnswerScope,
        *,
        company: str | None = None,
        source: str = "whatsapp_reply",
        approved_at: str = "",
    ) -> None:
        if scope is AnswerScope.COMPANY and not company:
            raise ValueError("company is required for COMPANY scope")
        pk = _company_pk(company) if scope is AnswerScope.COMPANY else _GLOBAL_PK
        rows = self._candidate_rows(pk, question)
        matched = self._matching(rows, question)
        if matched is not None:
            self._conditional_update(
                matched, answer=answer, source=source, approved_at=approved_at,
            )
        else:
            sk = normalize_label(question)
            if sk in rows:
                raise AnswerBankConflictError(
                    "Answer bank revised key belongs to an incompatible fact"
                )
            legacy = _legacy_label(question)
            self._conditional_insert({
                "pk": pk, "sk": sk, "question_raw": question,
                "answer": answer, "source": source,
                "approved_at": approved_at, "use_count": 0,
            }, legacy_row=rows.get(legacy))
        # A writer between the first candidate reads and commit must not turn
        # this operation into a silently accepted ambiguous match.
        self._matching(self._candidate_rows(pk, question), question)

    def seed_global(self, entries: dict[str, str], *, source: str = "seed") -> None:
        """Bootstrap only: never overwrite either old or revised candidate key."""
        seen: set[str] = set()
        for question in entries:
            revised = normalize_label(question)
            if revised in seen:
                raise AnswerBankConflictError(
                    "Seed includes duplicate semantic fact labels"
                )
            seen.add(revised)
        for question, answer in entries.items():
            candidates = _candidates(question)
            rows = self._candidate_rows(_GLOBAL_PK, question)
            if rows:
                raise AnswerBankConflictError(
                    "Seed candidate already occupies a cloud fact identity"
                )
            item = {
                "pk": _GLOBAL_PK, "sk": candidates[0],
                "question_raw": question, "answer": answer,
                "source": source, "use_count": 0,
            }
            self._conditional_insert(item, legacy_row=None)

    def _scope_facts(self, pk: str) -> dict[str, tuple[str, str]]:
        """Read one existing scope; refuse any alias, even with equal answers."""
        by_label: dict[str, tuple[str, str]] = {}
        start = None
        while True:
            params = {
                "KeyConditionExpression": boto3.dynamodb.conditions.Key("pk").eq(pk),
                "ConsistentRead": True,
            }
            if start is not None:
                params["ExclusiveStartKey"] = start
            resp = self._table.query(**params)
            for item in resp.get("Items", []):
                raw = item.get("question_raw", item["sk"])
                label = normalize_label(raw)
                if label in by_label:
                    raise AnswerBankAmbiguityError(
                        "Ambiguous answer bank facts within one scope"
                    )
                by_label[label] = (raw, item["answer"])
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        return by_label

    def all_facts(self, company: str) -> dict[str, str]:
        selected = self._scope_facts(_GLOBAL_PK)
        selected.update(self._scope_facts(_company_pk(company)))
        return {raw: answer for raw, answer in selected.values()}


__all__ = [
    "AnswerBank", "AnswerBankAmbiguityError", "AnswerBankConflictError", "make_pk",
]
