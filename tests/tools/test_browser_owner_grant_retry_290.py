"""Issue 290: a row's own approved text/combobox answer binds on a retry.

The owner's direct answer is stored twice: exactly, as the row's control-bound
grant, and as a shared bank fact. The next owner answer to the same label on
another job overwrites that fact. Write and readback took their value from the
bank, so the retry of an approved row read another job's text and failed exact
binding on value alone ("fields=value"), re-asking the owner every run.
Synthetic values only: no network, browser or owner data.
"""
import logging

import pytest

from tests.tools.test_browser_final_submit_grants import PK, URL, _field, _page
from tools import browser_skill_apply as forms

LABEL_TEXT = "Why do you want to work here?"
LABEL_COMBO = "How did you hear about us?"
APPROVED_TEXT = "Synthetic approved paragraph for this row only."
OTHER_JOB_TEXT = "Synthetic paragraph approved for a different job."
APPROVED_CAPTION = "Synthetic Referral Channel"
BANK_PREFIX = "Synthetic"


def _textarea(value=""):
    return _field(selector="#why", label=LABEL_TEXT, value=value, tag="textarea",
                  type="textarea", name="why")


def _combobox(selected=""):
    return _field(selector="#source", label=LABEL_COMBO, value="", type="combobox",
                  name="", combobox={"selected_text": selected, "options": []})


def _owner_grant(page, target, value, **extra):
    bound = forms._bound_target(page, target)
    return {"value": value, "url": page["url"],
            **{k: bound.get(k) for k in (
                "selector", "label", "question", "tag", "type",
                "form_selector", "form_action")}, **extra}


def _row(monkeypatch, grants):
    row = {"pk": PK, "status": "submitting", "possible_submission": False,
           "human_approved_answers": grants}
    monkeypatch.setattr(forms, "_row", lambda pk, **k: row if pk == PK else {})
    return row


def _value(facts, key):
    return str(facts[forms._fact_key(facts, key)]).strip()


@pytest.mark.parametrize("target, label, approved, bank", [
    (_textarea(), LABEL_TEXT, APPROVED_TEXT, OTHER_JOB_TEXT),
    (_combobox(), LABEL_COMBO, APPROVED_CAPTION, BANK_PREFIX),
], ids=["textarea", "combobox"])
def test_same_row_approved_value_binds_on_retry(monkeypatch, caplog, target, label,
                                                approved, bank):
    page = _page(target)
    key = forms._key(page, target)
    row = _row(monkeypatch, {key: _owner_grant(page, target, approved)})
    bound = forms._bound_target(page, target)
    # The defect: the shared bank fact is another approval, so value alone fails.
    bank_facts = {label.lower(): bank}
    with caplog.at_level(logging.INFO), pytest.raises(forms.Gate):
        forms.exact_approval(PK, key, _value(bank_facts, key), target=bound, url=URL)
    assert "fields=value grant_has_control_identity" in caplog.text
    # The fix: the row's own exact grant is the value written and read back.
    facts = forms._owner_grant_facts(row, bank_facts)
    assert _value(facts, key) == approved
    forms.exact_approval(PK, key, _value(facts, key), target=bound, url=URL)


def test_textarea_final_readback_binds_same_value_and_gates_a_changed_one(monkeypatch):
    blank = _textarea()
    key = forms._key(_page(blank), blank)
    row = _row(monkeypatch, {key: _owner_grant(_page(blank), blank, APPROVED_TEXT)})
    facts = forms._owner_grant_facts(row, {LABEL_TEXT: OTHER_JOB_TEXT})
    filled = forms._ApprovedFormValues({}, PK, facts)
    written = _textarea(APPROVED_TEXT)
    forms._final_exact_grant(_page(written), written, APPROVED_TEXT, filled)
    # A genuinely different page value is never normalized into the grant.
    for changed in (APPROVED_TEXT + " edited", OTHER_JOB_TEXT, APPROVED_TEXT.upper()):
        current = _textarea(changed)
        with pytest.raises(forms.Gate):
            forms._final_exact_grant(_page(current), current, changed, filled)


def test_changed_combobox_value_still_gates(monkeypatch):
    target = _combobox()
    page = _page(target)
    key = forms._key(page, target)
    row = _row(monkeypatch, {key: _owner_grant(page, target, APPROVED_CAPTION)})
    facts = forms._owner_grant_facts(row, {LABEL_COMBO: BANK_PREFIX})
    with pytest.raises(forms.Gate):
        forms.exact_approval(PK, key, _value(facts, key) + " Other",
                             target=forms._bound_target(page, target), url=URL)


def test_overlay_keeps_exact_grant_bytes_and_replaces_every_label_twin():
    spaced = "  Synthetic  approved   text with spacing  "
    row = {"human_approved_answers": {LABEL_TEXT: {
        "value": spaced, "tag": "textarea", "type": "textarea",
        "label": LABEL_TEXT, "question": ""}}}
    facts = forms._owner_grant_facts(
        row, {LABEL_TEXT.upper(): OTHER_JOB_TEXT, LABEL_TEXT + "*": OTHER_JOB_TEXT,
              "Name": "Synthetic Name"})
    # No twin survives to make the fact key ambiguous, and nothing is paraphrased.
    assert facts == {LABEL_TEXT: spaced, "Name": "Synthetic Name"}


@pytest.mark.parametrize("grant", [
    {"source": "bank-auto", "tag": "textarea", "type": "textarea"},
    {"tag": "input", "type": "text"},
    {"tag": "input", "type": "email"},
    {"tag": "input", "type": "radio"},
    {"tag": "input", "type": "checkbox"},
    {"tag": "input", "type": "combobox", "value": "   "},
    {"tag": "input", "type": "combobox",
     "question": "I agree to the arbitration agreement", "options": []},
], ids=["bank-auto", "text", "email", "radio", "checkbox", "blank", "material"])
def test_overlay_ignores_grants_outside_the_defect(grant):
    row = {"human_approved_answers": {LABEL_TEXT: {
        "value": APPROVED_TEXT, "label": LABEL_TEXT, "question": "", **grant}}}
    facts = {LABEL_TEXT: OTHER_JOB_TEXT}
    assert forms._owner_grant_facts(row, facts) == facts
