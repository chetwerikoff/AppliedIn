"""Issue #330: an observed selector disambiguates captions, never safety."""
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_apply_decision_regression import act
from tests.tools.test_browser_typeahead import TypeaheadSession, option, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('answer', [
    'Testville', 'Testville, Testland', 'Testville, Test Region, Testland',
])
async def test_identical_captions_keep_explicit_choice_or_first_eligible(answer, explicit, caplog):
    caption = 'Testville, Test Region, Testland'
    options = [dict(option(caption, '#first'), safe=False),
               option(caption, '#second'), option(caption, '#third')]
    form = snapshot(options)
    original = deepcopy(form)
    action = {'action': 'combobox', 'selector': '#name-combo', 'fact': 'City'}
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        if explicit:
            action = forms._resolve_decision_option(
                form, dict(action, action='click', selector='#third'), {'City': answer})
            assert action['option_selector'] == '#third'
            assert action['value'] == caption
        session = TypeaheadSession([form])
        _, filled, evidence = await act(session, form, action, {'City': answer})
        forms._log_combobox_outcome(None, '#name-combo', evidence)
    selected = '#third' if explicit else '#second'
    assert session.calls == [('click', selected)]
    assert evidence['selected_option']['selector'] == selected
    assert filled == {'#name-combo': caption}
    assert form == original
    assert 'Testville' not in caplog.text and 'Testland' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['absent', 'changed-caption', 'unsafe', 'duplicate-selector'])
async def test_stale_bound_selector_uses_existing_fallback(mode):
    chosen = option('Testville, Testland', '#chosen')
    form = snapshot([chosen])
    action = forms._resolve_decision_option(
        form, {'action': 'click', 'selector': '#chosen', 'fact': 'City'},
        {'City': 'Testville'})
    current = deepcopy(form)
    current['controls'][0]['combobox']['options'] = [option(chosen['label'], '#fallback')]
    if mode != 'absent':
        stale = dict(chosen)
        if mode == 'changed-caption':
            stale['label'] = 'Other City, Testland'
        elif mode == 'unsafe':
            stale['safe'] = False
        current['controls'][0]['combobox']['options'].insert(0, stale)
        if mode == 'duplicate-selector':
            current['controls'][0]['combobox']['options'].insert(0, dict(stale))
    session = TypeaheadSession([current])
    _, _, evidence = await act(session, current, action, {'City': 'Testville'})
    assert session.calls == [('click', '#fallback')]
    assert evidence['selected_option']['selector'] == '#fallback'


@pytest.mark.asyncio
@pytest.mark.parametrize('flag,value', [
    ('safe', False), ('safe', None), ('disabled', True), ('visible', False),
    ('unique', False), ('submit', True), ('navigates', True), ('commits_application', True),
])
async def test_current_bound_option_cannot_bypass_option_safety(flag, value):
    chosen = option('Testville, Testland', '#chosen')
    form = snapshot([chosen])
    action = forms._resolve_decision_option(
        form, {'action': 'click', 'selector': '#chosen', 'fact': 'City'},
        {'City': 'Testville'})
    chosen[flag] = value
    session = TypeaheadSession([form])
    _, _, evidence = await act(session, form, action, {'City': 'Testville'})
    assert session.calls == []
    assert evidence['performed'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['disabled', 'submit', 'native-submit'])
async def test_current_bound_option_cannot_bypass_parent_safety(mode):
    form = snapshot([option('Testville', '#chosen')])
    action = forms._resolve_decision_option(
        form, {'action': 'click', 'selector': '#chosen'}, {})
    target = form['controls'][0]
    if mode == 'disabled':
        target['disabled'] = True
    elif mode == 'submit':
        target['submit'] = True
    else:
        target.update(tag='button', native_type='submit', submit=True, label='Next',
                      native_form_owner_selector=target['form_selector'])
    session = TypeaheadSession([form])
    if mode == 'disabled':
        with pytest.raises(forms.ControlGate):
            await act(session, form, action, {})
    else:
        _, _, evidence = await act(session, form, action, {})
        assert evidence['performed'] is False
    assert session.calls == []


@pytest.mark.asyncio
async def test_differing_captions_without_choice_remain_ambiguous():
    form = snapshot([option('Testville, Testland', '#first'),
                     option('Testville, Otherland', '#second')])
    session = TypeaheadSession([form])
    _, _, evidence = await act(session, form,
                              {'action': 'combobox', 'selector': '#name-combo', 'fact': 'City'},
                              {'City': 'Testville'})
    assert session.calls == []
    assert evidence['performed'] is False
