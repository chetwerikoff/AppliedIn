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
    # A dangling error IDREF is an unproved graph, not an excuse to mint
    # final submission evidence from a country-picker caption.
    assert not page['inventory_verified'] and page['opaque_controls'] and not page['truncated']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    field = combo(snapshot, 'country')
    graph = field['combobox']
    assert graph == next(c['combobox'] for c in witness['controls']
                         if c['selector'] == '#country')
    assert field['type'] == 'combobox' and not field.get('name')
    assert {'#country', '#phone', '#location'} <= {
        c['selector'] for c in page['controls']}
    assert graph.get('selected_value', '') == graph.get('hidden_value', '') == ''
    if phase == 'C0':
        assert field['value'] == ''
        assert graph['options'] == []
    # Even if a selected caption is visible at C2, no named form value may
    # be inferred from it while the current receiver remains unproved.
    if phase == 'C2':
        assert not field.get('has_value')
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, True)


@pytest.mark.parametrize('attr', ['aria-describedby', 'aria-errormessage'])
async def test_padded_idref_list_is_valid_in_both_readers(phone_grants, attr):
    # ARIA lists may have surrounding whitespace; only an empty list is malformed.
    session = PhoneSession(countryErrorAbsent=True, countryAttr=[
        attr, ' react-select-country-placeholder country-error '])
    page, _ = await forms._observe_controls(session, await session.page())
    # Whitespace around IDREF tokens is not a native form receiver. The
    # generic reader keeps its current opacity instead of a shape exemption.
    assert page['opaque_controls'] and not page['inventory_verified']
    country = combo({'page': page}, 'country')
    graph = country['combobox']
    witness = next(c for c in session.snapshot['witness']['controls']
                   if c['selector'] == '#country')
    assert graph == witness['combobox']
    assert not country.get('name') and graph.get('selected_value', '') == ''
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, True)
    assert session.calls == []


@pytest.mark.parametrize('scenario', [
    *({'countryErrorAbsent': True, 'countryAttr': [attr, 'react-select-country-placeholder '+bad]}
      for attr in ('aria-describedby', 'aria-errormessage') for bad in ('foo-error', 'location-error')),
    {'countryErrorOutside': True}, {'countryErrorForeign': True}, {'countryErrorDuplicate': True},
    {'countryErrorForeignOwner': True},
    {'countryErrorAbsent': True, 'countryAttr': ['aria-describedby', 'country-error']},
    {'countryErrorAbsent': True, 'countryPlaceholderAbsent': True},
    *({'countryErrorAbsent': True, 'countryAttr': [attr, value]}
      for attr in ('aria-describedby', 'aria-errormessage')
      for value in (None, '', ' ', '\t')),
])
async def test_unproven_reference_still_gates_before_ipc(
        phone_grants, scenario, monkeypatch, tmp_path):
    """Ambiguous ARIA references never grant a final application Submit."""
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    session = PhoneSession(**scenario)
    snapshot = session.snapshot
    page, fixed = snapshot['page'], snapshot['witness']
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in fixed['controls']]
    current = next(c for c in page['controls'] if c['selector'] == '#country')
    independent = next(c for c in fixed['controls']
                       if c['selector'] == '#country')
    assert current['combobox'] == independent['combobox']
    assert not current['combobox'].get('selected_text', '')
    assert not current['combobox'].get('selected_value', '')
    assert session.calls == []
    # r06 removed the C0/C1 quarantine certificate as an early preparation
    # permission. The real raw-click guard still refuses every malformed
    # reference case with zero Submit IPC and no synthetic approval.
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)
    assert session.calls == []


async def test_read_only_recognition_does_not_cache_missing_error_admission(
        phone_grants, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    session = PhoneSession(countryErrorAbsent=True)
    await forms._observe_controls(session, await session.page())
    assert session.calls == []
    # A later DOM with a foreign ARIA error owner loses inspectability.
    # Reversible preparation is not required to throw a human Gate here:
    # the real final raw-click guard must refuse with zero Submit IPC.
    session.scenario.update(countryErrorAbsent=False, countryErrorOutside=True)
    session.snapshot = dom(**session.scenario)
    assert session.snapshot['page']['inventory_verified'] is False
    assert session.snapshot['page']['opaque_controls'] is True
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path)
    assert session.calls == []


async def test_missing_error_preserves_approved_receipt_and_literal_readbacks(phone_grants):
    session, filled = PhoneSession(countryErrorAbsent=True), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    page, _ = await forms._observe_controls(session, await session.page())
    # The reversible picker option was clicked with an exact grant. A missing
    # referenced native receiver still refuses at final Submit, with no second
    # option click and no fabricated submitted-value evidence.
    assert receipt['action'] == 'combobox'
    assert receipt['committed_caption'] == receipt['committed_value'] == 'Approved country'
    graph = combo({'page': page}, 'country')['combobox']
    assert graph.get('selected_value', '') == ''
    with pytest.raises(forms.Gate, match='form inventory is incomplete'):
        forms.check_form(page, filled, True, receipts=[receipt])
    with pytest.raises(forms.Gate):
        forms.check_approvals('example-co#1', [receipt], page)
    assert session.calls == [
        ('click', '#country'),
        ('fill', '#country', '--value', 'Approved country'),
        ('click', '#country-option')]


@pytest.mark.parametrize('source', ['_closed_form', '_greenhouse_form'])
def test_native_fixture_bytes_are_absent_error_c0(source):
    from tools import browser_skill_fixture as fixture
    markup = getattr(fixture, source)()
    assert b'id="country-error"' not in markup
    assert b'aria-describedby="react-select-country-placeholder country-error"' in markup
    assert b'aria-errormessage="country-error"' in markup
    if source == '_greenhouse_form':
        # This reference test inspects the mounted country widget. The parser
        # cannot run hydration; initial nonqualification is tested separately.
        markup = markup.replace(b'<template id="greenhouse-template">', b'').replace(b'</template>', b'')
    snapshot = served_fixture_dom(markup)
    assert snapshot['page']['opaque_controls'] and not snapshot['page']['inventory_verified']
    assert [c['selector'] for c in snapshot['page']['controls']] == [
        c['selector'] for c in snapshot['witness']['controls']]
    assert combo(snapshot, 'country')['combobox'] == next(
        c['combobox'] for c in snapshot['witness']['controls']
        if c['selector'] == '#country')
    country = combo(snapshot, 'country')
    assert country['type'] == 'combobox' and not country.get('name')
    assert country['combobox'].get('selected_value', '') == ''
    with pytest.raises(forms.Gate):
        forms.check_form(snapshot['page'], {}, True)
    # The four-leg harness retains Ashby separately from the native W entry.
    assert b'id="country"' not in fixture._ashby_form()
    assert fixture._FORM == fixture._entry_form()
    assert b"input.setAttribute('aria-describedby','country-error')" not in markup


@pytest.mark.parametrize(('scenario', 'guard'), [
    ({'countryErrorOutside': True}, ' || !path[8].contains(ref)'),
    ({'countryErrorForeignOwner': True}, 'nativeForm(ref)!==form || '),
])
def test_each_reference_ownership_guard_is_independently_necessary(scenario, guard):
    from tools.browser_skill_dom import PAGE
    scripts = [PAGE, forms._FIXED_CONTROL_WITNESS]
    # Execute both real, current readers on the same foreign IDREF DOM. The
    # cross-form/foreign reference stays unapproved at the final form guard.
    snapshot = json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps({'fullPhone': True, **scenario}), *scripts,
    ], text=True))
    page, witness = snapshot['page'], snapshot['witness']
    assert page['opaque_controls'] and not page['inventory_verified']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    field = next(c for c in page['controls'] if c['selector'] == '#country')
    independent = next(c for c in witness['controls'] if c['selector'] == '#country')
    assert field['combobox'] == independent['combobox']
    assert field['combobox'].get('selected_value', '') == ''
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, True)


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
