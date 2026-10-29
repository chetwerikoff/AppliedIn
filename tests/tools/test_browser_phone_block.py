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
        assert graph['selected_text'] == graph['selected_value'] == ''
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

async def test_receiverless_commit_has_no_fabricated_transport(phone_grants):
    facts, _ = phone_grants
    session, filled = PhoneSession(presentation=True, pickerDecoration=True), {}
    receipt = await choose_country(session, facts, filled)
    assert receipt['committed_caption'] == receipt['committed_value'] == facts['Country']
    assert receipt['hidden_selector'] == receipt['hidden_value'] == ''
    assert session.calls == [
        ('click', '#country'), ('fill', '#country', '--value', facts['Country']),
        ('click', '#country-option')]
    # A reversible option click is not independent submitted-value evidence.
    current = focused_country(await session.page(), receipt)
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(current, filled, True, receipts=[receipt])
    with pytest.raises(forms.Gate, match='committed accessible choice'):
        forms.check_approvals('example-co#1', [receipt], current)

@pytest.mark.parametrize('scenario', [
    {'countryCaption': 'approved country'}, {'countryCaption': 'Approved  country'},
    {'countryCodes': {'value': 'CODE'}}, {'countryCodes': {'data-value': ''}},
    {'countryCodes': {'value': 'Approved country', 'data-value': 'CODE'}},
    {'countryDanger': True},
])
async def test_country_option_literal_or_safety_failure_precedes_click(phone_grants, scenario):
    facts, _ = phone_grants
    session, filled = PhoneSession(**scenario), {}
    if scenario.get('countryDanger'):
        with pytest.raises(forms.Gate, match='submission-capable'):
            await choose_country(session, facts, filled)
        assert not any(c == ('click', '#country-option') for c in session.calls)
        return
    receipt = await choose_country(session, facts, filled)
    assert forms._same(receipt['committed_caption'], facts['Country'])
    assert receipt['committed_value'] == (
        scenario.get('countryCodes', {}).get('data-value')
        or scenario.get('countryCodes', {}).get('value')
        or receipt['committed_caption'])
    assert session.calls == [
        ('click', '#country'), ('fill', '#country', '--value', facts['Country']),
        ('click', '#country-option')]
    # Benign captions and encoded codes are selectable preparation; absent
    # named form-owned value still refuses the single final Submit.
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(focused_country(await session.page(), receipt),
                         filled, True, receipts=[receipt])

@pytest.mark.parametrize('phase', ['C1', 'C2'])
async def test_country_candidate_cannot_supply_initial_admission(phone_grants, phase):
    session, filled = PhoneSession(phases={'country': phase}), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    assert forms._same(receipt['committed_caption'], phone_grants[0]['Country'])
    assert not any(c == ('click', '#submit') for c in session.calls)
    # An initial ARIA phase confers neither per-step authority nor Submit.
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(focused_country(await session.page(), receipt),
                         filled, True, receipts=[receipt])

@pytest.mark.parametrize('key', ['selected_text', 'displayed_text'])
async def test_each_country_token_projection_is_literal(phone_grants, key):
    session, filled = PhoneSession(), approved_values(facts=phone_grants[0])
    receipt = await choose_country(session, phone_grants[0], filled)
    final = focused_country(await session.page(), receipt, form_owned=True)
    forms.check_form(final, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], final)
    combo({'page': final}, 'country')['combobox'][key] = 'Different country'
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(final, filled, True, receipts=[receipt])

@pytest.mark.parametrize('tamper', [
    'receipt_copy', 'receipt_caption', 'receipt_code', 'current_caption',
    'current_code', 'form_action', 'grant',
])
async def test_country_history_cannot_replace_original_proof(phone_grants, tamper):
    facts, row = phone_grants
    session, filled = PhoneSession(), approved_values(facts=facts)
    receipt = await choose_country(session, facts, filled)
    current = focused_country(await session.page(), receipt, form_owned=True)
    if tamper == 'receipt_copy':
        forms.check_form(current, filled, True, receipts=[dict(receipt)])
        forms.check_approvals('example-co#1', [dict(receipt)], current)
        return
    if tamper == 'receipt_caption':
        receipt['committed_caption'] = 'Different country'
    elif tamper == 'receipt_code':
        receipt['committed_value'] = 'DIFFERENT'
    elif tamper == 'current_caption':
        combo({'page': current}, 'country')['combobox']['selected_text'] = 'Different country'
    elif tamper == 'current_code':
        combo({'page': current}, 'country')['combobox']['selected_value'] = 'DIFFERENT'
    elif tamper == 'form_action':
        combo({'page': current}, 'country')['form_action'] = 'https://other.example.test/'
    else:
        row['human_approved_answers']['Country']['value'] = 'Different country'
    with pytest.raises(forms.Gate):
        forms.check_approvals('example-co#1', [receipt], current)

@pytest.mark.parametrize('selector', ['#country-button', '#country-search', '#country-clear', '#picker-option-0'])
@pytest.mark.parametrize('verb', ['click', 'fill', 'select', 'combobox', 'choose'])
async def test_picker_decoration_is_never_a_controller_target(phone_grants, selector, verb):
    session = PhoneSession()
    with pytest.raises(forms.Gate):
        await forms.execute(session, await session.page(), {'action': verb, 'selector': selector, 'fact': 'Country'},
                            facts=phone_grants[0], filled={}, resume_path='', company='example-co',
                            jd_text='', resume_tex='', github='', pk='example-co#1', job_url=URL)
    assert session.calls == []


@pytest.mark.parametrize('tamper', [
    None, 'selected_text', 'displayed_text', 'identity', 'grant', 'receipt',
    'location', 'school', 'degree', 'discipline',
])
async def test_complete_phone_form_rechecks_each_current_answer_and_approval(
        phone_grants, tamper):
    facts, row = phone_grants
    session = PhoneSession()
    receipt = await choose_country(session, facts)
    # This explicit synthetic positive creates *named* form-owned values for
    # all five questions, not an exemption for the receiverless HTML.
    page, filled, history = completed_approved_form(
        await session.page(), facts, receipt)
    forms.check_form(page, filled, True, receipts=history)
    forms.check_approvals('example-co#1', history, page)
    if tamper is None:
        return
    country = combo({'page': page}, 'country')
    if tamper in {'selected_text', 'displayed_text'}:
        country['combobox'][tamper] = 'Different country'
    elif tamper == 'identity':
        country['form_action'] = 'https://other.example.test/'
    elif tamper == 'grant':
        row['human_approved_answers']['Country']['value'] = 'Different approval'
    elif tamper == 'receipt':
        receipt['committed_value'] = 'DIFFERENT'
    else:
        peer = combo({'page': page}, tamper)
        peer['value'] = peer['combobox']['selected_value'] = 'DIFFERENT'
    with pytest.raises(forms.Gate):
        forms.check_form(page, filled, True, receipts=history)
        forms.check_approvals('example-co#1', history, page)


async def test_actual_raw_submit_refuses_receiverless_country_without_ipc(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    await assert_unapproved_submit_is_refused(
        dom(fullPhone=True, phases={'country': 'C2'}),
        monkeypatch, tmp_path, require_incomplete=False)

async def test_country_grant_is_not_a_phone_grant(phone_grants):
    facts, row = phone_grants
    del row['human_approved_answers']['Country']
    row['human_approved_answers']['Phone'] = {'value': facts['Country']}
    session = PhoneSession()
    with pytest.raises(forms.Gate):
        await choose_country(session, facts)
    assert session.calls == []


def test_country_open_popup_stays_in_the_native_form():
    snapshot = dom(fullPhone=True, phases={'country': 'C1'}, countryPortal=True)
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False


async def test_receiverless_history_never_compares_nonexistent_transport(phone_grants):
    session, filled = PhoneSession(), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    current = focused_country(await session.page(), receipt)
    with pytest.raises(forms.Gate, match='committed accessible choice'):
        forms.check_approvals('example-co#1', [receipt], current)
    valid = focused_country(await session.page(), receipt, form_owned=True)
    forms.check_approvals('example-co#1', [receipt], valid)
    combo({'page': valid}, 'country')['combobox']['selected_value'] = ''
    with pytest.raises(forms.Gate, match='committed accessible choice'):
        forms.check_approvals('example-co#1', [receipt], valid)

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

@pytest.mark.parametrize('member', [
    'current_caption', 'form_selector', 'missing_name', 'receipt_fact',
    'receipt_selector', 'receipt_code_type', 'receipt_code_literal',
    'receipt_caption', 'receipt_answer', 'fabricated_transport',
])
async def test_each_receiverless_commit_evidence_member_is_validated(phone_grants, member):
    session, filled = PhoneSession(), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    current = focused_country(await session.page(), receipt, form_owned=True)
    field = combo({'page': current}, 'country')
    if member == 'current_caption':
        field['combobox']['selected_text'] = ''
    elif member == 'form_selector':
        field['form_selector'] = '#other-form'
    elif member == 'missing_name':
        field['name'] = ''
    elif member == 'receipt_fact':
        receipt['fact'] = 'unapproved'
    elif member == 'receipt_selector':
        receipt['selector'] = '#forged'
    elif member == 'receipt_code_type':
        receipt['committed_value'] = None
    elif member == 'receipt_code_literal':
        receipt['committed_value'] = 'DIFFERENT'
    elif member == 'receipt_caption':
        receipt['committed_caption'] = 'Different country'
    elif member == 'receipt_answer':
        receipt['approved_value'] = 'Different country'
    elif member == 'fabricated_transport':
        field['combobox']['hidden_value'] = 'invented'
    with pytest.raises(forms.Gate):
        forms.check_form(current, filled, True, receipts=[receipt])
        forms.check_approvals('example-co#1', [receipt], current)

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

async def test_unnamed_required_proxy_allows_approved_country_preparation(
        phone_grants):
    # A required empty native validation proxy is inventoried for Submit but
    # has no name, accessible label or form-owned answer of its own. A common
    # Phone fieldset legend cannot block the Country field's approved selection.
    session, filled = PhoneSession(requiredProxy=True), {}
    original = await session.page()
    country = combo({'page': original}, 'country')
    phone, = [c for c in original['controls'] if c['selector'] == '#phone']
    assert forms._key(original, country) == 'Country'
    assert forms._key(original, phone) == 'Phone'
    proxies = [c for c in original['controls']
               if c.get('required') and c.get('type') == 'text'
               and not c.get('name') and not c.get('label')]
    assert len(proxies) == 5
    assert all(not forms._could_receive_an_answer(c) for c in proxies)
    receipt = await choose_country(session, phone_grants[0], filled)
    assert receipt['fact'] == 'Country'
    assert receipt['committed_caption'] == phone_grants[0]['Country']
    assert session.calls == [
        ('click', '#country'),
        ('fill', '#country', '--value', phone_grants[0]['Country']),
        ('click', '#country-option')]
    # A successful reversible choice does not mint a named form receiver.
    # The complete final check still denies an absent live submitted value.
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(focused_country(await session.page(), receipt),
                         filled, True, receipts=[receipt])


@pytest.mark.parametrize('scenario', [
    {'countryCaption': 'approved country'}, {'countryCodes': {'data-value': 'CODE'}},
])
async def test_genuine_country_option_failure_keeps_country_commit_cause(phone_grants, scenario):
    session, filled = PhoneSession(requiredProxy=True, **scenario), {}
    receipt = await choose_country(session, phone_grants[0], filled)
    assert forms._same(receipt['committed_caption'], phone_grants[0]['Country'])
    assert session.calls == [
        ('click', '#country'),
        ('fill', '#country', '--value', phone_grants[0]['Country']),
        ('click', '#country-option')]
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(focused_country(await session.page(), receipt),
                         filled, True, receipts=[receipt])

def test_genuine_country_option_linkage_failure():
    target = combo(dom(fullPhone=True, phases={'country': 'C1'}), 'country')
    target['combobox']['options'] = []
    with pytest.raises(forms.Gate, match='combobox option linkage'):
        forms._combobox_options(target)

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
    # An optional accessory is not automatically a submitted form answer;
    # a nonempty optional telephone value IS and cannot escape approval.
    phone = next(c for c in page['controls'] if c['selector'] == '#phone')
    phone.update(value='Synthetic unapproved autofill', has_value=True)
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)
    complete = {'url': page['url'], 'controls': [copy.deepcopy(phone)],
                'inventory_verified': True, 'truncated': False,
                'opaque_controls': False, 'shadow_roots': False,
                'unsupported_frames': []}
    with pytest.raises(forms.Gate, match='Unverified or changed form value'):
        forms.check_form(complete, {}, True)

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


def test_generic_legend_question_only_disambiguates_an_exact_named_receiver():
    # The real reader associates the same Phone fieldset legend with a
    # separate country combobox and telephone input. A single native input
    # bearing that legend anchors the two *distinct* visible answer labels.
    # This never grants either answer; exact_approval still binds each raw
    # selector, question, label, form and current per-row approved value.
    page = dom(fullPhone=True)['page']
    country = combo({'page': page}, 'country')
    phone, = [c for c in page['controls'] if c['selector'] == '#phone']
    assert country['question'] == phone['question'] == 'Phone'
    assert country['label'] == 'Country' and phone['label'] == 'Phone'
    assert country['form_selector'] == phone['form_selector']
    assert forms._key(page, country) == 'Country'
    assert forms._key(page, phone) == 'Phone'


@pytest.mark.parametrize('receiver_change', [
    {'required': True},
    {'name': 'country_search'},
    {'value': 'Unapproved country', 'has_value': True},
    {'combobox': {'selected_text': 'Unapproved country'}},
])
def test_optional_accessory_becoming_an_answer_blocks_shared_group_grant(
        receiver_change):
    # A decorative search is not an applicant answer. Becoming required,
    # named, selected or valued restores the duplicate-question denial.
    page = dom(fullPhone=True)['page']
    country = combo({'page': page}, 'country')
    accessory = next(c for c in page['controls']
                     if c['selector'] == '#country-search')
    for key, value in receiver_change.items():
        if key == 'combobox':
            accessory['combobox'].update(value)
        else:
            accessory[key] = value
    with pytest.raises(forms.Gate, match='More than one control uses'):
        forms._key(page, country)


@pytest.mark.parametrize('mutation', ['no-legend-anchor', 'duplicate-label',
                                      'competing-sibling'])
def test_shared_group_legend_without_unique_native_anchor_remains_ambiguous(mutation):
    page = dom(fullPhone=True)['page']
    country = combo({'page': page}, 'country')
    phone, = [c for c in page['controls'] if c['selector'] == '#phone']
    if mutation == 'no-legend-anchor':
        phone['label'] = 'Telephone number'
    elif mutation == 'duplicate-label':
        country['label'] = 'Phone'
    else:
        page['controls'].append({**phone, 'selector': '#second-phone',
                                  'label': 'Phone'})
    with pytest.raises(forms.Gate, match='More than one control uses'):
        forms._key(page, country)