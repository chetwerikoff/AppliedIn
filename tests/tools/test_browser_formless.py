"""Real three-child Ashby structure, exercised offline by both production readers.

No fixture marker supplies authority: the normal tracked entry must admit two
independent complete observations before the direct human question is reachable.
"""
from copy import deepcopy
import asyncio

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import (
    COMPANY, PK, dom, entry, posting, world, attempt, no_mutations,
)


def observed(**changes):
    spec = dict(root=False, formless=True, **changes)
    page = dom(spec)
    fixed = dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)
    page['url'] = fixed['url'] = entry('ashby')
    return page, fixed


class RootSession:
    def __init__(self, page, fixed):
        self.current, self.fixed = deepcopy(page), deepcopy(fixed)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call(self, method, *args, **kwargs):
        self.calls.append((method, *args))
        if method == 'evaluate':
            if args == (forms.ENTRY_PAGE,):
                return {'value': deepcopy(self.current)}
            assert args == (forms._ENTRY_FIXED_CONTROL_WITNESS,)
            return {'value': deepcopy(self.fixed)}
        if method == 'navigate':
            return {}
        if method == 'fill':
            selector, _, value = args
            for snapshot in (self.current, self.fixed):
                for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                    if c['selector'] == selector:
                        c.update(value=value, has_value=True)
            return {}
        if method == 'upload':
            selector, _, path = args
            for snapshot in (self.current, self.fixed):
                for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                    if c['selector'] == selector:
                        c['files'] = [path]
            return {}
        pytest.fail(f'Unexpected mutating IPC: {method}')


def root_world(monkeypatch, **changes):
    row, configs, _, decision = world(monkeypatch)
    page, fixed = observed(**changes)
    raw = RootSession(page, fixed)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *a: raw)
    provenance = forms._application_entry(row, PK, COMPANY, posting('ashby'), configs)
    return row, raw, decision, provenance


async def admitted(monkeypatch):
    row, raw, decision, provenance = root_world(monkeypatch)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance)
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=asyncio.get_running_loop().time() + 10)
    return row, raw, session, page, provenance


def test_readers_independently_agree_on_real_shape_and_never_read_autofill():
    page, fixed = observed()
    assert page['entry'] == fixed['entry']
    root = page['entry']['formless']
    assert root['root'] == '#application-root'
    assert root['submit'] == '#structural-submit'
    assert root['excluded'] == ['#autofill-file', '#autofill-button']
    assert not {c['selector'] for c in page['controls']} & set(root['excluded'])
    resume = next(c for c in page['entry']['root_evidence'] if c['id'] == '_systemfield_resume')
    assert resume['associated'] and resume['required'] and resume['in_container']
    assert resume['files'] == []
    assert page['entry']['root'] is None


async def test_normal_apply_reaches_actual_direct_human_question(monkeypatch):
    _, raw, decision, _ = root_world(monkeypatch)
    result = await attempt()
    assert result['status'] == 'gate', result
    question = result['form_question']
    assert question['label'] == 'Name'
    assert question['owner_kind'] == 'ashby-formless-root'
    assert question['form_selector'] == '#application-root'
    assert question['form_action'] == '#structural-submit'
    assert not decision.await_count
    no_mutations(raw)
    # The real unchanged consumer, not a hand-built approval API.
    from agent.run import _direct_form_label
    pending = dict(pk=PK, status='needs_human', gate_source='applier',
                   gate_call_id='direct', jd_url=posting('ashby'), ats='ashby',
                   gate_pending={'form_question': question})
    assert _direct_form_label(PK, pending) == 'Name'


@pytest.mark.parametrize('changes,cause', [
    ({'duplicate': True}, 'ambiguous application roots'),
    ({'wrongOrder': True}, 'sibling identity mismatch'),
    ({'outside': '_systemfield_email'}, 'candidate control outside root'),
    ({'hiddenOutside': True}, 'receiver outside root'),
    ({'duplicateLabel': True}, 'label/control association'),
    *[({'extraType': typ}, 'unsupported ' + typ) for typ in ('select', 'radio', 'checkbox', 'hidden')],
    ({'extraType': 'file'}, 'unsupported file receiver'),
    ({'extraType': 'text', 'extraAttrs': {'role': 'combobox'}}, 'unsupported autocomplete/combobox'),
    ({'extraType': 'text', 'extraAttrs': {'contenteditable': 'true'}}, 'unsupported contenteditable'),
])
async def test_normal_apply_preserves_specific_danger_before_model_or_ipc(monkeypatch, changes, cause):
    _, raw, decision, _ = root_world(monkeypatch, **changes)
    result = await attempt()
    assert result['status'] == 'gate', result
    assert cause in result['question'], result
    assert not decision.await_count
    no_mutations(raw)


@pytest.mark.parametrize('selector,verb', [
    ('#autofill-file', 'upload'), ('#autofill-file', 'fill'),
    ('#autofill-button', 'click'), ('#dropzone', 'click'),
])
async def test_actual_ipc_producer_denies_pane_and_dropzone_even_without_execute(monkeypatch, selector, verb):
    _, raw, session, _, _ = await admitted(monkeypatch)
    with pytest.raises(forms.Gate, match='forbidden at IPC producer'):
        await session.call(verb, selector)
    no_mutations(raw)


async def test_exact_structural_grant_permits_only_matching_fill_with_readback(monkeypatch):
    row, raw, session, page, provenance = await admitted(monkeypatch)
    with pytest.raises(forms.Gate) as gate:
        await forms._bootstrap(session, page, PK, {}, provenance=provenance)
    q = gate.value.form_question
    row['human_approved_answers'] = {'Name': {
        'value': 'Test User', 'label': q['control_label'],
        **{k: q[k] for k in ('selector', 'url', 'question', 'tag', 'type', 'form_selector', 'form_action')},
    }}
    filled = {}
    receipt = await forms.execute(
        session, page, {'action': 'fill', 'selector': q['selector'], 'fact': 'Name'},
        facts={'Name': 'Test User'}, filled=filled, resume_path='Resume.pdf',
        company=COMPANY, jd_text='', resume_tex='', github='', pk=PK,
        job_url=posting('ashby'), provenance=provenance)
    assert receipt['approved_value'] == 'Test User'
    assert filled == {'#_systemfield_name': 'Test User'}
    assert [c for c in raw.calls if c[0] == 'fill'] == [
        ('fill', '#_systemfield_name', '--value', 'Test User')]


async def test_changed_root_after_admission_is_immediate_drift(monkeypatch):
    _, raw, session, _, _ = await admitted(monkeypatch)
    raw.current['entry']['formless']['pane'] = '#changed-pane'
    with pytest.raises(forms.Gate, match='root identity drift'):
        await session.page()
    no_mutations(raw)
