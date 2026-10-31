"""Issue #282: default-'on' native radio values are not option identities.

All captures and writes here are synthetic, local, and offline. No live browser,
network access, owner records, real application or Submit action is involved.
"""
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom
from tests.tools.test_browser_bootstrap import (
    PK, SECURE_URL, field, secure_inventory, secure_world,
)
from tests.tools.test_browser_application_entry import dom, entry
from tests.tools.test_browser_final_submit_grants import _field, _page


DECLINE = "Decline to answer"
AFFIRM = "Woman"
QUESTION = "Gender"


def native_radios():
    """Sanitized Ashby shape: no value/data-value attribute, IDL value 'on'."""
    def member(caption, selector):
        return field(
            caption, selector, tag="input", type="radio", required=True,
            name="eeo_gender", question=QUESTION, value="on", has_value=True,
            value_attr_present=False, checked=False)
    return secure_inventory(
        member(DECLINE, "#decline"), member(AFFIRM, "#affirm"))


def grant(page, answer=DECLINE):
    bound = forms._bound_target(page, page["controls"][0])
    return {
        "value": answer, "url": page["url"],
        **{key: bound[key] for key in (
            "selector", "label", "question", "tag", "type",
            "form_selector", "form_action", "radio_name", "radio_members")},
    }


def install_grant(monkeypatch, page, answer=DECLINE):
    bound = grant(page, answer)
    browser, _, row = secure_world(
        monkeypatch, page, grants={QUESTION: bound})
    browser.allow_mutations = True
    return browser, row


def test_entry_reader_records_value_attribute_without_rewriting_idl_value():
    # Only the application ENTRY_PAGE gains this observation. An absent HTML
    # attribute must not be fabricated into a distinct machine value.
    assert "value_attr_present:e.hasAttribute('value')" in browser_skill_dom.ENTRY_PAGE
    assert "value_attr_present:e.hasAttribute('value')" not in browser_skill_dom.PAGE
    source = forms.ENTRY_PAGE.replace(
        "(() => {\n",
        "(() => {\n"
        "  for (const e of document.querySelectorAll('input')) {\n"
        "    if (e.type === 'radio') e.value = 'on';\n"
        "  }\n", 1)
    observed = dom({
        "root": False, "formless": True, "survey": True,
        "surveyChoices": "synthetic", "url": entry("ashby"),
    }, source)
    radios = [c for c in observed["controls"] if c.get("type") == "radio"]
    assert len(radios) == 2
    assert all(c["tag"] == "input" and c["value"] == "on"
               and c["value_attr_present"] is False for c in radios)
    assert len({c["selector"] for c in radios}) == 2


def test_implicit_on_group_is_accepted_but_on_is_never_a_choice():
    page = native_radios()
    members = forms._radio_group(page, page["controls"][0])
    assert [member["label"] for member in members] == [DECLINE, AFFIRM]
    assert [member["value"] for member in members] == ["on", "on"]
    assert forms._implicit_on_radios(members)
    assert forms._radio_answer_matches(members, "on") == []
    assert [c["selector"] for c in forms._radio_answer_matches(
        members, "   DECLINE to answer ")] == ["#decline"]
    bound = forms._bound_target(page, members[1])
    assert bound["selector"] == "#decline"
    assert [(m["selector"], m["caption"]) for m in bound["radio_members"]] == [
        ("#decline", DECLINE), ("#affirm", AFFIRM)]


@pytest.mark.parametrize("change,predicate", [
    ("target_tag", "target.tag"),
    ("target_type", "target.type"),
    ("target_name", "target.name"),
    ("target_form", "target.form"),
    ("target_question", "target.question"),
    ("too_few", "members.count"),
    ("member_tag", "member.tag"),
    ("member_not_in_form", "member.in_form"),
    ("member_question", "member.question"),
    ("member_action", "member.form_action"),
    ("missing_selector", "member.selector"),
    ("missing_caption", "member.label"),
    ("missing_value", "member.value"),
    ("duplicate_selector", "selectors.distinct"),
    ("duplicate_caption", "captions.distinct"),
    ("duplicate_explicit_value", "values.distinct"),
    ("mixed_default_on", "values.distinct"),
    ("missing_attribute_evidence", "values.distinct"),
])
def test_every_other_radio_predicate_still_refuses_with_name_only(
        change, predicate, caplog):
    page = native_radios()
    first, second = page["controls"][:2]
    if change == "target_tag":
        first["tag"] = "div"
    elif change == "target_type":
        first["type"] = "checkbox"
    elif change == "target_name":
        first["name"] = ""
    elif change == "target_form":
        first["form_selector"] = ""
    elif change == "target_question":
        first["question"] = ""
    elif change == "too_few":
        page["controls"].remove(second)
    elif change == "member_tag":
        second["tag"] = "div"
    elif change == "member_not_in_form":
        second["in_form"] = False
    elif change == "member_question":
        second["question"] = "Another question"
    elif change == "member_action":
        second["form_action"] = "https://example-co.test/other"
    elif change == "missing_selector":
        second["selector"] = ""
    elif change == "missing_caption":
        second["label"] = ""
    elif change == "missing_value":
        second["value"] = ""
    elif change == "duplicate_selector":
        second["selector"] = first["selector"]
    elif change == "duplicate_caption":
        second["label"] = "  DECLINE TO  ANSWER  "
    elif change == "duplicate_explicit_value":
        first.update(value="same", value_attr_present=True)
        second.update(value="same", value_attr_present=True)
    elif change == "mixed_default_on":
        second["value_attr_present"] = True
    elif change == "missing_attribute_evidence":
        first.pop("value_attr_present")

    with caplog.at_level(logging.INFO):
        with pytest.raises(forms.Gate) as refusal:
            forms._radio_group(page, first)
    expected_message = (
        "A native radio group needs a form, name and visible question."
        if predicate.startswith("target.")
        else "Radio group members or captions are incomplete or ambiguous.")
    assert str(refusal.value) == expected_message
    diagnostics = [r.getMessage() for r in caplog.records
                   if "radio group failed predicate=" in r.getMessage()]
    assert diagnostics == [f"radio group failed predicate={predicate}"]
    assert not any(text in diagnostics[0] for text in (
        DECLINE, AFFIRM, QUESTION, "#decline", "#affirm"))


def test_rooted_formless_radio_group_keeps_complete_bound_identity():
    first = _field(
        selector="#decline", label=DECLINE, value="on", has_value=True,
        type="radio", name="eeo_gender", question=QUESTION, required=True,
        in_form=False, form_selector="", form_action="",
        value_attr_present=False, checked=False)
    second = _field(
        selector="#affirm", label=AFFIRM, value="on", has_value=True,
        type="radio", name="eeo_gender", question=QUESTION, required=True,
        in_form=False, form_selector="", form_action="",
        value_attr_present=False, checked=False)
    page = _page(first, second, rooted=True)
    assert len(forms._radio_group(page, first)) == 2
    bound = forms._bound_target(page, second)
    assert bound["selector"] == first["selector"]
    assert bound["owner_kind"] == "ashby-formless-root"
    assert bound["form_selector"] == "#application-root"
    assert [(m["selector"], m["caption"]) for m in bound["radio_members"]] == [
        ("#decline", DECLINE), ("#affirm", AFFIRM)]


@pytest.mark.asyncio
async def test_granted_decline_writes_checked_selector_and_verifies_at_submit(
        monkeypatch):
    page = native_radios()
    browser, row = install_grant(monkeypatch, page)
    original = deepcopy(page)
    assert forms._approved_native_choice(page, page["controls"][0], PK) == (
        DECLINE, page["controls"][0])
    filled = {}
    receipt = await forms.execute(
        browser, page, {"action": "choose", "selector": "#decline",
                        "fact": QUESTION}, facts={}, filled=filled,
        resume_path="", company="example-co", jd_text="",
        resume_tex="", github="", pk=PK, job_url=SECURE_URL)
    assert ("click", "#decline") in browser.calls
    assert filled == {"#decline": "on"}
    assert receipt["approved_value"] == DECLINE
    assert [c["selector"] for c in page["controls"]
            if c.get("type") == "radio" and c.get("checked")] == ["#decline"]
    assert grant(page) == grant(original)

    guarded = forms._ApprovedFormValues(dict(filled), PK, {})
    forms.check_form(page, guarded, uploaded=True, receipts=[receipt])
    forms.check_approvals(PK, [receipt], page)
    assert row["status"] == "submitting" and not row.get("possible_submission")


@pytest.mark.asyncio
async def test_affirmative_ungranted_choice_never_writes(monkeypatch):
    page = native_radios()
    browser, _, row = secure_world(monkeypatch, page)
    browser.allow_mutations = True
    assert row["human_approved_answers"] == {}
    assert forms._approved_native_choice(page, page["controls"][0], PK) is None
    with pytest.raises(forms.Gate):
        await forms.execute(
            browser, page, {"action": "choose", "selector": "#affirm",
                            "fact": QUESTION}, facts={}, filled={},
            resume_path="", company="example-co", jd_text="",
            resume_tex="", github="", pk=PK, job_url=SECURE_URL)
    assert not any(call[0] in {"click", "fill", "select", "upload"}
                   for call in browser.calls)


@pytest.mark.asyncio
async def test_default_on_readback_rejects_the_other_checked_selector(monkeypatch):
    page = native_radios()
    browser, _ = install_grant(monkeypatch, page)
    original = browser.call

    async def wrong_click(*args, **kwargs):
        response = await original(*args, **kwargs)
        if args[0] == "click":
            page["controls"][0]["checked"] = False
            page["controls"][1]["checked"] = True
        return response

    browser.call = wrong_click
    with pytest.raises(forms.Gate, match="readback"):
        await forms.execute(
            browser, page, {"action": "choose", "selector": "#decline",
                            "fact": QUESTION}, facts={}, filled={},
            resume_path="", company="example-co", jd_text="",
            resume_tex="", github="", pk=PK, job_url=SECURE_URL)
    assert ("click", "#decline") in browser.calls


def test_two_checked_default_on_members_fail_exactly_one_at_final_guard(
        monkeypatch):
    page = native_radios()
    browser, _ = install_grant(monkeypatch, page)
    first, second = page["controls"][:2]
    first["checked"] = second["checked"] = True
    filled = forms._ApprovedFormValues({"#decline": "on"}, PK, {})
    with pytest.raises(forms.Gate, match="Native radio group has conflicting checked members"):
        forms.check_form(page, filled, uploaded=True)
    assert not [call for call in browser.calls if call[0] == "click"]


def test_final_guard_does_not_treat_other_default_on_member_as_granted(
        monkeypatch):
    page = native_radios()
    install_grant(monkeypatch, page)
    page["controls"][1]["checked"] = True
    filled = forms._ApprovedFormValues({"#affirm": "on"}, PK, {})
    with pytest.raises(forms.Gate, match="native selected member is not the approved answer"):
        forms.check_form(page, filled, uploaded=True)
