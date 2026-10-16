"""Only an actually absent exact error ID may lack a mounted reference."""
import json
import subprocess
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
    assert [c['selector'] for c in page['controls']] == [c['selector'] for c in witness['controls']]
    graph = combo(snapshot, 'country')['combobox']
    assert graph == next(c['combobox'] for c in witness['controls'] if c['selector'] == '#country')
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
    {'countryErrorForeignOwner': True},
    {'countryErrorAbsent': True, 'countryAttr': ['aria-describedby', 'country-error']},
    {'countryErrorAbsent': True, 'countryPlaceholderAbsent': True},
    *({'countryErrorAbsent': True, 'countryAttr': [attr, value]}
      for attr in ('aria-describedby', 'aria-errormessage')
      for value in (None, '', ' ', '\t', ' country-error', 'country-error ',
                    'react-select-country-placeholder country-error\t')),
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
    session.snapshot = dom(**session.scenario)
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
    assert [c['selector'] for c in snapshot['page']['controls']] == [c['selector'] for c in snapshot['witness']['controls']]
    assert combo(snapshot, 'country')['combobox'] == next(
        c['combobox'] for c in snapshot['witness']['controls'] if c['selector'] == '#country')
    assert combo(snapshot, 'country')['combobox']['phase'] == 'C0'
    assert b'id="country"' not in fixture._FORM
    assert fixture._FORM == fixture._entry_form()
    assert b"input.setAttribute('aria-describedby','country-error')" not in markup


@pytest.mark.parametrize(('scenario', 'guard'), [
    ({'countryErrorOutside': True}, ' || !path[8].contains(ref)'),
    ({'countryErrorForeignOwner': True}, 'nativeForm(ref)!==form || '),
])
def test_each_reference_ownership_guard_is_independently_necessary(scenario, guard):
    from tools.browser_skill_dom import PAGE
    scripts = [PAGE, forms._FIXED_CONTROL_WITNESS]
    # Remove one predicate in memory, never in the production source. Each reader
    # must independently expose the unsafe graph only when its guard is removed.
    for script in scripts:
        assert script.count(guard) == 1
    snapshot = json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps({'fullPhone': True, **scenario}),
        *(script.replace(guard, '') for script in scripts),
    ], text=True))
    for reader in snapshot.values():
        target = next(c for c in reader['controls'] if c['selector'] == '#country')
        assert target['combobox']['receiverless_country'] is True


def test_untouched_missing_error_c0_is_closed_and_observational():
    from tools.browser_skill_dom import PAGE
    probe = r"""(()=>{
      const result=PRODUCTION;
      const e=document.getElementById('country');
      result.c0={expanded:e.getAttribute('aria-expanded'), value:e.value,
        active:e.getAttribute('aria-activedescendant'), controls:e.hasAttribute('aria-controls'),
        owns:e.hasAttribute('aria-owns'), popup:e.closest('.phone-input__country').querySelectorAll('[role="listbox"]').length,
        placeholder:e.closest('fieldset').querySelectorAll('.select__placeholder').length};
      return result;
    })()"""
    snapshot = json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps({'fullPhone': True, 'countryErrorAbsent': True}),
        probe.replace('PRODUCTION', PAGE), forms._FIXED_CONTROL_WITNESS,
    ], text=True))
    assert snapshot['page']['c0'] == {
        'expanded': 'false', 'value': '', 'active': '', 'controls': False,
        'owns': False, 'popup': 0, 'placeholder': 1}


def test_fixture_aggregate_counts_executed_scenarios(monkeypatch, capsys):
    from tools import browser_skill_fixture as fixture
    calls = []
    def scenario(*args, **kwargs):
        calls.append((args, kwargs))
        return 0
    # Exercise the real public composition without browser, sockets or owner data.
    monkeypatch.setattr(fixture, '_run_scenario', scenario)
    assert fixture.run() == 0
    aggregate = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert calls
    assert aggregate['sessions'] == len(calls)
    assert aggregate['safe_gate'] is True
