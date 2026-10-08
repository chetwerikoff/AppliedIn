"""The daemon must use the selected profile, not the external worker's habits.

All browser/model calls are fakes. In particular, a failed submit transport must
stay uncertain even when its error looks like a retryable browser outage.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_runtime as runtime
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import claude_chrome, jd

GOOD = 'Responsibilities and qualifications for the engineering program. ' * 20


def enable(tmp_path):
    (tmp_path / 'browser.local.yaml').write_text('engine: browser_skill\nbrowser: dedicated\n')


def target(**values):
    return {'selector': '#field', 'label': 'First name', 'question': '', 'type': 'text',
            'tag': 'input', 'disabled': False, 'has_value': False, 'value': '',
            'required': False, 'in_form': True, 'submit': False, **values}


def page(*controls, text=GOOD):
    return {'url': 'https://employer.test/job/1', 'title': 'Program manager',
            'text': text, 'description': GOOD, 'controls': list(controls),
            'truncated': False, 'unsupported_frames': [], 'shadow_roots': False,
            'inventory_verified': True, 'opaque_controls': False}


def test_private_config_preserves_original_engine_and_rejects_unknown_values(tmp_path):
    assert runtime.configuration()['engine'] == 'chrome'
    enable(tmp_path)
    cfg = runtime.configuration()
    assert cfg['engine'] == 'browser_skill' and cfg['browser'] == 'dedicated'
    assert cfg['user_data_dir'].endswith('/.local/share/appliedin/chrome')
    (tmp_path / 'browser.local.yaml').write_text('engine: imaginary\n')
    ready, reason = runtime.available()
    assert not ready
    assert 'Unsupported browser engine' in reason


def test_real_cli_browser_list_format_and_errors_do_not_leak_page_data(monkeypatch, tmp_path):
    enable(tmp_path)
    payload = [{'instance_id': 'dedicated', 'unresponsive': False, 'version_skew': False}]
    monkeypatch.setattr(bsk, '_executable', lambda: '/fake/bsk')
    monkeypatch.setattr(bsk.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps(payload), returncode=0))
    assert bsk.available() == (True, '')
    with pytest.raises(bsk.Unavailable) as exc:
        bsk._result(json.dumps({'ok': False, 'code': 'offline', 'message': 'PRIVATE ANSWER'}), 2)
    assert 'offline' in str(exc.value)
    assert 'PRIVATE' not in str(exc.value)


@pytest.mark.parametrize('browsers', [[], [{'instance_id': 'another'}],
                                     [{'instance_id': 'dedicated', 'unresponsive': True}]])
def test_missing_selected_profile_never_falls_back(monkeypatch, tmp_path, browsers):
    enable(tmp_path)
    monkeypatch.setattr(bsk, '_sync', lambda *a, **kw: {'browsers': browsers})
    monkeypatch.setattr('tools.browser_profile.profile_pids', lambda directory: [123])
    monkeypatch.setattr(claude_chrome, 'available', lambda: pytest.fail('No Claude fallback'))
    ready, reason = runtime.available()
    assert not ready and 'not connected' in reason


async def test_session_pins_profile_and_stops_only_its_own_window(monkeypatch, tmp_path):
    enable(tmp_path)
    calls = []
    monkeypatch.setattr(bsk, 'available', lambda: (True, ''))
    monkeypatch.setattr('tools.browser_profile.ensure_browser', lambda: (True, ''))
    async def command(*args, **kw):
        calls.append(args)
        if args[:2] == ('session', 'start'):
            return {'session_id': 'ours', 'browser_instance_id': 'dedicated'}
        return {'ok': True}
    monkeypatch.setattr(bsk, 'command', command)
    with pytest.raises(ValueError):
        async with bsk.Session('jd'):
            raise ValueError('posting failure')
    assert calls[0][2:4] == ('--browser', 'dedicated')
    assert calls[-1] == ('session', 'stop', 'ours')
    assert not bsk._LIVE


async def test_mismatched_profile_is_closed_before_navigation(monkeypatch, tmp_path):
    enable(tmp_path)
    monkeypatch.setattr(bsk, 'available', lambda: (True, ''))
    monkeypatch.setattr('tools.browser_profile.ensure_browser', lambda: (True, ''))
    command = AsyncMock(side_effect=[{'session_id': 'wrong', 'browser_instance_id': 'other'}, {}])
    monkeypatch.setattr(bsk, 'command', command)
    with pytest.raises(bsk.Unavailable, match='mismatch'):
        async with bsk.Session('jd'):
            pytest.fail('Must not act in another profile')
    assert command.call_args_list[-1].args == ('session', 'stop', 'wrong')


async def test_selected_jd_and_apply_dispatch_never_call_claude(monkeypatch, tmp_path):
    enable(tmp_path)
    monkeypatch.setattr(claude_chrome, 'run_task', AsyncMock(side_effect=AssertionError('Claude')))
    monkeypatch.setattr(claude_chrome, 'apply_chrome',
                        AsyncMock(side_effect=AssertionError('Claude')))
    read = AsyncMock(return_value=({'title': 'Program manager', 'description': GOOD}, ''))
    apply = AsyncMock(return_value={'status': 'gate', 'question': 'Review'})
    monkeypatch.setattr(bsk, 'run_task', read)
    monkeypatch.setattr(bsk, 'apply_chrome', apply)
    assert (await runtime.run_task('read', report_key='description'))[0]['description'] == GOOD
    assert (await runtime.apply('url', 'Company', {}, 'chatgpt/model'))['status'] == 'gate'
    assert read.await_count == apply.await_count == 1


def test_pipeline_reader_uses_browser_skill_and_outage_remains_retryable(monkeypatch, tmp_path):
    enable(tmp_path)
    monkeypatch.setattr(jd, '_browser_retry', (0, ''))
    monkeypatch.setattr(jd, '_from_ats', lambda url: None)
    monkeypatch.setattr(jd, '_get', lambda *args: None)
    monkeypatch.setattr(bsk, 'available', lambda: (True, ''))
    monkeypatch.setattr(bsk, 'run_task', AsyncMock(return_value=(
        {'title': 'Program manager', 'description': GOOD}, '')))
    monkeypatch.setattr(claude_chrome, 'run_task', AsyncMock(side_effect=AssertionError('Claude')))
    assert jd.fetch_jd('https://employer.test/job/1') == GOOD
    assert runtime.is_infrastructure('BrowserSkill unavailable: profile disconnected')
    assert runtime.is_disconnected('BrowserSkill unavailable: profile disconnected')


@pytest.mark.parametrize(('question', 'value'), [
    ('Gender', 'Male'), ('Disability', 'Yes'), ('Race', 'Asian'),
    ('Veteran status', 'I am a veteran'), ('Are you in a restricted country?', 'Yes'),
    ('First name', 'TODO'),
])
async def test_unsafe_values_are_refused_before_a_browser_write(question, value):
    session = SimpleNamespace(call=AsyncMock())
    with pytest.raises(forms.Gate):
        await forms.execute(session, page(target(label=question)),
                            {'action': 'fill', 'selector': '#field', 'fact': 'Answer'},
                            facts={'Answer': value}, filled={}, resume_path='',
                            company='Company', jd_text='', resume_tex='', github='')
    session.call.assert_not_awaited()


@pytest.mark.parametrize(('question', 'value'), [
    ('Disability', 'No'), ('Gender', 'Prefer not to say'),
    ('Restricted country', 'None of the above'),
])
def test_safe_declines_and_negative_sanctions_answers_are_not_left_blank(question, value):
    assert forms.guarded_value(target(label=question), value) == value


async def test_model_text_cannot_replace_approved_fact(monkeypatch):
    session = SimpleNamespace(call=AsyncMock())
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting',
        'human_approved_answers': {'First name': 'Approved'}})
    filled = {}
    await forms.execute(session, page(target()),
                        {'action': 'fill', 'selector': '#field', 'fact': 'First name',
                         'value': 'Invented'},
                        facts={'First name': 'Approved'}, filled=filled, resume_path='',
                        company='Company', jd_text='', resume_tex='', github='',
                        pk='Company#1')
    session.call.assert_awaited_once_with('fill', '#field', '--value', 'Approved')
    assert filled == {'#field': 'Approved'}


async def test_click_cannot_bypass_guarded_choice_or_submit():
    for control in [target(type='radio'), target(type='checkbox'),
                    target(tag='button', type='submit', label='Submit application', submit=True)]:
        session = SimpleNamespace(call=AsyncMock())
        with pytest.raises(forms.Gate):
            await forms.execute(session, page(control), {'action': 'click', 'selector': '#field'},
                                facts={}, filled={}, resume_path='', company='Company',
                                jd_text='', resume_tex='', github='')
        session.call.assert_not_awaited()


def test_native_option_must_match_both_the_dom_and_the_approved_answer():
    control = target(tag='select', type='select', label='Gender',
                     options=[{'value': '1', 'label': 'Male'},
                              {'value': '2', 'label': 'Prefer not to say'}])
    with pytest.raises(forms.Gate):
        forms.choose_value(control, 'Male', '1')
    assert forms.choose_value(control, 'Prefer not to say', '2') == '2'
    with pytest.raises(forms.Gate):
        forms.choose_value(control, 'Prefer not to say', '1')


def test_submit_requires_verified_attachment_and_rechecks_autofill():
    with pytest.raises(forms.Gate, match='attachment'):
        forms.check_form(page(), {}, False)
    with pytest.raises(forms.Gate, match='changed'):
        forms.check_form(page(target(value='Changed', has_value=True)),
                         {'#field': 'Approved'}, True)
    with pytest.raises(forms.Gate, match='Decline'):
        forms.check_form(page(target(label='Gender', value='Male', has_value=True)), {}, True)


def test_success_requires_new_page_evidence_and_no_remaining_submit_control():
    before = page(text='Fill the application')
    success = page(text='Thank you for applying')
    assert forms.confirmation(before, success)
    assert not forms.confirmation(success, success)
    assert not forms.confirmation(before, page(target(label='Submit', submit=True),
                                             text='Thank you for applying'))
    assert not forms.confirmation(before, page(text='The site can’t be reached'))


def test_stale_resume_and_duplicates_are_rechecked_before_submit(monkeypatch, tmp_path):
    pdf = tmp_path / 'Resume.pdf'
    pdf.write_bytes(b'pdf')
    row = {'status': 'submitting', 'resume_tex_key': 'r.tex', 'resume_seed': 'old'}
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    monkeypatch.setattr('agent.run.seed_fingerprint', lambda: 'current')
    monkeypatch.setattr('tools.browser_apply._duplicate_refusal', lambda pk: None)
    with pytest.raises(forms.Gate, match='base changed'):
        forms.check_dispatch('Company#1', str(pdf))
    row['resume_seed'] = 'current'
    forms.check_dispatch('Company#1', str(pdf))
    monkeypatch.setattr('tools.browser_apply._duplicate_refusal', lambda pk: {'status': 'failed'})
    with pytest.raises(forms.Gate, match='duplicate'):
        forms.check_dispatch('Company#1', str(pdf))


@pytest.mark.parametrize(('verb', 'button'),
                         [('submit', 'Submit application'), ('click', 'Continue')])
async def test_submit_ipc_failure_is_terminal_uncertain_not_infrastructure_retry(
        monkeypatch, verb, button):
    submit = target(selector='#submit', tag='button', type='submit',
                    label=button, submit=True)
    resume = target(selector='#resume', label='Resume', type='file', files=['Resume.pdf'])
    current = page(resume, submit)
    pk = 'example-co#job-1'
    evidence = []

    class Tracking:
        def __init__(self):
            self.row = {'pk': pk, 'status': 'submitting'}

        def set_status(self, observed_pk, status, **attrs):
            assert observed_pk == pk
            evidence.append('write')
            self.row.update(status=getattr(status, 'value', status), **attrs)

        def get(self, observed_pk):
            assert observed_pk == pk
            evidence.append('readback')
            return dict(self.row)

    tracking = Tracking()
    monkeypatch.setattr('core.stores.make_stores', lambda *a, **kw:
                        SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(forms, '_row', tracking.get)

    class FakeSession:
        def __init__(self, kind):
            self.kind = kind

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def navigate(self, url):
            return current

        async def page(self):
            return current

        async def call(self, *args, **kwargs):
            if args[0] == 'upload':
                return {}
            if args[0] == 'click':
                assert tracking.row['possible_submission'] is True
                assert tracking.row['status'] == 'needs_human'
                assert tracking.row['gate_reason'] == 'submit_uncertain'
                assert tracking.row['fail_kind'] == 'uncertain'
                assert tracking.row['last_button'] == button
                assert tracking.row['last_url'] == current['url']
                assert evidence[-2:] == ['write', 'readback'] if verb == 'submit' else (
                    evidence[-3:] == ['write', 'readback', 'readback'])
                evidence.append('click')
                raise bsk.Unavailable('BrowserSkill unavailable: offline')
            pytest.fail('Unexpected browser IPC')

    monkeypatch.setattr(bsk, 'Session', FakeSession)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *args: None)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: 'Resume.pdf')
    monkeypatch.setattr(bsk, 'decision', AsyncMock(side_effect=[
        {'action': 'upload', 'selector': '#resume'},
        {'action': verb, 'selector': '#submit'},
    ]))
    result = await forms.apply('https://employer.test/job/1', 'example-co', {},
                               'chatgpt/model', pk=pk, resume_path='Resume.pdf')
    assert result['status'] == 'uncertain'
    assert not runtime.is_disconnected(result['detail'])
    assert not runtime.is_infrastructure(result['detail'])
    assert result['step'] == {'last_button': button, 'url': current['url']}
    assert button in result['detail'] and current['url'] in result['detail']
    assert 'click' in evidence
    assert evidence.index('write') < evidence.index('click')
    assert evidence.index('readback', evidence.index('write')) < evidence.index('click')


async def test_discovery_admits_only_links_seen_in_browser(monkeypatch):
    link = target(selector='#job', tag='a', type='a', href='https://employer.test/job/1')
    current = page(link)
    session = SimpleNamespace(navigate=AsyncMock(return_value=current))
    monkeypatch.setattr('discovery.progress.cancelled', lambda: False)
    monkeypatch.setattr(bsk, 'decision', AsyncMock(return_value={'action': 'finish', 'report': {
        'jobs': [{'url': link['href'], 'title': 'Real'},
                 {'url': 'https://invented.test/job/2', 'title': 'Invented'}]}}))
    result = await bsk._crawl(session, 'Find postings', 'https://employer.test/careers')
    assert result['jobs'] == [{'url': link['href'], 'title': 'Real'}]


def test_startup_reuses_shared_daemon_and_never_restarts_it(monkeypatch):
    monkeypatch.setattr(bsk, '_sync', lambda *args: {'ok': True})
    monkeypatch.setattr(bsk.subprocess, 'Popen', lambda *args, **kw: pytest.fail('Shared daemon'))
    assert bsk.ensure_daemon() == (True, '')


async def test_typed_posting_url_is_not_reparsed_with_prompt_punctuation(monkeypatch):
    url = 'https://employer.test/job/R123?value=a,b'
    seen = []
    class Reader:
        def __init__(self, kind):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, value):
            seen.append(value)
            return page()
    monkeypatch.setattr(bsk, 'Session', Reader)
    report, problem = await bsk.run_task(f'Open {url}, wait for it to load.',
                                         report_key='description', urls=[url])
    assert not problem and report['description'] == GOOD.strip()
    assert seen == [url]


async def test_untyped_role_button_cannot_bypass_self_id_guards():
    session = SimpleNamespace(call=AsyncMock())
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, page(target(type='button', tag='div', label='Male', in_form=False)),
            {'action': 'click', 'selector': '#field'}, facts={}, filled={},
            resume_path='', company='Company', jd_text='', resume_tex='', github='')
    session.call.assert_not_awaited()


async def test_required_acknowledgement_does_not_authorize_a_citizenship_fact():
    session = SimpleNamespace(call=AsyncMock())
    control = target(type='checkbox', label='I certify I am a US citizen', required=True)
    with pytest.raises(forms.Gate, match='approved answer'):
        await forms.execute(session, page(control),
                            {'action': 'choose', 'selector': '#field'}, facts={}, filled={},
                            resume_path='', company='Company', jd_text='',
                            resume_tex='', github='')
    session.call.assert_not_awaited()


def test_removed_attachment_and_preselected_answer_cannot_pass_submit_checks():
    with pytest.raises(forms.Gate, match='no longer present'):
        forms.check_form(page(), {}, True, 'Resume.pdf')
    control = target(type='radio', label='Yes', question='Authorized to work?',
                     checked=True, name='work_auth')
    with pytest.raises(forms.Gate, match='preselected'):
        forms.check_form(page(control), {}, True)
    forms.check_form(page(control), {'#field': 'Yes'}, True)


def test_native_select_can_derive_the_option_without_a_model_guessed_value():
    control = target(tag='select', label='Gender',
                     options=[{'value': '1', 'label': 'Male'},
                              {'value': '2', 'label': 'Prefer not to say'}])
    assert forms.choose_value(control, 'Prefer not to say', '') == '2'


async def test_workday_shell_is_not_a_job_description():
    url = 'https://tenant.wd1.myworkdayjobs.com/careers/job/Remote/Lead_R1'
    shell = {**page(text='Cookies and navigation. ' * 30), 'description': ''}
    session = SimpleNamespace(navigate=AsyncMock(return_value=shell))
    with pytest.raises(bsk.Unavailable, match='container is empty'):
        await bsk._read(session, url)


async def test_invalid_legacy_url_does_not_open_an_agent_window(monkeypatch):
    monkeypatch.setattr(bsk, 'Session', lambda *a: pytest.fail('Invalid URLs stay outside Chrome'))
    report, problem = await bsk.run_task('Open /jobs/1', report_key='description', urls=['/jobs/1'])
    assert not report and 'HTTP(S)' in problem


async def test_manual_confirmation_during_fill_keeps_the_applied_ledger(monkeypatch):
    import fakeredis

    from agent import run
    from core.models import Status
    from core.storage.local import RedisTracking

    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = 'acme#1'
    tracking.set_status(pk, Status.TAILORED, company='Acme',
                        jd_url='https://employer.test/job/1', gate_reason='approval')
    stores = SimpleNamespace(tracking=tracking, secrets=None,
                             answer_bank=SimpleNamespace(all_facts=lambda company: {}))
    monkeypatch.setattr(run, '_jd_text', AsyncMock(return_value=GOOD))
    monkeypatch.setattr(run, '_github_context', lambda: '')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda row: '')
    monkeypatch.setattr('tools.credentials.get_login', lambda *args: None)
    monkeypatch.setattr('core.rotation.ensure', lambda *args: None)
    monkeypatch.setattr('core.profiles.resolve_for', lambda row: None)

    async def manual_confirmation(*args, **kwargs):
        # The queue dispatcher consumed the approval marker before handing off.
        assert tracking.get(pk)['gate_reason'] == ''
        tracking.set_status(pk, Status.APPLIED_MANUAL, confirmation_id='owner-confirmed')
        return {'status': 'gate', 'question': 'Already recorded during this browser session.'}
    monkeypatch.setattr('tools.browser_apply.apply', manual_confirmation)
    result = await run._apply_direct(pk, stores)
    assert result['reason'] == 'already_applied'
    assert tracking.get(pk)['status'] == 'applied_manual'
    assert tracking.get(pk)['confirmation_id'] == 'owner-confirmed'


def test_background_prepare_failure_does_not_leave_an_unclaimed_working_card(monkeypatch):
    import fakeredis
    from fastapi import BackgroundTasks

    import server
    from core.models import Status
    from core.storage.local import RedisTracking

    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    tracking.set_status('acme#1', Status.FOUND, company='Acme')
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, 'make_stores', lambda *args: stores)
    def bad_score(pk, stores, *, prepare_only):
        assert prepare_only
        tracking.set_status(pk, Status.TAILORING)
        raise ValueError('Malformed structured score')
    monkeypatch.setattr('agent.run.run_job', bad_score)
    endpoints = {r.path: r.endpoint for r in server.create_app().routes
                 if hasattr(r, 'endpoint')}
    background = BackgroundTasks()
    endpoints['/actions/run-job/{pk}']('acme#1', background)
    background.tasks[0].func()
    row = tracking.get('acme#1')
    assert row['status'] == 'error'
    assert row['error'] == 'Preparation failed: ValueError'


@pytest.mark.parametrize('destination', [
    'https://employer.test/job/1',
    'https://employer.test/job/1/apply',
    'https://employer.test/job/1/confirmation',
])
def test_navigation_stays_within_explicit_job_route(destination):
    assert forms.navigation_allowed(destination, 'https://employer.test/job/1')


@pytest.mark.parametrize('destination', [
    'https://employer.test/job/2', 'https://employer.test/next',
    'https://job-boards.greenhouse.io/other/jobs/1',
    'https://jobs.lever.co/other/job/2',
    'https://tenant.wd1.myworkdayjobs.com/other/job/2',
    'https://jobs.smartrecruiters.com/other/1',
])
def test_host_and_ats_suffix_never_authorize_an_unrelated_job(destination):
    assert not forms.navigation_allowed(destination, 'https://employer.test/job/1')


@pytest.mark.parametrize('destination', [
    'https://external.test/next', 'https://greenhouse.io.external.test/job/1',
    'https://notmyworkdayjobs.com/job/1', 'https://ashbyhq.com@external.test/job/1',
    'javascript:alert(1)',
 ])
def test_lookalike_and_unrelated_hosts_are_not_navigation_permission(destination):
    assert not forms.navigation_allowed(destination, 'https://employer.test/job/1')


def test_direct_board_host_requires_a_job_route_not_domain_wide_access():
    direct = 'https://eeho.fa.us2.oraclecloud.com/tenant/jobs/role-1'
    assert forms.navigation_allowed(direct + '/apply',
                                    'https://careers.oracle.com/job/1', direct)
    assert not forms.navigation_allowed(
        'https://eeho.fa.us2.oraclecloud.com/tenant/jobs/role-2',
        'https://careers.oracle.com/job/1', direct)
    assert not forms.navigation_allowed(
        'https://another.oraclecloud.com/tenant/jobs/role-1',
        'https://careers.oracle.com/job/1', direct)


@pytest.mark.parametrize('verb', ['navigate', 'click'])
async def test_application_gates_external_link_before_the_browser_follows_it(monkeypatch, verb):
    outside = 'https://external.test/next'
    current = page(target(selector='#outside', tag='a', type='a', in_form=False,
                          label='View details', href=outside))
    calls = []
    class FakeSession:
        def __init__(self, kind):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, url):
            calls.append(url)
            return current
        async def page(self):
            return current
        async def call(self, *args, **kwargs):
            pytest.fail('A forbidden link must never reach browser click')
    monkeypatch.setattr(bsk, 'Session', FakeSession)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *args: None)
    monkeypatch.setattr(forms, '_row', lambda pk: {})
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda *args: 'Resume.pdf')
    monkeypatch.setattr(bsk, 'decision', AsyncMock(return_value={
        'action': verb, 'selector': '#outside' if verb == 'click' else '', 'url': outside}))
    result = await forms.apply(current['url'], 'Company', {}, 'chatgpt/model',
                               pk='Company#1', resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert 'tracked job and tenant' in result['question']
    assert result['status'] == 'gate'
    assert result['step']['url'] == current['url']
    assert calls == [current['url']]


def test_handoff_preserves_step_but_never_reproduces_oauth_tickets():
    result = forms.handoff('Check the portal.', {
        'last_button': 'Continue',
        'url': 'https://employer.test/review?job=123&access_token=SECRET#id_token=SECRET'})
    assert result['step'] == {'last_button': 'Continue',
                              'url': 'https://employer.test/review?job=123'}
    assert 'SECRET' not in result['text']


async def test_exact_approved_textarea_answer_is_not_replaced_with_a_generated_essay(monkeypatch):
    session = SimpleNamespace(call=AsyncMock())
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting',
        'human_approved_answers': {'Motivation': 'Approved explanation'}})
    monkeypatch.setattr('tools.narrative.draft_answer',
                        lambda *a, **kw: pytest.fail('An exact approved answer already exists'))
    await forms.execute(
        session, page(target(tag='textarea', type='textarea', label='Motivation')),
        {'action': 'fill', 'selector': '#field', 'fact': '', 'essay': True},
        facts={'Motivation': 'Approved explanation'}, filled={}, resume_path='',
        company='Company', jd_text='', resume_tex='', github='', pk='Company#1')
    session.call.assert_awaited_once_with('fill', '#field', '--value', 'Approved explanation')


async def test_file_url_permission_error_is_a_human_gate_not_an_upload_retry():
    raw = json.dumps({'code': 'cdp_failed', 'message': 'PRIVATE CONTENT', 'exit_code': 3,
                      'hint': "check Chrome's Allow access to file URLs permission",
                      'data': {'effect_state': 'unknown'}})
    with pytest.raises(bsk.Unavailable) as caught:
        bsk._result(raw, 3)
    error = caught.value
    assert error.file_access_required and 'PRIVATE CONTENT' not in str(error)
    session = SimpleNamespace(call=AsyncMock(side_effect=error))
    with pytest.raises(forms.Gate, match='Allow access to file URLs'):
        await forms.execute(
            session, page(target(type='file', label='Resume')),
            {'action': 'upload', 'selector': '#field'}, facts={}, filled={},
            resume_path='Resume.pdf', company='Company', jd_text='', resume_tex='', github='')
    assert session.call.await_count == 1


async def test_unknown_upload_effect_never_triggers_an_automatic_repeat():
    error = bsk.Unavailable('BrowserSkill operation unavailable: transport')
    error.effect_state = 'unknown'
    session = SimpleNamespace(call=AsyncMock(side_effect=error))
    with pytest.raises(forms.Gate, match='No automatic repeat'):
        await forms.execute(
            session, page(target(type='file', label='Resume')),
            {'action': 'upload', 'selector': '#field'}, facts={}, filled={},
            resume_path='Resume.pdf', company='Company', jd_text='', resume_tex='', github='')
    assert session.call.await_count == 1


def test_verified_file_input_is_not_mistaken_for_unapproved_text():
    resume = target(type='file', label='Resume', required=True, has_value=True,
                    value='C:\\fakepath\\Resume.pdf', files=['Resume.pdf'])
    forms.check_form(page(resume), {}, True, 'Resume.pdf')
    resume['files'] = []
    with pytest.raises(forms.Gate, match='no longer present'):
        forms.check_form(page(resume), {}, True, 'Resume.pdf')


@pytest.mark.parametrize(('field', 'action', 'facts'), [
    (target(label='Years of experience'), {'action': 'fill', 'selector': '#field',
                                            'fact': 'Years'}, {'Years': '10 years'}),
    (target(tag='textarea', type='textarea', label='Why this role?'),
     {'action': 'fill', 'selector': '#field', 'essay': True}, {}),
    (target(tag='textarea', type='textarea', label='Why this role?'),
     {'action': 'fill', 'selector': '#field', 'fact': 'Motivation'},
     {'Motivation': 'Invented but plausible narrative'}),
    (target(type='checkbox', label='I consent to share my personal data', required=True),
     {'action': 'choose', 'selector': '#field'}, {}),
    (target(type='checkbox', label='I accept marketing messages', required=True),
     {'action': 'choose', 'selector': '#field'}, {}),
])
async def test_unapproved_fact_essay_and_consequential_consent_have_zero_writes(
        monkeypatch, field, action, facts):
    monkeypatch.setattr(forms, '_row', lambda pk: {
        'pk': pk, 'status': 'submitting', 'human_approved_answers': {}})
    session = SimpleNamespace(call=AsyncMock())
    with pytest.raises(forms.Gate, match='human-approved'):
        await forms.execute(
            session, page(field), action, facts=facts, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='', pk='example-co#1')
    session.call.assert_not_awaited()


def test_opaque_and_hidden_form_values_block_final_click():
    attachment = target(selector='#resume', type='file', label='Resume',
                        files=['Resume.pdf'])
    with pytest.raises(forms.Gate, match='uninspectable'):
        forms.check_form({**page(attachment), 'opaque_controls': True}, {}, True, 'Resume.pdf')
    with pytest.raises(forms.Gate, match='incomplete'):
        forms.check_form({**page(attachment), 'inventory_verified': False},
                         {}, True, 'Resume.pdf')
    with pytest.raises(forms.Gate, match='hidden'):
        forms.check_form(page(attachment, target(selector='#private', type='hidden',
                                                value='unverified', has_value=True)),
                         {}, True, 'Resume.pdf')


def test_stale_page_text_and_fakepath_never_prove_uploaded_pdf():
    attachment = target(selector='#resume', type='file', label='Resume',
                        value='C:\\fakepath\\Resume.pdf', has_value=True, files=[])
    with pytest.raises(forms.Gate, match='no longer present'):
        forms.check_form(page(attachment, text='Resume.pdf upload completed'),
                         {}, True, 'Resume.pdf')


@pytest.mark.parametrize('error', [
    TimeoutError('synthetic upload timeout'),
    bsk.Unavailable('BrowserSkill operation unavailable: synthetic transport'),
])
async def test_unknown_or_timeout_upload_is_human_inspection_not_requeue(error):
    session = SimpleNamespace(call=AsyncMock(side_effect=error))
    with pytest.raises(forms.Gate, match='No automatic repeat'):
        await forms.execute(
            session, page(target(type='file', label='Resume')),
            {'action': 'upload', 'selector': '#field'}, facts={}, filled={},
            resume_path='Resume.pdf', company='example-co', jd_text='',
            resume_tex='', github='')
    session.call.assert_awaited_once()


async def test_unclassified_js_proceed_or_one_click_apply_never_reaches_ipc():
    for wording in ['View', 'Proceed', 'Apply now', 'Edit']:
        session = SimpleNamespace(call=AsyncMock())
        button = target(tag='button', type='button', label=wording)
        with pytest.raises(forms.Gate):
            await forms.execute(
                session, page(button), {'action': 'click', 'selector': '#field'},
                facts={}, filled={}, resume_path='', company='example-co',
                jd_text='', resume_tex='', github='', pk='example-co#1')
        session.call.assert_not_awaited()


def test_job_id_query_cannot_change_on_same_ats_host():
    origin = 'https://tenant.ats.test/company/apply?job_id=one'
    assert forms.navigation_allowed(
        'https://tenant.ats.test/company/apply?job_id=one', origin)
    assert not forms.navigation_allowed(
        'https://tenant.ats.test/company/apply?job_id=two', origin)


@pytest.mark.parametrize('outcome', ['submitted', 'not_submitted'])
def test_held_outcome_requires_explicit_portal_verification_and_never_autorequeues(
        monkeypatch, outcome):
    import fakeredis
    import server
    from core.models import Status
    from core.storage.local import RedisTracking
    from core.apply_queue import ApplyQueue

    redis = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(redis)
    pk = 'example-co#synthetic-role'
    tracking.set_status(pk, Status.NEEDS_HUMAN, company='example-co',
                        possible_submission=True, gate_reason='submit_uncertain',
                        fail_kind='uncertain', last_button='Continue',
                        last_url='https://example.test/job/1')
    stores = SimpleNamespace(tracking=tracking)
    monkeypatch.setattr(server, 'make_stores', lambda *args: stores)
    monkeypatch.setattr('tools.browser_skill.applies_running', lambda: 0)
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if r.path == '/actions/resolve-uncertain/{pk:path}')

    assert not endpoint(pk, {'outcome': outcome})['ok']
    assert not endpoint(pk, {'portal_checked': True,
                             'outcome': outcome})['ok']
    assert tracking.get(pk)['possible_submission']
    assert not ApplyQueue(redis).pending()

    if outcome == 'submitted':
        result = endpoint(pk, {'portal_checked': True, 'outcome': outcome,
                               'confirmation': 'Synthetic portal confirmation'})
        assert result == {'ok': True, 'status': 'applied_manual'}
        assert tracking.get(pk)['possible_submission'] is True
        assert tracking.get(pk)['confirmation_id'] == 'Synthetic portal confirmation'
    else:
        result = endpoint(pk, {'portal_checked': True, 'outcome': outcome,
                               'new_apply_decision': True})
        assert result == {'ok': True, 'status': 'tailored', 'queued': False}
        assert tracking.get(pk)['possible_submission'] is False
        assert tracking.get(pk)['gate_reason'] == 'approval'
    assert not ApplyQueue(redis).pending()


def test_destructive_reset_refuses_existing_hold_before_any_live_side_effect(monkeypatch):
    import fakeredis
    import server
    from core.models import Status
    from core.storage.local import RedisTracking

    client = fakeredis.FakeRedis(decode_responses=True)
    tracking = RedisTracking(client)
    tracking.set_status('example-co#hold', Status.NEEDS_HUMAN,
                        possible_submission=True, fail_kind='uncertain')
    monkeypatch.setattr(server, 'make_stores',
                        lambda *args: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr('tools.browser_skill.applies_running', lambda: 0)
    monkeypatch.setattr('tools.browser_runtime.kill_live_sessions',
                        lambda: pytest.fail('Reset must refuse before browser effects'))
    endpoint = next(r.endpoint for r in server.create_app().routes
                    if r.path == '/actions/reset')
    refusal = endpoint()
    assert not refusal['ok']
    assert tracking.get('example-co#hold')['possible_submission']


def test_existing_tracking_full_row_writer_can_erase_a_read_back_hold():
    """Known, uncorrected architect blocker: tracking full-row SET is not CAS.

    This deliberately demonstrates the failure, NOT a concurrency-safety PASS.
    The storage implementation is out of this Issue's approved source scope.
    """
    import fakeredis
    from core.models import Status
    from core.storage.local import RedisTracking

    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    pk = 'example-co#interleaved-stale-write'
    tracking.set_status(pk, Status.SUBMITTING, company='example-co')
    stale_pre_hold_row = tracking.get(pk)  # writer A has already read
    tracking.set_status(pk, Status.NEEDS_HUMAN, possible_submission=True,
                        fail_kind='uncertain', gate_reason='submit_uncertain')
    assert tracking.get(pk)['possible_submission'] is True  # writer B read-back

    tracking._write(pk, stale_pre_hold_row, prev_status='submitting')
    # Exact real storage method unconditionally writes the stale full row.
    assert not tracking.get(pk).get('possible_submission')
    assert tracking.get(pk)['status'] == 'submitting'
