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


def control(label, selector, *, tag='input', type='text', **changes):
    return {
        'selector': selector, 'label': label, 'question': '',
        'tag': tag, 'type': type, 'required': True,
        'disabled': False, 'readonly': False, 'in_form': True,
        'form_selector': '#application', 'form_action': URL,
        'native_form_owner_selector': '#application',
        'formaction': '', 'submit': False, 'checked': False, 'has_value': False,
        'value': '', 'options': [], **changes,
    }


def page(*controls):
    marker = control('Submit application', '#submit', tag='button', type='submit',
                     required=False, submit=True)
    return {
        'url': URL, 'controls': [*controls, marker], 'inventory_verified': True,
        'truncated': False, 'opaque_controls': False, 'shadow_roots': False,
        'unsupported_frames': [],
    }


def production_observation(current, *, overrides=None, scroll_x=0, scroll_y=0):
    overrides = overrides or {}
    specs = []
    for c in current['controls']:
        spec = {'id': c['selector'][1:], 'tag': c['tag'], 'type': c['type'],
                'disabled': c['disabled'], 'options': c.get('options', [])}
        if c['type'] in {'checkbox', 'radio'}:
            spec['caption'] = {'text': c['label']}
        spec.update(overrides.get(c['selector'], {}))
        specs.append(spec)
    return evaluate(*specs, scroll_x=scroll_x, scroll_y=scroll_y)


class Session:
    """The only accepted transport is read-only evaluate with a fixed JS payload."""

    def __init__(self, current, *observations):
        self.current = current
        self.observations = [deepcopy(o) for o in observations]
        self.calls = []

    async def page(self):
        return deepcopy(self.current)

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] != 'evaluate':
            pytest.fail('A synthetic BrowserSkill mutation was attempted')
        assert args[1] == forms._FIXED_CONTROL_WITNESS
        assert self.observations, 'Unexpected extra witness evaluation'
        return {'value': self.observations.pop(0)}


def no_mutation(session):
    assert all(call[0] == 'evaluate' for call in session.calls)


def grant(c, value):
    approved = {
        'value': value, 'selector': c['selector'], 'url': URL,
        'label': c['label'], 'question': c['question'], 'tag': c['tag'],
        'type': c['type'], 'form_selector': c['form_selector'],
        'form_action': c['form_action'],
    }
    if c['tag'] == 'select':
        approved['options'] = deepcopy(c['options'])
    if c['type'] == 'checkbox':
        approved['visible_text'] = c['label']
    return approved


def row_with_grant(c, value):
    return {'pk': PK, 'status': 'submitting', 'possible_submission': False,
            'human_approved_answers': {c['label']: grant(c, value)}}


async def execute(session, current, c, verb, value):
    return await forms.execute(
        session, current, {'action': verb, 'selector': c['selector'],
                           'fact': c['label']},
        facts={c['label']: value}, filled={}, resume_path='',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=PK, job_url=URL,
    )


@pytest.mark.parametrize('hidden_rects', [
    [rect(width=0, height=0)],
    [rect(right=-5850)],
    [rect(height=0)],
])
async def test_bootstrap_skips_invisible_selector_but_can_offer_visible_one(
        monkeypatch, hidden_rects):
    hidden = control('Hidden field', '#hidden')
    shown = control('Visible field', '#visible')
    current = page(hidden, shown)
    result = production_observation(
        current, overrides={'#hidden': {'rects': hidden_rects}})
    assert result['controls'][0]['visible'] is False
    assert result['controls'][1]['visible'] is True
    session = Session(current, result)
    monkeypatch.setattr(forms, '_row', lambda pk: {'pk': pk, 'human_approved_answers': {}})
    prepared = await forms._bootstrap(session, current, PK, {}, URL)
    # A refreshed, independently witnessed page is a new snapshot, not the
    # original Python object. Its contents, not object identity, must match.
    assert prepared == current
    fixed = {c['selector']: c for c in result['controls']}
    # Independent witnessing must still refuse the invisible receiver; the
    # visible control remains identifiable without an early ordinary gate.
    with pytest.raises(forms.Gate, match='not visibly editable'):
        forms._disclosure_question(current, hidden, fixed, URL)
    question = forms._disclosure_question(current, shown, fixed, URL)
    assert question['selector'] == shown['selector']
    assert question['selector'] in {
        c['selector'] for c in result['controls'] if c['visible'] is True
    }
    with pytest.raises(forms.Gate, match='Required field is empty: Hidden field'):
        forms.check_form(prepared, {}, uploaded=True)
    no_mutation(session)


@pytest.mark.parametrize('tag,kind,verb,value', [
    ('input', 'text', 'fill', 'Test User'),
    ('select', 'select', 'select', 'Remote'),
    ('input', 'checkbox', 'choose', 'yes'),
])
async def test_old_grant_does_not_authorize_now_invisible_write(
        monkeypatch, tag, kind, verb, value):
    options = [{'value': 'remote', 'label': 'Remote'}] if kind == 'select' else []
    c = control('Location' if kind == 'select' else
                'Newsletter' if kind == 'checkbox' else 'Name',
                '#target', tag=tag, type=kind, options=options)
    current = page(c)
    previously_visible = production_observation(current)
    assert previously_visible['controls'][0]['visible'] is True
    witness = {item['selector']: item for item in previously_visible['controls']}
    assert forms._disclosure_question(current, c, witness, URL)['selector'] == c['selector']

    evidence = production_observation(
        current, overrides={'#target': {'rects': [rect(width=0)]}})
    assert evidence['controls'][0]['visible'] is False
    session = Session(current, evidence)
    monkeypatch.setattr(forms, '_row', lambda pk: row_with_grant(c, value))
    # The grant was genuinely usable before the control became invisible.
    # Otherwise an unrelated approval mismatch could falsely satisfy this Gate.
    forms.exact_approval(PK, c['label'], value,
                         target=forms._bound_target(current, c), url=URL)
    with pytest.raises(forms.Gate) as raised:
        await execute(session, current, c, verb, value)
    assert raised.value.form_question is None
    assert [call[0] for call in session.calls] == ['evaluate']
    no_mutation(session)


async def test_second_witness_prevents_write_after_visible_to_invisible_change(monkeypatch):
    c = control('Name', '#name', required=False)
    current = page(c)
    once_visible = production_observation(current)
    now_hidden = production_observation(
        current, overrides={'#name': {'rects': [rect(right=-5)]}})
    session = Session(current, once_visible, now_hidden)
    monkeypatch.setattr(forms, '_row', lambda pk: row_with_grant(c, 'Test User'))
    with pytest.raises(forms.Gate) as raised:
        await execute(session, current, c, 'fill', 'Test User')
    assert raised.value.form_question is None
    assert [call[0] for call in session.calls] == ['evaluate', 'evaluate']
    no_mutation(session)


async def test_committing_fixed_witness_cannot_be_normalized_to_an_absent_page_flag(monkeypatch):
    c = control('Name', '#name', required=False)
    current = page(c)
    fixed = production_observation(current)
    assert fixed['controls'][0]['commits_application'] is False
    fixed['controls'][0]['commits_application'] = True
    session = Session(current, fixed)
    monkeypatch.setattr(forms, '_row', lambda pk: row_with_grant(c, 'Test User'))
    with pytest.raises(forms.Gate, match='predicate=commits_application.equal'):
        await execute(session, current, c, 'fill', 'Test User')
    assert [call[0] for call in session.calls] == ['evaluate']
    no_mutation(session)


async def test_unrelated_excluded_caption_gates_even_granted_visible_text_fill(
        monkeypatch):
    c = control('Name', '#name', required=False)
    unrelated = control('Newsletter', '#newsletter', type='checkbox', required=False)
    current = page(c, unrelated)
    result = production_observation(current, overrides={'#newsletter': {
        'caption': {'text': 'Newsletter', 'mode': 'aria',
                    'rects': [rect(width=0, height=0)]}}})
    assert result['controls'][0]['visible'] is True
    assert result['controls'][1]['visible'] is True
    assert result['controls'][1]['visible_text'] == ''
    session = Session(current, result)
    monkeypatch.setattr(forms, '_row', lambda pk: row_with_grant(c, 'Test User'))
    with pytest.raises(forms.Gate) as raised:
        await execute(session, current, c, 'fill', 'Test User')
    assert raised.value.form_question is None  # Whole-page generic identity Gate.
    no_mutation(session)


async def test_order_and_native_select_option_provenance_still_gate():
    c = control('Location', '#location', tag='select', type='select',
                options=[{'value': 'remote', 'label': 'Remote'},
                         {'value': 'office', 'label': 'On site'}])
    current = page(c)
    valid = production_observation(current)
    assert valid['controls'][0]['options'] == c['options']
    reordered = deepcopy(valid)
    reordered['controls'][0]['options'].reverse()
    session = Session(current, reordered)
    with pytest.raises(forms.Gate, match='witness'):
        await forms._observe_controls(session, current)
    no_mutation(session)


@pytest.mark.parametrize('native', [
    {'readonly': True},
    {'inert': True},
    {'disabled': True},
])
def test_native_non_geometry_guards_still_refuse_questions(native):
    c = control('Name', '#name')
    current = page(c)
    observed = production_observation(current, overrides={'#name': native})
    dom = {c['selector']: c for c in observed['controls']}
    assert observed['controls'][0]['visible'] is True
    with pytest.raises(forms.Gate, match='visibly editable'):
        forms._disclosure_question(current, c, dom, URL, bootstrap=True)
