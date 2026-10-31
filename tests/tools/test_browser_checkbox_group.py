"""Issue 278: a native checkbox option group is ONE owner question.

A Greenhouse form can carry required `input[type=checkbox]` options that share
one fieldset legend. They used to gate with "More than one control uses the same
approval question; review manually." and could not be answered at all. The
synthetic pages below carry no real employer, person or application.

At final Submit, the pinned guard checks EVERY checked and unchecked group
member against a current owner group grant before the ordinary per-option
exact grant check. Derived member approvals alone are not authority.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent import run
from tests.tools.test_browser_bootstrap import (
    Browser, PK, SECURE_URL, field, inventory, secure_inventory, submit, witness,
)
from tools import browser_skill_apply as forms

URL = SECURE_URL
QUESTION = 'Which regions can you work in?'
OPTIONS = ['North America', 'Europe', 'Asia Pacific']
REQUIRED = 'Required acknowledgement not completed'
GROUP_BOUND = 'Checkbox group answer is not bound to the current group grant'
SHARED = 'More than one control uses the same approval question'


def box(label, selector, **extra):
    extra.setdefault('question', QUESTION)
    extra.setdefault('form_action', URL)
    return field(label, selector, type='checkbox', value='on', has_value=True, **extra)


def group_controls(**extra):
    return [box(label, f'#region-{i}', **extra) for i, label in enumerate(OPTIONS)]


def page_of(*controls, resume=True):
    extra = [field('Resume', '#resume', required=False, type='file',
                   files=['Resume.pdf'], has_value=True, form_action=URL)] if resume else []
    page = inventory(*controls, *extra, submit(form_action=URL), url=URL)
    page['title'] = 'Synthetic Role'
    return page


def group_page(**kw):
    return page_of(*group_controls(), **kw)


def by(page, selector):
    return next(c for c in page['controls'] if c['selector'] == selector)


@pytest.fixture
def row(monkeypatch):
    stored = {'pk': PK, 'status': 'submitting', 'possible_submission': False,
              'human_approved_answers': {}}
    monkeypatch.setattr(forms, '_row', lambda pk, *a, **kw: stored)

    class Tracking:
        def set_status(self, pk, status, **fields):
            assert pk == PK
            stored.update(status=status, **fields)

        def replace_grants_if_unchanged(self, pk, expected, new):
            from core.storage.base import grants_replace_patch
            assert pk == PK
            merged = grants_replace_patch(stored, expected, new)
            if merged is None:
                return False
            stored['human_approved_answers'] = merged
            return True

    monkeypatch.setattr('core.stores.make_stores',
                        lambda: SimpleNamespace(tracking=Tracking()))
    return stored


def grant_for(page, selected):
    """The grant `resume_job` writes for an owner answer (see test_resume_*)."""
    group = forms._checkbox_group(page, page['controls'][0])
    first = group[0]
    return {'value': json.dumps(selected), 'selector': first['selector'], 'url': page['url'],
            'label': first['label'], 'question': first['question'], 'tag': 'input',
            'type': 'checkbox_group', 'form_selector': first['form_selector'],
            'form_action': first['form_action'], 'options': [c['label'] for c in group],
            'group_members': forms._group_binding(group), 'selected': list(selected)}


def answered(row, page, selected):
    row['human_approved_answers'][QUESTION] = grant_for(page, selected)
    forms._derive_group_member_grants(page, PK)


# ---------------------------------------------------------------- detection


def test_group_is_detected_in_page_order_with_distinct_member_keys():
    page = group_page()
    group = forms._checkbox_group(page, by(page, '#region-1'))
    assert [c['label'] for c in group] == OPTIONS
    keys = {forms._key(page, c) for c in group}
    assert len(keys) == 3 and QUESTION not in keys


def _radio_peer(page):
    page['controls'].insert(0, field('Remote', '#radio', type='radio', name='x',
                                     question=QUESTION, form_action=URL))


def _text_peer(page):
    page['controls'].insert(0, field('Other regions', '#other', type='text',
                                     question=QUESTION, form_action=URL))


def _duplicate_label(page):
    by(page, '#region-1')['label'] = by(page, '#region-0')['label']


def _blank_label(page):
    by(page, '#region-1')['label'] = ''


def _hidden_attestation(page):
    by(page, '#region-1')['ashby_hidden_choice'] = True


def _disabled_member(page):
    by(page, '#region-1')['disabled'] = True


def _different_action(page):
    by(page, '#region-1')['form_action'] = URL + '/other'


@pytest.mark.parametrize('mutate', [
    _radio_peer, _text_peer, _duplicate_label, _blank_label,
    _hidden_attestation, _disabled_member, _different_action,
])
def test_every_other_duplicate_keeps_the_collision_gate(mutate):
    page = group_page()
    mutate(page)
    assert forms._checkbox_group(page, by(page, '#region-0')) is None
    with pytest.raises(forms.Gate):
        forms._key(page, by(page, '#region-0'))


def test_two_unrelated_checkboxes_with_distinct_questions_are_not_a_group():
    page = page_of(box('One', '#a', question='First?'), box('Two', '#b', question='Second?'))
    assert forms._checkbox_group(page, by(page, '#a')) is None
    assert forms._key(page, by(page, '#a')) == 'First?'


def test_remaining_collision_logs_shape_only(caplog):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    page = group_page()
    _radio_peer(page)
    with pytest.raises(forms.Gate, match=SHARED):
        forms._key(page, by(page, '#region-0'))
    logged = ' '.join(r.getMessage() for r in caplog.records
                      if 'collision' in r.getMessage())
    assert 'size' in logged and 'radio' in logged and 'checkbox' in logged
    for private in (QUESTION, *OPTIONS, 'Remote', '#region-0'):
        assert private not in logged


# ----------------------------------------------------------- owner question


def test_without_a_grant_the_group_is_one_checkbox_group_owner_question(row):
    page = group_page()
    question = forms._disclosure_question(
        page, by(page, '#region-1'), {c['selector']: c for c in witness(page)['controls']},
        URL, bootstrap=True, pk=PK)
    assert question['type'] == 'checkbox_group'
    assert question['label'] == QUESTION == question['question']
    assert question['options'] == OPTIONS
    assert question['selector'] == '#region-0'
    assert [m['selector'] for m in question['group_members']] == [
        '#region-0', '#region-1', '#region-2']
    prompt = forms._choice_prompt(by(page, '#region-0'), {**question, 'pk': PK})
    for option in OPTIONS:
        assert option in prompt
    assert 'JSON array' in prompt


async def test_bootstrap_raises_the_owner_question_before_any_write(monkeypatch, row):
    from tests.tools.test_browser_bootstrap import URL as RAW_URL
    page = secure_inventory(*group_controls(form_action=RAW_URL))
    row.update(company='example-co', job_id='1', jd_url=SECURE_URL,
               title=page['title'], ats='custom')
    browser = Browser(page)
    filled = forms._ApprovedFormValues({}, PK, {})
    with pytest.raises(forms.Gate) as gate:
        await forms._bootstrap(browser, page, PK, {}, SECURE_URL, filled=filled,
                               history=[], job_url=SECURE_URL, company='example-co')
    assert gate.value.reason == 'owner_question'
    asked = gate.value.form_question
    assert asked['type'] == 'checkbox_group' and asked['options'] == OPTIONS
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)
    assert row['human_approved_answers'] == {}


# ------------------------------------------------------------------- grant


def test_grant_is_current_only_for_the_exact_option_set_and_selectors(row):
    page = group_page()
    row['human_approved_answers'][QUESTION] = grant_for(page, ['Europe'])
    group = forms._checkbox_group(page, by(page, '#region-0'))
    assert forms._current_group_grant(page, group, row)['selected'] == ['Europe']
    # Order never matters; the set does.
    shuffled = deepcopy(page)
    shuffled['controls'][:3] = list(reversed(shuffled['controls'][:3]))
    reordered = forms._checkbox_group(shuffled, by(shuffled, '#region-0'))
    assert forms._current_group_grant(shuffled, reordered, row) is not None

    def renamed(p):
        by(p, '#region-2')['label'] = 'Asia'

    def added(p):
        p['controls'].insert(3, box('Africa', '#region-3'))

    def removed(p):
        p['controls'].pop(2)

    def moved_selector(p):
        by(p, '#region-2')['selector'] = '#moved'

    def moved_form(p):
        for c in p['controls'][:3]:
            c['form_selector'] = '#other-form'

    for drift in (renamed, added, removed, moved_selector, moved_form):
        drifted = deepcopy(page)
        drift(drifted)
        found = forms._checkbox_group(drifted, by(drifted, '#region-0'))
        assert found is None or forms._current_group_grant(drifted, found, row) is None, drift


@pytest.mark.parametrize('selected', [[], ['Unknown'], ['Europe', 'Europe'], 'Europe', None])
def test_a_malformed_stored_selection_is_never_current(row, selected):
    page = group_page()
    stored = grant_for(page, ['Europe'])
    stored['selected'] = selected
    stored['value'] = json.dumps(selected)
    row['human_approved_answers'][QUESTION] = stored
    group = forms._checkbox_group(page, by(page, '#region-0'))
    assert forms._current_group_grant(page, group, row) is None


def test_owner_answer_must_be_a_json_array_of_exact_offered_labels():
    parse = run._parse_group_answer
    assert parse('["Europe", "North America"]', OPTIONS) == ['Europe', 'North America']
    for bad in ('Europe', '"Europe"', '[]', '{}', '["europe"]', '["Mars"]',
                '["Europe", "Europe"]', '[1]', '[null]', 'not json', ''):
        assert parse(bad, OPTIONS) is None, bad


def stored_question(page):
    group = forms._checkbox_group(page, by(page, '#region-0'))
    return {'pk': PK, 'label': QUESTION, 'selector': '#region-0', 'url': URL,
            'control_label': OPTIONS[0], 'question': QUESTION, 'tag': 'input',
            'type': 'checkbox_group', 'form_selector': '#application',
            'form_action': URL, 'options': [c['label'] for c in group],
            'group_members': forms._group_binding(group)}


def gated_row(question):
    return {'pk': PK, 'jd_url': URL, 'status': 'needs_human', 'gate_source': 'applier',
            'gate_call_id': 'direct', 'gate_pending': {'form_question': question}}


def test_stored_group_question_validation():
    question = stored_question(group_page())
    assert run._direct_form_label(PK, gated_row(question)) == QUESTION
    for tamper in (
        lambda q: q.update(options=OPTIONS[:2]),
        lambda q: q.update(options=list(reversed(OPTIONS))),
        lambda q: q['group_members'][1].update(form_selector='#elsewhere'),
        lambda q: q['group_members'][1].update(selector='#region-0'),
        lambda q: q.update(selector='#region-1'),
        lambda q: q.update(options=['Only one'], group_members=q['group_members'][:1]),
    ):
        changed = deepcopy(question)
        tamper(changed)
        assert run._direct_form_label(PK, gated_row(changed)) is None


# ----------------------------------------------------- derived member grants

def test_atomic_group_replace_guards_original_group_and_every_member_value(row, monkeypatch):
    from core.storage.base import grants_replace_patch

    page = group_page()
    original = grant_for(page, ['Europe'])
    keys = {member['label']: forms._member_key(member)
            for member in page['controls'][:3]}
    old_unselected = {'source': 'checkbox-group', 'group_ref': 'prior',
                      'value': 'yes', 'selector': '#region-0'}
    row['human_approved_answers'].update({
        QUESTION: original,
        keys['North America']: deepcopy(old_unselected),
        'other question': {'value': 'preserved'},
    })
    calls = []

    def replace(pk, expected, new):
        calls.append((pk, deepcopy(expected), deepcopy(new)))
        merged = grants_replace_patch(row, expected, new)
        assert merged is not None
        row['human_approved_answers'] = merged
        return True

    monkeypatch.setattr('core.stores.make_stores', lambda: SimpleNamespace(
        tracking=SimpleNamespace(replace_grants_if_unchanged=replace)))
    forms._derive_group_member_grants(page, PK)
    assert len(calls) == 1
    observed_pk, expected, new = calls[0]
    assert observed_pk == PK
    assert expected == {
        QUESTION: original,
        keys['North America']: old_unselected,
        keys['Europe']: None,
        keys['Asia Pacific']: None,
    }
    assert set(new) == set(expected)
    assert new[QUESTION] == original
    ref = forms._group_ref(original)
    for label in OPTIONS:
        grant = new[keys[label]]
        assert grant['source'] == 'checkbox-group'
        assert grant['group_ref'] == ref
        assert grant['selector'] == f'#region-{OPTIONS.index(label)}'
        assert grant['form_selector'] == '#application'
        assert grant['value'] == ('yes' if label == 'Europe' else 'no')
    assert row['human_approved_answers']['other question'] == {'value': 'preserved'}
    assert all(row['human_approved_answers'][k] == v for k, v in new.items())

    # Exact already-recorded state needs no second write.
    forms._derive_group_member_grants(page, PK)
    assert len(calls) == 1


@pytest.mark.parametrize('race', ['revoke_group', 'change_group', 'replace_member',
                                  'status', 'possible_submission'])
def test_atomic_group_replace_refuses_concurrent_changes_without_partial_write(
        row, monkeypatch, race):
    from core.storage.base import grants_replace_patch

    page = group_page()
    grants = row['human_approved_answers']
    grants[QUESTION] = grant_for(page, ['Europe'])
    key = forms._member_key(by(page, '#region-1'))
    old_member = {'value': 'yes', 'source': 'checkbox-group',
                  'group_ref': 'prior-answer'}
    grants[key] = deepcopy(old_member)
    calls = []

    def raced(pk, expected, new):
        calls.append((deepcopy(expected), deepcopy(new)))
        assert pk == PK
        if race == 'revoke_group':
            grants.pop(QUESTION)
        elif race == 'change_group':
            grants[QUESTION] = grant_for(page, ['North America'])
        elif race == 'replace_member':
            grants[key] = {'source': 'dashboard', 'value': 'changed'}
        elif race == 'status':
            row['status'] = 'needs_human'
        else:
            row['possible_submission'] = True
        merged = grants_replace_patch(row, expected, new)
        if merged is not None:
            row['human_approved_answers'] = merged
            return True
        return False

    monkeypatch.setattr('core.stores.make_stores', lambda: SimpleNamespace(
        tracking=SimpleNamespace(replace_grants_if_unchanged=raced)))
    with pytest.raises(forms.Gate, match='before atomic recording'):
        forms._derive_group_member_grants(page, PK)
    assert len(calls) == 1
    expected, proposed = calls[0]
    assert expected[QUESTION]['selected'] == ['Europe']
    assert expected[key] == old_member
    assert len(proposed) == len(OPTIONS) + 1
    assert all(grants.get(forms._member_key(member)) != proposed[
        forms._member_key(member)] for member in page['controls'][:3]
        if forms._member_key(member) != key)
    assert forms._bound_checkbox_group_member(
        page, by(page, '#region-1'),
        forms._ApprovedFormValues({}, PK, {})) is False



def test_atomic_derived_grants_cover_every_member_and_follow_the_answer(row):
    page = group_page()
    answered(row, page, ['North America', 'Asia Pacific'])
    grants = row['human_approved_answers']
    keys = {c['label']: forms._member_key(c) for c in page['controls'][:3]}
    ref = forms._group_ref(grants[QUESTION])
    assert set(grants) == {QUESTION, *keys.values()}
    for label in OPTIONS:
        member = grants[keys[label]]
        selected = label in {'North America', 'Asia Pacific'}
        assert member['source'] == 'checkbox-group'
        assert member['group_ref'] == ref
        assert member['value'] == ('yes' if selected else 'no')
        assert member['visible_text'] == label
        control = next(c for c in page['controls'] if c['label'] == label)
        if selected:
            assert forms._approved_native_choice(page, control, PK)[0] == 'yes'
        else:
            assert forms._approved_native_choice(page, control, PK) is None

    # A narrower owner answer overwrites the old positive key with an inert no,
    # rather than deleting it or keeping authority from a previous group_ref.
    answered(row, page, ['North America'])
    assert grants[keys['Asia Pacific']]['value'] == 'no'
    assert grants[keys['Europe']]['value'] == 'no'
    assert grants[keys['Asia Pacific']]['group_ref'] == forms._group_ref(grants[QUESTION])
    before = deepcopy(grants)
    by(page, '#region-2')['label'] = 'Asia'
    forms._derive_group_member_grants(page, PK)
    assert grants == before   # Drift cannot authorize a fresh derived write.
    assert forms._current_group_grant(
        page, forms._checkbox_group(page, by(page, '#region-0')), row) is None


def test_group_members_are_never_bank_auto_answered(row):
    page = group_page()
    proof = {c['selector']: c for c in witness(page)['controls']}
    forms._auto_grant_from_global(page, proof, PK, {QUESTION: 'yes'}, {QUESTION: 'yes'}, URL)
    assert row['human_approved_answers'] == {}


# ---------------------------------------------------------------- final check


def final_state(row, page, selected, checked):
    answered(row, page, selected)
    for member in page['controls'][:3]:
        member['checked'] = member['label'] in checked
    filled = forms._ApprovedFormValues({}, PK, {})
    for member in page['controls'][:3]:
        if member['checked']:
            filled[member['selector']] = member['value']
    return filled


def run_final(page, filled):
    forms.check_form(page, filled, True, 'Resume.pdf', [])


def test_standalone_required_checkbox_keeps_the_existing_gate(row):
    page = page_of(box('I agree', '#agree', question='Agreement'))
    filled = forms._ApprovedFormValues({}, PK, {})
    with pytest.raises(forms.Gate, match='Required acknowledgement not completed: I agree'):
        forms.check_form(page, filled, True, 'Resume.pdf', [])


def test_exactly_granted_group_passes_the_final_check(row):
    page = group_page()
    filled = final_state(row, page, ['North America', 'Asia Pacific'],
                         ['North America', 'Asia Pacific'])
    before = deepcopy(page)
    run_final(page, filled)
    assert page == before          # no copy, no mutation: every flag is the real one
    assert [c['required'] for c in page['controls'][:3]] == [True, True, True]


def test_final_check_rejects_without_a_current_binding_grant(row):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    del row['human_approved_answers'][QUESTION]
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_final_check_rejects_zero_checked_members(row):
    page = group_page()
    filled = final_state(row, page, ['Europe'], [])
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_final_check_rejects_a_checked_member_the_owner_did_not_grant(row):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe', 'North America'])
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_final_check_rejects_a_granted_member_that_is_not_checked(row):
    page = group_page()
    filled = final_state(row, page, ['Europe', 'North America'], ['Europe'])
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


@pytest.mark.parametrize('drift', ['option', 'selector', 'form', 'extra'])
def test_final_check_rejects_option_or_selector_drift(row, drift):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    if drift == 'option':
        by(page, '#region-2')['label'] = 'APAC'
    elif drift == 'selector':
        by(page, '#region-2')['selector'] = '#region-new'
    elif drift == 'form':
        for c in page['controls'][:3]:
            c['form_selector'] = '#changed'
    else:
        page['controls'].insert(3, box('Africa', '#region-3'))
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


@pytest.mark.parametrize('change', ['revoked', 'different_selection', 'wrong_type'])
def test_all_checked_members_still_need_current_group_authority(row, change):
    page = group_page()
    filled = final_state(row, page, OPTIONS, OPTIONS)
    if change == 'revoked':
        row['human_approved_answers'].pop(QUESTION)
    elif change == 'different_selection':
        row['human_approved_answers'][QUESTION] = grant_for(page, ['Europe'])
    else:
        row['human_approved_answers'][QUESTION]['type'] = 'checkbox'
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_optional_group_members_cannot_bypass_missing_group_grant(row):
    page = group_page()
    for member in page['controls'][:3]:
        member['required'] = False
    filled = final_state(row, page, ['Europe'], ['Europe'])
    run_final(page, filled)  # A genuinely current group is valid when optional.
    row['human_approved_answers'].pop(QUESTION)
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


@pytest.mark.parametrize('drift', ['options', 'member_selector', 'member_form_selector'])
def test_grant_must_bind_the_complete_stored_option_and_member_census(row, drift):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    grant = row['human_approved_answers'][QUESTION]
    if drift == 'options':
        grant['options'] = ['North America', 'Europe']
    elif drift == 'member_selector':
        grant['group_members'][0]['selector'] = '#stale'
    else:
        grant['group_members'][2]['form_selector'] = '#different'
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


@pytest.mark.parametrize('state', ['unchecked_unknown', 'selected_nonboolean'])
def test_every_member_needs_an_exact_boolean_checked_state(row, state):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    if state == 'unchecked_unknown':
        by(page, '#region-0')['checked'] = None
    else:
        by(page, '#region-1')['checked'] = 1
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


@pytest.mark.parametrize('drift', ['missing_ref', 'stale_ref', 'selector', 'form_selector'])
def test_selected_derived_member_needs_current_ref_and_identity(row, drift):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    derived = row['human_approved_answers'][forms._member_key(by(page, '#region-1'))]
    if drift == 'missing_ref':
        derived.pop('group_ref')
    elif drift == 'stale_ref':
        derived['group_ref'] = 'old-answer'
    elif drift == 'selector':
        derived['selector'] = '#stale'
    else:
        derived['form_selector'] = '#stale'
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_unchecked_member_cannot_retain_a_stale_derived_grant(row):
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    grants = row['human_approved_answers']
    stale = deepcopy(grants[forms._member_key(by(page, '#region-1'))])
    stale['group_ref'] = 'prior-grant'
    grants[forms._member_key(by(page, '#region-0'))] = stale
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)


def test_standalone_checked_checkbox_retains_ordinary_exact_grant(row):
    page = page_of(box('Independent acknowledgement', '#agree',
                       question='Independent acknowledgement'))
    member = by(page, '#agree')
    member['checked'] = True
    row['human_approved_answers'][forms._key(page, member)] = {
        'value': 'yes', 'selector': member['selector'], 'url': page['url'],
        'label': member['label'], 'question': member['question'],
        'tag': 'input', 'type': 'checkbox',
        'form_selector': member['form_selector'],
        'form_action': member['form_action'], 'visible_text': member['label'],
    }
    filled = forms._ApprovedFormValues({member['selector']: member['value']}, PK, {})
    run_final(page, filled)


# -------------------------------------------------------------- unchecking


def test_controller_may_only_uncheck_an_ungranted_member_of_the_current_group(row):
    page = group_page()
    answered(row, page, ['Europe'])
    for member in page['controls'][:3]:
        member['checked'] = True
    allowed = {c['label']: forms._group_uncheck_allowed(page, c, PK)
               for c in page['controls'][:3]}
    assert allowed == {'North America': True, 'Europe': False, 'Asia Pacific': True}
    row['human_approved_answers'].pop(QUESTION)
    assert not any(forms._group_uncheck_allowed(page, c, PK) for c in page['controls'][:3])
    answered(row, page, ['Europe'])
    by(page, '#region-2')['label'] = 'Asia'
    assert not any(forms._group_uncheck_allowed(page, c, PK) for c in page['controls'][:3])


class ToggleBrowser(Browser):
    def __init__(self, page):
        super().__init__(page)
        self.clicks = []

    async def call(self, *args, **kwargs):
        if args[0] == 'click':
            self.clicks.append(args[1])
            target = next(c for c in self.current['controls'] if c['selector'] == args[1])
            target['checked'] = not target['checked']
            return {}
        return await super().call(*args, **kwargs)


async def test_settle_clears_every_ungranted_member_and_verifies_each_state(row):
    page = group_page()
    answered(row, page, ['Europe'])
    by(page, '#region-0')['checked'] = True       # pre-checked, not granted
    by(page, '#region-1')['checked'] = True       # just written, granted
    browser = ToggleBrowser(page)
    await forms._settle_checkbox_group(browser, by(page, '#region-1'), PK, None)
    assert browser.clicks == ['#region-0']
    assert [c['checked'] for c in page['controls'][:3]] == [False, True, False]


async def test_settle_fails_when_the_ungranted_member_stays_checked(row):
    page = group_page()
    answered(row, page, ['Europe'])
    by(page, '#region-0')['checked'] = True
    by(page, '#region-1')['checked'] = True

    class Stuck(ToggleBrowser):
        async def call(self, *args, **kwargs):
            if args[0] == 'click':
                self.clicks.append(args[1])
                return {}
            return await super().call(*args, **kwargs)

    with pytest.raises(forms.Gate, match='does not match the owner answer'):
        await forms._settle_checkbox_group(Stuck(page), by(page, '#region-1'), PK, None)


# ------------------------------------------------------------- end to end


async def test_controller_page_and_other_consumers_keep_required(row):
    page = group_page()
    final_state(row, page, ['Europe'], ['Europe'])

    class Raw:
        async def call(self, command, script):
            assert command == 'evaluate'
            return {'value': deepcopy(page)}

    row['ats'] = 'custom'
    controller = forms.ApplicationIPCController(
        PK, 'example-co', URL, '', {}, {}, [], {}, lambda: None)
    shown = await controller._page(Raw())
    assert [c['required'] for c in shown['controls'][:3]] == [True, True, True]
    assert forms._unanswered(shown, by(shown, '#region-0'))


async def test_real_owner_answer_then_exact_checks_and_clearing(monkeypatch):
    from tests.tools import test_browser_greenhouse_settlement_owner_gate as owner
    from tools import browser_skill as bsk

    case, raw, stores = owner.tracked_greenhouse(monkeypatch)
    members = [box(label, f'#region-{i}', form_action=case.url)
               for i, label in enumerate(OPTIONS)]
    members[0]['checked'] = True          # pre-checked, will not be granted
    for page in (case.page, raw.state):
        page['controls'][1:2] = deepcopy(members)

    original = raw.call

    async def call(command, *args, **kwargs):
        if command == 'click':
            target = raw.target(args[0])
            assert target['type'] == 'checkbox'
            raw.writes.append(('click', args[0]))
            raw.calls.append((command, *args))
            target['checked'] = not target['checked']
            return {}
        return await original(command, *args, **kwargs)

    raw.call = call

    async def model_must_not_run(*a, **kw):
        pytest.fail('The group is an owner question before any model review')

    monkeypatch.setattr(bsk, 'decision', model_must_not_run)
    first = await owner.run._apply_direct(case.pk, stores)
    assert first['result'] == 'gated', first
    row = stores.tracking.get(case.pk)
    question = row['gate_pending']['form_question']
    assert row['gate_reason'] == 'owner_question'
    assert question['type'] == 'checkbox_group' and question['options'] == OPTIONS
    assert owner.run._direct_form_label(case.pk, row) == QUESTION
    assert ('click', '#region-1') not in raw.writes

    tokens = owner.server._owner_gate_tokens(row)
    for bad in ('Europe', '[]', '["Mars"]', '["Europe","Europe"]', '{"a": 1}'):
        refused = owner.run.resume_job(
            case.pk, bad, stores, disposition='answer',
            expected_gate_generation=tokens['gate_generation'],
            expected_question_identity=tokens['gate_question_identity'])
        assert refused['reason'] == 'invalid_answer', bad
        assert stores.tracking.get(case.pk)['gate_pending'] == row['gate_pending']
    accepted = owner.run.resume_job(
        case.pk, json.dumps(['Europe']), stores, disposition='answer',
        expected_gate_generation=tokens['gate_generation'],
        expected_question_identity=tokens['gate_question_identity'])
    assert accepted['result'] == 'queued', accepted
    assert stores.answer_bank.writes == []          # never banked
    grant = stores.tracking.get(case.pk)['human_approved_answers'][QUESTION]
    assert grant['type'] == 'checkbox_group' and grant['selected'] == ['Europe']
    assert [m['selector'] for m in grant['group_members']] == [
        '#region-0', '#region-1', '#region-2']
    assert not any(k in stores.tracking.get(case.pk)['human_approved_answers']
                   for k in (f'{QUESTION} → {label}' for label in OPTIONS))

    seen = []

    async def decide(_prompt, page, _history, *, model):
        choices = page['approved_choices']
        seen.append([c['selector'] for c in choices])
        if len(seen) == 1:
            assert [c['selector'] for c in choices] == ['#region-1']
            return {'action': 'choose', 'selector': '#region-1', 'fact': choices[0]['key']}
        return {'action': 'gate', 'question': 'Synthetic stop before Submit'}

    monkeypatch.setattr(bsk, 'decision', decide)
    second = await owner.run._apply_direct(case.pk, stores)
    assert second['result'] == 'gated', second
    assert ('click', '#region-1') in raw.writes
    assert ('click', '#region-0') in raw.writes      # the pre-checked option is cleared
    assert ('click', '#region-2') not in raw.writes
    assert [raw.target(f'#region-{i}')['checked'] for i in range(3)] == [False, True, False]
    recorded = stores.tracking.get(case.pk)['human_approved_answers']
    ref = forms._group_ref(recorded[QUESTION])
    for label in OPTIONS:
        derived = recorded[f'{QUESTION} → {label}']
        assert derived['group_ref'] == ref
        assert derived['value'] == ('yes' if label == 'Europe' else 'no')
    assert not any(call[0] == 'click' and call[1] == '#submit' for call in raw.calls)


async def test_raw_click_guard_allows_only_the_exact_clearing_click(monkeypatch, row):
    from unittest.mock import AsyncMock
    page = group_page()
    answered(row, page, ['Europe'])
    for member in page['controls'][:3]:
        member['checked'] = True
    controller = forms.ApplicationIPCController(
        PK, 'example-co', URL, '', {}, {}, [], {}, lambda: None)
    monkeypatch.setattr(controller, '_page', AsyncMock(return_value=deepcopy(page)))
    monkeypatch.setattr(controller, '_fresh_preparation', lambda _url: None)
    monkeypatch.setattr(controller, '_row', lambda: {'ats': 'custom'})

    class Raw:
        async def page(self):
            return deepcopy(page)

        async def call(self, *args):
            assert args == ('evaluate', forms._FIXED_CONTROL_WITNESS)
            return {'value': witness(page)}

    await controller.before_ipc(Raw(), ('click', '#region-0'), {})       # not granted
    await controller.before_ipc(Raw(), ('click', '#region-2'), {})       # not granted
    with pytest.raises(forms.Gate):                                      # granted: never unchecked
        await controller.before_ipc(Raw(), ('click', '#region-1'), {})
    row['human_approved_answers'].pop(QUESTION)
    with pytest.raises(forms.Gate):                                      # no grant: no clearing
        await controller.before_ipc(Raw(), ('click', '#region-0'), {})


def test_atomic_write_claim_without_actual_write_fails_readback(row, monkeypatch):
    page = group_page()
    row['human_approved_answers'][QUESTION] = grant_for(page, ['Europe'])
    monkeypatch.setattr('core.stores.make_stores', lambda: SimpleNamespace(
        tracking=SimpleNamespace(
            replace_grants_if_unchanged=lambda *a, **k: True)))  # silent no-op
    with pytest.raises(forms.Gate, match='did not record exactly'):
        forms._derive_group_member_grants(page, PK)


def test_a_checked_option_needs_a_derived_grant_minted_from_the_current_answer(row):
    page = group_page()
    filled = final_state(row, page, ['Europe', 'North America'],
                         ['Europe', 'North America'])
    run_final(page, filled)
    # The owner narrows the answer, but a leftover derived grant for the dropped
    # option still sits in the row (a missed removal): it is never authority.
    leftovers = {k: deepcopy(v) for k, v in row['human_approved_answers'].items()
                 if v.get('source') == 'checkbox-group'}
    row['human_approved_answers'][QUESTION] = grant_for(page, ['Europe'])
    row['human_approved_answers'].update(leftovers)
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)

    # A leftover minted from an EARLIER answer for a still-selected option too.
    page = group_page()
    filled = final_state(row, page, ['Europe'], ['Europe'])
    run_final(page, filled)
    row['human_approved_answers'][forms._member_key(by(page, '#region-1'))][
        'group_ref'] = 'old-answer'
    with pytest.raises(forms.Gate, match=GROUP_BOUND):
        run_final(page, filled)
