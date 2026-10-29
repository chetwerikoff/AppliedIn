"""Issue #241: the only Submit guard checks live receivers against exact grants.

Fixtures are synthetic dictionaries: no network, browser, application, storage or
owner records. An earlier write ledger is not allowed to authorize Submit.
"""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill_apply as forms


URL = "https://jobs.ashbyhq.com/example-co/synthetic/application"
PK = "example-co#synthetic"


def _page(*controls, rooted=False):
    page = {
        "url": URL, "controls": list(controls), "inventory_verified": True,
        "truncated": False, "opaque_controls": [], "unsupported_frames": [],
        "shadow_roots": [],
    }
    if rooted:
        page["entry"] = {
            "root": None,
            "formless": {
                "root": "#application-root", "container": "#container",
                "submit": "#submit", "error": "",
            },
            "root_evidence": [
                {"selector": c["selector"], "type": c["type"],
                 "inside": True, "in_container": True, "native_owner": "",
                 "declared_owner": "", "formaction": None,
                 "yes_no": c.get("type") == "button",
                 "field_entry": "question-1" if c.get("type") == "button" else ""}
                for c in controls
            ],
        }
    return page


def _field(selector="#name", label="Name", value="Test User", **changes):
    return {
        "selector": selector, "tag": "input", "type": "text",
        "label": label, "question": "", "value": value, "has_value": bool(value),
        "required": True, "in_form": True, "form_selector": "#form",
        "form_action": URL, "name": "candidate_name", "disabled": False,
        "checked": False, **changes,
    }


def _grant(page, target, value):
    bound = forms._bound_target(page, target)
    grant = {
        "value": value, "url": page["url"],
        **{k: bound.get(k) for k in (
            "selector", "label", "question", "tag", "type",
            "form_selector", "form_action")},
    }
    if "boolean_members" in bound:
        grant["boolean_members"] = bound["boolean_members"]
    return grant


def _check(monkeypatch, page, *, facts, values, grants, receipts=None):
    row = {
        "pk": PK, "status": "submitting", "possible_submission": False,
        "human_approved_answers": grants,
    }
    monkeypatch.setattr(forms, "_row", lambda pk: row if pk == PK else {})
    filled = forms._ApprovedFormValues(dict(values), PK, facts)
    return forms.check_form(page, filled, uploaded=True, receipts=receipts)


def _boolean_page(*, selected=None):
    def member(caption):
        active = selected == caption
        return {
            "selector": "#" + caption.casefold(), "tag": "button",
            "type": "button", "native_type": "submit", "label": caption,
            "question": "Work authorization", "name": "", "value":
                caption.casefold() if active else "",
            "has_value": active, "checked": active, "required": None,
            "disabled": False, "in_form": False, "form_selector": "",
            "form_action": "", "formaction": "", "submit": False,
            "navigates": False, "commits_application": False,
        }
    return _page(member("Yes"), member("No"), rooted=True)


def _assent(*, checked):
    return _field(
        "#assent", "I certify the foregoing legal declaration", value="on",
        type="checkbox", checked=checked, has_value=True, required=True)


def test_value_written_into_wrong_control_cannot_borrow_another_grant(monkeypatch):
    page = _page(_field(selector="#wrong"))
    # The text and ledger match; only the observed selector differs.
    grant = _grant(page, page["controls"][0], "Test User")
    grant["selector"] = "#right"
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={"Name": "Test User"},
               values={"#wrong": "Test User"}, grants={"Name": grant})


def test_exact_owner_grant_can_authorize_a_value_without_a_bank_fact(monkeypatch):
    # The scoped human grant, not a generic fact-bank copy, is the authority.
    page = _page(_field())
    _check(monkeypatch, page, facts={}, values={"#name": "Test User"},
           grants={"Name": _grant(page, page["controls"][0], "Test User")})


def test_invented_value_with_matching_ledger_and_facts_is_not_a_grant(monkeypatch):
    page = _page(_field(value="Invented User"))
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={"Name": "Invented User"},
               values={"#name": "Invented User"},
               grants={"Name": _grant(page, page["controls"][0], "Test User")})


@pytest.mark.asyncio
async def test_unapproved_rooted_yes_no_click_refused_by_raw_producer(monkeypatch):
    # Ashby Yes/No buttons carry native_type=submit, but remain choice clicks;
    # their grant must be checked before the Submit classification.
    page = _boolean_page()
    row = {"ats": "ashby", "pk": PK, "status": "submitting",
           "possible_submission": False, "human_approved_answers": {}}
    controller = forms.ApplicationIPCController(
        PK, "example-co", URL, "", {}, {}, [], {}, lambda: None)
    monkeypatch.setattr(controller, "_page", AsyncMock(return_value=page))
    monkeypatch.setattr(controller, "_fresh_preparation", lambda *_: None)
    monkeypatch.setattr(controller, "_row", lambda: row)
    monkeypatch.setattr(forms, "_row", lambda pk: row if pk == PK else {})
    with pytest.raises(forms.Gate, match="witnessed owner choice"):
        await controller.before_ipc(object(), ("click", "#yes"), {})
    assert not controller.committed


def test_unanswered_rooted_yes_no_refused_at_final_submit(monkeypatch):
    page = _boolean_page()
    # Root proof no longer forbids reversible preparation; final Submit must
    # refuse the missing pair even when requiredness is unreadable.
    with pytest.raises(forms.Gate, match="Yes/No answer is empty"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_selected_rooted_yes_no_with_exact_member_grant_is_accepted(monkeypatch):
    page = _boolean_page(selected="No")
    _check(monkeypatch, page, facts={}, values={},
           grants={"Work authorization": _grant(page, page["controls"][0], "No")})


def test_ticked_rooted_yes_no_needs_matching_member_and_exact_grant(monkeypatch):
    page = _boolean_page(selected="No")
    # A previously approved Yes must not authorize a No rendered by the page.
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={},
               grants={"Work authorization": _grant(page, page["controls"][0], "Yes")})


def test_unticked_required_assent_refused(monkeypatch):
    page = _page(_assent(checked=False))
    with pytest.raises(forms.Gate, match="Required acknowledgement not completed"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_unapproved_ticked_assent_refused_even_if_writer_ledger_agrees(monkeypatch):
    page = _page(_assent(checked=True))
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={"#assent": "on"}, grants={})


def _ashby_combobox():
    receiver = {
        "selector": "#choice-value", "tag": "input", "type": "hidden",
        "name": "work_location", "label": "", "question": "",
        "value": "remote", "has_value": True, "required": False,
        "in_form": False, "form_selector": "", "form_action": "",
    }
    combo = {
        "selector": "#choice", "tag": "button", "type": "combobox",
        "name": "", "label": "Work mode", "question": "",
        "required": False, "in_form": False, "form_selector": "",
        "form_action": "", "value": "", "has_value": False,
        "disabled": False, "combobox": {
            "linked": False, "options": [], "selected_text": "Remote",
            "displayed_text": "Remote", "selected_value": "remote",
            "hidden_selector": "#choice-value", "hidden_value": "remote",
        },
    }
    return _page(receiver, combo, rooted=True)


def test_ashby_formless_root_accepts_named_hidden_combobox_receiver(monkeypatch):
    # Regression for the proven pre-fix refusal: neither member has native
    # form ownership, but both belong to one verified application root.
    page = _ashby_combobox()
    grant = _grant(page, page["controls"][1], "Remote")
    _check(monkeypatch, page, facts={"Work mode": "Remote"},
           values={"#choice": "Remote"}, grants={"Work mode": grant})


@pytest.mark.parametrize("drift", ["foreign", "changed-bytes", "no-root"])
def test_ashby_combobox_still_rejects_foreign_or_changed_hidden_values(
        monkeypatch, drift):
    page = _ashby_combobox()
    if drift == "foreign":
        page["entry"]["root_evidence"][0]["inside"] = False
    elif drift == "changed-bytes":
        page["controls"][0]["value"] = "hybrid"
    else:
        page["entry"]["formless"]["root"] = ""
    with pytest.raises(forms.Gate, match="hidden form value is not independently authorized"):
        _check(monkeypatch, page, facts={"Work mode": "Remote"},
               values={"#choice": "Remote"},
               grants={"Work mode": _grant(page, page["controls"][1], "Remote")})


def test_unselected_radio_machine_value_is_not_an_answer(monkeypatch):
    # Native radios always have machine option.value. With unknown requiredness
    # that string does not prove any member was checked.
    radio = _field("#choice", "Choice", type="radio", question="Choice",
                   name="answer", value="yes", has_value=True,
                   checked=False, required=None)
    with pytest.raises(forms.Gate, match="unreadable requiredness"):
        _check(monkeypatch, _page(radio), facts={}, values={}, grants={})


def test_unknown_requiredness_and_opaque_inventory_still_fail_closed(monkeypatch):
    page = _page(_field(value="", required=None))
    with pytest.raises(forms.Gate, match="unreadable requiredness"):
        _check(monkeypatch, page, facts={}, values={}, grants={})
    page["opaque_controls"] = [{"selector": "#opaque"}]
    with pytest.raises(forms.Gate, match="uninspectable"):
        _check(monkeypatch, page, facts={}, values={}, grants={})
