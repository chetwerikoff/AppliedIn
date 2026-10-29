"""Issue #241: the only Submit guard checks live receivers against exact grants.

Fixtures use synthetic records and real offline ENTRY_PAGE/fixed DOM readers.
No network, browser, application or owner records. A ledger is not a grant.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_reversible_gates import _LIVE_ENTRIES
from tests.tools.test_browser_application_entry import dom, entry


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


def _rooted_live_dom(mutate):
    # The existing sibling-mismatch fixture cannot accept movePane=False as
    # **kwargs. Reuse its actual Ashby DOM shape with the rooted layout.
    prefix = (_LIVE_ENTRIES.replace("__REQUIRED__", "true")
              .replace("__MUTATE__", mutate))
    spec = dict(root=False, formless=True, movePane=False, domOrder=True,
                url=entry("ashby"))
    return (dom(spec, prefix + forms.ENTRY_PAGE),
            dom(spec, prefix + forms._ENTRY_FIXED_CONTROL_WITNESS))


def _rooted_boolean_reader(*, multiple=False, selected=False):
    # Reader output, not fabricated button/yes_no/field_entry keys.
    mutation = ""
    if multiple:
        mutation += (
            "const other=node('div',{class:'ashby-application-form-input-yesno'},"
            "entry('Synthetic second Boolean question','synthetic-second'));"
            "untyped(other,{text:'Yes'});untyped(other,{text:'No'});"
            "node('input',{type:'checkbox',style:{display:'none'},rect:null},other);"
        )
    if selected:
        mutation += ("group.children[1].setAttribute('aria-checked','true');"
                     "group.children[1].value='no';")
        if multiple:
            mutation += ("other.children[0].setAttribute('aria-checked','true');"
                         "other.children[0].value='yes';")
    page, witness = _rooted_live_dom(mutation)
    assert page["entry"] == witness["entry"]
    assert page["entry"]["formless"]["root"]
    buttons = [e for e in page["entry"]["root_evidence"]
               if e.get("boolean_group") and e.get("tag") == "button"]
    assert len(buttons) == (4 if multiple else 2)
    assert all(e["type"] == e["native_type"] == "submit"
               and not e["yes_no"] and not e["field_entry"] for e in buttons)
    return page


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


def test_reader_rooted_boolean_member_is_not_a_reversible_choice():
    page = _rooted_boolean_reader()
    yes = next(c for c in page["controls"]
               if c["tag"] == "button" and c["label"] == "Yes")
    assert not forms._boolean_pair_member(page, yes["selector"])
    with pytest.raises(forms.Gate, match="Unsupported Boolean receiver"):
        forms._boolean_pair(page, yes)


@pytest.mark.parametrize("selected", [False, True])
def test_reader_rooted_boolean_refuses_even_with_a_guessed_exact_grant(
        monkeypatch, selected):
    page = _rooted_boolean_reader(selected=selected)
    yes = next(c for c in page["controls"]
               if c["tag"] == "button" and c["label"] == "Yes")
    grants = ({"Synthetic Boolean question": _grant(page, yes, "No")}
              if selected else {})
    with pytest.raises(forms.Gate, match="rooted Yes/No choice has no verified member"):
        _check(monkeypatch, page, facts={}, values={}, grants=grants)


@pytest.mark.parametrize("selected", [False, True])
def test_reader_multiple_rooted_yes_no_groups_cannot_bypass_final_grants(
        monkeypatch, selected):
    page = _rooted_boolean_reader(multiple=True, selected=selected)
    assert sum(e.get("boolean_group") is True
               for e in page["entry"]["root_evidence"]) >= 4
    # Previously the global len(members)==2 condition failed for four buttons.
    with pytest.raises(forms.Gate, match="rooted Yes/No choice has no verified member"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_unticked_required_assent_refused(monkeypatch):
    page = _page(_assent(checked=False))
    with pytest.raises(forms.Gate, match="Required acknowledgement not completed"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_unapproved_ticked_assent_refused_even_if_writer_ledger_agrees(monkeypatch):
    page = _page(_assent(checked=True))
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={"#assent": "on"}, grants={})


def _reader_hidden_combobox(drift="normal"):
    # The production reader omits an invisible formless hidden receiver from
    # controls while root_evidence still records its raw value and ownership.
    parent = "document.body" if drift == "foreign" else "location"
    mutation = (
        "const picker=location.children[0];"
        "picker.setAttribute('data-value-input','synthetic-value');"
        "picker.setAttribute('data-value-name','work_location');"
        "picker.setAttribute('data-selected-value','remote');"
        "picker.setAttribute('data-selected-label','Remote');"
        "picker.value='Remote';"
        "c.children.splice(c.children.indexOf(group.parentElement),1);"
        "const hidden=node('input',{id:'synthetic-value',name:'work_location',"
        "type:'hidden',rect:null,style:{display:'none'}},"
        + parent + ");"
        "hidden.value='" + ("hybrid" if drift == "changed-bytes" else "remote") + "';"
    )
    if drift == "native-owned":
        mutation += (
            "const foreignForm=node('form',{id:'foreign-owner'},document.body);"
            "hidden.setAttribute('form','foreign-owner');hidden.form=foreignForm;"
        )
    page, witness = _rooted_live_dom(mutation)
    assert page["entry"] == witness["entry"]
    assert page["entry"]["formless"]["root"]
    return page


def _isolated_reader_combo(page):
    # Keep authentic PAGE controls and complete root evidence; omit unrelated
    # required name/email inputs only for the targeted final-receiver test.
    current = deepcopy(page)
    owner = next(c for c in current["controls"]
                 if c.get("type") == "combobox"
                 and (c.get("combobox") or {}).get("selected_value") == "remote")
    selector = (owner["combobox"] or {}).get("hidden_selector")
    current["controls"] = [c for c in current["controls"]
                           if c["selector"] in {owner["selector"], selector}]
    return current, owner


def test_real_reader_hidden_receiver_remains_absent_not_synthetically_authorized(
        monkeypatch):
    real = _reader_hidden_combobox()
    evidence = next(e for e in real["entry"]["root_evidence"]
                    if e["selector"] == "#synthetic-value")
    assert evidence["type"] == "hidden" and evidence["value"] == "remote"
    assert evidence["inside"] and evidence["in_container"]
    assert "name" not in evidence
    assert not any(c["selector"] == "#synthetic-value" for c in real["controls"])
    page, owner = _isolated_reader_combo(real)
    assert owner["combobox"]["hidden_selector"] == "#synthetic-value"
    assert owner["combobox"]["hidden_value"] == owner["combobox"]["selected_value"]
    with pytest.raises(forms.Gate, match="hidden form value is not independently authorized"):
        _check(monkeypatch, page, facts={},
               values={owner["selector"]: "Remote"},
               grants={forms._key(page, owner): _grant(page, owner, "Remote")})


@pytest.mark.parametrize("drift", ["foreign", "native-owned", "changed-bytes", "no-root"])
def test_real_reader_combobox_refuses_foreign_native_or_changed_receivers(
        monkeypatch, drift):
    page = _reader_hidden_combobox(
        "normal" if drift == "no-root" else drift)
    evidence = next(e for e in page["entry"]["root_evidence"]
                    if e["selector"] == "#synthetic-value")
    if drift == "foreign":
        assert not evidence["inside"] and not evidence["in_container"]
    if drift == "native-owned":
        assert evidence["native_owner"] == "#foreign-owner"
        assert any(c["selector"] == "#synthetic-value" for c in page["controls"])
    if drift == "no-root":
        page["entry"]["formless"]["root"] = ""
    page, owner = _isolated_reader_combo(page)
    with pytest.raises(forms.Gate, match="(hidden form value|form-own value receiver)"):
        _check(monkeypatch, page, facts={},
               values={owner["selector"]: "Remote"},
               grants={forms._key(page, owner): _grant(page, owner, "Remote")})


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
