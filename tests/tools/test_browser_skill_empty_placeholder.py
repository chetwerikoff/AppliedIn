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
from tests.tools.test_browser_greenhouse_parity import assert_unapproved_submit_is_refused

RECT = dict(width=120, height=24, right=120, bottom=24)
ZERO = {**RECT, 'height': 0}
# Issue 256 admits the fixture's unrendered .iti phone picker, so the opaque
# picker no longer stops Submit first. The required Location placeholder must
# now be refused itself, by the same single final guard.
LOCATION_REFUSAL = dict(require_incomplete=False,
                        reason='Unverified or changed combobox value: Where are you located?')


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
    # The r06 readers expose accessible controls, not display-geometry receipts.
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')), json.dumps(scenario),
        forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS,
    ], text=True))


def evidence(out, selector='#location'):
    page, witness = out['page'], out['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    assert [(c['selector'], c['tag'], c['type'], c['form_selector'])
            for c in page['controls']] == [
        (c['selector'], c['tag'], c['type'], c['form_selector'])
        for c in witness['controls']]
    for original, independent in zip(page['controls'], witness['controls']):
        assert original.get('combobox') == independent.get('combobox')
    target = next(c for c in page['controls'] if c['selector'] == selector)
    independent = next(c for c in witness['controls'] if c['selector'] == selector)
    return target, independent


class ObservedSession:
    def __init__(self, out):
        self.snapshot, self.calls = out, []

    async def page(self):
        return copy.deepcopy(self.snapshot['page'])

    async def call(self, command, script):
        assert command == 'evaluate' and script == forms._FIXED_CONTROL_WITNESS
        return {'value': copy.deepcopy(self.snapshot['witness'])}


async def refuses(out, monkeypatch, tmp_path):
    target, independent = evidence(out)
    assert target['required'] is True
    assert independent['type'] == 'combobox' and independent['combobox']['linked'] is False
    assert independent['combobox']['selected_value'] == ''
    assert independent['combobox']['selected_text'] == ''
    # A collapsed or transparent sibling display is not proof of a submitted
    # value. Reobserve safely, then deny at the single production Submit seam.
    session = ObservedSession(out)
    observed, lookup = await forms._observe_controls(session, await session.page())
    assert observed['url'] == out['page']['url']
    assert lookup['#location']['combobox'] == target['combobox']
    assert session.calls == []
    await assert_unapproved_submit_is_refused(out, monkeypatch, tmp_path, **LOCATION_REFUSAL)


async def test_greenhouse_empty_zero_height_placeholder_has_safe_generic_read(
        monkeypatch, tmp_path):
    out = observe()
    target, independent = evidence(out)
    assert target['required'] is True and independent['visible'] is True
    assert target['combobox']['displayed_text'] == ''
    assert target['combobox']['selected_value'] == ''
    assert independent['visible_text']
    # A blank zero-height sibling is neither a committed answer nor a reason
    # to lose the independently inspectable native textarea.
    session = ObservedSession(out)
    page, lookup = await forms._observe_controls(session, await session.page())
    additional = next(c for c in page['controls'] if c['selector'] == '#additional')
    assert forms._native_answer(additional)
    assert additional['required'] is False
    assert additional['label'] == 'Additional question'
    assert lookup['#additional']['visible'] is True
    assert session.calls == []
    await assert_unapproved_submit_is_refused(out, monkeypatch, tmp_path, **LOCATION_REFUSAL)


async def test_greenhouse_empty_zero_height_placeholder_zero_size_denies_submit(
        monkeypatch, tmp_path):
    await refuses(observe(changes=[dict(
        id='location-value', rects=[{**RECT, 'width': 0, 'height': 0}])]),
        monkeypatch, tmp_path)


async def test_greenhouse_nonempty_zero_height_placeholder_denies_submit(
        monkeypatch, tmp_path):
    await refuses(observe(changes=[dict(
        id='location-display', text='Choose', rects=[ZERO])]), monkeypatch, tmp_path)


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_greenhouse_zero_height_single_value_gates(
        grants, ambiguous, monkeypatch, tmp_path):
    class Selecting(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args[0] == 'click' and self.phases.get('location') == 'C2':
                change = dict(id='location-display', rects=[ZERO])
                if ambiguous:
                    change['attrs'] = {'class': 'react-select__single-value react-select__placeholder'}
                self.snapshot = execute(dict(phases=self.phases, visibility=[change]))
            return result
    session, filled = Selecting(), {}
    receipt = await choose(session, grants, filled=filled)
    # A zero-height/ambiguous sibling display no longer blocks reversible
    # ARIA option preparation. That visual shape also cannot prove an approved
    # form-owned submitted value at the ONE final Submit boundary.
    assert session.phases['location'] == 'C2'
    assert receipt['action'] == 'combobox' and receipt['committed_value'] == grants['location']
    assert filled['#location'] == grants['location']
    assert session.calls == [
        ('click', '#location'),
        ('fill', '#location', '--value', grants['location']),
        ('click', '#location-option'),
    ]
    target, independent = evidence(session.snapshot)
    assert target['combobox'] == independent['combobox']
    # A typed accessible value and a visual single-value decoration are not
    # proof of a committed option after this zero-height DOM transition.
    assert target['combobox']['selected_text'] == ''
    assert target['combobox']['selected_value'] == ''
    assert not target.get('name')  # No native submitted receiver from decoration.
    before = list(session.calls)
    current, witness = await forms._observe_controls(session, await session.page())
    assert current['controls'] and witness['#location']['combobox'] == target['combobox']
    assert session.calls == before
    with pytest.raises(forms.Gate):
        forms.check_form(current, filled, True, receipts=[receipt])
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert not any(c[0] == 'submit' for c in session.calls)


async def test_greenhouse_required_text_placeholder_keeps_required_field(
        monkeypatch, tmp_path):
    out = observe(original=True)
    target, independent = evidence(out)
    assert target['required'] is True
    assert target['type'] == independent['type'] == 'combobox'
    assert independent['visible'] is True and independent['visible_text']
    assert target['combobox']['selected_value'] == ''
    assert target['combobox']['linked'] is False
    # A readable placeholder, including 'Choose', is not an approved value.
    await assert_unapproved_submit_is_refused(out, monkeypatch, tmp_path, **LOCATION_REFUSAL)


async def test_greenhouse_optional_native_control_still_passes():
    out = observe(original=True)
    target, independent = evidence(out, '#additional')
    assert target['tag'] == 'textarea' and target['required'] is False
    assert target['label'] == 'Additional question'
    assert independent['visible'] is True and independent['readonly'] is False
    assert forms._native_answer(target)
    session = ObservedSession(out)
    page, lookup = await forms._observe_controls(session, await session.page())
    additional = next(c for c in page['controls'] if c['selector'] == '#additional')
    assert additional['label'] == 'Additional question'
    assert forms._native_answer(additional) and lookup['#additional']['visible'] is True
    assert session.calls == []


def test_greenhouse_optional_text_placeholder_still_passes():
    out = observe(original=True, optional=True)
    target, independent = evidence(out)
    assert target['required'] is False
    assert target['type'] == 'combobox' and independent['visible'] is True
    assert independent['visible_text']
    # An optional, unselected picker stays optional; the visible caption of
    # a sibling placeholder cannot be converted to a submitted value.
    assert target['combobox']['selected_value'] == ''
    assert target['combobox']['selected_text'] == ''
    assert target['combobox']['options'] == []


async def test_greenhouse_hidden_label_denies_submit(monkeypatch, tmp_path):
    out = observe(changes=[dict(id='location-label', style={'opacity': '0'})])
    target, independent = evidence(out)
    assert target['label']  # Text exists in DOM, but is not visibly evidenced.
    assert independent['visible_text'] == ''
    await refuses(out, monkeypatch, tmp_path)


async def test_greenhouse_positive_height_opacity_zero_placeholder_denies_submit(
        monkeypatch, tmp_path):
    out = observe(changes=[dict(
        id='location-display', text='', rects=[RECT], style={'opacity': '0'})])
    assert evidence(out)[1]['visible'] is True  # Input, not sibling display.
    await refuses(out, monkeypatch, tmp_path)


@pytest.mark.parametrize('rects', [[], [{**ZERO, 'width': 0}], [ZERO, RECT], [{**ZERO, 'height': -1}]])
async def test_placeholder_geometry_cannot_invent_answer_or_submit(
        rects, monkeypatch, tmp_path):
    out = observe(changes=[dict(
        id='location-display', text='', rects=rects, style={'opacity': '0'})])
    target, independent = evidence(out)
    # The generic reader has no shape-certificate exemption for missing,
    # multiple, zero or negative sibling rectangles. None fills the field.
    assert target['required'] is True and independent['visible'] is True
    assert target['combobox']['selected_value'] == ''
    assert target['combobox']['displayed_text'] == ''
    await refuses(out, monkeypatch, tmp_path)
