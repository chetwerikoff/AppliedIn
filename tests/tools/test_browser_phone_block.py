"""The receiverless country is an answer; the hidden ITI is never an answer."""
import copy

import pytest

from tools import browser_skill_apply as forms
import runpy
from pathlib import Path

_helpers = runpy.run_path(str(Path(__file__).with_name('test_browser_closed_widgets.py')))
Session, dom, combo, KEYS, URL = (_helpers[k] for k in ('Session', 'dom', 'combo', 'KEYS', 'URL'))


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
        self.snapshot = dom(phases=self.phases, **self.scenario)
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
    row = {'pk': 'example-co#1', 'status': 'submitting', 'human_approved_answers': grants}
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    return facts, row


async def choose_country(session, facts, filled=None):
    return await forms._execute_combobox(
        session, await session.page(),
        {'action': 'combobox', 'selector': '#country', 'fact': 'Country'},
        facts=facts, filled=filled if filled is not None else {}, pk='example-co#1',
        job_url=URL, ctx=None, provenance=None)


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
def test_exact_phone_inventory_uses_both_production_readers(phase):
    snapshot = dom(fullPhone=True, phases={'country': phase})
    p, w = snapshot['page'], snapshot['witness']
    assert p['inventory_verified'] is True and p['opaque_controls'] is False
    assert p['truncated'] is False
    assert set(w) == {'url', 'controls'}
    assert [c['selector'] for c in p['controls']] == [c['selector'] for c in w['controls']]
    assert {'#country', '#phone'} <= {c['selector'] for c in p['controls']}
    assert not any(c['selector'].startswith(('#picker-option-', '#country-search', '#country-clear', '#country-button')) for c in p['controls'])
    c = combo(snapshot, 'country')['combobox']
    assert c == next(c['combobox'] for c in w['controls'] if c['selector'] == '#country')
    assert c['receiverless_country'] is True and len(c['country_graph']) == 13
    assert not {'selected_value', 'hidden_value', 'hidden_selector'} & c.keys()
    if phase == 'C2':
        assert c['selected_text'] == c['displayed_text'] == 'Approved country'


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
def test_unproven_phone_shape_remains_opaque(scenario):
    snapshot = dom(fullPhone=True, **scenario)
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    if scenario.get('flatPicker'):
        # This separate legacy shape cannot be covered by mutating the nested graph.
        for reader in snapshot.values():
            sels = {c['selector'] for c in reader['controls']}
            assert {'#country-button', '#country-search', '#picker-option-0'} <= sels
    if any(k.startswith('picker') or k in {'visiblePicker', 'multipleTel', 'extraPicker', 'nestedPicker'} for k in scenario):
        for reader in snapshot.values():
            sels = {c['selector'] for c in reader['controls']}
            assert '#country-search' in sels and any(c['type'] == 'option' for c in reader['controls'])


async def test_receiverless_commit_has_no_fabricated_transport(phone_grants):
    facts, _ = phone_grants
    session, filled = PhoneSession(presentation=True, pickerDecoration=True), {}
    receipt = await choose_country(session, facts, filled)
    assert not {'selected_value', 'hidden_value', 'hidden_selector', 'committed_value', 'hidden_value_before'} & receipt.keys()
    final, _ = await forms._observe_controls(session, await session.page())
    forms.check_form(final, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], final)
    assert session.calls == [('click', '#country'), ('click', '#country-option')]


@pytest.mark.parametrize('scenario', [
    {'countryCaption': 'approved country'}, {'countryCaption': 'Approved  country'},
    {'countryCodes': {'value': 'CODE'}}, {'countryCodes': {'data-value': ''}},
    {'countryCodes': {'value': 'Approved country', 'data-value': 'CODE'}},
    {'countryDanger': True},
])
async def test_country_option_literal_or_safety_failure_precedes_click(phone_grants, scenario):
    facts, _ = phone_grants
    session = PhoneSession(**scenario)
    with pytest.raises(forms.Gate):
        await choose_country(session, facts)
    assert session.calls == [('click', '#country')]


@pytest.mark.parametrize('phase', ['C1', 'C2'])
async def test_country_candidate_cannot_supply_initial_admission(phone_grants, phase):
    session = PhoneSession(phases={'country': phase})
    with pytest.raises(forms.Gate):
        await choose_country(session, phone_grants[0])
    assert session.calls == []


@pytest.mark.parametrize('key', ['selected_text', 'displayed_text'])
async def test_each_country_token_projection_is_literal(phone_grants, key):
    session = PhoneSession()
    receipt = await choose_country(session, phone_grants[0])
    target = combo(session.snapshot, 'country')
    target['combobox'][key] = 'approved country'
    with pytest.raises(forms.Gate, match='receiverless country commitment'):
        forms._check_closed_commit(target, 'Approved country', receipt)


@pytest.mark.parametrize('tamper', ['plain', 'receipt', 'phases', 'identity', 'grant'])
async def test_country_history_cannot_replace_original_proof(phone_grants, tamper):
    facts, row = phone_grants
    session = PhoneSession()
    receipt = await choose_country(session, facts)
    if tamper == 'plain':
        receipt = dict(receipt)
    elif tamper == 'receipt':
        receipt['option_selector'] = '#forged'
    elif tamper == 'phases':
        session._closed_admissions['#country']['phases'] = ('C0', 'C2')
    elif tamper == 'identity':
        combo(session.snapshot, 'country')['combobox']['country_graph'][5] = '#changed'
    else:
        row['human_approved_answers']['Country']['value'] = 'Changed approval'
    with pytest.raises(forms.Gate):
        forms.check_approvals('example-co#1', [receipt], await session.page())


@pytest.mark.parametrize('selector', ['#country-button', '#country-search', '#country-clear', '#picker-option-0'])
@pytest.mark.parametrize('verb', ['click', 'fill', 'select', 'combobox', 'choose'])
async def test_picker_decoration_is_never_a_controller_target(phone_grants, selector, verb):
    session = PhoneSession()
    with pytest.raises(forms.Gate):
        await forms.execute(session, await session.page(), {'action': verb, 'selector': selector, 'fact': 'Country'},
                            facts=phone_grants[0], filled={}, resume_path='', company='example-co',
                            jd_text='', resume_tex='', github='', pk='example-co#1', job_url=URL)
    assert session.calls == []


@pytest.mark.parametrize('verb', ['submit', 'click'])
@pytest.mark.parametrize('tamper', [None, 'selected_text', 'displayed_text', 'identity', 'grant', 'receipt',
                                  'location', 'school', 'degree', 'discipline'])
async def test_actual_native_preflights_reconcile_country_without_transport(
        monkeypatch, phone_grants, verb, tamper):
    # Keep real bootstrap, execution, check_form and verify_history/check_approvals.
    # The hold seam stops the valid path after history, strictly before click IPC.
    from tools import browser_skill as bsk
    facts, row = phone_grants
    history_seen, form_seen, holds = [], [], []
    class Direct(PhoneSession):
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, url):
            assert url == URL
            return await self.page()
        async def call(self, *args, **kwargs):
            if args[0] == 'upload':
                assert args == ('upload', '#resume', '--file', 'Synthetic.pdf')
                next(c for c in self.snapshot['page']['controls'] if c['selector'] == '#resume')['files'] = ['Synthetic.pdf']
                self.calls.append(args)
                return {}
            result = await super().call(*args)
            if verb == 'click':
                for snapshot in self.snapshot.values():
                    next(c for c in snapshot['controls'] if c['selector'] == '#submit')['label'] = 'Continue'
            return result
    session = Direct()
    if verb == 'click':
        for snapshot in session.snapshot.values():
            next(c for c in snapshot['controls'] if c['selector'] == '#submit')['label'] = 'Continue'
    actions = iter([*({'action': 'combobox', 'selector': '#'+k, 'fact': k} for k in KEYS),
                    {'action': 'combobox', 'selector': '#country', 'fact': 'Country'},
                    {'action': 'upload', 'selector': '#resume'},
                    {'action': verb, 'selector': '#submit'}])
    async def decide(task, page, history, **kwargs):
        action = next(actions)
        if action['action'] == verb and action['selector'] == '#submit':
            if tamper in {'selected_text', 'displayed_text'}:
                for snapshot in session.snapshot.values():
                    combo({'page': snapshot}, 'country')['combobox'][tamper] = 'approved country'
            elif tamper == 'identity':
                for snapshot in session.snapshot.values():
                    combo({'page': snapshot}, 'country')['combobox']['country_graph'][5] = '#changed'
            elif tamper == 'grant':
                row['human_approved_answers']['Country']['value'] = 'Changed approval'
            elif tamper == 'receipt':
                next(r for r in history if r.get('selector') == '#country')['option_selector'] = '#forged'
            elif tamper in KEYS:
                for snapshot in session.snapshot.values():
                    combo({'page': snapshot}, tamper)['combobox']['selected_value'] = 'Changed transport'
        return action
    original_form, original_history = forms.check_form, forms.check_approvals
    def checked_form(*args, **kwargs):
        result = original_form(*args, **kwargs)
        form_seen.append(True)
        return result
    def checked_history(*args, **kwargs):
        result = original_history(*args, **kwargs)
        history_seen.append(True)
        return result
    def stop_before_hold(*args, **kwargs):
        holds.append(True)
        raise forms.Gate('offline intercept after native history reconciliation')
    monkeypatch.setattr(bsk, 'Session', lambda *a, **kw: session)
    monkeypatch.setattr(bsk, 'decision', decide)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'check_form', checked_form)
    monkeypatch.setattr(forms, 'check_approvals', checked_history)
    monkeypatch.setattr(forms, 'hold_possible_submission', stop_before_hold)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *a: '')
    result = await forms.apply(URL, 'example-co', facts, 'synthetic', pk='example-co#1', resume_path='Synthetic.pdf')
    assert result['status'] == 'gate'
    assert not any(c == ('click', '#submit') for c in session.calls)
    if tamper is None:
        assert form_seen and history_seen and holds, result
        assert 'offline intercept' in result['question']
    else:
        assert not holds and not history_seen, result


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
    session = PhoneSession()
    receipt = await choose_country(session, phone_grants[0])
    class NoTransport(dict):
        def get(self, key, *args):
            assert key not in {'selected_value', 'hidden_value'}, 'receiverless transport comparison'
            return super().get(key, *args)
    page = await session.page()
    target = combo({'page': page}, 'country')
    target['combobox'] = NoTransport(target['combobox'])
    forms.check_approvals('example-co#1', [receipt], page)


def test_served_fixture_html_itself_passes_production_readers_offline():
    # Parse the actual served bytes: no HTTP server, network or second fixture.
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
    tree.feed(fixture._FORM.decode())
    snapshot = dom(fixtureTree=tree.root, fixtureUrl=fixture.URL)
    assert snapshot['page']['inventory_verified'] is True
    assert snapshot['page']['opaque_controls'] is False
    assert snapshot['page']['truncated'] is False
    assert [c['selector'] for c in snapshot['page']['controls']] == [c['selector'] for c in snapshot['witness']['controls']]
    assert combo(snapshot, 'country')['combobox']['receiverless_country'] is True
    assert not any(c['selector'].startswith('#iti-0__') for c in snapshot['page']['controls'])


@pytest.mark.parametrize('member', ['phase', 'admission_key', 'admission_phase', 'receipt_key',
                                   'option_selector', 'option_codes_type', 'option_codes_literal',
                                   'receipt_caption', 'receipt_answer', 'fabricated_transport'])
async def test_each_receiverless_commit_evidence_member_is_validated(phone_grants, member):
    session = PhoneSession()
    receipt = await choose_country(session, phone_grants[0])
    target = copy.deepcopy(combo(session.snapshot, 'country'))
    if member == 'phase':
        target['combobox']['phase'] = 'C1'
    elif member == 'admission_key':
        receipt._country_admission['key'] = ('https://example-co.test/changed', forms._closed_key(target))
    elif member == 'admission_phase':
        receipt._country_admission['phase'] = 'C1'
    elif member == 'receipt_key':
        receipt['closed_admission'] = ('wrong-key',)
    elif member == 'option_selector':
        receipt['option_selector'] = ''
    elif member == 'option_codes_type':
        receipt['option_codes'] = []
    elif member == 'option_codes_literal':
        receipt['option_codes'] = ('CODE',)
    elif member == 'receipt_caption':
        receipt['committed_caption'] = 'approved country'
    elif member == 'receipt_answer':
        receipt['approved_value'] = 'Changed approval'
    elif member == 'fabricated_transport':
        receipt['hidden_value'] = 'invented'
    # Simulate a defective code-owned producer, separately from seal-tamper tests.
    receipt._witnessed = tuple(sorted(receipt.items()))
    with pytest.raises(forms.Gate, match='receiverless country commitment'):
        forms._check_closed_commit(target, 'Approved country', receipt)


@pytest.mark.parametrize('scenario', ['secondIti', 'movedIti', 'renamedPhone',
                                      'secondTelOutside', 'brokenPhoneChain'])
def test_picker_exemption_requires_one_exact_phone_branch(scenario):
    # A fully linked second picker is not the already-covered two-tels-in-one case.
    snapshot = dom(fullPhone=True, **{scenario: True})
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    for reader in snapshot.values():
        selectors = {c['selector'] for c in reader['controls']}
        assert {'#country-button', '#country-search', '#picker-option-0'} <= selectors
        if scenario == 'secondIti':
            assert {'#second-country-button', '#second-country-search',
                    '#second-picker-option-0'} <= selectors
