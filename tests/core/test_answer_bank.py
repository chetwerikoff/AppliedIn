import pytest
from botocore.exceptions import ClientError

from core.models import AnswerScope
from core.storage.answer_bank import (
    AnswerBank, AnswerBankAmbiguityError, AnswerBankConflictError,
)

from .conftest import make_answer_bank_table


def test_global_fact_reused_across_companies(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.put("Do you require sponsorship?", "Yes, H-1B", AnswerScope.GLOBAL)
    # different company, synonym-spaced/cased label -> still resolves
    assert bank.lookup("do you require sponsorship", "Stripe") == "Yes, H-1B"
    assert bank.lookup("Do You Require Sponsorship?", "Acme") == "Yes, H-1B"


def test_company_scope_shadows_global_only_for_that_company(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.put("Why us?", "generic", AnswerScope.GLOBAL)
    bank.put("Why us?", "I love Stripe payments", AnswerScope.COMPANY, company="Stripe")
    assert bank.lookup("why us", "Stripe") == "I love Stripe payments"
    assert bank.lookup("why us", "Acme") == "generic"


def test_seed_global(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.seed_global({"Notice period": "30 days", "Salary": "$200k"})
    assert bank.lookup("notice period?", "Any") == "30 days"



def _legacy(bank, pk="global", *, sk="c experience",
            raw="C++ experience", answer="old"):
    bank._table.put_item(Item={
        "pk": pk, "sk": sk, "question_raw": raw, "answer": answer,
        "source": "legacy", "use_count": 3,
    })


def test_conditional_matched_update_preserves_cloud_raw_metadata_and_extra(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.put("Email", "old@example.test", AnswerScope.GLOBAL,
             source="seed", approved_at="original")
    bank._table.update_item(
        Key={"pk": "global", "sk": "email"},
        UpdateExpression="SET #extra = :data, #count = :five",
        ExpressionAttributeNames={"#extra": "other_attribute", "#count": "use_count"},
        ExpressionAttributeValues={":data": "untouched", ":five": 5},
    )
    bank.put("Email ✱", "new@example.test", AnswerScope.GLOBAL,
             source="gate_approval", approved_at="2026-01-01T00:00:00Z")
    row = bank._table.get_item(Key={"pk": "global", "sk": "email"})["Item"]
    assert (row["pk"], row["sk"], row["question_raw"]) == ("global", "email", "Email")
    assert row["answer"] == "new@example.test"
    assert row["source"] == "gate_approval"
    assert row["approved_at"] == "2026-01-01T00:00:00Z"
    assert row["use_count"] == 0
    assert row["other_attribute"] == "untouched"
    assert len(bank._scope_facts("global")) == 1
    assert bank.all_facts("example-co") == {"Email": "new@example.test"}


def test_cloud_new_raw_company_shadows_global_by_comparison(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.put("Email", "global@example.test", AnswerScope.GLOBAL)
    bank.put("Email ✱", "company@example.test", AnswerScope.COMPANY,
             company="example-co")
    assert bank.lookup("Email *", "example-co") == "company@example.test"
    assert bank.lookup("Email *", "other-co") == "global@example.test"
    row = bank._table.get_item(
        Key={"pk": "company#example-co", "sk": "email"}
    )["Item"]
    assert row["question_raw"] == "Email ✱"
    assert bank.all_facts("example-co") == {"Email ✱": "company@example.test"}
    assert bank.all_facts("other-co") == {"Email": "global@example.test"}


def test_legacy_cplusplus_row_updates_in_place_and_never_answers_c(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank)
    assert bank.lookup("C++ experience", "example-co") == "old"
    assert bank.lookup("C experience", "example-co") is None
    bank.put("C++ experience", "updated", AnswerScope.GLOBAL,
             source="gate_approval", approved_at="synthetic")
    row = bank._table.get_item(Key={"pk": "global", "sk": "c experience"})["Item"]
    assert row["question_raw"] == "C++ experience"
    assert row["answer"] == "updated"
    assert row["source"] == "gate_approval"
    assert row["approved_at"] == "synthetic"
    assert row["use_count"] == 0
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c++ experience"}
    ).get("Item") is None


def test_revised_incompatible_occupant_cannot_mask_valid_old_candidate(
        aws, monkeypatch):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank, "company#example-co", sk="c++ experience",
            raw="Unrelated", answer="wrong")
    _legacy(bank, "company#example-co", answer="company old")
    bank.put("C++ experience", "global", AnswerScope.GLOBAL)
    reads = []
    get = bank._table.get_item

    def observe(**kwargs):
        reads.append(kwargs)
        return get(**kwargs)

    monkeypatch.setattr(bank._table, "get_item", observe)
    assert bank.lookup("C++ experience", "example-co") == "company old"
    assert [(call["Key"]["pk"], call["Key"]["sk"]) for call in reads] == [
        ("company#example-co", "c++ experience"),
        ("company#example-co", "c experience"),
    ]
    assert all(call["ConsistentRead"] is True for call in reads)
    bank.put("C++ experience", "company new", AnswerScope.COMPANY,
             company="example-co")
    assert get(Key={"pk": "company#example-co",
                    "sk": "c experience"})["Item"]["answer"] == "company new"
    assert get(Key={"pk": "company#example-co",
                    "sk": "c++ experience"})["Item"]["answer"] == "wrong"
    assert bank.lookup("C++ experience", "other-co") == "global"


@pytest.mark.parametrize("same_answer", [True, False])
@pytest.mark.parametrize("scope_pk", ["global", "company#example-co"])
def test_same_scope_cloud_aliases_fail_closed_even_if_shadowed(
        aws, same_answer, scope_pk):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    if scope_pk != "global":
        bank.put("C++ experience", "global", AnswerScope.GLOBAL)
    _legacy(bank, scope_pk, answer="old")
    _legacy(bank, scope_pk, sk="c++ experience",
            answer="old" if same_answer else "conflicting")
    with pytest.raises(AnswerBankAmbiguityError, match="Ambiguous answer bank"):
        bank.lookup("C++ experience", "example-co")
    with pytest.raises(AnswerBankAmbiguityError, match="Ambiguous answer bank"):
        bank.all_facts("example-co")
    with pytest.raises(AnswerBankAmbiguityError, match="Ambiguous answer bank"):
        bank.put("C++ experience", "new", AnswerScope.GLOBAL if scope_pk == "global"
                 else AnswerScope.COMPANY, company="example-co")


def test_stale_matched_update_does_not_replace_intervening_writer(aws, monkeypatch):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.put("Email", "original", AnswerScope.GLOBAL)
    real_update = bank._table.update_item

    def concurrent_write(**kwargs):
        real_update(
            Key={"pk": "global", "sk": "email"},
            UpdateExpression="SET #answer = :answer",
            ExpressionAttributeNames={"#answer": "answer"},
            ExpressionAttributeValues={":answer": "intervening"},
        )
        return real_update(**kwargs)

    monkeypatch.setattr(bank._table, "update_item", concurrent_write)
    with pytest.raises(AnswerBankConflictError, match="conditional update"):
        bank.put("Email ✱", "stale", AnswerScope.GLOBAL)
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "email"}
    )["Item"]["answer"] == "intervening"


def test_incompatible_revised_cloud_row_cannot_be_overwritten(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank, sk="c++ experience", raw="Different", answer="keep")
    with pytest.raises(AnswerBankConflictError, match="incompatible"):
        bank.put("C++ experience", "do not overwrite", AnswerScope.GLOBAL)
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c++ experience"}
    )["Item"]["answer"] == "keep"


def test_seed_rejects_free_revised_target_when_legacy_is_occupied(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank)
    with pytest.raises(AnswerBankConflictError, match="Seed candidate"):
        bank.seed_global({"C++ experience": "new"})
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c experience"}
    )["Item"]["answer"] == "old"
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c++ experience"}
    ).get("Item") is None
    assert len(bank._scope_facts("global")) == 1


def test_seed_occupied_incompatible_candidate_and_same_batch_aliases(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank, raw="Unrelated")
    with pytest.raises(AnswerBankConflictError, match="Seed candidate"):
        bank.seed_global({"C++ experience": "new"})
    with pytest.raises(AnswerBankConflictError, match="duplicate semantic"):
        bank.seed_global({"Email": "first", "Email ✱": "second"})
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "email"}
    ).get("Item") is None


def test_seed_transaction_protects_intervening_legacy_writer(aws, monkeypatch):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    real_insert = bank._conditional_insert

    def intervening_legacy(item, *, legacy_row):
        _legacy(bank, answer="intervening")
        return real_insert(item, legacy_row=legacy_row)

    monkeypatch.setattr(bank, "_conditional_insert", intervening_legacy)
    with pytest.raises(AnswerBankConflictError, match="candidate"):
        bank.seed_global({"C++ experience": "new"})
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c experience"}
    )["Item"]["answer"] == "intervening"
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c++ experience"}
    ).get("Item") is None


def test_seed_new_metadata_and_cloud_empty_key_boundary(aws):
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    bank.seed_global({"Notice period": "2 weeks"}, source="bootstrap")
    row = bank._table.get_item(
        Key={"pk": "global", "sk": "notice period"}
    )["Item"]
    assert row["source"] == "bootstrap"
    assert row["use_count"] == 0
    assert "approved_at" not in row
    with pytest.raises(ClientError):
        bank.put("*", "invalid cloud empty key", AnswerScope.GLOBAL)


def test_dual_key_seed_transaction_accepts_resource_native_values(aws, monkeypatch):
    """Table.meta.client handles AttributeValue serialization once, for both actions."""
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    client = bank._table.meta.client
    real_transaction = client.transact_write_items
    calls = []

    def inspect(**kwargs):
        (condition, put) = kwargs["TransactItems"]
        assert condition["ConditionCheck"] == {
            "TableName": "answer_bank",
            "Key": {"pk": "global", "sk": "c experience"},
            "ConditionExpression": "attribute_not_exists(pk)",
        }
        assert put["Put"] == {
            "TableName": "answer_bank",
            "Item": {
                "pk": "global", "sk": "c++ experience",
                "question_raw": "C++ experience", "answer": "fresh",
                "source": "fixture", "use_count": 0,
            },
            "ConditionExpression": "attribute_not_exists(pk)",
        }
        calls.append(kwargs)
        return real_transaction(**kwargs)

    monkeypatch.setattr(client, "transact_write_items", inspect)
    bank.seed_global({"C++ experience": "fresh"}, source="fixture")
    assert len(calls) == 1
    assert bank.lookup("C++ experience", "example-co") == "fresh"
    assert bank.lookup("C experience", "example-co") is None


def test_dual_key_insert_checks_existing_incompatible_legacy_row(
        aws, monkeypatch):
    """An unrelated legacy occupant must remain stable across the atomic insert."""
    make_answer_bank_table()
    bank = AnswerBank("answer_bank")
    _legacy(bank, raw="Different", answer="old")
    client = bank._table.meta.client
    real_transaction = client.transact_write_items
    calls = []

    def inspect(**kwargs):
        (condition, put) = kwargs["TransactItems"]
        check = condition["ConditionCheck"]
        assert check["Key"] == {"pk": "global", "sk": "c experience"}
        assert check["ConditionExpression"] == "#raw = :raw AND #answer = :old"
        assert check["ExpressionAttributeValues"] == {
            ":raw": "Different", ":old": "old",
        }
        assert check["ExpressionAttributeNames"] == {
            "#raw": "question_raw", "#answer": "answer",
        }
        assert put["Put"]["Item"]["sk"] == "c++ experience"
        assert put["Put"]["ConditionExpression"] == "attribute_not_exists(pk)"
        calls.append(kwargs)
        return real_transaction(**kwargs)

    monkeypatch.setattr(client, "transact_write_items", inspect)
    bank.put("C++ experience", "new", AnswerScope.GLOBAL)
    assert len(calls) == 1
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c experience"}
    )["Item"]["answer"] == "old"
    assert bank._table.get_item(
        Key={"pk": "global", "sk": "c++ experience"}
    )["Item"]["answer"] == "new"
    assert bank.lookup("C++ experience", "example-co") == "new"
    assert bank.lookup("C experience", "example-co") is None
