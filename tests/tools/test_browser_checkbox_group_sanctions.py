"""Issue 287: a sanctions checkbox group answered with its safe negative option.

Two live native checkbox groups carried a sanctions / restricted-country
question. The owner chose the safe negative option, yet the apply path raised
"A sanctions/restricted-country question requires its safe negative option."
with no owner question token.

Cause: every per-member choice guard checked the member's machine answer
``yes`` under the sanctions question. ``yes`` is never a safe negative, so even
"None of the above" gated. For a group member, ``yes`` only means "this option
is checked"; the answer is the option's visible caption. The guard now checks
that caption, so the group passes only when every checked option is a safe
negative. Unchecked members are not answers. A standalone sanctions checkbox
keeps its old fail-closed behavior. The pages are synthetic.
"""
from __future__ import annotations

import json

import pytest

from tests.tools.test_browser_bootstrap import PK, field
from tests.tools.test_browser_checkbox_group import (
    ToggleBrowser, URL, by, page_of, row, run_final,  # noqa: F401  (row is a fixture)
)
from tools import browser_skill_apply as forms

SANCTIONS = 'requires its safe negative option'

# Shape 1: a country list with a "None of the above" option.
COUNTRIES_Q = ('Are you a citizen or resident of, or located in, any of these '
               'countries or regions: Cuba, Iran, North Korea, Syria, or the '
               'Crimea, Donetsk or Luhansk regions of Ukraine?')
COUNTRIES = ['Cuba', 'Iran', 'North Korea', 'Syria',
             'Crimea, Donetsk or Luhansk regions of Ukraine', 'None of the above']
# Shape 2: sanctions-list statements with a negative and a dependent
# not-applicable option for the prior "None of the above" answer.
LISTS_Q = ('Are you, or is any entity you own, subject to sanctions or listed '
           'on a restricted party list?')
LISTS = ['I am listed on a sanctions list',
         'An entity I own is a restricted party',
         'No, none of these apply to me',
         'Not applicable, I answered none of the above to the prior question']

SHAPES = {
    'countries': (COUNTRIES_Q, COUNTRIES, ['None of the above']),
    'lists': (LISTS_Q, LISTS, ['No, none of these apply to me',
                               'Not applicable, I answered none of the above '
                               'to the prior question']),
}


def sanctions_page(question, options):
    return page_of(*[field(label, f'#opt-{i}', type='checkbox', value=f'v{i}',
                           has_value=True, question=question, form_action=URL)
                     for i, label in enumerate(options)])


def members(page):
    return [c for c in page['controls'] if c.get('type') == 'checkbox']


def answer(row, page, selected):
    group = forms._checkbox_group(page, members(page)[0])
    assert group is not None and len(group) == len(members(page))
    first = group[0]
    row['human_approved_answers'][first['question']] = {
        'value': json.dumps(selected), 'selector': first['selector'],
        'url': page['url'], 'label': first['label'], 'question': first['question'],
        'tag': 'input', 'type': 'checkbox_group',
        'form_selector': first['form_selector'], 'form_action': first['form_action'],
        'options': [c['label'] for c in group],
        'group_members': forms._group_binding(group), 'selected': list(selected)}
    forms._derive_group_member_grants(page, PK)


@pytest.mark.parametrize('shape', SHAPES)
async def test_safe_negative_group_selection_passes_every_choice_guard(row, shape):
    question, options, selected = SHAPES[shape]
    page = sanctions_page(question, options)
    assert forms._SANCTIONS_RX.search(question)
    answer(row, page, selected)
    filled = forms._ApprovedFormValues({}, PK, {})
    browser = ToggleBrowser(page)
    for member in members(page):
        if member['label'] not in selected:
            # An unchecked member is not an answer and is never chosen.
            assert forms._approved_native_choice(page, member, PK) is None
            continue
        assert forms._approved_native_choice(page, member, PK)[0] == 'yes'
        await forms.execute(
            browser, page,
            {'action': 'choose', 'selector': member['selector'],
             'fact': forms._member_key(member)},
            facts={}, filled=filled, resume_path='Resume.pdf',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
        assert member['checked'] is True
    assert [c['label'] for c in members(page) if c['checked']] == selected
    run_final(page, filled)


@pytest.mark.parametrize('shape', SHAPES)
async def test_prechecked_safe_negative_group_reconciles(row, shape):
    question, options, selected = SHAPES[shape]
    page = sanctions_page(question, options)
    answer(row, page, selected)
    for member in members(page):
        member['checked'] = True       # every option prechecked by the page
    browser = ToggleBrowser(page)
    filled = {}
    current, _ = await forms._reconcile_checkbox_group(
        browser, page, members(page)[0], PK, None, filled)
    assert [c['label'] for c in members(page) if c['checked']] == selected
    run_final(current, forms._ApprovedFormValues(filled, PK, {}))


@pytest.mark.parametrize('selected', [
    ['Cuba'], ['Cuba', 'None of the above'], ['Iran', 'Syria']])
def test_affirmative_country_selection_gates(row, selected):
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    answer(row, page, selected)
    affirmative = by(page, f'#opt-{COUNTRIES.index(selected[0])}')
    with pytest.raises(forms.Gate, match=SANCTIONS):
        forms._approved_native_choice(page, affirmative, PK)


def test_affirmative_list_selection_gates(row):
    page = sanctions_page(LISTS_Q, LISTS)
    answer(row, page, [LISTS[0], LISTS[2]])
    with pytest.raises(forms.Gate, match=SANCTIONS):
        forms._approved_native_choice(page, by(page, '#opt-0'), PK)


async def test_affirmative_member_cannot_be_chosen_by_the_controller(row):
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    answer(row, page, ['Cuba'])
    with pytest.raises(forms.Gate, match=SANCTIONS):
        await forms.execute(
            ToggleBrowser(page), page,
            {'action': 'choose', 'selector': '#opt-0',
             'fact': forms._member_key(by(page, '#opt-0'))},
            facts={}, filled=forms._ApprovedFormValues({}, PK, {}),
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='', pk=PK, job_url=URL)


async def test_prechecked_affirmative_selection_gates_in_reconciliation(row):
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    answer(row, page, ['Iran', 'None of the above'])
    for member in members(page):
        member['checked'] = member['label'] in {'Iran', 'None of the above'}
    with pytest.raises(forms.Gate, match=SANCTIONS):
        await forms._reconcile_checkbox_group(
            ToggleBrowser(page), page, members(page)[0], PK, None, {})


def test_checked_affirmative_member_fails_the_final_check(row):
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    answer(row, page, ['Cuba'])
    cuba = by(page, '#opt-0')
    cuba['checked'] = True
    filled = forms._ApprovedFormValues({cuba['selector']: cuba['value']}, PK, {})
    with pytest.raises(forms.Gate, match=SANCTIONS):
        run_final(page, filled)


def test_standalone_sanctions_checkbox_keeps_its_gate(row):
    """One checkbox is not an option group: its yes stays fail-closed."""
    page = page_of(field('I confirm', '#confirm', type='checkbox', value='on',
                         has_value=True, question=COUNTRIES_Q, form_action=URL))
    target = by(page, '#confirm')
    assert forms._checkbox_group(page, target) is None
    row['human_approved_answers'][forms._key(page, target)] = {
        'value': 'yes', 'selector': target['selector'], 'url': page['url'],
        'label': target['label'], 'question': target['question'],
        'tag': 'input', 'type': 'checkbox',
        'form_selector': target['form_selector'],
        'form_action': target['form_action'], 'visible_text': target['label'],
    }
    with pytest.raises(forms.Gate, match=SANCTIONS):
        forms._approved_native_choice(page, target, PK)


@pytest.mark.parametrize('shape', SHAPES)
async def test_bootstrap_accounts_a_prechecked_safe_negative_group(row, shape):
    from tests.tools.test_browser_bootstrap import (
        SECURE_URL, URL as RAW_URL, secure_inventory)

    question, options, selected = SHAPES[shape]
    page = secure_inventory(*[
        field(label, f'#opt-{i}', type='checkbox', value=f'v{i}', has_value=True,
              question=question, form_action=RAW_URL)
        for i, label in enumerate(options)])
    row.update(company='example-co', job_id='1', jd_url=SECURE_URL,
               title=page['title'], ats='custom')
    answer(row, page, selected)
    for member in members(page):
        member['checked'] = member['label'] in selected
    browser = ToggleBrowser(page)
    filled = {}
    await forms._bootstrap(
        browser, page, PK, {}, SECURE_URL, filled=filled, history=[],
        job_url=SECURE_URL, company='example-co')
    assert browser.clicks == []
    assert set(filled) == {m['selector'] for m in members(page)
                           if m['label'] in selected}
