"""The blank box is visible; its empty display need not occupy vertical space.

Execute PAGE and the independent fixed witness, never a browser or network.
"""
import copy
import json
import re
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms, browser_skill_fixture as fixture
from tests.tools.test_browser_closed_widgets import Session, choose, grants
from tests.tools.test_browser_greenhouse_fixture import Tree
from tests.tools.test_browser_greenhouse_parity import instrument

RECT = dict(width=120, height=24, right=120, bottom=24)
ZERO = {**RECT, 'height': 0}


def observe(*, changes=(), optional=False, original=False):
    markup = fixture._greenhouse_form().decode()
    markup = re.sub(r'</form>.*?<script>', '</form><script>', markup, flags=re.S)
    markup = markup.replace('<template id="greenhouse-template">', '').replace('</template>', '')
    tree = Tree()
    tree.feed(markup)
    def prepare(node):
        if node['attrs'].get('id') == 'location-value':
            node['children'][-1]['attrs']['id'] = 'location-display'
        if optional:
            node['children'] = [c for c in node['children']
                                if c['attrs'].get('class') != 'validation-only']
        for child in node['children']:
            prepare(child)
    prepare(tree.root)
    changes = list(changes)
    if not original:
        changes.insert(0, dict(id='location-display', text='', rects=[ZERO]))
    if optional:
        # Only this offline tree has an optional graph; the fixture remains required.
        changes.append(dict(id='location', attrs={'aria-required': None}))
    return execute(dict(fixtureTree=tree.root, fixtureUrl=fixture.URL, visibility=changes))


def execute(scenario):
    witness = instrument(forms._GREENHOUSE_FIXED_CONTROL_WITNESS).replace(
        'const pickerParts = new Set();', '''
        global.__graphs[1].displayProof = Array.from(newGraphs).filter(([e,g])=>g).map(([e,g])=>{
          const d=graphNodes.get(e).display;
          return [selector(e), {original:visible(d), text:clean(d.innerText), rects:Array.from(d.getClientRects())}];
        });
        const pickerParts = new Set();''')
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')), json.dumps(scenario),
        instrument(forms.GREENHOUSE_PAGE), witness,
    ], text=True))


def evidence(out, selector='#location'):
    page, witness = out['page'], out['witness']
    assert out['graphs'][0] == {k: v for k, v in out['graphs'][1].items() if k != 'displayProof'}
    assert [c['selector'] for c in page['controls']] == [c['selector'] for c in witness['controls']]
    target = next(c for c in page['controls'] if c['selector'] == selector)
    strict = next(c for c in witness['controls'] if c['selector'] == selector)
    return target, strict


class ObservedSession:
    def __init__(self, out):
        self.snapshot, self.calls = out, []

    async def page(self):
        return copy.deepcopy(self.snapshot['page'])

    async def call(self, command, script):
        assert command == 'evaluate' and script == forms._FIXED_CONTROL_WITNESS
        return {'value': copy.deepcopy(self.snapshot['witness'])}


async def refuses(out):
    target, strict = evidence(out)
    assert strict['graph_display_strict_visible'] is False
    session = ObservedSession(out)
    with pytest.raises(forms.Gate, match='visibly editable'):
        await forms._observe_controls(session, await session.page())
    with pytest.raises(forms.Gate, match='visibly editable'):
        forms._disclosure_question(out['page'], target,
                                   {c['selector']: c for c in out['witness']['controls']}, fixture.URL)
    assert session.calls == []
    assert not any(c[0] == 'submit' for c in session.calls)


async def test_greenhouse_empty_zero_height_placeholder_visible_v_passes():
    out = observe()
    target, strict = evidence(out)
    assert target['required'] is True
    assert target['combobox']['phase'] == 'C0' and target['combobox']['displayed_text'] == ''
    assert strict['graph_label_strict_visible'] is True
    assert strict['graph_display_strict_visible'] is True
    proof = dict(out['graphs'][1]['displayProof'])['#location']
    assert proof == dict(original=False, text='', rects=[ZERO])
    # Paired nonempty test has the identical zero-height geometry and strict false.
    assert evidence(observe(changes=[dict(id='location-display', text='Choose', rects=[ZERO])]))[1]['graph_display_strict_visible'] is False
    session = ObservedSession(out)
    page, lookup = await forms._observe_controls(session, await session.page())
    additional = next(c for c in page['controls'] if c['selector'] == '#additional')
    question = forms._disclosure_question(page, additional, lookup, fixture.URL)
    assert question['question'] == 'Additional question'
    assert session.calls == []


async def test_greenhouse_empty_zero_height_placeholder_zero_size_v_gates():
    await refuses(observe(changes=[dict(id='location-value', rects=[{**RECT, 'width': 0, 'height': 0}])]))


async def test_greenhouse_nonempty_zero_height_placeholder_gates():
    await refuses(observe(changes=[dict(id='location-display', text='Choose', rects=[ZERO])]))


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_greenhouse_zero_height_single_value_gates(grants, ambiguous):
    class Selecting(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args[0] == 'click' and self.phases.get('location') == 'C2':
                change = dict(id='location-display', rects=[ZERO])
                if ambiguous:
                    change['attrs'] = {'class': 'react-select__single-value react-select__placeholder'}
                self.snapshot = execute(dict(phases=self.phases, visibility=[change]))
            return result
    session = Selecting()
    with pytest.raises(forms.Gate, match='visibly editable'):
        await choose(session, grants)
    assert session.phases['location'] == 'C2'
    assert session.calls == [('click', '#location'), ('click', '#location-option')]
    assert evidence(session.snapshot)[1]['graph_display_strict_visible'] is False
    before = list(session.calls)
    with pytest.raises(forms.Gate, match='visibly editable'):
        await forms._observe_controls(session, await session.page())
    assert session.calls == before  # Count setup IPC; none after failed readback.
    assert not any(c[0] == 'submit' for c in session.calls)


def test_greenhouse_required_text_placeholder_still_passes():
    out = observe(original=True)
    target, strict = evidence(out)
    assert target['required'] is True
    assert dict(out['graphs'][1]['displayProof'])['#location']['text'] == 'Choose'
    assert dict(out['graphs'][1]['displayProof'])['#location']['original'] is True
    assert strict['graph_display_strict_visible'] is True


async def test_greenhouse_optional_native_control_still_passes():
    out = observe(original=True)
    target, strict = evidence(out, '#additional')
    assert target['tag'] == 'textarea' and target['required'] is False
    assert 'graph_display_strict_visible' not in strict and strict['visible'] is True
    session = ObservedSession(out)
    page, lookup = await forms._observe_controls(session, await session.page())
    target = next(c for c in page['controls'] if c['selector'] == '#additional')
    assert forms._disclosure_question(page, target, lookup, fixture.URL)['question'] == 'Additional question'


def test_greenhouse_optional_text_placeholder_still_passes():
    out = observe(original=True, optional=True)
    target, strict = evidence(out)
    assert target['required'] is False
    assert dict(out['graphs'][1]['displayProof'])['#location']['text'] == 'Choose'
    assert dict(out['graphs'][1]['displayProof'])['#location']['original'] is True
    assert strict['graph_display_strict_visible'] is True


async def test_greenhouse_non_strict_label_gates():
    out = observe(changes=[dict(id='location-label', style={'opacity': '0'})])
    assert evidence(out)[1]['graph_label_strict_visible'] is False
    await refuses(out)


async def test_greenhouse_positive_height_opacity_zero_placeholder_gates():
    await refuses(observe(changes=[dict(id='location-display', text='', rects=[RECT], style={'opacity': '0'})]))


@pytest.mark.parametrize('rects', [[], [{**ZERO, 'width': 0}], [ZERO, RECT], [{**ZERO, 'height': -1}]])
async def test_placeholder_geometry_is_exact_and_complete(rects):
    out = observe(changes=[dict(id='location-display', text='', rects=rects, style={'opacity': '0'})])
    if not rects:
        # No client rect is already excluded by the unchanged graph recognizer.
        assert dict(out['graphs'][0]['graphs'])['#location'] is None
    else:
        await refuses(out)
