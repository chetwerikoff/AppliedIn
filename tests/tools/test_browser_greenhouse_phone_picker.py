"""Issue 256: the live Greenhouse embed .iti phone picker is one phone receiver.

The shape below is the intl-tel-input picker read on public Waymo, Stripe and
Databricks embeds (selector shape and widget type only, synthetic options).
Its container is display:none on all three; the fake DOM spells "not
rendered" with the hidden attribute. The visible #country combobox in the
same phone fieldset stays the country receiver, answered per field from the
bank. Before Issue 256 the 247 unrendered picker nodes made every embed
inventory opaque, so no embed row could ever reach the final guard.
"""
import json
import re

import pytest

from tests.tools.reader_node_harness import reader_output
from tests.tools.test_browser_greenhouse_fixture import Tree
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture


def _options(count=244):
    return ''.join(
        f'<li id="iti-0__item-s{i}" class="iti__country{" iti__highlight" if not i else " "}"'
        f' tabindex="-1" role="option" data-dial-code="{i}" data-country-code="s{i}"'
        f' aria-selected="false"><div class="iti__flag iti__s{i}"></div>'
        f'<span class="iti__country-name">Synthetic {i}</span>'
        f'<span class="iti__dial-code" dir="ltr">+{i}</span></li>'
        for i in range(count))


def live_picker(*, container='hidden', button='', search='', clear='', dialog='role="dialog"',
                option_extra='', list_attrs='', extra_child='', box_extra=''):
    return (
        '<div class="iti iti--allow-dropdown iti--show-flags iti--inline-dropdown">'
        f'<div class="iti__country-container" style="left: 0px;" {container}>'
        '<button type="button" class="iti__selected-country" aria-expanded="false"'
        ' aria-label="Select country" aria-haspopup="dialog"'
        f' aria-controls="iti-0__dropdown-content" title="Select country" {button}>'
        '<div class="iti__selected-country-primary"><div class="iti__flag iti__globe"></div>'
        '<div class="iti__arrow" aria-hidden="true"></div></div></button>'
        f'<div id="iti-0__dropdown-content" class="iti__dropdown-content iti__hide " {dialog}'
        ' aria-modal="true">'
        '<div class="iti__search-input-wrapper"><span class="iti__search-icon" aria-hidden="true">'
        '<svg class="iti__search-icon-svg" aria-hidden="true"><circle></circle><line></line></svg></span>'
        '<input id="iti-0__search-input" type="search" class="iti__search-input" placeholder="Search"'
        ' role="combobox" aria-expanded="true" aria-label="Search"'
        f' aria-controls="iti-0__country-listbox" aria-autocomplete="list" autocomplete="off" {search}>'
        '<button type="button" class="iti__search-clear iti__hide" aria-label="Clear search"'
        f' tabindex="-1" {clear}><svg class="iti__search-clear-svg" aria-hidden="true">'
        '<mask id="iti-0-clear-mask"><rect></rect><path></path></mask><circle></circle></svg></button></div>'
        '<span class="iti__a11y-text">244 results found</span>'
        '<div class="iti__no-results iti__hide" aria-hidden="true">No results found</div>'
        '<ul class="iti__country-list" id="iti-0__country-listbox" role="listbox"'
        f' aria-label="List of countries" {list_attrs}>' + option_extra + _options() + '</ul>'
        f'</div>{box_extra}</div>'
        '<input id="phone" class="input input__single-line iti__tel-input" aria-label="Phone"'
        ' aria-describedby="phone-description phone-error phone-help" aria-invalid="false"'
        ' aria-errormessage="phone-error" aria-required="true" type="tel" maxlength="20"'
        ' dir="ltr" autocomplete="off" data-intl-tel-input-id="0">'
        f'{extra_child}</div>')


def snapshot(picker=None, *, reader=None, outside=False):
    markup = fixture._greenhouse_form().decode()
    markup = re.sub(r'</form>.*?<script>', '</form><script>', markup, flags=re.S)
    markup = markup.replace('<template id="greenhouse-template">', '').replace('</template>', '')
    # Replace the fixture's older picker markup with the live embed shape.
    start = markup.index('<div id="telephone" class="iti')
    # The first of these closes the old .iti wrapper itself.
    end = markup.index('</div></div></div><span id="phone-description">') + len('</div>')
    picker = live_picker() if picker is None else picker
    if outside:
        markup = markup[:start] + markup[end:]
        assert markup.count('<form id="application-form"') == 1
        markup = markup.replace('<form id="application-form"', '<div id="other-host">'
                                + picker + '</div><form id="application-form"', 1)
    else:
        markup = markup[:start] + picker + markup[end:]
    tree = Tree()
    tree.feed(markup)
    assert not tree.stack
    page_reader, witness_reader = reader or (forms.GREENHOUSE_PAGE,
                                             forms._GREENHOUSE_FIXED_CONTROL_WITNESS)
    return json.loads(reader_output(
        'closed_widget_dom.cjs', dict(fixtureTree=tree.root, fixtureUrl=fixture.URL),
        page_reader, witness_reader))


def _admitted(out):
    page, witness = out['page'], out['witness']
    assert page['greenhouse']['phone_pickers'] == 1
    assert page['opaque_controls'] is False and page['opaque_count'] == 0
    assert page['inventory_verified'] is True
    assert forms._greenhouse_not_ready(page) == ''
    # Exactly one phone receiver; no picker part is an inventory control.
    assert [c['selector'] for c in page['controls'] if c['type'] == 'tel'] == ['#phone']
    assert not any(c['selector'].startswith('#iti-') or c.get('label') in {
        'Select country', 'Search', 'Clear search'} for c in page['controls'])
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    return page


def _refused(out):
    page = out['page']
    assert page['greenhouse']['phone_pickers'] == 0
    assert page['opaque_controls'] is True and page['opaque_count'] > 0
    assert page['inventory_verified'] is False
    assert forms._greenhouse_not_ready(page) == 'opaque_controls application inventory'
    with pytest.raises(forms.Gate, match='inventory is incomplete'):
        forms.check_form(page, {}, uploaded=True)
    return page


def test_live_embed_phone_picker_is_one_phone_receiver():
    page = _admitted(snapshot())
    # The visible Country picker stays its own field with no picker options:
    # the hidden .iti list never becomes a country choice.
    country = next(c for c in page['controls'] if c['selector'] == '#country')
    assert country['type'] == 'combobox' and country['required'] is True
    assert country['combobox']['options'] == []
    assert not country['combobox']['selected_value']


def test_admitted_picker_still_binds_country_and_phone_per_field():
    page = _admitted(snapshot())
    # Nothing is chosen for the owner: an approved Country answer without a
    # proven form value, and an unapproved phone, both still stop Submit.
    alone = dict(page, controls=[c for c in page['controls'] if c['selector'] == '#country'])
    with pytest.raises(forms.Gate, match='Unverified or changed combobox value: Country'):
        forms.check_form(alone, {'#country': 'Synthetic Country'}, uploaded=True)
    phone = dict(next(c for c in page['controls'] if c['selector'] == '#phone'))
    assert phone['required'] is True and phone['value'] == ''
    with pytest.raises(forms.Gate, match='Required field is empty: Phone'):
        forms.check_form(dict(page, controls=[phone]), {}, uploaded=True)
    changed = dict(phone, value='+1 555 0100', has_value=True)
    with pytest.raises(forms.Gate, match='phone value is empty or changed'):
        forms.check_form(dict(page, controls=[changed]), {'#phone': '+1 555 0199'},
                         uploaded=True)


def test_generic_reader_keeps_the_same_picker_opaque():
    # The admission is Greenhouse-reader only; the generic graph stays
    # library-agnostic, so the same nodes remain opaque outside Greenhouse.
    _admitted(snapshot())
    generic = snapshot(reader=(bsk.PAGE, forms._FIXED_CONTROL_WITNESS))['page']
    assert generic['opaque_controls'] is True and generic['inventory_verified'] is False
    assert generic['opaque_count'] == 247


@pytest.mark.parametrize('variant', [
    # A rendered picker could change the dial code: not admitted.
    dict(container=''),
    # A second element claiming the listbox is a foreign link.
    dict(box_extra='<div aria-controls="iti-0__country-listbox"></div>'),
    # Any extra control inside the picker.
    dict(box_extra='<input id="iti-extra" type="text">'),
    dict(option_extra='<li class="iti__country" role="option"><a href="/x">x</a></li>'),
    dict(option_extra='<li class="iti__country" role="option"><button type="submit">x</button></li>'),
    dict(option_extra='<div class="iti__country" role="option">x</div>'),
    dict(list_attrs='aria-owns="iti-other"'),
    dict(search='name="country_search"'),
    dict(search='type="text"'),
    dict(button='formaction="https://example-co.test/elsewhere"'),
    dict(clear='type="submit"'),
    dict(dialog='role="listbox"'),
    dict(box_extra='<synthetic-widget></synthetic-widget>'),
    # A third child of the picker wrapper.
    dict(extra_child='<input id="second-phone" type="tel">'),
], ids=['rendered', 'foreign-link', 'extra-input', 'link-option', 'submit-option',
        'non-li-option', 'listbox-aria-owns', 'named-search', 'text-search',
        'formaction-button', 'submit-clear', 'wrong-dialog-role', 'custom-element',
        'extra-wrapper-child'])
def test_opaque_or_ambiguous_picker_variants_still_refuse(variant):
    # The unmutated live shape is admitted; each single mutation refuses.
    _admitted(snapshot())
    picker = live_picker(**variant)
    if 'type="text"' in variant.get('search', ''):
        picker = picker.replace('type="search" class="iti__search-input"',
                                'class="iti__search-input"')
    if 'type="submit"' in variant.get('clear', ''):
        picker = picker.replace('<button type="button" class="iti__search-clear',
                                '<button class="iti__search-clear')
    _refused(snapshot(picker))


def test_duplicate_listbox_id_refuses():
    _admitted(snapshot())
    picker = live_picker().replace(
        '<span class="iti__a11y-text">',
        '<span id="iti-0__country-listbox"></span><span class="iti__a11y-text">')
    _refused(snapshot(picker))


def test_picker_outside_the_bound_form_is_not_admitted():
    # Only a picker inside the bound Greenhouse form can be its phone field.
    _admitted(snapshot())
    page = snapshot(outside=True)['page']
    assert page['greenhouse']['root']['selector'] == '#application-form'
    assert page['greenhouse']['phone_pickers'] == 0
    phone = next(c for c in page['controls'] if c['selector'] == '#phone')
    assert phone['in_form'] is False
