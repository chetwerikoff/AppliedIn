"""Offline diagnostics for existing BrowserSkill control-resolution Gates.

All controls, selectors, answers, paths and event consumers are synthetic.
Neither the application browser nor owner tracking/storage is contacted.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture


URL = 'https://example.com/jobs/synthetic'
PK = 'example-co#synthetic'
FIRST_SENTENCE = (
    'The chosen control is absent, disabled or ambiguous; inspect the current page.'
)
FIELD_SECRET = 'SYNTH_FIELD_VALUE_SECRET'
ANSWER_SECRET = 'SYNTH_APPROVED_ANSWER_SECRET'
PATH_SECRET = 'SYNTH_STAGED_PATH_SECRET'
LABEL_SECRET = 'SYNTH_CONTROL_LABEL_SECRET'
QUESTION_SECRET = 'SYNTH_CONTROL_QUESTION_SECRET'
BUTTON_SECRET = 'SYNTH_PREVIOUS_BUTTON_SECRET'
ID_SECRET = 'SYNTH_SHORT_ID_SELECTOR_SECRET'
ATTRIBUTE_SECRET = 'SYNTH_SHORT_ATTRIBUTE_SELECTOR_SECRET'
LONG_SECRET = 'SYNTH_LONG_SELECTOR_SECRET'
SECRETS = (
    FIELD_SECRET, ANSWER_SECRET, PATH_SECRET, LABEL_SECRET, QUESTION_SECRET,
    BUTTON_SECRET, ID_SECRET, ATTRIBUTE_SECRET, LONG_SECRET,
)
FIELDS = ('action', 'selector', 'reason', 'caller')


def _assert_metadata(message, fields):
    assert message.startswith(FIRST_SENTENCE)
    assert message.index('action=') < message.index('selector=')
    assert message.index('selector=') < message.index('reason=')
    assert message.index('reason=') < message.index('caller=')
    for key in FIELDS:
        assert f'{key}={fields[key]}' in message
    assert len(fields['selector']) <= 120
    assert '\n' not in fields['selector'] and '\r' not in fields['selector']


@pytest.mark.parametrize('mode,reason', [
    ('missing', 'absent: 0 matches'),
    ('duplicate', 'ambiguous: 3 matches'),
    ('disabled', 'disabled'),
])
def test_existing_resolution_reasons_and_external_two_argument_gate(mode, reason):
    selector = 'form > input:nth-of-type(2)'
    controls = ([{'selector': selector, 'disabled': False}] * 3
                if mode == 'duplicate' else
                [{'selector': selector, 'disabled': True}]
                if mode == 'disabled' else [])
    action = {'selector': selector, 'action': 'fill', 'value': FIELD_SECRET,
              'answer': ANSWER_SECRET, 'label': LABEL_SECRET, 'question': QUESTION_SECRET}
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': controls}, action)
    fields = caught.value.diagnostics
    assert fields == {'action': 'fill', 'selector': selector,
                      'reason': reason, 'caller': 'external'}
    _assert_metadata(str(caught.value), fields)
    assert all(secret not in str(caught.value) for secret in SECRETS)
    assert all(secret not in repr(fields) for secret in SECRETS)


def test_success_keeps_original_control_object_and_exact_raw_selector():
    selected = {'selector': f'input#{ID_SECRET}', 'disabled': False, 'label': LABEL_SECRET}
    page = {'controls': [selected, {'selector': '#unrelated', 'disabled': False}]}
    assert forms.control(page, {'selector': selected['selector'], 'action': 'fill'}) is selected
    # Equality is against the unmodified selector, not its redacted display.
    with pytest.raises(forms.ControlGate) as caught:
        forms.control(page, {'selector': f'input#{ID_SECRET}_different', 'action': 'fill'})
    assert caught.value.diagnostics['reason'] == 'absent: 0 matches'
    assert caught.value.diagnostics['selector'] == '[redacted]'


def test_external_two_argument_helper_stays_safe_without_legacy_call_sites():
    # Discovery intentionally uses the two-argument public helper inside
    # _crawl. The sealed fixture has no direct control-resolution call site.
    for module in (bsk, fixture):
        source = Path(inspect.getsourcefile(module)).read_text()
        tree = ast.parse(source)
        sites = [call for call in ast.walk(tree)
                 if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                 and call.func.id == 'control']
        if module is bsk:
            crawls = [node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == '_crawl']
            assert len(crawls) == 1 and len(sites) == 1
            call = sites[0]
            assert len(call.args) == 2 and not call.keywords
            assert [arg.id if isinstance(arg, ast.Name) else None
                    for arg in call.args] == ['page', 'action']
            assert any(isinstance(node, ast.Assign) and node.value is call
                       and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == 'target'
                       for node in ast.walk(crawls[0]))
        else:
            assert sites == []
    # The same public helper still resolves and refuses with two arguments.
    target = {'selector': 'button', 'disabled': False}
    assert forms.control({'controls': [target]}, {'selector': 'button'}) is target
    for controls in ([], [target, target], [{**target, 'disabled': True}]):
        with pytest.raises(forms.ControlGate):
            forms.control({'controls': controls}, {'selector': 'button'})


@pytest.mark.parametrize('selector,expected', [
    (f'#{ID_SECRET}', '[redacted]'),
    (f'input[data-token="{ATTRIBUTE_SECRET}"]', '[redacted]'),
    (f'input > label#{ID_SECRET}', '[redacted]'),
    ('input.class-name', '[redacted]'),
    ('input:nth-of-type(1) > select:nth-of-type(12)',
     'input:nth-of-type(1) > select:nth-of-type(12)'),
    ('body>form>div:nth-of-type(23)>input',
     'body > form > div:nth-of-type(23) > input'),
])
def test_whole_selector_redaction_precedes_display_cap(selector, expected):
    action = {'action': 'fill', 'selector': selector, 'value': FIELD_SECRET}
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': []}, action, 'execute-initial')
    assert caught.value.diagnostics['selector'] == expected
    assert caught.value.diagnostics['caller'] == 'execute-initial'
    _assert_metadata(str(caught.value), caught.value.diagnostics)
    for secret in SECRETS:
        assert secret not in str(caught.value)


def test_long_selector_never_exposes_untrusted_suffix_or_truncated_prefix():
    unsafe = ('body > div > ' + ('a' * 180) + f'#{LONG_SECRET}')
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': []}, {'action': 'fill', 'selector': unsafe})
    assert caught.value.diagnostics['selector'] == '[redacted]'
    assert 'body > div' not in str(caught.value)
    assert LONG_SECRET not in str(caught.value)

    structural = 'body' + ' > div:nth-of-type(2)' * 15 + ' > input'
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': []}, {'action': 'click', 'selector': structural})
    display = caught.value.diagnostics['selector']
    assert len(display) <= 120
    assert structural.startswith(display)
    assert display.split(' > ')[-1] in ('body', 'div:nth-of-type(2)', 'input')


def test_untrusted_action_and_caller_fall_back_to_code_owned_tokens():
    action = {'selector': f'#{ID_SECRET}', 'action': f'fill_{ANSWER_SECRET}'}
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': []}, action, f'caller_{LABEL_SECRET}')
    assert caught.value.diagnostics == {
        'action': 'control', 'selector': '[redacted]',
        'reason': 'absent: 0 matches', 'caller': 'external',
    }
    assert not any(secret in str(caught.value) for secret in SECRETS)


def test_control_handoff_does_not_echo_previous_button_or_untrusted_fields():
    action = {'selector': f'input[data-token="{ATTRIBUTE_SECRET}"]',
              'action': 'fill', 'value': FIELD_SECRET, 'answer': ANSWER_SECRET,
              'label': LABEL_SECRET, 'question': QUESTION_SECRET}
    with pytest.raises(forms.ControlGate) as caught:
        forms.control({'controls': []}, action, 'decision-fresh')
    context = forms._control_gate_handoff(
        caught.value, {'last_button': BUTTON_SECRET,
                       'url': URL + '?token=synthetic-secret'})
    assert context['step']['last_button'] == ''
    assert context['step']['url'] == URL
    assert {key: context['step'][key] for key in FIELDS} == caught.value.diagnostics
    _assert_metadata(context['text'], context['step'])
    assert not any(secret in repr(context) for secret in SECRETS)
    assert 'synthetic-secret' not in repr(context)
