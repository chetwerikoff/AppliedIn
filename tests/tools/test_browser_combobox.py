"""Issue #293: ungated accessible combobox preparation and final readback.

Only the single pre-Submit check may deny an incompletely selected required
picker. Ordinary option expansion/selection carries no grant or witness.
All fixtures are synthetic, and browser calls are stubbed offline.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms

URL = 'http://127.0.0.1/fixture/example-co/job/1'
PK = 'example-co#1'
OPTIONS = [
    {'selector': '#country-example', 'unique': True, 'label': 'Example Country',
     'value': 'EX', 'selected': False, 'safe': True},
    {'selector': '#country-other', 'unique': True, 'label': 'Other Country',
     'value': 'OT', 'selected': False, 'safe': True},
]

def control(*, selected_text='', selected_value='', hidden_value='',
            hidden_selector='#country-code', required=True):
    return {
        'selector': '#country', 'label': 'Country', 'question': 'What country?',
        'tag': 'input', 'type': 'combobox', 'required': required, 'disabled': False,
        'value': 'Example Country' if selected_text else '', 'has_value': bool(selected_text),
        'in_form': True, 'form_selector': '#application', 'form_action': URL,
        'formaction': '', 'submit': False, 'checked': False, 'name': 'country',
        'options': [], 'combobox': {
            'linked': True, 'link_id': 'country-list',
            'listbox_selector': '#country-list',
            'options': deepcopy(OPTIONS), 'selected_text': selected_text,
            'displayed_text': selected_text, 'selected_value': selected_value,
            'hidden_selector': hidden_selector, 'hidden_value': hidden_value,
        },
    }

def button():
    return {**control(), 'selector': '#submit', 'label': 'Submit application',
            'question': '', 'tag': 'button', 'type': 'submit', 'required': False,
            'native_form_owner_selector': '#application',
            'submit': True, 'options': [], 'combobox': None}


def page(combo=None):
    target = combo or control()
    controls = [target, button()]
    if target['combobox'] and target['combobox'].get('hidden_selector'):
        value = target['combobox']['hidden_value']
        controls.append({
            'selector': target['combobox']['hidden_selector'],
            'label': 'Country code', 'question': '',
            'tag': 'input', 'type': 'hidden', 'required': False, 'disabled': False,
            'value': value, 'has_value': bool(value),
            'in_form': True, 'form_selector': '#application', 'form_action': URL,
            'formaction': '', 'submit': False, 'checked': False, 'name': 'country_code',
            'options': [],
        })
    return {'url': URL, 'text': 'Synthetic form', 'controls': controls,
            'inventory_verified': True, 'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}


def grant(answer='Example Country'):
    target = control()
    return {'value': answer, 'selector': target['selector'], 'url': URL,
            'label': target['label'], 'question': target['question'],
            'tag': target['tag'], 'type': target['type'],
            'form_selector': target['form_selector'], 'form_action': URL}

class FakeSession:
    def __init__(self, current):
        self.current = deepcopy(current)
        self.calls = []

    async def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {}

    async def page(self):
        return deepcopy(self.current)


def _complete_form(combo):
    form = page(combo)
    form['controls'].append({
        'selector': '#resume', 'label': 'Resume', 'question': 'Resume',
        'type': 'file', 'tag': 'input', 'disabled': False,
        'files': ['Test User.pdf'], 'in_form': True,
        'form_selector': '#application', 'form_action': URL})
    return form


@pytest.mark.asyncio
async def test_current_accessible_option_is_chosen_without_per_widget_grant():
    form = page()
    session = FakeSession(form)
    filled = {}
    result = await forms.execute(
        session, form,
        {'action': 'combobox', 'selector': '#country',
         'value': 'Example Country'}, facts={}, filled=filled,
        resume_path='unused-synthetic.pdf', company='example-co', jd_text='',
        resume_tex='', github='', pk=PK, job_url=URL)
    assert result['action'] == 'combobox'
    assert session.calls == [(('click', '#country-example'), {})]
    assert filled == {'#country': 'Example Country'}


@pytest.mark.asyncio
async def test_unknown_combobox_fact_requests_owner_without_click():
    form = page()
    session = FakeSession(form)
    with pytest.raises(forms.Gate) as failure:
        await forms.execute(
            session, form, {'action': 'combobox', 'selector': '#country'},
            facts={}, filled={}, resume_path='unused-synthetic.pdf',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
    assert failure.value.reason == 'owner_question'
    assert failure.value.form_question['label'] == 'What country?'
    assert not session.calls


@pytest.mark.asyncio
async def test_unmatched_and_ambiguous_options_do_not_invent_a_selection():
    for options in ([], [OPTIONS[0], dict(OPTIONS[0], selector='#duplicate')]):
        combo = control()
        combo['combobox']['options'] = deepcopy(options)
        form = page(combo)
        session = FakeSession(form)
        filled = {}
        await forms.execute(
            session, form, {'action': 'combobox', 'selector': '#country',
                            'value': 'Example Country'},
            facts={}, filled=filled, resume_path='unused-synthetic.pdf',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
        assert ('click', '#country-example') not in [x[0] for x in session.calls]
        assert filled == {'#country': 'Example Country'}
        with pytest.raises(forms.Gate, match='required accessible picker'):
            forms.check_form(_complete_form(combo), filled, True, 'Test User.pdf')


def test_required_picker_uses_fresh_selection_not_model_filled_ledger():
    form = _complete_form(control())
    with pytest.raises(forms.Gate, match='required accessible picker'):
        forms.check_form(form, {'#country': 'Example Country'}, True, 'Test User.pdf')
    selected = _complete_form(control(selected_text='Example Country',
                                      selected_value='EX', hidden_value='EX'))
    forms.check_form(selected, {}, True, 'Test User.pdf')


@pytest.mark.parametrize('mutation', [
    lambda controls: controls[-1].update(files=[]),
    lambda controls: controls.append({
        'selector': '#foreign-file', 'type': 'file', 'files': ['extra.pdf']}),
])
def test_selected_picker_does_not_waive_current_pdf_receiver(mutation):
    form = _complete_form(control(selected_text='Example Country',
                                  selected_value='EX'))
    mutation(form['controls'])
    with pytest.raises(forms.Gate, match='resume input|résumé input'):
        forms.check_form(form, {}, True, 'Test User.pdf')


@pytest.mark.asyncio
async def test_native_select_uses_current_accessible_option_not_approval_ledger():
    select = {
        'selector': '#region', 'label': 'Region', 'question': 'Region',
        'tag': 'select', 'type': 'select', 'disabled': False,
        'options': [{'label': 'Synthetic region', 'value': 'SR'}],
    }
    form = {'controls': [select]}
    session = FakeSession(form)
    filled = {}
    await forms.execute(
        session, form, {'action': 'select', 'selector': '#region',
                        'value': 'Synthetic region'},
        facts={}, filled=filled, resume_path='', company='example-co',
        jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    assert session.calls == [(('select', '#region', '--value', 'SR'), {})]
    assert filled['#region'] == 'SR'
