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
           'opacity-label', 'opacity-text', 'opacity-inner', 'opacity-card',
           'clip-text', 'clip-label', 'clip-inner',
           'clip-path-text', 'clip-path-label', 'clip-path-inner',
           'opacity-span', 'hidden-span', 'no-rect-span']
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
    # A nearby visual card is not an accessible question relationship.
    page = read({}, reader)
    items = choices(page)
    assert len(items) == 3
    assert [c['question'] for c in items] == ['', '', '']
    assert [c['label'] for c in items] == [
        'First option', 'Second option', 'Send synthetic updates']
    assert all(c['required'] for c in items)
    assert all(c['native_form_owner_selector'] == '#application-form' for c in items)
    assert not any(c['checked'] for c in items)
    with pytest.raises(forms.Gate, match='visible question'):
        forms._radio_group(page, items[0])
    # A semantic fieldset/legend remains a positive, library-independent path.
    identified = read({'wrapper': 'fieldset'}, reader)
    named = choices(identified)
    assert [c['question'] for c in named] == ['Existing standard question'] * 3
    assert forms._radio_group(identified, named[0]) == named[:2]

@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_rendered_text_span_and_required_marker_remain_the_question(reader, kind):
    # Decorative text alone is insufficient; semantic form grouping names it.
    page = read({'kinds': [kind], 'defect': 'visible-span'}, reader)
    assert all(c['question'] == '' and c['required'] for c in choices(page))
    identified = read({'kinds': [kind], 'defect': 'visible-span',
                       'wrapper': 'fieldset'}, reader)
    assert all(c['question'] == 'Existing standard question'
               and c['required'] for c in choices(identified))

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
    prepared = await forms._bootstrap(session, page, 'example-co#synthetic', {}, URL)
    assert prepared == page
    assert not any(c['checked'] for c in choices(session.current))
    assert all(call[0] == 'evaluate' for call in session.calls)
    with pytest.raises(forms.Gate):
        forms.check_form(prepared, {}, uploaded=True)

@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind,defect', MUTATIONS)
async def test_malformed_card_reader_matrix(reader, kind, defect, monkeypatch, tmp_path):
    """r06: a malformed card is not an answer grant; only the final Submit guard decides.

    Every mutation passes through the shipped JS reader and the real application
    raw-click producer. An omitted required choice and a DOM-preselected choice
    without a row approval must both refuse *before* any Submit IPC. No old
    per-step Lever shape certificate is asserted or reintroduced.
    """
    from tools import browser_skill as bsk

    observed = read({'kinds': [kind], 'defect': defect}, reader)
    items = choices(observed)
    assert items
    assert not any(c.get('checked') for c in items)
    assert any(c['selector'].startswith('#' + kind + '-') for c in items)
    assert any(c.get('selector') == '#submit' and c.get('submit')
               for c in observed['controls'])

    # The other final-Submit prerequisites are synthetic and satisfied so that
    # the actual code-owned form/value check, not missing PDF or job identity,
    # adjudicates these native choices.
    pdf = tmp_path / 'synthetic-row.pdf'
    pdf.write_bytes(b'%PDF-1.4\\nsynthetic row-only attachment\\n')
    row = {'pk': 'example-co#synthetic', 'company': 'example-co',
           'job_id': 'synthetic', 'jd_url': URL, 'ats': 'custom',
           'status': 'submitting', 'title': observed['title']}
    pdf_identity = ('synthetic-row-pdf',)
    cli_calls = []
    checked_pages = []
    real_check_form = forms.check_form

    async def fake_command(*args, **kwargs):
        cli_calls.append((args, kwargs))
        return {}

    def checked_form(*args, **kwargs):
        checked_pages.append(args[0])
        return real_check_form(*args, **kwargs)

    def no_hold(*_args, **_kwargs):
        pytest.fail('Unapproved choice reached the durable Submit hold')

    monkeypatch.setattr(bsk, 'command', fake_command)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *_a, **_k: None)
    monkeypatch.setattr(forms, 'check_form', checked_form)
    monkeypatch.setattr(forms, 'hold_possible_submission', no_hold)
    monkeypatch.setattr(forms, 'check_approvals', no_hold)

    for selected_without_approval in (False, True):
        page = deepcopy(observed)
        candidate = next(c for c in choices(page)
                         if c['selector'].startswith('#' + kind + '-'))
        candidate['checked'] = selected_without_approval
        page['controls'].append({
            'selector': '#synthetic-row-resume', 'tag': 'input', 'type': 'file',
            'label': 'Resume', 'files': [pdf.name], 'in_form': True,
            'form_selector': '#application-form', 'form_action': URL,
        })
        controller = forms.ApplicationIPCController(
            'example-co#synthetic', 'example-co', URL, str(pdf),
            {}, {}, [], {'url': URL}, lambda: None)
        monkeypatch.setattr(controller, '_row', lambda: row)
        monkeypatch.setattr(controller, '_fresh_preparation', lambda _url: None)
        monkeypatch.setattr(controller, '_row_pdf', lambda: pdf_identity)
        controller.upload_completed = True
        controller.upload_identity = pdf_identity

        async def fake_page(_session):
            return page

        monkeypatch.setattr(controller, '_page', fake_page)
        session = bsk.Session('apply')
        session.id = 'synthetic-session'
        session.bind_application_controller(controller)
        with pytest.raises(forms.Gate) as exc:
            await session.application_effect('click', '#submit')
        # The real final check must run, and neither a missing choice nor an
        # invented checked answer may reach a raw click or durable hold.
        assert checked_pages[-1] is page
        if (page.get('inventory_verified') is True and not page.get('truncated')
                and not any(page.get(k) for k in
                            ('opaque_controls', 'unsupported_frames', 'shadow_roots'))):
            if selected_without_approval:
                assert str(exc.value).startswith('Unverified preselected answer:')
            else:
                assert str(exc.value).startswith((
                    'Required choice is empty:', 'Required acknowledgement not completed:'))
        else:
            assert str(exc.value).startswith((
                'The form inventory is incomplete;', 'This form has uninspectable components;'))
        assert cli_calls == []
        assert controller.committed is False
    assert len(checked_pages) == 2


@pytest.mark.parametrize('kind,defect', MUTATIONS)
async def test_structural_gate_precedes_any_question_grant_or_choice(monkeypatch, kind, defect):
    """A card-shape mutation cannot authorize a native answer or Submit.

    Structural admission of a legacy card was deleted under r06; the real
    final-submit guard for every mutation is separately exercised by
    test_malformed_card_reader_matrix above. Safe preparation cannot acquire
    owner authority from any of these malformed visual decorations.
    """
    session = Session({'kinds': [kind], 'defect': defect})
    before = [(c['selector'], c.get('checked')) for c in choices(session.current)]
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: {})
    try:
        prepared = await forms._bootstrap(
            session, session.current, 'example-co#synthetic', {}, URL)
        assert prepared == session.current
    except forms.Gate as exc:
        # An actually mismatched independent witness may refuse observation.
        # A removed per-ATS card-shape certificate is not a valid refusal.
        assert 'current DOM witness is unavailable or changed' in str(exc)
    assert [(c['selector'], c.get('checked')) for c in choices(session.current)] == before
    assert all(call[0] == 'evaluate' for call in session.calls)
    with pytest.raises(forms.Gate):
        forms.check_form(session.current, {}, uploaded=True)

@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('fresh', [False, True], ids=['initial', 'refreshed'])
async def test_observation_boundary_checks_flag_even_without_question_identity_change(kind, fresh):
    # A removed per-card flag cannot become answer/Submit authority.
    session = Session({'kinds': [kind]})
    changed = deepcopy(session.current)
    for c in choices(changed):
        c['lever_card_unresolved'] = True
    if fresh:
        session.fresh = changed
    else:
        session.current = changed
    current, witness = await forms._observe_controls(session, session.current)
    assert [(c['selector'], c.get('checked')) for c in choices(current)] == [
        (c['selector'], False) for c in choices(changed)]
    assert all(witness[c['selector']]['visible_text'] == c['label']
               for c in choices(current))
    assert all(call[0] == 'evaluate' for call in session.calls)
    with pytest.raises(forms.Gate):
        forms.check_form(current, {}, uploaded=True)

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
    prepared = await forms._bootstrap(
        session, session.current, 'example-co#synthetic', {}, URL)
    assert prepared == session.current
    fixed = {c['selector']: c for c in session.fixed['controls']}
    question = forms._disclosure_question(prepared, checkbox, fixed, URL)
    assert question['control_label'] == 'Send synthetic updates'
    assert question['question'] == ''
    with pytest.raises(forms.Gate, match='exact human-approved answer'):
        forms.exact_approval(
            'example-co#synthetic', question['label'], 'yes',
            target=checkbox, url=URL)
    assert all(call[0] == 'evaluate' for call in session.calls)
    with pytest.raises(forms.Gate):
        forms.check_form(prepared, {}, uploaded=True)

@pytest.mark.parametrize('reader', READERS, ids=['PAGE', 'ENTRY_PAGE', 'GREENHOUSE_PAGE'])
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_native_role_override_retains_unresolved_flag_from_production_reader(reader, kind):
    # An author-supplied role="switch" on an input is not evidence of native
    # radio/checkbox group membership or an authorised form-owned value.
    spec = {'kinds': [kind], 'defect': 'two-texts', 'role': 'switch'}
    page = read(spec, reader)
    inputs = [c for c in page['controls']
              if c['tag'] == 'input' and c['selector'].startswith('#'+kind+'-')]
    assert len(inputs) == (2 if kind == 'radio' else 1)
    assert all(c['type'] == 'switch' and c['question'] == ''
               and not c['checked'] and c['in_form'] for c in inputs)
    witness = read(spec, forms._FIXED_CONTROL_WITNESS)
    known = {c['selector']: c for c in witness['controls']}
    assert all(known[c['selector']]['type'] == 'switch' for c in inputs)
    with pytest.raises(forms.Gate, match='Only observed native or linked combobox'):
        forms._disclosure_question(page, inputs[0], known, URL)
    with pytest.raises(forms.Gate):
        forms.check_form(page, {}, uploaded=True)

@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('fresh', [False, True], ids=['initial', 'refreshed'])
@pytest.mark.parametrize('optional', [False, True], ids=['required', 'optional'])
async def test_unresolved_role_override_gates_initial_and_refreshed_observation(kind, fresh, optional):
    malformed = {'kinds': [kind], 'defect': 'two-texts', 'role': 'switch',
                 'optional': optional}
    session = Session({'kinds': [kind], 'optional': optional} if fresh else malformed)
    if fresh:
        # Independent current-page drift must refuse the stale witness, even
        # though the legacy per-card refusal is no longer an admission gate.
        session.fresh = read(malformed)
        with pytest.raises(forms.Gate, match='current DOM witness is unavailable or changed'):
            await forms._observe_controls(session, session.current)
    else:
        page, fixed = await forms._observe_controls(session, session.current)
        assert page == session.current
        assert all(fixed[c['selector']]['type'] == 'switch'
                   for c in page['controls'] if c.get('type') == 'switch')
        with pytest.raises(forms.Gate):
            forms.check_form(page, {}, uploaded=True)
    assert all(call[0] == 'evaluate' for call in session.calls)
    assert len(session.calls) == 1

@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('optional', [False, True], ids=['required', 'optional'])
async def test_unresolved_role_override_precedes_any_bootstrap_grant(monkeypatch, kind, optional):
    session = Session({'kinds': [kind], 'defect': 'two-texts', 'role': 'switch',
                       'optional': optional})
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: pytest.fail('Grant bank reached'))
    prepared = await forms._bootstrap(
        session, session.current, 'example-co#synthetic', {}, URL)
    assert prepared == session.current
    assert session.calls == []
    inputs = [c for c in prepared['controls'] if c.get('type') == 'switch']
    assert len(inputs) == (2 if kind == 'radio' else 1)
    witness = read({'kinds': [kind], 'defect': 'two-texts', 'role': 'switch',
                    'optional': optional}, forms._FIXED_CONTROL_WITNESS)
    fixed = {c['selector']: c for c in witness['controls']}
    with pytest.raises(forms.Gate, match='Only observed native or linked combobox'):
        forms._disclosure_question(prepared, inputs[0], fixed, URL)
    with pytest.raises(forms.Gate):
        forms.check_form(prepared, {}, uploaded=True)