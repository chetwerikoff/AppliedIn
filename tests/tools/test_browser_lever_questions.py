"""A wrong card question binds the wrong human answer; refuse the whole card.

Execute the shipped JS against parent-edge synthetic DOM, then the real controller
and fixed witness. No socket, browser, account, or fixture approval is involved.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as dom

URL = 'https://careers.example-co.test/apply'
REASON = 'Lever question card did not match the exact supported shape; inspect manually.'
READERS = [dom.PAGE, dom.ENTRY_PAGE, dom.GREENHOUSE_PAGE]
DEFECTS = ['two-names', 'extra-text', 'extra-role', 'two-texts', 'hidden-text',
           'empty-text', 'outside-form', 'reassociated', 'member-owner', 'no-owner',
           'two-labels', 'two-fields', 'text-path', 'field-path', 'member-path',
           'missing-qa', 'blank-name', 'two-checkboxes',
           'label-under-field', 'split-sections', 'nested-sections', 'non-div-sections',
           'opacity-label', 'opacity-text', 'opacity-inner', 'opacity-card']
MUTATIONS = [(kind, defect) for kind in ('radio', 'checkbox') for defect in DEFECTS
             if (kind, defect) != ('checkbox', 'blank-name')]


def read(spec, script=dom.PAGE):
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('entry_dom_fixture.js')),
        json.dumps({'url': URL, 'lever': spec}), script], text=True))


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
def test_exact_questions_keep_visible_required_marker_and_own_card(reader):
    page = read({}, reader)
    items = choices(page)
    assert len(items) == 3
    assert [c['question'] for c in items] == [
        'Synthetic radio question *', 'Synthetic radio question *',
        'Synthetic checkbox question *']
    assert [c['label'] for c in items] == ['First option', 'Second option', 'Send synthetic updates']
    assert not any(c.get('lever_card_unresolved') for c in items)
    assert all(c['native_form_owner_selector'] == '#application-form' for c in items)
    assert all(not c['checked'] for c in items)
    assert forms._radio_group(page, items[0]) == items[:2]


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
async def test_real_fixed_witness_and_controller_expose_unselected_question(monkeypatch, kind):
    session = Session({'kinds': [kind]})
    page, witness = await forms._observe_controls(session, session.current)
    for c in choices(page):
        fixed = witness[c['selector']]
        assert 'question' not in fixed
        assert fixed['visible_text'] == c['label']
        assert fixed['native_form_owner_selector'] == '#application-form'
        assert fixed['visible'] and not fixed['readonly'] and not fixed['disabled']
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: {})
    with pytest.raises(forms.Gate) as exc:
        await forms._bootstrap(session, page, 'example-co#synthetic', {}, URL)
    assert exc.value.form_question['question'] == 'Synthetic '+kind+' question *'
    assert not any(c['checked'] for c in choices(session.current))
    assert all(call[0] == 'evaluate' for call in session.calls)


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind,defect', MUTATIONS)
def test_malformed_card_reader_matrix(reader, kind, defect):
    page = read({'kinds': [kind], 'defect': defect}, reader)
    items = choices(page)
    assert items
    assert all(c['question'] == '' and c.get('lever_card_unresolved') is True for c in items)


@pytest.mark.parametrize('kind,defect', MUTATIONS)
async def test_structural_gate_precedes_any_question_grant_or_choice(monkeypatch, kind, defect):
    session = Session({'kinds': [kind], 'defect': defect})
    # A uniquely captioned checkbox would otherwise be an approvable label-only handoff.
    if kind == 'checkbox':
        assert choices(session.current)[0]['label'] == 'Send synthetic updates'
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: pytest.fail('Grant bank reached'))
    monkeypatch.setattr(forms, '_disclosure_question',
                        lambda *a, **kw: pytest.fail('Question constructed before structural Gate'))
    with pytest.raises(forms.Gate) as exc:
        await forms._bootstrap(session, session.current, 'example-co#synthetic', {}, URL)
    assert str(exc.value) == REASON
    assert exc.value.form_question is None
    assert session.calls == []


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('fresh', [False, True], ids=['initial', 'refreshed'])
async def test_observation_boundary_checks_flag_even_without_question_identity_change(kind, fresh):
    session = Session({'kinds': [kind]})
    changed = deepcopy(session.current)
    for c in choices(changed):
        c['lever_card_unresolved'] = True
    if fresh:
        session.fresh = changed
    else:
        session.current = changed
    with pytest.raises(forms.Gate) as exc:
        await forms._observe_controls(session, session.current)
    assert str(exc.value) == REASON
    assert exc.value.form_question is None
    assert all(call[0] == 'evaluate' for call in session.calls)


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('wrapper', ['fieldset', 'form-group'])
@pytest.mark.parametrize('empty', [False, True])
def test_standard_wrapper_precedence_even_when_old_question_is_empty(reader, wrapper, empty):
    page = read({'wrapper': wrapper, 'emptyWrapper': empty, 'defect': 'two-texts'}, reader)
    for c in choices(page):
        assert c['question'] == ('' if empty else 'Existing standard question')
        assert 'lever_card_unresolved' not in c


async def test_outside_card_label_only_checkbox_keeps_approvable_handoff(monkeypatch):
    session = Session({'kinds': ['checkbox'], 'outsideCard': True, 'defect': 'two-texts'})
    checkbox, = choices(session.current)
    assert checkbox['question'] == '' and 'lever_card_unresolved' not in checkbox
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: {})
    with pytest.raises(forms.Gate) as exc:
        await forms._bootstrap(session, session.current, 'example-co#synthetic', {}, URL)
    assert exc.value.form_question['control_label'] == 'Send synthetic updates'
    assert exc.value.form_question['question'] == ''


@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_native_role_override_retains_unresolved_flag_from_production_reader(reader, kind):
    page = read({'kinds': [kind], 'defect': 'two-texts', 'role': 'switch'}, reader)
    inputs = [c for c in page['controls']
              if c['tag'] == 'input' and c['selector'].startswith('#'+kind+'-')]
    assert len(inputs) == (2 if kind == 'radio' else 1)
    assert all(c['type'] == 'switch' and c['question'] == ''
               and c.get('lever_card_unresolved') is True for c in inputs)


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('fresh', [False, True], ids=['initial', 'refreshed'])
@pytest.mark.parametrize('optional', [False, True], ids=['required', 'optional'])
async def test_unresolved_role_override_gates_initial_and_refreshed_observation(kind, fresh, optional):
    malformed = {'kinds': [kind], 'defect': 'two-texts', 'role': 'switch', 'optional': optional}
    session = Session({'kinds': [kind], 'optional': optional} if fresh else malformed)
    if fresh:
        session.fresh = read(malformed)
    with pytest.raises(forms.Gate) as exc:
        await forms._observe_controls(session, session.current)
    assert str(exc.value) == REASON
    assert exc.value.form_question is None
    assert len(session.calls) == int(fresh)


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('optional', [False, True], ids=['required', 'optional'])
async def test_unresolved_role_override_precedes_any_bootstrap_grant(monkeypatch, kind, optional):
    session = Session({'kinds': [kind], 'defect': 'two-texts', 'role': 'switch', 'optional': optional})
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: pytest.fail('Grant bank reached'))
    monkeypatch.setattr(forms, '_disclosure_question',
                        lambda *a, **kw: pytest.fail('Question constructed before structural Gate'))
    with pytest.raises(forms.Gate) as exc:
        await forms._bootstrap(session, session.current, 'example-co#synthetic', {}, URL)
    assert str(exc.value) == REASON
    assert exc.value.form_question is None
    assert session.calls == []
