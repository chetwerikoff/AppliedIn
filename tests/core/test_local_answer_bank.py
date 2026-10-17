"""Offline Markdown fact identity regressions (synthetic data only)."""

import pytest

from core.models import AnswerScope
from core.storage.local import MarkdownAnswerBank


def test_global_update_keeps_old_raw_key_and_row_count(tmp_path):
    path = tmp_path / "facts.md"
    bank = MarkdownAnswerBank(path)
    bank.put("Email", "old@example.test", AnswerScope.GLOBAL)
    bank.put("Unrelated", "unchanged", AnswerScope.GLOBAL)
    bank.put("Email ✱", "updated@example.test", AnswerScope.GLOBAL)
    reloaded = MarkdownAnswerBank(path)
    assert reloaded.lookup("Email *", "example-co") == "updated@example.test"
    assert reloaded.all_facts("example-co") == {
        "Email": "updated@example.test", "Unrelated": "unchanged",
    }
    assert len(reloaded._load()["global"]) == 2
    assert "- **Email**: updated@example.test" in path.read_text()
    assert "- **Email ✱**:" not in path.read_text()


def test_new_company_canonical_key_shadows_equivalent_global(tmp_path):
    path = tmp_path / "facts.md"
    bank = MarkdownAnswerBank(path)
    bank.put("Email", "global@example.test", AnswerScope.GLOBAL)
    bank.put("Email ✱", "company@example.test", AnswerScope.COMPANY,
             company="example-co")
    reopened = MarkdownAnswerBank(path)
    assert reopened.lookup("Email *", "example-co") == "company@example.test"
    assert reopened.lookup("Email *", "other-co") == "global@example.test"
    assert reopened.all_facts("example-co") == {"Email": "company@example.test"}
    assert reopened.all_facts("other-co") == {"Email": "global@example.test"}
    assert reopened._load()["company#example-co"] == [
        ("Email", "company@example.test")
    ]


def test_legacy_decorated_company_raw_key_survives_update(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text(
        "# AppliedIn facts\n\n## global\n"
        "- **Email**: global@example.test\n"
        "- **Other**: intact\n\n"
        "## company: example-co\n"
        "- **Email ✱**: old@example.test\n"
    )
    bank = MarkdownAnswerBank(path)
    assert bank.lookup("Email *", "example-co") == "old@example.test"
    bank.put("Email *", "new@example.test", AnswerScope.COMPANY,
             company="example-co")
    bank = MarkdownAnswerBank(path)
    assert bank.all_facts("example-co") == {
        "Other": "intact", "Email ✱": "new@example.test",
    }
    assert bank.all_facts("other-co")["Email"] == "global@example.test"
    assert bank._load()["company#example-co"] == [
        ("Email ✱", "new@example.test")
    ]


def test_first_insert_canonical_and_literal_inner_stars_survive_reload(tmp_path):
    path = tmp_path / "facts.md"
    bank = MarkdownAnswerBank(path)
    inputs = {
        "First Name*": "Test User",
        "First* Name": "Test* User",
        "Literal** Inner": "Test** Inner",
        "Inner** Colon": "Test**:suffix",
    }
    for q, answer in inputs.items():
        bank.put(q, answer, AnswerScope.GLOBAL)
    raw = path.read_text()
    assert "- **First Name**: Test User" in raw
    assert "- **First Name***:" not in raw
    bank = MarkdownAnswerBank(path)
    assert bank._load()["global"] == [
        ("First Name", "Test User"),
        ("First* Name", "Test* User"),
        ("Literal** Inner", "Test** Inner"),
        ("Inner** Colon", "Test**:suffix"),
    ]
    for q, answer in inputs.items():
        assert bank.lookup(q, "example-co") == answer


def test_canonical_new_unicode_ascii_and_empty_labels(tmp_path):
    path = tmp_path / "facts.md"
    bank = MarkdownAnswerBank(path)
    bank.put("Email *", "a@example.test", AnswerScope.GLOBAL)
    bank.put("Email ✱", "b@example.test", AnswerScope.GLOBAL)
    bank.put("*", "first blank", AnswerScope.GLOBAL)
    bank.put("", "second blank", AnswerScope.GLOBAL)
    bank = MarkdownAnswerBank(path)
    assert bank._load()["global"] == [
        ("Email", "b@example.test"), ("", "second blank"),
    ]
    assert bank.lookup("*", "example-co") == "second blank"
    assert bank.lookup("", "example-co") == "second blank"
    assert "- ****: second blank" in path.read_text()


def test_existing_trailing_star_row_reloads_untouched_answer(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text(
        "# AppliedIn facts\n\n## global\n"
        "- **First Name***: Test User\n"
        "- **Inner**Star**: Test** User\n"
    )
    bank = MarkdownAnswerBank(path)
    assert bank._load()["global"] == [
        ("First Name*", "Test User"), ("Inner**Star", "Test** User"),
    ]
    bank.put("First Name", "New User", AnswerScope.GLOBAL)
    bank = MarkdownAnswerBank(path)
    assert bank._load()["global"] == [
        ("First Name*", "New User"), ("Inner**Star", "Test** User"),
    ]



def test_hand_edited_legacy_delimiter_without_space_is_readable(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text("# AppliedIn facts\n\n## global\n- **Email**:old@example.test\n")
    bank = MarkdownAnswerBank(path)
    assert bank.lookup("Email ✱", "example-co") == "old@example.test"
    bank.put("Email*", "new@example.test", AnswerScope.GLOBAL)
    assert MarkdownAnswerBank(path)._load()["global"] == [
        ("Email", "new@example.test")
    ]



@pytest.mark.parametrize("question,answer", [
    ("Email", "**Role**: Test User"),
    ("Email", "Test**: User"),
    ("First* Name", "Test* User"),
    ("Role**: Name", "Test**: User"),
    (r"Role**\: Name", r"Test**: \ User"),
])
def test_delimiter_like_markdown_question_and_answer_round_trip(tmp_path, question, answer):
    """An answer containing **: must not become part of the stored raw key."""
    path = tmp_path / "facts.md"
    bank = MarkdownAnswerBank(path)
    bank.put(question, answer, AnswerScope.GLOBAL)
    reopened = MarkdownAnswerBank(path)
    assert reopened._load()["global"] == [(question, answer)]
    assert reopened.lookup(question, "example-co") == answer
    # Matched updates must preserve the exact key through another disk write.
    reopened.put(question, answer + "!", AnswerScope.GLOBAL)
    again = MarkdownAnswerBank(path)
    assert again._load()["global"] == [(question, answer + "!")]
    assert again.lookup(question, "example-co") == answer + "!"


def test_legacy_trailing_star_with_delimiter_like_approved_answer(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text(
        "# AppliedIn facts\n\n## global\n"
        "- **First Name***: **Role**: Test User\n"
        "- **Email ✱**: Test**: User\n"
    )
    reopened = MarkdownAnswerBank(path)
    assert reopened._load()["global"] == [
        ("First Name*", "**Role**: Test User"),
        ("Email ✱", "Test**: User"),
    ]
    assert reopened.lookup("First Name", "example-co") == "**Role**: Test User"
    reopened.put("First Name*", "Test**: User", AnswerScope.GLOBAL)
    assert MarkdownAnswerBank(path)._load()["global"] == [
        ("First Name*", "Test**: User"),
        ("Email ✱", "Test**: User"),
    ]
