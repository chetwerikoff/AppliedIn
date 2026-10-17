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
    # Hydration mounts the template before selection; the original script changes
    # display branches and drops both validation-only proxies on commitment.
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
    markup = markup.replace(country_placeholder,
                            '<div id="react-select-country-placeholder" class="select__single-value">'
                            'Synthetic Country</div>', 1)
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
    if mutation == 'country-outside-fieldset':
        def detach(node, target):
            for child in node['children']:
                if child is target:
                    node['children'].remove(child)
                    return True
                if detach(child, target):
                    return True
            return False
        assert detach(tree.root, country)
        form['children'].append(country)
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
        assert not mutation

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


@pytest.mark.parametrize('mutation,opaque', [
    ('valued-unknown', 1),
    ('country-outside-fieldset', 1),
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
    assert all(path.startswith('html:nth-of-type(1) > ') and path != 'unavailable'
               for path in page['opaque_locators'])
    if mutation == 'second-in-phone-fieldset':
        country = next(c for c in page['controls'] if c['selector'] == '#country')
        assert country['combobox'].get('mode') != 'closed-react-select'
        assert country['combobox']['quarantined'] is True
        # This is the deliberate two-opaque synthetic case: the country graph
        # rejects the extra fieldset branch, and the unknown combobox has no link.
        assert any(c['selector'] == '#unknown-selected' for c in page['controls'])
