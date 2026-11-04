"""Issue #324: local-only observations never copy synthetic answers or labels."""
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_absent_decision import OPTION, name_combo
from tests.tools.test_browser_apply_decision_regression import _AfterUploadHarness, page


@pytest.mark.parametrize('mode,reason', [
    ('disabled', 'disabled'), ('option-disabled', 'disabled'),
    ('visible', 'visible-false'), ('unique', 'unique-false'),
    ('safe', 'safe-not-true'), ('safe-missing', 'safe-not-true'),
    ('submit', 'option-submit'),
    ('navigates', 'option-navigates'),
    ('commits_application', 'option-commits-application'),
    ('parent-submit', 'parent-is-submission'),
    ('parent-native-submit', 'parent-native-submit'),
    ('parent-type', 'non-combobox-parent'),
    ('parent-private-type', 'non-combobox-parent'),
    ('no-fact-empty-label', 'empty-answer'),
    ('no-candidate', 'option-not-matching-answer'),
    ('duplicate-answer', 'answer-exact-ambiguous'),
    ('caption-alias', 'caption-not-unique'),
    ('duplicate-parent', 'nested-count'), ('duplicate-option', 'nested-count'),
    ('absent-selector', 'nested-count'),
])
def test_refusals_preserve_action_and_log_one_value_free_reason(mode, reason, caplog):
    target = name_combo()
    option = target['combobox']['options'][1]
    option.update(label='PRIVATE-LABEL', value='PRIVATE-VALUE')
    action = {'action': 'click', 'selector': OPTION, 'fact': 'PRIVATE-FACT',
              'value': 'PRIVATE-MODEL-ANSWER'}
    facts = {'PRIVATE-FACT': 'PRIVATE-LABEL'}
    controls = [target]
    if mode == 'disabled':
        target['disabled'] = True
    elif mode == 'option-disabled':
        option['disabled'] = True
    elif mode in {'visible', 'unique', 'safe'}:
        option[mode] = False
    elif mode == 'safe-missing':
        option.pop('safe')
    elif mode in {'submit', 'navigates', 'commits_application'}:
        option[mode] = True
    elif mode == 'parent-submit':
        target['submit'] = True
    elif mode == 'parent-native-submit':
        target.update(tag='button', native_type='submit', submit=True, label='Next',
                      native_form_owner_selector=target['form_selector'])
    elif mode.startswith('parent-'):
        target['type'] = 'PRIVATE-TYPE' if mode == 'parent-private-type' else 'button'
    elif mode == 'no-fact-empty-label':
        action.pop('fact')
        option['label'] = ''
    elif mode == 'no-candidate':
        facts['PRIVATE-FACT'] = 'PRIVATE-OTHER-ANSWER'
    elif mode == 'duplicate-answer':
        target['combobox']['options'].append(
            dict(option, selector='#twin', label='PRIVATE-OTHER-LABEL', value=option['label']))
    elif mode == 'caption-alias':
        facts['PRIVATE-FACT'] = 'PRIVATE-FACT-VALUE'
        option['value'] = 'PRIVATE-FACT-VALUE'
        target['combobox']['options'].append(
            dict(option, selector='#twin', label='PRIVATE-OTHER-LABEL', value=option['label']))
    elif mode == 'duplicate-parent':
        controls.append(dict(deepcopy(target), selector='#other'))
    elif mode == 'duplicate-option':
        target['combobox']['options'].append(deepcopy(option))
    elif mode == 'absent-selector':
        action['selector'] = '#missing'
    before = deepcopy((controls, action, facts))
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        assert forms._resolve_decision_option(page(*controls), action, facts) is action
    assert (controls, action, facts) == before
    messages = [r.message for r in caplog.records if 'nested-option refusal' in r.message]
    assert len(messages) == 1
    assert f'reason={reason} ' in messages[0]
    assert f"fact_present={'fact' in action}" in messages[0]
    assert 'parent_type=' in messages[0]
    assert 'PRIVATE-' not in caplog.text


def test_success_and_top_level_actions_do_not_log_refusal(caplog):
    target = name_combo()
    action = {'action': 'click', 'selector': OPTION, 'fact': 'Name'}
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        assert forms._resolve_decision_option(page(target), action, {'Name': 'Test User'}) == {
            'action': 'combobox', 'selector': '#name-combo', 'value': 'Test User',
            'option_selector': OPTION}
        direct = dict(action, selector='#name-combo')
        assert forms._resolve_decision_option(page(target), direct, {}) is direct
    assert not caplog.records


@pytest.mark.parametrize('mode,found,committed,opened', [
    ('committed', 'yes', 'yes', 'yes'),
    ('not-committed', 'yes', 'no', 'yes'),
    ('empty-selection', 'yes', 'unknown', 'yes'),
    ('display-only-selection', 'yes', 'unknown', 'yes'),
    ('no-option', 'no', 'unknown', 'yes'),
    ('no-readback', 'yes', 'unknown', 'unknown'),
    ('missing-control', 'yes', 'unknown', 'unknown'),
    ('ambiguous-control', 'yes', 'unknown', 'unknown'),
    ('missing-selection', 'yes', 'unknown', 'yes'),
    ('empty-options', 'yes', 'yes', 'unknown'),
])
def test_outcomes_use_only_observed_state(mode, found, committed, opened, caplog):
    target = name_combo()
    option = target['combobox']['options'][1]
    option.update(label='PRIVATE-LABEL', value='PRIVATE-VALUE')
    target['combobox'].update(selected_text='PRIVATE-LABEL', selected_value='PRIVATE-VALUE')
    evidence = {'performed': True, 'selected_option': deepcopy(option)}
    after = page(target)
    if mode == 'not-committed':
        target['combobox'].update(selected_text='PRIVATE-OTHER', selected_value='PRIVATE-OTHER')
    elif mode in {'empty-selection', 'display-only-selection'}:
        target['combobox'].update(selected_text='', selected_value='')
        if mode == 'display-only-selection':
            target['combobox']['displayed_text'] = 'PRIVATE-LABEL'
            target['value'] = 'PRIVATE-LABEL'
    elif mode == 'no-option':
        evidence = {'performed': False, 'selected_option': None}
    elif mode == 'no-readback':
        after = None
    elif mode == 'missing-control':
        after = page()
    elif mode == 'ambiguous-control':
        after = page(target, deepcopy(target))
    elif mode == 'missing-selection':
        target['combobox'].pop('selected_text')
    elif mode == 'empty-options':
        target['combobox']['options'] = []
    before = deepcopy((after, evidence))
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        forms._log_combobox_outcome(after, '#name-combo', evidence)
    assert (after, evidence) == before
    assert [r.message for r in caplog.records] == [
        f'combobox outcome option_found={found} committed={committed} listbox_still_open={opened} '
        'match=none options_seen=0 segment_candidates=0']
    assert 'PRIVATE-' not in caplog.text


@pytest.mark.asyncio
async def test_existing_loop_readback_emits_one_outcome_without_extra_calls(tmp_path, monkeypatch, caplog):
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [
        {'action': 'click', 'selector': OPTION, 'fact': 'Name'}, {'action': 'finish'},
    ])
    target = name_combo()
    target['form_action'] = harness.current['url']
    harness.current['controls'].append(target)
    raw_call = forms.bsk.Session.call

    async def call(self, *args, **kwargs):
        if args == ('click', OPTION):
            harness.events.append('click')
            harness.calls.append(args)
            return {}
        return await raw_call(self, *args, **kwargs)

    monkeypatch.setattr(forms.bsk.Session, 'call', call)
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        result = await harness.run({'Name': 'Test User'})
    assert result['status'] == 'gate'
    outcomes = [r.message for r in caplog.records if r.message.startswith('combobox outcome')]
    assert outcomes == [
        'combobox outcome option_found=yes committed=unknown listbox_still_open=yes '
        'match=exact options_seen=2 segment_candidates=0']
    assert [c for c in harness.calls if c[0] == 'click'] == [('click', OPTION)]
    assert all('Test User' not in message for message in outcomes)
