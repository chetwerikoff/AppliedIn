"""Generic accessible phone controls need real form-owned Submit values."""
import copy

import pytest

from tools import browser_skill_apply as forms
import runpy
from pathlib import Path

_helpers = runpy.run_path(str(Path(__file__).with_name('test_browser_closed_widgets.py')))
Session, dom, combo, KEYS, URL = (_helpers[k] for k in ('Session', 'dom', 'combo', 'KEYS', 'URL'))

def approved_values(values=None, facts=None):
    return forms._ApprovedFormValues(
        values or {}, 'example-co#1', facts or {})


class PhoneSession(Session):
    def __init__(self, **scenario):
        super().__init__(fullPhone=True, **scenario)

    async def call(self, *args):
        if args[0] == 'evaluate':
            return await super().call(*args)
        if args == ('click', '#country'):
            self.calls.append(args)
            self.phases['country'] = 'C1'
        elif args == ('click', '#country-option'):
            self.calls.append(args)
            self.phases['country'] = 'C2'
        else:
            return await super().call(*args)
        scenario = {**self.scenario, 'phases': {
            **self.scenario.get('phases', {}), **self.phases}}
        self.snapshot = dom(**scenario)
        return {}


@pytest.fixture
def phone_grants(monkeypatch):
    snapshot = dom(fullPhone=True)
    facts = {**{key: 'Approved '+key for key in KEYS}, 'Country': 'Approved country'}
    grants = {}
    for key, answer in facts.items():
        target = combo(snapshot, 'country' if key == 'Country' else key)
        grants[key] = {k: target[k] for k in (
            'selector', 'label', 'question', 'tag', 'type', 'form_selector', 'form_action')}
        grants[key].update(value=answer, url=URL)
    # The focused-country projection has no sibling Phone receiver to
    # disambiguate the shared reader question, so retain its observed key.
    grants['Phone'] = dict(grants['Country'])
    row = {'pk': 'example-co#1', 'status': 'submitting', 'human_approved_answers': grants}
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    return facts, row


async def choose_country(session, facts, filled=None):
    return await forms._execute_combobox(
        session, await session.page(),
        {'action': 'combobox', 'selector': '#country', 'fact': 'Country'},
        facts=facts, filled=filled if filled is not None else approved_values(facts=facts), pk='example-co#1',
        job_url=URL, ctx=None, provenance=None)


def focused_country(page, receipt, *, form_owned=False):
    """Isolate current country while preserving its actual receiver semantics."""
    current = copy.deepcopy(page)
    field = combo({'page': current}, 'country')
    current['controls'] = [field]
    current.update(inventory_verified=True, truncated=False, opaque_controls=False,
                   shadow_roots=False, unsupported_frames=[])
    if form_owned:
        # Explicit synthetic final positive; the real HTML lacks this receiver.
        value, caption = receipt['committed_value'], receipt['committed_caption']
        field.update(name='country', value=value, has_value=True)
        field['combobox'].update(selected_text=caption, displayed_text=caption,
                                 selected_value=value, hidden_selector='',
                                 hidden_value='')
    return current


def completed_approved_form(page, facts, country_receipt):
    """Fixture for the generic current-value and approval-history guard."""
    final = copy.deepcopy(page)
    keep = {'#'+key for key in (*KEYS, 'country')} | {
        '#phone', '#resume', '#submit'}
    final['controls'] = [c for c in final['controls'] if c['selector'] in keep]
    final.update(inventory_verified=True, truncated=False, opaque_controls=False,
                 shadow_roots=False, unsupported_frames=[])
    filled, receipts = approved_values(facts=facts), []
    for key in (*KEYS, 'country'):
        field = combo({'page': final}, key)
        bank_key = 'Country' if key == 'country' else key
        answer = facts[bank_key]
        caption = country_receipt['committed_caption'] if key == 'country' else answer
        value = country_receipt['committed_value'] if key == 'country' else answer
        field.update(name=key, value=value, has_value=True)
        field['combobox'].update(selected_text=caption, displayed_text=caption,
                                 selected_value=value, hidden_selector='', hidden_value='')
        filled[field['selector']] = answer
        receipts.append(country_receipt if key == 'country' else {
            'action': 'combobox', 'label': field['label'],
            'selector': field['selector'], 'question': field['question'],
            'url': final['url'], 'fact': bank_key, 'approved_value': answer,
            'committed_caption': caption, 'committed_value': value,
            'hidden_selector': '', 'hidden_value': '',
        })
    return final, approved_values(filled, facts), receipts

@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
def test_exact_phone_inventory_uses_both_production_readers(phase):
    snapshot = dom(fullPhone=True, phases={'country': phase})
    page, witness = snapshot['page'], snapshot['witness']
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    assert page['truncated'] is False
    assert set(witness) == {'url', 'controls', 'application_identity'}
    assert witness['application_identity'] == page['application_identity']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    assert {'#country', '#phone', '#resume', '#submit'} <= {
        c['selector'] for c in page['controls']}
    # No special-purpose C0/C1/C2 or phone-picker inventory exclusion.
    owner = combo(snapshot, 'country')
    independent = next(c for c in witness['controls'] if c['selector'] == '#country')
    assert owner['combobox'] == independent['combobox']
    for graph in (owner['combobox'], independent['combobox']):
        # The C2 branch has one local caption, but no submitted machine value.
        assert graph['selected_text'] == ('Approved country' if phase == 'C2' else '')
        assert graph['selected_value'] == ''
        assert graph['hidden_selector'] == graph['hidden_value'] == ''
    assert owner['type'] == 'combobox' and not owner['name']
    assert not owner['has_value']

@pytest.mark.parametrize('scenario', [
    {'flatPicker': True}, {'visiblePicker': True}, {'multipleTel': True},
    {'pickerAttr': ['country-button', 'aria-controls', None]},
    {'pickerAttr': ['country-search', 'aria-controls', 'missing']},
    {'pickerAttr': ['dropdown', 'role', 'listbox']},
    {'pickerAttr': ['dropdown', 'aria-modal', None]},
    {'pickerAttr': ['country-clear', 'type', 'submit']},
    {'pickerDuplicate': True}, {'pickerForeign': True}, {'pickerExtra': True},
    {'pickerOptionDanger': True}, {'extraPicker': True}, {'nestedPicker': True},
    {'countryWrongFor': True}, {'countryHidden': True}, {'countryExtra': True},
    {'countryWrongChain': True}, {'countryAttr': ['aria-labelledby', 'missing']},
    {'countryAttr': ['aria-describedby', 'missing']},
    {'countryAttr': ['aria-activedescendant', None]},
    {'countryAttr': ['tabindex', '-1']},
    {'pickerShared': True}, {'pickerDuplicateOption': True}, {'pickerUnknown': True},
    {'pickerMove': ['dropdown', 'telephone']}, {'pickerMove': ['country-search', 'dropdown']},
    {'pickerAttr': ['country-button', 'aria-expanded', 'true']},
    {'pickerAttr': ['country-search', 'type', 'text']},
    {'pickerAttr': ['picker-option-0', 'role', 'checkbox']},
    {'pickerAttr': ['country-clear', 'formaction', '/away']},
    {'outsideOption': True}, {'countryDuplicate': True}, {'countryTyped': True},
    {'countryNoToken': True}, {'countryAttr': ['aria-controls', '']},
])
async def test_unproven_phone_shape_remains_opaque(scenario, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = dom(fullPhone=True, **scenario)
    page, witness = snapshot['page'], snapshot['witness']
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    # Duplicate IDs may erase the literal '#country' selector entirely.
    # Reconcile any still-observed ARIA owner by its fresh reader identity.
    candidates = [c for c in page['controls'] if c['type'] == 'combobox'
                  and (c['selector'] == '#country' or c['label'] == 'Country')]
    assert candidates
    for country in candidates:
        independent = next(c for c in witness['controls']
                           if c['selector'] == country['selector'])
        assert country['combobox'] == independent['combobox']
        assert country['combobox'].get('selected_text', '') == ''
        assert country['combobox'].get('selected_value', '') == ''
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)

@pytest.mark.parametrize('bad', ['empty_country', 'empty_phone', 'extra_file'])
def test_complete_phone_form_uses_current_answers_and_single_pdf(bad):
    """No group grant or historical receipt substitutes for live form values."""
    file_field = {'selector': '#resume', 'type': 'file', 'tag': 'input',
                  'label': 'Resume', 'files': ['Test User Resume.pdf']}
    phone = {'selector': '#phone', 'type': 'tel', 'tag': 'input',
             'label': 'Phone', 'required': True, 'value': '+15550101010'}
    country = {'selector': '#country', 'type': 'combobox', 'tag': 'input',
               'label': 'Country', 'required': True, 'value': 'EX',
               'combobox': {'selected_text': 'Synthetic Country'}}
    page = {'url': URL, 'inventory_verified': True, 'truncated': False,
            'opaque_controls': False, 'unsupported_frames': [],
            'shadow_roots': False, 'controls': [phone, country, file_field]}
    forms.check_form(page, {}, True, 'Test User Resume.pdf')
    if bad == 'empty_country':
        country['value'] = ''
        country['combobox']['selected_text'] = ''
    elif bad == 'empty_phone':
        phone['value'] = ''
    else:
        page['controls'].append({'selector': '#other', 'type': 'file',
                                 'label': 'Additional attachment',
                                 'files': ['Unrelated.pdf']})
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, True, 'Test User Resume.pdf')


async def test_actual_raw_submit_refuses_receiverless_country_without_ipc(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    await assert_unapproved_submit_is_refused(
        dom(fullPhone=True, phases={'country': 'C2'}),
        monkeypatch, tmp_path, require_incomplete=False)

def test_country_open_popup_stays_in_the_native_form():
    snapshot = dom(fullPhone=True, phases={'country': 'C1'}, countryPortal=True)
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False


def served_fixture_dom(markup=None):
    # Parsed bytes prove structure only; primary smoke owns browser hydration.
    from html.parser import HTMLParser
    from tools import browser_skill_fixture as fixture
    class Tree(HTMLParser):
        def __init__(self):
            super().__init__()
            self.root = None
            self.stack = []
        def handle_starttag(self, tag, attrs):
            node = {'tag': tag, 'attrs': {k: v if v is not None else True for k, v in attrs},
                    'text': '', 'children': []}
            if self.stack:
                self.stack[-1]['children'].append(node)
            else:
                self.root = node
            if tag not in {'input', 'meta', 'br', 'link', 'hr', 'img'}:
                self.stack.append(node)
        def handle_endtag(self, tag):
            assert self.stack[-1]['tag'] == tag, (tag, self.stack[-1]['tag'])
            self.stack.pop()
        def handle_data(self, text):
            if self.stack:
                self.stack[-1]['text'] += text
    tree = Tree()
    tree.feed((markup if markup is not None else fixture._closed_form()).decode())
    return dom(fixtureTree=tree.root, fixtureUrl=fixture.URL)


async def test_served_fixture_html_itself_passes_production_readers_offline(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = served_fixture_dom()
    page, witness = snapshot['page'], snapshot['witness']
    assert page['inventory_verified'] is False and page['opaque_controls'] is True
    assert page['truncated'] is False
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    assert {'#resume', '#submit', '#country', '#phone'} <= {
        c['selector'] for c in page['controls']}
    country = combo(snapshot, 'country')
    assert country['combobox'] == next(
        c['combobox'] for c in witness['controls'] if c['selector'] == '#country')
    assert country['combobox']['selected_value'] == ''
    assert not country['name'] and not country['has_value']
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)

@pytest.mark.parametrize('scenario', ['secondIti', 'movedIti', 'renamedPhone',
                                      'secondTelOutside', 'brokenPhoneChain'])
async def test_picker_exemption_requires_one_exact_phone_branch(
        scenario, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = dom(fullPhone=True, **{scenario: True})
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    assert [c['selector'] for c in snapshot['page']['controls']] == [
        c['selector'] for c in snapshot['witness']['controls']]
    assert {'#country', '#resume', '#submit'} <= {
        c['selector'] for c in snapshot['page']['controls']}
    # No special-case phone-shape exemption: generic ARIA ownership and
    # the raw final Submit guard decide, never an ITI ancestor signature.
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)

@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
async def test_hidden_optional_aria_parts_cannot_mask_form_receivers(
        phase, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = dom(fullPhone=True, phases={'country': phase})
    page, fixed = snapshot['page'], snapshot['witness']
    assert page['inventory_verified'] is False and page['opaque_controls'] is True
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in fixed['controls']]
    assert {'#phone', '#country', '#resume', '#submit'} <= {
        c['selector'] for c in page['controls']}
    # A non-required telephone value needs no historical per-control grant.
    # The actual form cannot submit with an opaque inventory; the isolated
    # final check below provides the staged résumé so it can examine just this
    # optional field without failing on an unrelated missing PDF.
    phone = next(c for c in page['controls'] if c['selector'] == '#phone')
    phone.update(value='Synthetic optional autofill', has_value=True, required=False)
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)
    filename = 'Synthetic Resume.pdf'
    complete = {'url': page['url'], 'controls': [copy.deepcopy(phone), {
                    'selector': '#resume', 'label': 'Resume', 'type': 'file',
                    'disabled': False, 'files': [filename],
                }],
                'inventory_verified': True, 'truncated': False,
                'opaque_controls': False, 'shadow_roots': False,
                'unsupported_frames': []}
    forms.check_form(complete, {}, True, attachment=filename)

@pytest.mark.parametrize('attribute,value', [
    ('aria-required', 'true'),
    ('name', 'hidden_receiver'),
    ('type', 'submit'),
])
async def test_hidden_submittable_aria_controls_remain_visible_to_final_guard(
        attribute, value, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    # The generic inactive-role exception MUST NOT consume required, named
    # or committing controls, even while their picker is collapsed. A hidden
    # page is never evidence that these submitted receivers are safe.
    snapshot = dom(fullPhone=True, pickerAttr=[
        'country-search', attribute, value,
    ])
    page, fixed = snapshot['page'], snapshot['witness']
    assert '#country-search' in {c['selector'] for c in page['controls']}
    assert '#country-search' in {c['selector'] for c in fixed['controls']}
    assert page['opaque_controls'] is True
    assert page['inventory_verified'] is False
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)

