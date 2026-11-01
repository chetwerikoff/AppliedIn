"""Greenhouse phone-country accessibility and final readback, offline only.

The phone and country controls are ordinary form preparation. A visual flag
or dial code is not a native submitted value and cannot satisfy final Submit.
No widget-scoped grant, historical receipt or country recognizer is required.
"""
import copy

import pytest

from tools import browser_skill_apply as forms

FORM = '#application-form'
URL = 'https://example-co.test/job/1'
SHARED = 'More than one control uses the same approval question'


def _control(selector, label, *, typ, question='Phone', form=FORM, **extra):
    return {'selector': selector, 'label': label, 'question': question,
            'tag': 'input', 'type': typ, 'native_type': 'tel' if typ == 'tel' else '',
            'in_form': True, 'form_selector': form, 'form_action': URL,
            'name': '', 'required': True, 'value': '', 'has_value': False,
            'checked': False, 'disabled': False,
            'combobox': ({'linked': False, 'link_id': '', 'listbox_selector': '',
                          'options': [], 'selected_text': '', 'displayed_text': '',
                          'selected_value': '', 'hidden_selector': '', 'hidden_value': ''}
                         if typ == 'combobox' else None), **extra}


def live_page():
    """The value-free shape every probed live embed returned from GREENHOUSE_PAGE."""
    return {'url': URL, 'inventory_verified': True,
            'greenhouse': {'phone_pickers': 1,
                           'phone_country': {'selector': '#country', 'display': ''}},
            'controls': [
                _control('#first_name', 'First Name*', typ='text', question=''),
                _control('#country', 'Country*', typ='combobox'),
                _control('#phone', 'Phone*', typ='tel'),
                _control('#question_1', 'Please select the country where you reside*',
                         typ='combobox', question=''),
            ]}


def _by(page, selector):
    return next(c for c in page['controls'] if c['selector'] == selector)


def _assert_live_split():
    page = live_page()
    phone, country = _by(page, '#phone'), _by(page, '#country')
    assert phone['selector'] != country['selector']
    assert phone['type'] == 'tel' and country['type'] == 'combobox'
    assert phone['label'] == 'Phone*' and country['label'] == 'Country*'
    assert phone['form_selector'] == country['form_selector']


def test_live_phone_fieldset_gives_tel_and_country_distinct_keys():
    _assert_live_split()


def _no_reader(page):
    del page['greenhouse']


def _two_pickers(page):
    page['greenhouse']['phone_pickers'] = 2


def _foreign_picker(page):
    page['greenhouse']['phone_country']['selector'] = '#question_1'


def _third_sibling(page):
    page['controls'].append(_control('#extension', 'Extension', typ='text'))


def _second_tel(page):
    page['controls'].append(_control('#mobile', 'Mobile*', typ='tel'))


def _other_combobox_caption(page):
    _by(page, '#country')['label'] = 'Region*'


def _other_tel_caption(page):
    _by(page, '#phone')['label'] = 'Mobile*'


def _key_claimed_elsewhere(page):
    page['controls'].append(_control('#question_2', 'Phone country*', typ='text', question=''))


def test_bare_country_fact_never_answers_the_phone_country_key():
    # A residence "Country" fact is not the dialling country. Only the bank
    # fact "Phone country" may bind the picker, and nothing infers it.
    _assert_live_split()
    assert forms._fact_key({'Country': 'Example Country'}, 'Phone country') is None
    assert forms._fact_key({'Phone country': 'Example Country'},
                           'Phone country') == 'Phone country'


# Greenhouse reader: the re-read is reported apart from the control. ------------

def test_greenhouse_reader_reports_the_shown_country_only_beside_the_tel_input():
    from tests.tools.test_browser_greenhouse_selected_inventory import selected_snapshot
    selected = selected_snapshot()
    for reader in (selected['page'], selected['witness']):
        assert reader['greenhouse']['phone_country'] == {
            'selector': '#country', 'display': 'Synthetic Country',
            'single_values': 1, 'expanded': False, 'flags': [], 'option_flags': {}}
        country = _by(reader, '#country')
        # Never a caption, form value or Submit authority on the control.
        assert country['combobox']['selected_text'] == ''
        assert country['combobox']['selected_value'] == ''
        assert not country.get('name') and not country.get('has_value')
    assert _by(selected['page'], '#country')['type'] == 'combobox'
    unselected = selected_snapshot('unmounted-placeholder-c0')
    assert unselected['page']['greenhouse']['phone_country'] == {
        'selector': '#country', 'display': '', 'single_values': 0, 'expanded': False,
        'flags': [], 'option_flags': {}}
    for mutation in ('second-in-phone-fieldset', 'country-outside-fieldset'):
        out = selected_snapshot(mutation)
        assert out['page']['greenhouse']['phone_country'] == {
            'selector': '', 'display': '', 'single_values': 0, 'expanded': False,
            'flags': [], 'option_flags': {}}


@pytest.mark.parametrize('mutation, flags', [
    # Live: <div class="iti__flag iti__<iso2>"></div><span>+code</span>.
    ('flagged-country', ['zz']),
    ('two-flag-country', ['zz', 'zy']),
    ('unknown-flag-country', ['']),
])
def test_greenhouse_reader_reports_the_shown_flag_and_dial_code(mutation, flags):
    from tests.tools.test_browser_greenhouse_selected_inventory import selected_snapshot
    out = selected_snapshot(mutation)
    for reader in (out['page'], out['witness']):
        assert reader['greenhouse']['phone_country'] == {
            'selector': '#country', 'display': '+99', 'single_values': 1,
            'expanded': False, 'flags': flags, 'option_flags': {}}
        assert _by(reader, '#country')['combobox']['selected_text'] == ''


# Ordinary input/combobox preparation has no phone-country grant layer.

@pytest.mark.asyncio
async def test_phone_and_country_are_ordinary_ungated_preparation():
    page = live_page()
    country = _by(page, '#country')
    country['combobox']['options'] = [
        {'selector': '#country-option', 'label': 'Synthetic Country',
         'value': 'EX', 'safe': True}]
    class Session:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append(args)
            return {}

    session = Session()
    filled = {}
    arguments = dict(facts={'Phone': '+15550101010',
                            'Country': 'Synthetic Country'},
                     filled=filled, resume_path='', company='example-co',
                     jd_text='Synthetic job', resume_tex='', github='',
                     pk='example-co#1', job_url=URL)
    await forms.execute(session, page,
                        {'action': 'fill', 'selector': '#phone', 'fact': 'Phone'},
                        **arguments)
    await forms.execute(session, page,
                        {'action': 'combobox', 'selector': '#country',
                         'fact': 'Country'}, **arguments)
    assert session.calls == [
        ('fill', '#phone', '--value', '+15550101010'),
        ('click', '#country-option')]
    assert filled == {'#phone': '+15550101010', '#country': 'Synthetic Country'}


@pytest.mark.parametrize('country_value,selected_text,valid', [
    ('EX', 'Synthetic Country', True),
    ('', '', False),
])
def test_current_country_receiver_and_pdf_govern_final_readback(
        country_value, selected_text, valid):
    """Visual country flag alone cannot replace a submitted native answer."""
    phone = _control('#phone', 'Phone*', typ='tel', question='',
                     value='+15550101010', required=True)
    country = _control('#country', 'Country*', typ='combobox', question='',
                       value=country_value, required=True)
    country['combobox']['selected_text'] = selected_text
    file_field = {
        'selector': '#resume', 'label': 'Resume', 'type': 'file',
        'tag': 'input', 'files': ['Test User Resume.pdf'],
    }
    page = {'url': URL, 'controls': [phone, country, file_field],
            'inventory_verified': True, 'truncated': False,
            'opaque_controls': False, 'unsupported_frames': [],
            'shadow_roots': False}
    if valid:
        forms.check_form(page, {}, True, 'Test User Resume.pdf')
    else:
        with pytest.raises(forms.Gate, match='required accessible picker'):
            forms.check_form(page, {}, True, 'Test User Resume.pdf')


def test_visible_country_without_form_value_is_not_a_submit_answer():
    from tests.tools.test_browser_greenhouse_selected_inventory import selected_snapshot
    observed = selected_snapshot('flagged-country')['page']
    country = copy.deepcopy(_by(observed, '#country'))
    country.update(required=True, value='')
    country['combobox']['selected_text'] = ''
    page = {'url': observed['url'], 'controls': [
        country, {'selector': '#resume', 'label': 'Resume',
                  'tag': 'input', 'type': 'file',
                  'files': ['Test User Resume.pdf']}],
        'inventory_verified': True, 'truncated': False,
        'opaque_controls': False, 'unsupported_frames': [],
        'shadow_roots': False}
    assert observed['greenhouse']['phone_country']['display'] == '+99'
    with pytest.raises(forms.Gate, match='required accessible picker'):
        forms.check_form(page, {}, True, 'Test User Resume.pdf')
