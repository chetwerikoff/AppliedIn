"""Issue #279: an exact grant survives a page reload for the same Ashby checkbox.

Ashby checkbox ids carry a per-page-load UUID. The grant records a load-stable
identity only when exactly one current answer receiver yields it. Synthetic
records only: no network, browser, application or owner records.
"""
import logging
from copy import deepcopy
from types import SimpleNamespace

import fakeredis
import pytest

from core.models import Status
from core.storage.local import RedisTracking
from tests.tools.test_browser_final_submit_grants import PK, URL, _field, _page
from tools import browser_skill_apply as forms

FIELD_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_FIELD_UUID = "11111111-2222-3333-4444-555555555555"
LABEL = "I confirm I have read the above."
SECRET_VALUE_TEXT = "yes"


def _checkbox(load, field=FIELD_UUID, **changes):
    base = dict(
        selector=f"#{load}_{field}-labeled-checkbox-0", label=LABEL,
        value="on", type="checkbox", name="", in_form=False, form_selector="",
        form_action="")
    return _field(**{**base, **changes})


def _grant(page, target, **extra):
    bound = forms._bound_target(page, target)
    grant = {
        "value": "yes", "url": page["url"], "visible_text": target["label"],
        **{k: bound.get(k) for k in (
            "selector", "label", "question", "tag", "type",
            "form_selector", "form_action")},
        **extra,
    }
    if bound.get("control_identity"):
        grant["control_identity"] = bound["control_identity"]
    return grant


def _approve(monkeypatch, grant, page, target):
    row = {"pk": PK, "status": "submitting", "possible_submission": False,
           "human_approved_answers": {forms._key(page, target): grant}}
    monkeypatch.setattr(forms, "_row", lambda pk, **k: row if pk == PK else {})
    forms.exact_approval(PK, forms._key(page, target), "yes",
                         target=forms._bound_target(page, target), url=page["url"])


LOAD_ONE = "2999abbb-e8c7-441a-8238-3b8a13535090"
LOAD_TWO = "a4d333ff-6517-47d2-a648-2bf903ff18f3"
ESCAPED_ONE = "\\32 999abbb-e8c7-441a-8238-3b8a13535090"


def test_same_checkbox_binds_after_page_reload(monkeypatch):
    first = _checkbox(LOAD_TWO)
    page_one = _page(first)
    grant = _grant(page_one, first)
    assert grant["control_identity"] == f"#{FIELD_UUID}-labeled-checkbox-0"
    reloaded = _checkbox(ESCAPED_ONE)
    _approve(monkeypatch, grant, _page(reloaded), reloaded)


def test_same_checkbox_binds_after_another_field_reveals_or_validates(monkeypatch):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    reloaded = _checkbox(LOAD_ONE)
    revealed = _field(selector="#revealed", label="Revealed", value="")
    validated = _field(selector="#name", label="Name", value="Test User", required=True)
    _approve(monkeypatch, grant, _page(revealed, validated, reloaded), reloaded)


def test_different_checkbox_does_not_bind(monkeypatch):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    other = _checkbox(LOAD_ONE, field=OTHER_FIELD_UUID)
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, grant, _page(other), other)


@pytest.mark.parametrize("change", [
    {"label": "Another statement"}, {"question": "Another question"},
    {"form_selector": "#other-form"}, {"form_action": "https://example.test/x"},
])
def test_identity_does_not_relax_any_other_binding_field(monkeypatch, change):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    reloaded = _checkbox(LOAD_ONE, **change)
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, grant, _page(reloaded), reloaded)


def test_different_page_url_does_not_bind(monkeypatch):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    reloaded = _checkbox(LOAD_ONE)
    page = _page(reloaded)
    page["url"] = URL + "?other"
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, grant, page, reloaded)


def test_ambiguous_identity_is_not_recorded_and_falls_back_to_exact_selector(monkeypatch):
    first = _checkbox(LOAD_TWO)
    twin = _checkbox(LOAD_ONE, label="Another statement")
    ambiguous = _page(first, twin)
    assert "control_identity" not in forms._bound_target(ambiguous, first)
    grant = _grant(ambiguous, first)
    assert "control_identity" not in grant
    # the exact selector still binds in the same load, a reload re-asks
    _approve(monkeypatch, grant, ambiguous, first)
    reloaded = _checkbox(LOAD_ONE)
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, grant, _page(reloaded), reloaded)


def test_grant_identity_needs_a_unique_identity_on_the_current_page(monkeypatch):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    reloaded = _checkbox(LOAD_ONE)
    twin = _checkbox("bbbbbbbb-6517-47d2-a648-2bf903ff18f3")
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, grant, _page(reloaded, twin), reloaded)


def test_old_grant_without_identity_gates_once_on_a_new_load(monkeypatch):
    first = _checkbox(LOAD_TWO)
    old = _grant(_page(first), first)
    del old["control_identity"]
    reloaded = _checkbox(LOAD_ONE)
    with pytest.raises(forms.Gate):
        _approve(monkeypatch, old, _page(reloaded), reloaded)


def test_selector_without_uuid_prefix_has_no_identity():
    plain = _checkbox("x")
    plain["selector"] = "#consent-labeled-checkbox-0"
    assert "control_identity" not in forms._bound_target(_page(plain), plain)
    nested = _checkbox("x")
    nested["selector"] = f"div > #{LOAD_ONE}_{FIELD_UUID}-labeled-checkbox-0"
    assert "control_identity" not in forms._bound_target(_page(nested), nested)


def test_text_and_radio_controls_never_get_an_identity():
    text = _field(selector=f"#{LOAD_ONE}_{FIELD_UUID}-field")
    radio = _field(selector=f"#{LOAD_ONE}__systemfield_eeoc_gender-labeled-radio-0",
                   type="radio", name=f"{LOAD_ONE}__systemfield_eeoc_gender")
    assert "control_identity" not in forms._bound_target(_page(text), text)
    assert forms._checkbox_identity_of(radio) == ""


def test_failed_binding_logs_only_field_names(monkeypatch, caplog):
    first = _checkbox(LOAD_TWO)
    grant = _grant(_page(first), first)
    other = _checkbox(LOAD_ONE, field=OTHER_FIELD_UUID)
    with caplog.at_level(logging.INFO):
        with pytest.raises(forms.Gate):
            _approve(monkeypatch, grant, _page(other), other)
    lines = [r.getMessage() for r in caplog.records
             if "exact grant binding mismatch" in r.getMessage()]
    assert lines and "fields=selector" in lines[0]
    assert "grant_has_control_identity=True" in lines[0]
    for text in lines:
        assert LABEL not in text and FIELD_UUID not in text and LOAD_ONE not in text


def test_bootstrap_grant_state_is_current_after_reload_and_stale_for_other_control():
    first = _checkbox(LOAD_TWO)
    page_one = _page(first)
    grant = _grant(page_one, first)
    reloaded = _checkbox(LOAD_ONE)
    question = {"url": URL, "visible_text": reloaded["label"],
                "control_identity": forms._bound_target(_page(reloaded), reloaded)[
                    "control_identity"]}
    assert forms._bootstrap_grant_state(grant, reloaded, question, "yes") == "current"
    other = _checkbox(LOAD_ONE, field=OTHER_FIELD_UUID)
    other_question = {"url": URL, "visible_text": other["label"],
                      "control_identity": forms._bound_target(_page(other), other)[
                          "control_identity"]}
    assert forms._bootstrap_grant_state(grant, other, other_question, "yes") == "stale"


def _rooted_text_page(root="#application-root", submit="#submit"):
    field = _field(selector="#free-text", label="Free text", value="", type="text",
                   in_form=False, form_selector="", form_action="")
    page = _page(field, rooted=True)
    page["entry"]["formless"].update(root=root, submit=submit)
    return page, field


def _text_grant(page, field):
    bound = forms._bound_target(page, field)
    return {"value": "answer", "url": page["url"],
            **{k: bound.get(k) for k in (
                "selector", "label", "question", "tag", "type",
                "form_selector", "form_action")}}


def _approve_text(monkeypatch, grant, page, field):
    key = forms._key(page, field)
    row = {"pk": PK, "status": "submitting", "possible_submission": False,
           "human_approved_answers": {key: grant}}
    monkeypatch.setattr(forms, "_row", lambda pk, **k: row if pk == PK else {})
    forms.exact_approval(PK, key, "answer", target=forms._bound_target(page, field),
                         url=page["url"])


def test_text_grant_binds_on_the_same_structural_root(monkeypatch):
    page, field = _rooted_text_page()
    grant = _text_grant(page, field)
    assert grant["form_selector"] == "#application-root"
    reread, field_again = _rooted_text_page()
    _approve_text(monkeypatch, grant, reread, field_again)


def test_text_grant_from_root_a_fails_on_root_b(monkeypatch):
    page, field = _rooted_text_page()
    grant = _text_grant(page, field)
    other, other_field = _rooted_text_page(root="#another-root")
    with pytest.raises(forms.Gate):
        _approve_text(monkeypatch, grant, other, other_field)
    other, other_field = _rooted_text_page(submit="#another-submit")
    with pytest.raises(forms.Gate):
        _approve_text(monkeypatch, grant, other, other_field)


def test_old_empty_form_text_grant_still_fails_exactly(monkeypatch, caplog):
    page, field = _rooted_text_page()
    grant = _text_grant(page, field)
    grant["form_selector"] = grant["form_action"] = ""
    with caplog.at_level(logging.INFO):
        with pytest.raises(forms.Gate):
            _approve_text(monkeypatch, grant, page, field)
    line = next(r.getMessage() for r in caplog.records
                if "exact grant binding mismatch" in r.getMessage())
    assert "fields=form_selector,form_action" in line
    assert "grant_has_control_identity=False" in line


def _bank_pair(**changes):
    page, field = _rooted_text_page()
    bound = forms._bound_target(page, field)
    fresh = {"value": "answer", "url": page["url"], "source": "bank-auto",
             **{k: bound.get(k) for k in (
                 "selector", "label", "question", "tag", "type",
                 "form_selector", "form_action")}}
    stale = {**fresh, "form_selector": "", "form_action": "", **changes}
    return stale, fresh, bound


def test_stale_bank_grant_with_only_empty_form_fields_is_replaceable():
    stale, fresh, bound = _bank_pair()
    assert forms._stale_bank_grant_replaceable(stale, fresh, bound)


@pytest.mark.parametrize("change", [
    {"value": "other"}, {"label": "Changed"}, {"question": "Changed"},
    {"tag": "textarea"}, {"type": "email"}, {"selector": "#elsewhere"},
    {"url": URL + "?other"}, {"source": "owner"}, {"form_selector": "#some-form"},
    {"form_action": "https://example.test/x"}, {"extra": "field"},
])
def test_stale_bank_grant_with_any_other_difference_is_not_replaceable(change):
    stale, fresh, bound = _bank_pair(**change)
    assert not forms._stale_bank_grant_replaceable(stale, fresh, bound)


def test_owner_or_unstamped_grant_is_never_replaceable():
    stale, fresh, bound = _bank_pair()
    del stale["source"]
    assert not forms._stale_bank_grant_replaceable(stale, fresh, bound)
    assert not forms._stale_bank_grant_replaceable("not a grant", fresh, bound)


def test_replacement_needs_both_fresh_form_fields_non_empty():
    stale, fresh, bound = _bank_pair()
    for field in ("form_selector", "form_action"):
        assert not forms._stale_bank_grant_replaceable(
            stale, {**fresh, field: ""}, {**bound, field: ""})
    assert not forms._stale_bank_grant_replaceable(
        stale, {**fresh, "form_selector": "#drifted"}, bound)


def test_synthetic_run_grant_replacement_is_guarded(tmp_path):
    from tools import browser_skill_fixture as fixture

    run = fixture._SyntheticRun(str(tmp_path / 'Synthetic-Resume.pdf'))
    pk = fixture.PK
    run.row.update(status="submitting", possible_submission=False,
                   human_approved_answers={"k": {"value": "a"}, "o": {"value": "z"}})
    assert not run.replace_grants_if_unchanged(pk, {"k": {"value": "x"}}, {"k": {"value": "b"}})
    assert run.row["human_approved_answers"] == {"k": {"value": "a"}, "o": {"value": "z"}}
    assert run.replace_grants_if_unchanged(pk, {"k": {"value": "a"}}, {"k": {"value": "b"}})
    assert run.row["human_approved_answers"] == {"k": {"value": "b"}, "o": {"value": "z"}}
    run.row["possible_submission"] = True
    assert not run.replace_grants_if_unchanged(pk, {"k": {"value": "b"}}, {"k": {"value": "c"}})
    run.row.update(possible_submission=False, status="needs_human")
    assert not run.replace_grants_if_unchanged(pk, {"k": {"value": "b"}}, {"k": {"value": "c"}})


# --- final-route bank-auto replacement of a stale empty-form bank grant -------

def _bank_world(monkeypatch, *, stale_changes=None, facts_value="answer", **row_extra):
    page, field = _rooted_text_page()
    field.update(selector="#_systemfield_name", label="Name", question="Name")
    page["controls"] = [field]
    page["entry"]["root_evidence"][0]["selector"] = "#_systemfield_name"
    bound = forms._bound_target(page, field)
    key = forms._key(page, field)
    stale = {"value": "answer", "url": page["url"], "source": "bank-auto",
             **{k: bound.get(k) for k in (
                 "selector", "label", "question", "tag", "type")},
             "form_selector": "", "form_action": "", **(stale_changes or {})}
    store = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    store.set_status(PK, Status.SUBMITTING, human_approved_answers={key: stale},
                     possible_submission=False, **row_extra)
    monkeypatch.setattr("core.stores.make_stores", lambda: SimpleNamespace(tracking=store))
    monkeypatch.setattr(forms, "_disclosure_question", lambda *a, **k: {
        "selector": field["selector"], "url": page["url"],
        "control_label": field["label"], "question": field["question"],
        "tag": field["tag"], "type": field["type"],
        "form_selector": bound["form_selector"], "form_action": bound["form_action"]})
    scope = frozenset({field["selector"]})
    facts = {"Name": facts_value}

    def run(**kwargs):
        forms._auto_grant_from_global(
            page, {}, PK, facts, {"Name": facts_value}, "https://jobs.ashbyhq.com",
            formless_selectors=scope, **kwargs)
    return store, key, stale, page, field, run


def _grants(store):
    return store.get(PK)["human_approved_answers"]


def test_stale_bank_grant_is_replaced_and_then_binds(monkeypatch):
    store, key, stale, page, field, run = _bank_world(monkeypatch)
    run()
    replaced = _grants(store)[key]
    assert replaced["form_selector"] == "#application-root"
    assert replaced["form_action"] == "#submit" and replaced["source"] == "bank-auto"
    assert {k: v for k, v in replaced.items() if k not in {"form_selector", "form_action"}} == {
        k: v for k, v in stale.items() if k not in {"form_selector", "form_action"}}
    monkeypatch.setattr(forms, "_row", lambda pk, **k: store.get(pk))
    forms.exact_approval(PK, key, "answer", target=forms._bound_target(page, field),
                         url=page["url"])


@pytest.mark.parametrize("stale_changes", [
    {"value": "other"}, {"label": "Changed"}, {"question": "Changed"},
    {"source": "dashboard"}, {"form_selector": "#some-form"},
    {"form_action": "#other-submit"},
])
def test_stale_grant_with_other_difference_is_not_overwritten(monkeypatch, stale_changes):
    store, key, stale, page, field, run = _bank_world(monkeypatch, stale_changes=stale_changes)
    before = deepcopy(store.get(PK))
    run()
    assert store.get(PK) == before


def test_stale_bank_grant_is_kept_when_the_bank_value_differs(monkeypatch):
    store, key, stale, page, field, run = _bank_world(monkeypatch, facts_value="new bank value")
    before = deepcopy(store.get(PK))
    run()
    assert store.get(PK) == before


def test_reversible_route_never_replaces_a_grant(monkeypatch):
    store, key, stale, page, field, run = _bank_world(monkeypatch)
    before = deepcopy(store.get(PK))
    run(reversible_scope=frozenset({field["selector"]}))
    assert store.get(PK) == before


def test_concurrent_grant_change_is_never_overwritten(monkeypatch):
    store, key, stale, page, field, run = _bank_world(monkeypatch)
    real = store.replace_grants_if_unchanged

    def raced(pk, expected, new):
        row = store.get(pk)
        row["human_approved_answers"][key] = {**stale, "source": "dashboard", "value": "owner"}
        store._write(pk, row, prev_status=row["status"])
        return real(pk, expected, new)
    monkeypatch.setattr(store, "replace_grants_if_unchanged", raced)
    run()
    assert _grants(store)[key]["value"] == "owner"
    assert _grants(store)[key]["source"] == "dashboard"


def test_possible_submission_blocks_the_replacement(monkeypatch):
    store, key, stale, page, field, run = _bank_world(monkeypatch)
    real = store.replace_grants_if_unchanged

    def marked(pk, expected, new):
        row = store.get(pk)
        row["possible_submission"] = True
        store._write(pk, row, prev_status=row["status"])
        return real(pk, expected, new)
    monkeypatch.setattr(store, "replace_grants_if_unchanged", marked)
    run()
    assert _grants(store)[key] == stale


# --- mixed batch: new bank grant + stale replacement commit in one guarded write

def _mixed_world(monkeypatch):
    page, a = _rooted_text_page()
    a.update(selector="#_systemfield_name", label="Name", question="Name")
    b = deepcopy(a)
    b.update(selector="#_systemfield_email", label="Email", question="Email")
    page["controls"] = [a, b]
    page["entry"]["root_evidence"][0]["selector"] = "#_systemfield_name"
    evidence_b = deepcopy(page["entry"]["root_evidence"][0])
    evidence_b["selector"] = "#_systemfield_email"
    page["entry"]["root_evidence"].append(evidence_b)
    key_a, key_b = forms._key(page, a), forms._key(page, b)
    bound = {item["selector"]: forms._bound_target(page, item) for item in (a, b)}
    stale = {"value": "answer", "url": page["url"], "source": "bank-auto",
             **{k: bound[a["selector"]].get(k) for k in (
                 "selector", "label", "question", "tag", "type")},
             "form_selector": "", "form_action": ""}
    store = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    store.set_status(PK, Status.SUBMITTING, human_approved_answers={key_a: stale},
                     possible_submission=False)
    monkeypatch.setattr("core.stores.make_stores", lambda: SimpleNamespace(tracking=store))
    monkeypatch.setattr(forms, "_disclosure_question", lambda page_, item, *args, **kwargs: {
        "selector": item["selector"], "url": page["url"], "control_label": item["label"],
        "question": item["question"], "tag": item["tag"], "type": item["type"],
        "form_selector": bound[item["selector"]]["form_selector"],
        "form_action": bound[item["selector"]]["form_action"]})
    real_row = forms._row
    state = {"reads": 0, "change": None}

    def row(pk, **kwargs):
        value = real_row(pk, **kwargs)
        state["reads"] += 1
        if state["reads"] == 2 and state["change"]:  # between final reread and write
            raw = store.get(PK)
            state["change"](raw)
            store._write(PK, raw, prev_status=raw["status"])
        return value
    monkeypatch.setattr(forms, "_row", row)
    scope = frozenset({a["selector"], b["selector"]})

    def run():
        forms._auto_grant_from_global(
            page, {}, PK, {"Name": "answer", "Email": "answer"},
            {"Name": "answer", "Email": "answer"}, "https://jobs.ashbyhq.com",
            formless_selectors=scope)
    return store, key_a, key_b, stale, state, run


def test_mixed_batch_writes_addition_and_replacement_together(monkeypatch):
    store, key_a, key_b, stale, state, run = _mixed_world(monkeypatch)
    run()
    grants = _grants(store)
    assert grants[key_a]["form_selector"] == "#application-root"
    assert grants[key_b]["source"] == "bank-auto" and grants[key_b]["form_action"] == "#submit"


@pytest.mark.parametrize("change", [
    lambda raw: raw["human_approved_answers"].update(
        {k: {"value": "OWNER", "source": "dashboard"} for k in list(
            raw["human_approved_answers"])}),
    lambda raw: raw.update(status="needs_human"),
    lambda raw: raw.update(possible_submission=True),
], ids=["grant-changed", "not-submitting", "possible-submission"])
def test_mixed_batch_race_writes_nothing(monkeypatch, change):
    store, key_a, key_b, stale, state, run = _mixed_world(monkeypatch)
    state["change"] = change
    run()
    after = store.get(PK)
    expected_grants = {key_a: stale}
    if change is not None:
        probe = {"status": "submitting", "possible_submission": False,
                 "human_approved_answers": {key_a: dict(stale)}}
        change(probe)
        expected_grants = probe["human_approved_answers"]
        assert after["status"] == probe["status"]
        assert after["possible_submission"] == probe["possible_submission"]
    # Nothing of the batch is written: no addition B, no replacement of A.
    assert key_b not in after["human_approved_answers"]
    assert after["human_approved_answers"] == expected_grants


def test_mixed_batch_preserves_an_unrelated_concurrent_grant(monkeypatch):
    store, key_a, key_b, stale, state, run = _mixed_world(monkeypatch)
    state["change"] = lambda raw: raw["human_approved_answers"].update(
        {"added-by-owner": {"value": "x", "source": "dashboard"}})
    run()
    grants = _grants(store)
    assert grants["added-by-owner"] == {"value": "x", "source": "dashboard"}
    assert key_b in grants and grants[key_a]["form_selector"] == "#application-root"
