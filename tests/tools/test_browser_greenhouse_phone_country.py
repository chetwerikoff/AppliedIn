"""Issue 259: the Greenhouse phone fieldset's country picker has its own key.

A read-only probe of the live Waymo, Stripe and Databricks embeds found the
same phone fieldset on all three: legend "Phone", a tel input captioned
"Phone*" and a react-select #country combobox captioned "Country*" (through
aria-labelledby). Both controls take the legend as their question. The legend
branch of _key needs a caption equal to the legend, and the required asterisk
defeated it, so BOTH receivers refused with "More than one control uses the
same approval question" and every embed row gated at bootstrap.

Only that exact pair splits: the tel input keeps "Phone" and the picker gets
"Phone country", bound to that bank fact alone. A bare "Country" fact never
chooses it. The final Submit decision (check_form) is unchanged and still
refuses the receiverless picker; see test_submit_still_refuses_* below.
"""
import copy
import runpy
from pathlib import Path

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
    assert forms._key(page, _by(page, '#phone')) == 'Phone'
    assert forms._key(page, _by(page, '#country')) == 'Phone country'


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


@pytest.mark.parametrize('mutate', [
    _no_reader, _two_pickers, _foreign_picker, _third_sibling, _second_tel,
    _other_combobox_caption, _other_tel_caption, _key_claimed_elsewhere,
])
def test_any_other_shared_question_shape_still_refuses(mutate):
    # The live shape splits; each departure from it keeps the generic refusal.
    _assert_live_split()
    page = live_page()
    mutate(page)
    with pytest.raises(forms.Gate, match=SHARED):
        forms._key(page, _by(page, '#country'))


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
    assert forms._key(selected['page'], _by(selected['page'], '#country')) == 'Phone country'
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


# Preparation: bank-bound exact option, then a re-read. -------------------------

_helpers = runpy.run_path(str(Path(__file__).with_name('test_browser_phone_block.py')))
PhoneSession = _helpers['PhoneSession']
# The synthetic DOM's one country option, captioned like the live ones
# ("United States +1"); its flag and the shown value are the Greenhouse reader's.
ANSWER = 'Approved country +99'
FLAG = 'zz'


class GreenhousePhoneSession(PhoneSession):
    """The generic phone DOM with the live captions and the Greenhouse re-read."""

    def __init__(self, shown=None, shown_flags=None, option_flags=None, **scenario):
        self.shown = shown
        self.shown_flags = [FLAG] if shown_flags is None else shown_flags
        self.option_flags = [FLAG] if option_flags is None else option_flags
        super().__init__(countryCaption=ANSWER, **scenario)

    def _live(self, reader):
        for c in reader['controls']:
            if c['selector'] == '#country':
                c['label'] = 'Country*'
            elif c['selector'] == '#phone':
                c['label'] = 'Phone*'
        chosen = self.phases.get('country') == 'C2'
        opened = self.phases.get('country') == 'C1'
        reader['greenhouse'] = {'phone_pickers': 1, 'phone_country': {
            'selector': '#country',
            'display': (self.shown if self.shown is not None else '+99') if chosen else '',
            'single_values': int(chosen), 'expanded': opened,
            'flags': self.shown_flags if chosen else [],
            'option_flags': {'#country-option': self.option_flags} if opened else {}}}
        return reader

    async def page(self):
        return self._live(copy.deepcopy(self.snapshot['page']))

    async def call(self, *args):
        result = await super().call(*args)
        if args[0] == 'evaluate':
            return {'value': self._live(result['value'])}
        return result


@pytest.fixture
def phone_country_grant(monkeypatch):
    session = GreenhousePhoneSession(requiredProxy=True)
    snapshot = session._live(copy.deepcopy(session.snapshot['page']))
    target = _by(snapshot, '#country')
    grant = {k: target[k] for k in (
        'selector', 'label', 'question', 'tag', 'type', 'form_selector', 'form_action')}
    grant.update(value=ANSWER, url=URL)
    row = {'pk': 'example-co#1', 'status': 'submitting',
           'human_approved_answers': {'Phone country': grant}}
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    return row


async def _choose(session, facts, filled):
    return await forms._execute_combobox(
        session, await session.page(),
        {'action': 'combobox', 'selector': '#country', 'fact': 'Phone country'},
        facts=facts, filled=filled, pk='example-co#1', job_url=URL,
        ctx=None, provenance=None)


async def test_phone_country_fact_selects_the_exact_option_and_rereads_it(
        phone_country_grant):
    session, filled = GreenhousePhoneSession(requiredProxy=True), {}
    original = await session.page()
    assert forms._key(original, _by(original, '#country')) == 'Phone country'
    assert forms._key(original, _by(original, '#phone')) == 'Phone'
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert 'unresolved' not in receipt
    assert receipt['fact'] == 'Phone country'
    assert receipt['committed_caption'] == ANSWER
    assert (receipt['country_flag'], receipt['dial_code']) == (FLAG, '+99')
    assert filled == {'#country': ANSWER}
    assert session.calls == [
        ('click', '#country'),
        ('fill', '#country', '--value', ANSWER),
        ('click', '#country-option')]


async def test_bare_country_fact_opens_nothing(phone_country_grant):
    session, filled = GreenhousePhoneSession(requiredProxy=True), {}
    with pytest.raises(forms.Gate, match='Choose an exact approved answer'):
        await forms._execute_combobox(
            session, await session.page(),
            {'action': 'combobox', 'selector': '#country', 'fact': 'Country'},
            facts={'Country': ANSWER}, filled=filled, pk='example-co#1',
            job_url=URL, ctx=None, provenance=None)
    assert session.calls == [] and filled == {}


@pytest.mark.parametrize('reread', [
    {'shown': ''}, {'shown': '+98'}, {'shown': ANSWER},
    {'shown_flags': ['zy']}, {'shown_flags': [FLAG, FLAG]}, {'shown_flags': []},
])
async def test_reread_mismatch_records_no_choice(phone_country_grant, reread):
    # The click happened, but without the clicked flag and dial code shown
    # nothing is recorded as filled; the one final Submit decision still refuses.
    session, filled = GreenhousePhoneSession(requiredProxy=True, **reread), {}
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert receipt['unresolved']
    assert receipt['fact'] == 'Phone country'
    assert 'country_flag' not in receipt
    assert filled == {}
    assert session.calls[-1] == ('click', '#country-option')


@pytest.mark.parametrize('option_flags', [[], ['zz', 'zy'], [''], ['ZZ']])
async def test_option_without_one_flag_country_is_never_clicked(
        phone_country_grant, option_flags):
    session = GreenhousePhoneSession(option_flags=option_flags, requiredProxy=True)
    filled = {}
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert receipt['unresolved'] and filled == {}
    assert ('click', '#country-option') not in session.calls


def test_dial_code_is_the_captions_one_trailing_code():
    assert forms._dial_code('United States +1') == '+1'
    assert forms._dial_code('Åland Islands +358') == '+358'
    for caption in ('United States', 'United States+1', '+1', 'Code +1 +2',
                    'United States +1 ', 'United States +12345', None):
        assert forms._dial_code(caption) == ''


async def test_non_greenhouse_host_still_refuses_the_receiverless_picker(
        phone_country_grant):
    # The same exact choice on another host keeps the generic final refusal:
    # check_form demands a form-owned value, which react-select lacks.
    session, filled = GreenhousePhoneSession(requiredProxy=True), {}
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert filled == {'#country': ANSWER}
    final = copy.deepcopy(await session.page())
    final['controls'] = [_by(final, '#country')]
    final.update(inventory_verified=True, truncated=False, opaque_controls=False,
                 shadow_roots=False, unsupported_frames=[])
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(final, filled, True, receipts=[receipt])


# Final Submit: criterion (c), ARCH ruling B. ------------------------------------

GH_URL = 'https://job-boards.greenhouse.io/embed/job_app?for=example-co&token=1'
PHONE = '+12025550123'
PK = 'example-co#1'


def submit_page():
    """The live shape at final Submit after this session's exact choice."""
    page = live_page()
    page.update(url=GH_URL, truncated=False, opaque_controls=False,
                shadow_roots=False, unsupported_frames=[])
    page['greenhouse']['phone_country'].update(
        display='+99', single_values=1, expanded=False, flags=[FLAG], option_flags={})
    page['controls'] = [
        _control('#country', 'Country*', typ='combobox', form_action=GH_URL),
        _control('#phone', 'Phone*', typ='tel', form_action=GH_URL,
                 value=PHONE, has_value=True),
        # react-select's own toggle sits in the same fieldset; it answers nothing.
        {**_control('#toggle', 'Toggle flyout', typ='button', form_action=GH_URL),
         'tag': 'button', 'required': False},
    ]
    return page


def submit_receipt(**changes):
    return {'action': 'combobox', 'label': 'Country*', 'selector': '#country',
            'question': 'Phone', 'url': GH_URL, 'fact': 'Phone country',
            'approved_value': ANSWER, 'committed_caption': ANSWER,
            'committed_value': ANSWER, 'hidden_selector': '', 'hidden_value_before': '',
            'hidden_value': '', 'country_flag': FLAG, 'dial_code': '+99', **changes}


@pytest.fixture
def submit_grants(monkeypatch):
    page = submit_page()
    grants = {}
    for key, selector, value in (('Phone country', '#country', ANSWER),
                                 ('Phone', '#phone', PHONE)):
        target = _by(page, selector)
        grants[key] = {k: target[k] for k in (
            'selector', 'label', 'question', 'tag', 'type', 'form_selector', 'form_action')}
        grants[key].update(value=value, url=GH_URL, source='bank-auto')
    row = {'pk': PK, 'status': 'submitting', 'human_approved_answers': grants}
    monkeypatch.setattr(forms, '_row', lambda pk, **_: row)
    return row


def submit_values():
    return forms._ApprovedFormValues({'#country': ANSWER, '#phone': PHONE}, PK,
                                     {'Phone country': ANSWER, 'Phone': PHONE})


def test_exact_greenhouse_phone_country_passes_both_final_guards(submit_grants):
    page, receipts = submit_page(), [submit_receipt()]
    forms.check_form(page, submit_values(), True, receipts=receipts)
    forms.check_approvals(PK, receipts, page)


def _shown_caption_not_code(page, receipts):
    page['greenhouse']['phone_country']['display'] = ANSWER


def _code_mismatch(page, receipts):
    page['greenhouse']['phone_country']['display'] = '+98'


def _flag_mismatch(page, receipts):
    page['greenhouse']['phone_country']['flags'] = ['zy']


def _two_flags(page, receipts):
    page['greenhouse']['phone_country']['flags'] = [FLAG, FLAG]


def _no_flag(page, receipts):
    page['greenhouse']['phone_country']['flags'] = []


def _recorded_flag_differs(page, receipts):
    receipts[0]['country_flag'] = 'zy'


def _recorded_code_differs(page, receipts):
    receipts[0]['dial_code'] = '+98'


def _caption_without_code(page, receipts):
    # The shown flag and code agree with the effect, but the approved caption
    # does not end in that code.
    caption = 'Approved country'
    receipts[0].update(approved_value=caption, committed_caption=caption,
                       committed_value=caption)


def _two_single_values(page, receipts):
    page['greenhouse']['phone_country']['single_values'] = 2


def _open_picker(page, receipts):
    page['greenhouse']['phone_country']['expanded'] = True


def _second_combobox(page, receipts):
    page['controls'].append(_control('#code', 'Code*', typ='combobox', form_action=GH_URL))


def _named_combobox(page, receipts):
    _by(page, '#country')['name'] = 'country'


def _receiver_combobox(page, receipts):
    _by(page, '#country')['combobox'].update(
        hidden_selector='#country-code', hidden_value=ANSWER)


def _no_recorded_effect(page, receipts):
    receipts.clear()


def _effect_not_reread(page, receipts):
    del receipts[0]['country_flag'], receipts[0]['dial_code']


def _near_caption_effect(page, receipts):
    receipts[0]['committed_caption'] = receipts[0]['committed_value'] = ANSWER.upper()


def _other_host(page, receipts):
    page['url'] = receipts[0]['url'] = URL


@pytest.mark.parametrize('mutate', [
    _shown_caption_not_code, _code_mismatch, _flag_mismatch, _two_flags, _no_flag,
    _recorded_flag_differs, _recorded_code_differs, _caption_without_code,
    _two_single_values, _open_picker,
    _second_combobox,
    _named_combobox, _receiver_combobox, _no_recorded_effect, _effect_not_reread,
    _near_caption_effect, _other_host,
])
def test_every_other_phone_country_state_still_refuses_submit(submit_grants, mutate):
    # The exact shape passes (fails on main); each departure keeps a refusal.
    test_exact_greenhouse_phone_country_passes_both_final_guards(submit_grants)
    page, receipts = submit_page(), [submit_receipt()]
    mutate(page, receipts)
    with pytest.raises(forms.Gate):
        forms.check_form(page, submit_values(), True, receipts=receipts)
    if receipts:
        # The receipt comparison is skipped only for the proven exact shape.
        with pytest.raises(forms.Gate):
            forms.check_approvals(PK, receipts, page)
