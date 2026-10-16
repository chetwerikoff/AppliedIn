"""Only an actually absent exact error ID may lack a mounted reference."""
import runpy
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms

_helpers = runpy.run_path(str(Path(__file__).with_name('test_browser_phone_block.py')))
PhoneSession, dom, combo, choose_country, phone_grants, served_fixture_dom = (
    _helpers[k] for k in ('PhoneSession', 'dom', 'combo', 'choose_country', 'phone_grants', 'served_fixture_dom'))


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
def test_absent_exact_error_in_both_independent_readers(phase):
    snapshot = dom(fullPhone=True, countryErrorAbsent=True, phases={'country': phase})
    page, witness = snapshot['page'], snapshot['witness']
    assert page['inventory_verified'] and not page['opaque_controls'] and not page['truncated']
    assert page['controls'] == witness['controls']
    graph = combo(snapshot, 'country')['combobox']
    assert graph['mode'] == 'closed-react-select' and graph['phase'] == phase
    assert graph['receiverless_country'] and len(graph['country_graph']) == 13
    assert {'#country', '#phone', '#location'} <= {c['selector'] for c in page['controls']}
    assert not {'hidden_selector', 'hidden_value', 'selected_value'} & graph.keys()
    if phase == 'C0':
        assert combo(snapshot, 'country')['value'] == ''
        assert graph['options'] == []
    if phase == 'C2':
        assert graph['selected_text'] == graph['displayed_text'] == 'Approved country'


@pytest.mark.parametrize('scenario', [
    *({'countryErrorAbsent': True, 'countryAttr': [attr, 'react-select-country-placeholder '+bad]}
      for attr in ('aria-describedby', 'aria-errormessage') for bad in ('foo-error', 'location-error')),
    {'countryErrorOutside': True}, {'countryErrorForeign': True}, {'countryErrorDuplicate': True},
    {'countryErrorAbsent': True, 'countryAttr': ['aria-describedby', 'country-error']},
    {'countryErrorAbsent': True, 'countryPlaceholderAbsent': True},
    *({'countryErrorAbsent': True, 'countryAttr': [attr, value]}
      for attr in ('aria-describedby', 'aria-errormessage') for value in (None, '', ' ')),
])
async def test_unproven_reference_still_gates_before_ipc(phone_grants, scenario):
    session = PhoneSession(**scenario)
    page = await session.page()
    assert page['opaque_controls'] and not page['inventory_verified']
    for reader in session.snapshot.values():
        target = next(c for c in reader['controls'] if c['selector'] == '#country')
        assert target['combobox']['quarantined'] is True
        assert target['combobox'].get('mode') != 'closed-react-select'
    with pytest.raises(forms.Gate):
        await choose_country(session, phone_grants[0])
    assert session.calls == []


async def test_read_only_recognition_does_not_cache_missing_error_admission(phone_grants):
    session = PhoneSession(countryErrorAbsent=True)
    await forms._observe_controls(session, await session.page())
    assert session.calls == []
    session.scenario.update(countryErrorAbsent=False, countryErrorOutside=True)
    session.snapshot = dom(fullPhone=True, **session.scenario)
    with pytest.raises(forms.Gate):
        await choose_country(session, phone_grants[0])
    assert session.calls == []


async def test_missing_error_preserves_approved_receipt_and_literal_readbacks(phone_grants):
    session, filled = PhoneSession(countryErrorAbsent=True), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    page, _ = await forms._observe_controls(session, await session.page())
    forms.check_form(page, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], page)
    graph = combo({'page': page}, 'country')['combobox']
    assert graph['selected_text'] == graph['displayed_text'] == 'Approved country'
    assert session.calls == [('click', '#country'), ('click', '#country-option')]


@pytest.mark.parametrize('source', ['_closed_form', '_greenhouse_form'])
def test_native_fixture_bytes_are_absent_error_c0(source):
    from tools import browser_skill_fixture as fixture
    markup = getattr(fixture, source)()
    assert b'id="country-error"' not in markup
    assert b'aria-describedby="react-select-country-placeholder country-error"' in markup
    assert b'aria-errormessage="country-error"' in markup
    snapshot = served_fixture_dom(markup)
    assert snapshot['page']['inventory_verified'] and not snapshot['page']['opaque_controls']
    assert snapshot['page']['controls'] == snapshot['witness']['controls']
    assert combo(snapshot, 'country')['combobox']['phase'] == 'C0'
    assert b'id="country"' not in fixture._FORM
    assert fixture._FORM == fixture._entry_form()
    assert b"input.setAttribute('aria-describedby','country-error')" not in markup
