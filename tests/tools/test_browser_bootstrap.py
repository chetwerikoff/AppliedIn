"""Synthetic direct-application form provenance and first-field availability.

No BrowserSkill daemon, browser, network, owner's facts, or real application.
The fake evaluate result is separate from the PAGE-shaped inventory. It proves
the server consumes the transport shape, not that production JavaScript works.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms

# secure_world replaces the module Session with an inspection-only fake.
# Raw IPC regressions retain the actual producer class before that patch.
REAL_BSK_SESSION = bsk.Session

URL = 'http://127.0.0.1/fixture/example-co/job/1'
PK = 'example-co#1'


def field(label='Email', selector='#email', *, required=True, **extra):
    result = {
        'selector': selector, 'label': label, 'question': '',
        'tag': 'input', 'type': 'email' if label == 'Email' else 'text',
        'required': required, 'disabled': False, 'value': '', 'has_value': False,
        'in_form': True, 'form_selector': '#application', 'form_action': URL,
        'formaction': '', 'submit': False, 'checked': False, 'options': [],
        **extra,
    }
    result.setdefault('native_form_owner_selector',
                      result['form_selector'] if result['in_form'] else '')
    return result


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
         'native_form_owner_selector': c.get('native_form_owner_selector', ''),
         'form_action': c.get('form_action', ''),
         'formaction': c.get('formaction', ''), 'disabled': c['disabled'],
         'readonly': False, 'inert': False,
         'visible': c.get('synthetic_visible', True),
         'visible_text': c['label'] if c.get('type') in {'radio', 'checkbox'} else '',
         'diagnostic_visible_label': c.get('diagnostic_visible_label', ''),
         'options': deepcopy(c.get('options', [])),
         'combobox': deepcopy(c.get('combobox'))}
        for c in page['controls']]}


class Browser:
    def __init__(self, page, result=None, fail=False):
        self.current = page
        self.result = result if result is not None else witness(page)
        self.fail = fail
        self.calls = []
        self.pages = 0
        self.allow_mutations = False

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
            if not self.allow_mutations:
                pytest.fail('No mutation belongs in a pre-owner-answer regression')
            matches = [c for c in self.current['controls'] if c['selector'] == args[1]]
            assert len(matches) == 1
            target = matches[0]
            if args[0] in {'fill', 'select'}:
                target['value'] = args[-1]
                target['has_value'] = True
            elif args[0] == 'upload':
                assert target['type'] == 'file'
                target['files'] = ['Resume.pdf']
                target['has_value'] = True
            else:
                assert target['tag'] == 'input' and target['type'] in {'radio', 'checkbox'}
                if target['type'] == 'radio':
                    for c in self.current['controls']:
                        if c.get('type') == 'radio' and c.get('name') == target['name']:
                            c['checked'] = c is target
                else:
                    target['checked'] = True
            return {}
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


# The historic bootstrap tests used an HTTP-loopback URL and a partial row.
# Those are now rejected at application entry, before _bootstrap is reached.
# Give just the migrated cases a safe synthetic HTTPS row so they prove the
# current preparation, approved-writer and final raw-Submit behaviors.
SECURE_URL = 'https://example-co.test/job/1'


def secure_inventory(*controls):
    page = inventory(*(deepcopy(c) for c in controls), submit(), url=SECURE_URL)
    page['title'] = 'Synthetic Role'
    for control in page['controls']:
        assert control['form_action'] == URL
        control['form_action'] = SECURE_URL
    return page


def secure_world(monkeypatch, page, *, grants=None):
    """Use a real tracked HTTPS job for migrated apply/decision regressions."""
    assert page['url'] == SECURE_URL
    browser, decide, row = world(monkeypatch, page, grants=grants)
    row.update(company='example-co', job_id='1', jd_url=SECURE_URL,
               title=page['title'], ats='custom')
    return browser, decide, row


async def secure_attempt(page, *, facts=None):
    assert page['url'] == SECURE_URL
    return await forms.apply(
        SECURE_URL, 'example-co', facts or {}, 'synthetic',
        pk=PK, resume_path='Resume.pdf')


def visible_question(page, target):
    """An ordinary observed question is an answer request, never a writer grant."""
    return forms._ordinary_owner_question(page, target, PK)


async def prepared_without_unapproved_write(monkeypatch, page, *, facts=None, grants=None):
    """Inspect a synthetic form without legacy bootstrap/per-field approvals."""
    row = {
        'pk': PK, 'company': 'example-co', 'job_id': '1',
        'jd_url': SECURE_URL, 'title': page['title'], 'ats': 'custom',
        'status': 'submitting', 'possible_submission': False,
        'human_approved_answers': grants or {},
    }
    monkeypatch.setattr(forms, '_row', lambda pk: row if pk == PK else
                        pytest.fail('Unexpected synthetic row lookup'))
    browser = Browser(page)
    observed = await browser.page()
    observed, independent = await forms._observe_controls(browser, observed)
    assert observed is page
    assert set(independent) == {c['selector'] for c in page['controls']}
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)
    assert row['status'] == 'submitting' and not row['possible_submission']
    return browser, row, {}, []

async def final_submit_denied(monkeypatch, tmp_path, page, row, filled, history, *,
                              reason, expect_form=True):
    """Reach the one production final-click decision with synthetic valid PDF.

    Only the form/value evidence is deliberately defective: job, origin, file
    bytes and read-back attachment are consistent. The real application IPC
    producer must not call the fake raw transport or create a submission hold.
    """
    pdf = tmp_path / 'Resume.pdf'
    data = b'%PDF-1.4\nsynthetic example-co test-only file\n'
    pdf.write_bytes(data)
    attached = field('Resume', '#resume', required=False, type='file',
                     files=[pdf.name], has_value=True)
    attached['form_action'] = SECURE_URL
    assert len([c for c in page['controls'] if c['selector'] == '#submit']) == 1
    assert not any(c['selector'] == '#resume' for c in page['controls'])
    page['controls'].insert(-1, attached)
    submitted, raw_calls, reached_form = [], [], []
    controller = forms.ApplicationIPCController(
        PK, 'example-co', SECURE_URL, str(pdf), {}, filled, history, {},
        lambda: submitted.append(True))
    monkeypatch.setattr(controller, '_row', lambda: row)

    def pdf_identity():
        assert pdf.read_bytes() == data
        return len(data), controller.expected_digest

    controller.upload_identity = pdf_identity()
    controller.upload_completed = True
    monkeypatch.setattr(controller, '_row_pdf', pdf_identity)

    def preparation(origin):
        assert origin == SECURE_URL
        forms._tracked_preparation_job(PK, 'example-co', SECURE_URL)

    def dispatch(pk, path, **kwargs):
        assert (pk, path) == (PK, str(pdf.resolve()))
        assert not kwargs

    original_form_check = forms.check_form

    def checked_form(*args, **kwargs):
        reached_form.append(True)
        return original_form_check(*args, **kwargs)

    async def read_page(_session):
        return deepcopy(page)

    async def command(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(controller, '_fresh_preparation', preparation)
    monkeypatch.setattr(controller, '_page', read_page)
    monkeypatch.setattr(forms, 'check_dispatch', dispatch)
    monkeypatch.setattr(forms, 'check_form', checked_form)
    monkeypatch.setattr(forms, 'hold_possible_submission', lambda *_a, **_kw:
                        pytest.fail('Unapproved form reached the submission hold'))
    monkeypatch.setattr(bsk, 'command', command)
    raw_session = bsk.Session('apply')
    raw_session.id = 'synthetic-offline'
    raw_session.bind_application_controller(controller)
    with pytest.raises(forms.PreSubmitGate) as caught:
        await raw_session.application_effect('click', '#submit')
    assert caught.value.item == (2 if expect_form else 1)
    assert reached_form == ([True] if expect_form else [])
    assert raw_calls == [] and submitted == []
    assert controller.committed is False
    assert row['status'] == 'submitting' and row['possible_submission'] is False


async def test_derived_cross_tenant_board_gates_before_tracking_or_browser(monkeypatch):
    # An untrusted query cannot manufacture a Greenhouse job/board route.
    url = 'https://employer.test/job/1?gh_jid=42&board=other-tenant'
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)

    def unavailable_row(*args, **kwargs):
        raise OSError('tracking unavailable')

    monkeypatch.setattr(forms, '_row', unavailable_row)
    monkeypatch.setattr(bsk, 'Session', lambda *a: pytest.fail(
        'Malformed job query must not open the application browser'))
    result = await forms.apply(url, 'example-co', {}, 'synthetic',
                               pk=PK, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'Malformed Greenhouse job identity' in result['question']

    # r06 moves real ATS tenant/board equality to the ONE final click guard.
    # A safely reached but unrelated allowlisted tenant has no Submit IPC.
    trusted = 'https://careers.example-co.test/job/1'
    foreign = 'https://job-boards.greenhouse.io/other-tenant/jobs/1'
    row = {'pk': PK, 'company': 'example-co', 'job_id': '1',
           'jd_url': trusted, 'ats': 'greenhouse',
           'greenhouse_board': 'example-co', 'status': 'submitting',
           'possible_submission': False}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    # The actual final guard must deny when the configured tenant cannot be
    # freshly verified, independently of any page-supplied origin hint.
    monkeypatch.setattr(forms, '_configured_alias', lambda *_a, **_kw: None)
    writes = []
    controller = forms.ApplicationIPCController(
        PK, 'example-co', trusted, '', {}, {}, [], {},
        lambda: writes.append('submit-started'))
    target = {'selector': '#submit', 'label': 'Submit application',
              'type': 'submit', 'tag': 'button', 'submit': True,
              'in_form': True, 'form_selector': '#app',
              'form_action': foreign}
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.check_pre_submit(
            None, {'url': foreign, 'controls': [target]}, target)
    assert refusal.value.item == 1
    assert writes == [] and controller.committed is False


@pytest.mark.parametrize('extra_control', [
    field('I agree to terms', '#terms', tag='input', type='checkbox',
          required=True, checked=False),
    field('Password', '#secret', type='password', required=True),
    field('Arbitration consent', '#legal', tag='input', type='radio',
          name='legal', question='Arbitration consent', required=True),
])
async def test_login_boundary_and_missing_assent_fact_use_current_paths(
        monkeypatch, tmp_path, extra_control):
    page = secure_inventory(field(), extra_control)
    kind = extra_control.get('type')
    if kind == 'checkbox':
        browser, row, filled, history = await prepared_without_unapproved_write(
            monkeypatch, page)
        await final_submit_denied(
            monkeypatch, tmp_path, page, row, filled, history,
            reason='required application field is empty')
    else:
        browser = Browser(page)
        if kind == 'password':
            action = {'action': 'fill', 'selector': '#email', 'fact': 'Email'}
            facts = {'Email': 'synthetic@example.test'}
        else:
            action = {'action': 'choose', 'selector': '#legal',
                      'fact': 'Arbitration consent'}
            facts = {}
        with pytest.raises(forms.Gate) as denial:
            await forms.execute(
                browser, page, action, facts=facts, filled={}, resume_path='',
                company='example-co', jd_text='', resume_tex='', github='',
                pk=PK, job_url=SECURE_URL)
        if kind == 'password':
            assert 'owner account action' in str(denial.value)
        else:
            assert denial.value.reason == 'owner_question'
            assert denial.value.form_question['selector'] == '#legal'
        assert not any(x[0] in {'fill', 'click', 'upload'} for x in browser.calls)

async def test_duplicate_question_can_fill_two_current_receivers_without_grants():
    # A shared question name is not a grant collision: both current controls
    # may be filled, then the final readback checks the actual form.
    p = secure_inventory(field('First', '#a', question='Contact details'),
                         field('Second', '#b', question='Contact details'))
    browser = Browser(p)
    browser.allow_mutations = True
    filled = {}
    for selector in ('#a', '#b'):
        receipt = await forms.execute(
            browser, p,
            {'action': 'fill', 'selector': selector, 'fact': 'Contact details'},
            facts={'Contact details': 'Synthetic answer'}, filled=filled,
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=SECURE_URL)
        assert receipt['action'] == 'fill'
    assert filled == {'#a': 'Synthetic answer', '#b': 'Synthetic answer'}
    assert [c[0] for c in browser.calls] == ['fill', 'fill']
    attached = field('Resume', '#resume', required=False, type='file',
                     files=['Resume.pdf'], has_value=True)
    attached['form_action'] = SECURE_URL
    p['controls'].insert(-1, attached)
    forms.check_form(p, filled, True, 'Resume.pdf')

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


@pytest.mark.asyncio
async def test_native_next_is_still_a_raw_submit_and_requires_final_readback(
        monkeypatch, tmp_path):
    # Caption "Next" is not permission to bypass native form submission.
    from tests.tools.test_browser_pre_submit_check import _world
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    target['label'] = 'Next'
    page['controls'][0]['value'] = ''
    assert forms.native_submit(target)
    assert not forms.is_submission(target)
    with pytest.raises(forms.PreSubmitGate) as denied:
        await controller.before_ipc(None, ('click', '#submit'), {})
    assert denied.value.item == 2
    assert not controller.committed and not fixture.held

def test_direct_owner_answer_banks_fact_and_requeues_without_field_grant(monkeypatch):
    from agent import run
    answer = 'synthetic@example.test'
    page = inventory(field(), submit())
    question = forms._ordinary_owner_question(page, page['controls'][0], PK)
    row = {'pk': PK, 'status': 'needs_human', 'jd_url': URL,
           'company': 'example-co', 'gate_source': 'applier',
           'gate_call_id': 'direct', 'possible_submission': False,
           'gate_pending': {'question': 'Email address needed',
                            'form_question': question}}

    class Tracking:
        def get(self, pk):
            assert pk == PK
            return deepcopy(row)

        def set_status(self, pk, status, **changes):
            assert pk == PK
            row.update(status=status, **changes)

    tracking = Tracking()
    saved = {}
    bank = SimpleNamespace(put=lambda label, value, scope, **kwargs:
                           saved.update(label=label, value=value))
    stores = SimpleNamespace(tracking=tracking, answer_bank=bank)
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(run, '_enqueue_apply', lambda *args, **kwargs:
                        {'result': 'queued', 'pk': PK})
    tokens = _owner_reply(tracking, row)
    stale = run.resume_job(
        PK, answer, stores, disposition='answer',
        expected_gate_generation=tokens['expected_gate_generation'] + '-stale',
        expected_question_identity=tokens['expected_question_identity'])
    assert stale['reason'] == 'invalid_answer'
    assert saved == {}
    assert run.resume_job(PK, answer, stores, **tokens)['result'] == 'queued'
    assert saved == {'label': 'Email', 'value': answer}
    assert not row.get('human_approved_answers')
    assert row['gate_pending'] == {}
    assert row['status'] == 'tailored'

@pytest.mark.asyncio
async def test_radio_choice_uses_current_caption_without_a_grant():
    approved = field('Approved', '#approved', type='radio', name='review_status',
                     question='Review status', required=True,
                     value='approved', has_value=True, checked=False)
    pending = field('Pending', '#pending', type='radio', name='review_status',
                    question='Review status', required=True,
                    value='pending', has_value=True, checked=False)
    page = secure_inventory(approved, pending)
    browser = Browser(page)
    browser.allow_mutations = True
    receipt = await forms.execute(
        browser, page, {'action': 'choose', 'selector': '#approved',
                        'fact': 'Review status'},
        facts={'Review status': 'Approved'}, filled={}, resume_path='',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=PK, job_url=SECURE_URL)
    assert receipt['action'] == 'choose'
    assert browser.calls[-1] == ('click', '#approved')
    assert page['controls'][0]['checked'] is True
    assert page['controls'][1]['checked'] is False

def test_direct_owner_question_identity_is_bound_to_the_pending_row():
    from agent.run import _direct_form_label
    page = inventory(field('Location', '#location'), submit())
    question = forms._ordinary_owner_question(page, page['controls'][0], PK)
    row = {'pk': PK, 'status': 'needs_human', 'gate_source': 'applier',
           'gate_call_id': 'direct', 'gate_pending': {'form_question': question}}
    assert _direct_form_label(PK, row) == 'Location'
    assert _direct_form_label('another-job', row) is None
    assert _direct_form_label(PK, {**row, 'status': 'tailored'}) is None
    assert _direct_form_label(PK, {**row, 'gate_pending': {
        'form_question': {**question, 'pk': 'another-job'}}}) is None

@pytest.mark.asyncio
async def test_unlabelled_but_observed_question_can_use_explicit_fact():
    # Ordinary preparation does not mint or require a control-label witness.
    p = secure_inventory(field('', '#email', question='Email address'))
    browser = Browser(p)
    browser.allow_mutations = True
    filled = {}
    await forms.execute(
        browser, p, {'action': 'fill', 'selector': '#email',
                    'fact': 'Email address'},
        facts={'Email address': 'synthetic@example.test'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=SECURE_URL)
    assert filled == {'#email': 'synthetic@example.test'}
    assert browser.calls == [('fill', '#email', '--value', 'synthetic@example.test')]

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



@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_checked_ordinary_assent_choice_is_not_an_extra_submit_item(kind):
    bound = field('I agree to terms', '#bound-choice',
                  tag='input', type=kind, required=True,
                  checked=True, has_value=True, name='attestation',
                  question='Arbitration consent' if kind == 'radio' else '',
                  value='accept')
    members = [bound]
    if kind == 'radio':
        members.append(field(
            'I decline', '#decline-choice', tag='input', type='radio',
            required=True, checked=False, has_value=True, name='attestation',
            question='Arbitration consent', value='decline'))
    # Legal answers are normal answers, not a seventh pre-Submit item. A
    # finished synthetic form satisfies the ordinary required-choice check.
    filename = 'Synthetic Resume.pdf'
    page = secure_inventory(
        *members,
        field('Resume', '#resume', type='file', required=False, files=[filename]))
    forms.check_form(page, {}, uploaded=True, attachment=filename)

async def test_material_radio_with_missing_fact_uses_ordinary_owner_question(monkeypatch):
    radio = field('Accept', '#legal-yes', tag='input', type='radio',
                  name='legal', question='Arbitration consent', required=True,
                  value='yes', has_value=True)
    declined = field('Decline', '#legal-no', tag='input', type='radio',
                     name='legal', question='Arbitration consent', required=True,
                     value='no', has_value=True)
    page = secure_inventory(field(), radio, declined)
    browser = Browser(page)
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            browser, page,
            {'action': 'choose', 'selector': '#legal-yes',
             'fact': 'Arbitration consent'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=SECURE_URL)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == '#legal-yes'
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

async def test_model_prerequisite_gate_question_origin_is_forwarded(monkeypatch):
    page = secure_inventory(field())
    browser, decide, row = secure_world(monkeypatch, page)
    model_question = 'Synthetic prerequisite: approve test-only facts before continuing.'
    decide.return_value = {'action': 'gate', 'question': model_question}
    report = await secure_attempt(page)

    assert decide.await_count == 1
    prompt, model_page, model_history = decide.await_args.args
    assert 'Available fact keys: []' in prompt
    assert model_page['controls'][0]['label'] == 'Email'
    assert model_history == []
    assert report['status'] == 'gate'
    assert report['reason'] == 'agent_review_unavailable'
    assert report['review_route'] == 'luna'
    assert report['form_question'] is None
    assert 'Last button:' in report['question']
    assert model_question not in report['question']
    assert row['status'] == 'submitting'
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)



def _material_readers_with_synthetic_dom(edit):
    """Two actual production readers over one offline synthetic DOM shape."""
    from tests.tools.test_browser_application_entry import dom
    from tools.browser_skill_dom import PAGE
    opener = '(() => {\n'
    assert PAGE.startswith(opener) and forms._FIXED_CONTROL_WITNESS.startswith(opener)
    spec = {'url': SECURE_URL}
    return (dom(spec, PAGE.replace(opener, opener + edit, 1)),
            dom(spec, forms._FIXED_CONTROL_WITNESS.replace(opener, opener + edit, 1)))


@pytest.mark.parametrize('kind', ['hidden-name', 'select-options', 'visible-label'])
async def test_actual_readers_supply_only_proven_visible_material_label(kind, monkeypatch):
    if kind == 'hidden-name':
        edit = """
          const e = document.getElementById('email');
          e.setAttribute('type', 'hidden');
          e.setAttribute('name', 'consent-HIDDEN_NAME_SENTINEL');
          e.getClientRects = () => [];
          node('input', {id:'required-other', type:'text', required:true},
               document.getElementById('application-form'));
        """
        match, unsafe = 'consent', 'HIDDEN_NAME_SENTINEL'
    elif kind == 'select-options':
        edit = """
          const e = document.getElementById('email');
          e.tagName = 'SELECT'; e.localName = 'select';
          e.removeAttribute('type'); e.type = ''; e.required = true;
          const option = node('option',
            {text:'Privacy consent OPTION_CAPTION_SENTINEL'}, e);
          option.value = 'OPTION_VALUE_SENTINEL';
          e.options = [option];
        """
        match, unsafe = 'Privacy', 'OPTION_CAPTION_SENTINEL'
    else:
        edit = """
          const e = document.getElementById('email');
          e.required = true;
          node('label', {'for':'email',
            text:'I agree to terms ' + 'x'.repeat(140) + 'VISIBLE_TAIL_SENTINEL'},
            document.getElementById('application-form'));
        """
        match, unsafe = 'agree', 'VISIBLE_TAIL_SENTINEL'
    page, independent = _material_readers_with_synthetic_dom(edit)
    original = next(c for c in page['controls'] if c['selector'] == '#email')
    observed = next(c for c in independent['controls'] if c['selector'] == '#email')
    assert unsafe in original['label']
    if kind == 'hidden-name':
        assert original['type'] == 'hidden'
        assert observed['visible'] is False
    elif kind == 'select-options':
        assert original['tag'] == 'select'
        assert 'OPTION_VALUE_SENTINEL' in str(original['options'])
        assert observed['diagnostic_visible_label'] == ''
    else:
        assert original['tag'] == 'input'
        assert observed['visible'] is True
        assert observed['diagnostic_visible_label'].startswith('I agree to terms ')

    class OfflineReaderSession:
        async def call(self, operation, script):
            assert operation == 'evaluate' and script == forms._FIXED_CONTROL_WITNESS
            return {'value': deepcopy(independent)}

        async def page(self):
            return deepcopy(page)

    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting', 'human_approved_answers': {}})
    # #241 removes the bootstrap-only fallback which interpreted arbitrary
    # material-sounding labels as a separate write barrier. An actual
    # form-owned material SELECT may still request its exact owner grant.
    try:
        prepared = await forms._bootstrap(
            OfflineReaderSession(), page, PK, {}, SECURE_URL)
    except forms.Gate as denial:
        assert not str(denial).startswith(
            'Material assent outside a bound choice needs human inspection.')
        if kind == 'select-options' and denial.reason == 'owner_question':
            question = denial.form_question
            assert question['tag'] == question['type'] == 'select'
            assert question['options'] and question['pk'] == PK
        else:
            assert denial.reason in {'owner_question', 'unknown_field'}
    else:
        assert prepared == page


async def test_actual_reader_hidden_role_is_not_owner_material_type():
    """A hidden input's arbitrary ARIA role is matching input, not display-safe type."""
    role = 'HIDDEN_ROLE_SENTINEL_' + 'x' * 240
    page, independent = _material_readers_with_synthetic_dom("""
        const e = document.getElementById('email');
        e.setAttribute('type', 'hidden');
        e.setAttribute('name', 'consent-HIDDEN_NAME_SENTINEL');
        e.setAttribute('role', 'HIDDEN_ROLE_SENTINEL_' + 'x'.repeat(240));
        e.getClientRects = () => [];
        node('input', {id:'required-other', type:'text', required:true},
             document.getElementById('application-form'));
    """)
    original = next(c for c in page['controls'] if c['selector'] == '#email')
    observed = next(c for c in independent['controls']
                    if c['selector'] == '#email')
    assert original['type'] == role and observed['type'] == role
    assert 'consent-HIDDEN_NAME_SENTINEL' in original['label']
    assert original['in_form'] is True and observed['visible'] is False

    class OfflineReaderSession:
        async def call(self, operation, script):
            assert operation == 'evaluate' and script == forms._FIXED_CONTROL_WITNESS
            return {'value': deepcopy(independent)}

        async def page(self):
            return deepcopy(page)

    # A malicious hidden ARIA role never upgrades the control into a
    # material-choice grant, and no longer forces an early diagnostic gate.
    try:
        prepared = await forms._bootstrap(
            OfflineReaderSession(), page, PK, {}, SECURE_URL)
    except forms.Gate as denial:
        detail = str(denial)
        assert not detail.startswith(
            'Material assent outside a bound choice needs human inspection.')
        assert 'HIDDEN_ROLE_SENTINEL_' not in detail
        assert 'HIDDEN_NAME_SENTINEL' not in detail
    else:
        assert prepared == page


@pytest.mark.parametrize('queued_worker', [False, True], ids=['direct', 'queued-worker'])
@pytest.mark.parametrize('material', [False])
async def test_material_gate_owner_detail_is_not_ordinary_event_or_info_log(
        monkeypatch, material, queued_worker):
    """Actual synthetic Gate result -> owner row, event, INFO; no browser/apply."""
    import logging
    from io import StringIO

    from agent import run
    from core.logging import _ConsoleFormatter, _JsonFormatter

    label = 'I agree to terms OWNER_VISIBLE_LOG_SENTINEL'
    page = secure_inventory(field(), field(
        label, '#material', required=False,
        diagnostic_visible_label=label)) if material else secure_inventory(field())
    _, decide, _ = secure_world(monkeypatch, page)
    if not material:
        decide.return_value = {
            'action': 'gate', 'question': 'Synthetic OTHER_GATE_SENTINEL'}
    gate = await secure_attempt(page)
    assert gate['status'] == 'gate'
    assert ('OWNER_VISIBLE_LOG_SENTINEL' in gate['question']) is material
    assert (gate.get('material_assent_diagnostic') is True) is material

    row = {'pk': PK, 'status': 'tailored', 'company': 'example-co',
           'jd_url': SECURE_URL, 'title': 'Synthetic Role'}
    class Tracking:
        def get(self, pk):
            assert pk == PK
            return dict(row)

        def get_current(self, pk):
            assert pk == PK
            return dict(row)

        def set_status(self, pk, status, **updates):
            assert pk == PK
            row.update(status=getattr(status, 'value', status), **updates)

    stores = SimpleNamespace(
        tracking=Tracking(), secrets=None,
        answer_bank=SimpleNamespace(all_facts=lambda company: {}))
    events = []
    monkeypatch.setattr('core.events.emit',
                        lambda kind, **kwargs: events.append((kind, kwargs)))
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *a, **kw: False)
    monkeypatch.setattr(run, '_jd_meta', AsyncMock(
        return_value={'text': 'Synthetic offline job description'}))
    monkeypatch.setattr(run, '_commit_posting_meta',
                        lambda pk, current, meta, stores: dict(current))
    monkeypatch.setattr('tools.posting_guard.authorised', lambda *a, **kw: True)
    monkeypatch.setattr(run, '_no_sponsorship', lambda text: False)
    monkeypatch.setattr('core.rotation.ensure', lambda *a, **kw: None)
    monkeypatch.setattr('core.profiles.resolve_for', lambda row: None)
    monkeypatch.setattr('core.profiles.expand_all', lambda facts: facts)
    monkeypatch.setattr('tools.credentials.get_login', lambda *a, **kw: None)
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: 'Synthetic.pdf')
    monkeypatch.setattr(
        run, 'get_settings', lambda: SimpleNamespace(browser_model='offline'))
    monkeypatch.setattr(
        'tools.browser_runtime.configuration', lambda: {'engine': 'browser_skill'})
    fake_apply = AsyncMock(return_value=gate)
    monkeypatch.setattr('tools.browser_apply.apply', fake_apply)

    # get_logger uses a non-propagating, application-formatted StreamHandler.
    # A full suite can add pytest/root handlers that mirror its stdout output;
    # count messages only from this one real application sink, not all of capfd.
    assert run.log.propagate is False
    app_handlers = [handler for handler in run.log.handlers
                    if type(handler) is logging.StreamHandler
                    and isinstance(handler.formatter,
                                   (_ConsoleFormatter, _JsonFormatter))]
    assert app_handlers
    with StringIO() as app_output, monkeypatch.context() as log_capture:
        log_capture.setattr(run.log, 'level', logging.INFO)
        log_capture.setattr(app_handlers[0], 'stream', app_output)
        if queued_worker:
            # Exercise the synchronous worker's actual INFO sink and async apply.
            monkeypatch.setattr(run, 'make_stores', lambda: stores)
            queue = SimpleNamespace(retry=Mock(), done=Mock())
            item = {'pk': PK}
            result = run.run_queued(item, queue)
            queue.done.assert_called_once_with(item)
            queue.retry.assert_not_called()
        else:
            result = await run._apply_direct(PK, stores)
        log_output = app_output.getvalue()
    assert fake_apply.await_count == 1
    assert result['result'] == 'gated' and result['question'] == gate['question']
    assert (result.get('material_assent_diagnostic') is True) is material
    assert row['status'] == 'needs_human'
    assert row['gate_pending']['question'] == gate['question']
    assert row['gate_pending']['review_route'] == 'agent_unavailable'
    assert row['gate_reason'] == 'unknown_field'
    ordinary = [args['detail'] for event, args in events if event == 'gate']
    assert ordinary == (['material-assent: human inspection required']
                        if material else [gate['question']])
    lines = log_output.splitlines()
    gated_lines = [text for text in lines if 'gated pk=' in text]
    assert len(gated_lines) == 1
    assert 'INFO' in gated_lines[0]
    assert f'gated pk={PK}:' in gated_lines[0]
    assert ordinary[0] in log_output
    if queued_worker:
        queued_lines = [text for text in lines if 'apply: ' in text]
        assert len(queued_lines) == 1
        assert 'INFO' in queued_lines[0]
        if material:
            assert "'question': 'material-assent: human inspection required'" in queued_lines[0]
        else:
            assert 'OTHER_GATE_SENTINEL' in queued_lines[0]
    if material:
        assert 'OWNER_VISIBLE_LOG_SENTINEL' in row['gate_pending']['question']
        assert 'gated pk=' + PK + ': material-assent: human inspection required' in gated_lines[0]
        assert 'OWNER_VISIBLE_LOG_SENTINEL' not in log_output
        assert all('OWNER_VISIBLE_LOG_SENTINEL' not in text for text in ordinary)
    else:
        assert 'OTHER_GATE_SENTINEL' in ordinary[0]
        assert 'OTHER_GATE_SENTINEL' in gated_lines[0]


def test_fixed_read_only_script_contains_effective_dom_predicates():
    # Static unit coverage, not the outstanding real 127.0.0.1 smoke.
    script = forms._FIXED_CONTROL_WITNESS
    assert "for (let n=e; n && n.nodeType===1; n=n.parentElement)" in script
    assert 'Number(style.opacity) > 0' in script
    assert "e.matches(':disabled')" in script


async def test_unrelated_native_search_does_not_veto_application(
        monkeypatch, tmp_path):
    search = field('Search jobs', '#search', required=False, type='search',
                   form_selector='#site-search')
    search_submit = submit('Search', selector='#search-submit',
                           form_selector='#site-search')
    p = secure_inventory(search, search_submit, field())
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, p)
    page, fixed = await forms._observe_controls(browser, p)
    email = forms.control(page, {'selector': '#email'})
    q = forms._ordinary_owner_question(page, email, PK)
    assert (q['label'], q['selector'], q['form_selector'], q['form_action']) == (
        'Email', '#email', '#application', SECURE_URL)
    assert q['form_selector'] != search['form_selector']
    assert [item for item in page['controls']
            if item.get('form_selector') == '#application']
    # The second native Search Submit has a different owner and cannot be
    # borrowed by the application. It must not hide the Email question
    # during preparation or authorize either form's irreversible click.
    await final_submit_denied(
        monkeypatch, tmp_path, p, row, filled, history,
        reason='Competing native Submit owners', expect_form=False)
    assert not filled and not history
    assert not any(x[0] in {'fill', 'select', 'click', 'upload'}
                   for x in browser.calls)

async def test_plausible_competing_native_form_stays_generic(
        monkeypatch, tmp_path):
    other = field('Candidate reference', '#other', required=False,
                  form_selector='#other-form')
    p = secure_inventory(other, field())
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, p)
    page, fixed = await forms._observe_controls(browser, p)
    email = forms.control(page, {'selector': '#email'})
    question = forms._ordinary_owner_question(page, email, PK)
    assert question['label'] == 'Email'
    assert question['form_selector'] == '#application'
    assert question['form_selector'] != other['form_selector']
    # The separate optional native form is not an approval for Email. Neither
    # form can commit while the real applicant field is still unanswered.
    await final_submit_denied(
        monkeypatch, tmp_path, p, row, filled, history,
        reason='Required field is empty: Email')
    assert not filled and not history and not row['human_approved_answers']
    assert not any(x[0] in {'fill', 'select', 'click', 'upload'}
                   for x in browser.calls)


@pytest.mark.parametrize('value', [
    'Apply now', '  apply   NOW  ', 'Submit application', 'Submit',
    'Apply now!', 'Apply', 'Next',
])
async def test_native_input_submit_is_irreversible_regardless_of_value(
        monkeypatch, tmp_path, value):
    # r06 removes the obsolete early closed allowlist. A real native
    # input[type=submit] can submit even if its caption says "Next";
    # an empty Email must prevent ALL of them at the final raw-click seam.
    p = secure_inventory(field())
    native = p['controls'][-1]
    native.update(tag='input', type='submit', submit=True,
                  label='go', value=value, has_value=True)
    assert forms.native_submit(native)
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, p)
    assert not filled and not history
    await final_submit_denied(
        monkeypatch, tmp_path, p, row, filled, history,
        reason='Required field is empty: Email')
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'}
                   for c in browser.calls)


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


async def test_second_round_native_resume_file_is_positive_application_marker(
        monkeypatch):
    # The file input and a native Next/Submit remain observable, but neither
    # is per-step admission for the unanswered Email. The final form guard
    # owns all mandatory field validation.
    page = secure_inventory(
        field(), field('Upload resume', '#resume', type='file', required=True))
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    resume, email = page['controls'][1], page['controls'][0]
    assert resume['type'] == 'file' and resume['in_form']
    assert page['controls'][-1]['type'] == 'submit'
    assert visible_question(page, email)['label'] == 'Email'
    assert row['human_approved_answers'] == {}
    assert filled == {} and history == []
    # The current final check needs the staged attachment before it can
    # report the independently missing mandatory Email.
    resume['files'] = ['Resume.pdf']
    with pytest.raises(forms.Gate, match='required application field is empty'):
        forms.check_form(page, filled, uploaded=True, attachment='Resume.pdf')
    assert [call[0] for call in browser.calls] == ['evaluate']

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


GH_PK = 'example-co#123456'
GH_WRAP = 'https://careers.example-co.test/openings/platform-engineer?gh_jid=123456'
GH_DIRECT = 'https://job-boards.greenhouse.io/embed/job_app?for=example-co&token=123456'
GH_CAREERS = 'https://careers.example-co.test/openings'


def _greenhouse_row():
    return {'pk': GH_PK, 'company': 'example-co',
            'greenhouse_board': 'example-co', 'job_id': '123456',
            'ats': 'greenhouse', 'jd_url': GH_WRAP,
            'status': 'submitting', 'human_approved_answers': {},
            'possible_submission': False}


def _greenhouse_config(monkeypatch, tmp_path, *, careers_url=GH_CAREERS):
    from agent import run

    config = tmp_path / 'watchlist.yaml'
    config.write_text(
        'companies:\n  - name: example-co\n    ats: greenhouse\n'
        '    board: example-co\n'
        f'    careers_url: {careers_url}\n'
        '    discovery: feed\n    mode: gated\n')
    settings = SimpleNamespace(config_dir=str(tmp_path))
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    monkeypatch.setattr(run, 'get_settings', lambda: settings)
    return config


def _greenhouse_session(monkeypatch, tmp_path, page=None, *, careers_url=GH_CAREERS):
    _greenhouse_config(monkeypatch, tmp_path, careers_url=careers_url)
    if page is None:
        page = inventory(field(form_action=GH_DIRECT),
                         submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, decide, _ = world(monkeypatch, page)
    original_call = browser.call

    def scoped(observed):
        observed = deepcopy(observed)
        observed['greenhouse'] = {
            'root': {'selector': '#application', 'action': GH_DIRECT},
            'frames': [], 'response_controls': False, 'badge_responses': 0}
        return observed

    async def call(*args, **kwargs):
        # Keep the original synthetic transport and revocation seams, while
        # representing the new scoped PAGE separately from its fixed witness.
        if args == ('navigate', GH_DIRECT):
            return await browser.navigate(GH_DIRECT)
        if args == ('evaluate', forms.GREENHOUSE_PAGE):
            browser.calls.append(args)
            return {'value': scoped(await browser.page())}
        if args == ('evaluate', forms._GREENHOUSE_FIXED_CONTROL_WITNESS):
            result = await original_call('evaluate', forms._FIXED_CONTROL_WITNESS)
            browser.calls[-1] = args
            result['value'] = scoped(result['value'])
            return result
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(browser, 'call', call)
    row = _greenhouse_row()
    monkeypatch.setattr(forms, '_row', lambda _pk, *args, **kwargs: row)
    return browser, decide, row


def _greenhouse_provenance(row, configs=None):
    from discovery.watchlist import CompanyConfig
    from tools.claude_chrome import verified_greenhouse_alias

    configs = configs if configs is not None else [
        CompanyConfig(name='example-co', ats='greenhouse',
                      careers_url='https://careers.example-co.test/openings',
                      board='example-co')]
    return verified_greenhouse_alias(
        row, pk=GH_PK, company='example-co', url=GH_WRAP, configs=configs)


def test_explicit_greenhouse_tenant_requires_canonical_row_and_config():
    from discovery.watchlist import CompanyConfig
    from tools.claude_chrome import direct_board_url

    row = _greenhouse_row()
    alias = _greenhouse_provenance(row)
    assert alias and alias.url == GH_DIRECT
    unpinned = {**row}
    unpinned.pop('greenhouse_board')
    assert _greenhouse_provenance(unpinned) is None
    assert direct_board_url(GH_WRAP) == ''  # The Claude engine is unchanged.
    assert _greenhouse_provenance({**row, 'job_id': '654321'}) is None
    assert _greenhouse_provenance({**row, 'pk': 'example-co#654321'}) is None
    assert _greenhouse_provenance({**row, 'ats': 'custom'}) is None
    assert _greenhouse_provenance({**row, 'jd_url': GH_WRAP + '#changed'}) is None

    good = CompanyConfig(name='example-co', ats='greenhouse',
                         board='example-co',
                         careers_url='https://careers.example-co.test/openings')
    for board in ('https://job-boards.greenhouse.io/example-co', 'EXAMPLE-CO',
                  'example.co/path', 'example%2dco', 'éxample-co', 'foo@bar'):
        assert _greenhouse_provenance(row, [good.model_copy(
            update={'board': board})]) is None
    for careers in ('http://careers.example-co.test/openings',
                    'http://careers.example-co.test',
                    'https://user@careers.example-co.test',
                    'https://careers.example-co.test:443',
                    'https://careers.example-co.test?x=1',
                    'https://careers.example-co.test?',
                    'https://careers.example-co.test#fragment',
                    'https://careers.example-co.test:443/openings',
                    'https://user@careers.example-co.test/openings',
                    'https://careers.example-co.test/openings?x=1',
                    'https://careers.example-co.test/openings#fragment',
                    'https://careers.example-co.test/openings/%2e',
                    'https://careers.example-co.test/openings//other',
                    'https://careers.example-co.test/openings/../other',
                    'https://CAREERS.example-co.test/openings'):
        assert _greenhouse_provenance(row, [good.model_copy(
            update={'careers_url': careers})]) is None
    assert _greenhouse_provenance(row, [good, good]) is None
    assert _greenhouse_provenance(row, []) is None
    assert _greenhouse_provenance(row, [good.model_copy(
        update={'ats': 'custom'})]) is None


def test_configured_greenhouse_origin_requires_exact_query_scope():
    from discovery.watchlist import CompanyConfig
    from tools.claude_chrome import verified_greenhouse_alias

    row = _greenhouse_row()
    for careers_url in ('https://careers.example-co.test',
                        'https://careers.example-co.test/'):
        config = CompanyConfig(name='example-co', ats='greenhouse',
                               board='example-co', careers_url=careers_url)
        # A bare host certifies only /?gh_jid=..., not /openings.
        assert _greenhouse_provenance(row, [config]) is None

    root = 'https://careers.example-co.test/?gh_jid=123456'
    valid = {**row, 'jd_url': root}
    config = CompanyConfig(name='example-co', ats='greenhouse',
                           board='example-co',
                           careers_url='https://careers.example-co.test')
    alias = verified_greenhouse_alias(
        valid, pk=GH_PK, company='example-co', url=root, configs=[config])
    assert alias is not None and alias.url == GH_DIRECT
    assert alias.wrapper == root and alias.careers_url == config.careers_url


def test_empty_path_greenhouse_wrapper_stays_rejected_for_origin_config():
    from discovery.watchlist import CompanyConfig

    config = CompanyConfig(name='example-co', ats='greenhouse',
                           board='example-co',
                           careers_url='https://careers.example-co.test')
    row = {**_greenhouse_row(),
           'jd_url': 'https://careers.example-co.test?gh_jid=123456'}
    assert _greenhouse_provenance(row, [config]) is None


def test_unicode_company_collisions_do_not_authorize_alias():
    from discovery.watchlist import CompanyConfig
    from tools.claude_chrome import verified_greenhouse_alias

    row = {**_greenhouse_row(), 'company': 'Exämple-Co',
           'pk': 'exämple-co#123456'}
    first = CompanyConfig(name='Exämple-Co', ats='greenhouse',
                          board='example-co',
                          careers_url='https://careers.example-co.test/openings')
    duplicate = first.model_copy(update={'name': 'EXÄMPLE-CO'})
    assert verified_greenhouse_alias(
        row, pk=row['pk'], company=row['company'], url=row['jd_url'],
        configs=[first]) is not None
    assert verified_greenhouse_alias(
        row, pk=row['pk'], company=row['company'], url=row['jd_url'],
        configs=[first, duplicate]) is None


@pytest.mark.parametrize('query', [
    'gh_jid=654321', 'gh_jid=123456&gh_jid=123456',
    'gh_jid=%31%32%33%34%35%36', 'gh_jid=１２３４５６',
    'GH_JID=123456', 'gh_jid=123456&board=example-co',
    'gh_jid=123456&board=another', 'gh_jid=123456&Board=example-co',
    'gh_jid=123456&src=feed',
])
def test_only_one_exact_literal_gh_jid_is_new_alias(query):
    from tools.claude_chrome import greenhouse_alias_candidate

    from tools.claude_chrome import verified_greenhouse_alias
    from discovery.watchlist import CompanyConfig

    row = {**_greenhouse_row(), 'jd_url': GH_WRAP.split('?')[0] + '?' + query}
    assert not greenhouse_alias_candidate(row['jd_url']) or query == 'gh_jid=654321'
    assert verified_greenhouse_alias(
        row, pk=GH_PK, company='example-co', url=row['jd_url'],
        configs=[CompanyConfig(
            name='example-co', ats='greenhouse', board='example-co',
            careers_url='https://careers.example-co.test/openings')]) is None


def test_alias_effective_route_guard_is_job_scoped_and_legacy_unchanged():
    alias = _greenhouse_provenance(_greenhouse_row())
    assert forms.navigation_allowed(GH_DIRECT, provenance=alias)
    assert not forms.navigation_allowed(GH_DIRECT + '/apply', provenance=alias)
    assert not forms.navigation_allowed(GH_WRAP, provenance=alias)
    assert forms.navigation_allowed(GH_DIRECT, GH_DIRECT)  # Legacy explicit origin.
    for route in (
        GH_DIRECT + '/../654321', GH_DIRECT + '/./apply',
        GH_DIRECT + '/%2e%2e/654321', GH_DIRECT + '/%2fother',
        GH_DIRECT + '//apply', GH_DIRECT + r'/\..\other',
        GH_DIRECT + '?src=feed', GH_DIRECT + '#different',
        'https://job-boards.greenhouse.io/example-co/jobs/654321',
        'https://job-boards.greenhouse.io/other/jobs/123456',
        'https://job-boards.greenhouse.io.evil.test/example-co/jobs/123456',
        'https://foreign.test/job/123456',
    ):
        assert not forms.navigation_allowed(route, provenance=alias), route


@pytest.mark.parametrize('careers_url,matched', [
    (GH_CAREERS, True),
    ('https://careers.example-co.test', False),
    ('https://careers.example-co.test/', False),
])
async def test_real_zero_grant_alias_bootstrap_to_fresh_direct_label(
        monkeypatch, tmp_path, careers_url, matched):
    from agent import run

    browser, decide, row = _greenhouse_session(
        monkeypatch, tmp_path, careers_url=careers_url)
    response = await forms.apply(
        GH_WRAP, 'example-co', {}, 'synthetic', pk=GH_PK,
        resume_path='Resume.pdf')
    assert response['status'] == 'gate'
    assert response['form_question'] is None
    if not matched:
        # A bare/root-slash tuple cannot certify /openings?gh_jid.
        assert browser.calls[0] == ('navigate', GH_WRAP)
        assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                       for call in browser.calls)
        assert not decide.await_count
        return
    assert response['review_route'] == 'agent_unavailable'
    assert decide.await_count == 1
    assert not row['human_approved_answers']
    # The old direct-grant envelope can still be checked as an independent
    # reader exercise, but ordinary missing fields no longer create one in
    # the real preparation path.
    observed = browser.current
    target = next(c for c in observed['controls'] if c['selector'] == '#email')
    fixed = {c['selector']: c for c in witness(observed)['controls']}
    q = forms._disclosure_question(
        observed, target, fixed, GH_WRAP,
        provenance=_greenhouse_provenance(row))
    q['pk'] = GH_PK
    assert q['origin_row'] == {
        'jd_url': GH_WRAP, 'company': 'example-co', 'ats': 'greenhouse'}
    assert q['url'] == GH_DIRECT and q['form_action'] == GH_DIRECT
    assert (q['label'], q['selector'], q['form_selector']) == (
        'Email', '#email', '#application')
    # Two complete independently witnessed/re-read pairs, then bootstrap's own
    # fresh witness and re-read before disclosing the original question.
    assert browser.calls[0] == ('navigate', GH_DIRECT)
    assert browser.calls.count(('evaluate', forms.GREENHOUSE_PAGE)) >= 2
    assert browser.calls.count(
        ('evaluate', forms._GREENHOUSE_FIXED_CONTROL_WITNESS)) >= 2
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)
    with pytest.raises(forms.Gate, match='Required field is empty: Email'):
        forms.check_form(observed, {}, uploaded=True)

    # The same synthetic tracking-status seam used by the actual direct writer.
    class Tracking:
        def get(self, pk):
            assert pk == GH_PK
            return deepcopy(row)

        def set_status(self, pk, status, **changes):
            assert pk == GH_PK
            row.update(status=status, **changes)

    tracking = Tracking()
    tracking.set_status(
        GH_PK, 'needs_human', gate_source='applier', gate_call_id='direct',
        gate_pending={'question': response['question'], 'form_question': q})
    assert run._direct_form_label(GH_PK, tracking.get(GH_PK)) == 'Email'
    row['jd_url'] = (
        'https://careers.example-co.test/openings/other?gh_jid=123456')
    assert run._direct_form_label(GH_PK, tracking.get(GH_PK)) is None
    row['jd_url'] = GH_WRAP
    q.pop('origin_row')
    assert run._direct_form_label(GH_PK, tracking.get(GH_PK)) is None


async def test_no_explicit_alias_config_stops_before_navigation(monkeypatch, tmp_path):
    browser, decide, row = _greenhouse_session(monkeypatch, tmp_path)
    (tmp_path / 'watchlist.yaml').write_text('companies: []\n')
    result = await forms.apply(
        GH_WRAP, 'example-co', {}, 'synthetic', pk=GH_PK,
        resume_path='Resume.pdf')
    # r06 permits host-only preparation without an old alias/pin; it never
    # permits final Submit using that absent tenant identity.
    assert result['status'] == 'gate' and result['form_question'] is None
    assert browser.calls[0] == ('navigate', GH_WRAP)
    assert all(c[0] in {'navigate', 'evaluate'} for c in browser.calls)
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'} for c in browser.calls)
    controller = forms.ApplicationIPCController(
        GH_PK, 'example-co', GH_WRAP, '', {}, {}, [], {}, lambda:
        pytest.fail('An unconfigured alias cannot start Submit'))
    with pytest.raises(forms.PreSubmitGate) as denial:
        await controller.check_pre_submit(
            None, browser.current, browser.current['controls'][-1])
    assert denial.value.item == 1
    assert not decide.await_count or decide.await_count == 1

async def test_alias_revoked_after_navigation_gates_before_bootstrap(monkeypatch, tmp_path):
    browser, decide, _ = _greenhouse_session(monkeypatch, tmp_path)
    original = browser.navigate

    async def revoke(url):
        page = await original(url)
        (tmp_path / 'watchlist.yaml').write_text('companies: []\n')
        return page

    monkeypatch.setattr(browser, 'navigate', revoke)
    result = await forms.apply(
        GH_WRAP, 'example-co', {}, 'synthetic', pk=GH_PK,
        resume_path='Resume.pdf')
    assert result['status'] == 'gate' and result['form_question'] is None
    assert browser.calls[0] == ('navigate', GH_DIRECT)
    assert sum(c[0] == 'evaluate' for c in browser.calls) >= 1
    assert all(c[0] in {'navigate', 'evaluate'} for c in browser.calls)
    # Losing the board pin is not a new per-step denial: re-observation may
    # continue, but no approved value or Submit was written.
    assert decide.await_count == 1

@pytest.mark.parametrize('verb,kind', [
    ('fill', 'email'), ('select', 'select'), ('choose', 'checkbox'),
    ('upload', 'file'), ('click', 'submit'),
])
async def test_alias_unsafe_native_receiver_never_mutates(
        monkeypatch, tmp_path, verb, kind):
    if kind == 'submit':
        chosen = submit('Continue', selector='#chosen',
                        form_action='https://foreign.test/job/123456')
    else:
        selected = {
            'email': {}, 'select': {'tag': 'select', 'type': 'select',
                                    'options': [{'value': 'a', 'label': 'Alpha'}]},
            'checkbox': {'type': 'checkbox', 'value': 'yes', 'has_value': True},
            'file': {'type': 'file'},
        }[kind]
        chosen = field('Upload résumé' if kind == 'file' else 'Email', '#chosen',
                       form_action='https://foreign.test/job/123456',
                       **selected)
    p = inventory(chosen, submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    monkeypatch.setattr(forms.submit_hold, 'is_held', lambda pk: True)
    monkeypatch.setattr(forms, 'check_before_committing_click', lambda *a, **kw: None)
    alias = _greenhouse_provenance(row)
    if kind == 'submit':
        # An unbound fake is not a privileged application IPC producer. Route
        # the committing target through the one real code-owned final verdict.
        controller = forms.ApplicationIPCController(
            GH_PK, 'example-co', GH_WRAP, '', {}, {}, [], {}, lambda:
            pytest.fail('Unsafe final Submit reached the click-start marker'))
        with pytest.raises(forms.Gate, match='Final submit receiver is outside'):
            await controller.authorize_final_submit(None, p, chosen)
    else:
        with pytest.raises(forms.Gate):
            await forms.execute(
                browser, p, {'action': verb, 'selector': '#chosen', 'fact': 'Email'},
                facts={'Email': 'test@example.test'}, filled={},
                resume_path='Resume.pdf', company='example-co', jd_text='',
                resume_tex='', github='', pk=GH_PK, allow_click=True,
                job_url=GH_WRAP, provenance=alias)
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

@pytest.mark.parametrize('verb', ['upload', 'click'])
async def test_tracked_action_never_promotes_observed_page_to_origin(
        monkeypatch, tmp_path, verb):
    p = inventory(field('Upload résumé', '#file', type='file'),
                  submit('Continue', selector='#next', form_action=GH_DIRECT),
                  url=GH_DIRECT)
    browser, _, _ = _greenhouse_session(monkeypatch, tmp_path, p)
    monkeypatch.setattr(forms.submit_hold, 'is_held', lambda pk: True)
    with pytest.raises(forms.Gate):
        await forms.execute(
            browser, p, {'action': verb, 'selector': '#file' if verb == 'upload'
                        else '#next'}, facts={}, filled={},
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='', pk=GH_PK, allow_click=True, job_url='')
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

async def test_alias_execute_gate_producer_captures_original_row(monkeypatch, tmp_path):
    p = inventory(field(form_action=GH_DIRECT),
                  submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    alias = _greenhouse_provenance(row)
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            browser, p, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={}, filled={}, resume_path='Resume.pdf',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=GH_PK, job_url=GH_WRAP, provenance=alias)
    assert caught.value.form_question['origin_row'] == {
        'jd_url': GH_WRAP, 'company': 'example-co', 'ats': 'greenhouse'}
    assert caught.value.form_question['url'] == GH_DIRECT
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'}
                   for c in browser.calls)


async def test_alias_exact_approved_field_writes_only_verified_receiver(
        monkeypatch, tmp_path):
    p = inventory(field(form_action=GH_DIRECT),
                  submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    browser.allow_mutations = True
    row['human_approved_answers'] = {
        'Email': {'value': 'test@example.test', 'selector': '#email',
                  'url': GH_DIRECT, 'label': 'Email', 'question': '',
                  'tag': 'input', 'type': 'email', 'form_selector': '#application',
                  'form_action': GH_DIRECT}}
    receipt = await forms.execute(
        browser, p, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
        facts={'Email': 'test@example.test'}, filled={},
        resume_path='Resume.pdf', company='example-co', jd_text='',
        resume_tex='', github='', pk=GH_PK, job_url=GH_WRAP,
        provenance=_greenhouse_provenance(row))
    assert receipt['action'] == 'fill'
    assert [c for c in browser.calls if c[0] == 'fill'] == [
        ('fill', '#email', '--value', 'test@example.test')]


async def test_alias_exact_upload_uses_fresh_native_receiver(monkeypatch, tmp_path):
    p = inventory(field('Upload résumé', '#resume', tag='input', type='file',
                        form_action=GH_DIRECT),
                  submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    browser.allow_mutations = True
    receipt = await forms.execute(
        browser, p, {'action': 'upload', 'selector': '#resume'},
        facts={}, filled={}, resume_path='Resume.pdf',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=GH_PK, job_url=GH_WRAP, provenance=_greenhouse_provenance(row))
    assert receipt['uploaded'] is True
    assert len([c for c in browser.calls if c[0] == 'upload']) == 1


async def test_tracked_direct_execute_does_not_borrow_forged_alias_without_context(
        monkeypatch, tmp_path):
    p = inventory(field(form_action=GH_DIRECT),
                  submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    # Even a matching-looking page.url and job_url cannot mint authority.
    with pytest.raises(forms.Gate, match='exact human-approved answer'):
        await forms.execute(
            browser, p, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={'Email': 'test@example.test'}, filled={},
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='', pk=GH_PK, job_url=GH_DIRECT)
    assert browser.calls == []


async def test_alias_wrong_tracked_caller_identity_never_mutates(monkeypatch, tmp_path):
    p = inventory(field(form_action=GH_DIRECT),
                  submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    with pytest.raises(forms.Gate):
        await forms.execute(
            browser, p, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={'Email': 'test@example.test'}, filled={},
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='', pk=GH_PK, job_url=GH_DIRECT,
            provenance=_greenhouse_provenance(row))
    assert browser.calls == []


async def test_alias_config_revoked_during_witness_gates_without_question(
        monkeypatch, tmp_path):
    browser, decide, _ = _greenhouse_session(monkeypatch, tmp_path)
    original_call = browser.call

    async def revoke_on_observe(*args, **kwargs):
        result = await original_call(*args, **kwargs)
        if args == ('evaluate', forms._GREENHOUSE_FIXED_CONTROL_WITNESS):
            (tmp_path / 'watchlist.yaml').write_text('companies: []\n')
        return result

    monkeypatch.setattr(browser, 'call', revoke_on_observe)
    outcome = await forms.apply(
        GH_WRAP, 'example-co', {}, 'synthetic', pk=GH_PK,
        resume_path='Resume.pdf')
    assert outcome['status'] == 'gate'
    assert outcome['form_question'] is None
    # The revocation is final-tenant evidence, not preparation admission.
    assert decide.await_count == 1
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'}
                   for c in browser.calls)

async def test_alias_approved_select_uses_exact_native_option(monkeypatch, tmp_path):
    options = [{'value': 'remote', 'label': 'Remote'},
               {'value': 'hybrid', 'label': 'Hybrid'}]
    select = field('Work mode', '#work', tag='select', type='select',
                   options=options, form_action=GH_DIRECT)
    p = inventory(select, submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    browser.allow_mutations = True
    row['human_approved_answers'] = {
        'Work mode': {
            'value': 'Remote', 'selector': '#work', 'url': GH_DIRECT,
            'label': 'Work mode', 'question': '', 'tag': 'select',
            'type': 'select', 'form_selector': '#application',
            'form_action': GH_DIRECT, 'options': deepcopy(options)}}
    receipt = await forms.execute(
        browser, p, {'action': 'select', 'selector': '#work',
                     'fact': 'Work mode', 'value': 'remote'},
        facts={'Work mode': 'Remote'}, filled={}, resume_path='Resume.pdf',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=GH_PK, job_url=GH_WRAP, provenance=_greenhouse_provenance(row))
    assert receipt['action'] == 'select'
    assert [c for c in browser.calls if c[0] == 'select'] == [
        ('select', '#work', '--value', 'remote')]


async def test_alias_approved_choice_uses_exact_native_control(monkeypatch, tmp_path):
    choice = field('Email contact', '#choice', tag='input', type='checkbox',
                   question='Contact preference', required=True, value='yes',
                   has_value=True, checked=False, form_action=GH_DIRECT)
    p = inventory(choice, submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    browser.allow_mutations = True
    row['human_approved_answers'] = {
        'Contact preference': {
            'value': 'yes', 'selector': '#choice', 'url': GH_DIRECT,
            'label': 'Email contact', 'question': 'Contact preference',
            'tag': 'input', 'type': 'checkbox',
            'form_selector': '#application', 'form_action': GH_DIRECT,
            'visible_text': 'Email contact'}}
    receipt = await forms.execute(
        browser, p, {'action': 'choose', 'selector': '#choice',
                     'fact': 'Contact preference'},
        facts={'Contact preference': 'yes'}, filled={}, resume_path='Resume.pdf',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=GH_PK, job_url=GH_WRAP, provenance=_greenhouse_provenance(row))
    assert receipt['action'] == 'choose'
    assert [c for c in browser.calls if c[0] == 'click'] == [
        ('click', '#choice')]


async def test_alias_safe_native_continue_stays_under_existing_hold(
        monkeypatch, tmp_path):
    next_button = submit('Continue', selector='#next', form_action=GH_DIRECT)
    p = inventory(next_button, url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, p)
    prior = browser.call

    async def fake_continue(*args, **kwargs):
        if args[0] == 'click' and args[1] == '#next':
            browser.calls.append(args)
            return {}
        return await prior(*args, **kwargs)

    monkeypatch.setattr(browser, 'call', fake_continue)
    monkeypatch.setattr(forms.submit_hold, 'is_held', lambda pk: pk == GH_PK)
    monkeypatch.setattr(forms, 'check_before_committing_click', lambda *a, **kw: None)
    receipt = await forms.execute(
        browser, p, {'action': 'click', 'selector': '#next'},
        facts={}, filled={}, resume_path='Resume.pdf',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=GH_PK, job_url=GH_WRAP, allow_click=True,
        provenance=_greenhouse_provenance(row))
    assert receipt['action'] == 'click'
    assert [c for c in browser.calls if c[0] == 'click'] == [
        ('click', '#next')]

@pytest.mark.parametrize('verb', ['upload', 'click'])
async def test_alias_mapping_revoked_during_receiver_evaluate_never_mutates(
        monkeypatch, tmp_path, verb):
    # Native receiver re-observation remains reversible under a revoked
    # Greenhouse board pin. The FINAL same-job/tenant guard, not an extra
    # pre-upload certificate, rejects the irrevocable Submit.
    receiver = (field('Upload résumé', '#resume', type='file',
                      form_action=GH_DIRECT)
                if verb == 'upload' else
                submit('Continue', selector='#next', form_action=GH_DIRECT))
    page = inventory(receiver, submit(form_action=GH_DIRECT), url=GH_DIRECT)
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, page)
    alias = _greenhouse_provenance(row)
    original = browser.call

    async def revoke_during_evaluate(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[0] == 'evaluate':
            (tmp_path / 'watchlist.yaml').write_text('companies: []\n')
        return result

    monkeypatch.setattr(browser, 'call', revoke_during_evaluate)
    action = {'selector': '#resume' if verb == 'upload' else '#next'}
    current_page, actual = await forms._fresh_native_receiver(
        browser, page, action, receiver, alias)
    assert current_page is not None and actual['selector'] == action['selector']
    assert [call[0] for call in browser.calls] == ['evaluate']
    assert forms._preparation_current(alias)
    assert not forms._alias_current(alias)
    controller = forms.ApplicationIPCController(
        GH_PK, 'example-co', GH_WRAP, '', {}, {}, [], {}, lambda:
        pytest.fail('Revoked tenant cannot issue Submit'))
    with pytest.raises(forms.Gate, match='tenant or board'):
        await controller.authorize_final_submit(
            None, page, page['controls'][-1])
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

async def test_alias_revoked_during_session_acquisition_prevents_navigation(
        monkeypatch, tmp_path):
    # The formerly early board-pin check cannot stop approved host-only
    # preparation after the await. Session acquisition must not manufacture
    # a valid final tenant/board identity.
    browser, decide, _ = _greenhouse_session(monkeypatch, tmp_path)

    class RevokingSession:
        async def __aenter__(self):
            (tmp_path / 'watchlist.yaml').write_text('companies: []\n')
            return browser

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bsk, 'Session', lambda kind: RevokingSession())
    outcome = await forms.apply(
        GH_WRAP, 'example-co', {}, 'synthetic', pk=GH_PK,
        resume_path='Resume.pdf')
    assert outcome['status'] == 'gate' and outcome['form_question'] is None
    assert browser.calls[0] == ('navigate', GH_DIRECT)
    assert all(call[0] in {'navigate', 'evaluate'} for call in browser.calls)
    assert decide.await_count == 1

@pytest.mark.parametrize('tag', ['input', 'div'])
async def test_bootstrap_offers_only_witnessed_linked_combobox_for_direct_gate(
        monkeypatch, tag):
    combo = field(
        'Country', '#country', tag=tag, type='combobox', required=True,
        combobox={'linked': True, 'link_id': 'country-list',
                  'listbox_selector': '#country-list',
                  'options': [{'selector': '#country-example', 'unique': True,
                               'label': 'Example Country', 'value': 'EX',
                               'selected': False}],
                  'selected_text': '', 'displayed_text': '',
                  'selected_value': '', 'hidden_selector': '', 'hidden_value': ''})
    current = inventory(combo, submit())
    monkeypatch.setattr(forms, '_row', lambda _pk: {})
    evidence = witness(current)
    evidence['controls'][0]['combobox'] = deepcopy(combo['combobox'])
    browser = Browser(current, result=evidence)
    prepared = await forms._bootstrap(browser, current, PK, {}, URL)
    assert prepared is current
    fixed = {c['selector']: c for c in evidence['controls']}
    question = forms._disclosure_question(current, combo, fixed, URL)
    question['pk'] = PK
    assert question == {
        'pk': PK, 'label': 'Country', 'control_label': 'Country',
        'question': '', 'selector': '#country', 'url': URL, 'tag': tag,
        'type': 'combobox', 'form_selector': '#application', 'form_action': URL}
    assert [call[0] for call in browser.calls] == ['evaluate']


    with pytest.raises(forms.Gate, match='Unverified or changed combobox value'):
        forms.check_form(prepared, {}, uploaded=True)
@pytest.mark.parametrize('ats,canonical,target,status', [
    ('teamtailor', 'https://example-co.teamtailor.com/jobs/backend-123',
     'https://careers.example-co.test/jobs/backend-123', 301),
    ('recruitee', 'https://example-co.recruitee.com/o/backend-123',
     'https://careers.example-co.test/o/backend-123', 302),
])
async def test_vendor_redirect_probe_precedes_fake_browser_and_persists_exact_witness(
        monkeypatch, ats, canonical, target, status):
    import httpx
    from core.ids import make_pk
    from agent import run

    pk = make_pk('example-co', 'backend-123')
    page = inventory(field(form_action=target), submit(form_action=target), url=target)
    browser, _, row = world(monkeypatch, page)
    row.update(pk=pk, company='example-co', job_id='backend-123', ats=ats,
               jd_url=canonical, status='submitting')
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    calls = []

    def response(request):
        calls.append(request)
        assert str(request.url) == canonical
        return httpx.Response(status, headers={'Location': target}, request=request)

    async_client = httpx.AsyncClient
    def client_factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        return async_client(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(forms.httpx, 'AsyncClient', client_factory)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')
    monkeypatch.setattr(forms, 'check_dispatch', lambda *args, **kwargs: None)
    result = await forms.apply(canonical, 'example-co', {}, 'synthetic',
                               pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate', result
    assert result['form_question'] is None
    # The synthetic link is still proven by the real redirect probe, but no
    # ordinary missing Email prompt is emitted before final Submit.
    provenance = forms.VendorRedirectProvenance(
        pk, 'example-co', ats, 'backend-123', canonical, target)
    item = page['controls'][0]
    question = forms._disclosure_question(
        page, item, {c['selector']: c for c in witness(page)['controls']},
        canonical, provenance=provenance)
    question['pk'] = pk
    assert question['verified_redirect_url'] == target
    assert question['origin_row'] == {
        'jd_url': canonical, 'company': 'example-co', 'ats': ats,
        'pk': pk, 'job_id': 'backend-123'}
    assert len(calls) == 1
    assert browser.calls[0] == ('navigate', target)
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

    row.update(status='needs_human', gate_source='applier', gate_call_id='direct',
               gate_pending={'form_question': question})
    assert run._direct_form_label(pk, row) == question['label']
    tampered = deepcopy(question)
    tampered['verified_redirect_url'] = 'https://careers.other.test/jobs/backend-123'
    row['gate_pending'] = {'form_question': tampered}
    assert run._direct_form_label(pk, row) is None
    tampered = deepcopy(question)
    tampered['form_action'] = 'https://careers.other.test/jobs/backend-123'
    row['gate_pending'] = {'form_question': tampered}
    assert run._direct_form_label(pk, row) is None
    row['gate_pending'] = {'form_question': question}

    class Tracking:
        def get(self, _pk):
            return deepcopy(row)

        def set_status(self, _pk, status, **changes):
            row.update(status=status, **changes)

    stores = SimpleNamespace(
        tracking=Tracking(),
        answer_bank=SimpleNamespace(put=lambda *args, **kwargs: None))
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(run, '_enqueue_apply', lambda *args, **kwargs: {'result': 'queued'})
    assert run.resume_job(pk, 'approved synthetic answer', stores,
                          **_owner_reply(stores.tracking, row))['result'] == 'queued'
    assert row['human_approved_answers'][question['label']][
        'verified_redirect_url'] == target



@pytest.mark.parametrize('location', [
    'https://careers.example-co.test/jobs/backend-123#fragment',
    'https://careers.example-co.test/jobs/backend-123#',
    'https://careers.example-co.test/jobs/backend-123\t',
    'https://careers.example-co.test/jobs/backend-123\n',
    'https://careers.example-co.test/jobs/backend-123?',
    'https://careers.example-co.test: /jobs/backend-123',
    'https://careers.example-co.test:/jobs/backend-123',
    'https://careers.example-co.test:443/jobs/backend-123',
    'https://careers.example-co.test./jobs/backend-123',
    'https://LOCALHOST/jobs/backend-123',
    'https://localhost/jobs/backend-123',
    'https://careers.local/jobs/backend-123',
    'https://careers.localhost/jobs/backend-123',
    'https://0177.0.0.1/jobs/backend-123',
    'https://127.1/jobs/backend-123',
    'https://2130706433/jobs/backend-123',
    'https://127.0.0.1/jobs/backend-123',
    'https://user@careers.example-co.test/jobs/backend-123',
    'http://careers.example-co.test/jobs/backend-123',
    'https://careers.example-co.test/jobs/backend-123?extra=1',
    'https://careers.example-co.test/jobs/other',
    r'https://careers.example-co.test/jobs\backend-123',
    'https://careers%2Eexample-co.test/jobs/backend-123',
    'https://Careers.example-co.test/jobs/backend-123',
    'https://other-co.teamtailor.com/jobs/backend-123',
    'https://other-co.recruitee.com/o/backend-123',
])
def test_vendor_location_is_raw_exact_dns_and_job_scoped(location):
    assert not forms._literal_redirect_target(
        'https://example-co.teamtailor.com/jobs/backend-123', location)


def test_redirect_navigation_rejects_return_to_vendor_and_other_custom_host():
    row = {'pk': 'example-co#backend-123', 'company': 'example-co',
           'job_id': 'backend-123', 'ats': 'teamtailor',
           'jd_url': 'https://example-co.teamtailor.com/jobs/backend-123'}
    proven = forms.redirect_provenance(
        row, row['pk'], 'https://careers.example-co.test/jobs/backend-123')
    assert proven is not None
    assert forms.navigation_allowed(proven.target_url, provenance=proven)
    assert not forms.navigation_allowed(row['jd_url'], provenance=proven)
    assert not forms.navigation_allowed(
        'https://careers.other.test/jobs/backend-123', provenance=proven)
    assert forms.navigation_allowed(
        'https://careers.example-co.test/jobs/backend-123/step', provenance=proven)
    assert not forms.navigation_allowed(
        proven.target_url + r'/..\other', provenance=proven)
    assert not forms.navigation_allowed(
        'https://careers.example-co.test/jobs/backend-123' + chr(9),
        provenance=proven)

@pytest.mark.parametrize('observed_url', [
    'https://example-co.teamtailor.com/jobs/backend-123',
    'https://careers.other.test/jobs/backend-123',
    'https://careers.example-co.test/jobs/backend-123/step',
    r'https://careers.example-co.test/jobs/backend-123/..\other',
])
async def test_vendor_initial_page_must_equal_exact_redirect_target(
        monkeypatch, observed_url):
    import httpx
    from core.ids import make_pk

    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = make_pk('example-co', 'backend-123')
    page = inventory(field(form_action=target), submit(form_action=target),
                     url=observed_url)
    browser, _, row = world(monkeypatch, page)
    row.update(pk=pk, company='example-co', job_id='backend-123',
               ats='teamtailor', jd_url=canonical)
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    async_client = httpx.AsyncClient
    def client_factory(**kwargs):
        return async_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                301, headers={'Location': target}, request=request)), **kwargs)

    monkeypatch.setattr(forms.httpx, 'AsyncClient', client_factory)
    result = await forms.apply(canonical, 'example-co', {}, 'synthetic',
                               pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'Initial page did not match the exact vendor-verified target' in result['question']
    assert browser.calls == [('navigate', target)]
    if observed_url.endswith('/step'):
        proven = forms.redirect_provenance(row, pk, target)
        assert forms.navigation_allowed(observed_url, provenance=proven)
@pytest.mark.parametrize('status,locations', [
    (200, [('Location', 'https://careers.example-co.test/jobs/backend-123')]),
    (307, [('Location', 'https://careers.example-co.test/jobs/backend-123')]),
    (301, []),
    (302, [('Location', 'https://careers.example-co.test/jobs/backend-123'),
           ('Location', 'https://careers.example-co.test/jobs/backend-123')]),
    (301, [('Location', 'https://careers.example-co.test/jobs/other')]),
    (301, [('Location', ' https://careers.example-co.test/jobs/backend-123')]),
    (None, []),
])
async def test_vendor_redirect_rejections_gate_before_browser(
        monkeypatch, status, locations):
    import httpx
    from core.ids import make_pk

    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    pk = make_pk('example-co', 'backend-123')
    browser, _, row = world(monkeypatch, inventory(field(), submit()))
    row.update(pk=pk, company='example-co', job_id='backend-123',
               ats='teamtailor', jd_url=canonical)
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    requests = []

    def response(request):
        requests.append(request)
        if status is None:
            raise httpx.ConnectError('synthetic network failure', request=request)
        return httpx.Response(status, headers=locations, request=request)
    async_client = httpx.AsyncClient
    def client_factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        return async_client(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(forms.httpx, 'AsyncClient', client_factory)
    result = await forms.apply(canonical, 'example-co', {}, 'synthetic',
                               pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert len(requests) == 1 and str(requests[0].url) == canonical
    assert browser.calls == []


@pytest.mark.parametrize('drift_field,drift_value', [
    ('pk', 'other#job'), ('company', 'other-company'),
    ('ats', 'recruitee'), ('job_id', 'other-job'),
    ('jd_url', 'https://example-co.teamtailor.com/jobs/other'),
])
async def test_vendor_redirect_row_drift_after_http_response_stops_before_browser(
        monkeypatch, drift_field, drift_value):
    import httpx
    from core.ids import make_pk

    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = make_pk('example-co', 'backend-123')
    browser, _, row = world(monkeypatch, inventory(field(), submit()))
    row.update(pk=pk, company='example-co', job_id='backend-123',
               ats='teamtailor', jd_url=canonical)
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)

    class RevokingSession:
        async def __aenter__(self):
            row[drift_field] = drift_value
            return browser

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bsk, 'Session', lambda kind: RevokingSession())
    async_client = httpx.AsyncClient
    def client_factory(**kwargs):
        return async_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                301, headers={'Location': target}, request=request)), **kwargs)

    monkeypatch.setattr(forms.httpx, 'AsyncClient', client_factory)
    result = await forms.apply(canonical, 'example-co', {}, 'synthetic',
                               pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert browser.calls == []


@pytest.mark.parametrize('drift_field,drift_value', [
    ('pk', 'other#job'), ('company', 'other-company'),
    ('ats', 'recruitee'), ('job_id', 'other-job'),
    ('jd_url', 'https://example-co.teamtailor.com/jobs/other'),
])
async def test_vendor_row_drift_during_dom_witness_prevents_field_gate(
        monkeypatch, drift_field, drift_value):
    import httpx
    from core.ids import make_pk

    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = make_pk('example-co', 'backend-123')
    browser, _, row = world(
        monkeypatch, inventory(field(form_action=target), submit(form_action=target),
                             url=target))
    row.update(pk=pk, company='example-co', job_id='backend-123',
               ats='teamtailor', jd_url=canonical)
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    original = browser.call

    async def revoke_after_witness(*args, **kwargs):
        result = await original(*args, **kwargs)
        row[drift_field] = drift_value
        return result

    monkeypatch.setattr(browser, 'call', revoke_after_witness)
    async_client = httpx.AsyncClient
    def client_factory(**kwargs):
        return async_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                301, headers={'Location': target}, request=request)), **kwargs)

    monkeypatch.setattr(forms.httpx, 'AsyncClient', client_factory)
    result = await forms.apply(canonical, 'example-co', {}, 'synthetic',
                               pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert result['form_question'] is None
    assert 'job/tenant mapping changed during native control inspection' in result['question']
    assert [call[0] for call in browser.calls] == ['navigate', 'evaluate']

def _vendor_row(pk, canonical, ats='teamtailor'):

    return {'pk': pk, 'company': 'example-co', 'job_id': 'backend-123',
            'ats': ats, 'jd_url': canonical, 'status': 'submitting',
            'possible_submission': False, 'human_approved_answers': {}}


def _install_vendor_redirect(monkeypatch, target, *, status=301, after_response=None):
    import httpx

    requests = []
    async_client = httpx.AsyncClient
    def respond(request):
        requests.append(request)
        response = httpx.Response(
            status, headers={'Location': target}, request=request)
        if after_response:
            after_response()
        return response
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        return async_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(forms.httpx, 'AsyncClient', factory)
    return requests


def _redirect_question(row, pk, target):
    return {
        'pk': pk, 'label': 'Email', 'control_label': 'Email', 'question': '',
        'selector': '#email', 'url': target, 'tag': 'input', 'type': 'email',
        'form_selector': '#application', 'form_action': target, 'formaction': '',
        'origin_row': {'jd_url': row['jd_url'], 'company': row['company'],
                       'ats': row['ats'], 'pk': pk, 'job_id': row['job_id']},
        'verified_redirect_url': target,
    }


@pytest.mark.parametrize('raw_url', [
    chr(9) + 'https://example-co.teamtailor.com/jobs/backend-123',
    'https://example-co.teamtailor.com/jobs/backend-123' + chr(10),
    'https://example-co.team' + chr(10) + 'tailor.com/jobs/backend-123',
])
async def test_noncanonical_vendor_url_refuses_before_http_client(monkeypatch, raw_url):
    from core.ids import make_pk

    pk = make_pk('example-co', 'backend-123')
    row = _vendor_row(pk, raw_url)
    original_urlsplit = forms.urlsplit
    def refuse_parser_normalization(candidate, *args, **kwargs):
        if candidate == raw_url:
            pytest.fail('Noncanonical tracked URL reached URL parsing')
        return original_urlsplit(candidate, *args, **kwargs)
    monkeypatch.setattr(forms, 'urlsplit', refuse_parser_normalization)
    monkeypatch.setattr(forms.httpx, 'AsyncClient', lambda **kwargs: pytest.fail(
        'Noncanonical tracked URL reached HTTP client construction'))
    assert await forms._probe_vendor_redirect(row, pk, 'example-co', raw_url) is None


@pytest.mark.parametrize('mutation', [
    'missing_target', 'company', 'job_id', 'jd_url', 'ats', 'formaction',
    'leading_tab', 'trailing_newline', 'embedded_newline',
])
def test_direct_redirect_label_revalidates_literal_target_and_current_row(mutation):
    from agent import run
    from core.ids import make_pk

    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = make_pk('example-co', 'backend-123')
    row = _vendor_row(pk, canonical, 'teamtailor')
    row.update(status='needs_human', gate_source='applier', gate_call_id='direct')
    question = _redirect_question(row, pk, target)
    if mutation == 'missing_target':
        question.pop('verified_redirect_url')
    elif mutation == 'company':
        row['company'] = 'other-company'
    elif mutation == 'job_id':
        row['job_id'] = 'other-job'
    elif mutation == 'jd_url':
        row['jd_url'] = 'https://example-co.teamtailor.com/jobs/other'
    elif mutation == 'ats':
        row['ats'] = 'recruitee'
    elif mutation == 'formaction':
        question['formaction'] = 'https://foreign.test/submit'
    elif mutation == 'leading_tab':
        row['jd_url'] = chr(9) + canonical
        question['origin_row']['jd_url'] = row['jd_url']
    elif mutation == 'trailing_newline':
        row['jd_url'] = canonical + chr(10)
        question['origin_row']['jd_url'] = row['jd_url']
    else:
        row['jd_url'] = canonical.replace('teamtailor', chr(10) + 'teamtailor')
        question['origin_row']['jd_url'] = row['jd_url']
    row['gate_pending'] = {'form_question': question}
    assert run._direct_form_label(pk, row) is None


async def test_recruitee_cross_tenant_redirect_gates_at_apply_before_browser(monkeypatch):
    from core.ids import make_pk

    canonical = 'https://example-co.recruitee.com/o/backend-123'
    target = 'https://other-co.recruitee.com/o/backend-123'
    pk = make_pk('example-co', 'backend-123')
    page = inventory(field(form_action=target), submit(form_action=target), url=target)
    browser, _, row = world(monkeypatch, page)
    row.update(_vendor_row(pk, canonical, 'recruitee'))
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    requests = _install_vendor_redirect(monkeypatch, target, status=302)
    result = await forms.apply(
        canonical, 'example-co', {}, 'synthetic', pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'did not prove one safe same-job redirect' in result['question']
    assert len(requests) == 1
    assert browser.calls == []


async def test_vendor_row_drift_during_resume_staging_gates_before_chrome(monkeypatch):
    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = 'example-co#backend-123'
    browser, _, row = world(
        monkeypatch, inventory(field(form_action=target), submit(form_action=target),
                             url=target))
    row.update(_vendor_row(pk, canonical))
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    requests = _install_vendor_redirect(
        monkeypatch, target, after_response=lambda: None)
    sessions = []
    def stage(path, owner):
        row['jd_url'] = 'https://example-co.teamtailor.com/jobs/other'
        return path
    monkeypatch.setattr('tools.claude_chrome._stage_resume', stage)
    monkeypatch.setattr('core.events.emit', lambda *args, **kwargs: None)
    def session(kind):
        sessions.append(kind)
        return browser
    monkeypatch.setattr(bsk, 'Session', session)
    result = await forms.apply(
        canonical, 'example-co', {}, 'synthetic', pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'changed before BrowserSkill startup' in result['question']
    assert len(requests) == 1
    assert sessions == []
    assert browser.calls == []


async def test_vendor_combobox_final_observation_rechecks_sibling_formaction(monkeypatch):
    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = 'example-co#backend-123'
    combo = field('Country', '#country', required=False, type='combobox',
                  form_action=target, combobox={
                      'linked': True, 'link_id': 'country-list',
                      'listbox_selector': '#country-list',
                      'options': [{'selector': '#country-example', 'unique': True,
                                   'label': 'Example Country', 'value': 'EX'}],
                      'selected_text': '', 'displayed_text': '',
                      'selected_value': '', 'hidden_selector': '', 'hidden_value': ''})
    page = inventory(combo, submit(form_action=target), url=target)
    browser, _, row = world(monkeypatch, page)
    row.update(_vendor_row(pk, canonical))
    row['human_approved_answers'] = {'Country': {
        'value': 'Example Country', 'selector': '#country', 'url': target,
        'label': 'Country', 'question': '', 'tag': 'input', 'type': 'combobox',
        'form_selector': '#application', 'form_action': target,
        'verified_redirect_url': target}}
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    _install_vendor_redirect(monkeypatch, target)
    decision_returned = False
    page_refreshes = 0
    original_page = browser.page
    async def refresh_page():
        nonlocal page_refreshes
        if decision_returned:
            page_refreshes += 1
            if page_refreshes == 2:
                browser.current['controls'][1]['formaction'] = 'https://foreign.test/submit'
        return await original_page()
    monkeypatch.setattr(browser, 'page', refresh_page)
    original_call = browser.call
    async def live_witness(*args, **kwargs):
        if args[0] == 'evaluate':
            evidence = witness(browser.current)
            for source, observed in zip(browser.current['controls'], evidence['controls']):
                if source.get('type') == 'combobox':
                    observed['combobox'] = deepcopy(source['combobox'])
            browser.result = evidence
        if args[0] == 'click' and args[1] == '#country-example':
            browser.calls.append(args)
            return {}
        return await original_call(*args, **kwargs)
    monkeypatch.setattr(browser, 'call', live_witness)
    async def decide(*args, **kwargs):
        nonlocal decision_returned
        decision_returned = True
        return {'action': 'combobox', 'selector': '#country', 'fact': 'Country'}
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await forms.apply(
        canonical, 'example-co', {'Country': 'Example Country'}, 'synthetic',
        pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'form destination is external' in result['question']
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)


async def test_vendor_combobox_row_drift_after_initial_observation_has_no_question(monkeypatch):
    canonical = 'https://example-co.teamtailor.com/jobs/backend-123'
    target = 'https://careers.example-co.test/jobs/backend-123'
    pk = 'example-co#backend-123'
    combo = field('Country', '#country', required=False, type='combobox',
                  form_action=target, combobox={
                      'linked': True, 'link_id': 'country-list',
                      'listbox_selector': '#country-list',
                      'options': [{'selector': '#country-example', 'unique': True,
                                   'label': 'Example Country', 'value': 'EX'}],
                      'selected_text': '', 'displayed_text': '',
                      'selected_value': '', 'hidden_selector': '', 'hidden_value': ''})
    page = inventory(combo, submit(form_action=target), url=target)
    browser, _, row = world(monkeypatch, page)
    row.update(_vendor_row(pk, canonical))
    row['human_approved_answers'] = {'Country': {
        'value': 'Example Country', 'selector': '#country', 'url': target,
        'label': 'Country', 'question': '', 'tag': 'input', 'type': 'combobox',
        'form_selector': '#application', 'form_action': target,
        'verified_redirect_url': target}}
    evidence = witness(page)
    evidence['controls'][0]['combobox'] = deepcopy(combo['combobox'])
    browser.result = evidence
    monkeypatch.setattr(forms, '_row', lambda _pk, **kwargs: row)
    _install_vendor_redirect(monkeypatch, target)
    evaluations = 0
    original_call = browser.call
    async def drift_after_execute_observation(*args, **kwargs):
        nonlocal evaluations
        result = await original_call(*args, **kwargs)
        if args[0] == 'evaluate':
            evaluations += 1
            if evaluations == 1:
                row['jd_url'] = 'https://example-co.teamtailor.com/jobs/other'
        return result
    monkeypatch.setattr(browser, 'call', drift_after_execute_observation)
    async def decide(*args, **kwargs):
        return {'action': 'combobox', 'selector': '#country', 'fact': 'Country'}
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await forms.apply(
        canonical, 'example-co', {'Country': 'Example Country'}, 'synthetic',
        pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert result['form_question'] is None
    assert 'tracked posting changed before combobox preparation' in result['question']
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)


def _marked_name_grant(*, selector="#first", value="Test User", url=GH_DIRECT):
    return {
        "value": value, "selector": selector, "url": url,
        "label": "First Name*", "question": "", "tag": "input", "type": "text",
        "form_selector": "#application", "form_action": GH_DIRECT,
    }


def _marked_greenhouse_page():
    return inventory(
        field("First Name*", "#first", form_action=GH_DIRECT),
        field("Email", "#email", form_action=GH_DIRECT),
        submit(form_action=GH_DIRECT), url=GH_DIRECT,
    )


@pytest.mark.parametrize("facts,observed,expected", [
    ({"First name": "Ada", "First Name": "Ada"}, "First Name*", "First Name"),
    ({"Last name": "Lovelace", "Last Name": "Lovelace"}, "Last Name*", "Last Name"),
    ({"First name": " Ada ", "First Name": "Ada"}, "First Name*", "First Name"),
    ({"First name": "Ada", "First Name": "Eve"}, "First Name*", None),
    ({"First Name*": "Eve", "First Name": "Ada"}, "First Name*", "First Name*"),
    ({"Unrelated": "Ada"}, "First Name*", None),
])
def test_marked_native_fact_key_deduplicates_only_identical_value_aliases(
        facts, observed, expected):
    assert forms._fact_key(facts, observed) == expected


async def test_production_wrapper_reuses_marked_first_and_last_names_with_aliases(
        monkeypatch, tmp_path):
    """Production wrapper derives name twins; neither creates a new approval."""
    from core.models import AnswerScope
    from core.storage.local import MarkdownAnswerBank
    from tools import browser_apply, browser_runtime, submit_hold

    path = tmp_path / "wrapper-names.md"
    bank = MarkdownAnswerBank(path)
    bank.put("Full name", "Ada Lovelace", AnswerScope.GLOBAL)
    bank.put("First Name*", "Ada", AnswerScope.GLOBAL)
    bank.put("Last Name*", "Lovelace", AnswerScope.GLOBAL)
    facts = MarkdownAnswerBank(path).all_facts("example-co")
    assert facts == {
        "Full name": "Ada Lovelace",
        "First Name": "Ada",
        "Last Name": "Lovelace",
    }
    page = inventory(
        field("First Name*", "#first", form_action=GH_DIRECT),
        field("Last Name*", "#last", form_action=GH_DIRECT),
        submit(form_action=GH_DIRECT), url=GH_DIRECT,
    )
    browser, _, row = _greenhouse_session(monkeypatch, tmp_path, page)
    row["human_approved_answers"] = {
        "First Name*": _marked_name_grant(value="Ada"),
        "Last Name*": {
            **_marked_name_grant(selector="#last", value="Lovelace"),
            "label": "Last Name*",
        },
        # Previously granted spellings must not duplicate model-visible data
        # or substitute for the raw First Name*/Last Name* control grants.
        "First name": {**_marked_name_grant(value="Ada"), "label": "First name"},
        "Last name": {
            **_marked_name_grant(selector="#last", value="Lovelace"),
            "label": "Last name",
        },
    }
    original_grants = deepcopy(row["human_approved_answers"])

    # Only the outside runtime/store are fake: actual browser_apply.apply ->
    # browser_runtime.apply -> browser_skill_apply.apply and its exact guards run.
    # Seed a current *bound* JD for the production wrapper, not a legacy
    # metadata-free row that the posting boundary must reject.
    from tools import posting_guard as pg
    guarded = pg.analyze("Synthetic responsibilities for this test role. " * 16)
    row.update(jd_text=guarded["text"], posting_guard=guarded["posting_guard"])
    monkeypatch.setattr(
        "core.stores.make_stores",
        lambda: SimpleNamespace(tracking=SimpleNamespace(
            get=lambda _pk: row, get_current=lambda _pk: row)),
    )
    monkeypatch.setattr(submit_hold, "blocked", lambda *a, **kw: False)
    monkeypatch.setattr(browser_runtime, "available", lambda: (True, ""))
    dispatched = []
    async def offline_engine(url, company, enriched, model, **kwargs):
        dispatched.append(dict(enriched))
        assert enriched["First name"] == enriched["First Name"] == "Ada"
        assert enriched["Last name"] == enriched["Last Name"] == "Lovelace"
        return await forms.apply(url, company, enriched, model, **kwargs)

    monkeypatch.setattr(
        browser_runtime, "backend",
        lambda: SimpleNamespace(apply_chrome=offline_engine),
    )
    checked = []
    exact = forms.exact_approval

    def capture_exact(pk, key, value, **kwargs):
        checked.append((key, value, kwargs["target"]["selector"]))
        return exact(pk, key, value, **kwargs)

    monkeypatch.setattr(forms, "exact_approval", capture_exact)
    prompts = []

    async def stop_before_write(prompt, observed, history, *, model):
        prompts.append(prompt)
        return {"action": "gate", "question": "Synthetic stop before any write"}

    monkeypatch.setattr(bsk, "decision", stop_before_write)
    result = await browser_apply.apply(
        GH_WRAP, "example-co", facts, "synthetic", pk=GH_PK,
        jd_text=guarded["text"], resume_path="Resume.pdf",
    )
    assert result["status"] == "gate", result
    assert "Synthetic stop before any write" in result["question"]
    assert len(dispatched) == 1
    assert checked == [
        ("First Name*", "Ada", "#first"),
        ("Last Name*", "Lovelace", "#last"),
    ]
    assert len(prompts) == 1
    assert ("Exact human-authorized fact KEYS on this tracked job: "
            "['First Name', 'Last Name'].") in prompts[0]
    assert row["human_approved_answers"] == original_grants
    assert not [call for call in browser.calls
                if call[0] in {"fill", "select", "click", "upload"}]


# Issue #103: the choice answer is an existing control grant, never bank fact data.
def _choice_grant_from_page(page, target, answer):
    nodes = {c['selector']: c for c in witness(page)['controls']}
    question = forms._disclosure_question(
        page, target, nodes, page['url'], bootstrap=True)
    grant = {
        'value': answer, 'selector': question['selector'],
        'url': question['url'], 'label': question['control_label'],
        'question': question['question'], 'tag': question['tag'],
        'type': question['type'], 'form_selector': question['form_selector'],
        'form_action': question['form_action'],
    }
    if target['type'] == 'radio':
        grant.update(radio_name=question['radio_name'],
                     radio_members=deepcopy(question['radio_members']))
    elif target['type'] == 'checkbox':
        grant['visible_text'] = question['visible_text']
    return question['label'], grant


async def test_missing_native_choice_grant_keeps_bound_first_question(monkeypatch):
    box = field('Confirm details', '#confirm', type='checkbox',
                question='Details confirmation', value='on', has_value=True)
    page = secure_inventory(box)
    browser, decide, row = secure_world(monkeypatch, page)
    report = await secure_attempt(page)
    assert report['status'] == 'gate' and report['form_question'] is None
    assert decide.await_count == 1
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            browser, page, {'action': 'choose', 'selector': '#confirm',
                            'fact': 'Details confirmation'},
            facts={}, filled={}, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=SECURE_URL)
    question = caught.value.form_question
    assert question is not None
    assert (question['label'], question['selector']) == (
        'Details confirmation', '#confirm')
    assert row['human_approved_answers'] == {}
    assert not any(call[0] == 'click' for call in browser.calls)

async def test_optional_disabled_native_does_not_satisfy_required_choice(
        monkeypatch, tmp_path):
    optional = field('Optional detail', '#optional', required=False, disabled=True)
    pending = field('Contact preference', '#choice', type='checkbox',
                    question='Preferred contact', value='on', has_value=True)
    page = secure_inventory(optional, pending)
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    assert page['controls'][0]['disabled'] is True
    assert page['controls'][1]['required'] is True
    assert page['controls'][1].get('checked') is not True
    await final_submit_denied(
        monkeypatch, tmp_path, page, row, filled, history,
        reason='required native choice')
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'}
                   for c in browser.calls)


async def test_required_radio_group_with_optional_leader_is_checked_at_submit(
        monkeypatch, tmp_path):
    first = field('Morning', '#morning', type='radio', name='schedule',
                  question='Schedule preference', value='am',
                  required=False, has_value=True)
    second = field('Evening', '#evening', type='radio', name='schedule',
                   question='Schedule preference', value='pm',
                   required=True, has_value=True)
    page = secure_inventory(first, second)
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    group = [c['selector'] for c in page['controls']
             if c.get('type') == 'radio' and c.get('name') == 'schedule']
    assert group == ['#morning', '#evening']
    assert any(c['required'] for c in page['controls']
               if c.get('type') == 'radio')
    assert not any(c['checked'] for c in page['controls']
                   if c.get('type') == 'radio')
    await final_submit_denied(
        monkeypatch, tmp_path, page, row, filled, history,
        reason='required native choice')
    assert not any(c[0] in {'fill', 'select', 'click', 'upload'}
                   for c in browser.calls)

async def test_unassociated_radio_group_defers_authority_to_final_submit(monkeypatch):
    """An unlabelled native group can be observed, but never granted or submitted.

    The old per-step card-shape gate barred unrelated safe preparation. r06
    instead retains the unanswered radios for the final code-owned guard.
    """
    page = secure_inventory(
        field('First option', '#answer-a', tag='input', type='radio',
              name='screening-answer', value='first', required=True),
        field('Second option', '#answer-b', tag='input', type='radio',
              name='screening-answer', value='second', required=True),
    )
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    assert browser.pages > 0
    assert not any(c[0] in {'click', 'fill', 'select', 'upload'}
                   for c in browser.calls)
    assert row['human_approved_answers'] == {}
    assert filled == {} and history == []
    assert forms._ordinary_owner_question(
        page, page['controls'][0], PK)['selector'] == '#answer-a'
    filename = 'Synthetic Resume.pdf'
    page['controls'].append(field(
        'Resume', '#resume', type='file', required=False, files=[filename],
        form_action=SECURE_URL))
    with pytest.raises(forms.Gate, match='required native choice has no current selection'):
        forms.check_form(page, filled, True, attachment=filename)


async def test_new_legal_or_broader_privacy_choice_is_normal_owner_answer():
    """Legal/consent questions use ordinary fact requests, not write gates."""
    question_text = 'I consent to sharing personal data with marketing partners'
    page = secure_inventory(field(
        question_text, '#marketing', tag='input', type='checkbox',
        value='yes', required=True))
    browser = Browser(page)
    action = {'action': 'choose', 'selector': '#marketing',
              'fact': question_text}
    with pytest.raises(forms.Gate) as missing:
        await forms.execute(
            browser, page, action, facts={}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=SECURE_URL)
    assert missing.value.reason == 'owner_question'
    question = missing.value.form_question
    assert question['control_label'] == question_text
    assert question['selector'] == '#marketing'
    assert question['form_action'] == SECURE_URL
    assert not browser.calls

    # A supplied synthetic fact can be prepared normally. The final code
    # decision still owns Submit; this reversible click never submits.
    browser.allow_mutations = True
    filled = {}
    receipt = await forms.execute(
        browser, page, action, facts={question_text: 'I agree'},
        filled=filled, resume_path='', company='example-co',
        jd_text='', resume_tex='', github='', pk=PK, job_url=SECURE_URL)
    assert receipt['action'] == 'choose'
    assert browser.calls == [('click', '#marketing')]
    assert page['controls'][0]['checked'] is True

@pytest.mark.parametrize('verb,caption', [
    ('submit', 'Submit application'), ('click', 'Continue'),
])
async def test_unbound_application_session_cannot_issue_committing_click(
        monkeypatch, verb, caption):
    """A fake/nonbinding Session cannot bypass the one raw Submit guard.

    The synthetic HTTPS row, current form and already attached test PDF are
    otherwise admissible. A complete form is not authority for raw click IPC.
    """
    page = secure_inventory(field(
        'Resume', '#resume', type='file', required=False,
        files=['Test User Resume.pdf'], has_value=True))
    page['controls'][-1]['label'] = caption
    browser, decide, row = world(monkeypatch, page)
    row.update(company='example-co', job_id='1', jd_url=SECURE_URL,
               ats='custom', title=page['title'])
    decide.return_value = {'action': verb, 'selector': '#submit'}

    report = await forms.apply(
        SECURE_URL, 'example-co', {}, 'synthetic',
        pk=PK, resume_path='Test User Resume.pdf')
    assert report['status'] == 'gate'
    assert 'bound raw IPC guard' in report['question']
    assert 'no click is safe' in report['question']
    assert decide.await_count == 1
    assert not any(call[0] == 'click' for call in browser.calls)
    assert row['status'] == 'submitting' and row['possible_submission'] is False


def _synthetic_aria_role_combobox_page(tag='button'):
    combo = dict(
        linked=True, link_id='work-mode-list', listbox_selector='#work-mode-list',
        options=[dict(selector='#work-mode-remote', unique=True,
                      value='remote', label='Remote')],
        selected_text='', displayed_text='', selected_value='',
        hidden_selector='', hidden_value='',
    )
    return secure_inventory(field(
        'Work mode', '#work-mode', tag=tag, type='combobox',
        question='Work mode', combobox=combo, submit=False, href='',
    ))


@pytest.mark.parametrize('tag', ['button', 'span'])
def test_safe_aria_combobox_roles_are_tag_independent(tag, monkeypatch):
    # Never query the owner's tracking store in offline synthetic proof.
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting', 'possible_submission': False,
        'human_approved_answers': {},
    })
    # A documented ARIA role/linkage is evidence; a React-specific input/div
    # taxonomy is not. The independent transport witness must still agree.
    page = _synthetic_aria_role_combobox_page(tag)
    independent = witness(page)
    independent['controls'][0]['combobox'] = deepcopy(
        page['controls'][0]['combobox'])
    proof = {c['selector']: c for c in independent['controls']}
    question = forms._disclosure_question(
        page, page['controls'][0], proof, SECURE_URL)
    assert question['type'] == 'combobox'
    assert question['tag'] == tag
    assert question['selector'] == '#work-mode'
    assert question['form_action'] == SECURE_URL
    assert question['label'] == 'Work mode'
    # Neither valid accessibility nor options grant an unapproved answer.
    with pytest.raises(forms.Gate, match='exact human-approved answer'):
        forms.exact_approval(
            'example-co#1', question['label'], 'Remote',
            target=page['controls'][0], url=SECURE_URL)


@pytest.mark.parametrize('unsafe', [
    {'submit': True},
    {'navigates': True},
    {'href': 'https://foreign.example-co.test/open'},
    {'formaction': 'https://foreign.example-co.test/submit'},
    {'combobox': {'linked': True, 'link_id': '',
                  'listbox_selector': '', 'options': []}},
])
def test_aria_combobox_cannot_launder_committing_receiver(unsafe):
    page = _synthetic_aria_role_combobox_page()
    page['controls'][0].update(unsafe)
    proof = witness(page)
    proof['controls'][0]['combobox'] = deepcopy(
        page['controls'][0]['combobox'])
    with pytest.raises(forms.Gate, match='safe, exclusively linked ARIA combobox'):
        forms._disclosure_question(
            page, page['controls'][0],
            {c['selector']: c for c in proof['controls']},
            SECURE_URL)

@pytest.mark.parametrize('positive', [
    'acknowledge', 'acknowledges', 'acknowledged', 'acknowledging',
    'acknowledgement', 'acknowledgments', 'acknowledgment',
    'certify', 'certifies', 'certified', 'certifying',
    'certification', 'certifications', 'certificate', 'certificates',
    'authorization', 'authorizations', 'authorisation', 'authorisations',
    'authorize', 'authorizes', 'authorized', 'authorizing',
    'authorise', 'authorises', 'authorised', 'authorising',
])
def test_material_real_word_inflections_match_at_word_boundaries(positive):
    assert forms._MATERIAL_ASSENT.search('Please ' + positive + ' this application.')
    assert not forms._MATERIAL_ASSENT.search('pre' + positive + 'suffix')


@pytest.mark.parametrize('negative', [
    'acknowledg', 'certif', 'authori', 'authoriz', 'authorizationless',
    'authoriseable', 'certifiable', 'uncertifiedness', 'acknowledgability',
])
def test_material_partial_stems_are_not_standalone_legal_words(negative):
    assert forms._MATERIAL_ASSENT.search(negative) is None


@pytest.mark.parametrize('host', [
    'example-co.test', 'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io',
])
@pytest.mark.parametrize('scope', ['empty', 'global', 'company', 'shadow'])
async def test_scoped_facts_reach_runtime_and_decision_keys_on_regional_hosts(
        monkeypatch, tmp_path, host, scope):
    """Offline source transport proof, not an assertion about any live model."""
    from core.models import AnswerScope
    from core.storage.local import MarkdownAnswerBank
    from core import stores as store_module
    from tools import browser_apply as dispatch, browser_runtime as runtime
    from tools import submit_hold, posting_guard

    company = 'example-co'
    bank = MarkdownAnswerBank(tmp_path / 'synthetic-facts.md')
    if scope in {'global', 'shadow'}:
        bank.put('Contact detail', 'GLOBAL_SENTINEL', AnswerScope.GLOBAL)
    if scope in {'company', 'shadow'}:
        bank.put('Contact detail', 'COMPANY_SENTINEL', AnswerScope.COMPANY,
                 company=company)
    facts = bank.all_facts(company)
    expect = ('COMPANY_SENTINEL' if scope in {'company', 'shadow'}
              else 'GLOBAL_SENTINEL' if scope == 'global' else '')
    assert facts == ({'Contact detail': expect} if expect else {})
    assert bank.all_facts('another-co') == (
        {'Contact detail': 'GLOBAL_SENTINEL'} if scope in {'global', 'shadow'} else {})

    url = ('https://example-co.test/job/1' if host == 'example-co.test'
           else f'https://{host}/example-co/jobs/1')
    target = field('Contact detail', '#contact', required=False)
    p = inventory(target, submit(), url=url)
    for item in p['controls']:
        item['form_action'] = url
    browser, decide, row = world(monkeypatch, p)
    row.update(company=company, jd_url=url, job_id='1',
               ats='greenhouse' if 'greenhouse.io' in host else 'custom')
    if expect:
        row['human_approved_answers'] = {
            'Contact detail': {
                'value': expect, 'selector': '#contact', 'url': url,
                'label': 'Contact detail', 'question': '', 'tag': 'input',
                'type': 'text', 'form_selector': '#application',
                'form_action': url,
            }}
    monkeypatch.setattr(store_module, 'make_stores',
                        lambda: SimpleNamespace(tracking=SimpleNamespace(
                            get_current=lambda pk: deepcopy(row))))
    monkeypatch.setattr(submit_hold, 'blocked', lambda *a, **kw: False)
    monkeypatch.setattr(posting_guard, 'authorised', lambda *a, **kw: True)
    monkeypatch.setattr(runtime, 'available', lambda: (True, ''))
    monkeypatch.setattr(runtime, 'backend',
                        lambda: SimpleNamespace(apply_chrome=forms.apply))
    report = await dispatch.apply(
        url, company, facts, 'synthetic', pk=PK, jd_text='Synthetic role',
        resume_path='Resume.pdf')
    assert report['status'] == 'gate' and decide.await_count == 1
    prompt, model_page, history = decide.await_args.args
    advertised = "['Contact detail']" if expect else '[]'
    assert f'Available fact keys: {advertised}' in prompt
    assert model_page['controls'][0]['label'] == 'Contact detail'
    assert history == []
    assert not any(secret in str((prompt, model_page, history))
                   for secret in ('GLOBAL_SENTINEL', 'COMPANY_SENTINEL'))
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)

@pytest.mark.parametrize('optional', [True, False])
async def test_material_select_uses_fact_or_owner_question_not_widget_gate(optional):
    consent = field(
        'Data privacy agreement', '#consent-optional',
        required=not optional, tag='select', type='select',
        options=[{'value': '', 'label': 'Please choose'},
                 {'value': 'yes', 'label': 'I agree'},
                 {'value': 'no', 'label': 'I decline'}])
    page = secure_inventory(consent)
    browser = Browser(page)
    action = {'action': 'select', 'selector': consent['selector'],
              'fact': 'Data privacy agreement'}
    with pytest.raises(forms.Gate) as denied:
        await forms.execute(browser, page, action, facts={}, filled={},
                            resume_path='', company='example-co', jd_text='',
                            resume_tex='', github='', pk=PK, job_url=SECURE_URL)
    assert denied.value.reason == 'owner_question'
    assert denied.value.form_question['selector'] == consent['selector']
    assert browser.calls == []
    browser.allow_mutations = True
    filled = {}
    receipt = await forms.execute(
        browser, page, action, facts={'Data privacy agreement': 'I decline'},
        filled=filled, resume_path='', company='example-co', jd_text='',
        resume_tex='', github='', pk=PK, job_url=SECURE_URL)
    assert receipt['action'] == 'select'
    assert filled == {consent['selector']: 'no'}
    assert browser.calls == [('select', consent['selector'], '--value', 'no')]

@pytest.mark.parametrize('label', [
    'Are you legally authorized to work in the country…',
    'Are you currently authorized to work in the U.S.?',
    'Do you have work authorization?',
    'Do you have authorization to work in this country?',
    'Are you legally eligible to work in the U.S.?',
    'Are you authorised to work in this country?',
])
@pytest.mark.parametrize('kind', ['text', 'select', 'combobox'])
async def test_ordinary_work_eligibility_uses_bank_fact_preparation(
        monkeypatch, label, kind):
    """The production bootstrap, not a regex-only assertion, admits these facts."""
    eligible = field(label, '#eligibility', type=kind, tag=(
        'select' if kind == 'select' else 'input'))
    if kind == 'select':
        eligible['options'] = [
            {'value': '', 'label': 'Choose'},
            {'value': 'yes', 'label': 'Yes'},
            {'value': 'no', 'label': 'No'},
        ]
    if kind == 'combobox':
        eligible['combobox'] = {
            'linked': True, 'link_id': 'eligibility-options',
            'listbox_selector': '#eligibility-options',
            'options': [{'selector': '#yes', 'unique': True,
                         'label': 'Yes', 'value': 'yes', 'safe': True},
                        {'selector': '#no', 'unique': True,
                         'label': 'No', 'value': 'no', 'safe': True}],
            'selected_text': '', 'displayed_text': '', 'selected_value': '',
            'hidden_selector': '#eligibility-hidden', 'hidden_value': '',
        }
    page = secure_inventory(eligible)
    browser, decide, row = secure_world(monkeypatch, page)
    # An ordinary bank fact is eligible for preparation; it must never
    # manufacture a same-job material-assent grant.
    row['human_approved_answers'] = {}
    report = await secure_attempt(page, facts={label: 'Yes'})
    assert report['status'] == 'gate'
    assert report.get('material_assent_diagnostic') is not True
    assert report['form_question'] is None
    assert decide.await_count == 1
    assert not row['human_approved_answers']
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)


@pytest.mark.parametrize('label', [
    'I authorize Example Co to conduct a background check',
    'Are you authorized to work? I consent to a background check.',
    'Work authorization and Candidate Privacy Notice acknowledgement',
    'Authorization to work and agreement to legal terms',
])
async def test_work_eligibility_and_consent_are_ordinary_answerable_fields(
        monkeypatch, label):
    """Mixed legal answers use supplied facts, not a seventh pre-Submit gate."""
    material = field(label, '#material', tag='select', type='select',
                     options=[{'value': '', 'label': 'Choose'},
                              {'value': 'yes', 'label': 'I agree'},
                              {'value': 'no', 'label': 'I decline'}])
    page = secure_inventory(material)
    browser = Browser(page)
    action = {'action': 'select', 'selector': '#material', 'fact': label}

    with pytest.raises(forms.Gate) as missing:
        await forms.execute(browser, page, action, facts={}, filled={},
                            resume_path='', company='example-co',
                            jd_text='', resume_tex='', github='', pk=PK,
                            job_url=SECURE_URL)
    assert missing.value.reason == 'owner_question'
    assert missing.value.form_question['selector'] == '#material'
    assert browser.calls == []

    browser.allow_mutations = True
    filled = {}
    receipt = await forms.execute(
        browser, page, action, facts={label: 'I agree'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=SECURE_URL)
    assert receipt['action'] == 'select'
    assert browser.calls == [('select', '#material', '--value', 'yes')]
    assert page['controls'][0]['value'] == 'yes'
    assert filled == {'#material': 'yes'}

def test_navigation_diagnostic_has_bounded_host_path_and_unavailable_evidence():
    """No URL text, userinfo, query, fragment or path payload escapes the Gate."""
    tracked = 'https://example-co.test/job/1?tracked=PRIVATE_QUERY'
    url = ('https://USER_SECRET:PASSWORD_SECRET@foreign.example.test/other-job'
           '?token=QUERY_SECRET#FRAGMENT_SECRET')
    failure = forms._navigation_gate(
        'Initial redirect is not proven to be the same employer job.',
        tracked, url)
    message = str(failure)
    assert failure.reason == 'defect_provenance'
    assert 'Failed predicate: literal_https_destination_origin' in message
    assert 'Tracked host/path: example-co.test/job/1' in message
    assert 'destination host/path: foreign.example.test/other-job' in message
    assert 'independent redirect/ATS provenance: unavailable' in message
    for secret in ('PRIVATE_QUERY', 'USER_SECRET', 'PASSWORD_SECRET',
                   'QUERY_SECRET', 'FRAGMENT_SECRET', 'https://', '@'):
        assert secret not in message
    oversized = 'https://foreign.example.test/' + 'PATH_SECRET' * 30
    assert forms._navigation_host_path(oversized) == (
        'foreign.example.test/(path unavailable)')
    assert forms._navigation_host_path(
        'https://foreign.example.test/opening\nCONTROL_SECRET') == 'unavailable'
    assert forms._navigation_host_path('missing destination') == 'unavailable'


async def test_initial_navigation_redirect_explains_same_employer_origin(monkeypatch):
    page = secure_inventory(field())
    browser, _, row = secure_world(monkeypatch, page)
    bad = ('https://foreign.example.test/job/1'
           '?token=QUERY_SECRET#FRAGMENT_SECRET')
    browser.current = {**deepcopy(page), 'url': bad}
    report = await secure_attempt(page)
    assert report['status'] == 'gate'
    assert report['reason'] == 'defect_provenance'
    assert report['review_route'] == 'hard_refusal'
    assert 'Initial redirect is not proven' in report['question']
    assert 'Failed predicate: same_tracked_employer_https_origin' in report['question']
    assert 'Tracked host/path: example-co.test/job/1' in report['question']
    assert 'destination host/path: foreign.example.test/job/1' in report['question']
    assert report['step']['url'] == 'unavailable'
    assert 'QUERY_SECRET' not in report['question']
    assert 'FRAGMENT_SECRET' not in report['question']
    assert browser.calls == [('navigate', SECURE_URL)]
    assert row.get('possible_submission') is False


@pytest.mark.parametrize('verb', ['click', 'navigate'])
async def test_observed_link_and_model_navigation_share_failed_provenance_predicate(
        monkeypatch, verb):
    destination = ('https://foreign.example.test/other-job'
                   '?token=QUERY_SECRET#FRAGMENT_SECRET')
    linked = field('Read more', '#next', tag='a', type='link',
                   href=destination, required=False, in_form=False)
    page = secure_inventory(field(), linked)
    browser, decide, row = secure_world(monkeypatch, page)
    # Retain the production action-scope and route predicates. Only the
    # unrelated field reader/approved-choice setup is skipped by this fixture.
    monkeypatch.setattr(forms, '_bootstrap',
                        AsyncMock(side_effect=lambda _s, current, *a, **k: current))
    monkeypatch.setattr(forms, '_current_approved_choices',
                        AsyncMock(return_value=[]))
    decide.return_value = ({'action': 'click', 'selector': '#next'}
                           if verb == 'click'
                           else {'action': 'navigate', 'url': destination})
    report = await secure_attempt(page)
    assert decide.await_count == 1
    assert report['status'] == 'gate'
    assert report['reason'] == 'defect_provenance'
    assert report['navigation_provenance_diagnostic'] is True
    assert 'Navigation requires provenance to the same tracked job and tenant.' in (
        report['question'])
    assert 'Failed predicate: same_tracked_employer_https_origin' in report['question']
    assert 'destination host/path: foreign.example.test/other-job' in report['question']
    assert 'QUERY_SECRET' not in report['question']
    assert 'FRAGMENT_SECRET' not in report['question']
    assert browser.calls == [('navigate', SECURE_URL)]
    assert row.get('possible_submission') is False


async def test_navigation_diagnostic_stays_out_of_telemetry(monkeypatch):
    import logging
    from io import StringIO

    from agent import run
    from core.logging import _ConsoleFormatter, _JsonFormatter

    # The same real consumer sinks must redact both owner-only diagnostics.
    nav_page = secure_inventory(field())
    nav_browser, _, _ = secure_world(monkeypatch, nav_page)
    nav_browser.current = {
        **deepcopy(nav_page),
        'url': ('https://USER_SECRET:PASSWORD_SECRET@foreign.example.test/other-job'
                '?token=QUERY_SECRET#FRAGMENT_SECRET')}
    navigation = await secure_attempt(nav_page)
    assert navigation['status'] == 'gate'
    # BrowserSkill reports the code-owned reason; run.py must redact it
    # without relying on the removed producer-side diagnostic flag.
    assert navigation.get('navigation_provenance_diagnostic') is not True
    assert navigation['reason'] == 'defect_provenance'

    row = {'pk': PK, 'status': 'tailored', 'company': 'example-co',
           'jd_url': SECURE_URL, 'title': 'Synthetic Role'}

    class Tracking:
        def get(self, key):
            assert key == PK
            return dict(row)

        def get_current(self, key):
            assert key == PK
            return dict(row)

        def set_status(self, key, status, **updates):
            assert key == PK
            row.update(status=getattr(status, 'value', status), **updates)

    stores = SimpleNamespace(
        tracking=Tracking(), secrets=None,
        answer_bank=SimpleNamespace(all_facts=lambda company: {}))
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *a, **kw: False)
    monkeypatch.setattr(run, '_jd_meta', AsyncMock(
        return_value={'text': 'Synthetic offline description'}))
    monkeypatch.setattr(run, '_commit_posting_meta',
                        lambda pk, current, meta, stores: dict(current))
    monkeypatch.setattr('tools.posting_guard.authorised', lambda *a, **kw: True)
    monkeypatch.setattr(run, '_no_sponsorship', lambda text: False)
    monkeypatch.setattr('core.rotation.ensure', lambda *a, **kw: None)
    monkeypatch.setattr('core.profiles.resolve_for', lambda row: None)
    monkeypatch.setattr('core.profiles.expand_all', lambda facts: facts)
    monkeypatch.setattr('tools.credentials.get_login', lambda *a, **kw: None)
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: 'Synthetic.pdf')
    monkeypatch.setattr(
        run, 'get_settings', lambda: SimpleNamespace(browser_model='offline'))
    monkeypatch.setattr(
        'tools.browser_runtime.configuration', lambda: {'engine': 'browser_skill'})
    assert run.log.propagate is False
    handlers = [handler for handler in run.log.handlers
                if type(handler) is logging.StreamHandler
                and isinstance(handler.formatter,
                               (_ConsoleFormatter, _JsonFormatter))]
    assert handlers

    cases = ((navigation, 'navigation-provenance: human inspection required',
              ('foreign.example.test', 'example-co.test/job/1', 'other-job',
               'USER_SECRET', 'PASSWORD_SECRET', 'QUERY_SECRET', 'FRAGMENT_SECRET')),)
    for report, safe_detail, private_values in cases:
        monkeypatch.setattr('tools.browser_apply.apply',
                            AsyncMock(return_value=report))
        for queued_worker in (False, True):
            events = []
            monkeypatch.setattr(
                'core.events.emit',
                lambda kind, **kw: events.append((kind, kw)))
            row['status'] = 'tailored'
            with StringIO() as stream, monkeypatch.context() as capture:
                capture.setattr(run.log, 'level', logging.INFO)
                capture.setattr(handlers[0], 'stream', stream)
                if queued_worker:
                    capture.setattr(run, 'make_stores', lambda: stores)
                    queue = SimpleNamespace(retry=Mock(), done=Mock())
                    item = {'pk': PK}
                    result = run.run_queued(item, queue)
                    queue.done.assert_called_once_with(item)
                    queue.retry.assert_not_called()
                else:
                    result = await run._apply_direct(PK, stores)
                info_output = stream.getvalue()
            assert result['result'] == 'gated'
            assert result['question'] == report['question']
            assert row['status'] == 'needs_human'
            assert row['gate_pending']['question'] == report['question']
            gate_events = [data for kind, data in events if kind == 'gate']
            assert len(gate_events) == 1
            assert gate_events[0]['detail'] == safe_detail
            if report is navigation:
                assert 'url' not in gate_events[0]
            gated = [line for line in info_output.splitlines()
                     if 'gated pk=' in line]
            assert len(gated) == 1
            assert safe_detail in gated[0]
            if queued_worker:
                queued = [line for line in info_output.splitlines()
                          if 'apply: ' in line]
                assert len(queued) == 1
                assert safe_detail in queued[0]
            for private in private_values:
                assert private not in info_output
                assert private not in str(gate_events)
            if report is navigation:
                assert result['navigation_provenance_diagnostic'] is True
                assert row['gate_reason'] == 'defect_provenance'
                assert 'foreign.example.test/other-job' in report['question']
                for secret in ('USER_SECRET', 'PASSWORD_SECRET',
                               'QUERY_SECRET', 'FRAGMENT_SECRET'):
                    assert secret not in report['question']


@pytest.mark.parametrize('suffix', ['', '%2Dencoded'])
def test_foreign_host_predicate_precedes_path_encoding(suffix):
    destination = 'https://foreign.example.test/other' + suffix
    gate = forms._navigation_gate(
        'Navigation requires same-job provenance.',
        SECURE_URL, destination)
    assert 'Failed predicate: same_tracked_employer_https_origin' in str(gate)


@pytest.mark.parametrize('ats,route_suffix', [
    ('ashby', 'application'), ('lever', 'apply'),
])
def test_typed_entry_foreign_host_uses_rejecting_origin_predicate(ats, route_suffix):
    job = '12345678-1234-4234-8234-123456789abc'
    tracked = f'https://{forms._APPLICATION_ENTRY[ats][0]}/example-co/{job}'
    proof = forms.ApplicationEntryProvenance(
        'example-co#' + job, 'example-co', ats, job,
        tracked, 'example-co', tracked + '/' + route_suffix)
    gate = forms._navigation_gate(
        'Navigation requires same-job provenance.', tracked,
        'https://foreign.example.test/other%2Djob?job=QUERY_SENTINEL',
        provenance=proof)
    detail = str(gate)
    assert 'Failed predicate: same_tracked_employer_https_origin' in detail
    assert 'QUERY_SENTINEL' not in detail
    assert 'foreign.example.test/(path unavailable)' in detail


@pytest.mark.parametrize('href,commits', [
    ('https://jobs.ashbyhq.com/example-co/job/application', False),
    ('https://jobs.lever.co/example-co/job/apply', False),
    ('https://jobs.ashbyhq.com/example-co/job/submit', True),
    ('https://jobs.ashbyhq.com/example-co/job/requestSubmit', True),
    ('https://jobs.ashbyhq.com/example-co/job/%73ubmit', True),
    ('https://jobs.ashbyhq.com/example-co/job/application?next=%73ubmit', True),
    ('https://jobs.ashbyhq.com/example-co/job/application#finalize', True),
])
def test_raw_get_effect_classification_keeps_safe_application_reversible(
        href, commits):
    """Only the final IPC producer may commit; plain form GET is preparation."""
    assert forms._committing_get_route(href) is commits


def test_tracked_literal_origin_does_not_borrow_destination_policy(monkeypatch):
    """A tracked canonical posting is checked independently of chosen navigation."""
    from core.ids import make_pk
    job = '12345678-1234-4234-8234-123456789abc'
    company = 'example-co'
    url = f'https://jobs.ashbyhq.com/{company}/{job}'
    pk = make_pk(company, job)
    monkeypatch.setattr(forms, '_row', lambda *_args, **_kw: {
        'pk': pk, 'company': company, 'job_id': job, 'jd_url': url,
        'ats': 'ashby', 'status': 'submitting',
        'possible_submission': False,
    })
    monkeypatch.setattr(forms, 'navigation_allowed',
                        lambda *_args, **_kwargs: False)
    forms._tracked_preparation_job(pk, company, url)
    assert not forms._preparation_origin(
        'https://jobs.ashbyhq.com/other-co/' + job, url)


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [
    'material-resume-open', 'material-witness', 'execute',
])
async def test_identity_flaps_then_stabilizes_with_complete_witness(
        monkeypatch, site):
    import asyncio
    base = inventory(field(), submit())
    pages = [deepcopy(base) for _ in range(7)]
    pages[1]['text'] = 'Different synthetic inventory'
    # Identity changes are made in inventory-relevant properties, not prose.
    pages[1]['controls'][0]['label'] = 'Changed initial receiver'
    sequence = iter([pages[1], pages[2], pages[3], pages[4], pages[5]])
    calls = []
    class Flapping:
        async def call(self, *args):
            calls.append(('evaluate', args))
            return {'value': witness(self.page_snapshot)}
        async def page(self):
            self.page_snapshot = next(sequence)
            calls.append(('page', None))
            return deepcopy(self.page_snapshot)
    browser = Flapping()
    browser.page_snapshot = base
    slept = []
    async def fake_sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', fake_sleep)
    current, proof = await forms._observe_controls(browser, base, site=site)
    assert current['controls'][0]['label'] == 'Email'
    assert '#email' in proof
    assert len(slept) <= 3
    assert all(delay == 0.25 for delay in slept)


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [
    'material-resume-open', 'material-witness', 'execute',
])
async def test_identity_never_stabilizes_and_gates_with_literal_site(
        monkeypatch, site):
    base = inventory(field(), submit())
    class Flapping:
        reads = 0
        async def call(self, *args):
            return {'value': witness(self.last_page)}
        async def page(self):
            self.reads += 1
            fresh = deepcopy(base)
            fresh['controls'][0]['label'] = 'Synthetic version ' + str(self.reads)
            self.last_page = fresh
            return deepcopy(fresh)
    browser = Flapping()
    browser.last_page = base
    delays = []
    async def fake_sleep(seconds):
        delays.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', fake_sleep)
    with pytest.raises(forms.Gate) as exc:
        await forms._observe_controls(browser, base, site=site)
    assert 'predicate=inventory.identity.equal' in str(exc.value)
    assert 'site=' + site in str(exc.value)
    assert len(delays) == 3
    assert all(delay == 0.25 for delay in delays)


@pytest.mark.asyncio
async def test_other_witness_predicates_remain_single_read(monkeypatch):
    base = inventory(field(), submit())
    bad = witness(base)
    bad['controls'][0]['selector'] = '#wrong'
    browser = Browser(base, result=bad)
    delays = []
    async def fake_sleep(seconds):
        delays.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', fake_sleep)
    with pytest.raises(forms.Gate) as exc:
        await forms._observe_controls(browser, base, site='execute')
    assert 'predicate=selector.equal' in str(exc.value)
    assert 'site=execute' in str(exc.value)
    assert not delays
    assert len([call for call in browser.calls if call[0] == 'evaluate']) == 1

async def test_reversible_fill_has_no_obsolete_prewrite_inventory_retry(monkeypatch):
    page = secure_inventory(field())
    browser, _, _ = secure_world(monkeypatch, page)
    browser.allow_mutations = True
    witness_calls = []
    original_call = browser.call

    async def transport(*args, **kwargs):
        if args[0] == 'evaluate':
            witness_calls.append(args)
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(browser, 'call', transport)
    filled = {}
    receipt = await forms.execute(
        browser, page, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
        facts={'Email': 'synthetic@example.test'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=SECURE_URL)
    assert receipt['action'] == 'fill'
    assert filled == {'#email': 'synthetic@example.test'}
    assert browser.calls == [('fill', '#email', '--value', 'synthetic@example.test')]
    assert witness_calls == []  # final Submit, not reversible fill, re-reads

@pytest.mark.asyncio
@pytest.mark.parametrize('site', ['bootstrap-formless', 'bootstrap-material'])
@pytest.mark.parametrize('failure', ['length', 'identity'])
@pytest.mark.parametrize('stabilizes', [True, False])
async def test_bootstrap_witness_bounded_reread(monkeypatch, site, failure, stabilizes):
    """A transient synthetic length/identity flap needs two stable complete reads."""
    base = inventory(field(), submit())
    class Flapping:
        def __init__(self):
            self.reads = 0
            self.evaluations = 0
            self.current = deepcopy(base)
        async def page(self):
            self.reads += 1
            self.current = deepcopy(base)
            if failure == 'identity' and (not stabilizes or self.reads == 1):
                self.current['controls'][0]['label'] = 'Synthetic version ' + str(self.reads)
            return deepcopy(self.current)
        async def call(self, *args):
            assert args == ('evaluate', forms._FIXED_CONTROL_WITNESS)
            self.evaluations += 1
            value = witness(self.current if failure == 'identity' else base)
            if failure == 'length' and (not stabilizes or self.evaluations == 1):
                value['controls'].pop()
            return {'value': value}
    browser = Flapping()
    delays = []
    async def fake_sleep(seconds):
        delays.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', fake_sleep)
    if stabilizes:
        observed, proof = await forms._observe_controls(browser, base, site=site)
        assert observed['url'] == base['url']
        assert set(proof) == {'#email', '#submit'}
        assert browser.evaluations <= 4
    else:
        with pytest.raises(forms.Gate) as denied:
            await forms._observe_controls(browser, base, site=site)
        assert 'site=' + site in str(denied.value)
        assert ('predicate=controls.length.equal' if failure == 'length'
                else 'predicate=inventory.identity.equal') in str(denied.value)
        assert browser.evaluations == 4
        assert len(delays) == 3
    assert all(delay == 0.25 for delay in delays)


def test_every_production_observe_controls_caller_names_its_site():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(forms))
    literal_sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != '_observe_controls':
            continue
        kw = next((x.value for x in node.keywords if x.arg == 'site'), None)
        if kw is None:
            # Both bootstrap and execute preserve the two-argument seam for
            # fixed synthetic witness doubles; code-owned context supplies site.
            # Untagged observer calls live only in the fixed bootstrap and
            # execute seams. Match enclosing functions instead of line numbers.
            parents = {child: parent for parent in ast.walk(tree)
                       for child in ast.iter_child_nodes(parent)}
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            assert owner is not None and owner.name in {
                '_bootstrap_observe_controls', '_execute_observe_controls'}
            continue
        if isinstance(kw, ast.Name):
            assert kw.id == 'site'
        else:
            assert isinstance(kw, ast.Constant) and isinstance(kw.value, str) and kw.value
            literal_sites.append(kw.value)
    assert {'execute', 'execute-readback'} <= set(literal_sites)
    assert len(literal_sites) >= 19
    # Bootstrap uses a two-positional-argument seam required by existing
    # synthetic submission guards; its scoped wrapper still owns fixed sites.
    wrappers = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == '_bootstrap_observe_controls']
    assert {next(k.value.value for k in node.keywords if k.arg == 'site')
            for node in wrappers} == {'bootstrap-formless', 'bootstrap-material'}


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [
    'confirmation-readback', 'readiness-witness', 'bootstrap-commit-readback',
    'native-receiver-readback', 'combobox-observe', 'execute-readback',
])
async def test_named_non_retry_observers_remain_single_read(monkeypatch, site):
    """An attribution-only site never opts a caller into inventory retries."""
    base = inventory(field(), submit())
    browser = Browser(base)
    reads = 0
    async def unstable_page():
        nonlocal reads
        reads += 1
        fresh = deepcopy(base)
        fresh['controls'][0]['label'] = 'Synthetic changed field'
        return fresh
    monkeypatch.setattr(browser, 'page', unstable_page)
    delays = []
    async def fake_sleep(seconds):
        delays.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', fake_sleep)
    with pytest.raises(forms.Gate) as denied:
        await forms._observe_controls(browser, base, site=site)
    assert 'predicate=inventory.identity.equal' in str(denied.value)
    assert 'site=' + site in str(denied.value)
    assert [call[0] for call in browser.calls] == ['evaluate']
    assert reads == 1
    assert not delays


@pytest.mark.asyncio
@pytest.mark.parametrize('site', ['bootstrap-formless', 'bootstrap-material'])
async def test_bootstrap_initially_stable_witness_never_rereads(monkeypatch, site):
    """A stable first bootstrap observation retains the one-evaluate contract."""
    page = inventory(field(), submit())
    browser = Browser(page)
    delays = []
    async def no_wait(seconds):
        delays.append(seconds)
    monkeypatch.setattr(forms.asyncio, 'sleep', no_wait)
    observed, controls = await forms._observe_controls(browser, page, site=site)
    assert observed == page
    assert set(controls) == {'#email', '#submit'}
    assert [call[0] for call in browser.calls] == ['evaluate']
    assert browser.pages == 1
    assert not delays


@pytest.mark.asyncio
async def test_final_readback_observer_reports_mismatched_current_selector(monkeypatch):
    # The independent DOM reader is still meaningful at final Submit. Only
    # the old per-write _execute_observe_controls wrapper was removed.
    page = inventory(field(), submit())
    browser = Browser(page)
    bad = witness(page)
    bad['controls'][0]['selector'] = '#changed'
    browser.result = bad
    with pytest.raises(forms.Gate) as error:
        await forms._observe_controls(browser, page, site='execute')
    assert 'predicate=selector.equal' in str(error.value)
    assert 'site=execute' in str(error.value)
    assert [call[0] for call in browser.calls] == ['evaluate']

@pytest.mark.parametrize('case,expected', [
    ('passive', ('yes', '1', '1', 'yes', 'yes')),
    ('non_ashby', ('no', '1', '1', 'yes', 'no')),
    ('duplicate_anchor', ('yes', '2', '1', 'no', 'no')),
    ('duplicate_bframe', ('yes', '1', '2', 'yes', 'no')),
    ('outside_badge', ('yes', '1', '1', 'no', 'no')),
    ('visible_bframe', ('yes', '1', '1', 'yes', 'no')),
])
def test_issue179_owner_gate_includes_bounded_enterprise_evidence(case, expected):
    # Assert the actual owner-facing exception, not a reader dictionary or the
    # formatter in isolation: a passive candidate can still need manual review
    # for a separate untrusted frame, without changing Enterprise admission.
    ashby, anchors, bframes, badge, passive = expected
    candidate = {
        'status': 'unknown', 'origin': 'https://www.google.com',
        'path': '/recaptcha/enterprise/anchor', 'source': 'url',
        'visibility': 'hidden', 'display': 'inline',
        'ashby_native': ashby, 'enterprise_anchors': int(anchors),
        'enterprise_bframes': int(bframes), 'anchor_in_badge': badge,
        'passive_enterprise': passive,
    }
    page = inventory(url=SECURE_URL)
    page['entry'] = {'frames': [candidate]}
    with pytest.raises(forms.CaptchaArtifactGate) as caught:
        forms._entry_safety(page, lambda url: url == SECURE_URL, None)
    message = str(caught.value)
    expected_text = (
        'unknown/untrusted application frame; inspect manually. '
        'Non-admitted frames: frame[1] origin=https://www.google.com '
        'path=/recaptcha/enterprise/anchor source_kind=url '
        'box=unknown outside_root=unknown visibility=hidden display=inline '
        'form_controls=unknown srcdoc=unknown parent_pattern=unknown '
        'anchor_sibling=unknown shared_recaptcha_container=unknown '
        f'ashby_native={ashby} enterprise_anchors={anchors} '
        f'enterprise_bframes={bframes} anchor_in_badge={badge} '
        f'passive_enterprise={passive} document_readability=unknown'
    )
    assert message == expected_text


@pytest.mark.parametrize('field,bad_value', [
    ('enterprise_anchors', True),
    ('enterprise_anchors', 10),
    ('enterprise_bframes', -1),
    ('enterprise_bframes', '2'),
])
def test_issue179_owner_gate_does_not_render_unbounded_counts(field, bad_value):
    frame = {
        'status': 'unknown', 'source': 'url',
        'ashby_native': 'yes', 'enterprise_anchors': 1,
        'enterprise_bframes': 1, 'anchor_in_badge': 'yes',
        'passive_enterprise': 'no',
    }
    frame[field] = bad_value
    page = inventory(url=SECURE_URL)
    page['entry'] = {'frames': [frame]}
    with pytest.raises(forms.CaptchaArtifactGate) as caught:
        forms._entry_safety(page, lambda url: url == SECURE_URL, None)
    assert f'{field}=other' in str(caught.value)

# Issue #178: only fresh, independently witnessed GLOBAL facts may acquire a
# row-bound grant. Every public fixture below is synthetic.
def _issue182_material_picker(monkeypatch, route='custom', *, closed_options=None):
    if route == 'lever':
        from tests.tools.test_browser_application_entry import (
            application, posting, world as entry_world)
        row, configs, _, _ = entry_world(monkeypatch, 'lever')
        page = application('lever')
        origin, pk = posting('lever'), row['pk']
        provenance = forms._application_entry(row, pk, 'example-co', origin, configs)
        material = field(
            'Acknowledge candidate privacy notice', '#material', type='combobox',
            form_selector='#application-form-lever', form_action=page['url'])
        page['controls'][0] = material
    else:
        page = secure_inventory(field(
            'Acknowledge candidate privacy notice', '#material', type='combobox'))
        _, _, row = secure_world(monkeypatch, page)
        material = page['controls'][0]
        origin, pk, provenance = SECURE_URL, PK, None

    material['combobox'] = {
        'linked': False, 'link_id': '', 'listbox_selector': '',
        'options': closed_options, 'selected_text': '', 'displayed_text': '',
        'selected_value': '', 'hidden_selector': '', 'hidden_value': '',
    }
    options = [
        {'selector': '#accept', 'label': 'I agree', 'value': 'yes'},
        {'selector': '#decline', 'label': 'I decline', 'value': 'no'},
    ]
    opened = deepcopy(page)
    opened['controls'][0]['combobox'].update(
        linked=True, link_id='material-list', listbox_selector='#material-list',
        options=[{**option, 'unique': True} for option in options])
    row['human_approved_answers'] = {
        material['label']: {
            'value': 'I agree', 'selector': material['selector'],
            'url': page['url'], 'label': material['label'],
            'question': material['question'], 'tag': material['tag'],
            'type': material['type'], 'form_selector': material['form_selector'],
            'form_action': material['form_action'], 'options': deepcopy(options),
        },
    }
    return row, page, opened, pk, origin, provenance


class _Issue182PickerBrowser(Browser):
    """Only reversible open/Escape operations; no synthetic write or Submit."""
    def __init__(self, closed, opened):
        super().__init__(deepcopy(closed))
        self.closed, self.opened = deepcopy(closed), deepcopy(opened)

    async def call(self, *args, **kwargs):
        if args in {('click', '#material'), ('press', 'Escape')}:
            self.calls.append(args)
            self.current = deepcopy(
                self.opened if args[0] == 'click' else self.closed)
            self.result = witness(self.current)
            return {}
        return await super().call(*args, **kwargs)


def _owner_reply(tracking, row):
    """Issue #202 r07: reissue the hand-built direct gate with a fresh
    generation, give the row stub the storage contract's own consume, and
    return the exact current-gate tokens the dashboard sends."""
    from agent import run
    from core.storage.base import direct_gate_patch
    pending = row['gate_pending']
    row['gate_pending'] = run._direct_gate_pending(
        pending.get('question'), pending['form_question'], 'owner')

    def consume(pk, *, expected_pending, disposition, grant=None, skip=None):
        patch = direct_gate_patch(row, expected_pending, disposition, grant, skip)
        if patch is None:
            return False
        row.update(deepcopy(patch))
        return True

    tracking.consume_direct_form_gate = consume
    return {'disposition': 'answer',
            'expected_gate_generation': row['gate_pending']['generation'],
            'expected_question_identity': forms.owner_question_identity(
                row['pk'], row['gate_pending']['form_question'])}
