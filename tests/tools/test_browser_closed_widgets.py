"""Closed widgets have a stricter contract than legacy caption/code pickers.

All browser observations below execute the two production JavaScript readers in
Node on a synthetic graph; the Session transports only synthetic snapshots.
"""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill_dom import PAGE

URL = 'https://example-co.test/job/1'
KEYS = ('location', 'school', 'degree', 'discipline')


def dom(**scenario):
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(scenario), PAGE, forms._FIXED_CONTROL_WITNESS,
    ], text=True))


def combo(snapshot, key='location'):
    return next(c for c in snapshot['page']['controls'] if c['selector'] == '#'+key)


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
@pytest.mark.parametrize('active_only', [False, True])
def test_four_fields_same_graph_in_both_production_readers(phase, active_only):
    snapshot = dom(phases=dict.fromkeys(KEYS, phase), activeOnly=active_only)
    p, w = snapshot['page'], snapshot['witness']
    assert p['inventory_verified'] and not p['opaque_controls']
    assert len(p['controls']) == len(w['controls']) == 37
    assert [c['selector'] for c in p['controls']] == [c['selector'] for c in w['controls']]
    assert not any(c['selector'].startswith('#country-') for c in p['controls'])
    for key in KEYS:
        c = combo(snapshot, key)
        wc = next(x for x in w['controls'] if x['selector'] == c['selector'])
        assert c['combobox'] == wc['combobox']
        graph = c['combobox']
        assert graph['mode'] == 'closed-react-select' and graph['phase'] == phase
        assert graph['field_selector'] == '#'+key+'-field'
        assert graph['value_container_selector'] == '#'+key+'-value'
        assert graph['label_selector'] == '#'+key+'-label'
        if phase == 'C2':
            assert all(graph[k] == 'Approved '+key for k in (
                'selected_text', 'displayed_text', 'selected_value', 'hidden_value'))
        if phase == 'C1':
            assert graph['linked'] and graph['options'][0]['value'] == 'Approved '+key


@pytest.mark.parametrize('scenario', [
    {'badGraph': True}, {'multipleReceiver': True}, {'searchToken': True},
    {'extraPicker': True}, {'nestedPicker': True}, {'unsafePicker': True},
    {'phases': {'location': 'C1', 'school': 'C1'}, 'conflict': True},
    {'phases': {'location': 'C1'}, 'shared': True},
    {'phases': {'location': 'C1'}, 'duplicate': True},
])
def test_unproven_graph_or_picker_retains_opacity(scenario):
    snapshot = dom(**scenario)
    assert snapshot['page']['opaque_controls']
    assert not snapshot['page']['inventory_verified']


class Session:
    def __init__(self, **scenario):
        self.scenario = scenario
        self.phases = {}
        self.calls = []
        self.snapshot = dom(**scenario)

    async def page(self):
        return copy.deepcopy(self.snapshot['page'])

    async def call(self, *args):
        if args[0] == 'evaluate':
            assert args[1] == forms._FIXED_CONTROL_WITNESS
            return {'value': copy.deepcopy(self.snapshot['witness'])}
        self.calls.append(args)
        assert args[0] == 'click', 'No filter/clear/unmount/country/submit IPC is needed'
        selector = args[1][1:]
        if selector in KEYS:
            self.phases[selector] = 'C1'
        elif selector.endswith('-option'):
            self.phases[selector.removesuffix('-option')] = 'C2'
        else:
            pytest.fail('Forbidden synthetic IPC: '+repr(args))
        self.snapshot = dom(phases=self.phases, **self.scenario)
        return {}


@pytest.fixture
def grants(monkeypatch):
    answers = {key: {'value': 'Approved '+key, 'selector': '#'+key,
                    'label': key, 'question': key, 'url': URL,
                    'tag': 'input', 'type': 'combobox',
                    'form_selector': '#application-form', 'form_action': URL}
               for key in KEYS}
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting', 'human_approved_answers': answers})
    return {key: 'Approved '+key for key in KEYS}


async def choose(session, facts, key='location', filled=None):
    return await forms._execute_combobox(
        session, await session.page(), {'action': 'combobox', 'selector': '#'+key, 'fact': key},
        facts=facts, filled=filled if filled is not None else {}, pk='example-co#1',
        job_url=URL, ctx=None, provenance=None)


async def test_four_exact_commits_and_presubmit_current_reread(grants):
    session, filled, receipts = Session(), {}, []
    for key in KEYS:
        receipts.append(await choose(session, grants, key, filled))
    final, _ = await forms._observe_controls(session, await session.page())
    forms.check_form(final, filled, True, receipts=receipts)
    forms.check_approvals('example-co#1', receipts, final)
    assert len(session.calls) == 8
    assert len(receipts) == 4
    for receipt in receipts:
        assert receipt['committed_value'] == receipt['approved_value']


@pytest.mark.parametrize('scenario', [
    {'caption': 'approved location'}, {'caption': 'Approved  location'},
    {'code': 'MACHINE_CODE'}, {'noReceiver': True}, {'sharedReceiver': True},
    {'danger': 'ancestor'}, {'danger': 'descendant'},
])
async def test_unsafe_choice_never_clicks_an_option(grants, scenario):
    session = Session(**scenario)
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert all(not c[1].endswith('-option') for c in session.calls)
    if scenario.get('noReceiver') or scenario.get('sharedReceiver'):
        assert session.calls == []


@pytest.mark.parametrize('phase', ['C1', 'C2'])
async def test_fresh_expanded_or_committed_candidate_cannot_admit(grants, phase):
    session = Session(phases={'location': phase})
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert session.calls == []


@pytest.mark.parametrize('key', ['selected_text', 'displayed_text', 'hidden_value', 'selected_value'])
async def test_each_current_readback_is_literal_and_independent(grants, key):
    session = Session()
    receipt = await choose(session, grants)
    current = await session.page()
    combo({'page': current})['combobox'][key] = 'approved location'
    with pytest.raises(forms.Gate):
        forms._check_closed_commit(combo({'page': current}), 'Approved location', receipt)
    with pytest.raises(forms.Gate):
        forms.check_form(current, {'#location': 'Approved location'}, True, receipts=[receipt])


@pytest.mark.parametrize('key', ['closed_admission', 'committed_value', 'committed_caption', 'hidden_selector', 'hidden_value_before'])
async def test_receipt_cannot_replace_current_admission_or_click_witness(grants, key):
    session = Session()
    receipt = await choose(session, grants)
    receipt[key] = 'Approved location' if key == 'hidden_value_before' else 'forged'
    with pytest.raises(forms.Gate):
        # A simulated code-generated receipt with bad evidence also fails;
        # this isolates readback equality from plain-dict/alteration detection.
        forms._check_closed_commit(combo(session.snapshot), 'Approved location', receipt)


async def test_no_receipt_is_not_a_committed_selection(grants):
    session = Session()
    await choose(session, grants)
    with pytest.raises(forms.Gate):
        forms.check_form(await session.page(), {'#location': 'Approved location'}, True)


@pytest.mark.parametrize('scenario', [
    'extraInput', 'noAutocomplete', 'wrongLabel', 'duplicateLabel',
    'explicitOutside', 'invalidExpanded', 'displayQuery', 'multipleTel',
])
def test_each_structural_admission_boundary(scenario):
    snapshot = dom(**{scenario: True})
    assert snapshot['page']['opaque_controls']
    assert not snapshot['page']['inventory_verified']


@pytest.mark.parametrize('scenario', ['foreign', 'nestedList', 'noPopup'])
def test_each_current_popup_ownership_boundary(scenario):
    snapshot = dom(phases={'location': 'C1'}, **{scenario: True})
    assert snapshot['page']['opaque_controls']


@pytest.mark.parametrize('anchor', ['field', 'value', 'wrapper', 'label'])
@pytest.mark.parametrize('stage', ['pre-open', 'expanded', 'committed'])
async def test_current_anchor_drift_gates_before_next_widget_ipc(grants, anchor, stage):
    class Drift(Session):
        reads = 0
        async def page(self):
            self.reads += 1
            if stage == 'pre-open' and self.reads == 3:
                self.snapshot = dom(drift=anchor)
            return await super().page()
        async def call(self, *args):
            result = await super().call(*args)
            if args[0] == 'click' and (
                    (stage == 'expanded' and args[1] == '#location')
                    or (stage == 'committed' and args[1] == '#location-option')):
                self.snapshot = dom(phases=self.phases, drift=anchor)
            return result
    session = Drift()
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert len(session.calls) == {'pre-open': 0, 'expanded': 1, 'committed': 2}[stage]


@pytest.mark.parametrize('phase', ['C0', 'C2'])
async def test_skipped_or_failed_expansion_has_no_option_ipc(grants, phase):
    class Drift(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args == ('click', '#location'):
                self.snapshot = dom(phases={'location': phase})
            return result
    session = Drift()
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert session.calls == [('click', '#location')]


async def test_admitted_widget_cannot_fall_back_to_legacy_linked_branch(grants):
    class Drift(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args == ('click', '#location'):
                self.snapshot = dom(phases={'location': 'C1'}, badGraph=True)
            return result
    session = Drift()
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert session.calls == [('click', '#location')]


async def test_forged_plain_receipt_is_not_code_witnessed(grants):
    session = Session()
    receipt = await choose(session, grants)
    with pytest.raises(forms.Gate):
        forms._check_closed_commit(combo(session.snapshot), 'Approved location', dict(receipt))


async def test_required_unapproved_bootstrap_precedes_all_widget_ipc(grants):
    session = Session()
    for snapshot in (session.snapshot['page'], session.snapshot['witness']):
        field = next(c for c in snapshot['controls'] if c['selector'] == '#additional')
        field.update(required=True, label='Unapproved final question', question='Unapproved final question')
    with pytest.raises(forms.Gate):
        await forms._bootstrap(session, await session.page(), 'example-co#1', grants, URL)
    assert session.calls == []


async def test_committed_new_mode_still_gets_independent_presubmit_witness(grants):
    session = Session()
    for key in KEYS:
        await choose(session, grants, key)
    session.snapshot['witness']['controls'][0]['combobox']['selected_text'] = 'drift'
    with pytest.raises(forms.Gate):
        await forms._bootstrap(session, await session.page(), 'example-co#1', grants, URL)


@pytest.mark.parametrize('boundary', ['initial', 'identity', 'phase', 'downgrade', 'missing'])
async def test_observation_itself_refuses_unproved_continuation(boundary):
    session = Session()
    if boundary != 'initial':
        await forms._observe_controls(session, await session.page())
    if boundary == 'initial':
        session.snapshot = dom(phases={'location': 'C1'})
    elif boundary == 'identity':
        session.snapshot = dom(drift='field')
    elif boundary == 'phase':
        session._closed_admissions['#location']['phase'] = 'C1'
    elif boundary == 'downgrade':
        session.snapshot = dom(phases={'location': 'C1'}, badGraph=True)
    elif boundary == 'missing':
        for observation in session.snapshot.values():
            observation['controls'] = [c for c in observation['controls'] if c['selector'] != '#location']
    with pytest.raises(forms.Gate):
        await forms._observe_controls(session, await session.page())
    assert session.calls == []


@pytest.mark.parametrize('scenario', ['conflict', 'shared', 'foreign', 'nestedList', 'duplicate'])
def test_popup_relationship_itself_refuses_ambiguous_ownership(scenario):
    snapshot = dom(phases={'location': 'C1', 'school': 'C1'}, **{scenario: True})
    assert combo(snapshot)['combobox']['linked'] is False
    assert combo(snapshot)['combobox']['options'] == []


@pytest.mark.parametrize('cleared', [False, True])
async def test_full_phone_readback_mismatch_gates_after_only_tel_fill(monkeypatch, cleared):
    session = Session()
    phone = next(c for c in session.snapshot['page']['controls'] if c['selector']=='#phone')
    phone.update(label='Phone', question='Phone')
    grant = {k: phone[k] for k in (
        'selector', 'label', 'question', 'tag', 'type', 'form_selector', 'form_action')}
    grant.update(value='+12025550123', url=URL)
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk':pk, 'status':'submitting', 'human_approved_answers':{'Phone':grant}})
    original = session.call
    async def transport(*args):
        if args[0] == 'fill':
            session.calls.append(args)
            # PAGE can retain approved-looking data-value while native value is empty.
            phone.update(value='+12025550123' if cleared else '+12025550124',
                         has_value=not cleared)
            return {}
        return await original(*args)
    session.call = transport
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, await session.page(), {'action':'fill','selector':'#phone','fact':'Phone'},
            facts={'Phone':'+12025550123'}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk='example-co#1', job_url=URL)
    assert session.calls == [('fill', '#phone', '--value', '+12025550123')]


@pytest.mark.parametrize('attrs', [
    {'data-value': 'Approved location', 'value': 'OTHER'},
    {'data-value': '', 'value': 'Approved location'},
    {'data-value': 'Approved location', 'value': ''},
    {'data-value': 'OTHER'},
])
async def test_every_present_option_attribute_must_be_approved(grants, attrs):
    session = Session(optionAttrs=attrs)
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert session.calls == [('click', '#location')]


async def test_attribute_less_option_commits_exact_caption_and_receiver(grants):
    session, filled = Session(optionAttrs={}), {}
    receipt = await choose(session, grants, filled=filled)
    assert receipt['committed_caption'] == receipt['committed_value'] == grants['location']
    assert receipt['hidden_value'] == grants['location']
    assert session.calls == [('click', '#location'), ('click', '#location-option')]
    final, _ = await forms._observe_controls(session, await session.page())
    forms.check_form(final, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], final)


def test_cleared_optional_phone_cannot_evade_presubmit():
    page = dom()['page']
    phone = next(c for c in page['controls'] if c['selector'] == '#phone')
    phone.update(value='+12025550123', has_value=False, required=False)
    with pytest.raises(forms.Gate, match='phone value'):
        forms.check_form(page, {'#phone': '+12025550123'}, True)


@pytest.mark.parametrize('delayed', ['C1', 'C2', 'both'])
async def test_delayed_component_phase_is_observed_without_extra_ipc(grants, delayed):
    class Delayed(Session):
        pending = None
        reads = 0
        async def call(self, *args):
            before = self.snapshot
            result = await super().call(*args)
            if args[0] == 'click':
                phase = 'C2' if args[1].endswith('-option') else 'C1'
                if delayed in {phase, 'both'}:
                    self.pending, self.snapshot, self.reads = self.snapshot, before, 0
            return result
        async def page(self):
            if self.pending is not None:
                self.reads += 1
                if self.reads > 2:
                    self.snapshot, self.pending = self.pending, None
            return await super().page()
    session = Delayed()
    receipt = await choose(session, grants)
    assert receipt['committed_value'] == 'Approved location'
    assert session.calls == [('click', '#location'), ('click', '#location-option')]


@pytest.mark.parametrize('verb', ['submit', 'click'])
async def test_direct_route_commit_requires_latest_fixed_witness(monkeypatch, grants, verb):
    from unittest.mock import AsyncMock
    from tools import browser_skill as bsk
    class Direct(Session):
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, url):
            assert url == URL
            return await self.page()
    session = Direct()
    await choose(session, grants)
    session.calls.clear()
    if verb == 'click':
        for snapshot in session.snapshot.values():
            next(c for c in snapshot['controls'] if c['selector'] == '#submit')['label'] = 'Continue'
    async def decide(*args, **kwargs):
        # The direct route has no alias. Drift arrives after bootstrap/model input.
        combo({'page': session.snapshot['witness']})['combobox']['selected_text'] = 'late drift'
        return {'action': verb, 'selector': '#submit'}
    monkeypatch.setattr(bsk, 'Session', lambda *a, **kw: session)
    monkeypatch.setattr(bsk, 'decision', decide)
    monkeypatch.setattr(forms, '_bootstrap', AsyncMock(side_effect=lambda s, p, *a, **kw: p))
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'check_form', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'check_approvals', lambda *a, **kw: None)
    held = []
    monkeypatch.setattr(forms, 'hold_possible_submission', lambda *a, **kw: held.append(a))
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *a: '')
    result = await forms.apply(URL, 'example-co', grants, 'synthetic',
                               pk='example-co#1', resume_path='Synthetic.pdf')
    assert result['status'] == 'gate' and 'DOM witness' in result['question']
    assert not held and session.calls == []


async def test_never_committed_component_times_out_without_more_ipc(monkeypatch, grants):
    class NeverCommitted(Session):
        async def call(self, *args):
            before = self.snapshot
            result = await super().call(*args)
            if args == ('click', '#location-option'):
                self.snapshot = before
            return result
    async def no_sleep(delay):
        pass
    monkeypatch.setattr(forms.asyncio, 'sleep', no_sleep)
    session = NeverCommitted()
    with pytest.raises(forms.Gate, match='bounded wait'):
        await choose(session, grants)
    assert session.calls == [('click', '#location'), ('click', '#location-option')]


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
def test_required_proxies_are_not_answers_or_immutable_identity(phase):
    snapshot = dom(fullPhone=True, requiredProxy=True, phases=dict.fromkeys((*KEYS, 'country'), phase))
    assert snapshot['page']['inventory_verified'] is True
    assert snapshot['page']['opaque_controls'] is False
    assert len(snapshot['page']['controls']) == len(snapshot['witness']['controls']) == 38
    for key in ('location', 'country'):
        target = combo(snapshot, key)
        graph = target['combobox']
        assert graph == next(c['combobox'] for c in snapshot['witness']['controls'] if c['selector'] == '#'+key)
        assert graph['required_proxy_mounted'] is (phase != 'C2')
        c0 = combo(dom(fullPhone=True, requiredProxy=True), key)
        assert forms._closed_key(target) == forms._closed_key(c0)
    country = combo(snapshot, 'country')['combobox']
    assert len(country['country_graph']) == 13
    assert country['field_selector'] == '#country-container'
    assert not {'hidden_selector', 'hidden_value', 'selected_value'} & country.keys()


PROXY_BAD = [
    *({'attr': [k, None]} for k in ('required', 'tabindex', 'aria-hidden', 'class')),
    {'attr': ['tabindex', '0']}, {'attr': ['aria-hidden', 'false']},
    *({'attr': [k, '']} for k in ('type', 'name', 'id', 'role', 'form',
        'disabled', 'readonly', 'autofocus', 'style', 'aria-label', 'data-answer', 'onclick', 'unknown')),
    {'value': 'Synthetic answer'}, {'style': {'opacity': '1'}},
    {'attr': ['value', 'Synthetic answer']},
    {'style': {'position': 'static'}}, {'style': {'pointerEvents': 'auto'}},
    {'parent': 'inside'}, {'foreign': 'other'}, {'foreign': 'null'}, {'second': True},
    {'shellCount': True}, {'shellNested': True}, {'comboForeign': True}, {'tag': 'textarea'},
    {'shellOutsideControl': True},
    {'ownerAttr': ['aria-required', None]}, {'ownerAttr': ['aria-required', 'false']},
    {'ownerAttr': ['aria-haspopup', 'false']}, {'ownerAttr': ['aria-autocomplete', None]},
    {'ownerAttr': ['aria-expanded', 'mixed']}, {'ownerAttr': ['aria-labelledby', 'missing']},
    *({'extra': kind} for kind in ('input', 'contenteditable', 'combobox')),
]


@pytest.mark.parametrize('key', ['location', 'country'])
@pytest.mark.parametrize('mutation', PROXY_BAD)
@pytest.mark.parametrize('phase', ['C0', 'C1'])
def test_required_proxy_fingerprint_and_first_C1_quarantine(key, mutation, phase):
    snapshot = dom(fullPhone=True, requiredProxy=True, phases={key: phase},
                   proxyMutation={'key': key, **mutation})
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    for reader in snapshot.values():
        graph = next(c['combobox'] for c in reader['controls'] if c['selector'] == '#'+key)
        assert graph.get('mode') != 'closed-react-select'
        assert graph.get('quarantined') is True
        assert graph['linked'] is False and graph['link_id'] == '' and graph['options'] == []


async def test_proxy_disappearance_preserves_real_commit_and_approvals(grants):
    session, filled, receipts = Session(requiredProxy=True), {}, []
    for key in KEYS:
        receipts.append(await choose(session, grants, key, filled))
    current, _ = await forms._observe_controls(session, await session.page())
    forms.check_form(current, filled, True, receipts=receipts)
    forms.check_approvals('example-co#1', receipts, current)
    assert session.calls == [step for key in KEYS for step in [('click', '#'+key), ('click', '#'+key+'-option')]]


@pytest.mark.parametrize('key', ['location', 'country'])
async def test_mounted_proxy_gates_even_with_committed_token_and_receipt(key, monkeypatch):
    # Import synthetic phone helpers without requiring pytest fixture registration.
    import runpy
    phone = runpy.run_path(str(Path(__file__).with_name('test_browser_phone_block.py')))
    facts = {**dict.fromkeys(KEYS, ''), 'Country': 'Approved country'}
    for k in KEYS:
        facts[k] = 'Approved '+k
    baseline = dom(fullPhone=True, requiredProxy=True)
    answers = {}
    for k, answer in facts.items():
        t = combo(baseline, 'country' if k == 'Country' else k)
        answers[k] = {**{n: t[n] for n in ('selector', 'label', 'question', 'tag', 'type', 'form_selector', 'form_action')}, 'value': answer, 'url': URL}
    monkeypatch.setattr(forms, '_row', lambda pk: {'pk': pk, 'status': 'submitting', 'human_approved_answers': answers})
    session = phone['PhoneSession'](requiredProxy=True)
    receipt = await (phone['choose_country'](session, facts) if key == 'country' else choose(session, facts))
    target = combo(session.snapshot, key)
    target['combobox']['required_proxy_mounted'] = True
    with pytest.raises(forms.Gate, match='closed_select.required_proxy_mounted'):
        forms._check_closed_commit(target, 'Approved '+key, receipt)
    with pytest.raises(forms.Gate, match='closed_select.required_proxy_mounted'):
        forms.check_form({'controls': [target], 'inventory_verified': True}, {}, True, receipts=[receipt])
    mounted = dom(fullPhone=True, requiredProxy=True, phases={key: 'C2'},
                  proxyMutation={'key': key, 'mounted': True})
    session.snapshot = mounted
    with pytest.raises(forms.Gate, match='closed_select.required_proxy_mounted'):
        await forms._observe_controls(session, await session.page())
    assert all(c[1] not in ('#submit', '#additional') for c in session.calls)


@pytest.mark.parametrize('key', ['location', 'country'])
def test_untouched_required_proxy_and_outside_F_cannot_submit(key):
    snapshot = dom(fullPhone=True, requiredProxy=True)
    target = combo(snapshot, key)
    with pytest.raises(forms.Gate, match='closed_select.required_proxy_uncommitted'):
        forms.check_form({'controls': [target], 'inventory_verified': True}, {}, True)
    outside = dom(fullPhone=True, requiredProxy=True, proxyMutation={'key': key, 'parent': 'outside'})
    assert outside['page']['inventory_verified'] is True
    assert combo(outside, key)['combobox']['mode'] == 'closed-react-select'
    ordinary = [c for c in outside['page']['controls'] if c.get('required') and c['type'] == 'text' and not c['has_value']]
    assert len(ordinary) == 1
    with pytest.raises(forms.Gate):
        forms.check_form({'controls': ordinary, 'inventory_verified': True}, {}, True)


@pytest.mark.parametrize('scenario,expected', [
    ({'custom': True}, '*:nth-child('),
    ({'depth': 14}, 'canvas:nth-of-type('),
    ({'count': 2}, 'canvas:nth-of-type('),
    ({'count': 8}, 'canvas:nth-of-type('),
])
def test_diagnostic_locators_are_unique_and_never_page_identifiers(scenario, expected):
    snapshot = dom(diagnostics=scenario)
    p = snapshot['page']
    assert p['opaque_count'] == scenario.get('count', 1)
    assert len(p['opaque_locators']) == min(3, p['opaque_count'])
    assert all(expected in x for x in p['opaque_locators'])
    assert all(forms._safe_diagnostic(x) == x for x in p['opaque_locators'])
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(p)
    reason = str(caught.value)
    assert 'opaque_controls' in reason
    assert 'control=unavailable' not in reason
    assert 'test-user' not in reason and '@' not in reason
    assert 'private-' not in reason
    assert reason.count('control=') <= 3


def test_repeated_deep_opaque_nodes_remain_fail_closed():
    # Two distinct sibling subtrees have the same final twelve structural
    # segments. The inventory must still Gate even while AC3 diagnostics await
    # a separately authorized change to the bounded log-safe CSS grammar.
    p = dom(diagnostics={'count': 2, 'depth': 14})['page']
    assert p['opaque_count'] == 2
    assert p['opaque_controls'] is True and p['inventory_verified'] is False
    assert len(p['opaque_locators']) == 2
    with pytest.raises(forms.Gate, match='opaque_count=2'):
        forms._inventory_ok(p)


@pytest.mark.xfail(
    strict=True,
    reason='Issue #110 AC3: safe Gate grammar permits only 12 direct-parent segments; '
           'repeated deeper branches require a separately approved validator scope change',
)
def test_repeated_deep_opaque_nodes_have_distinct_safe_locators():
    # Regression required by review #5558913444. The two canvases are present
    # and unique in the DOM but have identical last-12-segment selectors.
    # Keep the desired outcome explicit rather than silently weakening the
    # existing _safe_diagnostic validator or pretending this AC has passed.
    p = dom(diagnostics={'count': 2, 'depth': 14})['page']
    assert p['opaque_count'] == 2
    paths = p['opaque_locators']
    assert len(paths) == 2
    assert all(isinstance(x, str) and x != 'unavailable'
               and forms._safe_diagnostic(x) == x for x in paths)
    assert len(set(paths)) == 2
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(p)
    detail = str(caught.value)
    assert detail.count('control=') == 2
    assert 'control=unavailable' not in detail
    assert 'test-user' not in detail and 'example-co' not in detail and '@' not in detail


@pytest.mark.parametrize('fresh', [False, True])
@pytest.mark.parametrize('flags', [
    {'opaque_controls': True, 'opaque_count': 1, 'opaque_locators': ['#test-user@example-co.test']},
    {'unsupported_frames': ['https://test-user@example-co.test/private']},
    {'shadow_roots': True}, {'truncated': True}, {'inventory_verified': False},
    {'opaque_controls': True, 'unsupported_frames': ['private'], 'shadow_roots': True, 'truncated': True},
])
async def test_country_never_masks_initial_or_fresh_inventory_causes(fresh, flags):
    session = Session(fullPhone=True)
    original = await session.page()
    unsafe = {**copy.deepcopy(original), **flags}
    if fresh:
        async def current():
            return unsafe
        session.page = current
    with pytest.raises(forms.Gate) as caught:
        await forms._observe_controls(session, original if fresh else unsafe)
    reason = str(caught.value)
    for flag in flags:
        if flag in {'opaque_controls', 'unsupported_frames', 'shadow_roots', 'truncated'}:
            assert flag in reason
    if flags == {'inventory_verified': False}:
        assert 'inventory_verified=false/readiness' in reason
    assert 'receiverless country commitment' not in reason
    assert 'test-user' not in reason and '@' not in reason and 'https:' not in reason
    assert session.calls == []


@pytest.mark.parametrize('kind,predicate', [
    ('empty', 'combobox.link_id.required_nonempty'), ('unequal', 'combobox.link_id.equal'),
    ('timeout', 'unavailable'), ('cancel', 'unavailable'),
])
async def test_witness_failure_names_class_and_first_predicate_without_values(kind, predicate):
    import asyncio
    session = Session(fullPhone=True)
    if kind in {'empty', 'unequal'}:
        for reader in session.snapshot.values():
            c = next(c for c in reader['controls'] if c['selector'] == '#location')
            c['selector'] = '#test-user@example-co.test'
            c['combobox'] = {'linked': False, 'link_id': '', 'options': []}
        if kind == 'unequal':
            session.snapshot['witness']['controls'][0]['combobox']['link_id'] = 'private answer'
    else:
        async def error(*args):
            raise (TimeoutError('test-user@example-co.test private answer') if kind == 'timeout'
                   else asyncio.CancelledError('private answer'))
        session.call = error
    with pytest.raises(forms.Gate) as caught:
        await forms._observe_controls(session, await session.page())
    reason = str(caught.value)
    assert 'exception=' + {'empty': 'ValueError', 'unequal': 'ValueError',
                            'timeout': 'TimeoutError', 'cancel': 'CancelledError'}[kind] in reason
    assert 'predicate=' + predicate in reason
    assert 'test-user' not in reason and 'private answer' not in reason
    assert 'receiverless country commitment' not in reason
    assert session.calls == []


@pytest.mark.parametrize('boundary', ['missing', 'downgrade', 'initial', 'drift'])
async def test_every_admission_Gate_and_form_question_passes_witness_try(monkeypatch, boundary):
    session = Session(fullPhone=True)
    sentinel = forms.Gate('closed admission ' + boundary, form_question={'synthetic': boundary})
    def refused(*args, **kwargs):
        raise sentinel
    monkeypatch.setattr(forms, '_observe_closed_admissions', refused)
    with pytest.raises(forms.Gate) as caught:
        await forms._observe_controls(session, await session.page())
    assert caught.value is sentinel
    assert caught.value.form_question == {'synthetic': boundary}
    assert session.calls == []


@pytest.mark.parametrize('cause', ['inventory', 'approval', 'destination', 'phase timeout'])
async def test_country_wrapper_preserves_noncommit_Gates(monkeypatch, cause):
    session = Session(fullPhone=True)
    sentinel = forms.Gate('safe synthetic ' + cause, form_question={'synthetic': cause})
    async def refused(*args, **kwargs):
        raise sentinel
    monkeypatch.setattr(forms, '_execute_closed_combobox', refused)
    with pytest.raises(forms.Gate) as caught:
        await forms._execute_combobox(session, await session.page(),
            {'action': 'combobox', 'selector': '#country'}, facts={}, filled={},
            pk='example-co#1', job_url=URL, ctx=None, provenance=None)
    assert caught.value is sentinel
    assert session.calls == []


@pytest.mark.parametrize('scenario', ['badGraph', 'extraInput', 'noAutocomplete', 'wrongLabel',
    'duplicateLabel', 'explicitOutside', 'invalidExpanded', 'displayQuery', 'multipleReceiver'])
def test_each_failed_owner_check_is_quarantined_before_real_legacy_C1_options(scenario):
    snapshot = dom(requiredProxy=True, phases={'location': 'C1'}, **{scenario: True})
    assert snapshot['page']['opaque_controls'] is True
    for reader in snapshot.values():
        graph = next(c['combobox'] for c in reader['controls'] if c['selector'] == '#location')
        assert graph.get('quarantined') is True
        assert graph['linked'] is False and graph['options'] == []


def test_candidate_signal_A_survives_without_any_proxy_signature():
    snapshot = dom(phases={'location': 'C1'}, noAutocomplete=True)
    for reader in snapshot.values():
        graph = next(c['combobox'] for c in reader['controls'] if c['selector'] == '#location')
        assert graph.get('quarantined') is True
        assert graph['linked'] is False and graph['options'] == []
