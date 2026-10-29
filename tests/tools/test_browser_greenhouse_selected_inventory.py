"""Synthetic settled Greenhouse country/location inventory, without a browser.

The second combobox case is intentionally adversarial: it proves the fieldset
does not grant authority to another widget, not that this was the live second node.
"""
from tests.tools.reader_node_harness import reader_output
import json
import re
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_greenhouse_fixture import Tree
from tests.tools.test_browser_greenhouse_parity import instrument


def _find(node, node_id):
    if node['attrs'].get('id') == node_id:
        return node
    for child in node['children']:
        hit = _find(child, node_id)
        if hit is not None:
            return hit
    return None


def selected_snapshot(mutation=''):
    markup = fixture._greenhouse_form().decode()
    # Hydration mounts the template before selection. The Greenhouse-specific
    # country commit replaces its placeholder with an ID-less single-value
    # branch; location and the other selected controls retain their receivers.
    markup = re.sub(r'</form>.*?<script>', '</form><script>', markup, flags=re.S)
    markup = markup.replace('<template id="greenhouse-template">', '').replace('</template>', '')
    location_placeholder = '<div class="react-select__placeholder">Choose</div>'
    country_placeholder = (
        '<div id="react-select-country-placeholder" class="select__placeholder">Choose</div>')
    proxy = '<input required tabindex="-1" aria-hidden="true" class="validation-only" value="">'
    assert markup.count(location_placeholder) == 4
    assert markup.count(country_placeholder) == 1
    assert markup.count(proxy) == 2
    markup = markup.replace(location_placeholder,
                            '<div class="react-select__single-value">Synthetic Example City</div>', 1)
    # The actual Greenhouse fixture replaces, rather than renames, the C0
    # placeholder on selection. Retained-ID is a compatibility/control contrast.
    if mutation == 'retained-country-placeholder':
        selected_country = ('<div id="react-select-country-placeholder" '
                            'class="select__single-value">Synthetic Country</div>')
    elif mutation == 'unmounted-placeholder-c0':
        selected_country = '<div class="select__placeholder">Choose</div>'
    else:
        selected_country = '<div class="select__single-value">Synthetic Country</div>'
    markup = markup.replace(country_placeholder, selected_country, 1)
    markup = markup.replace(proxy, '')
    markup = markup.replace(
        '<input type="hidden" id="location-code" name="location_value">',
        '<input type="hidden" id="location-code" name="location_value" value="Synthetic Example City">')
    tree = Tree()
    tree.feed(markup)
    assert not tree.stack
    form = _find(tree.root, 'application-form')
    country = _find(tree.root, 'country')
    fieldset = _find(tree.root, 'country-label')
    assert form is not None and country is not None and fieldset is not None
    if mutation == 'unknown-country-reference':
        country['attrs']['aria-describedby'] += ' synthetic-unresolved-ref'
    elif mutation == 'only-missing-country-error':
        country['attrs']['aria-describedby'] = 'country-error'
    elif mutation in {'duplicate-country-placeholder', 'foreign-country-placeholder'}:
        placeholder = dict(tag='span', attrs={'id': 'react-select-country-placeholder'},
                           text='', children=[])
        if mutation == 'duplicate-country-placeholder':
            owner = _find(tree.root, 'country-owner')
            assert owner is not None
            owner['children'].extend([placeholder, dict(
                tag='span', attrs={'id': 'react-select-country-placeholder'},
                text='', children=[])])
        else:
            form['children'].append(placeholder)

    if mutation in {'country-outside-fieldset', 'country-subtree-outside-fieldset'}:
        def detach(node, target):
            for child in node['children']:
                if child is target:
                    node['children'].remove(child)
                    return True
                if detach(child, target):
                    return True
            return False

        target = country
        if mutation == 'country-subtree-outside-fieldset':
            # Preserve label, input wrapper and selected display together:
            # the generic C2 graph must not admit this receiverless #country.
            def country_branch(node):
                if node['attrs'].get('class') == 'phone-input__country':
                    return node
                for child in node['children']:
                    found = country_branch(child)
                    if found is not None:
                        return found
                return None

            target = country_branch(tree.root)
            assert target is not None and _find(target, 'country') is country
        assert detach(tree.root, target)
        form['children'].append(target)
    elif mutation in {'valued-unknown', 'second-in-phone-fieldset'}:
        contender = dict(tag='input', attrs={'id': 'unknown-selected', 'role': 'combobox',
                                              'value': 'Synthetic unknown'},
                         text='', children=[])
        if mutation == 'valued-unknown':
            form['children'].append(contender)
        else:
            def enclosing_fieldset(node):
                if node['tag'] == 'fieldset' and node['attrs'].get('class') == 'phone-input':
                    return node
                return next((x for child in node['children']
                             if (x := enclosing_fieldset(child)) is not None), None)
            phone = enclosing_fieldset(tree.root)
            assert phone is not None
            phone['children'].append(contender)
    else:
        assert mutation in {
            '', 'retained-country-placeholder', 'unmounted-placeholder-c0',
            'unknown-country-reference', 'only-missing-country-error',
            'duplicate-country-placeholder', 'foreign-country-placeholder',
        }

    return json.loads(reader_output(
        'closed_widget_dom.cjs',
        {'fixtureTree': tree.root, 'fixtureUrl': fixture.URL},
        instrument(forms.GREENHOUSE_PAGE),
        instrument(forms._GREENHOUSE_FIXED_CONTROL_WITNESS)))


def _control(source, selector):
    matches = [c for c in source['controls'] if c.get('selector') == selector]
    assert len(matches) == 1
    return matches[0]


def _unproven_final_inventory(page):
    # Generic accessibility may observe a picker without proving its form
    # value or every sibling widget. Only the one final Submit guard consumes
    # this complete-inventory requirement; reversible discovery still works.
    assert page['inventory_verified'] is False
    assert page['opaque_controls'] is True
    assert page['opaque_count'] > 0
    assert len(page['opaque_locators']) == page['opaque_count']
    with pytest.raises(forms.Gate, match='inventory is incomplete'):
        forms.check_form(page, {}, uploaded=True)


def test_settled_country_and_location_use_exact_greenhouse_owner_graphs():
    out = selected_snapshot()
    page, witness = out['page'], out['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    _unproven_final_inventory(page)
    for selector in ('#country', '#location'):
        a, b = _control(page, selector), _control(witness, selector)
        assert a['type'] == b['type'] == 'combobox'
        assert a['combobox'] == b['combobox']
        assert a['combobox']['options'] == []
    # The visual div is NOT a browser-owned selected caption. A missing
    # selected-value property must not be reconstructed from React CSS/DOM.
    country = _control(page, '#country')
    assert not country['has_value']
    assert not country['combobox']['selected_text']
    assert not country['combobox']['selected_value']
    assert not country['combobox']['hidden_value']


def test_selected_country_without_placeholder_is_main_failing_regression():
    out = selected_snapshot()
    page, witness = out['page'], out['witness']
    _unproven_final_inventory(page)
    for source in (page, witness):
        country = _control(source, '#country')
        location = _control(source, '#location')
        # A visually selected ID-less node and a retained named machine input
        # are different kinds of evidence. Neither can authorize Country.
        # ID-less visual branches need not expose a name property at all.
        # Absence is NOT an authorization to attribute a sibling receiver.
        assert not country.get('name')
        assert country['combobox']['selected_text'] == ''
        assert country['combobox']['hidden_selector'] == ''
        assert location['type'] == 'combobox'
        assert location['combobox']['hidden_selector'] == ''
        assert location['combobox']['hidden_value'] == ''
        assert location['combobox']['selected_text'] == ''
    # A named hidden field can hold bytes without being linked to the ARIA
    # owner. Neither reader may attribute that unrelated value to the picker.
    stored = _control(page, '#location-code')
    assert stored['type'] == 'hidden' and stored['name'] == 'location_value'
    assert stored['value'] == 'Synthetic Example City'
    # The mandatory final form read rejects the unproven widget even with a
    # synthetic approval, rather than handing a fake C2 certificate to Submit.
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, uploaded=True)


@pytest.mark.parametrize('mutation', [
    'unknown-country-reference',
    'only-missing-country-error',
    'duplicate-country-placeholder',
    'foreign-country-placeholder',
    'unmounted-placeholder-c0',
])
def test_selected_country_placeholder_exception_is_exact_and_c2_only(mutation):
    out = selected_snapshot(mutation)
    page, witness = out['page'], out['witness']
    _unproven_final_inventory(page)
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    country = _control(page, '#country')
    assert country['type'] == 'combobox'
    assert not country['combobox'].get('selected_value')
    assert not country['combobox'].get('hidden_value')
    # Unknown, duplicated, foreign or unmounted ARIA references are not
    # stand-in form receivers just because a sibling visual value is present.
    assert _control(witness, '#country')['combobox'] == country['combobox']


def test_retained_country_placeholder_c2_remains_classifiable():
    out = selected_snapshot('retained-country-placeholder')
    page = out['page']
    _unproven_final_inventory(page)
    country = _control(page, '#country')
    assert country['type'] == 'combobox'
    assert not country['combobox'].get('selected_value')
    assert not country['combobox'].get('selected_text')


def test_intact_country_subtree_outside_phone_fieldset_never_downgrades_to_generic_c2():
    out = selected_snapshot('country-subtree-outside-fieldset')
    page = out['page']
    _unproven_final_inventory(page)
    for source in (page, out['witness']):
        country = _control(source, '#country')
        assert country['type'] == 'combobox'
        assert not country['combobox'].get('selected_value')
        assert not country['combobox'].get('hidden_value')
        assert _control(source, '#location')['type'] == 'combobox'


@pytest.mark.parametrize('mutation,minimum_opaque', [
    ('valued-unknown', 1),
    ('country-outside-fieldset', 1),
    ('country-subtree-outside-fieldset', 1),
    ('second-in-phone-fieldset', 2),
])
def test_selected_country_does_not_hide_unproven_widgets(mutation, minimum_opaque):
    out = selected_snapshot(mutation)
    page = out['page']
    _unproven_final_inventory(page)
    assert page['opaque_count'] >= minimum_opaque
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in out['witness']['controls']]
    if mutation in {'valued-unknown', 'second-in-phone-fieldset'}:
        extra = _control(page, '#unknown-selected')
        assert extra['type'] == 'combobox'
        assert extra['value'] == 'Synthetic unknown'
        assert extra['combobox']['linked'] is False
    # An off-form receiver never gains an inferred hidden value.
    country = _control(page, '#country')
    assert not country['combobox'].get('selected_value')


def test_existing_greenhouse_smoke_leg_observes_selected_country_with_location():
    state = fixture._SyntheticRun('Synthetic-only.pdf')
    state.greenhouse = True
    state.combo_touched.add('Country')
    page = selected_snapshot()['page']
    state.observe_page(page)
    assert state.last_page is page
    assert state.combo_observations['Country']['outcome'] == 'not_committed'
    # No obsolete Greenhouse C2/receiverless-county certificate may be
    # inferred from the selected div. This is an unverified final form.
    assert state.country_choice_inventory is None
    _unproven_final_inventory(state.last_page)
    mutated = selected_snapshot('valued-unknown')['page']
    state.observe_page(mutated)
    assert state.combo_observations['Country']['outcome'] == 'not_committed'
    assert state.last_page['opaque_controls'] is True
    assert any(c.get('selector') == '#unknown-selected'
               for c in state.last_page['controls'])
    assert state.submit_ipc == state.post_attempts == 0
