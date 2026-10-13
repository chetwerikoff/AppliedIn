"""Synthetic direct-application form provenance and first-field availability.

No BrowserSkill daemon, browser, network, owner's facts, or real application.
The fake evaluate result is separate from the PAGE-shaped inventory. It proves
the server consumes the transport shape, not that production JavaScript works.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms

URL = 'http://127.0.0.1/fixture/example-co/job/1'
PK = 'example-co#1'


def field(name='Email', selector='#email', *, required=True, **extra):
    return {
        'selector': selector, 'label': name, 'question': '',
        'tag': 'input', 'type': 'email' if name == 'Email' else 'text',
        'required': required, 'disabled': False, 'value': '', 'has_value': False,
        'in_form': True, 'form_selector': '#application', 'form_action': URL,
        'formaction': '', 'submit': False, 'checked': False, 'options': [],
        **extra,
    }


def submit(label='Submit application', *, selector='#submit', **extra):
    return field(label, selector, required=False, tag='button',
                 type='submit', submit=True, **extra)


def inventory(*controls, url=URL):
    return {
        'url': url, 'text': 'Synthetic application form ' * 40,
        'controls': list(controls), 'inventory_verified': True,
        'truncated': False, 'opaque_controls': False, 'shadow_roots': False,
        'unsupported_frames': [],
    }


def witness(page):
    return {'url': page['url'], 'controls': [
        {'selector': c['selector'], 'unique': True, 'unique_form': True,
         'tag': c['tag'], 'type': c['type'], 'in_form': c['in_form'],
         'form_selector': c.get('form_selector', ''),
         'form_action': c.get('form_action', ''),
         'formaction': c.get('formaction', ''), 'disabled': c['disabled'],
         'readonly': False, 'inert': False, 'visible': True,
         'options': deepcopy(c.get('options', []))}
        for c in page['controls']]}


class Browser:
    def __init__(self, page, result=None, fail=False):
        self.current = page
        self.result = result if result is not None else witness(page)
        self.fail = fail
        self.calls = []
        self.pages = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def navigate(self, url):
        self.calls.append(('navigate', url))
        return self.current

    async def page(self):
        self.pages += 1
        return self.current

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'evaluate':
            assert args[1] == forms._FIXED_CONTROL_WITNESS
            if self.fail:
                raise RuntimeError('synthetic evaluate failure')
            return {'value': deepcopy(self.result)}
        if args[0] in {'fill', 'select', 'upload', 'click'}:
            pytest.fail('No mutation belongs in a pre-owner-answer regression')
        raise AssertionError(args)


def world(monkeypatch, current, *, facts=None, grants=None, fail=False, result=None):
    pk = PK
    row = {'pk': pk, 'status': 'submitting', 'human_approved_answers': grants or {},
           'possible_submission': False}
    browser = Browser(current, fail=fail, result=result)
    monkeypatch.setattr(bsk, 'Session', lambda kind: browser)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')
    decide = AsyncMock(return_value={'action': 'gate', 'question': 'Generic manual review'})
    monkeypatch.setattr(bsk, 'decision', decide)
    return browser, decide, row


async def attempt(current, *, facts=None):
    return await forms.apply(URL, 'example-co', facts or {}, 'synthetic',
                             pk=PK, resume_path='Resume.pdf')


@pytest.mark.parametrize('extra,selected', [
    ({}, '#email'),
    ({'type': 'tel'}, '#email'),
])
async def test_first_required_field_gates_before_model_or_write(monkeypatch, extra, selected):
    c = field(**extra)
    p = inventory(c, submit())
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['status'] == 'gate'
    q = report['form_question']
    assert (q['pk'], q['label'], q['selector'], q['form_selector'],
            q['form_action'], q['tag'], q['type']) == (
                PK, 'Email', selected, '#application', URL, 'input', c['type'])
    assert not decide.await_count
    assert [x[0] for x in browser.calls] == ['navigate', 'evaluate']


async def test_dom_order_and_exact_current_grant_only(monkeypatch):
    c1, c2 = field('First name', '#first'), field('Email', '#email')
    p = inventory(c1, c2, submit())
    browser, decide, row = world(monkeypatch, p)
    assert (await attempt(p))['form_question']['label'] == 'First name'
    row['human_approved_answers'] = {
        'First name': {'value': 'Test User', 'selector': '#first', 'url': URL,
                       'label': 'First name', 'question': '', 'tag': 'input',
                       'type': 'text', 'form_selector': '#application',
                       'form_action': URL}}
    assert (await attempt(p, facts={'First name': 'Test User'}))[
        'form_question']['label'] == 'Email'
    assert not decide.await_count
    assert all(x[0] not in {'fill', 'select', 'click'} for x in browser.calls)


@pytest.mark.parametrize('prior,bank', [
    ({}, {}),
    ({}, {'Email': 'test@example.test'}),
    ({'value': 'old@example.test', 'selector': '#email', 'url': URL,
      'label': 'Email', 'question': '', 'tag': 'input', 'type': 'email',
      'form_selector': '#application', 'form_action': URL},
     {'Email': 'new@example.test'}),
    ({'value': 'test@example.test', 'selector': '#email', 'url': URL,
      'label': 'Email', 'question': ''}, {'Email': 'test@example.test'}),
])
async def test_bank_or_incomplete_or_changed_value_is_not_disclosure(
        monkeypatch, prior, bank):
    p = inventory(field(), submit())
    browser, decide, row = world(monkeypatch, p,
                                 grants={'Email': prior} if prior else {})
    report = await attempt(p, facts=bank)
    assert report['form_question']['label'] == 'Email'
    assert not decide.await_count
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


@pytest.mark.parametrize('problem', [
    'evaluate-error', 'missing-node', 'duplicate-node', 'wrong-form', 'hidden',
    'readonly', 'inert', 'foreign-action', 'unsafe-override',
])
async def test_no_witness_or_unsafe_receiver_never_mints_grant(
        monkeypatch, problem):
    p = inventory(field(), submit())
    evidence = witness(p)
    if problem == 'missing-node':
        evidence['controls'] = evidence['controls'][1:]
    elif problem == 'duplicate-node':
        evidence['controls'][0]['unique'] = False
    elif problem == 'wrong-form':
        evidence['controls'][0]['form_selector'] = '#other'
    elif problem in {'hidden', 'readonly', 'inert'}:
        evidence['controls'][0][{'hidden': 'visible',
                               'readonly': 'readonly',
                               'inert': 'inert'}[problem]] = problem != 'hidden'
    elif problem == 'foreign-action':
        p['controls'][0]['form_action'] = 'https://foreign.test/form'
        evidence = witness(p)
    elif problem == 'unsafe-override':
        p['controls'][1]['formaction'] = 'https://foreign.test/form'
        evidence = witness(p)
    browser, decide, row = world(monkeypatch, p, fail=problem == 'evaluate-error',
                                 result=evidence)
    report = await attempt(p)
    assert report['status'] == 'gate'
    assert report['form_question'] is None
    assert not any(x[0] in {'fill', 'select'} for x in browser.calls)
    # The controller may allow model review when the control is ineligible;
    # in neither path can it mint a disclosure question.
    assert decide.await_count in {0, 1}


@pytest.mark.parametrize('extra_control', [
    field('I agree to terms', '#terms', tag='input', type='checkbox',
          required=True, checked=False),
    field('Password', '#secret', type='password', required=True),
    field('Arbitration consent', '#legal', tag='input', type='radio',
          name='legal', question='Arbitration consent', required=True),
])
async def test_login_and_material_choice_wall_precedes_text_gate(monkeypatch, extra_control):
    p = inventory(field(), extra_control, submit())
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['form_question'] is None
    assert not decide.await_count
    assert all(x[0] != 'fill' for x in browser.calls)


async def test_ordinary_required_radio_does_not_hide_unrelated_text(monkeypatch):
    radio = field('Evenings', '#evenings', tag='input', type='radio',
                  name='schedule', question='Schedule preference')
    p = inventory(radio, field(), submit())
    browser, decide, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question']['label'] == 'Email'
    assert not decide.await_count


async def test_duplicate_form_question_never_authorizes_either_sibling(monkeypatch):
    a = field('First', '#a', question='Contact details')
    b = field('Second', '#b', question='Contact details')
    p = inventory(a, b, submit())
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['form_question'] is None
    assert decide.await_count == 1
    for chosen in ('#a', '#b'):
        with pytest.raises(forms.Gate) as exc:
            await forms.execute(browser, p, {'action': 'fill', 'selector': chosen,
                'fact': 'Contact details'}, facts={}, filled={}, resume_path='',
                company='example-co', jd_text='', resume_tex='', github='', pk=PK,
                job_url=URL)
        assert exc.value.form_question is None


@pytest.mark.parametrize('kind', ['sign-in', 'subscribe'])
async def test_selected_email_on_own_login_or_subscription_form_is_not_grantable(
        monkeypatch, kind):
    button = submit('Sign in' if kind == 'sign-in' else 'Subscribe')
    siblings = [field(), button]
    if kind == 'sign-in':
        siblings.insert(1, field('Password', '#password', type='password'))
    p = inventory(*siblings)
    browser, _, row = world(monkeypatch, p)
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(browser, p,
            {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    assert exc.value.form_question is None
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


async def test_safe_native_next_continuation_can_gate_without_final_marker(monkeypatch):
    p = inventory(field(), submit('Next'))
    browser, decide, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question'] is None
    assert decide.await_count == 1
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(browser, p,
            {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    assert exc.value.form_question['label'] == 'Email'


def test_only_actual_direct_owner_reply_persists_complete_identity(monkeypatch):
    from agent import run
    from core.models import Status
    answer = 'synthetic@example.test'
    p = inventory(field(), submit())
    question = forms._disclosure_question(p, p['controls'][0],
        {'#email': {'visible': True, 'readonly': False,
                    'disabled': False, 'inert': False}}, URL, bootstrap=True)
    question['pk'] = PK

    class Tracking:
        row = {'pk': PK, 'status': 'needs_human', 'jd_url': URL,
               'company': 'example-co', 'gate_source': 'applier',
               'gate_call_id': 'direct',
               'gate_pending': {'question': 'The observed field needs an answer.',
                                'form_question': question}}
        def get(self, pk):
            return self.row.copy()
        def set_status(self, pk, status, **kwargs):
            self.row.update(status=status, **kwargs)

    tracking = Tracking()
    saved = {}
    bank = SimpleNamespace(put=lambda *args, **kwargs: saved.update(
        label=args[0], value=args[1]))
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank)
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(run, '_enqueue_apply', lambda *args, **kwargs: {'result': 'queued'})
    assert run.resume_job(PK, answer, stores)['result'] == 'queued'
    grant = tracking.row['human_approved_answers']['Email']
    assert all(grant[k] == question[original] for k, original in (
        ('selector', 'selector'), ('url', 'url'), ('label', 'control_label'),
        ('tag', 'tag'), ('type', 'type'), ('form_selector', 'form_selector'),
        ('form_action', 'form_action')))
    assert saved['value'] == answer
    monkeypatch.setattr(forms, '_row', lambda pk: {
        **tracking.row, 'status': 'submitting'})
    forms.exact_approval(PK, 'Email', answer, target=p['controls'][0], url=URL)
    assert not forms.exact_approval(PK, 'Email', answer, target=p['controls'][0], url=URL)


@pytest.mark.parametrize('missing', ['tag', 'type', 'form_selector',
                                     'form_action', 'options'])
def test_direct_writer_refuses_malformed_control_provenance(monkeypatch, missing):
    from agent.run import _direct_form_label
    p = inventory(field('Location', '#location', tag='select', type='select',
                        options=[{'value': '', 'label': 'Choose'},
                                 {'value': 'remote', 'label': 'Remote'}]), submit())
    q = forms._disclosure_question(p, p['controls'][0],
        {'#location': {'visible': True, 'readonly': False,
                       'disabled': False, 'inert': False}}, URL, bootstrap=True)
    q['pk'] = PK
    q.pop(missing)
    row = {'pk': PK, 'status': 'needs_human', 'gate_source': 'applier',
           'gate_call_id': 'direct', 'jd_url': URL,
           'gate_pending': {'form_question': q}}
    assert _direct_form_label(PK, row) is None
