"""Production readers independently classify the same synthetic nodes, never a browser.

The old strict witness rejected two owner graphs under opacity/geometry contrasts,
leaving their two required proxies in its inventory despite the existing filter.
Graph recognition is now shared; actionable label/display evidence stays strict.
"""
import copy
from tests.tools.reader_node_harness import reader_output
import json
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_closed_widgets import Session, choose, dom, grants


def instrument(script):
    return script.replace('const pickerParts = new Set();', '''
      global.__graphs.push({graphs:Array.from(newGraphs,([e,g])=>[selector(e),g]),
        proxies:Array.from(requiredProxies,selector), quarantined:Array.from(quarantined,selector)});
      const pickerParts = new Set();''')


def observe(kind='greenhouse', *, old=False, **scenario):
    readers = ((forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS) if kind == 'entry'
               else (forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS))
    if old:
        readers = tuple(s.replace('!graphVisible(e)', '!visible(e)')
                        .replace('!graphVisible(L)', '!visible(L)')
                        .replace('graphVisible(n)', 'visible(n)') for s in readers)
    return json.loads(reader_output(
        'closed_widget_dom.cjs', scenario, *(instrument(s) for s in readers)))


RECT = dict(width=120, height=24, right=120, bottom=24)
CONTRASTS = [
    ('own-opacity', {'style': {'opacity': '0'}}),
    ('ancestor-opacity', {'parentStyle': {'opacity': '0'}}),
    ('own-display', {'style': {'display': 'none'}}),
    ('ancestor-display', {'parentStyle': {'display': 'none'}}),
    ('own-collapse', {'style': {'visibility': 'collapse'}}),
    ('ancestor-collapse', {'parentStyle': {'visibility': 'collapse'}}),
    ('visible-descendant', {'parentStyle': {'visibility': 'hidden'},
                            'style': {'visibility': 'visible'}}),
    ('empty-rects', {'rects': []}),
    ('zero-width', {'rects': [{**RECT, 'width': 0}]}),
    ('zero-height', {'rects': [{**RECT, 'height': 0}]}),
    ('nonpositive-edges', {'rects': [{**RECT, 'right': -20, 'bottom': -20}]}),
    ('scroll-offset', {'rects': [{**RECT, 'right': -20, 'bottom': -20}],
                       'scrollX': 100, 'scrollY': 100}),
    ('fixed-edge', {'rects': [{**RECT, 'right': -20}],
                    'parentStyle': {'position': 'fixed'}, 'scrollX': 100}),
]


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('site', ['input', 'label', 'display', 'rejected-display'])
@pytest.mark.parametrize('name,change', CONTRASTS, ids=[r[0] for r in CONTRASTS])
def test_graph_eligibility_matrix(kind, site, name, change):
    change = copy.deepcopy(change)
    offsets = {k: change.pop(k) for k in ('scrollX', 'scrollY') if k in change}
    suffix = {'input': '', 'label': '-label', 'display': '-display',
              'rejected-display': '-display'}[site]
    spec = dict(requiredProxy=True, proxyKeys=['location', 'school'],
                visibility=[dict(id=k+suffix, **change) for k in ('location', 'school')],
                **offsets)
    if site == 'rejected-display':
        spec.update(badGraph=True, wrongLabel=True, proxyKeys=['school', 'degree'],
                    visibility=[dict(id='location-display', **change)])
    out = observe(kind, **spec)
    p, w = out['page'], out['witness']
    assert out['graphs'][0] == out['graphs'][1]
    assert [c['selector'] for c in p['controls']] == [c['selector'] for c in w['controls']]
    assert [(c['selector'], c['tag'], c['type'], c['form_selector']) for c in p['controls']] == [
        (c['selector'], c['tag'], c['type'], c['form_selector']) for c in w['controls']]
    assert all(any(c['selector'] == '#filler-'+str(i) for c in p['controls']) for i in range(25))
    for pc, wc in zip(p['controls'], w['controls']):
        assert pc.get('combobox') == wc.get('combobox')
        assert 'graph_label_strict_visible' not in pc
        assert 'graph_display_strict_visible' not in pc
    # Isolate the original +2 class on the very same nodes without changing production.
    if site != 'rejected-display' and name not in {'empty-rects', 'scroll-offset'}:
        old = observe(kind, old=True, **spec)
        assert len(old['witness']['controls']) == len(old['page']['controls']) + 2
        assert len(old['graphs'][0]['proxies']) == 2
        assert old['graphs'][1]['proxies'] == []
    elif site == 'rejected-display' and name not in {'empty-rects', 'scroll-offset'}:
        old = observe(kind, old=True, **spec)
        assert '#location' in old['graphs'][0]['quarantined']
        assert '#location' not in old['graphs'][1]['quarantined']


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('mutation', [
    {'shellNested': True}, {'foreign': 'null'}, {'style': {'position': 'static'}},
    {'ownerAttr': ['aria-required', None]},
])
def test_invalid_proxy_is_preserved_not_blanket_hidden(kind, mutation):
    out = observe(kind, requiredProxy=True, proxyKeys=['location', 'school'],
                  proxyMutation={'key': 'location', **mutation})
    assert out['graphs'][0] == out['graphs'][1]
    assert len(out['graphs'][0]['proxies']) == 1
    assert [c['selector'] for c in out['page']['controls']] == [c['selector'] for c in out['witness']['controls']]


@pytest.mark.parametrize('part', ['location', 'location-label', 'location-display'])
@pytest.mark.parametrize('change', [dict(style={'opacity': '0'}), dict(style={'visibility': 'collapse'}),
                                  dict(rects=[{**RECT, 'width': 0}]),
                                  dict(rects=[{**RECT, 'right': -20}], parentStyle={'position': 'fixed'}, scrollX=100)])
async def test_graph_parity_never_grants_strict_invisible_evidence(grants, part, change):
    change = copy.deepcopy(change)
    offsets = {k: change.pop(k) for k in ('scrollX', 'scrollY') if k in change}
    session = Session(visibility=[dict(id=part, **change)], **offsets)
    p, w = session.snapshot['page'], session.snapshot['witness']
    target = next(c for c in p['controls'] if c['selector'] == '#location')
    evidence = next(c for c in w['controls'] if c['selector'] == '#location')
    assert target['combobox'] == evidence['combobox']
    if part != 'location':
        assert evidence['visible'] is True
        key = 'graph_label_strict_visible' if part.endswith('label') else 'graph_display_strict_visible'
        assert evidence[key] is False
    with pytest.raises(forms.Gate, match='visibly editable'):
        forms._disclosure_question(p, target, {c['selector']: c for c in w['controls']}, p['url'])
    with pytest.raises(forms.Gate):
        await choose(session, grants)
    assert session.calls == []


@pytest.mark.parametrize('flag', ['graph_label_strict_visible', 'graph_display_strict_visible'])
@pytest.mark.parametrize('value', [None, False, 1, 'true'])
def test_missing_or_nonboolean_strict_evidence_never_discloses(flag, value):
    session = Session()
    p, w = session.snapshot['page'], session.snapshot['witness']
    target = next(c for c in p['controls'] if c['selector'] == '#location')
    lookup = {c['selector']: c for c in w['controls']}
    lookup['#location'][flag] = value
    with pytest.raises(forms.Gate, match='visibly editable'):
        forms._disclosure_question(p, target, lookup, p['url'])


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
@pytest.mark.parametrize('part', ['location-label', 'location-display'])
@pytest.mark.parametrize('change', [dict(style={'opacity': '0'}), dict(style={'visibility': 'collapse'}),
                                  dict(rects=[{**RECT, 'height': 0}]),
                                  dict(rects=[{**RECT, 'right': -20}], parentStyle={'position': 'fixed'})])
async def test_every_phase_refuses_strict_evidence_before_commit_or_submit(grants, phase, part, change):
    class Changing(Session):
        async def call(self, *args):
            result = await super().call(*args)
            if args[0] == 'click' and self.phases.get('location') == phase:
                self.snapshot = dom(
                    phases=self.phases, visibility=[dict(id=part, **change)], scrollX=100)
            return result
    session = Changing(**({'visibility': [dict(id=part, **change)], 'scrollX': 100}
                          if phase == 'C0' else {}))
    with pytest.raises(forms.Gate, match='visibly editable'):
        await choose(session, grants)
    assert len(session.calls) == {'C0': 0, 'C1': 1, 'C2': 2}[phase]
    # A fresh pre-submit observation must refuse as well, independently of the receipt.
    with pytest.raises(forms.Gate, match='visibly editable'):
        await forms._observe_controls(session, await session.page())


@pytest.mark.parametrize('flag', ['graph_label_strict_visible', 'graph_display_strict_visible'])
async def test_strict_flag_guard_removal_is_detected(monkeypatch, flag):
    session = Session()
    evidence = next(c for c in session.snapshot['witness']['controls'] if c['selector'] == '#location')
    evidence[flag] = False
    with pytest.raises(forms.Gate, match='visibly editable'):
        await forms._observe_controls(session, await session.page())
    # Remove only the refusal in memory: the same witness is otherwise admissible.
    monkeypatch.setattr(forms, '_require_graph_visibility', lambda *args: None)
    page, lookup = await forms._observe_controls(session, await session.page())
    target = next(c for c in page['controls'] if c['selector'] == '#location')
    assert forms._disclosure_question(page, target, lookup, page['url'])['selector'] == '#location'
