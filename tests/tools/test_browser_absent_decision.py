"""Issue #322: nested selectors and stale decisions, entirely offline/synthetic."""
import json
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_apply_decision_regression import (
    MemorySession, _AfterUploadHarness, act, name_combo as _name_combo, page,
)
from tests.tools.reader_node_harness import reader_output
from tests.tools.test_browser_greenhouse_fixture import Tree


OPTION = '#name-right-option'


def name_combo():
    target = _name_combo()
    for option in target['combobox']['options']:
        option['safe'] = True
    return target


@pytest.mark.asyncio
@pytest.mark.parametrize('verb', ['click', 'choose', 'combobox'])
async def test_nested_option_uses_parent_combobox_execution(verb):
    form = page(name_combo())
    action = forms._resolve_decision_option(
        form, {'action': verb, 'selector': OPTION, 'fact': 'Name'},
        {'Name': 'Test User'})
    assert action['action'] == 'combobox'
    assert action['selector'] == '#name-combo'
    session = MemorySession(form)
    result, filled, evidence = await act(session, form, action, {'Name': 'Test User'})
    assert result['action'] == 'combobox'
    assert evidence['selected_option']['selector'] == OPTION
    assert [args for args, _ in session.calls] == [('click', OPTION)]
    assert filled == {'#name-combo': 'Test User'}


@pytest.mark.parametrize('mode', [
    'parents', 'duplicate-option', 'disabled', 'option-disabled', 'invisible',
    'submit', 'navigates', 'commits_application', 'not-unique', 'contrary-fact',
    'unsupported-parent', 'parent-submit',
])
def test_ineligible_or_ambiguous_nested_option_retains_control_gate(mode):
    target = name_combo()
    option = target['combobox']['options'][1]
    controls = [target]
    facts = {'Name': 'Test User'}
    if mode == 'parents':
        controls.append(dict(deepcopy(target), selector='#other-picker'))
    elif mode == 'duplicate-option':
        target['combobox']['options'].append(deepcopy(option))
    elif mode == 'disabled':
        target['disabled'] = True
    elif mode == 'option-disabled':
        option['disabled'] = True
    elif mode == 'invisible':
        option['visible'] = False
    elif mode in {'submit', 'navigates', 'commits_application'}:
        option[mode] = True
    elif mode == 'not-unique':
        option['unique'] = False
    elif mode == 'contrary-fact':
        facts['Name'] = 'Wrong'
    elif mode == 'unsupported-parent':
        target['type'] = 'button'
    elif mode == 'parent-submit':
        target['submit'] = True
    form = page(*controls)
    original = {'action': 'click', 'selector': OPTION, 'fact': 'Name'}
    action = forms._resolve_decision_option(form, original, facts)
    assert action is original
    with pytest.raises(forms.ControlGate, match='absent: 0 matches'):
        forms.control(form, action, 'decision-action')


def test_diagnostic_is_local_only_and_contains_no_answer(caplog):
    form = page(name_combo())
    action = {'action': 'click', 'selector': OPTION, 'value': 'PRIVATE-ANSWER'}
    with pytest.raises(forms.ControlGate) as caught:
        forms.control(form, action, 'decision-action')
    assert OPTION in caplog.text
    assert 'unique_nested_control=True' in caplog.text
    assert 'PRIVATE-ANSWER' not in caplog.text
    assert OPTION not in str(caught.value)
    assert caught.value.diagnostics['selector'] == '[redacted]'


@pytest.mark.asyncio
async def test_second_absent_decision_gates_after_exactly_one_reobserve(tmp_path, monkeypatch):
    missing = {'action': 'click', 'selector': '#missing'}
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [missing, missing])
    result = await harness.run({'Name': 'Test User'})
    assert result['status'] == 'gate'
    assert result['step']['reason'] == 'absent: 0 matches'
    assert len(harness.views) == 2
    first, second = [i for i, event in enumerate(harness.events) if event == 'decision']
    assert harness.events[first + 1:second] == ['page']
    assert 'Your previous selector is not on the current page; choose only from available_choices.' in harness.views[1][0]
    assert all(call[0] == 'upload' for call in harness.calls)


@pytest.mark.asyncio
async def test_corrected_decision_uses_fresh_page_and_executes_once(tmp_path, monkeypatch):
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [
        {'action': 'click', 'selector': '#missing'},
        {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
        {'action': 'finish'},
    ])
    result = await harness.run({'Name': 'Test User'})
    assert result['status'] == 'gate'  # Script ends without submitting anything.
    assert result['step'].get('reason') != 'absent: 0 matches'
    assert len(harness.views) == 3
    assert [c for c in harness.calls if c[0] == 'fill'] == [
        ('fill', '#name', '--value', 'Test User')]
    assert 'Your previous selector' not in harness.views[2][0]


@pytest.mark.asyncio
async def test_resolved_action_still_meets_unchanged_submit_classification(tmp_path, monkeypatch):
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [
        {'action': 'click', 'selector': OPTION, 'fact': 'Name'},
        {'action': 'finish'},
    ])
    combo = name_combo()
    combo['form_action'] = harness.current['url']
    harness.current['controls'].append(combo)
    checked = []

    def classify(target):
        checked.append(target['selector'])
        # First check is normalization. The loop must independently classify
        # again even after normalization changes click into combobox.
        return target['selector'] == '#name-combo' and checked.count('#name-combo') > 1

    monkeypatch.setattr(forms, 'is_submission', classify)
    result = await harness.run({'Name': 'Test User'})
    assert result['status'] == 'gate'
    assert checked.count('#name-combo') >= 2
    assert 'Submit' in result['question'] or result.get('reason', '').startswith('defect_pre_submit')
    assert not any(c[0] == 'click' for c in harness.calls)


@pytest.mark.asyncio
async def test_last_budgeted_decision_still_gets_one_retry(tmp_path, monkeypatch):
    missing = {'action': 'click', 'selector': '#missing'}
    harness = _AfterUploadHarness(
        tmp_path, monkeypatch, [{'action': 'wait'}] * 98 + [missing, missing])
    result = await harness.run({'Name': 'Test User'})
    assert result['status'] == 'gate'
    assert result['step']['reason'] == 'absent: 0 matches'
    assert len(harness.views) == 100  # The proactive upload uses the first step.


@pytest.mark.parametrize('danger', ['submit-ancestor', 'submit-descendant'])
def test_reader_unsafe_option_with_false_commit_flags_still_gates(danger):
    option = '<span id="unsafe-option" role="option">Test User</span>'
    if danger == 'submit-ancestor':
        option = '<button type="submit">' + option + '</button>'
    else:
        option = option.replace('Test User', '<button type="submit">Test User</button>')
    tree = Tree()
    tree.feed('<html><body><form id="application" action="https://example.test/apply">'
              '<input id="picker" role="combobox" aria-label="Name" aria-controls="choices"'
              ' aria-expanded="true"><div id="choices" role="listbox">' + option
              + '</div></form></body></html>')
    assert not tree.stack
    from tools.browser_skill_dom import PAGE
    observed = json.loads(reader_output(
        'closed_widget_dom.cjs', dict(fixtureTree=tree.root, fixtureUrl='https://example.test/job'),
        PAGE, '(() => ({}))()'))['page']
    parent = next(c for c in observed['controls'] if c['selector'] == '#picker')
    descriptor = parent['combobox']['options'][0]
    assert descriptor['safe'] is False
    assert all(descriptor[k] is False for k in ('submit', 'navigates', 'commits_application'))
    action = {'action': 'click', 'selector': '#unsafe-option', 'fact': 'Name'}
    assert forms._resolve_decision_option(observed, action, {'Name': 'Test User'}) is action
    with pytest.raises(forms.ControlGate, match='absent: 0 matches'):
        forms.control(observed, action, 'decision-action')


@pytest.mark.asyncio
@pytest.mark.parametrize('after_miss', [False, True])
async def test_completed_upload_skip_survives_removed_receiver_and_preserves_retry(
        tmp_path, monkeypatch, after_miss):
    missing = {'action': 'click', 'selector': '#missing'}
    actions = ([missing] if after_miss else []) + [
        {'action': 'upload', 'selector': '#resume'},
        missing if after_miss else {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
    ]
    if not after_miss:
        actions.insert(1, {'action': 'upload', 'selector': '#resume'})
        actions.append({'action': 'finish'})
    harness = _AfterUploadHarness(tmp_path, monkeypatch, actions)
    raw_page = forms.bsk.Session.page
    reads_after_upload = 0

    async def remove_receiver_after_upload_readback(self):
        nonlocal reads_after_upload
        if any(c[0] == 'upload' for c in harness.calls):
            reads_after_upload += 1
            # Preserve upload readback, then model the page removing its input.
            if reads_after_upload > 1:
                harness.current['controls'] = [c for c in harness.current['controls']
                                               if c['selector'] != '#resume']
        return await raw_page(self)

    monkeypatch.setattr(forms.bsk.Session, 'page', remove_receiver_after_upload_readback)
    result = await harness.run({'Name': 'Test User'})
    assert len(harness.views) == (3 if after_miss else 4), result
    assert len([c for c in harness.calls if c[0] == 'upload']) == 1
    if after_miss:
        assert result['step']['reason'] == 'absent: 0 matches'
        assert sum('Your previous selector' in task for task, _ in harness.views) == 1
    else:
        assert all('Your previous selector' not in task for task, _ in harness.views)
        assert [c for c in harness.calls if c[0] == 'fill'] == [
            ('fill', '#name', '--value', 'Test User')]
