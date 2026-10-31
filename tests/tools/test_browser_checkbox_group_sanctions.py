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
         'Not applicable (i.e., I selected “none of the above” for the prior question)']

SHAPES = {
    'countries': (COUNTRIES_Q, COUNTRIES, ['None of the above']),
    'lists': (LISTS_Q, LISTS, LISTS[2:]),
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


@pytest.mark.parametrize(('answer', 'gates'), [('Yes', True), ('No', False)])
def test_standalone_sanctions_radio_guard_is_unchanged(answer, gates):
    """Radios never take the group caption route: answer and caption are guarded."""
    page = page_of(*[field(label, f'#r-{label}', type='radio', value=label,
                           has_value=True, name='sanctioned', question=LISTS_Q,
                           form_action=URL) for label in ('Yes', 'No')])
    radio = by(page, f'#r-{answer}')
    if gates:
        with pytest.raises(forms.Gate, match=SANCTIONS):
            forms._guard_checkbox_choice(page, radio, answer)
    else:
        forms._guard_checkbox_choice(page, radio, answer)


def test_group_route_only_for_an_explicit_yes(row):
    """A non-yes group answer keeps the current guard: here the sanctions gate."""
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    none = by(page, f'#opt-{COUNTRIES.index("None of the above")}')
    forms._guard_checkbox_choice(page, none, 'yes')
    with pytest.raises(forms.Gate, match=SANCTIONS):
        forms._guard_checkbox_choice(page, none, 'on')


def test_exact_follow_up_caption_needs_no_regex_change():
    caption = LISTS[3]
    assert forms._SAFE_OPTION_RX.match(caption)
    assert not forms._SAFE_OPTION_RX.match(LISTS[0])


def _spy(monkeypatch, name):
    """Record each call of a module function with the source line that made it."""
    import inspect
    real, calls = getattr(forms, name), []

    def spy(*args, **kwargs):
        caller = inspect.stack(context=1)[1]
        calls.append((caller.function, (caller.code_context or [''])[0].strip(), args))
        return real(*args, **kwargs)
    monkeypatch.setattr(forms, name, spy)
    return calls


async def test_pre_click_site_guards_the_group_caption(row, monkeypatch):
    """execute guards again right before its click, on the LATEST page.

    That pre-click guard was the fifth 'yes' site this Issue found. It must take
    the caption route for a safe negative and still gate an affirmative member.
    """
    calls = _spy(monkeypatch, '_guard_checkbox_choice')
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    answer(row, page, ['None of the above'])
    none = by(page, f'#opt-{COUNTRIES.index("None of the above")}')
    await forms.execute(
        ToggleBrowser(page), page,
        {'action': 'choose', 'selector': none['selector'],
         'fact': forms._member_key(none)},
        facts={}, filled=forms._ApprovedFormValues({}, PK, {}),
        resume_path='Resume.pdf', company='example-co', jd_text='',
        resume_tex='', github='', pk=PK, job_url=URL)
    pre_click = [c for c in calls if c[0] == 'execute'
                 and c[1] == '_guard_checkbox_choice(latest, current, value)']
    assert len(pre_click) == 1
    assert pre_click[0][2][1]['selector'] == none['selector']
    assert pre_click[0][2][2] == 'yes'
    assert none['checked'] is True

    # The same pre-click site gates an affirmative member when only it sees one:
    # every earlier guard is satisfied, then the latest caption is Cuba.
    real = forms._guard_checkbox_choice

    def only_pre_click_sees_cuba(page_, selected, answer_):
        import inspect
        line = (inspect.stack(context=1)[1].code_context or [''])[0].strip()
        if line == '_guard_checkbox_choice(latest, current, value)':
            selected = {**selected, 'label': 'Cuba'}
        return real(page_, selected, answer_)
    monkeypatch.setattr(forms, '_guard_checkbox_choice', only_pre_click_sees_cuba)
    page = sanctions_page(COUNTRIES_Q, COUNTRIES)
    row['human_approved_answers'].clear()
    answer(row, page, ['None of the above'])
    none = by(page, f'#opt-{COUNTRIES.index("None of the above")}')
    browser = ToggleBrowser(page)
    with pytest.raises(forms.Gate, match=SANCTIONS):
        await forms.execute(
            browser, page,
            {'action': 'choose', 'selector': none['selector'],
             'fact': forms._member_key(none)},
            facts={}, filled=forms._ApprovedFormValues({}, PK, {}),
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='', pk=PK, job_url=URL)
    assert browser.clicks == []          # refused before the click


def test_every_non_group_or_non_yes_answer_uses_the_pinned_guard(row, monkeypatch):
    """The wrapper changes ONLY a checkbox-group member's yes; all else delegates."""
    pinned = _spy(monkeypatch, '_guard_native_choice_answer')
    # A standalone checkbox yes (non-group) goes through the pinned helper,
    # also on the real _approved_native_choice route.
    page = page_of(field('Independent acknowledgement', '#agree', type='checkbox',
                         value='on', has_value=True,
                         question='Independent acknowledgement', form_action=URL))
    agree = by(page, '#agree')
    assert forms._checkbox_group(page, agree) is None
    row['human_approved_answers'][forms._key(page, agree)] = {
        'value': 'yes', 'selector': agree['selector'], 'url': page['url'],
        'label': agree['label'], 'question': agree['question'],
        'tag': 'input', 'type': 'checkbox',
        'form_selector': agree['form_selector'],
        'form_action': agree['form_action'], 'visible_text': agree['label'],
    }
    assert forms._approved_native_choice(page, agree, PK)[0] == 'yes'
    assert [(c[0], c[2]) for c in pinned] == [('_guard_checkbox_choice', (agree, 'yes'))]

    # A group member with a non-yes answer delegates too.
    pinned.clear()
    group_page_ = sanctions_page(LISTS_Q, LISTS)
    member = by(group_page_, '#opt-2')
    with pytest.raises(forms.Gate, match=SANCTIONS):
        forms._guard_checkbox_choice(group_page_, member, 'on')
    assert [c[2] for c in pinned] == [(member, 'on')]

    # Only a group member's yes takes the caption route, without the pinned helper.
    pinned.clear()
    forms._guard_checkbox_choice(group_page_, member, 'yes')
    assert pinned == []
