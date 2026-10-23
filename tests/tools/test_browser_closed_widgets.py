"""Closed widgets have a stricter contract than legacy caption/code pickers.

All browser observations below execute the two production JavaScript readers in
Node on a synthetic graph; the Session transports only synthetic snapshots.
"""
import copy
from tests.tools.reader_node_harness import reader_output
import json
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill_dom import PAGE

URL = 'https://example-co.test/job/1'
KEYS = ('location', 'school', 'degree', 'discipline')


def _one_shot_dom(**scenario):
    # Keep the exact pre-migration CLI arguments and the independent witness.
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(scenario), PAGE, forms._FIXED_CONTROL_WITNESS,
    ], text=True))


def dom(**scenario):
    if scenario.get('secondIti'):
        # Cloning the full 244-option phone picker exceeds the VM request
        # budget in CI. Preserve the original one-shot reader/witness command.
        return _one_shot_dom(**scenario)
    return json.loads(reader_output(
        'closed_widget_dom.cjs', scenario, PAGE, forms._FIXED_CONTROL_WITNESS))


def combo(snapshot, key='location'):
    return next(c for c in snapshot['page']['controls'] if c['selector'] == '#'+key)


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
@pytest.mark.parametrize('active_only', [False, True])
async def test_generic_four_field_readers_never_grant_final_submit(
        phase, active_only, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # All C0/C1/C2 phases (including preexpanded and precommitted) are tested
    # at the actual raw Submit seam below. Phase by itself must never demand
    # a per-step human Gate or confer submission authority.
    # A C0/C1/C2 class/phase certificate no longer exists. The generic
    # ARIA owners must agree across the independent readers, and neither a
    # legacy "selected" caption nor a phase authorizes application Submit.
    snapshot = dom(phases=dict.fromkeys(KEYS, phase), activeOnly=active_only)
    page, witness = snapshot['page'], snapshot['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    for key in KEYS:
        owner = combo(snapshot, key)
        independent = next(x for x in witness['controls']
                           if x['selector'] == owner['selector'])
        assert owner['type'] == independent['type'] == 'combobox'
        assert owner['combobox'] == independent['combobox']
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)


async def test_receiver_backed_country_still_requires_final_answer_binding(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # A native hidden receiver may be observed without any ATS-specific
    # country exception. Its caption and selector are not approved answers.
    snapshot = dom(receiverBackedCountry=True, phases={'location': 'C2'})
    page, witness = snapshot['page'], snapshot['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    for reader in (page, witness):
        country = next(c for c in reader['controls']
                       if c['selector'] == '#country')
        assert country['type'] == 'combobox'
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)


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
        if args[0] == 'fill':
            # r06 permits an exact-approved reversible picker search write.
            # Filling does NOT select an option, populate any hidden receiver,
            # or confer permission for a final Submit. Only the raw option
            # click may cause a later independent committed-value readback.
            if (len(args) != 4 or args[2] != '--value'
                    or args[1] not in {'#' + k for k in (*KEYS, 'country')}
                    or not isinstance(args[3], str) or not args[3]):
                pytest.fail('Unsafe synthetic combobox fill: '+repr(args))
            self.calls.append(args)
            for reader in self.snapshot.values():
                matches = [c for c in reader['controls']
                           if c['selector'] == args[1]]
                assert len(matches) == 1 and matches[0]['type'] == 'combobox'
                target = matches[0]
                target.update(value=args[3], has_value=True)
                target['combobox']['displayed_text'] = args[3]
                assert target['combobox'].get('selected_value', '') == ''
            return {}
        self.calls.append(args)
        assert args[0] == 'click', 'No filter/clear/unmount/country/submit IPC is needed'
        selector = args[1][1:]
        if selector in (*KEYS, 'country'):
            self.phases[selector] = 'C1'
        elif selector.endswith('-option') and selector.removesuffix('-option') in (*KEYS, 'country'):
            self.phases[selector.removesuffix('-option')] = 'C2'
        else:
            pytest.fail('Forbidden synthetic IPC: '+repr(args))
        scenario = {**self.scenario, 'phases': {
            **self.scenario.get('phases', {}), **self.phases}}
        self.snapshot = dom(**scenario)
        return {}


class FormOwnedSelection(Session):
    """Emulate form-owned values from a real DOM option change handler.

    The base fake regenerates synthetic DOM on every picker action. Persist
    each independently observed committed selection across those re-renders;
    a search fill alone still cannot select an option or create a receiver.
    """
    def __init__(self, **scenario):
        super().__init__(**scenario)
        self.selected_form_values = {}
        self.observed_choice = None

    async def call(self, *args):
        selected = None
        key = None
        if len(args) == 2 and args[0] == 'click' and args[1].endswith('-option'):
            key = args[1][1:].removesuffix('-option')
            owner = next(c for c in self.snapshot['page']['controls']
                         if c['selector'] == '#'+key)
            matches = [o for o in owner['combobox']['options']
                       if o.get('selector') == '#'+key+'-option']
            assert len(matches) == 1
            selected = copy.deepcopy(matches[0])
            assert selected['unique'] is True and selected['safe'] is True
            assert not selected.get('submit') and not selected.get('navigates')
            # Emulate a real change handler's submitted value: use the
            # option code when present, otherwise its exact caption. The
            # final guard still requires an independent form-owned readback.
            machine_value = selected['value'] or selected['label']
            assert machine_value
        result = await super().call(*args)
        if selected is not None:
            self.selected_form_values[key] = (selected['label'], machine_value)
            if key == 'location':
                self.observed_choice = self.selected_form_values[key]
        for selected_key, (caption, machine_value) in self.selected_form_values.items():
            if self.phases.get(selected_key) != 'C2':
                continue
            for reader in self.snapshot.values():
                owner = next(c for c in reader['controls']
                             if c['selector'] == '#'+selected_key)
                owner.update(name=selected_key, value=machine_value, has_value=True)
                owner['combobox'].update(
                    selected_text=caption, displayed_text=caption,
                    selected_value=machine_value)
        return result


async def test_synthetic_reversible_picker_fill_is_not_a_selection():
    session = Session()
    await session.call('click', '#location')
    await session.call('fill', '#location', '--value', 'Approved location')
    assert session.calls == [
        ('click', '#location'),
        ('fill', '#location', '--value', 'Approved location')]
    for reader in session.snapshot.values():
        target = next(c for c in reader['controls'] if c['selector'] == '#location')
        assert target['value'] == 'Approved location' and target['has_value']
        assert target['combobox']['displayed_text'] == 'Approved location'
        assert target['combobox']['selected_value'] == ''
        assert target['combobox']['hidden_value'] == ''
    # A typed input is preparation, never final form-own selection proof.
    with pytest.raises(forms.Gate):
        forms.check_form(session.snapshot['page'],
                         {'#location': 'Approved location'}, True)


@pytest.fixture
def grants(monkeypatch):
    # The retired shape-specific picker assigned its own synthetic question.
    # The current generic PAGE reader supplies the actual observed control
    # identity; only THAT exact per-row question can authorize a write.
    initial = dom()
    answers = {}
    for key in KEYS:
        target = combo(initial, key)
        assert target['label'] == key and target['type'] == 'combobox'
        answers[key] = {
            **{slot: target[slot] for slot in (
                'selector', 'label', 'question', 'tag', 'type',
                'form_selector', 'form_action')},
            'url': initial['page']['url'],
            'value': 'Approved ' + key,
        }
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting', 'human_approved_answers': answers})
    return {key: 'Approved '+key for key in KEYS}


async def choose(session, facts, key='location', filled=None):
    return await forms._execute_combobox(
        session, await session.page(), {'action': 'combobox', 'selector': '#'+key, 'fact': key},
        facts=facts, filled=filled if filled is not None else {}, pk='example-co#1',
        job_url=URL, ctx=None, provenance=None)


def focused_final_choices(page, keys=('location',)):
    """Isolate actual form-owned choices without importing incomplete peers.

    This is only a negative/positive final-form fixture, never a replacement
    for the production final Submit guard's whole-page inventory.
    """
    final = copy.deepcopy(page)
    kept = {'#'+key for key in keys}
    for key in keys:
        owner = next(c for c in final['controls'] if c['selector'] == '#'+key)
        hidden = (owner.get('combobox') or {}).get('hidden_selector')
        if hidden:
            kept.add(hidden)
            assert sum(c['selector'] == hidden for c in final['controls']) == 1
    final['controls'] = [c for c in final['controls'] if c['selector'] in kept]
    assert len(final['controls']) == len(kept)
    final.update(inventory_verified=True, truncated=False, opaque_controls=False,
                 shadow_roots=False, unsupported_frames=[])
    return final


def focused_final_page(page, key='location'):
    return focused_final_choices(page, (key,))

async def test_four_exact_commits_and_presubmit_current_reread(grants):
    # The generic option click is preparation, not proof. Four independent
    # *current form-owned* values must agree with the approved captions.
    session, filled, receipts = FormOwnedSelection(), {}, []
    for key in KEYS:
        receipts.append(await choose(session, grants, key, filled))
    current, _ = await forms._observe_controls(session, await session.page())
    final = focused_final_choices(current, KEYS)
    forms.check_form(final, filled, True, receipts=receipts)
    forms.check_approvals('example-co#1', receipts, final)
    assert len(session.calls) == 3 * len(KEYS)
    assert len(receipts) == len(KEYS)
    for key, receipt in zip(KEYS, receipts):
        owner = next(c for c in final['controls'] if c['selector'] == '#'+key)
        assert receipt['committed_caption'] == grants[key]
        assert receipt['committed_value'] == owner['value']
        assert owner['combobox']['selected_value'] == owner['value']
    # The same click receipts with a changed *current* form value fail Submit.
    changed = copy.deepcopy(final)
    changed['controls'][0]['value'] = 'Other submitted value'
    with pytest.raises(forms.Gate, match='form-own value receiver'):
        forms.check_form(changed, filled, True, receipts=receipts)

@pytest.mark.parametrize('scenario', [
    {'caption': 'approved location'}, {'caption': 'Approved  location'},
    {'code': 'MACHINE_CODE'}, {'noReceiver': True}, {'sharedReceiver': True},
    {'danger': 'ancestor'}, {'danger': 'descendant'},
])
async def test_unsafe_choice_never_clicks_an_option(grants, scenario):
    session, filled = (FormOwnedSelection(**scenario) if scenario.get('caption')
                       else Session(**scenario)), {}
    if scenario.get('danger'):
        # A potential committing option is never reversible preparation.
        with pytest.raises(forms.Gate):
            await choose(session, grants, filled=filled)
        assert not any(c[1].endswith('-option') for c in session.calls)
        return
    receipt = await choose(session, grants, filled=filled)
    current = focused_final_page(await session.page())
    if scenario.get('caption'):
        # Benign case/whitespace variations of the approved caption are
        # reversible; a later material caption change still blocks Submit.
        assert session.observed_choice is not None
        assert receipt['committed_value'] == session.observed_choice[1]
        assert receipt['committed_caption'] == session.observed_choice[0]
        forms.check_form(current, filled, True, receipts=[receipt])
        changed = copy.deepcopy(current)
        next(c for c in changed['controls']
             if c['selector'] == '#location')['combobox']['selected_text'] = 'Different location'
        with pytest.raises(forms.Gate):
            forms.check_form(changed, filled, True, receipts=[receipt])
    else:
        # A foreign machine value or missing/non-owned receiver cannot be
        # laundered by an approved displayed caption at the final boundary.
        with pytest.raises(forms.Gate):
            forms.check_form(current, filled, True, receipts=[receipt])
    assert not any(c[0] == 'submit' for c in session.calls)


@pytest.mark.parametrize('key', ['selected_text', 'displayed_text', 'hidden_value', 'selected_value'])
async def test_each_current_readback_is_literal_and_independent(grants, key):
    if key == 'hidden_value':
        # Unlike the CSS-only closed widget, this generic fixture has an
        # actual named hidden form receiver and its independently read code.
        from tests.tools import test_browser_combobox as generic
        base = generic.page(generic.control(
            selected_text='Example Country', selected_value='EX',
            hidden_value='EX'))
        filled = {'#country': 'Example Country'}
        forms.check_form(base, filled, True)
        mutated = copy.deepcopy(base)
        mutated['controls'][0]['combobox']['hidden_value'] = 'Different code'
        with pytest.raises(forms.Gate, match='hidden form value'):
            forms.check_form(mutated, filled, True)
        changed_receiver = copy.deepcopy(base)
        changed_receiver['controls'][-1]['value'] = 'Different code'
        with pytest.raises(forms.Gate, match='hidden form value'):
            forms.check_form(changed_receiver, filled, True)
        return

    session = FormOwnedSelection()
    receipt = await choose(session, grants)
    assert session.observed_choice == (
        receipt['committed_caption'], receipt['committed_value'])
    base = focused_final_page(await session.page())
    filled = {'#location': 'Approved location'}
    forms.check_form(base, filled, True, receipts=[receipt])
    mutated = copy.deepcopy(base)
    next(c for c in mutated['controls'] if c['selector'] == '#location')['combobox'][key] = 'Different location'
    with pytest.raises(forms.Gate):
        forms.check_form(mutated, filled, True, receipts=[receipt])
    assert not any(c[:2] == ('click', '#submit') for c in session.calls)


def _current_form_owned_choice(current, receipt):
    """Give the synthetic post-click page an actual named submitted receiver."""
    owner = next(c for c in current['controls'] if c['selector'] == '#location')
    assert not (owner.get('combobox') or {}).get('hidden_selector')
    caption = receipt['committed_caption']
    owner.update(name='location', value=caption, has_value=True)
    owner['combobox'].update(
        selected_text=caption, displayed_text=caption,
        selected_value=receipt['committed_value'])
    return current


@pytest.mark.parametrize('key', ['committed_value', 'committed_caption',
                                 'hidden_selector', 'hidden_value_before',
                                 'hidden_value'])
async def test_receipt_cannot_replace_current_admission_or_click_witness(grants, key):
    session = Session()
    receipt = await choose(session, grants)
    current = _current_form_owned_choice(
        focused_final_page(await session.page()), receipt)
    filled = {'#location': 'Approved location'}
    forms.check_form(current, filled, True, receipts=[receipt])
    forged = dict(receipt)
    forged[key] = 'Approved location' if key == 'hidden_value_before' else 'forged'
    # Historical evidence must not authorize or invalidate live submitted
    # values. The approval-history audit may reject a corrupted record.
    forms.check_form(current, filled, True, receipts=[forged])
    if key != 'hidden_value_before':
        with pytest.raises(forms.Gate):
            forms.check_approvals('example-co#1', [forged], current)
    changed = copy.deepcopy(current)
    owner = next(c for c in changed['controls'] if c['selector'] == '#location')
    owner['value'] = 'Different location'
    owner['has_value'] = True
    with pytest.raises(forms.Gate, match='form-own value receiver'):
        forms.check_form(changed, filled, True, receipts=[forged])
    assert ('click', '#submit') not in session.calls


async def test_no_receipt_is_not_a_committed_selection(grants):
    session = Session()
    receipt = await choose(session, grants)
    current = _current_form_owned_choice(
        focused_final_page(await session.page()), receipt)
    filled = {'#location': 'Approved location'}
    forms.check_form(current, filled, True, receipts=[receipt])
    # Fresh approved caption and this form's named submitted value suffice.
    forms.check_form(current, filled, True, receipts=[])
    for value in ('', 'Different location'):
        changed = copy.deepcopy(current)
        owner = next(c for c in changed['controls'] if c['selector'] == '#location')
        owner['value'] = value
        owner['has_value'] = bool(value)
        with pytest.raises(forms.Gate, match='form-own value receiver'):
            forms.check_form(changed, filled, True, receipts=[])
    without_name = copy.deepcopy(current)
    next(c for c in without_name['controls']
         if c['selector'] == '#location')['name'] = ''
    with pytest.raises(forms.Gate, match='form-own value receiver'):
        forms.check_form(without_name, filled, True, receipts=[])
    assert ('click', '#submit') not in session.calls

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
async def test_anchor_drift_does_not_confer_submit_authority(grants, anchor, stage):
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

    # Old shape-anchor drift is not an automatic intermediate human Gate.
    # Nor may an old receipt mint the final form-owned submitted value.
    session, filled = Drift(), {}
    receipt = await choose(session, grants, filled=filled)
    assert receipt['action'] == 'combobox'
    assert ('click', '#submit') not in session.calls
    with pytest.raises(forms.Gate):
        forms.check_form(focused_final_page(await session.page()),
                         filled, True, receipts=[receipt])

@pytest.mark.parametrize('phase', ['C0', 'C2'])
async def test_skipped_or_failed_expansion_has_no_option_ipc(grants, phase, monkeypatch):
    if phase == 'C0':
        # This failed-expansion negative exceeded the migrated-item budget.
        # Keep *all* of its implicit Session/Drift reads on the original CLI.
        monkeypatch.setitem(globals(), 'dom', _one_shot_dom)
    class Drift(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args == ('click', '#location'):
                self.snapshot = dom(phases={'location': phase})
            return result
    session, filled = Drift(), {}
    receipt = await choose(session, grants, filled=filled)
    # Missing/late-mounted picker options produce no invented selection
    # receipt, and the final form guard refuses the incomplete control.
    assert receipt['unresolved'] and filled == {}
    assert not any(c[1].endswith('-option') for c in session.calls)
    with pytest.raises(forms.Gate):
        forms.check_form(focused_final_page(await session.page()),
                         filled, True, receipts=[receipt])


async def test_legacy_shape_loss_cannot_grant_a_final_submitted_value(grants):
    class Drift(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args == ('click', '#location'):
                self.snapshot = dom(phases={'location': 'C1'}, badGraph=True)
            return result
    session, filled = Drift(), {}
    receipt = await choose(session, grants, filled=filled)
    # A generic ARIA relationship can still allow reversible preparation
    # even when the former library-specific graph mode disappears.
    assert receipt['action'] == 'combobox'
    assert ('click', '#submit') not in session.calls
    with pytest.raises(forms.Gate):
        forms.check_form(focused_final_page(await session.page()),
                         filled, True, receipts=[receipt])

async def test_forged_plain_receipt_is_not_code_witnessed(grants):
    session = Session()
    receipt = await choose(session, grants)
    current = _current_form_owned_choice(
        focused_final_page(await session.page()), receipt)
    filled = {'#location': 'Approved location'}
    forged = dict(receipt)
    forged['committed_value'] = 'forged'
    forms.check_form(current, filled, True, receipts=[forged])
    with pytest.raises(forms.Gate, match='committed accessible choice or form value changed'):
        forms.check_approvals('example-co#1', [forged], current)
    changed = copy.deepcopy(current)
    next(c for c in changed['controls']
         if c['selector'] == '#location')['value'] = 'Different location'
    with pytest.raises(forms.Gate, match='form-own value receiver'):
        forms.check_form(changed, filled, True, receipts=[receipt])
    assert ('click', '#submit') not in session.calls

async def test_required_unapproved_bootstrap_precedes_all_widget_ipc(grants):
    session = Session()
    for snapshot in (session.snapshot['page'], session.snapshot['witness']):
        field = next(c for c in snapshot['controls'] if c['selector'] == '#additional')
        field.update(required=True, label='Unapproved final question', question='Unapproved final question')
    prepared = await forms._bootstrap(
        session, await session.page(), 'example-co#1', grants, URL)
    unapproved = [c for c in prepared['controls'] if c['selector'] == '#additional']
    assert len(unapproved) == 1
    assert unapproved[0]['required'] is True and not unapproved[0]['has_value']
    # Reversible picker work may proceed; the missing required field never
    # authorizes any write or final Submit from the observed inventory.
    assert session.calls == []
    with pytest.raises(forms.Gate):
        forms.check_form(prepared, {}, uploaded=True)


async def test_current_witness_disagreement_is_not_a_picker_phase_gate(grants):
    session = Session()
    for key in KEYS:
        await choose(session, grants, key)
    session.snapshot['witness']['controls'][0]['combobox']['selected_text'] = 'drift'
    # Bootstrap itself does not require the old C0/C1/C2 terminal gate.
    await forms._bootstrap(session, await session.page(), 'example-co#1', grants, URL)
    # But a subsequent real independent DOM read must not bless this mismatch.
    with pytest.raises(forms.Gate, match='combobox.selected_text.equal'):
        await forms._observe_controls(session, await session.page())
    assert ('click', '#submit') not in session.calls

@pytest.mark.parametrize('boundary', ['initial', 'identity', 'phase', 'downgrade', 'missing'])
async def test_unproved_closed_widget_observation_refuses_final_submit(
        boundary, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # A vanished C0/C1/C2 structural certificate no longer prevents a safe
    # observation or reversible preparation. It cannot authorize a final
    # application Submit: exercise the real raw-click guard, not a removed
    # per-step _closed_admissions gate.
    scenario = {
        'initial': {'phases': {'location': 'C1'}},
        'identity': {'drift': 'field'},
        'phase': {'phases': {'location': 'C2'}, 'badGraph': True},
        'downgrade': {'phases': {'location': 'C1'}, 'badGraph': True},
        'missing': {},
    }[boundary]
    session = Session(**scenario)
    if boundary == 'missing':
        for observation in session.snapshot.values():
            observation['controls'] = [
                c for c in observation['controls']
                if c['selector'] != '#location'
            ]
    assert session.calls == []
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path)
    assert session.calls == []


@pytest.mark.parametrize('scenario', ['conflict', 'shared', 'foreign', 'nestedList', 'duplicate'])
def test_popup_relationship_itself_refuses_ambiguous_ownership(scenario):
    snapshot = dom(phases={'location': 'C1', 'school': 'C1'}, **{scenario: True})
    assert combo(snapshot)['combobox']['linked'] is False
    assert combo(snapshot)['combobox']['options'] == []


@pytest.mark.parametrize('scenario', ['foreign', 'nestedList'])
async def test_foreign_or_nested_listbox_never_authorizes_final_submit(
        scenario, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # r06 §6: a rendered ARIA option belongs to its current listbox. A
    # foreign native form or a nested listbox is another owner, even if
    # aria-controls and option captions otherwise match an approved answer.
    snapshot = dom(phases={'location': 'C1'}, **{scenario: True})
    page, fixed = snapshot['page'], snapshot['witness']
    owner = combo(snapshot, 'location')
    witness_owner = next(c for c in fixed['controls']
                         if c['selector'] == owner['selector'])
    assert owner['combobox'] == witness_owner['combobox']
    assert owner['combobox']['linked'] is False
    assert owner['combobox']['options'] == []
    assert page['opaque_controls'] and not page['inventory_verified']
    await assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path)


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
async def test_option_machine_attributes_never_replace_live_submitted_value(grants, attrs):
    # An opaque machine code need not equal the approved displayed caption.
    # The actual form-owned current value MUST equal the selected option code.
    session, filled = FormOwnedSelection(optionAttrs=attrs), {}
    receipt = await choose(session, grants, filled=filled)
    assert receipt['action'] == 'combobox'
    assert session.observed_choice is not None
    final = focused_final_page(await session.page())
    forms.check_form(final, filled, True, receipts=[receipt])
    changed = copy.deepcopy(final)
    next(c for c in changed['controls'] if c['selector'] == '#location')['value'] = (
        'Different submitted machine code')
    with pytest.raises(forms.Gate, match='form-own value receiver'):
        forms.check_form(changed, filled, True, receipts=[receipt])
    assert ('click', '#submit') not in session.calls

async def test_attribute_less_option_commits_exact_caption_and_receiver(grants):
    session, filled = FormOwnedSelection(optionAttrs={}), {}
    receipt = await choose(session, grants, filled=filled)
    assert receipt['committed_caption'] == receipt['committed_value'] == grants['location']
    assert receipt['hidden_value'] == ''  # No invented hidden input commitment.
    assert session.observed_choice == (grants['location'], grants['location'])
    assert session.calls == [
        ('click', '#location'), ('fill', '#location', '--value', 'Approved location'),
        ('click', '#location-option')]
    final, _ = await forms._observe_controls(session, await session.page())
    focused = focused_final_page(final)
    forms.check_form(focused, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], focused)

def test_cleared_optional_phone_cannot_evade_presubmit():
    page = dom()['page']
    phone = next(c for c in page['controls'] if c['selector'] == '#phone')
    phone.update(value='+12025550123', has_value=False, required=False)
    # A partially inspectable page must fail closed before individual values.
    assert page['inventory_verified'] is False and page['opaque_controls']
    with pytest.raises(forms.Gate, match='form inventory is incomplete'):
        forms.check_form(page, {'#phone': '+12025550123'}, True)

    # Independently prove the phone-specific negative with a complete current
    # native form. Opaque unrelated widgets cannot mask this checked value.
    complete = {
        'url': URL, 'controls': [copy.deepcopy(phone)],
        'inventory_verified': True, 'truncated': False,
        'opaque_controls': False, 'shadow_roots': False,
        'unsupported_frames': [],
    }
    with pytest.raises(forms.Gate, match='current native value and its readback disagree'):
        forms.check_form(complete, {'#phone': '+12025550123'}, True)


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
    assert session.calls == [
        ('click', '#location'), ('fill', '#location', '--value', 'Approved location'),
        ('click', '#location-option')]


async def test_direct_raw_submit_refuses_changed_current_choice(
        monkeypatch, tmp_path, grants):
    """One production raw application click, no fake model-level submit gate."""
    from tools import browser_skill as bsk

    session, filled = FormOwnedSelection(), {}
    receipt = await choose(session, grants, filled=filled)
    current = copy.deepcopy(await session.page())
    current['controls'] = [c for c in current['controls']
                           if c['selector'] in {'#location', '#resume', '#submit'}]
    current.update(inventory_verified=True, truncated=False, opaque_controls=False,
                   shadow_roots=False, unsupported_frames=[])
    choice = next(c for c in current['controls'] if c['selector'] == '#location')
    choice['combobox']['selected_text'] = 'Different displayed location'

    pdf = tmp_path / 'Test User Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\nSynthetic row PDF only\n')
    next(c for c in current['controls']
         if c['selector'] == '#resume')['files'] = [pdf.name]
    row = {'pk': 'example-co#1', 'company': 'example-co', 'jd_url': URL,
           'job_id': '1', 'title': 'Synthetic', 'ats': 'custom',
           'status': 'submitting', 'possible_submission': False}
    raw_commands, started = [], []
    controller = forms.ApplicationIPCController(
        row['pk'], row['company'], URL, str(pdf), grants, filled,
        [receipt], {}, lambda: started.append(True))
    monkeypatch.setattr(controller, '_row', lambda: row)
    monkeypatch.setattr(controller, '_fresh_preparation', lambda _url: None)
    identity = (pdf.stat().st_size, controller.expected_digest)
    monkeypatch.setattr(controller, '_row_pdf', lambda: identity)
    controller.upload_identity = identity
    controller.upload_completed = True

    async def reread(_session):
        return copy.deepcopy(current)

    async def command(*args, **kwargs):
        raw_commands.append((args, kwargs))
        return {}

    monkeypatch.setattr(controller, '_page', reread)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'hold_possible_submission',
                        lambda *a, **kw: pytest.fail('Unapproved choice reached Submit hold'))
    monkeypatch.setattr(bsk, 'command', command)
    raw = bsk.Session('apply')
    raw.id = 'synthetic-session'
    raw.bind_application_controller(controller)
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        await raw.application_effect('click', '#submit')
    assert raw_commands == [] and started == []
    assert controller.committed is False
    assert ('click', '#submit') not in session.calls

async def test_never_committed_component_refuses_final_submit(
        monkeypatch, tmp_path, grants):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    class NeverCommitted(Session):
        async def call(self, *args):
            before = self.snapshot
            result = await super().call(*args)
            if args == ('click', '#location-option'):
                self.snapshot = before
            return result

    session, filled = NeverCommitted(), {}
    receipt = await choose(session, grants, filled=filled)
    assert session.calls == [
        ('click', '#location'),
        ('fill', '#location', '--value', 'Approved location'),
        ('click', '#location-option'),
    ]
    assert receipt['committed_value'] == 'Approved location'
    # The reversible click returned, but this DOM never committed a selected
    # caption/receiver. Isolate this real field from unrelated required fields
    # to prove the exact final-form failure, not just an incomplete page.
    final = copy.deepcopy(session.snapshot['page'])
    field = combo({'page': final})
    assert not field['combobox']['selected_text']
    keep = {'#location', '#resume', '#submit'}
    if field['combobox'].get('hidden_selector'):
        keep.add(field['combobox']['hidden_selector'])
    final['controls'] = [c for c in final['controls'] if c['selector'] in keep]
    assert {'#location', '#resume', '#submit'} <= {
        c['selector'] for c in final['controls']}
    final.update(inventory_verified=True, truncated=False,
                 opaque_controls=False, shadow_roots=False,
                 unsupported_frames=[])
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(final, filled, True, receipts=[receipt])
    # The production raw-click seam independently refuses this minimal
    # incomplete selection with zero Submit IPC and no irreversible hold.
    await assert_unapproved_submit_is_refused(
        {'page': final}, monkeypatch, tmp_path, require_incomplete=False)
    assert not any(call[0] == 'submit' for call in session.calls)

@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
async def test_required_proxies_never_count_as_submitted_answers(
        phase, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # Missing or mounted native required proxies must be inventoried, not
    # silently promoted to the former C0/C1/C2 immutable identity. Regardless
    # of old picker presentation, no current exact approved form values exist.
    snapshot = dom(fullPhone=True, requiredProxy=True,
                   phases=dict.fromkeys((*KEYS, 'country'), phase))
    page, witness = snapshot['page'], snapshot['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    for key in ('location', 'country'):
        observed = combo(snapshot, key)
        independent = next(c for c in witness['controls']
                           if c['selector'] == observed['selector'])
        assert observed['combobox'] == independent['combobox']
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)


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
async def test_required_proxy_mutation_denies_real_final_submit(
        key, mutation, phase, monkeypatch, tmp_path):
    """A malformed required proxy must never turn into a submitted answer.

    The generic reader no longer issues C0/C1 quarantine certificates.
    Exercise the production raw-click controller rather than resurrecting
    that deleted per-step proof. Every matrix case must deny before the hold.
    """
    from tools import browser_skill as bsk

    snapshot = dom(fullPhone=True, requiredProxy=True, phases={key: phase},
                   proxyMutation={'key': key, **mutation})
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    # The generic reader may retain a real ARIA listbox during C1 even though
    # the malformed required proxy makes the overall inventory untrustworthy.
    # Rendered options are not submitted values or an authorization to click.
    page_graph = next(c['combobox'] for c in snapshot['page']['controls']
                      if c['selector'] == '#'+key)
    witness_graph = next(c['combobox'] for c in snapshot['witness']['controls']
                         if c['selector'] == '#'+key)
    assert page_graph == witness_graph
    for graph in (page_graph, witness_graph):
        assert graph['selected_text'] == graph['selected_value'] == ''
        assert graph['hidden_value'] == ''
        if phase == 'C0':
            assert graph['linked'] is False
            assert graph['link_id'] == graph['listbox_selector'] == ''
            assert graph['options'] == []
        else:
            assert graph['linked'] is True
            assert graph['link_id'] == ('country-options' if key == 'country' else key+'-list')
            assert graph['listbox_selector'] == '#'+graph['link_id']
            assert len(graph['options']) == 1
            option = graph['options'][0]
            assert option['selector'] == '#'+key+'-option'
            assert option['label'] == 'Approved '+key
            assert option['unique'] is True and option['selected'] is False
            assert option['safe'] is True and option['submit'] is False

    # Supply a real synthetic PDF and otherwise matching job identity, so the
    # final guard reaches the generic form check rather than refusing for an
    # unrelated row, missing attachment, or bad destination.
    pdf = tmp_path / 'Test User Resume.pdf'
    pdf_bytes = b'%PDF-1.4\nSynthetic row PDF only\n'
    pdf.write_bytes(pdf_bytes)
    page = copy.deepcopy(snapshot['page'])
    file_inputs = [c for c in page['controls'] if c['selector'] == '#resume']
    submitters = [c for c in page['controls'] if c['selector'] == '#submit']
    assert len(file_inputs) == len(submitters) == 1
    assert file_inputs[0]['type'] == 'file'
    file_inputs[0]['files'] = [pdf.name]
    assert page['url'] == URL and page['title'] == 'Synthetic'
    assert submitters[0]['type'] == 'submit'
    approved = {name: 'Approved ' + name for name in (*KEYS, 'country')}
    row = {'pk': 'example-co#1', 'company': 'example-co', 'job_id': '1',
           'jd_url': URL, 'title': 'Synthetic', 'ats': 'custom',
           'status': 'submitting', 'human_approved_answers': {
               name: {'value': value} for name, value in approved.items()}}
    began_click, raw_commands, reached_form = [], [], []
    controller = forms.ApplicationIPCController(
        row['pk'], row['company'], URL, str(pdf), approved,
        {'#' + name: value for name, value in approved.items()}, [],
        {}, lambda: began_click.append(True))
    monkeypatch.setattr(controller, '_row', lambda: row)

    def verify_row_pdf():
        if pdf.read_bytes() != pdf_bytes:
            raise forms.Gate('The synthetic row PDF bytes changed.')
        return pdf.stat().st_size, controller.expected_digest

    monkeypatch.setattr(controller, '_row_pdf', verify_row_pdf)
    controller.upload_identity = verify_row_pdf()
    controller.upload_completed = True

    def verify_dispatch(pk, staged_path, **kwargs):
        assert pk == row['pk'] and staged_path == str(pdf.resolve())
        assert kwargs == {}

    def verify_preparation(origin):
        assert origin == URL

    async def reread(_session):
        return copy.deepcopy(page)

    async def fake_command(*args, **kwargs):
        raw_commands.append((args, kwargs))
        return {}

    original_check_form = forms.check_form

    def real_form_check(*args, **kwargs):
        reached_form.append(True)
        return original_check_form(*args, **kwargs)

    monkeypatch.setattr(forms, 'check_dispatch', verify_dispatch)
    monkeypatch.setattr(controller, '_fresh_preparation', verify_preparation)
    monkeypatch.setattr(controller, '_page', reread)
    monkeypatch.setattr(forms, 'check_form', real_form_check)
    monkeypatch.setattr(
        forms, 'hold_possible_submission',
        lambda *_a, **_kw: pytest.fail('Malformed proxy reached the Submit hold'))
    monkeypatch.setattr(bsk, 'command', fake_command)
    session = bsk.Session('apply')
    session.id = 'synthetic-session'
    session.bind_application_controller(controller)

    with pytest.raises(forms.Gate, match='form inventory is incomplete'):
        await session.application_effect('click', '#submit')
    assert reached_form == [True]  # Same production guard and form reader.
    assert raw_commands == [] and began_click == []
    assert controller.committed is False and controller.history == []
    assert controller.upload_completed is True  # The PDF was not the refusal.
    assert controller.filled == {'#' + name: value for name, value in approved.items()}


async def test_proxy_disappearance_preserves_real_commit_and_approvals(grants):
    # A required synthetic proxy disappearing after a real option selection
    # is not itself authority. The final current named input and caption are.
    session, filled = FormOwnedSelection(requiredProxy=True), {}
    receipt = await choose(session, grants, filled=filled)
    current, _ = await forms._observe_controls(session, await session.page())
    final = focused_final_page(current)
    owner = next(c for c in final['controls'] if c['selector'] == '#location')
    assert owner['value'] == owner['combobox']['selected_value']
    forms.check_form(final, filled, True, receipts=[receipt])
    forms.check_approvals('example-co#1', [receipt], final)
    assert session.calls == [
        ('click', '#location'), ('fill', '#location', '--value', grants['location']),
        ('click', '#location-option')]

@pytest.mark.parametrize('key', ['location', 'country'])
def test_empty_unnamed_native_proxy_is_not_an_independent_approval_question(key):
    # Requiredness alone does not make an unlabelled, unnamed, empty
    # validation input a separately grantable answer. It stays in the final
    # inventory: required-field validation must still fail at Submit.
    page = dom(fullPhone=True, requiredProxy=True, proxyKeys=[key])['page']
    target = combo({'page': page}, key)
    proxies = [c for c in page['controls']
               if c.get('type') == 'text' and c.get('required')
               and not c.get('label') and not c.get('name')]
    assert len(proxies) == 1
    proxy = proxies[0]
    assert proxy['in_form'] is True and not proxy['value']
    assert forms._could_receive_an_answer(proxy) is False
    assert forms._key(page, target) == ('Country' if key == 'country' else key)
    with pytest.raises(forms.Gate, match='Required field is empty'):
        forms.check_form(
            {'controls': [proxy], 'inventory_verified': True}, {}, True)


@pytest.mark.parametrize('field, value', [
    ('name', 'synthetic_answer'), ('has_value', True),
    ('value', 'Unapproved synthetic value'),
])
def test_native_proxy_becoming_an_answer_restores_shared_question_collision(
        field, value):
    # A mutated input that acquires a name or value is no longer a blank
    # non-answer. Do not let disambiguation borrow the Phone native anchor.
    page = dom(fullPhone=True, requiredProxy=True,
               proxyKeys=['country'])['page']
    country = combo({'page': page}, 'country')
    proxy, = [c for c in page['controls']
              if c.get('type') == 'text' and c.get('required')
              and not c.get('label') and not c.get('name')]
    proxy[field] = value
    assert forms._could_receive_an_answer(proxy)
    with pytest.raises(forms.Gate, match='More than one control uses'):
        forms._key(page, country)


@pytest.mark.parametrize('key', ['location', 'country'])
async def test_mounted_proxy_cannot_authorize_final_raw_submit(
        key, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    # The retired closed-commit certificate no longer exists. A C2 widget
    # with a re-mounted required proxy still cannot authorize Submit.
    snapshot = dom(fullPhone=True, requiredProxy=True, phases={key: 'C2'},
                   proxyMutation={'key': key, 'mounted': True})
    assert snapshot['page']['opaque_controls'] is True
    assert snapshot['page']['inventory_verified'] is False
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)

@pytest.mark.parametrize('key', ['location', 'country'])
def test_untouched_required_proxy_and_other_required_field_cannot_submit(key):
    snapshot = dom(fullPhone=True, requiredProxy=True)
    target = combo(snapshot, key)
    assert target['type'] == 'combobox'
    # Neither a mounted proxy nor an old C0/C1 certificate is a submitted
    # value. Focus just this required field to test its final refusal.
    with pytest.raises(forms.Gate):
        forms.check_form(focused_final_page(snapshot['page'], key), {}, True)
    outside = dom(fullPhone=True, requiredProxy=True,
                  proxyMutation={'key': key, 'parent': 'outside'})
    ordinary = [c for c in outside['page']['controls']
                if c.get('required') and c['type'] == 'text' and not c['has_value']]
    # The generic reader retains all five required text inputs, not only
    # the one whose proxy was moved. Each empty native receiver independently
    # blocks final Submit; an old proxy-specific inventory exemption cannot
    # hide the four peers.
    assert len(ordinary) == 5
    assert len({c['selector'] for c in ordinary}) == len(ordinary)
    for field in ordinary:
        assert field['tag'] == 'input' and field['in_form'] is True
        with pytest.raises(forms.Gate, match='Required field is empty'):
            forms.check_form(
                {'controls': [field], 'inventory_verified': True}, {}, True)

def _diagnostic_paths(page, final_segment):
    # The fixture has unrelated unmatched ARIA picker parts, which remain
    # opaque. Only new diagnostic canvases/custom nodes count for this check.
    return [path for path in page['opaque_locators']
            if path.split(' > ')[-1].startswith(final_segment)]


@pytest.mark.parametrize('scenario,segment', [
    ({'custom': True}, '*:nth-child('),
    ({'depth': 14}, 'canvas:nth-of-type('),
    ({'count': 2}, 'canvas:nth-of-type('),
    ({'count': 8}, 'canvas:nth-of-type('),
])
def test_diagnostic_locators_are_unique_and_never_page_identifiers(scenario, segment):
    baseline = dom()['page']
    p = dom(diagnostics=scenario)['page']
    paths = _diagnostic_paths(p, segment)
    count = scenario.get('count', 1)
    assert p['opaque_count'] == baseline['opaque_count'] + count
    assert len(p['opaque_locators']) == p['opaque_count']
    assert len(paths) == len(set(paths)) == count
    assert all(forms._safe_diagnostic(path) == path for path in paths)
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(p)
    reason = str(caught.value)
    assert 'opaque_controls' in reason
    assert 'opaque_count=' + str(p['opaque_count']) in reason
    assert reason.count('control=') == min(3, p['opaque_count'])
    assert 'test-user' not in reason and '@' not in reason
    assert 'private-' not in reason


def test_repeated_deep_opaque_nodes_remain_fail_closed():
    p = dom(diagnostics={'count': 2, 'depth': 14})['page']
    assert p['opaque_count'] == dom()['page']['opaque_count'] + 2
    assert p['opaque_controls'] is True and p['inventory_verified'] is False
    paths = _diagnostic_paths(p, 'canvas:nth-of-type(')
    assert len(paths) == len(set(paths)) == 2
    with pytest.raises(forms.Gate, match='opaque_count=' + str(p['opaque_count'])):
        forms._inventory_ok(p)


@pytest.mark.parametrize('depth,count', [(14, 2), (14, 3), (30, 2), (30, 8)])
def test_repeated_deep_opaque_nodes_have_distinct_safe_locators(depth, count):
    p = dom(diagnostics={'count': count, 'depth': depth})['page']
    assert p['opaque_count'] == dom()['page']['opaque_count'] + count
    assert p['opaque_controls'] is True and p['inventory_verified'] is False
    paths = _diagnostic_paths(p, 'canvas:nth-of-type(')
    assert len(paths) == len(set(paths)) == count
    assert all(forms._safe_diagnostic(path) == path
               and path != 'unavailable' for path in paths)
    assert all(path.count(' > ') >= depth for path in paths)
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(p)
    detail = str(caught.value)
    assert 'opaque_count=' + str(p['opaque_count']) in detail
    assert detail.count('control=') == min(3, p['opaque_count'])
    assert 'test-user' not in detail and 'example-co' not in detail and '@' not in detail

def test_safe_diagnostic_accepts_only_bounded_structural_segments():
    # The browser and Gate share the same per-locator limits. Over-limit
    # untrusted diagnostics are still redacted, not echoed into a Gate.
    from tools.browser_skill_dom import _DIAGNOSTIC_MAX_SEGMENTS, _DIAGNOSTIC_MAX_CHARS
    path = ' > '.join(['div:nth-of-type(1)'] * (_DIAGNOSTIC_MAX_SEGMENTS - 1)
                      + ['canvas:nth-of-type(1)'])
    assert len(path) < _DIAGNOSTIC_MAX_CHARS
    assert forms._safe_diagnostic(path) == path
    over_limit = path + ' > div:nth-of-type(1)'
    assert len(over_limit) < _DIAGNOSTIC_MAX_CHARS
    assert forms._safe_diagnostic(over_limit) == 'unavailable'
    too_long = ' > '.join(
        ['div:nth-of-type(' + '1234567890' * 4 + ')'] * _DIAGNOSTIC_MAX_SEGMENTS)
    assert len(too_long) > _DIAGNOSTIC_MAX_CHARS
    assert forms._safe_diagnostic(too_long) == 'unavailable'


@pytest.mark.parametrize('unsafe', [
    '#test-user@example-co.test',
    'div:nth-of-type(1) > input[data-answer="synthetic-secret"]',
    'div:nth-of-type(1) > div.synthetic-secret',
    'canvas:nth-of-type(0)',
    'canvas:nth-of-type(01)',
    'input:nth-child(1)',
    'div:nth-of-type(1) > canvas:nth-of-type(1),body:nth-of-type(1)',
    'div:nth-of-type(1)\ncanvas:nth-of-type(1)',
    '',
])
def test_safe_diagnostic_rejects_untrusted_or_nonstructural_paths(unsafe):
    assert forms._safe_diagnostic(unsafe) == 'unavailable'
    page = {'inventory_verified': False, 'opaque_controls': True,
            'opaque_count': 1, 'opaque_locators': [unsafe]}
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(page)
    assert 'control=unavailable' in str(caught.value)
    assert 'synthetic-secret' not in str(caught.value)
    assert '@example-co.test' not in str(caught.value)


@pytest.mark.parametrize('count', [2, 8])
def test_depth_thirty_opaque_nodes_keep_all_reader_locators_and_capped_gate(count):
    page = dom(diagnostics={'count': count, 'depth': 30})['page']
    assert page['opaque_count'] == dom()['page']['opaque_count'] + count
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    paths = _diagnostic_paths(page, 'canvas:nth-of-type(')
    assert len(paths) == len(set(paths)) == count
    assert all(forms._safe_diagnostic(path) == path and path != 'unavailable'
               for path in paths)
    with pytest.raises(forms.Gate, match='opaque_count=' + str(page['opaque_count'])) as caught:
        forms._inventory_ok(page)
    message = str(caught.value)
    assert message.count('control=') == min(3, page['opaque_count'])
    assert 'test-user' not in message and 'example-co' not in message and '@' not in message

@pytest.mark.parametrize('fresh', [False, True])
@pytest.mark.parametrize('flags', [
    {'opaque_controls': True, 'opaque_count': 1, 'opaque_locators': ['#test-user@example-co.test']},
    {'unsupported_frames': ['https://test-user@example-co.test/private']},
    {'shadow_roots': True}, {'truncated': True}, {'inventory_verified': False},
    {'opaque_controls': True, 'unsupported_frames': ['private'], 'shadow_roots': True, 'truncated': True},
])
async def test_country_cannot_hide_incomplete_final_form(
        fresh, flags, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    session = Session(fullPhone=True)
    original = await session.page()
    unsafe = {**copy.deepcopy(original), **flags}
    if fresh:
        async def current():
            return unsafe
        session.page = current
        # Different structural flags alone do not require an old interim
        # Gate. The final Submit guard checks the current full inventory.
        try:
            await forms._observe_controls(session, original)
        except forms.Gate as exc:
            assert 'test-user' not in str(exc) and '@' not in str(exc)
    with pytest.raises(forms.Gate) as caught:
        forms._inventory_ok(unsafe)
    reason = str(caught.value)
    for flag in flags:
        if flag in {'opaque_controls', 'unsupported_frames', 'shadow_roots', 'truncated'}:
            assert flag in reason
    assert 'receiverless country commitment' not in reason
    assert 'test-user' not in reason and '@' not in reason and 'https:' not in reason
    snapshot = copy.deepcopy(session.snapshot)
    snapshot['page'] = unsafe
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert session.calls == []

@pytest.mark.parametrize('kind,predicate', [
    ('empty', 'combobox.link_id.required_nonempty'), ('unequal', 'combobox.link_id.equal'),
    ('timeout', 'unavailable'), ('cancel', 'unavailable'),
])
async def test_witness_failure_names_class_and_first_predicate_without_values(
        kind, predicate, monkeypatch, tmp_path):
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
    if kind == 'empty':
        # An absent per-step link certificate is not permission to Submit.
        # Reject through the production final raw-click guard instead.
        from tests.tools.test_browser_greenhouse_parity import (
            assert_unapproved_submit_is_refused,
        )
        await forms._observe_controls(session, await session.page())
        await assert_unapproved_submit_is_refused(
            session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
        assert session.calls == []
        return
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
async def test_real_observation_preserves_genuine_gate_and_question(monkeypatch, boundary):
    # The retired _observe_closed_admissions does not exist. A transport
    # Gate must preserve the exact inspectable handoff question.
    session = Session(fullPhone=True)
    sentinel = forms.Gate('observation ' + boundary,
                          form_question={'synthetic': boundary})
    async def refused(*args, **kwargs):
        raise sentinel
    monkeypatch.setattr(session, 'call', refused)
    with pytest.raises(forms.Gate) as caught:
        await forms._observe_controls(session, await session.page())
    assert caught.value is sentinel
    assert caught.value.form_question == {'synthetic': boundary}
    assert session.calls == []

@pytest.mark.parametrize('cause', ['inventory', 'approval', 'destination', 'phase timeout'])
async def test_country_wrapper_preserves_noncommit_Gates(monkeypatch, cause):
    session = Session(fullPhone=True)
    sentinel = forms.Gate('safe synthetic ' + cause,
                          form_question={'synthetic': cause})
    async def refused(*args, **kwargs):
        raise sentinel
    # There is no retired shape-specific picker wrapper. The generic
    # independently witnessed observation must propagate genuine pre-click
    # refusals without replacing or silently losing the form question.
    monkeypatch.setattr(forms, '_observe_controls', refused)
    with pytest.raises(forms.Gate) as caught:
        await forms._execute_combobox(session, await session.page(),
            {'action': 'combobox', 'selector': '#country'}, facts={}, filled={},
            pk='example-co#1', job_url=URL, ctx=None, provenance=None)
    assert caught.value is sentinel
    assert caught.value.form_question == {'synthetic': cause}
    assert session.calls == []

@pytest.mark.parametrize('scenario', [
    'badGraph', 'extraInput', 'noAutocomplete', 'wrongLabel',
    'duplicateLabel', 'explicitOutside', 'invalidExpanded',
    'displayQuery', 'multipleReceiver'])
async def test_former_owner_signatures_cannot_authorize_final_raw_submit(
        scenario, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = dom(requiredProxy=True, phases={'location': 'C1'},
                   **{scenario: True})
    assert snapshot['page']['opaque_controls'] is True
    # The former library-specific quarantine is not another per-step gate.
    # Accessible ARIA options may render, but unsubmitted choices remain
    # insufficient at the irreversible raw-click guard.
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)

async def test_generic_aria_link_without_proxy_signature_never_grants_submit(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )
    snapshot = dom(phases={'location': 'C1'}, noAutocomplete=True)
    for reader in snapshot.values():
        graph = next(c['combobox'] for c in reader['controls']
                     if c['selector'] == '#location')
        assert graph.get('quarantined') is not True
        # Missing a removed widget-specific proxy signature does not cancel
        # an otherwise present ARIA relationship or prove its form-owned value.
        assert isinstance(graph['linked'], bool)
    await assert_unapproved_submit_is_refused(
        snapshot, monkeypatch, tmp_path, require_incomplete=False)
