"""Synthetic settled Greenhouse country/location inventory, without a browser.

The second combobox case is intentionally adversarial: it proves the fieldset
does not grant authority to another widget, not that this was the live second node.
"""
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

    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps({'fixtureTree': tree.root, 'fixtureUrl': fixture.URL}),
        instrument(forms.GREENHOUSE_PAGE), instrument(forms._GREENHOUSE_FIXED_CONTROL_WITNESS),
    ], text=True))


def test_settled_country_and_location_use_exact_greenhouse_owner_graphs():
    out = selected_snapshot()
    page, witness = out['page'], out['witness']
    assert page['inventory_verified'] is True and page['opaque_controls'] is False
    assert page['opaque_count'] == 0 and page['opaque_locators'] == []
    assert [c['selector'] for c in page['controls']] == [c['selector'] for c in witness['controls']]
    for selector, text in (('#location', 'Synthetic Example City'),
                           ('#country', 'Synthetic Country')):
        a = next(c for c in page['controls'] if c['selector'] == selector)
        b = next(c for c in witness['controls'] if c['selector'] == selector)
        assert a['combobox'] == b['combobox']
        assert a['combobox']['mode'] == 'closed-react-select'
        assert a['combobox']['phase'] == 'C2'
        assert a['combobox']['selected_text'] == a['combobox']['displayed_text'] == text
    country = next(c for c in page['controls'] if c['selector'] == '#country')
    assert country['combobox']['receiverless_country'] is True
    assert len(country['combobox']['country_graph']) == 13
    assert not {'hidden_selector', 'hidden_value', 'selected_value'} & country['combobox'].keys()



def test_selected_country_without_placeholder_is_main_failing_regression():
    # main rejects this exact C2 shape in countryGraph: its aria-describedby
    # still mentions react-select-country-placeholder, but the selected-value
    # DOM has replaced that node. The location picker remains a valid C2.
    out = selected_snapshot()
    page, witness = out['page'], out['witness']
    assert page['inventory_verified'] is True
    assert page['opaque_controls'] is False and page['opaque_count'] == 0
    assert page['opaque_locators'] == []
    forms._inventory_ok(page)
    for source in (page, witness):
        country = next(c for c in source['controls'] if c['selector'] == '#country')
        location = next(c for c in source['controls'] if c['selector'] == '#location')
        assert country['combobox']['mode'] == 'closed-react-select'
        assert country['combobox']['phase'] == 'C2'
        assert country['combobox']['receiverless_country'] is True
        assert country['combobox']['selected_text'] == fixture._FACTS['Country']
        assert not {'selected_value', 'hidden_value', 'hidden_selector'} & country['combobox'].keys()
        assert location['combobox']['mode'] == 'closed-react-select'
        assert location['combobox']['phase'] == 'C2'
    for graph in out['graphs']:
        assert '#country' not in graph['quarantined']
        assert next(g for selector, g in graph['graphs']
                    if selector == '#country')['phase'] == 'C2'
        assert next(g for selector, g in graph['graphs']
                    if selector == '#location')['phase'] == 'C2'


@pytest.mark.parametrize('mutation', [
    'unknown-country-reference',
    'only-missing-country-error',
    'duplicate-country-placeholder',
    'foreign-country-placeholder',
    'unmounted-placeholder-c0',
])
def test_selected_country_placeholder_exception_is_exact_and_c2_only(mutation):
    out = selected_snapshot(mutation)
    page = out['page']
    assert page['inventory_verified'] is False
    assert page['opaque_controls'] is True and page['opaque_count'] == 1
    assert len(page['opaque_locators']) == 1
    assert forms._safe_diagnostic(page['opaque_locators'][0]) == page['opaque_locators'][0]
    country = next(c for c in page['controls'] if c['selector'] == '#country')
    assert country['combobox'].get('mode') != 'closed-react-select'
    assert country['combobox']['quarantined'] is True
    location = next(c for c in page['controls'] if c['selector'] == '#location')
    assert location['combobox']['phase'] == 'C2'


def test_retained_country_placeholder_c2_remains_classifiable():
    out = selected_snapshot('retained-country-placeholder')
    assert out['page']['inventory_verified'] is True
    assert out['page']['opaque_count'] == 0


def test_intact_country_subtree_outside_phone_fieldset_never_downgrades_to_generic_c2():
    # Moving only the input breaks W/V ownership and misses the old fallback.
    # Move the entire otherwise-valid selected country widget and its label.
    out = selected_snapshot('country-subtree-outside-fieldset')
    page = out['page']
    assert page['inventory_verified'] is False
    assert page['opaque_controls'] is True and page['opaque_count'] == 1
    assert len(page['opaque_locators']) == 1
    assert forms._safe_diagnostic(page['opaque_locators'][0]) == page['opaque_locators'][0]
    for reader in (out['page'], out['witness']):
        country = next(c for c in reader['controls'] if c['selector'] == '#country')
        location = next(c for c in reader['controls'] if c['selector'] == '#location')
        assert country['combobox'].get('mode') != 'closed-react-select'
        assert country['combobox']['quarantined'] is True
        assert location['combobox']['phase'] == 'C2'
    for graph in out['graphs']:
        assert '#country' in graph['quarantined']
        assert next(g for selector, g in graph['graphs'] if selector == '#country') is None
    with pytest.raises(forms.Gate, match='opaque_count=1') as caught:
        forms._inventory_ok(page)
    assert 'control=unavailable' not in str(caught.value)


@pytest.mark.parametrize('mutation,opaque', [
    ('valued-unknown', 1),
    ('country-outside-fieldset', 1),
    ('country-subtree-outside-fieldset', 1),
    ('second-in-phone-fieldset', 2),
])
def test_selected_country_does_not_hide_unproven_widgets(mutation, opaque):
    out = selected_snapshot(mutation)
    page = out['page']
    assert page['opaque_controls'] is True
    assert page['inventory_verified'] is False
    assert page['opaque_count'] == opaque
    assert len(page['opaque_locators']) == opaque
    # A deep present country node must be diagnosable rather than 'unavailable'.
    assert all(forms._safe_diagnostic(path) == path
               for path in page['opaque_locators'])
    if mutation == 'second-in-phone-fieldset':
        country = next(c for c in page['controls'] if c['selector'] == '#country')
        assert country['combobox'].get('mode') != 'closed-react-select'
        assert country['combobox']['quarantined'] is True
        # This is the deliberate two-opaque synthetic case: the country graph
        # rejects the extra fieldset branch, and the unknown combobox has no link.
        assert any(c['selector'] == '#unknown-selected' for c in page['controls'])

def test_existing_greenhouse_smoke_leg_observes_selected_country_with_location():
    # The extra inventory assertion belongs to the existing selected-state
    # observation, not to a tenth fixture session or a real browser.
    state = fixture._SyntheticRun('Synthetic-only.pdf')
    state.greenhouse = True
    state.combo_touched.add('Country')
    state.observe_page(selected_snapshot()['page'])
    capture = state.country_choice_inventory
    assert capture is not None and capture['verified'] is True
    assert capture['opaque_count'] == 0 and capture['opaque_locators'] == []
    assert capture['country_phase'] == capture['location_phase'] == 'C2'
    assert capture['country_selected'] == fixture._FACTS['Country']
    assert capture['location_selected'] == fixture._FACTS['Where are you located?']
    # An unrelated opaque control must not be hidden by the country sub-case.
    state = fixture._SyntheticRun('Synthetic-only.pdf')
    state.greenhouse = True
    state.combo_touched.add('Country')
    state.observe_page(selected_snapshot('valued-unknown')['page'])
    assert state.country_choice_inventory is not None
    assert state.country_choice_inventory['verified'] is False
    assert state.country_choice_inventory['opaque_count'] == 1
