"""Offline geometry contract for the literal production fixed DOM witness.

Node evaluates the imported JavaScript against synthetic DOM objects; it does
not start Chromium, a socket, BrowserSkill, or an application.
"""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms

URL = 'https://example.test/jobs/synthetic-1'
PK = 'example-co#synthetic-1'

# Supply the exact Python-imported production witness to eval(process.argv[2]).
# DOM helpers are data fixtures, not a reimplementation of visible(e).
_NODE_DOM = r"""
const spec = JSON.parse(process.argv[1]);
global.scrollX = spec.scrollX;
global.scrollY = spec.scrollY;
global.location = {href: spec.url};
global.CSS = {escape: value => value};
const byId = new Map();

function makeNode(tag, data, parent = null) {
  const node = {
    id: data.id || '', tagName: tag.toUpperCase(), nodeType: 1,
    parentElement: parent, children: [], form: null,
    type: data.type || (tag === 'input' ? 'text' : ''),
    disabled: !!data.disabled, readOnly: !!data.readonly,
    inert: !!data.inert, innerText: data.text || '',
    value: data.value || '', name: data.name || '',
    style: data.style || {}, labels: [],
    options: (data.options || []).map(option => ({
      value: option.value, textContent: option.label
    })),
  };
  node.getClientRects = () => data.rects === undefined ?
    [{width: 120, height: 24, right: 120, bottom: 60}] : data.rects;
  node.getAttribute = key => {
    if (key === 'type') return tag === 'input' || tag === 'button' ? node.type : null;
    if (key === 'role') return data.role || null;
    if (key === 'aria-labelledby') return node.ariaId || null;
    if (key === 'aria-disabled') return data.ariaDisabled ? 'true' : null;
    if (key === 'formaction') return null;
    return null;
  };
  node.hasAttribute = key => key === 'readonly' && !!data.readonly;
  node.matches = key => key === ':disabled' && !!data.disabled;
  node.closest = key => {
    for (let parent = node; parent; parent = parent.parentElement) {
      if (key === 'form' && parent.tagName === 'FORM') return parent;
      if (key === '[inert]' && parent.inert) return parent;
    }
    return null;
  };
  return node;
}

const form = makeNode('form', {id: 'application', style: spec.formStyle || {}});
form.action = spec.url;
byId.set(form.id, form);
const controls = [];
for (const data of spec.controls) {
  const node = makeNode(data.tag || 'input', data, form);
  node.form = form;
  form.children.push(node);
  controls.push(node);
  byId.set(node.id, node);
  if (data.caption) {
    const caption = makeNode('label', {
      id: 'caption-' + data.id,
      text: data.caption.text,
      style: data.caption.style || {},
      rects: data.caption.rects,
    }, form);
    form.children.push(caption);
    byId.set(caption.id, caption);
    if (data.caption.mode === 'aria') node.ariaId = caption.id;
    else node.labels = [caption];
  }
}
global.document = {
  querySelectorAll: query => query.startsWith('#') ?
    (byId.has(query.slice(1)) ? [byId.get(query.slice(1))] : []) :
    query === '.iti' ? [] : query === '[role="combobox"]' ?
    controls.filter(n=>n.getAttribute('role')==='combobox') : controls,
  getElementById: id => byId.get(id) || null,
};
global.getComputedStyle = node => Object.assign({
  display: 'block', visibility: 'visible', opacity: '1', position: 'static'
}, node.style);
console.log(JSON.stringify(eval(process.argv[2])));
"""


def rect(width=120, height=24, right=120, bottom=60):
    return {'width': width, 'height': height, 'right': right, 'bottom': bottom}


def evaluate(*specs, scroll_x=0, scroll_y=0, form_style=None):
    payload = {'url': URL, 'controls': specs, 'scrollX': scroll_x,
               'scrollY': scroll_y, 'formStyle': form_style or {}}
    run = subprocess.run(
        ['node', '-e', _NODE_DOM, json.dumps(payload), forms._FIXED_CONTROL_WITNESS],
        text=True, capture_output=True, check=True,
    )
    return json.loads(run.stdout)


@pytest.mark.parametrize('rects,style,ancestor,scroll_x,scroll_y,expected', [
    pytest.param([rect()], {}, {}, 0, 0, True, id='normal'),
    pytest.param([rect(width=0)], {}, {}, 0, 0, False, id='zero-width'),
    pytest.param([rect(height=0)], {}, {}, 0, 0, False, id='zero-height'),
    pytest.param([rect(width=0, height=0)], {}, {}, 0, 0, False, id='zero-by-zero'),
    pytest.param([rect(width=0), rect(height=0)], {}, {}, 0, 0, False,
                 id='different-rects-cannot-combine'),
    pytest.param([rect(width=0), rect(height=0), rect()], {}, {}, 0, 0, True,
                 id='one-same-qualifying-rect'),
    pytest.param([rect(right=-1)], {}, {}, 0, 0, False, id='left-of-page'),
    pytest.param([rect(bottom=0)], {}, {}, 0, 0, False, id='above-page'),
    pytest.param([rect(right=1, bottom=2)], {}, {}, 0, 0, True, id='partial-origin'),
    pytest.param([rect(right=-5850)], {'position': 'fixed'}, {}, 0, 0, False,
                 id='fixed-left-non-scrolling'),
    pytest.param([rect(bottom=-10)], {'position': 'fixed'}, {}, 0, 100, False,
                 id='fixed-self-scrolled-negative-top'),
    pytest.param([rect(right=-5850)], {}, {'position': 'fixed'}, 7000, 0, False,
                 id='fixed-ancestor-scrolled-negative-left'),
    pytest.param([rect(right=120, bottom=85)], {}, {}, 200, 500, True,
                 id='nonfixed-in-document-scrolled'),
    pytest.param([rect(bottom=-50)], {}, {}, 0, 500, True,
                 id='nonfixed-above-viewport-still-on-page'),
    pytest.param([rect(bottom=1600)], {}, {}, 0, 0, True,
                 id='scrollable-below-fold'),
    pytest.param([rect()], {}, {'position': 'fixed'}, 0, 500, True,
                 id='in-view-fixed-ancestor-scrolled-document'),
    pytest.param([rect(bottom=-10)], {}, {'position': 'fixed'}, 0, 500, False,
                 id='fixed-dialog-internal-scroll-availability-loss'),
    pytest.param([rect(right=-9900)], {'position': 'absolute'}, {}, 0, 0, False,
                 id='native-off-origin-custom-choice-availability-loss'),
    pytest.param([rect(right=6000, bottom=6000)], {'position': 'fixed'}, {},
                 0, 0, True, id='positive-side-off-canvas-residual'),
])
def test_exact_production_geometry(rects, style, ancestor, scroll_x, scroll_y,
                                   expected):
    result = evaluate({'id': 'field', 'rects': rects, 'style': style},
                      scroll_x=scroll_x, scroll_y=scroll_y, form_style=ancestor)
    assert [c['selector'] for c in result['controls']] == ['#field']
    assert result['controls'][0]['visible'] is expected


@pytest.mark.parametrize('style,ancestor,expected', [
    ({'display': 'none'}, {}, False),
    ({'visibility': 'hidden'}, {}, False),
    ({'visibility': 'collapse'}, {}, False),
    ({'opacity': '0'}, {}, False),
    ({}, {'display': 'none'}, False),
    ({}, {'opacity': '0'}, False),
    ({}, {}, True),
])
def test_css_and_ancestor_visibility_rules_remain(style, ancestor, expected):
    observed = evaluate({'id': 'field', 'style': style}, form_style=ancestor)
    assert observed['controls'][0]['visible'] is expected


@pytest.mark.parametrize('native,flag', [
    ({'readonly': True}, 'readonly'),
    ({'disabled': True}, 'disabled'),
    ({'inert': True}, 'inert'),
    ({'ariaDisabled': True}, 'disabled'),
])
def test_native_state_is_reported_independently_of_geometry(native, flag):
    observed = evaluate({'id': 'field', **native})['controls'][0]
    assert observed['visible'] is True
    assert observed[flag] is True


@pytest.mark.parametrize('mode,caption_rects,expected', [
    ('labels', [rect()], 'Newsletter'),
    ('aria', [rect()], 'Newsletter'),
    ('labels', [rect(width=0, height=0)], ''),
    ('aria', [rect(right=-10)], ''),
    ('labels', [rect(bottom=-1)], ''),
])
def test_native_choice_caption_uses_same_literal_visible_predicate(
        mode, caption_rects, expected):
    result = evaluate({'id': 'choice', 'type': 'checkbox',
                       'caption': {'text': 'Newsletter', 'mode': mode,
                                   'rects': caption_rects}})
    assert result['controls'][0]['visible'] is True
    assert result['controls'][0]['visible_text'] == expected
