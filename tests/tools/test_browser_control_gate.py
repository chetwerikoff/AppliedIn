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
    # These modules no longer call control() directly. A source-only census
    # cannot require retired callers to exist merely to pin API compatibility.
    for module in (bsk, fixture):
        source = Path(inspect.getsourcefile(module)).read_text()
        tree = ast.parse(source)
        sites = [call for call in ast.walk(tree)
                 if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                 and call.func.id == 'control']
        assert sites == []
    # The same public helper still resolves and refuses with two arguments.
    target = {'selector': 'button', 'disabled': False}
    assert forms.control({'controls': [target]}, {'selector': 'button'}) is target
    for controls in ([], [target, target], [{**target, 'disabled': True}]):
        with pytest.raises(forms.ControlGate):
            forms.control({'controls': controls}, {'selector': 'button'})


def test_all_nineteen_current_control_call_sites_keep_named_provenance():
    source = inspect.getsource(forms)
    tree = ast.parse(source)
    sites = [call for call in ast.walk(tree)
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
             and call.func.id == 'control']
    # Nineteen resolution call sites remain across raw/preparation/final.
    # Two initial-decision branches share the same code-owned diagnostic tag.
    assert len(sites) == 19
    assert all(len(call.args) == 3 and not call.keywords
               and isinstance(call.args[2], ast.Constant)
               and isinstance(call.args[2].value, str)
               for call in sites)
    tags = [call.args[2].value for call in sites]
    assert len(set(tags)) == 18
    assert tags.count('decision-initial') == 2
    assert all(tags.count(tag) == 1 for tag in set(tags) - {'decision-initial'})
    assert all(0 < len(tag) <= 32 for tag in tags)
    assert set(tags) == forms._CONTROL_CALLERS - {'external'}


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


async def _synthetic_preaction_apply(monkeypatch, selector):
    """One regular initial control miss, before any browser form mutation."""
    from core import events
    from tools import browser_apply, claude_chrome

    page = {'url': URL, 'controls': [], 'inventory_verified': True,
            'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}
    browser_effects = []
    emitted = []

    class Session:
        def __init__(self, kind):
            assert kind == 'apply'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def navigate(self, url):
            assert url == URL
            return deepcopy(page)

        async def page(self):
            return deepcopy(page)

        async def call(self, *args, **kwargs):
            browser_effects.append(args)
            pytest.fail('An initial unresolved control cannot trigger a browser write')

    async def bootstrap(session, page, *args, **kwargs):
        return page

    facts = {'Full name': FIELD_SECRET, 'Synthetic Answer': ANSWER_SECRET}
    action = {'action': 'fill', 'selector': selector, 'value': FIELD_SECRET,
              'answer': ANSWER_SECRET, 'label': LABEL_SECRET,
              'question': QUESTION_SECRET, 'fact': 'Synthetic Answer',
              'resume_path': PATH_SECRET}
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **k: None)
    # The current preparation admission checks the exact tracked job identity
    # before reaching the control resolver. Keep this fixture synthetic but
    # canonical so its negative proves the intended zero-IPC control Gate.
    tracked = {'pk': PK, 'company': 'example-co', 'job_id': 'synthetic',
               'jd_url': URL, 'status': 'submitting', 'ats': '',
               'human_approved_answers': {
                   'Synthetic Answer': {'value': ANSWER_SECRET}}}
    monkeypatch.setattr(forms, '_row', lambda pk: tracked if pk == PK else {})
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    monkeypatch.setattr(bsk, 'Session', Session)
    monkeypatch.setattr(bsk, 'decision', AsyncMock(return_value=action))
    monkeypatch.setattr(browser_apply, '_site_rules', lambda *a, **k: '')
    monkeypatch.setattr(claude_chrome, '_stage_resume',
                        lambda *a, **k: f'/synthetic/{PATH_SECRET}.pdf')
    monkeypatch.setattr(events, 'emit', lambda kind, **kwargs: emitted.append((kind, kwargs)))

    result = await forms.apply(
        URL, 'example-co', facts, 'synthetic', pk=PK,
        resume_path=f'/synthetic/{PATH_SECRET}.pdf')
    assert result['status'] == 'gate'
    assert not browser_effects
    assert all(kind != 'gate' for kind, _ in emitted)  # Only _apply_direct emits the ordinary gate.
    assert result['step']['last_button'] == ''
    assert result['step']['reason'] == 'absent: 0 matches'
    assert result['step']['caller'] == 'decision-initial'
    assert result['step']['action'] == 'fill'
    assert result['step']['selector'] == '[redacted]'
    _assert_metadata(result['question'], result['step'])
    assert not any(secret in repr(result) for secret in SECRETS)
    return result, facts


@pytest.mark.parametrize('selector', [
    f'input#{ID_SECRET}',
    f'input[data-token="{ATTRIBUTE_SECRET}"]',
    'body > div > input' + ('x' * 160) + f'#{LONG_SECRET}',
])
async def test_apply_step_is_safe_at_initial_checkpoint(monkeypatch, selector):
    await _synthetic_preaction_apply(monkeypatch, selector)


async def test_existing_downstream_event_uses_apply_question_without_step(monkeypatch):
    from agent import run
    from core import events, profiles, rotation
    from tools import browser_apply, browser_runtime, credentials

    result, facts = await _synthetic_preaction_apply(
        monkeypatch, f'input[data-token="{ATTRIBUTE_SECRET}"]')

    class Tracking:
        def __init__(self):
            from tools import posting_guard as pg
            checked = pg.analyze("Synthetic engineering job. " * 40)
            self.row = {**{'pk': PK, 'status': 'tailored', 'company': 'example-co',
                        'jd_url': URL}, "jd_text": checked["text"],
                        "posting_guard": checked["posting_guard"]}
            self.writes = []

        def get(self, pk):
            assert pk == PK
            return dict(self.row)

        def get_current(self, pk):
            return self.get(pk)

        def update_posting_guard(self, pk, observed, inspected=None, *,
                                 reviewed_generation=None, source_witness=False,
                                 settle_refusal=False):
            from core.storage.base import guard_observation_matches, guard_patch
            current = self.get_current(pk)
            if not guard_observation_matches(current, observed):
                return False
            patch = guard_patch(current, inspected,
                                reviewed_generation=reviewed_generation,
                                source_witness=source_witness,
                                settle_refusal=settle_refusal)
            if patch is None:
                return False
            self.row.update(patch)
            return True

        def set_status(self, pk, status, **kwargs):
            assert pk == PK
            self.writes.append((status, kwargs))
            self.row.update(status=getattr(status, 'value', status), **kwargs)

    tracking = Tracking()
    stores = SimpleNamespace(
        tracking=tracking,
        answer_bank=SimpleNamespace(all_facts=lambda company: dict(facts)),
        secrets=None,
    )
    emitted = []
    async def forwarded(*args, **kwargs):
        return deepcopy(result)

    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *a, **k: False)
    from tools import posting_guard as pg
    monkeypatch.setattr(run, "_jd_meta",
                        AsyncMock(return_value=pg.analyze("Synthetic engineering job. " * 40)))
    monkeypatch.setattr(run, '_no_sponsorship', lambda *a, **k: False)
    monkeypatch.setattr(run, 'get_settings', lambda: SimpleNamespace(browser_model='synthetic'))
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: f'/synthetic/{PATH_SECRET}.pdf')
    monkeypatch.setattr(rotation, 'ensure', lambda *a, **k: None)
    monkeypatch.setattr(profiles, 'resolve_for', lambda row: None)
    monkeypatch.setattr(profiles, 'expand_all', lambda original: original)
    monkeypatch.setattr(credentials, 'get_login', lambda *a: None)
    monkeypatch.setattr(browser_runtime, 'configuration', lambda: {'engine': 'browser_skill'})
    monkeypatch.setattr(browser_apply, 'apply', forwarded)
    monkeypatch.setattr(events, 'emit', lambda kind, **kw: emitted.append((kind, kw)))

    consumed = await run._apply_direct(PK, stores)
    gates = [payload['detail'] for kind, payload in emitted if kind == 'gate']
    assert consumed == {'result': 'gated', 'pk': PK, 'question': result['question']}
    assert 'step' not in consumed
    assert gates == [result['question']]
    assert tracking.row['gate_pending']['question'] == result['question']
    assert {k: result['step'][k] for k in FIELDS} == {
        'action': 'fill', 'selector': '[redacted]',
        'reason': 'absent: 0 matches', 'caller': 'decision-initial',
    }
    _assert_metadata(gates[0], result['step'])
    assert not any(secret in gates[0] for secret in SECRETS)


async def test_picker_postaction_gate_preserves_historical_prior_click(monkeypatch):
    # An authorized picker-open click exists before the re-observation Gate.
    # The new diagnostic must not retroactively move the Gate ahead of that click.
    target = {'selector': 'button:nth-of-type(2)',
              'label': 'Work mode', 'question': 'Work mode',
              'type': 'button', 'tag': 'button',
              'combobox': {'mode': 'open', 'link_id': 'synthetic-options',
                           'linked': False, 'options': []}}
    before = {'url': URL, 'controls': [target]}
    after = {'url': URL, 'controls': []}
    action = {'action': 'combobox', 'selector': target['selector'], 'fact': 'Work mode'}
    effects = []

    class Session:
        async def call(self, *args):
            effects.append(args)
            assert args == ('click', target['selector'])

        async def page(self):
            return deepcopy(after)

    async def observed(session, page, **kwargs):
        return page, {}

    monkeypatch.setattr(forms, '_observe_controls', observed)
    monkeypatch.setattr(forms, '_disclosure_question',
                        lambda *a, **k: {'label': 'Work mode'})
    monkeypatch.setattr(forms, '_fact_key', lambda facts, question: question)
    monkeypatch.setattr(forms, 'exact_approval', lambda *a, **k: None)
    monkeypatch.setattr(forms, 'guarded_value', lambda *a, **k: 'Approved')
    with pytest.raises(forms.ControlGate) as caught:
        await forms._execute_combobox(
            Session(), before, action, facts={'Work mode': 'Approved'},
            filled={}, pk=PK, job_url=URL, ctx=None, provenance=None)
    assert effects == [('click', target['selector'])]
    assert caught.value.diagnostics['reason'] == 'absent: 0 matches'
    assert caught.value.diagnostics['caller'] == 'combobox-open'
    _assert_metadata(str(caught.value), caught.value.diagnostics)
