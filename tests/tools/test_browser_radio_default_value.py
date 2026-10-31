"""Issue 282: survey radios with no value attribute resolve by selector and caption.

A native radio without a value attribute reports the HTML default "on" for every
member. That value names no option, so the group is identified by its distinct
selectors and distinct captions. Mixed groups and duplicate explicit values stay
refused, and the decline-only guard and exact-grant checks are unchanged.
"""
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as dom
from tests.tools.test_browser_bootstrap import (
    PK, SECURE_URL, field, secure_inventory, secure_world, _choice_grant_from_page,
)

QUESTION = 'Gender'
CAPTIONS = ['Male', 'Female', 'Decline to self-identify']


def survey_radio(caption, selector, *, attr_present=False, value='on', **extra):
    """The sanitized live shape: no value attribute, IDL default value "on"."""
    return field(caption, selector, type='radio', name='systemfield_eeoc_gender',
                 question=QUESTION, value=value, has_value=True,
                 value_attr_present=attr_present, **extra)


def survey_page(**kwargs):
    return secure_inventory(*(
        survey_radio(caption, f'#gender-{i}', **kwargs)
        for i, caption in enumerate(CAPTIONS)))


def group_of(page):
    return forms._radio_group(page, page['controls'][0])


def test_reader_reports_value_attribute_presence_for_inputs():
    assert "value_attr_present:e.hasAttribute('value')" in dom.ENTRY_PAGE
    assert "e.tagName === 'INPUT' ? {value_attr_present" in dom.ENTRY_PAGE


def test_attribute_less_group_resolves_by_distinct_selector_and_caption():
    page = survey_page()
    members = group_of(page)
    assert [m['label'] for m in members] == CAPTIONS
    assert len({m['selector'] for m in members}) == 3
    assert {m['value'] for m in members} == {'on'}
    assert all(forms._default_radio_value(m) for m in members)


def test_default_value_never_names_an_option():
    page = survey_page()
    members = group_of(page)
    assert [m['label'] for m in members
            if forms._radio_answer_matches('on', m)] == []
    assert [m['label'] for m in members
            if forms._radio_answer_matches('decline to  self-identify', m)] == [
        'Decline to self-identify']


def test_explicit_values_still_match_by_value():
    page = survey_page(attr_present=True)
    for member, value in zip(page['controls'][:3], ('m', 'f', 'd')):
        member['value'] = value
    members = group_of(page)
    assert not any(forms._default_radio_value(m) for m in members)
    assert [m['label'] for m in members
            if forms._radio_answer_matches('d', m)] == ['Decline to self-identify']


def refusal(page, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        with pytest.raises(forms.Gate) as gate:
            group_of(page)
    assert str(gate.value) == (
        'Radio group members or captions are incomplete or ambiguous.')
    return [r.getMessage() for r in caplog.records if 'radio group refused' in r.getMessage()]


def test_mixed_group_with_one_value_attribute_still_gates(caplog):
    page = survey_page()
    page['controls'][1].update(value_attr_present=True, value='f')
    assert refusal(page, caplog) == ['radio group refused: predicate=values.mixed']


def test_duplicate_explicit_values_still_gate(caplog):
    page = survey_page(attr_present=True, value='dup')
    assert refusal(page, caplog) == ['radio group refused: predicate=values.distinct']


def test_unmarked_default_values_are_still_duplicates(caplog):
    # Without the reader's attribute-presence fact "on" is an ordinary duplicate.
    page = survey_page()
    for control in page['controls'][:3]:
        control.pop('value_attr_present')
    assert refusal(page, caplog) == ['radio group refused: predicate=values.distinct']


def test_attribute_less_non_default_value_is_not_the_default_shape(caplog):
    page = survey_page(value='dup')
    assert refusal(page, caplog) == ['radio group refused: predicate=values.distinct']


@pytest.mark.parametrize(('predicate', 'mutate'), [
    ('members.count', lambda cs: cs.__delitem__(slice(1, 3))),
    ('tag.input', lambda cs: cs[1].update(tag='div')),
    ('in_form', lambda cs: cs[1].update(in_form=False)),
    ('question.equal', lambda cs: cs[1].update(question='Other question')),
    ('form_action.equal', lambda cs: cs[1].update(form_action='https://elsewhere.test/')),
    ('selector.present', lambda cs: cs[1].update(selector='')),
    ('label.present', lambda cs: cs[1].update(label='  ')),
    ('value.present', lambda cs: cs[1].update(value='')),
    ('selectors.distinct', lambda cs: cs[1].update(selector=cs[0]['selector'])),
    ('captions.distinct', lambda cs: cs[1].update(label=' MALE ')),
])
def test_every_other_subpredicate_still_rejects_the_attribute_less_shape(
        predicate, mutate, caplog):
    page = survey_page()
    mutate(page['controls'])
    messages = refusal(page, caplog)
    assert messages == [f'radio group refused: predicate={predicate}'], messages


def grant_world(monkeypatch, page, answer):
    key, grant = _choice_grant_from_page(page, page['controls'][0], answer)
    browser, decide, row = secure_world(monkeypatch, page, grants={key: grant})
    browser.allow_mutations = True
    return browser, row, key


async def choose(browser, page, selector, key, value):
    return await forms.execute(
        browser, page, {'action': 'choose', 'selector': selector, 'fact': key,
                        'value': value},
        facts={}, filled={}, resume_path='', company='example-co', jd_text='',
        resume_tex='', github='', pk=PK, job_url=SECURE_URL)


async def test_granted_decline_caption_writes_by_selector(monkeypatch):
    page = survey_page()
    browser, row, key = grant_world(monkeypatch, page, 'Decline to self-identify')
    await choose(browser, page, '#gender-2', key, 'Decline to self-identify')
    clicks = [c for c in browser.calls if c[0] == 'click']
    assert clicks == [('click', '#gender-2')]


async def test_granted_decline_never_clicks_a_sibling(monkeypatch):
    page = survey_page()
    browser, row, key = grant_world(monkeypatch, page, 'Decline to self-identify')
    for selector in ('#gender-0', '#gender-1'):
        with pytest.raises(forms.Gate):
            await choose(browser, page, selector, key, 'Decline to self-identify')
    assert not any(c[0] in {'click', 'fill', 'select'} for c in browser.calls)


@pytest.mark.parametrize('caption', ['Male', 'Female'])
async def test_affirmative_caption_without_a_grant_gates(monkeypatch, caption):
    page = survey_page()
    browser, row, key = grant_world(monkeypatch, page, 'Decline to self-identify')
    selector = f'#gender-{CAPTIONS.index(caption)}'
    with pytest.raises(forms.Gate):
        await choose(browser, page, selector, key, caption)
    assert not any(c[0] in {'click', 'fill', 'select'} for c in browser.calls)


async def test_default_value_is_not_an_answer(monkeypatch):
    page = survey_page()
    browser, row, key = grant_world(monkeypatch, page, 'on')
    with pytest.raises(forms.Gate):
        await choose(browser, page, '#gender-2', key, 'on')
    assert not any(c[0] in {'click', 'fill', 'select'} for c in browser.calls)


def test_granted_choice_resolves_one_member_not_the_default_value(monkeypatch):
    page = survey_page()
    key, grant = _choice_grant_from_page(page, page['controls'][0],
                                         'Decline to self-identify')
    browser, decide, row = secure_world(monkeypatch, page, grants={key: grant})
    target = deepcopy(page['controls'][0])
    answer, selected = forms._approved_native_choice(page, target, PK)
    assert answer == 'Decline to self-identify'
    assert selected['selector'] == '#gender-2'


def final_world(monkeypatch, checked):
    """Grant on the unanswered page; Submit then sees the page with `checked`."""
    page = survey_page()
    for control in page['controls'][:3]:
        control['required'] = True
    key, grant = _choice_grant_from_page(page, page['controls'][0],
                                         'Decline to self-identify')
    secure_world(monkeypatch, page, grants={key: grant})
    for control in page['controls'][:3]:
        control['checked'] = control['selector'] in checked
    receipt = {'fact': key, 'action': 'choose', 'selector': '#gender-2',
               'url': page['url'], 'label': 'Decline to self-identify',
               'question': QUESTION, 'approved_value': 'Decline to self-identify'}
    return page, [receipt]


def test_final_check_accepts_exactly_the_granted_member_checked(monkeypatch):
    page, history = final_world(monkeypatch, {'#gender-2'})
    forms.check_approvals(PK, history, page)


@pytest.mark.parametrize('checked', [
    {'#gender-1', '#gender-2'}, {'#gender-0', '#gender-1', '#gender-2'},
    {'#gender-1'}, set()])
def test_final_check_rejects_anything_but_the_granted_member_alone(
        monkeypatch, checked):
    page, history = final_world(monkeypatch, checked)
    with pytest.raises(forms.Gate, match='no longer selected'):
        forms.check_approvals(PK, history, page)


@pytest.mark.parametrize(('checked', 'expected'), [
    ({'#gender-2'}, True), ({'#gender-1'}, False), (set(), False),
    ({'#gender-1', '#gender-2'}, False)])
def test_write_readback_needs_exactly_the_granted_member_checked(checked, expected):
    page = survey_page()
    for control in page['controls'][:3]:
        control['checked'] = control['selector'] in checked
    current = page['controls'][2]
    assert forms._default_radio_choice_read_back(
        page, current, current, 'Decline to self-identify') is expected


def test_final_check_with_the_default_value_as_answer_fails_closed(monkeypatch):
    # The pinned final check still compares by value too: "on" then matches every
    # attribute-less member, so it can never name the one granted member.
    page, history = final_world(monkeypatch, {'#gender-2'})
    history[0]['approved_value'] = 'on'
    with pytest.raises(forms.Gate, match='no longer selected'):
        forms.check_approvals(PK, history, page)


# The final Submit check end to end (check_form -> _final_exact_grant ->
# exact_approval), on the pinned code, for the attribute-less shape.
from tests.tools.test_browser_bootstrap import prepared_without_unapproved_write


def decline_grant(page):
    # A radio grant binds the group's first member, as _bound_target does.
    target = page['controls'][0]
    return {
        'value': 'Decline to self-identify', 'selector': target['selector'],
        'url': page['url'], 'label': target['label'], 'question': target['question'],
        'tag': target['tag'], 'type': target['type'],
        'form_selector': target['form_selector'], 'form_action': target['form_action'],
        'radio_name': target['name'],
        'radio_members': [{'selector': m['selector'], 'caption': m['label'],
                           'value': m['value']} for m in page['controls'][:3]],
    }


@pytest.mark.parametrize('recorded', ['on', 'Decline to self-identify'])
async def test_final_submit_check_binds_the_granted_selector_and_caption(
        monkeypatch, recorded):
    page = survey_page()
    page['controls'][2]['checked'] = True
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    # execute() records the member's machine value, here the implicit "on".
    filled['#gender-2'] = recorded
    row['human_approved_answers'] = {
        forms._key(page, page['controls'][0]): decline_grant(page)}
    forms.check_form(page, filled, uploaded=True)


@pytest.mark.parametrize('mutate,reason', [
    (lambda p: p['controls'][1].update(checked=True),
     'Unverified preselected answer: Female'),
    (lambda p: p['controls'][0].update(checked=True),
     'Unverified preselected answer: Male'),
    (lambda p: p['controls'][2].update(label='Prefer not to say'),
     'not the approved answer'),
    (lambda p: p['controls'][1].update(value_attr_present=True, value='f'),
     'incomplete or ambiguous'),
])
async def test_final_submit_check_refuses_drift_in_the_attribute_less_group(
        monkeypatch, mutate, reason):
    page = survey_page()
    page['controls'][2]['checked'] = True
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    filled['#gender-2'] = 'on'
    row['human_approved_answers'] = {
        forms._key(page, page['controls'][0]): decline_grant(page)}
    forms.check_form(page, filled, uploaded=True)
    mutate(page)
    with pytest.raises(forms.Gate, match=reason):
        forms.check_form(page, filled, uploaded=True)


async def test_final_submit_check_refuses_a_different_member_than_the_grant(
        monkeypatch):
    page = survey_page()
    page['controls'][1].update(checked=True)
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    filled['#gender-1'] = 'on'
    row['human_approved_answers'] = {
        forms._key(page, page['controls'][0]): decline_grant(page)}
    with pytest.raises(forms.Gate):
        forms.check_form(page, filled, uploaded=True)


def test_a_member_caption_never_names_another_member():
    members = group_of(survey_page())
    for a in members:
        named = [b['selector'] for b in members
                 if forms._radio_answer_matches(a['label'].upper(), b)]
        assert named == [a['selector']]
