"""The new SVG tags are inert parts at two exact placements, not general decor."""
import json
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms


def svg_observe(kind='greenhouse', *, mutation=None, **scenario):
    readers = ((forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS) if kind == 'entry'
               else (forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS))
    readers = tuple(s.replace('parts.forEach(n=>pickerParts.add(n));',
                             'parts.forEach(n=>pickerParts.add(n)); global.__graphs.push(parts.map(selector));')
                    for s in readers)
    if mutation:
        readers = tuple(mutation(s) for s in readers)
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(scenario), *readers], text=True))


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
def test_icon_and_clear_complete_svg_parts_are_inert(kind):
    out = svg_observe(kind, svgParts='positive')
    assert out['page']['inventory_verified']
    assert out['graphs'][0] == out['graphs'][1]
    parts = set(out['graphs'][0])
    assert {'#search-svg', '#icon-circle', '#icon-line', '#country-clear', '#clear-svg',
            '#clear-line', '#clear-mask', '#clear-rect', '#clear-path', '#clear-circle'} <= parts
    for observation in (out['page'], out['witness']):
        assert not (parts & {c['selector'] for c in observation['controls']})
        for selector in parts:
            with pytest.raises(forms.Gate):
                forms.control(observation, {'selector': selector})


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('placement', ['outside-svg', 'rect-visible', 'wrong-button', 'selected', 'option'])
def test_five_independent_svg_negatives_preserve_inventory_failure(kind, placement):
    out = svg_observe(kind, svgParts=placement)
    assert not out['page']['inventory_verified']
    assert out['page']['opaque_controls']
    assert '#country-search' in {c['selector'] for c in out['page']['controls']}
    assert len([c for c in out['page']['controls'] if c['selector'].startswith('#picker-option-')]) == 244
    with pytest.raises(forms.Gate, match='inventory is incomplete'):
        forms._inventory_ok(out['page'])


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
def test_old_selected_country_and_option_svg_decorations_remain_admitted(kind):
    out = svg_observe(kind, oldSvgDecoration=True)
    assert out['page']['inventory_verified']
    assert out['graphs'][0] == out['graphs'][1]


@pytest.mark.parametrize('placement', ['selected', 'option'])
def test_new_tag_placement_guard_is_sensitive_before_both_shortcuts(placement):
    # Retain the full structural button/244-option/2-editable shape and remove
    # only the exact-placement refusal. A wrong extra button is not this proof.
    out = svg_observe(svgParts=placement, mutation=lambda s: s.replace(
        "if (newTag && (!svg || (!iconPlacement && !clearPlacement))) return false;", ''))
    assert out['page']['inventory_verified']
    assert out['graphs'][0] == out['graphs'][1]
