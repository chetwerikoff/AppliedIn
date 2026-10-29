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


def _rooted_boolean_reader(*, multiple=False, selected=False, selected_member="No"):
    # Both independent readers must expose the production-shaped default-submit buttons.
    mutation = ""
    if multiple:
        mutation += (
            "const other=node('div',{class:'ashby-application-form-input-yesno'},"
            "entry('Synthetic second Boolean question','synthetic-second'));"
            "untyped(other,{text:'Yes'});untyped(other,{text:'No'});"
            "node('input',{type:'checkbox',style:{display:'none'},rect:null},other);"
        )
    if selected:
        selected_index = 0 if selected_member == "Yes" else 1
        mutation += (
            f"group.children[{selected_index}].setAttribute('aria-checked','true');"
            f"group.children[{selected_index}].value='{selected_member.lower()}';"
        )
        if multiple:
            mutation += ("other.children[0].setAttribute('aria-checked','true');"
                         "other.children[0].value='yes';")
    page, witness = _rooted_live_dom(mutation)
    assert page["entry"] == witness["entry"]
    assert page["entry"]["formless"]["root"]
    buttons = [e for e in page["entry"]["root_evidence"]
               if e.get("boolean_group") and e.get("tag") == "button"]
    assert len(buttons) == (4 if multiple else 2)
    assert all(e["type"] == "button" and e["native_type"] == "submit"
               and e["yes_no"] is True and e["field_entry"]
               and e["caption"].casefold() in {"yes", "no"}
               for e in buttons)
    controls = [c for c in page["controls"] if c.get("label", "").casefold() in {"yes", "no"}]
    assert len(controls) == len(buttons)
    assert all(c["type"] == "button" and c["native_type"] == "submit"
               and c["question"] for c in controls)
    return page


def _isolated_reader_boolean(page):
    current = deepcopy(page)
    current["controls"] = [
        control for control in current["controls"]
        if control.get("type") == "button"
        and control.get("label", "").casefold() in {"yes", "no"}
    ]
    return current


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


def test_final_submit_rejects_plain_dict_instead_of_row_bound_values():
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    selected = next(c for c in page['controls'] if c.get('checked') is True)
    with pytest.raises(forms.Gate, match='row-bound approved form values'):
        forms.check_form(page, {selected['selector']: selected['value']},
                         uploaded=True)


def test_reader_rooted_boolean_pair_is_a_proven_reversible_choice():
    page = _isolated_reader_boolean(_rooted_boolean_reader())
    yes = next(c for c in page["controls"]
               if c["tag"] == "button" and c["label"] == "Yes")
    members = forms._boolean_pair(page, yes)
    assert [member["label"] for member in members] == ["Yes", "No"]
    assert all(forms._boolean_pair_member(page, member["selector"])
               for member in members)


@pytest.mark.parametrize(
    ("selected", "message"),
    [(False, "A Yes/No answer is empty or conflicting"),
     (True, "exact human-approved answer")],
    ids=["unanswered", "unapproved-selection"],
 )
def test_reader_rooted_boolean_requires_one_selected_exact_grant(
        monkeypatch, selected, message):
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=selected))
    with pytest.raises(forms.Gate, match=message):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_reader_rooted_boolean_accepts_exact_grant_and_guarded_choice_click(
        monkeypatch):
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    yes = next(c for c in page["controls"] if c["label"] == "Yes")
    selected = next(c for c in page["controls"] if c.get("checked") is True)
    key = forms._key(page, yes)
    grant = _grant(page, yes, "No")
    _check(monkeypatch, page, facts={}, values={}, grants={key: grant})
    assert forms._approved_boolean_pair(page, yes, PK) == ("No", selected)


def test_reader_rooted_boolean_accepts_casefolded_stored_grant(monkeypatch):
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    yes = next(c for c in page["controls"] if c["label"] == "Yes")
    selected = next(c for c in page["controls"] if c.get("checked") is True)
    key = forms._key(page, yes)
    _check(monkeypatch, page, facts={}, values={},
           grants={key: _grant(page, yes, "no")})
    assert forms._approved_boolean_pair(page, yes, PK) == ("no", selected)


def test_reader_rooted_boolean_refuses_grant_for_the_other_member(monkeypatch):
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    yes = next(c for c in page["controls"] if c["label"] == "Yes")
    key = forms._key(page, yes)
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={},
               grants={key: _grant(page, yes, "Yes")})


def test_reader_rooted_boolean_rejects_grant_for_different_live_caption(
        monkeypatch):
    page = _isolated_reader_boolean(
        _rooted_boolean_reader(selected=True, selected_member="Yes"))
    yes = next(c for c in page["controls"] if c["label"] == "Yes")
    key = forms._key(page, yes)
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={},
               grants={key: _grant(page, yes, "no")})


def test_reader_multiple_rooted_yes_no_pairs_each_require_an_exact_grant(
        monkeypatch):
    page = _isolated_reader_boolean(
        _rooted_boolean_reader(multiple=True, selected=True))
    yes_buttons = [c for c in page["controls"] if c.get("label") == "Yes"]
    assert len(yes_buttons) == 2
    grants = {}
    for yes in yes_buttons:
        answer = "No" if yes["question"] == "Synthetic Boolean question" else "Yes"
        grants[forms._key(page, yes)] = _grant(page, yes, answer)
    _check(monkeypatch, page, facts={}, values={}, grants=grants)


def test_reader_multiple_rooted_yes_no_pairs_cannot_skip_an_unanswered_group(
        monkeypatch):
    page = _isolated_reader_boolean(
        _rooted_boolean_reader(multiple=True, selected=True))
    yes_buttons = [c for c in page["controls"] if c.get("label") == "Yes"]
    first = yes_buttons[0]
    grants = {forms._key(page, first): _grant(page, first, "No")}
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={}, grants=grants)


def test_reader_multiple_rooted_yes_no_pairs_refuse_unanswered_members(
        monkeypatch):
    page = _isolated_reader_boolean(
        _rooted_boolean_reader(multiple=True, selected=False))
    with pytest.raises(forms.Gate, match="A Yes/No answer is empty or conflicting"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_unticked_required_assent_refused(monkeypatch):
    page = _page(_assent(checked=False))
    with pytest.raises(forms.Gate, match="Required acknowledgement not completed"):
        _check(monkeypatch, page, facts={}, values={}, grants={})


def test_unapproved_ticked_assent_refused_even_if_writer_ledger_agrees(monkeypatch):
    page = _page(_assent(checked=True))
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={"#assent": "on"}, grants={})


def test_ticked_checkbox_accepts_casefolded_affirmative_grant(monkeypatch):
    page = _page(_assent(checked=True))
    target = page["controls"][0]
    grant = _grant(page, target, "Yes")
    grant["visible_text"] = target["label"]
    _check(monkeypatch, page, facts={}, values={"#assent": "on"},
           grants={forms._key(page, target): grant})


def _reader_hidden_combobox(drift="normal"):
    # Both independent readers must inventory only the exact rooted hidden receiver.
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
    return page, witness


def _hidden_reader_evidence(page):
    # A dynamically inserted synthetic ID is absent from the fixture's
    # precomputed ID index, so ENTRY_PAGE may return a positional selector.
    # Match the raw ID supplied by the reader, then use its actual selector.
    matches = [e for e in page["entry"]["root_evidence"]
               if e.get("id") == "synthetic-value"]
    assert len(matches) == 1
    evidence = matches[0]
    assert evidence["tag"] == "input" and evidence["type"] == "hidden"
    assert evidence["unique"] is True
    return evidence


def _isolated_reader_combo(page):
    # Keep authentic PAGE controls and complete root evidence; omit unrelated
    # required name/email inputs only for the targeted final-receiver test.
    current = deepcopy(page)
    owner = next(c for c in current["controls"]
                 if c.get("type") == "combobox"
                 and (c.get("combobox") or {}).get("selected_value") == "remote")
    selectors = {owner["selector"], (owner["combobox"] or {}).get("hidden_selector")}
    # A foreign native-form receiver has no combobox linkage, but must still
    # be checked as an independently inventoried, populated hidden control.
    selectors.update(c["selector"] for c in current["controls"]
                     if c.get("type") == "hidden"
                     and c.get("name") == "work_location")
    current["controls"] = [c for c in current["controls"]
                           if c["selector"] in selectors]
    return current, owner


def test_real_reader_hidden_receiver_is_inventoried_and_grant_bound(monkeypatch):
    real, witness = _reader_hidden_combobox()
    evidence = _hidden_reader_evidence(real)
    assert evidence["value"] == "remote"
    assert evidence["name"] == "work_location"
    assert evidence["inside"] and evidence["in_container"]
    for observed in (real, witness):
        receivers = [c for c in observed["controls"]
                     if c.get("selector") == evidence["selector"]]
        assert len(receivers) == 1
        assert receivers[0]["type"] == "hidden"
        assert receivers[0]["name"] == "work_location"
        assert receivers[0]["value"] == "remote"
        assert receivers[0]["in_form"] is False
    page, owner = _isolated_reader_combo(real)
    assert owner["combobox"]["hidden_selector"] == evidence["selector"]
    assert owner["combobox"]["hidden_value"] == owner["combobox"]["selected_value"]
    receiver = next(c for c in page["controls"] if c["selector"] == evidence["selector"])
    assert forms._combo_receiver_allowed(page, owner, receiver)
    key = forms._key(page, owner)
    with pytest.raises(forms.Gate, match="exact human-approved answer"):
        _check(monkeypatch, page, facts={}, values={owner["selector"]: "Remote"}, grants={})
    _check(monkeypatch, page, facts={}, values={owner["selector"]: "Remote"},
           grants={key: _grant(page, owner, "Remote")})

@pytest.mark.parametrize("drift", ["foreign", "native-owned", "changed-bytes", "no-root"])
def test_real_reader_combobox_refuses_foreign_native_or_changed_receivers(
        monkeypatch, drift):
    page, witness = _reader_hidden_combobox(
        "normal" if drift == "no-root" else drift)
    evidence = _hidden_reader_evidence(page)
    if drift == "foreign":
        assert not evidence["inside"] and not evidence["in_container"]
        assert not any(c["selector"] == evidence["selector"]
                       for c in page["controls"] + witness["controls"])
    if drift == "native-owned":
        assert evidence["native_owner"] and evidence["declared_owner"] == "foreign-owner"
        receivers = [c for c in page["controls"]
                     if c["selector"] == evidence["selector"]]
        assert len(receivers) == 1
        assert receivers[0]["in_form"] is True
        assert receivers[0]["native_form_owner_selector"] == evidence["native_owner"]
    if drift == "changed-bytes":
        assert evidence["value"] == "hybrid"
    if drift == "no-root":
        page["entry"]["formless"]["root"] = ""
    page, owner = _isolated_reader_combo(page)
    if drift == "native-owned":
        receiver = next(c for c in page["controls"]
                        if c["selector"] == evidence["selector"])
        assert not owner["combobox"]["hidden_selector"]
        assert not forms._combo_receiver_allowed(page, owner, receiver)
    else:
        assert owner["combobox"]["hidden_selector"] == evidence["selector"]
    with pytest.raises(forms.Gate, match="(hidden form value|form-own value receiver)"):
        _check(monkeypatch, page, facts={}, values={owner["selector"]: "Remote"},
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
