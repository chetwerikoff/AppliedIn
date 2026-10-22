"""SVG picker graphics are not independent accessible applicant controls.

Use both real production page/witness readers on synthetic parent-edge DOM.
The generic ARIA inventory and single code-owned raw Submit guard, not a
widget-specific SVG taxonomy, decide which controls can act and what can commit.
No browser, network, owner data, or real application is used.
"""
import json
from pathlib import Path
import subprocess

import pytest

from tools import browser_skill_apply as forms


def svg_observe(kind='greenhouse', **scenario):
    readers = ((forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS)
               if kind == 'entry' else
               (forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS))
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(scenario), *readers], text=True))


SVG_GRAPHICS = {
    '#search-svg', '#icon-circle', '#icon-line', '#clear-svg', '#clear-line',
    '#clear-mask', '#clear-rect', '#clear-path', '#clear-circle',
}
SCENARIOS = [
    pytest.param({'svgParts': 'positive'}, id='icon-and-clear'),
    pytest.param({'svgParts': 'outside-svg'}, id='outside-svg-line'),
    pytest.param({'svgParts': 'rect-visible'}, id='visible-svg-rectangle'),
    pytest.param({'svgParts': 'wrong-button'}, id='new-real-button'),
    pytest.param({'svgParts': 'selected'}, id='selected-country-svg'),
    pytest.param({'svgParts': 'option'}, id='option-svg'),
    pytest.param({'oldSvgDecoration': True}, id='old-decorations'),
]


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('scenario', SCENARIOS)
def test_svg_decoration_retains_independently_observed_answer_receivers(
        kind, scenario):
    # Missing old popup/graph certificates do not cause an extra per-step
    # refusal; readers inventory only their actual native and ARIA controls.
    out = svg_observe(kind, **scenario)
    page, witness = out['page'], out['witness']
    page_by_selector = {c['selector']: c for c in page['controls']}
    fixed_by_selector = {c['selector']: c for c in witness['controls']}
    assert len(page_by_selector) == len(page['controls'])
    assert list(page_by_selector) == list(fixed_by_selector)
    assert {'#country', '#country-search', '#country-clear',
            '#phone', '#resume', '#submit'} <= page_by_selector.keys()
    # An SVG is neither an actual form receiver nor a selectable ARIA option.
    assert not SVG_GRAPHICS.intersection(page_by_selector)
    assert not any(name.startswith('#picker-option-')
                   for name in page_by_selector)
    for selector in ('#country', '#country-search', '#phone', '#resume', '#submit'):
        current, fixed = page_by_selector[selector], fixed_by_selector[selector]
        assert (current['tag'], current['type'],
                current['form_selector'], current['form_action']) == (
                    fixed['tag'], fixed['type'],
                    fixed['form_selector'], fixed['form_action'])
        assert current['in_form'] == fixed['in_form']
    country = page_by_selector['#country']
    assert country['type'] == 'combobox'
    assert country['combobox'] == fixed_by_selector['#country']['combobox']
    assert page_by_selector['#phone']['type'] == 'tel'
    assert page_by_selector['#submit']['submit'] is True
    assert page_by_selector['#resume']['type'] == 'file'
    # A real extra button inside a mispositioned SVG wrapper is an *actual*
    # control, not an exemption merely because its child artwork is inert.
    if scenario.get('svgParts') == 'wrong-button':
        assert page_by_selector['#other-clear']['tag'] == 'button'
        assert fixed_by_selector['#other-clear']['type'] == 'button'
    else:
        assert '#other-clear' not in page_by_selector


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('scenario', SCENARIOS)
async def test_svg_geometry_never_authorizes_a_missing_final_form_answer(
        kind, scenario, monkeypatch, tmp_path):
    from tests.tools.test_browser_greenhouse_parity import (
        assert_unapproved_submit_is_refused,
    )

    # The actual application IPC controller receives a synthetically attached
    # row PDF and matching tracked job. Only current form-owned answers and
    # the unchanged code-owned final guard may authorize Submit. Decorative
    # SVG placement is not an alternative proof or a premature human gate.
    out = svg_observe(kind, **scenario)
    await assert_unapproved_submit_is_refused(
        out, monkeypatch, tmp_path, require_incomplete=False)


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('selector', ['#search-svg', '#clear-svg',
                                     '#country-clear', '#picker-option-0'])
async def test_unapproved_svg_or_picker_click_cannot_escape_raw_ipc(
        kind, selector, monkeypatch):
    from tools import browser_skill as bsk

    # Even a direct Session call must pass the one existing application
    # controller. Neither an SVG descendant, hidden search option nor
    # unapproved native clear button can issue an arbitrary browser click.
    page = svg_observe(kind, svgParts='positive')['page']
    emitted = []
    controller = forms.ApplicationIPCController(
        'example-co#1', 'example-co', page['url'], '', {}, {}, [], {},
        lambda: emitted.append('started'))

    async def current_page(_session):
        return page

    async def raw_command(*args, **kwargs):
        emitted.append((args, kwargs))
        return {}

    monkeypatch.setattr(controller, '_page', current_page)
    monkeypatch.setattr(controller, '_fresh_preparation', lambda *_: None)
    monkeypatch.setattr(bsk, 'command', raw_command)
    session = bsk.Session('apply')
    session.id = 'synthetic-offline'
    session.bind_application_controller(controller)
    with pytest.raises(forms.Gate):
        await session.application_effect('click', selector)
    assert emitted == [] and controller.committed is False
