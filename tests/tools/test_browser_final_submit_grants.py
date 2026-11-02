"""Issue #293: current-form submission readback without field approval grants.

Real offline Ashby reader cases remain; old grant authorization checks have been
replaced with the six-item final-state contract and required-control readback.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_reversible_gates import _LIVE_ENTRIES
from tests.tools.test_browser_application_entry import dom, entry


URL = 'https://jobs.ashbyhq.com/example-co/synthetic/application'


def _page(*controls, attached='Test User.pdf'):
    return {
        'url': URL, 'title': 'Synthetic role', 'inventory_verified': True,
        'truncated': False, 'opaque_controls': [],
        'unsupported_frames': [], 'shadow_roots': [],
        'controls': [
            {'selector': '#resume', 'tag': 'input', 'type': 'file',
             'label': 'Resume', 'question': 'Resume',
             'files': [attached] if attached else [],
             'required': True, 'in_form': True,
             'form_selector': '#application', 'form_action': URL},
            *controls,
        ],
    }


def _field(selector, label, *, kind='text', checked=False, required=True, value=''):
    return {
        'selector': selector, 'tag': 'input', 'type': kind,
        'label': label, 'question': label, 'value': value,
        'checked': checked, 'required': required,
        'disabled': False, 'in_form': True,
        'form_selector': '#application', 'form_action': URL,
    }


def _check(page, filled=None):
    return forms.check_form(page, filled or {}, True, 'Test User.pdf')


@pytest.mark.parametrize('checked', [False, True])
def test_required_acknowledgment_is_checked_on_final_readback(checked):
    page = _page(_field('#assent', 'I agree', kind='checkbox',
                        checked=checked, value='on'))
    if checked:
        _check(page)
    else:
        with pytest.raises(forms.Gate, match='required native choice'):
            _check(page, {'#assent': 'on'})


@pytest.mark.parametrize('checked', [(), ('Yes',), ('No',)])
def test_required_native_radio_uses_current_selection_not_machine_value(checked):
    choices = [
        _field('#yes', 'Yes', kind='radio', checked='Yes' in checked,
               value='yes'),
        _field('#no', 'No', kind='radio', checked='No' in checked,
               value='no'),
    ]
    for choice in choices:
        choice['question'] = 'Synthetic binary question'
    page = _page(*choices)
    if checked:
        _check(page)
    else:
        with pytest.raises(forms.Gate, match='required native choice'):
            _check(page, {'#yes': 'yes'})


def test_required_text_value_must_be_on_current_page_even_if_ledger_says_filled():
    page = _page(_field('#email', 'Email', value=''))
    with pytest.raises(forms.Gate, match='required application field is empty'):
        _check(page, {'#email': 'synthetic@example.test'})
    page['controls'][1]['value'] = 'synthetic@example.test'
    _check(page)


def test_optional_blank_choice_is_not_a_submit_refusal():
    _check(_page(_field('#optional', 'Optional question',
                        kind='checkbox', required=False)))


def test_opaque_or_truncated_final_inventory_never_authorizes_submit():
    page = _page(_field('#email', 'Email', value='synthetic@example.test'))
    for key, value in [('truncated', True), ('opaque_controls', [{'selector': '#x'}]),
                       ('unsupported_frames', [{'status': 'unknown'}])]:
        changed = deepcopy(page)
        changed[key] = value
        with pytest.raises(forms.Gate, match='final application inventory'):
            _check(changed)


def test_extra_file_is_not_authorized_by_first_resume_input():
    page = _page(_field('#email', 'Email', value='synthetic@example.test'))
    page['controls'].append({
        'selector': '#extra', 'tag': 'input', 'type': 'file',
        'label': 'Other upload', 'files': ['extra.pdf'],
    })
    with pytest.raises(forms.Gate, match='Exactly one current résumé input'):
        _check(page)


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
    # FF-243a probe of a live Ashby page: each pair's hidden checkbox is
    # named, and is checked exactly when Yes is pressed.
    mutation += "group.children[2].setAttribute('name','synthetic-companion');"
    if multiple:
        mutation += "other.children[2].setAttribute('name','synthetic-companion-2');"
    if selected:
        # The chosen member gains only aria-pressed; a <button> never carries
        # the answer as its value.
        selected_index = 0 if selected_member == "Yes" else 1
        mutation += f"group.children[{selected_index}].setAttribute('aria-pressed','true');"
        if selected_member == "Yes":
            mutation += "group.children[2].checked=true;"
        if multiple:
            mutation += ("other.children[0].setAttribute('aria-pressed','true');"
                         "other.children[2].checked=true;")
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




def test_real_reader_exposes_rooted_boolean_buttons_without_grants():
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    controls = page['controls']
    assert {c['label'] for c in controls} == {'Yes', 'No'}
    assert len([c for c in controls if c.get('checked') is True]) == 1


def test_real_reader_reports_a_hidden_value_receiver_consistently():
    page, witness = _reader_hidden_combobox()
    evidence = _hidden_reader_evidence(page)
    assert evidence['value'] == 'remote'
    assert evidence['inside'] and evidence['in_container']
    for observed in (page, witness):
        found = [c for c in observed['controls']
                 if c.get('selector') == evidence['selector']]
        assert len(found) == 1
        assert found[0]['type'] == 'hidden'
        assert found[0]['value'] == 'remote'
