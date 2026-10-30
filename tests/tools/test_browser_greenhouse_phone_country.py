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
            'selector': '#country', 'display': 'Synthetic Country'}
        country = _by(reader, '#country')
        # Never a caption, form value or Submit authority on the control.
        assert country['combobox']['selected_text'] == ''
        assert country['combobox']['selected_value'] == ''
        assert not country.get('name') and not country.get('has_value')
    assert forms._key(selected['page'], _by(selected['page'], '#country')) == 'Phone country'
    unselected = selected_snapshot('unmounted-placeholder-c0')
    assert unselected['page']['greenhouse']['phone_country'] == {
        'selector': '#country', 'display': ''}
    for mutation in ('second-in-phone-fieldset', 'country-outside-fieldset'):
        out = selected_snapshot(mutation)
        assert out['page']['greenhouse']['phone_country'] == {'selector': '', 'display': ''}


# Preparation: bank-bound exact option, then a re-read. -------------------------

_helpers = runpy.run_path(str(Path(__file__).with_name('test_browser_phone_block.py')))
PhoneSession = _helpers['PhoneSession']
ANSWER = 'Approved country'  # the synthetic DOM's one country option


class GreenhousePhoneSession(PhoneSession):
    """The generic phone DOM with the live captions and the Greenhouse re-read."""

    def __init__(self, shown=None, **scenario):
        self.shown = shown
        super().__init__(**scenario)

    def _live(self, reader):
        for c in reader['controls']:
            if c['selector'] == '#country':
                c['label'] = 'Country*'
            elif c['selector'] == '#phone':
                c['label'] = 'Phone*'
        chosen = self.phases.get('country') == 'C2'
        reader['greenhouse'] = {'phone_pickers': 1, 'phone_country': {
            'selector': '#country',
            'display': (self.shown if self.shown is not None else ANSWER) if chosen else ''}}
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


@pytest.mark.parametrize('shown', ['', 'Another country'])
async def test_reread_mismatch_records_no_choice(phone_country_grant, shown):
    # The click happened, but without the exact shown value nothing is
    # recorded as filled; the one final Submit decision still refuses.
    session, filled = GreenhousePhoneSession(shown=shown, requiredProxy=True), {}
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert receipt['unresolved']
    assert receipt['fact'] == 'Phone country'
    assert filled == {}


async def test_submit_still_refuses_the_receiverless_phone_country(phone_country_grant):
    # Issue 259 criterion (c) is held for an architect ruling: check_form is
    # pinned and still demands a form-owned value, which react-select lacks.
    session, filled = GreenhousePhoneSession(requiredProxy=True), {}
    receipt = await _choose(session, {'Phone country': ANSWER}, filled)
    assert filled == {'#country': ANSWER}
    final = copy.deepcopy(await session.page())
    final['controls'] = [_by(final, '#country')]
    final.update(inventory_verified=True, truncated=False, opaque_controls=False,
                 shadow_roots=False, unsupported_frames=[])
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(final, filled, True, receipts=[receipt])
