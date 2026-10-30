"""Issue #268: synthetic Greenhouse settlement questions use the direct owner gate.

The fixture has no real employer browser, owner facts, submitted application or
network. It exercises the actual applier row, exact owner tokens and bank grants.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import fakeredis
import pytest

import server
from agent import run
from core.apply_queue import ApplyQueue
from core.models import JobRecord, Status
from core.storage.local import RedisTracking
from tests.tools.test_browser_bootstrap import field, witness
from tests.tools.test_browser_bootstrap_settlement import (
    COMPANY, GH_PK, PHONE, _grant, _world,
)
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms


class SyntheticBank:
    def __init__(self, facts):
        self.facts = dict(facts)
        self.writes = []

    def all_facts(self, company):
        assert company == COMPANY
        return dict(self.facts)

    def _load(self):
        return {'global': list(self.facts.items())}

    def put(self, label, answer, scope, **kwargs):
        self.writes.append((label, answer.strip()))
        self.facts[label] = answer.strip()


def tracked_greenhouse(monkeypatch, *, kind='select', bank_facts=None):
    case, raw = _world(monkeypatch, 'greenhouse', kind='select')
    if kind == 'text':
        question = field('Screening detail', '#screening', type='text',
                         form_action=case.url)
    else:
        assert kind == 'select'
        question = field(
            'Screening choice', '#screening', tag='select', type='select',
            options=[{'value': 'remote', 'label': 'Remote'},
                     {'value': 'office', 'label': 'Office'}],
            form_action=case.url)
    for page in (case.page, raw.state):
        page['controls'][1] = deepcopy(question)

    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    assert tracking.put_new(JobRecord(
        company=COMPANY, job_id='123456', title='Synthetic role',
        jd_url=case.wrapper, jd_text='Synthetic role text',
        location='Synthetic city', ats='greenhouse'))
    tracking.set_status(
        GH_PK, Status.TAILORED, greenhouse_board=COMPANY,
        human_approved_answers={'Phone': _grant(case.phone, case.url, PHONE)})
    bank = SyntheticBank({'Phone': PHONE} if bank_facts is None else bank_facts)
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank, secrets=None)

    async def jd_meta(_row, *, yielding=True):
        return {'text': 'Synthetic role text'}

    monkeypatch.setattr(run, '_jd_meta', jd_meta)
    monkeypatch.setattr(
        run, '_commit_posting_meta',
        lambda pk, row, meta, current: current.tracking.get(pk))
    monkeypatch.setattr('tools.posting_guard.authorised', lambda *a, **k: True)
    monkeypatch.setattr('core.rotation.ensure', lambda *a, **k: None)
    monkeypatch.setattr('core.profiles.resolve_for', lambda *a, **k: None)
    monkeypatch.setattr('core.profiles.expand_all', lambda facts: facts)
    monkeypatch.setattr('tools.credentials.get_login', lambda *a, **k: None)
    monkeypatch.setattr('core.events.emit', lambda *a, **k: None)
    monkeypatch.setattr(run, 'get_settings',
                        lambda: SimpleNamespace(browser_model='synthetic'))
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: 'Resume.pdf')
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr('tools.browser_runtime.configuration',
                        lambda: {'engine': 'browser_skill'})
    monkeypatch.setattr('tools.browser_apply.apply', forms.apply)
    monkeypatch.setattr('core.stores.make_stores', lambda: stores)
    monkeypatch.setattr(forms, '_row', lambda pk, *a, **kw: tracking.get(pk) or {})
    return case, raw, stores


@pytest.mark.parametrize(('kind', 'answer'), [
    ('select', 'Remote'),
    ('text', 'Synthetic screening detail'),
])
async def test_required_greenhouse_question_is_tokened_and_exactly_resumable(
        monkeypatch, kind, answer):
    """Missing-bank native choice and free text have an answerable, bound gate."""
    case, raw, stores = tracked_greenhouse(monkeypatch, kind=kind)

    async def model_must_not_run(*a, **kw):
        pytest.fail('Required unbanked field must gate before model review')

    monkeypatch.setattr(bsk, 'decision', model_must_not_run)
    first = await run._apply_direct(case.pk, stores)
    assert first['result'] == 'gated', first
    row = stores.tracking.get(case.pk)
    assert row['status'] == 'needs_human'
    assert row['gate_reason'] == 'owner_question'
    assert row['gate_source'] == 'applier'
    assert row['gate_call_id'] == 'direct'
    assert row['gate_pending']['review_route'] == 'owner'
    question = row['gate_pending']['form_question']
    assert question['pk'] == case.pk
    assert question['selector'] == '#screening'
    assert question['label'] == ('Screening detail' if kind == 'text'
                                 else 'Screening choice')
    assert question['type'] == kind
    assert question['url'] == case.url

    # An exact phone grant and bank value settle before the missing screening
    # field gates; the ordinary fixture has no raw producer auto-grant binder.
    assert raw.writes == [('fill', '#phone')]
    assert raw.target('#screening')['has_value'] is False
    assert row['human_approved_answers']['Phone']['value'] == PHONE
    assert question['label'] not in row['human_approved_answers']
    assert stores.answer_bank.writes == []

    tokens = server._owner_gate_tokens(row)
    assert tokens['gate_generation']
    assert tokens['gate_question_identity'] == forms.owner_question_identity(
        case.pk, question)
    assert tokens['gate_skip_eligible'] is False
    for generation, identity in (
        (tokens['gate_generation'] + '-stale', tokens['gate_question_identity']),
        (tokens['gate_generation'], tokens['gate_question_identity'] + '-stale'),
    ):
        stale = run.resume_job(
            case.pk, answer, stores, disposition='answer',
            expected_gate_generation=generation,
            expected_question_identity=identity)
        assert stale['reason'] == 'invalid_answer'
        assert stores.tracking.get(case.pk)['gate_pending'] == row['gate_pending']
        assert stores.answer_bank.writes == []
        assert ApplyQueue(stores.tracking.r).pending() == []

    accepted = run.resume_job(
        case.pk, answer, stores, disposition='answer',
        expected_gate_generation=tokens['gate_generation'],
        expected_question_identity=tokens['gate_question_identity'])
    assert accepted['result'] == 'queued', accepted
    granted = stores.tracking.get(case.pk)['human_approved_answers'][question['label']]
    assert granted['selector'] == question['selector']
    assert granted['value'] == answer
    assert stores.answer_bank.writes == [(question['label'], answer)]

    decisions = []

    async def stop_before_submit(_prompt, _page, _history, *, model):
        decisions.append(True)
        return {'action': 'gate', 'question': 'Synthetic stop before Submit'}

    monkeypatch.setattr(bsk, 'decision', stop_before_submit)
    second = await run._apply_direct(case.pk, stores)
    assert second['result'] == 'gated', second
    assert decisions == [True]
    assert raw.writes == ([('fill', '#phone'), ('select', '#screening')]
                          if kind == 'select' else [('fill', '#phone')])
    assert not any(call[0] == 'click' and call[1] == '#submit'
                   for call in raw.calls)


async def test_greenhouse_global_bank_answers_still_auto_fill(monkeypatch):
    """Owner routing changes no guarded native bank-authorized write path."""
    facts = {'Phone': PHONE, 'Screening choice': 'Remote'}
    case, raw, stores = tracked_greenhouse(monkeypatch, bank_facts=facts)

    # This fake Session does not bind a production raw IPC controller, so mint
    # exact GLOBAL grants at the actual code-owned bank admission seam using
    # an independent synthetic fixed witness. No model, DOM or browser grants.
    stores.tracking.set_status(
        case.pk, Status.SUBMITTING, human_approved_answers={})
    page = deepcopy(raw.state)
    provenance = forms._configured_alias(case.pk, COMPANY, case.wrapper)
    assert isinstance(provenance, forms.GreenhouseJobAlias)
    forms._auto_grant_from_global(
        page, witness(page), case.pk, facts, facts, case.wrapper,
        provenance=provenance)
    stores.tracking.set_status(case.pk, Status.TAILORED)
    decisions = []

    async def stop_before_submit(_prompt, _page, _history, *, model):
        decisions.append(True)
        return {'action': 'gate', 'question': 'Synthetic stop before Submit'}

    monkeypatch.setattr(bsk, 'decision', stop_before_submit)
    result = await run._apply_direct(case.pk, stores)
    assert result['result'] == 'gated', result
    assert decisions == [True]
    assert raw.writes == [('fill', '#phone'), ('select', '#screening')]
    row = stores.tracking.get(case.pk)
    assert {key: row['human_approved_answers'][key]['source']
            for key in ('Phone', 'Screening choice')} == {
                'Phone': 'bank-auto', 'Screening choice': 'bank-auto'}
    assert stores.answer_bank.writes == []
    assert not any(call[0] == 'click' and call[1] == '#submit'
                   for call in raw.calls)
