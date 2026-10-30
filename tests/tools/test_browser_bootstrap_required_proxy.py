"""Issue #264: a react-select's required proxy input re-renders after a committed pick.

Live read of a public Greenhouse form: every required react-select is followed by an
id-less `input.requiredInput`, so PAGE gives it only a positional selector. Picking a
Location option makes react-select stop rendering that input (the combobox holds the
value now). The settlement loop then found zero matches for a selector nothing had
lost and gated with a message that did not say which control. These tests pin both
shapes: a legitimate re-render proceeds, a true loss still refuses and names the control.

Every page, grant and write is synthetic; no browser, network or submission is used.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

# apply() imports core.stores lazily; bind it before _world() patches get_settings,
# or a fresh process captures the patched SimpleNamespace (the sibling module's
# tests only pass after another test has imported it).
import core.stores  # noqa: F401
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import field
from tests.tools.test_browser_bootstrap_settlement import _apply, _world

PROXY = ('html:nth-of-type(1) > body:nth-of-type(1) > main:nth-of-type(1) > '
         'div:nth-of-type(5) > div:nth-of-type(1) > input:nth-of-type(1)')
SHIFTED = PROXY.replace('div:nth-of-type(5)', 'div:nth-of-type(6)')


def _proxy(case):
    return field('', PROXY, type='text', form_action=case.url)


def _world_with_proxy(monkeypatch, rerender):
    """Greenhouse city combobox followed by its proxy; `rerender` runs after the pick."""
    case, raw = _world(monkeypatch, 'greenhouse', kind='combobox')
    for controls in (case.page['controls'], raw.state['controls']):
        at = next(i for i, c in enumerate(controls) if c['selector'] == '#city')
        controls.insert(at + 1, _proxy(case))
    original = raw.call

    async def call(command, *args, **kwargs):
        result = await original(command, *args, **kwargs)
        if command == 'click' and args[0] == '#city-choice':
            rerender(raw.state['controls'])
        return result

    raw.call = call

    async def stop(prompt, page, history, *, model):
        return {'action': 'gate', 'question': 'Synthetic stop before submit'}

    monkeypatch.setattr(bsk, 'decision', stop)
    return case, raw


def _drop_proxy(controls):
    controls[:] = [c for c in controls if c['selector'] != PROXY]


def _shift_proxy(controls):
    next(c for c in controls if c['selector'] == PROXY)['selector'] = SHIFTED


def _lose_value_and_proxy(controls):
    """The pick did not stick: the proxy is gone but the combobox holds nothing."""
    _drop_proxy(controls)
    next(c for c in controls if c['selector'] == '#city')['combobox']['selected_text'] = ''


async def test_vanished_proxy_after_owner_commit_is_not_a_loss(monkeypatch):
    """react-select unmounts the proxy once the combobox holds the picked value."""
    case, raw = _world_with_proxy(monkeypatch, _drop_proxy)
    outcome = await _apply(case)
    assert outcome['status'] == 'gate'
    assert 'Synthetic stop before submit' in outcome['question']
    assert 'disappeared' not in outcome['question']
    assert raw.writes.count(('click', '#city-choice')) == 1
    assert not any(call[0] == 'click' and call[1] in {'#submit', '#continue'}
                   for call in raw.calls)


async def test_proxy_with_shifted_selector_is_rebound_by_its_owner(monkeypatch):
    """A re-render that only moves the proxy's nth-of-type path is the same control."""
    case, raw = _world_with_proxy(monkeypatch, _shift_proxy)
    outcome = await _apply(case)
    assert outcome['status'] == 'gate'
    assert 'Synthetic stop before submit' in outcome['question']
    assert 'disappeared' not in outcome['question']


async def test_proxy_lost_without_a_committed_owner_still_refuses_and_names_it(monkeypatch):
    """The proxy alone vanishing proves nothing; the owner must hold the value."""
    case, raw = _world_with_proxy(monkeypatch, _lose_value_and_proxy)
    outcome = await _apply(case)
    assert outcome['status'] == 'gate'
    assert 'A previously observed required control disappeared' in outcome['question']
    assert 'after "Location City"' in outcome['question']
    assert PROXY in outcome['question']
    assert not any(call[0] == 'click' and call[1] in {'#submit', '#continue'}
                   for call in raw.calls)


def test_helpers_refuse_a_proxy_whose_owner_is_missing_or_not_a_combobox():
    case_page = {'controls': [
        field('Location City', '#city', type='combobox',
              combobox={'selected_text': 'Test City'}),
    ]}
    proxy = field('', PROXY, type='text')
    owner = case_page['controls'][0]
    assert forms._rebind_required_proxy(case_page, proxy, owner) is True
    # Owner gone from the page: a true loss.
    assert forms._rebind_required_proxy({'controls': []}, proxy, owner) is None
    # Owner present but it no longer reads as a combobox: do not guess.
    plain = {'controls': [{**owner, 'type': 'text'}]}
    assert forms._rebind_required_proxy(plain, proxy, owner) is None
    # Owner present but empty: a required answer is missing, not re-rendered.
    empty = {'controls': [deepcopy({**owner, 'combobox': {'selected_text': ''}})]}
    assert forms._rebind_required_proxy(empty, proxy, owner) is None


def test_only_an_unlabeled_id_less_required_input_after_a_combobox_has_an_owner():
    combo = field('Location City', '#city', type='combobox', combobox={})
    proxy = field('', PROXY, type='text')
    labeled = field('Nickname', PROXY.replace('div:nth-of-type(1) >', 'div:nth-of-type(2) >'),
                    type='text')
    keyed = field('', '#unlabeled', type='text')
    optional = field('', PROXY + 'x', type='text', required=False)
    owners = forms._required_proxy_owners([combo, proxy, combo, labeled, combo, keyed,
                                           combo, optional])
    assert list(owners) == [PROXY]
    assert owners[PROXY] is combo


def test_gate_names_label_and_selector():
    named = forms._vanished_required_gate(field('Phone', '#phone', type='tel'), None)
    assert 'label "Phone"' in str(named) and 'selector #phone' in str(named)
    combo = field('Location City', '#city', type='combobox')
    unnamed = forms._vanished_required_gate(field('', PROXY, type='text'), combo)
    assert 'after "Location City"' in str(unnamed) and PROXY in str(unnamed)
