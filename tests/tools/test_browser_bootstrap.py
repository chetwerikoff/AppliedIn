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


def field(label='Email', selector='#email', *, required=True, **extra):
    return {
        'selector': selector, 'label': label, 'question': '',
        'tag': 'input', 'type': 'email' if label == 'Email' else 'text',
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


@pytest.mark.parametrize('kind', ['sign-in', 'subscribe', 'account-sibling'])
async def test_selected_email_on_own_login_or_subscription_form_is_not_grantable(
        monkeypatch, kind):
    button = submit('Sign in' if kind == 'sign-in' else
                    'Subscribe' if kind == 'subscribe' else 'Next')
    siblings = [field(), button]
    if kind == 'sign-in':
        siblings.insert(1, field('Password', '#password', type='password'))
    if kind == 'account-sibling':
        siblings.insert(1, field('Create account', '#account', required=False))
    p = inventory(*siblings)
    browser, _, row = world(monkeypatch, p)
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(browser, p,
            {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    assert exc.value.form_question is None
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


async def test_selected_continuation_without_positive_marker_is_generic(monkeypatch):
    # Architect amendment (c): even model-selected Next/Continue cannot
    # mint a disclosure grant without a positive native application marker.
    p = inventory(field(), submit('Next'))
    browser, decide, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question'] is None
    assert decide.await_count == 1
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(browser, p,
            {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    assert exc.value.form_question is None


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


async def test_missing_control_label_cannot_create_unwritable_owner_grant(monkeypatch):
    # Even a unique question is insufficient if the writer cannot bind
    # an original control_label to the current PAGE observation.
    p = inventory(field('', '#email', question='Email address'), submit())
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['status'] == 'gate' and report['form_question'] is None
    assert decide.await_count == 1
    assert not any(x[0] in {'fill', 'select'} for x in browser.calls)


# Settled PR #38 findings: every test is PAGE-shaped and uses a separate
# fake evaluate result. None drives the production JavaScript in a browser.
async def selected_gate(browser, page, *, verb='fill', selector='#email',
                        key='Email', facts=None):
    with pytest.raises(forms.Gate) as exc:
        await forms.execute(
            browser, page, {'action': verb, 'selector': selector, 'fact': key},
            facts=facts or {}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL)
    return exc.value


@pytest.mark.parametrize('visible_label', ['', 'go'])
@pytest.mark.parametrize('submit_value', ['Sign in', 'Subscribe'])
async def test_input_submit_value_reveals_account_purpose(
        monkeypatch, visible_label, submit_value):
    submitter = field(visible_label, '#submit', required=False, tag='input',
                      type='submit', submit=True, value=submit_value,
                      has_value=True)
    p = inventory(field(), submitter)
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['status'] == 'gate' and report['form_question'] is None
    assert not decide.await_count  # bootstrap's page-wide login wall
    exc = await selected_gate(browser, p)
    assert exc.form_question is None  # own-form model-selected safety
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


@pytest.mark.parametrize('option', [
    {'value': 'arbitration-accept', 'label': 'I agree to arbitration'},
    {'value': 'privacy consent', 'label': 'Accept'},
])
@pytest.mark.parametrize('with_text', [True, False])
async def test_material_native_select_options_block_all_grant_routes(
        monkeypatch, option, with_text):
    material = field('Response', '#response', tag='select', type='select',
                     options=[{'value': 'decline', 'label': 'Decline'}, option])
    controls = ([field()] if with_text else []) + [material, submit()]
    p = inventory(*controls)
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['status'] == 'gate' and report['form_question'] is None
    assert not decide.await_count
    exc = await selected_gate(browser, p, verb='select',
                              selector='#response', key='Response')
    assert exc.form_question is None
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


async def test_safe_native_select_options_remain_grantable(monkeypatch):
    location = field('Location', '#location', tag='select', type='select',
                     options=[{'value': '', 'label': 'Choose location'},
                              {'value': 'remote', 'label': 'Remote'}])
    p = inventory(location, submit())
    browser, decide, _ = world(monkeypatch, p)
    q = (await attempt(p))['form_question']
    assert q['label'] == 'Location' and q['options'] == location['options']
    assert not decide.await_count
    exc = await selected_gate(browser, p, verb='select',
                              selector='#location', key='Location')
    assert exc.form_question['options'] == location['options']
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


@pytest.mark.parametrize('case,attribute,value', [
    ('opacity-zero-ancestor', 'visible', False),
    ('disabled-fieldset', 'disabled', True),
])
async def test_invisible_or_effectively_disabled_witness_refuses_grant(
        monkeypatch, case, attribute, value):
    p = inventory(field(), submit())
    evidence = witness(p)
    evidence['controls'][0][attribute] = value
    browser, _, _ = world(monkeypatch, p, result=evidence)
    report = await attempt(p)
    assert report['status'] == 'gate' and report['form_question'] is None
    exc = await selected_gate(browser, p)
    assert exc.form_question is None
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


def test_fixed_read_only_script_contains_effective_dom_predicates():
    # Static unit coverage, not the outstanding real 127.0.0.1 smoke.
    script = forms._FIXED_CONTROL_WITNESS
    assert "for (let n=e; n && n.nodeType===1; n=n.parentElement)" in script
    assert 'Number(style.opacity) > 0' in script
    assert "e.matches(':disabled')" in script


async def test_unrelated_native_search_does_not_veto_application(monkeypatch):
    search = field('Search jobs', '#search', required=False, type='search',
                   form_selector='#site-search')
    search_submit = submit('Search', selector='#search-submit',
                           form_selector='#site-search')
    p = inventory(search, search_submit, field(), submit())
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    assert report['form_question']['label'] == 'Email'
    assert not decide.await_count
    exc = await selected_gate(browser, p)
    assert exc.form_question['label'] == 'Email'
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


async def test_plausible_competing_native_form_stays_generic(monkeypatch):
    other = field('Candidate reference', '#other', required=False,
                  form_selector='#other-form')
    p = inventory(other, field(), submit())
    browser, decide, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question'] is None
    assert decide.await_count == 1
    assert (await selected_gate(browser, p)).form_question is None


@pytest.mark.parametrize('value,eligible', [
    ('Apply now', True),
    ('  apply   NOW  ', True),
    ('Submit application', True),
    ('Submit', False),
    ('Apply now!', False),
    ('Apply', False),
    ('Next', False),
])
async def test_input_submit_marker_uses_only_exact_closed_allowlist(
        monkeypatch, value, eligible):
    native = field('go', '#native-submit', required=False, tag='input',
                   type='submit', submit=True, value=value, has_value=True)
    p = inventory(field(), native)
    browser, decide, _ = world(monkeypatch, p)
    report = await attempt(p)
    if eligible:
        assert report['form_question']['label'] == 'Email'
        assert not decide.await_count
    else:
        assert report['form_question'] is None
        assert decide.await_count == 1
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


@pytest.mark.parametrize('receiver', ['form_action', 'formaction'])
async def test_input_submit_marker_requires_safe_native_receiver(
        monkeypatch, receiver):
    native = field('go', '#native-submit', required=False, tag='input',
                   type='submit', submit=True, value='Apply now', has_value=True)
    native[receiver] = 'https://foreign.test/submit'
    p = inventory(field(), native)
    browser, _, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question'] is None
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)


@pytest.mark.parametrize('verb,key,answer,control', [
    ('fill', 'Location', 'Remote',
     field('Location', '#location', tag='select', type='select',
           options=[{'value': '', 'label': 'Choose'},
                    {'value': 'remote', 'label': 'Remote'}])),
    ('select', 'Email', 'synthetic@example.test', field()),
])
async def test_native_action_must_match_native_target_before_any_grant_or_ipc(
        monkeypatch, verb, key, answer, control):
    grant = {k: deepcopy(control.get(k)) for k in (
        'selector', 'label', 'question', 'tag', 'type',
        'form_selector', 'form_action')}
    grant.update(value=answer, url=URL)
    if control['tag'] == 'select':
        grant['options'] = deepcopy(control['options'])
    p = inventory(control, submit())
    browser, _, row = world(monkeypatch, p, grants={key: grant})
    exc = await selected_gate(browser, p, verb=verb, key=key,
                              selector=control['selector'], facts={key: answer})
    assert exc.form_question is None
    assert not browser.calls  # no evaluate and especially no mutating IPC


# The later architect amendment makes the positive native application
# marker a structural grant prerequisite. Wording-only classifier misses from
# the second source review are notes, not denial rules.
@pytest.mark.parametrize('case', [
    'input-button-sign-in', 'input-button-subscribe',
    'subscribe-action', 'signin-form-id', 'safe-next',
])
async def test_second_round_structural_marker_is_required_for_model_grants(
        monkeypatch, case):
    email = field()
    next_control = submit('Next')
    controls = [email, next_control]
    if case in {'input-button-sign-in', 'input-button-subscribe'}:
        controls.insert(1, field(
            'action', '#account-action', required=False, tag='input',
            type='button', submit=False, has_value=True,
            value='Sign in' if case == 'input-button-sign-in' else 'Subscribe'))
    elif case == 'subscribe-action':
        email['form_action'] = URL + '/subscribe'
        next_control['form_action'] = URL + '/subscribe'
    elif case == 'signin-form-id':
        email['form_selector'] = '#signin'
        next_control['form_selector'] = '#signin'
    p = inventory(*controls)
    browser, decide, _ = world(monkeypatch, p)
    result = await attempt(p)
    assert result['form_question'] is None
    assert decide.await_count == 1
    exc = await selected_gate(browser, p)
    assert exc.form_question is None
    assert all(c[0] not in {'fill', 'select'} for c in browser.calls)


async def test_second_round_native_resume_file_is_positive_application_marker(
        monkeypatch):
    resume = field('Upload resume', '#resume', required=True,
                   tag='input', type='file', submit=False)
    p = inventory(field(), resume, submit('Next'))
    browser, decide, _ = world(monkeypatch, p)
    # A same-form observed résumé input satisfies structural (c) even when
    # the continuation submit label is not in the closed final-action list.
    assert (await attempt(p))['form_question']['label'] == 'Email'
    assert not decide.await_count
    exc = await selected_gate(browser, p)
    assert exc.form_question['label'] == 'Email'


@pytest.mark.parametrize('label', ['I agree to arbitration', 'Acknowledgement'])
async def test_second_round_standalone_required_checkbox_remains_page_wall(
        monkeypatch, label):
    checkbox = field(label, '#standalone', tag='input', type='checkbox',
                     required=True, checked=False, in_form=False,
                     form_selector='', form_action='')
    p = inventory(field(), checkbox, submit())
    browser, decide, _ = world(monkeypatch, p)
    assert (await attempt(p))['form_question'] is None
    assert not decide.await_count
    assert all(x[0] not in {'fill', 'select'} for x in browser.calls)
