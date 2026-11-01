"""Generic accessible native questions, without visual-card admission gates.

Exercise the shipped PAGE readers on parent-edge synthetic DOM and the real
fixed witness, approval checks and final raw-click guard. No network, browser,
owner records or live application is involved.
"""
from copy import deepcopy
from tests.tools.reader_node_harness import reader_output
import json
from pathlib import Path
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as dom

URL = 'https://careers.example-co.test/apply'
READERS = [dom.PAGE, dom.ENTRY_PAGE, dom.GREENHOUSE_PAGE]
DEFECTS = ['two-names', 'extra-text', 'extra-role', 'two-texts', 'hidden-text',
           'empty-text', 'outside-form', 'reassociated', 'member-owner', 'no-owner',
           'two-labels', 'two-fields', 'text-path', 'field-path', 'member-path',
           'missing-qa', 'blank-name', 'two-checkboxes',
           'label-under-field', 'split-sections', 'nested-sections', 'non-div-sections',
           'opacity-label', 'opacity-text', 'opacity-inner', 'opacity-card',
           'clip-text', 'clip-label', 'clip-inner',
           'clip-path-text', 'clip-path-label', 'clip-path-inner',
           'opacity-span', 'hidden-span', 'no-rect-span']
MUTATIONS = [(kind, defect) for kind in ('radio', 'checkbox') for defect in DEFECTS
             if (kind, defect) != ('checkbox', 'blank-name')]


def read(spec, script=dom.PAGE):
    return json.loads(reader_output(
        'entry_dom_fixture.js', {'url': URL, 'lever': spec}, script))


def choices(page):
    return [c for c in page['controls']
            if c['tag'] == 'input' and c['type'] in {'radio', 'checkbox'}]


class Session:
    def __init__(self, spec, fresh=None):
        self.current = read(spec)
        self.fixed = read(spec, forms._FIXED_CONTROL_WITNESS)
        self.fresh = fresh
        self.calls = []

    async def call(self, method, *args):
        self.calls.append((method, *args))
        assert (method, *args) == ('evaluate', forms._FIXED_CONTROL_WITNESS)
        return {'value': deepcopy(self.fixed)}

    async def page(self):
        return deepcopy(self.fresh if self.fresh is not None else self.current)



@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
def test_reader_uses_native_questions_not_decorative_card_text(reader):
    page = read({}, reader)
    items = choices(page)
    assert len(items) == 3
    assert [c['question'] for c in items] == ['', '', '']
    assert [c['label'] for c in items] == [
        'First option', 'Second option', 'Send synthetic updates']
    assert all(c['required'] for c in items)
    assert all(c['native_form_owner_selector'] == '#application-form'
               for c in items)
    assert not any(c['checked'] for c in items)
    # A real fieldset/legend supplies an accessible question without a special
    # Lever-only recognizer or a model-issued authority token.
    identified = read({'wrapper': 'fieldset'}, reader)
    named = choices(identified)
    assert [c['question'] for c in named] == ['Existing standard question'] * 3


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_visible_spans_do_not_override_semantic_fieldset(reader, kind):
    page = read({'kinds': [kind], 'defect': 'visible-span'}, reader)
    assert all(c['question'] == '' and c['required'] for c in choices(page))
    identified = read({'kinds': [kind], 'defect': 'visible-span',
                       'wrapper': 'fieldset'}, reader)
    assert all(c['question'] == 'Existing standard question'
               and c['required'] for c in choices(identified))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
async def test_real_fixed_witness_exposes_native_questions_without_writes(kind):
    session = Session({'kinds': [kind]})
    current, witnessed = await forms._observe_controls(session, session.current)
    for item in choices(current):
        fixed = witnessed[item['selector']]
        assert 'question' not in fixed
        assert fixed['visible_text'] == item['label']
        assert fixed['native_form_owner_selector'] == '#application-form'
        assert fixed['visible'] and not fixed['readonly'] and not fixed['disabled']
    assert all(call[0] == 'evaluate' for call in session.calls)
    assert not any(c['checked'] for c in choices(current))


def _isolated_final_choices(page):
    # Production native-option inventory is synthetic but its required/readback
    # semantics are tested without a field grant, receipt, or old widget gate.
    controls = deepcopy(choices(page))
    return {
        'url': URL, 'title': 'Synthetic role',
        'inventory_verified': True, 'truncated': False,
        'opaque_controls': False, 'unsupported_frames': False,
        'shadow_roots': False,
        'controls': [
            {'selector': '#synthetic-row-resume', 'tag': 'input', 'type': 'file',
             'question': 'Resume', 'label': 'Resume',
             'files': ['synthetic-row.pdf'], 'required': True,
             'in_form': True, 'form_selector': '#application-form',
             'form_action': URL},
            *controls,
        ],
    }


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind,defect', MUTATIONS)
def test_malformed_decorative_card_does_not_invent_a_selected_native_answer(
        reader, kind, defect):
    observed = read({'kinds': [kind], 'defect': defect}, reader)
    items = choices(observed)
    assert items and not any(c.get('checked') for c in items)
    assert any(c['selector'].startswith('#' + kind + '-') for c in items)
    final = _isolated_final_choices(observed)
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(final, {}, True, 'synthetic-row.pdf')


@pytest.mark.parametrize('kind,defect', MUTATIONS)
def test_ungated_preparation_does_not_require_a_card_witness(kind, defect):
    page = read({'kinds': [kind], 'defect': defect})
    assert choices(page)
    assert not any(c.get('checked') for c in choices(page))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('fresh', [False, True], ids=['initial', 'refreshed'])
async def test_current_choice_readback_detects_independent_witness_drift(kind, fresh):
    session = Session({'kinds': [kind]})
    changed = deepcopy(session.current)
    choice = choices(changed)[0]
    choice['checked'] = True
    if fresh:
        session.fresh = changed
        with pytest.raises(forms.Gate, match='current DOM witness is unavailable or changed'):
            await forms._observe_controls(session, session.current)
    else:
        session.current = changed
        current, witnessed = await forms._observe_controls(session, session.current)
        assert choices(current)[0]['checked'] is True
        assert witnessed[choice['selector']]['visible_text'] == choice['label']


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('wrapper', ['fieldset', 'form-group'])
@pytest.mark.parametrize('empty', [False, True])
def test_standard_wrapper_takes_precedence_over_decorative_text(reader, wrapper, empty):
    page = read({'wrapper': wrapper, 'emptyWrapper': empty,
                 'defect': 'two-texts'}, reader)
    assert all(c['question'] == ('' if empty else 'Existing standard question')
               for c in choices(page))


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
def test_checked_native_radio_is_a_current_form_value_without_grant(reader):
    page = read({'kinds': ['radio'], 'wrapper': 'fieldset'}, reader)
    native = choices(page)
    assert len(native) == 2
    final = _isolated_final_choices(page)
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(final, {'#radio-1': 'stale'}, True, 'synthetic-row.pdf')
    radio = next(c for c in final['controls'] if c.get('type') == 'radio')
    radio['checked'] = True
    forms.check_form(final, {}, True, 'synthetic-row.pdf')


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_role_override_is_read_as_role_not_authorized_native_choice(reader, kind):
    spec = {'kinds': [kind], 'defect': 'two-texts', 'role': 'switch'}
    page = read(spec, reader)
    items = [c for c in page['controls']
             if c['tag'] == 'input' and c['selector'].startswith('#'+kind+'-')]
    assert len(items) == (2 if kind == 'radio' else 1)
    assert all(c['type'] == 'switch' and c['question'] == ''
               and not c['checked'] and c['in_form'] for c in items)
    witnessed = read(spec, forms._FIXED_CONTROL_WITNESS)
    by_selector = {c['selector']: c for c in witnessed['controls']}
    assert all(by_selector[c['selector']]['type'] == 'switch' for c in items)


@pytest.mark.asyncio
@pytest.mark.parametrize(('kind', 'selector'), [
    ('radio', '#custom-radio-first'),
    ('checkbox', '#custom-checkbox-updates'),
])
async def test_synthetic_lever_decisions_use_current_native_choice_selectors(
        kind, selector):
    """The custom-question fixture once scripted #name, which does not exist
    on these forms. It must reach the actual unanswered native question,
    without any applicant IPC or guessed consent value.
    """
    from tools import browser_skill_fixture as fixture

    state = fixture._SyntheticRun('Synthetic-Resume.pdf')
    state.custom_question = kind
    action = await state.decision('', {}, [], model='synthetic-fixture')
    assert action == {'action': 'choose', 'selector': selector}
    assert f'id="{selector[1:]}"'.encode() in fixture._lever_question_form(kind)
    assert state.choice_ipc == state.fills == state.ipc_index == 0
