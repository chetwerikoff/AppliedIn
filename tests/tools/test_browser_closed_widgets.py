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
                    'form_selector': '#application', 'form_action': URL}
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


async def test_full_phone_readback_mismatch_gates_after_only_tel_fill(monkeypatch):
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
            phone.update(value='+12025550124', has_value=True)
            return {}
        return await original(*args)
    session.call = transport
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, await session.page(), {'action':'fill','selector':'#phone','fact':'Phone'},
            facts={'Phone':'+12025550123'}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk='example-co#1', job_url=URL)
    assert session.calls == [('fill', '#phone', '--value', '+12025550123')]
