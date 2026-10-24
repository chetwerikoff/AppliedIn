"""Canonical ATS entry uses independent persisted authority, never page hints.

All records, DOM and sessions are synthetic. The fixed loopback fixture cannot
prove HTTPS ATS routing, so both vendor shapes are exercised offline here.
"""
from copy import deepcopy
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from core.ids import make_pk
from discovery.watchlist import CompanyConfig
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import field, submit, inventory, witness


@pytest.fixture(autouse=True)
def _arch1332_application_timing(request):
    """Measure each of the five exact ARCH13:32 cases in full CI, including xdist."""
    name = getattr(request.node, 'originalname', None) or request.node.name.split('[')[0]
    params = request.node.callspec.params if hasattr(request.node, 'callspec') else {}
    targeted = (
        (name == 'test_current_identity_rechecked_after_read_and_on_resume'
         and params.get('drift') == 'route')
        or name == 'test_native_receiver_visibility_must_agree_with_page_and_fixed_witnesses'
    )
    if not targeted:
        yield
        return
    started = time.perf_counter()
    yield
    elapsed = time.perf_counter() - started
    assert elapsed <= 5.0, f'ARCH13:32 {request.node.nodeid}: {elapsed:.3f}s exceeds 5s'
    request.node.warn(pytest.PytestWarning(
        f'ARCH13:32 measured {request.node.nodeid} = {elapsed:.3f}s (<=5s)'))

COMPANY = 'example-co'
JOB = '12345678-1234-4234-8234-123456789abc'
PK = make_pk(COMPANY, JOB)


def posting(ats):
    return f'https://{forms._APPLICATION_ENTRY[ats][0]}/example-co/{JOB}'


def entry(ats):
    return posting(ats) + '/' + forms._APPLICATION_ENTRY[ats][1]


def application(ats, **extra):
    # Distinct representative native Ashby and Lever control ownership shapes.
    owner = '#application-form' if ats == 'ashby' else '#application-form-lever'
    answer = field(selector='#email' if ats == 'ashby' else '#email-input',
                   form_selector=owner, form_action=entry(ats))
    button = submit(form_selector=owner, form_action=entry(ats))
    page = inventory(answer, button, url=entry(ats))
    page['text'] = 'Synthetic example-co.test application'
    return {**page, **extra}


def synthetic_ashby_entry_shape(page):
    """Compatibility for old hand-built control tests; real-reader cases use dom()."""
    entry_snapshot = page.get('entry')
    if not isinstance(entry_snapshot, dict) or page.get('url') != entry('ashby'):
        return
    controls = page.get('controls') or []
    owner = (entry_snapshot.get('root') or {}).get('selector')
    if not owner:
        entry_snapshot['native_candidates'] = []
        return
    siblings = [c for c in controls if c.get('form_selector') == owner]
    for c in siblings:
        c.update(form_action_attr_present=True, form_action_raw=entry('ashby'),
                 formaction_attr_present=False, formaction_raw=None)
    entry_snapshot['native_candidates'] = [{
        'selector': owner, 'plausible': True, 'action': entry('ashby'),
        'action_attr_present': True, 'action_attr': entry('ashby'),
        'controls': [{
            'selector': c['selector'], 'tag': c['tag'], 'type': c['type'],
            'submit': bool(c.get('submit')), 'caption': c.get('label', ''),
            'owned': True, 'native_owner': owner, 'declared_owner': None,
            'label_count': 1 if not c.get('submit') else 0,
            'labels_owned': True, 'disabled': c.get('disabled', False),
            'required': c.get('required', False), 'visible': True,
            'readonly': False, 'override_present': False,
            'override_raw': None, 'override_resolved': '',
        } for c in siblings],
    }]


class Session:
    def __init__(self, pages):
        self.snapshots = iter(pages)
        self.current = None
        self.calls = []
        self.pages = 0
        self.on_page = None
        self.on_evaluate = None
        self.after_submit = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def navigate(self, url):
        # When a configured ATS pin is missing, a tracked literal posting may
        # still be read for host-only preparation. No CLI or real page is used.
        self.calls.append(('navigate', url))
        return await self.page()

    async def page(self):
        self.pages += 1
        self.current = deepcopy(next(self.snapshots, self.current))
        if self.on_page:
            await self.on_page(self)
        return deepcopy(self.current)

    async def call(self, method, *args):
        self.calls.append((method, *args))
        if method == 'navigate':
            return {}
        if method == 'evaluate':
            if args == (forms.ENTRY_PAGE,):
                value = await self.page()
                if isinstance(value, dict) and 'entry' not in value:
                    controls = value.get('controls') or []
                    # Synthetic readers use the current native Submit
                    # classification, never a retired per-step marker helper.
                    owners = {c.get('form_selector') for c in controls
                              if isinstance(c, dict) and forms.native_submit(c)
                              and any(forms._same_key(text) in forms._APP_SUBMIT_LABELS
                                      for text in forms._submitter_texts(c))}
                    owner = next(iter(owners)) if len(owners) == 1 else None
                    value['entry'] = dict(frames=[], responses=[], response_error=False,
                                          helper_challenge=False, root=(
                        dict(selector=owner, action=value['url']) if owner else None))
                    self.current = deepcopy(value)
                if isinstance(value, dict) and value.get('url') == entry('ashby'):
                    synthetic_ashby_entry_shape(value)
                    self.current = deepcopy(value)
                return {'value': value}
            assert args in {(forms._ENTRY_FIXED_CONTROL_WITNESS,), (forms._FIXED_CONTROL_WITNESS,)}
            if self.on_evaluate:
                await self.on_evaluate(self)
            value = witness(self.current)
            if self.current.get('url') == entry('ashby'):
                for item, source in zip(value['controls'], self.current['controls']):
                    for key in ('form_action_attr_present', 'form_action_raw',
                                'formaction_attr_present', 'formaction_raw',
                                'label', 'submit', 'required'):
                        if key in source:
                            item[key] = deepcopy(source[key])
            if 'entry' in self.current:
                value['entry'] = deepcopy(self.current['entry'])
            return {'value': value}
        if method == 'click' and self.after_submit is not None:
            self.current = deepcopy(self.after_submit)
            return {}
        pytest.fail(f'Unexpected mutation: {method}')


def world(monkeypatch, ats='ashby', pages=None):
    row = dict(pk=PK, company=COMPANY, ats=ats, job_id=JOB,
               jd_url=posting(ats), status='submitting', human_approved_answers={})
    configs = [CompanyConfig(name=COMPANY, ats=ats, board='example-co')]
    monkeypatch.setattr(forms, '_row', lambda *a, **kw: row)
    monkeypatch.setattr('discovery.watchlist.load_watchlist', lambda path: configs)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *a: '')
    session = Session(pages or [application(ats)])
    monkeypatch.setattr(bsk, 'Session', lambda *a: session)
    decision = AsyncMock(return_value={'action': 'finish'})
    monkeypatch.setattr(bsk, 'decision', decision)
    return row, configs, session, decision


async def attempt(ats='ashby', **kwargs):
    return await forms.apply(posting(ats), COMPANY, {}, 'synthetic',
                             pk=PK, resume_path='Resume.pdf', **kwargs)


def no_mutations(session):
    assert all(c[0] in {'navigate', 'evaluate'} for c in session.calls)


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
async def test_direct_entry_two_fresh_forms_before_bootstrap(monkeypatch, ats):
    _, _, session, decision = world(monkeypatch, ats)
    bootstrap = AsyncMock(side_effect=forms.Gate('ready for human review'))
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    result = await attempt(ats)
    assert result['status'] == 'gate'
    assert 'ready for human review' in result['question']
    assert session.calls[0] == ('navigate', entry(ats))
    assert session.pages == 4  # two fresh PAGEs and their two witness rechecks
    assert bootstrap.await_count == 1
    assert not decision.await_count
    no_mutations(session)


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
async def test_direct_entry_gate_keeps_existing_question_payload(monkeypatch, ats):
    _, _, session, decision = world(monkeypatch, ats)
    question = {'label': 'Synthetic question', 'type': 'text'}
    rejection = forms.Gate('Exact synthetic owner question', form_question=question,
                           reason='owner_question')
    bootstrap = AsyncMock(side_effect=rejection)
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    report = await attempt(ats)
    assert report['status'] == 'gate'
    assert report['reason'] == 'owner_question'
    assert report['review_route'] == 'owner'
    assert report['question'].startswith(str(rejection))
    assert report['form_question'] is question
    assert bootstrap.await_count == 1 and not decision.await_count
    no_mutations(session)


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
async def test_direct_entry_unexpected_exception_stays_unknown(monkeypatch, ats):
    _, _, session, decision = world(monkeypatch, ats)
    bootstrap = AsyncMock(side_effect=RuntimeError('Synthetic unexpected fault'))
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    report = await attempt(ats)
    assert report == {'status': 'unknown',
                      'detail': 'BrowserSkill operation unavailable: RuntimeError'}
    assert 'question' not in report and 'form_question' not in report
    assert bootstrap.await_count == 1 and not decision.await_count
    no_mutations(session)


async def test_final_submit_gate_keeps_existing_question_without_click(
        monkeypatch, tmp_path):
    from tests.tools.test_browser_controller_support import install_controller_session
    row, _, endpoint, decide = world(monkeypatch, 'lever')
    _, _, pdf = install_controller_session(monkeypatch, row, endpoint, tmp_path)
    monkeypatch.setattr(forms, '_bootstrap',
                        AsyncMock(side_effect=lambda _session, current, *a, **k: current))
    decide.return_value = {'action': 'submit', 'selector': '#submit'}
    report = await forms.apply(posting('lever'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert report['status'] == 'gate'
    assert report['question'].startswith(
        'This row’s protected tailored PDF has not been verified.')
    assert report['form_question'] is None
    assert not any(call[0] == 'click' for call in endpoint.calls)
    assert not row.get('possible_submission')


@pytest.mark.parametrize('problem', [
    'missing-config', 'empty-board', 'empty-ats', 'foreign-company', 'wrong-ats',
    'foreign-board', 'duplicate', 'normalized-duplicate', 'unreadable',
    'dot', 'slash', 'underscore', 'encoded', 'url-board', 'long-board', 'leading-dash',
    'case-board', 'row-pk', 'row-company', 'row-job', 'row-url', 'noncanonical-uuid',
])
async def test_invalid_current_authority_gates_before_navigation(monkeypatch, problem):
    row, configs, session, decision = world(monkeypatch)
    if problem == 'missing-config':
        configs.clear()
    elif problem == 'unreadable':
        def unavailable(path):
            raise OSError('synthetic unavailable watchlist')
        monkeypatch.setattr('discovery.watchlist.load_watchlist', unavailable)
    elif problem in {'duplicate', 'normalized-duplicate'}:
        configs.append(configs[0].model_copy(update={
            'name': ' EXAMPLE-CO ' if problem == 'normalized-duplicate' else COMPANY}))
    elif problem.startswith('row-'):
        row[{'row-pk': 'pk', 'row-company': 'company', 'row-job': 'job_id',
             'row-url': 'jd_url'}[problem]] = 'foreign'
    elif problem == 'noncanonical-uuid':
        row['job_id'] = JOB.upper()
    else:
        key, value = {
            'empty-board': ('board', ''), 'empty-ats': ('ats', ''),
            'foreign-company': ('name', 'other-co'), 'wrong-ats': ('ats', 'lever'),
            'foreign-board': ('board', 'other-co'), 'dot': ('board', 'example-co.test'),
            'slash': ('board', 'example/co'), 'underscore': ('board', 'example_co'),
            'encoded': ('board', 'example%2dco'), 'url-board': ('board', posting('ashby')),
            'long-board': ('board', 'a' * 64), 'leading-dash': ('board', '-example'),
            'case-board': ('board', 'Example-Co'),
        }[problem]
        setattr(configs[0], key, value)
    result = await attempt()
    assert result['status'] == 'gate'
    tracked_mismatch = problem in {
        'row-pk', 'row-company', 'row-job', 'row-url', 'noncanonical-uuid'}
    if tracked_mismatch:
        # An incoherent canonical row must refuse before even a reversible
        # navigation. Board/watchlist mismatch is not the same authority.
        assert 'tracked' in result['question'].lower()
        assert session.calls == []
    else:
        # ARCH 21:58: missing/foreign board configuration cannot resurrect
        # a pre-browser tenant gate. The ONE final check still denies Submit.
        assert ('navigate', posting('ashby')) in session.calls
        assert not any(c[0] in {'fill', 'select', 'upload', 'click'}
                       for c in session.calls)
        guard = forms.ApplicationIPCController(
            PK, COMPANY, posting('ashby'), '', {}, {}, [], {}, lambda: None)
        with pytest.raises(forms.Gate, match='job/ATS tenant or board'):
            await guard.authorize_final_submit(
                session, {'url': entry('ashby'), 'controls': []}, {'label': 'Submit'})
    if tracked_mismatch:
        assert not decision.await_count
    else:
        # Ordinary preparation is allowed despite a broken tenant/board pin;
        # the actual final guard above still refuses before one raw Submit.
        assert decision.await_count == 1
        assert result['form_question'] is None


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('tail', ['?x=1', '?', '#', '#fragment', '/', '/application', '%2f'])
async def test_nonliteral_posting_has_no_entry_authority(monkeypatch, ats, tail):
    row, _, session, _ = world(monkeypatch, ats)
    row['jd_url'] = posting(ats) + tail
    result = await forms.apply(row['jd_url'], COMPANY, {}, 'synthetic',
                               pk=PK, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    assert session.calls == []


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('change', [
    lambda u: u.replace('https:', 'http:'),
    lambda u: u.replace('https://', 'https://user@'),
    lambda u: u.replace('/example-co/', ':443/example-co/'),
    lambda u: u.replace('/example-co/', ':/example-co/'),
    lambda u: u.replace('/example-co/', '/example-co.test/'),
    lambda u: u.replace('/example-co/', '/other-co/'),
    lambda u: u.replace('jobs.', 'JOBS.'),
    lambda u: u.replace(JOB, JOB.upper()),
    lambda u: u.replace(JOB, '12345678123442348234123456789abc'),
    lambda u: u.replace('/example-co/', '/example%2dco/'),
    lambda u: u + '\\',
])
async def test_ambiguous_posting_spelling_gates(monkeypatch, ats, change):
    row, _, session, _ = world(monkeypatch, ats)
    row['jd_url'] = change(posting(ats))
    result = await forms.apply(row['jd_url'], COMPANY, {}, 'synthetic',
                               pk=PK, resume_path='Resume.pdf')
    assert result['status'] == 'gate'
    if '/other-co/' in row['jd_url']:
        # ARCH21:58 / AC14: a host-allowlisted foreign tenant may prepare;
        # the configured/current tenant must still refuse at the final guard.
        assert ('navigate', row['jd_url']) in session.calls
        guard = forms.ApplicationIPCController(
            PK, COMPANY, row['jd_url'], '', {}, {}, [], {}, lambda: None)
        with pytest.raises(forms.Gate, match='job/ATS tenant or board'):
            await guard.authorize_final_submit(session, application(ats), {'label': 'Submit'})
        no_mutations(session)
    else:
        assert session.calls == []


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
def test_only_exact_entry_not_page_links_or_origin_descendants(monkeypatch, ats):
    world(monkeypatch, ats)
    proof = forms._configured_alias(PK, COMPANY, posting(ats))
    assert isinstance(proof, forms.ApplicationEntryProvenance)
    assert forms.navigation_allowed(entry(ats), provenance=proof)
    for href in [posting(ats), entry(ats) + '/', entry(ats) + '/success',
                 entry(ats) + '?', entry(ats) + '#', entry(ats) + '?x=1',
                 entry(ats).replace('example-co', 'foreign-co'),
                 entry(ats).replace(JOB, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
                 entry(ats).replace('https://', 'https://evil.test/'),
                 posting(ats) + '/apply' if ats == 'ashby' else posting(ats) + '/application']:
        assert not forms.navigation_allowed(href, entry(ats), provenance=proof), href


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('receiver', ['form_action', 'formaction'])
async def test_foreign_native_receiver_is_not_render_failure(monkeypatch, ats, receiver):
    page = application(ats)
    page['controls'][-1][receiver] = entry(ats) + '/other-receiver'
    _, _, session, decide = world(monkeypatch, ats, [page])
    report = await attempt(ats)
    assert report['status'] == 'gate'
    # The same-host receiver remains observable during preparation. Both ATS
    # paths must refuse the foreign *final* receiver before any Submit IPC.
    assert report.get('form_question') is None
    # A model finish decision is a reversible review outcome, not Submit.
    assert decide.await_count == 1
    guard = forms.ApplicationIPCController(
        PK, COMPANY, posting(ats), '', {}, {}, [], {}, lambda: None)
    with pytest.raises(forms.Gate, match='Final submit receiver'):
        await guard.authorize_final_submit(session, page, page['controls'][-1])
    no_mutations(session)


@pytest.mark.parametrize('shape', ['empty', 'chrome', 'search', 'no-marker', 'readonly'])
async def test_stable_shell_is_not_application_readiness(monkeypatch, shape):
    page = application('ashby')
    if shape == 'empty':
        page['controls'] = []
    elif shape == 'chrome':
        page['controls'] = [field('Application', '#tab', tag='a', type='',
                                  in_form=False, form_selector='', form_action='',
                                  href='https://jobs.ashbyhq.com/foreign/forged/application')]
    if shape in {'search', 'no-marker'}:
        # Real PAGE/fixed readers inventory every native form, even when the
        # application marker is absent; a hand-built control list does not.
        _, session, decide, _ = native_real_world(
            monkeypatch, **({'searchOnly': True} if shape == 'search'
                            else {'marker': 'absent'}))
    else:
        _, _, session, decide = world(monkeypatch, pages=[page])
    if shape == 'readonly':
        original = session.call
        async def call(method, *args):
            result = await original(method, *args)
            if args == (forms._ENTRY_FIXED_CONTROL_WITNESS,):
                result['value']['controls'][0]['readonly'] = True
            return result
        session.call = call
    monkeypatch.setattr(forms, '_APPLICATION_WAIT_SECONDS', 0.02)
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    report = await attempt()
    assert report['status'] == 'gate'
    expected = ('unstable application form inventory' if shape == 'no-marker'
                else 'no qualifying application form')
    assert expected + ' after bounded wait' in report['question']
    assert not decide.await_count
    no_mutations(session)


async def test_shell_then_late_changing_form_needs_own_equal_fresh_pair(monkeypatch):
    shell = application('lever', controls=[])
    first, second = application('lever'), application('lever')
    second['controls'][0]['label'] = 'Email address'
    # Three shell reads, one first pair, and two equal second pairs.
    _, _, session, _ = world(monkeypatch, 'lever',
                            [shell, shell, shell, first, first, second, second, second, second])
    bootstrap = AsyncMock(side_effect=forms.Gate('ready'))
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    report = await attempt('lever')
    assert 'ready' in report['question']
    assert session.pages == 9
    assert bootstrap.call_args.args[1]['controls'][0]['label'] == 'Email address'
    no_mutations(session)


async def test_changing_qualifying_forms_end_in_unstable_gate(monkeypatch):
    _, _, session, _ = world(monkeypatch)
    async def change(s):
        # Keep each PAGE/witness pair coherent but change between probes.
        s.current['controls'][0]['label'] = f'Email {(s.pages - 1) // 2}'
    session.on_page = change
    fake_clock(monkeypatch)
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 1.0)
    report = await attempt()
    assert report['status'] == 'gate'
    assert 'unstable application form inventory after bounded wait' in report['question']
    no_mutations(session)


@pytest.mark.parametrize('ipc', ['page', 'nested-page', 'evaluate', 'failed-page'])
async def test_hung_and_failed_reads_share_single_post_navigation_deadline(monkeypatch, ipc):
    _, _, session, decide = world(monkeypatch)
    async def hang(s):
        if ipc != 'nested-page' or s.pages == 2:
            if ipc == 'failed-page':
                raise OSError('synthetic failed read')
            await asyncio.Event().wait()
    if ipc == 'evaluate':
        session.on_evaluate = hang
    else:
        session.on_page = hang
    monkeypatch.setattr(forms, '_APPLICATION_WAIT_SECONDS', 0.02)
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    report = await asyncio.wait_for(attempt(), timeout=0.3)
    assert report['status'] == 'gate'
    assert 'application form inventory read timed out/unavailable' in report['question']
    assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('unsafe', ['inventory_verified', 'truncated', 'opaque_controls',
                                   'shadow_roots', 'controls'])
async def test_entry_incomplete_waits_but_malformed_is_immediate(monkeypatch, unsafe):
    page = application('ashby')
    page[unsafe] = (False if unsafe == 'inventory_verified' else
                    [None] if unsafe == 'controls' else True)
    _, _, session, decide = world(monkeypatch, pages=[page])
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    report = await attempt()
    assert report['status'] == 'gate'
    if unsafe == 'truncated':
        # ARCH 07:34: bounded inventory incompleteness is not a terminal
        # pre-final gate. Preparation may continue through the raw reader.
        assert 'truncated application inventory after bounded wait' not in report['question']
        assert session.pages >= 2
    else:
        assert 'inventory' in report['question']
        assert ('bounded wait' in report['question']) == (unsafe != 'controls')
        assert session.pages == 1
        assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('drift', ['board', 'ats', 'pk', 'company', 'job_id', 'jd_url', 'route'])
@pytest.mark.parametrize('when', ['read', 'resume'])
async def test_current_identity_rechecked_after_read_and_on_resume(monkeypatch, drift, when):
    row, configs, session, decide = world(monkeypatch)
    if when == 'resume':
        first = await attempt()
        assert first['status'] == 'gate' and first['form_question'] is None
        assert decide.await_count == 1
        # Re-entry retains a real exact approved-control grant, without
        # inventing the removed early per-step human question.
        email = application('ashby')['controls'][0]
        row['human_approved_answers'] = {'Email': {
            'value': 'test@example.test', 'selector': email['selector'],
            'url': entry('ashby'), 'label': email['label'],
            'question': email['question'], 'tag': email['tag'],
            'type': email['type'], 'form_selector': email['form_selector'],
            'form_action': email['form_action']}}
        decide.reset_mock()
        session.calls.clear()
    def change():
        if drift in {'board', 'ats'}:
            setattr(configs[0], drift, 'other')
        elif drift == 'route':
            session.current['url'] = entry('ashby') + '/other'
        else:
            row[drift] = 'other'
    if when == 'read' or drift == 'route':
        async def after(s):
            change()
        session.on_evaluate = after
    else:
        change()
    if drift == 'route':
        # Only the permanently drifted synthetic route needs a short waiter.
        monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    report = await attempt()
    assert report['status'] == 'gate'
    if drift in {'board', 'ats'}:
        # Current tenant pins are *not* a preparation certificate. They may
        # leave an approved field question available, but cannot pass Submit.
        guard = forms.ApplicationIPCController(
            PK, COMPANY, posting('ashby'), '', {}, {}, [], {}, lambda: None)
        with pytest.raises(forms.Gate, match='job/ATS tenant or board'):
            await guard.authorize_final_submit(
                session, {'url': entry('ashby'), 'controls': []}, {'label': 'Submit'})
    else:
        assert report.get('form_question') is None
    if drift in {'board', 'ats'}:
        assert decide.await_count == 1
    else:
        assert not decide.await_count
    no_mutations(session)
    if when == 'resume' and drift not in {'route', 'board', 'ats'}:
        assert session.calls == []


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('redirect', [False, True])
async def test_submit_confirmation_needs_exact_route_and_independent_new_proof(
        monkeypatch, tmp_path, ats, redirect):
    from pathlib import Path
    from tests.tools.test_browser_controller_support import install_controller_session
    initial = application(ats)
    email = initial['controls'][0]
    resume = field('Resume', '#resume', type='file', required=False, files=[],
                   form_selector=email['form_selector'], form_action=entry(ats))
    initial['controls'].insert(1, resume)
    row, _, session, decide = world(monkeypatch, ats, [initial])
    transport, tracking, pdf = install_controller_session(monkeypatch, row, session, tmp_path)
    row['human_approved_answers'] = {'Email': {
        **{k: email[k] for k in ('selector', 'label', 'question', 'tag', 'type',
                                  'form_selector', 'form_action')},
        'url': entry(ats), 'value': 'test@example.test'}}
    original_call = session.call
    async def call(method, *args, **kwargs):
        if method in {'fill', 'upload'}:
            session.calls.append((method, *args))
            target = next(c for c in session.current['controls'] if c['selector'] == args[0])
            if method == 'fill':
                target.update(value=args[2], has_value=True)
            else:
                target['files'] = [Path(args[2]).name]
            return {}
        return await original_call(method, *args)
    session.call = call
    hold = []
    real_hold = forms.hold_possible_submission
    def held(*args, **kwargs):
        hold.append(args)
        real_hold(*args, **kwargs)
    monkeypatch.setattr(forms, 'hold_possible_submission', held)
    decide.side_effect = [dict(action='fill', selector=email['selector'], fact='Email'),
                          dict(action='upload', selector='#resume'),
                          dict(action='submit', selector='#submit')]
    session.after_submit = inventory(url=entry(ats) + ('/thanks' if redirect else ''))
    session.after_submit['text'] = 'Thank you for applying'
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(forms.asyncio, 'sleep', fast_sleep)
    # #139 AC1/AC8: keep the same confirmation assertions, but exercise the
    # bound raw producer, exact row PDF and actual durable hold before click.
    result = await forms.apply(posting(ats), COMPANY, {'Email': 'test@example.test'},
                               'synthetic', pk=PK, resume_path=pdf)
    assert result['status'] == ('uncertain' if redirect else 'applied')
    assert len(hold) == 1
    assert [c for c in session.calls if c[0] == 'click'] == [('click', '#submit')]
    if redirect:
        assert 'Check the employer portal before retrying' in result['detail']


@pytest.mark.parametrize('board', ['example-co.test', 'a_b', 'a/b', 'a%20b',
                                    'https://example.test', '-a', 'a-', 'a' * 64, 'é'])
def test_board_grammar_even_with_matching_row_and_config(monkeypatch, board):
    row, configs, _, _ = world(monkeypatch)
    configs[0].board = board
    row['jd_url'] = f'https://jobs.ashbyhq.com/{board}/{JOB}'
    assert forms._configured_alias(PK, COMPANY, row['jd_url']) is None


@pytest.mark.parametrize('job', [JOB.upper(), JOB.replace('-', ''), 'not-a-uuid'])
def test_uuid_spelling_even_with_coherent_pk_and_posting(monkeypatch, job):
    row, _, _, _ = world(monkeypatch)
    row.update(job_id=job, pk=make_pk(COMPANY, job),
               jd_url=f'https://jobs.ashbyhq.com/example-co/{job}')
    assert forms._configured_alias(row['pk'], COMPANY, row['jd_url']) is None


@pytest.mark.parametrize('board', ['a', 'a' * 63, 'Example-Co'])
def test_canonical_board_boundaries_are_positive(monkeypatch, board):
    row, configs, _, _ = world(monkeypatch)
    configs[0].board = board
    row['jd_url'] = f'https://jobs.ashbyhq.com/{board}/{JOB}'
    proof = forms._configured_alias(PK, COMPANY, row['jd_url'])
    assert proof.board == board
    assert proof.url == row['jd_url'] + '/application'


async def test_one_monotonic_budget_includes_reads_and_sleeps_not_navigation(monkeypatch):
    _, _, session, _ = world(monkeypatch)
    loop = asyncio.get_running_loop()
    clock = [loop.time()]
    monkeypatch.setattr(loop, 'time', lambda: clock[0])
    original_call = session.call
    async def slow_navigation(method, *args):
        if method == 'navigate':
            clock[0] += 40
        return await original_call(method, *args)
    session.call = slow_navigation
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 15.0)
    async def slow_read(s):
        clock[0] += 4
    session.on_page = slow_read
    async def elapsed_sleep(delay):
        clock[0] += delay
    monkeypatch.setattr(forms.asyncio, 'sleep', elapsed_sleep)
    report = await attempt()
    assert report['status'] == 'gate'
    assert 'read timed out/unavailable' in report['question']
    # 40s navigation is excluded; four reads exhaust the single 15s wait,
    # whereas a restarted deadline would wrongly allow bootstrap.
    assert session.pages == 4
    no_mutations(session)


@pytest.mark.parametrize('problem', ['competing-form', 'password', 'wrong-owner', 'no-answer'])
async def test_native_application_ownership_is_positive_not_page_text(monkeypatch, problem):
    page = application('lever')
    if problem == 'competing-form':
        page['controls'].append(field('Other answer', '#other', form_selector='#other'))
    elif problem == 'password':
        page['controls'].append(field('Secret', '#secret', type='password',
                                       form_selector='#application-form-lever'))
    elif problem == 'wrong-owner':
        page['controls'][1]['form_selector'] = '#other'
    else:
        page['controls'] = page['controls'][1:]
    _, _, session, decide = world(monkeypatch, 'lever', [page])
    monkeypatch.setattr(forms, '_APPLICATION_WAIT_SECONDS', 0.02)
    report = await attempt('lever')
    assert report['status'] == 'gate'
    assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('verb', ['fill', 'upload', 'submit'])
async def test_mapping_drift_during_final_receiver_read_forbids_ipc(monkeypatch, verb):
    row, configs, session, decide = world(monkeypatch)
    page = application('ashby')
    if verb == 'upload':
        page['controls'][0] = field('Résumé', '#resume', type='file',
                                    form_action=entry('ashby'))
    session.current = page
    proof = forms._configured_alias(PK, COMPANY, posting('ashby'))
    async def drift(s):
        configs[0].board = 'other-co'
    session.on_evaluate = drift
    with pytest.raises(forms.Gate):
        if verb == 'submit':
            await forms._fresh_native_receiver(
                session, page, {'selector': '#submit'}, page['controls'][-1], proof)
            # ARCH21:58 / binding 3: receiver observation is preparation;
            # current tenant equality belongs to the ONE final guard only.
            guard = forms.ApplicationIPCController(
                PK, COMPANY, posting('ashby'), '', {}, {}, [], {}, lambda: None)
            await guard.authorize_final_submit(session, page, page['controls'][-1])
        else:
            await forms.execute(
                session, page, {'action': verb, 'selector': page['controls'][0]['selector'],
                                'fact': 'Email'},
                facts={'Email': 'test@example.test'}, filled={}, resume_path='Resume.pdf',
                company=COMPANY, jd_text='', resume_tex='', github='', pk=PK,
                job_url=posting('ashby'), provenance=proof)
    no_mutations(session)


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('drift', ['board', 'job_id', 'pk', None])
@pytest.mark.parametrize('refresh', ['page', 'evaluate'])
async def test_lazy_combobox_rechecks_current_authority_before_filter_fill(
        monkeypatch, ats, drift, refresh):
    from tests.tools import test_browser_combobox as combo

    row, configs, _, _ = world(monkeypatch, ats)
    initial_target = combo.control()
    initial_target['combobox']['options'] = []
    initial = combo.page(initial_target)
    opened = combo.page()
    committed = combo.page(combo.control(
        selected_text='Example Country', selected_value='EX', hidden_value='EX'))
    for page in (initial, opened, committed):
        page['url'] = entry(ats)
        for c in page['controls']:
            c['form_action'] = entry(ats)
    row['human_approved_answers'] = {'What country?': {
        **combo.grant(), 'url': entry(ats), 'form_action': entry(ats)}}
    session = combo.DynamicSession(initial, opened, committed)
    original_page, original_call = session.page, session.call
    drifted = False
    def change():
        nonlocal drifted
        if drift and session.stage == 1 and not drifted:
            drifted = True
            if drift == 'board':
                configs[0].board = 'other-co'
            else:
                row[drift] = 'foreign-job'
    async def page_read():
        if refresh == 'page':
            change()
        return await original_page()
    async def ipc(*args, **kwargs):
        if refresh == 'evaluate' and args[0] == 'evaluate':
            change()
        return await original_call(*args, **kwargs)
    session.page, session.call = page_read, ipc
    filled = {}
    proof = forms._configured_alias(PK, COMPANY, posting(ats))
    async def execute():
        return await forms.execute(
            session, initial, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled=filled, resume_path='',
            company=COMPANY, jd_text='', resume_tex='', github='', pk=PK,
            job_url=posting(ats), provenance=proof)
    if drift and drift != 'board':
        # The read changes no DOM/grant/receiver bytes: only independent current
        # authority is revoked. A later pre-option Gate cannot undo filter fill.
        with pytest.raises(forms.Gate):
            await execute()
        assert drifted
        assert not [call for call in session.calls if call[0] == 'fill']
        assert ('click', '#country-example') not in session.calls
        assert filled == {}
    else:
        receipt = await execute()
        assert receipt['committed_value'] == 'EX'
        assert ('fill', '#country', '--value', 'Example Country') in session.calls
        assert ('click', '#country-example') in session.calls
        if drift == 'board':
            # ARCH21:58 / AC14: pin drift cannot block approved selection.
            # Preserve the negative guarantee at the same final guard.
            assert drifted
            guard = forms.ApplicationIPCController(
                PK, COMPANY, posting(ats), '', {}, {}, [], {}, lambda: None)
            with pytest.raises(forms.Gate, match='job/ATS tenant or board'):
                await guard.authorize_final_submit(session, committed, {'label': 'Submit'})
            assert ('click', '#submit') not in session.calls


def dom(spec, script=None):
    import json
    source = script or forms.ENTRY_PAGE
    if spec.get('treeNodes', 0) >= 4096:
        # The boundary-size upload census traverses thousands of DOM nodes.
        # Its fresh-VM evaluation timed out in CI; keep the original one-shot
        # command for this expensive negative/safety matrix, without dropping it.
        import subprocess
        from pathlib import Path
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('entry_dom_fixture.js')),
             json.dumps(spec), source], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    from tests.tools.reader_node_harness import reader_output
    return json.loads(reader_output('entry_dom_fixture.js', spec, source))


@pytest.mark.parametrize('reader', ['page', 'plain_fixed', 'entry', 'fixed'])
@pytest.mark.parametrize('ats,raw_action', [
    ('ashby', 'explicit'), ('ashby', 'absent'), ('ashby', 'empty'),
    ('lever', 'explicit'),
])
def test_shared_page_and_fixed_readers_observe_host_scoped_native_action(reader, ats, raw_action):
    """The ordinary PAGE must not depend on variables scoped to entry readers."""
    from tools.browser_skill_dom import PAGE

    # Plain fixed reads have no entry-only ashbyNative/entryLabel declarations.
    scripts = {'page': PAGE, 'plain_fixed': forms._FIXED_CONTROL_WITNESS,
               'entry': forms.ENTRY_PAGE, 'fixed': forms._ENTRY_FIXED_CONTROL_WITNESS}
    snapshot = dom(dict(url=entry(ats), actionAttr=raw_action,
                        nativePair=ats == 'ashby' and raw_action == 'explicit'),
                   scripts[reader])
    assert snapshot['url'] == entry(ats)
    controls = {c['selector']: c for c in snapshot['controls']}
    for selector in ('#email', '#submit'):
        item = controls[selector]
        assert item['form_action'] == entry(ats)
        if ats == 'ashby':
            assert item['label'] == ('Email' if selector == '#email' else 'Submit application')
            assert item['form_action_attr_present'] is (raw_action != 'absent')
            assert item['form_action_raw'] == (
                None if raw_action == 'absent' else
                '' if raw_action == 'empty' else entry(ats))
            assert item['formaction_attr_present'] is False
            assert item['formaction_raw'] is None
        else:
            for key in ('form_action_attr_present', 'form_action_raw',
                        'formaction_attr_present', 'formaction_raw'):
                assert key not in item
    if ats == 'ashby' and raw_action == 'explicit':
        for selector in ('#foreign-email', '#foreign-resume', '#foreign-submit'):
            foreign = controls[selector]
            assert foreign['form_action_attr_present'] is True
            assert foreign['form_action_raw'] == foreign['form_action']
            assert foreign['form_action_raw'] != entry(ats)


def _dom_with_readable_child(spec, script):
    """Inject a synthetic child into the existing offline fixture in memory.

    The ordinary fixture deliberately refuses child-document reads. Its file
    remains unchanged; only this test process substitutes that getter.
    """
    import json
    import subprocess
    from pathlib import Path

    fixture = Path(__file__).with_name('entry_dom_fixture.js').read_text(encoding='utf-8')
    guard = "  Object.defineProperty(e,'contentDocument',{get:()=>{throw Error('CHILD FRAME READ');}});"
    getter = r"""  Object.defineProperty(e,'contentDocument',{get:()=>{
    const child=spec.child;
    if (!child) throw Error('CHILD FRAME READ');
    const control={tagName:child.control==='button'?'BUTTON':'DIV',
      shadowRoot:null,attributes:[]};
    const found=q=>{
      const selectors=q.split(',').map(s=>s.trim());
      return child.control==='button' ? selectors.includes('button') :
        child.control==='role-button' ?
          selectors.includes('[role="button"]') || selectors.includes('[role]') : false;
    };
    return {URL:child.url,readyState:child.readyState,
      querySelector:q=>found(q) ? control : null,
      querySelectorAll:q=>q==='*' && child.control!=='none' ? [control] : []};
  }});"""
    assert fixture.count(guard) == 1
    result = subprocess.run(
        ['node', '-e', fixture.replace(guard, getter), 'fixture',
         json.dumps(spec), script],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_unknown_frame_role_button_is_a_form_control(reader):
    # A complete, current child containing only div[role=button] is not empty.
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    url = 'https://widgets.example.test/embed/frame'
    page = _dom_with_readable_child(
        {'frames': ['R'], 'edit': {'src': url},
         'child': {'url': url, 'readyState': 'complete', 'control': 'role-button'}},
        script)
    frame = page['entry']['frames'][0]
    assert frame['status'] == 'unknown'
    assert frame['form_controls'] == 'yes'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda _url: True, None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('child_url,control,ready_state', [
    ('about:blank', 'none', 'complete'),
    ('https://widgets.example.test/embed/previous', 'button', 'complete'),
    ('https://widgets.example.test/embed/frame', 'button', 'loading'),
])
def test_unknown_frame_stale_or_incomplete_child_is_unknown(reader, child_url, control, ready_state):
    # A previous empty or actionable child, or one still loading, is not
    # evidence about this frame's current source.
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = _dom_with_readable_child(
        {'frames': ['R'], 'edit': {'src': 'https://widgets.example.test/embed/frame'},
         'child': {'url': child_url, 'readyState': ready_state, 'control': control}},
        script)
    assert page['entry']['frames'][0]['status'] == 'unknown'
    assert page['entry']['frames'][0]['form_controls'] == 'unknown'


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('control,expected', [('button', 'yes'), ('none', 'no')])
def test_unknown_frame_matching_complete_child_controls(reader, control, expected):
    # A matching, complete child still provides justified yes or no evidence.
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    url = 'https://widgets.example.test/embed/frame'
    page = _dom_with_readable_child(
        {'frames': ['R'], 'edit': {'src': url},
         'child': {'url': url, 'readyState': 'complete', 'control': control}},
        script)
    assert page['entry']['frames'][0]['status'] == 'unknown'
    assert page['entry']['frames'][0]['form_controls'] == expected


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_relative_unknown_frame_src_is_diagnostic_only(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom({'url': entry('ashby'), 'frames': ['R'],
                'edit': {'src': '/embed/frame?account=private-token#fragment'}}, script)
    frame = page['entry']['frames'][0]
    assert frame['status'] == 'unknown'
    assert frame['origin'] == 'https://jobs.ashbyhq.com'
    assert frame['path'] == '/embed/frame'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame') as error:
        forms._entry_safety(page, lambda u: u == entry('ashby'), None)
    detail = str(error.value)
    assert 'origin=https://jobs.ashbyhq.com path=/embed/frame' in detail
    for secret in ('account', 'private-token', 'fragment', '?', '#'):
        assert secret not in detail


@pytest.mark.parametrize('host', [
    'candidatealias.example.test', 'alphatoken.example.test', 'tenant123.example.test',
])
def test_unknown_frame_all_letter_and_numeric_hostname_redaction(host):
    page = dom({'frames': ['R'],
                'edit': {'src': f'https://{host}/frame/private-token?key=secret#fragment'}})
    assert page['entry']['frames'][0]['status'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame') as error:
        forms._entry_safety(page, lambda _url: True, None)
    detail = str(error.value)
    assert 'origin=https://[redacted].[redacted].[redacted]' in detail
    assert 'path=/frame/[redacted]' in detail
    for secret in (*host.split('.'), 'private-token', 'key', 'secret', 'fragment', '?', '#'):
        assert secret not in detail


async def test_multiple_unknown_frames_report_safe_structure_before_field_entry(monkeypatch):
    # The parent-only synthetic fixture makes every child document unreadable.
    # The entry guard must not invent a 'no controls' conclusion for any frame.
    private_segment = '12345678-1234-4234-8234-123456789abc'
    page = dom(dict(
        url=entry('ashby'), frames=['H', 'R', 'W', 'X'],
        edit={'src': 'https://widgets.example-co.test/frame/' + private_segment
              + '?access_token=do-not-print#fragment'}))
    assert len(page['entry']['frames']) == 4
    assert all(f['status'] == 'unknown' and f['form_controls'] == 'unknown'
               for f in page['entry']['frames'])

    with pytest.raises(forms.Gate, match='unknown/untrusted application frame') as error:
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)
    detail = str(error.value)
    for i in range(1, 5):
        assert f'frame[{i}] origin=https://[redacted].[redacted].[redacted]' in detail
    assert detail.count('path=/frame/[redacted]') == 4
    assert detail.count('source_kind=url') == 4
    assert detail.count('form_controls=unknown') == 4
    assert detail.count('outside_root=true') == 2
    assert detail.count('outside_root=false') == 2
    assert detail.count('visibility=hidden display=block') == 3
    assert 'visibility=visible display=block' in detail
    for dimensions in ('100x30', '236x48', '1x1'):
        assert f'box={dimensions}' in detail
    for secret in (private_segment, 'widgets', 'example-co', 'access_token', 'do-not-print', 'fragment', '?', '#'):
        assert secret not in detail

    _, _, session, decision = world(monkeypatch, 'ashby', [page])
    result = await attempt('ashby')
    assert result['status'] == 'gate'
    assert detail in result['question']
    assert decision.await_count == 0
    no_mutations(session)


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('kind', ['H', 'R', 'B', 'W', 'X'])
def test_complete_passive_signatures_are_element_specific(reader, kind):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=[kind]), script)
    assert page['entry']['frames'][0]['status'] == ('R' if kind == 'B' else kind)
    assert 'secret=' not in repr(page)
    assert 'fragment' not in repr(page)
    assert page['entry']['root']['selector'] == '#application-form'


@pytest.mark.parametrize('kind,edit', [
    ('H', {'src':'https://foreign.example-co.test/static/hcaptcha-enclave.html'}),
    ('R', {'src':'https://www.recaptcha.net/recaptcha/api2/anchor'}),
    ('W', {'title':'LinkedIn Embedded Content ' }),
    ('X', {'src':'about:blank'}),
])
def test_independent_one_condition_frame_lookalikes_gate(kind, edit):
    page = dom(dict(frames=[kind], edit=edit))
    assert page['entry']['frames'][0]['status'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda u: True, None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_exact_visible_badge_anchor_is_passive(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=['R'], visible=True, badge=True), script)
    assert page['entry']['frames'][0]['status'] == 'R'


@pytest.mark.parametrize('change', [
    {'badge':False}, {'inside':True}, {'bounds':{'width':300,'height':60}},
    {'bounds':{'width':256,'height':61}}, {'bounds':{'width':256.5,'height':60}},
    {'bounds':'missing'}, {'bounds':'nan'},
])
def test_seven_isolated_visible_anchor_violations_are_unknown(change):
    page = dom(dict(frames=['R'],visible=True,badge=True,**change) if 'badge' not in change
               else dict(frames=['R'],visible=True,**change))
    assert page['entry']['frames'][0]['status'] == 'unknown'


@pytest.mark.parametrize('placement', [{}, {'inside':True}, {'root':False}])
def test_visible_bframe_challenge_precedes_root_ordering(placement):
    page = dom(dict(frames=['B'],visible=True,**placement))
    assert page['entry']['frames'][0]['status'] == 'visible-recaptcha'
    with pytest.raises(forms.Gate, match='visible reCAPTCHA Enterprise challenge'):
        forms._entry_safety(page, lambda u: True, None)


@pytest.mark.parametrize('kind', ['R', 'X'])
def test_no_root_is_not_outside_root(kind):
    assert dom(dict(frames=[kind], root=False))['entry']['frames'][0]['status'] == 'unknown'
    assert dom(dict(frames=[kind]))['entry']['frames'][0]['status'] == kind



@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('marker', ['text','aria','labelledby','value','image','absent'])
def test_root_uses_existing_native_application_marker(reader, marker):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=['R'],marker=marker), script)
    assert bool(page['entry']['root']) == (marker != 'absent')
    assert page['entry']['frames'][0]['status'] == ('unknown' if marker=='absent' else 'R')

@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('name,kind', [('h-captcha-response','H'), ('g-recaptcha-response','R')])
@pytest.mark.parametrize('outside', [False, True])
@pytest.mark.parametrize('textarea', [False, True])
def test_provider_tokens_never_read_even_transiently(reader, name, kind, outside, textarea):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=[kind], tokens=[name], tokenOutside=outside, textarea=textarea), script)
    assert not page['entry']['response_error']
    assert all(c['selector'] != '#token-0' for c in page['controls'])
    assert page['entry']['responses'][0]['name'] == name
    assert len(page['controls']) == 2  # unrelated applicant siblings remain


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('name,kind', [('h-captcha-response','H'), ('g-recaptcha-response','R')])
def test_linked_receiver_claim_gates_before_graph_value_getter(reader, name, kind):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=[kind], tokens=[name], claim=True), script)
    assert page['entry']['response_error']
    assert page['controls'] == []
    assert 'hidden_value' not in repr(page) and 'selected_value' not in repr(page)
    with pytest.raises(forms.Gate, match='untrusted captcha response control'):
        forms._entry_safety(page, lambda u: True, None)


@pytest.mark.parametrize('tokens,frames', [
    (['h-captcha-response'], []), (['g-recaptcha-response'], []),
    (['h-captcha-response','h-captcha-response'], ['H']),
    (['g-recaptcha-response','g-recaptcha-response'], ['R']),
])
def test_orphan_and_duplicate_responses_are_immediate(tokens, frames):
    assert dom(dict(tokens=tokens,frames=frames))['entry']['response_error']


def test_hidden_helper_is_not_submit_and_visible_helper_is_challenge():
    page = dom(dict(helper='hidden'))
    assert [c['selector'] for c in page['controls']] == ['#email', '#submit']
    assert not page['entry']['helper_challenge']
    assert dom(dict(helper='visible'))['entry']['helper_challenge']


@pytest.mark.parametrize('kind', ['H','W'])
def test_direct_edges_are_required(kind):
    assert dom(dict(frames=[kind],brokenEdge=True))['entry']['frames'][0]['status'] == 'unknown'


@pytest.mark.parametrize('change', [{'required':True}, {'extra':True}])
def test_widget_row_must_not_be_a_question(change):
    assert dom(dict(frames=['W'],**change))['entry']['frames'][0]['status'] == 'unknown'


@pytest.mark.parametrize('reader', ['page','witness'])
@pytest.mark.parametrize('role', ['checkbox','radio','option'])
@pytest.mark.parametrize('placement', ['extraRole','rowRole'])
def test_w_row_cannot_mask_aria_applicant_controls(reader, role, placement):
    script = forms.ENTRY_PAGE if reader=='page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(frames=['W'],**{placement:role}),script)
    assert page['entry']['frames'][0]['status'] == 'unknown'
    selector = '#aria-applicant' if placement=='extraRole' else '#w-row'
    assert any(c['selector']==selector and c['type']==role for c in page['controls'])
    with pytest.raises(forms.Gate,match='unknown/untrusted application frame'):
        forms._entry_safety(page,lambda u: True,None)


@pytest.mark.parametrize('kind,edit,options,status', [
    ('H', {'src':'https://newassets.hcaptcha.com/static/hcaptcha-enclave.html/extra'}, {}, 'unknown'),
    ('H', {'style':{'visibility':'visible'}}, {}, 'visible-hcaptcha'),
    ('H', {'style':{'visibility':'visible','display':'none'}}, {}, 'H'),
    ('R', {'src':'https://foreign.example-co.test/recaptcha/enterprise/anchor'}, {}, 'unknown'),
    ('R', {'style':{'visibility':'visible','display':'none'}}, {}, 'R'),
    ('R', {}, {'inside':True}, 'unknown'),
    ('W', {'src':''}, {}, 'unknown'),
    ('W', {'src':'about:blank'}, {}, 'W'),
    ('W', {'rect':{'width':401,'height':48}}, {}, 'unknown'),
    ('W', {'rect':{'width':236,'height':101}}, {}, 'unknown'),
    ('W', {'rect':None}, {}, 'unknown'),
    ('X', {'src':''}, {}, 'unknown'),
    ('X', {'rect':{'width':2,'height':1}}, {}, 'unknown'),
    ('X', {'rect':{'width':1,'height':2}}, {}, 'unknown'),
    ('X', {'rect':None}, {}, 'unknown'),
    ('X', {'style':{'visibility':'visible','display':'none'}}, {}, 'unknown'),
    ('X', {}, {'inside':True}, 'unknown'),
])
def test_signature_conjunction_matrix(kind, edit, options, status):
    assert dom(dict(frames=[kind],edit=edit,**options))['entry']['frames'][0]['status'] == status


def fake_clock(monkeypatch):
    loop = asyncio.get_running_loop()
    clock = [loop.time()]
    monkeypatch.setattr(loop, 'time', lambda: clock[0])
    async def elapsed(delay):
        clock[0] += delay
    monkeypatch.setattr(forms.asyncio, 'sleep', elapsed)
    return clock


@pytest.mark.parametrize('ats,seconds', [('lever',15), ('ashby',90)])
async def test_exact_single_deadline_for_never_ready_page(monkeypatch, ats, seconds):
    clock = fake_clock(monkeypatch)
    start = clock[0]
    _, _, session, decide = world(monkeypatch, ats, [application(ats, controls=[])])
    report = await attempt(ats)
    assert 'no qualifying application form after bounded wait' in report['question']
    assert clock[0] - start == pytest.approx(seconds)
    assert len([c for c in session.calls if c[0]=='navigate']) == 1
    assert not decide.await_count
    no_mutations(session)


async def test_ashby_native_form_and_valid_token_after_sixty_seconds_stabilize(monkeypatch):
    clock = fake_clock(monkeypatch)
    start = clock[0]
    _, _, session, _ = world(monkeypatch, pages=[application('ashby',controls=[])])
    async def hydrate(s):
        if clock[0] - start > 61:
            s.current = application('ashby', entry={
                'root':{'selector':'#application-form','action':entry('ashby')},
                'frames':[{'selector':'#anchor','status':'R'}],
                'responses':[{'selector':'#token','name':'g-recaptcha-response','form':''}],
                'response_error':False,'helper_challenge':False})
    session.on_page = hydrate
    bootstrap = AsyncMock(side_effect=forms.Gate('ready after late native hydration'))
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    report = await attempt()
    assert 'ready after late native hydration' in report['question']
    assert 61 < clock[0] - start < 90
    assert bootstrap.await_count == 1
    assert all(c[1] in {forms.ENTRY_PAGE,forms._ENTRY_FIXED_CONTROL_WITNESS}
               for c in session.calls if c[0]=='evaluate')
    no_mutations(session)


@pytest.mark.parametrize('change,cause', [
    ({'frames':[{'status':'unknown'}]}, 'unknown/untrusted application frame'),
    ({'frames':[{'status':'visible-hcaptcha'}]}, 'visible hCaptcha challenge'),
    ({'frames':[{'status':'visible-recaptcha'}]}, 'visible reCAPTCHA Enterprise challenge'),
    ({'response_error':True}, 'untrusted captcha response control'),
    ({'helper_challenge':True}, 'visible hCaptcha challenge control'),
])
@pytest.mark.parametrize('verb', ['fill','submit'])
async def test_later_unsafe_observation_gates_before_next_ipc(monkeypatch, change, cause, verb):
    base = application('lever', entry={
        'root':{'selector':'#application-form-lever','action':entry('lever')},
        'frames':[{'status':'H'}], 'responses':[], 'response_error':False, 'helper_challenge':False})
    unsafe = deepcopy(base)
    unsafe['entry'].update(change)
    _, _, session, decide = world(monkeypatch, 'lever', [base]*4+[unsafe])
    monkeypatch.setattr(forms, '_bootstrap', AsyncMock(side_effect=lambda s,p,*a,**kw:p))
    decide.return_value = {'action':verb,'selector':'#email-input' if verb=='fill' else '#submit','fact':'Email'}
    report = await attempt('lever')
    assert cause in report['question']
    assert decide.await_count == 1
    no_mutations(session)


@pytest.mark.parametrize('read', ['first','nested'])
@pytest.mark.parametrize('receiver', ['form_action','formaction'])
async def test_partial_inventory_checks_unrelated_observed_receivers(monkeypatch, read, receiver):
    fake_clock(monkeypatch)
    bad = application('lever', inventory_verified=False)
    other = field('Other form','#other',form_selector='#unrelated',form_action=entry('lever'))
    other[receiver] = 'https://foreign.example-co.test/receive'
    bad['controls'].append(other)
    pages = [bad] if read=='first' else [application('lever'),bad]
    _, _, session, decide = world(monkeypatch,'lever',pages)
    report = await attempt('lever')
    assert 'native form receiver not proven' in report['question']
    assert session.pages == (1 if read=='first' else 2)
    assert not decide.await_count
    no_mutations(session)


async def test_truncation_claims_no_receiver_beyond_observed_bound(monkeypatch):
    page = application('lever',truncated=True)
    page['controls'] = [field(f'Synthetic {i}',f'#field-{i}',form_action=entry('lever')) for i in range(500)]
    bad = application('lever')
    bad['controls'][0]['form_action'] = 'https://foreign.example-co.test/receive'
    _, _, session, decide = world(monkeypatch,'lever',[page,bad])
    report = await attempt('lever')
    assert 'native form receiver not proven' in report['question']
    assert session.pages == 2 and not decide.await_count
    no_mutations(session)


def test_baseline_visibility_is_not_hidden_captcha_evidence():
    from tools.browser_skill_dom import PAGE
    assert ".filter(visible).map(e=>e.src)" in PAGE
    assert "getComputedStyle(e).visibility !== 'hidden'" in PAGE
    assert 'entryFrames' not in PAGE
    # The ordinary reader retains native tokens and normalized visible src.
    assert 'responseNodes' not in forms._FIXED_CONTROL_WITNESS


def test_real_row_and_forged_fixture_context_never_gain_synthetic_entry(monkeypatch):
    from tools import browser_skill_fixture as fixture
    row, _, _, _ = world(monkeypatch)
    row.update(pk=fixture.PK,company=fixture.COMPANY,jd_url=fixture.URL)
    assert forms._configured_alias(fixture.PK,fixture.COMPANY,fixture.URL) is None
    forged = fixture._SyntheticRun('/synthetic/resume.pdf')
    forged.enable_positive_fixture()
    assert forms._configured_alias(fixture.PK,fixture.COMPANY,fixture.URL,fixture_context=forged) is None
    with pytest.raises(PermissionError):
        forged.entry_provenance()


@pytest.mark.parametrize('drift', ['route','row','config'])
async def test_partial_first_read_cannot_hide_authority_drift(monkeypatch, drift):
    fake_clock(monkeypatch)
    row, configs, session, decide = world(monkeypatch,'lever',[application('lever',inventory_verified=False)])
    async def change(s):
        if drift=='route':
            s.current['url'] += '/foreign'
        elif drift=='row':
            row['job_id'] = 'foreign'
        else:
            configs[0].board = 'foreign'
    session.on_page = change
    result = await attempt('lever')
    if drift == 'row':
        assert 'route or current row/config changed' in result['question']
        assert session.pages == 1
    else:
        # ARCH21:58 / AC2/AC14: a same-host route or board-pin change cannot
        # recreate an early tenant certificate. Incomplete reads remain bounded
        # and the unchanged final guard must refuse this route/configuration.
        assert 'unverified application inventory after bounded wait' in result['question']
        assert session.pages > 1
        guard = forms.ApplicationIPCController(
            PK, COMPANY, posting('lever'), '', {}, {}, [], {}, lambda: None)
        with pytest.raises(forms.Gate, match='job/ATS tenant|different job/ATS tenant'):
            await guard.authorize_final_submit(session, session.current, {'label': 'Submit'})
    assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('member', ['tag','type','in_form','selector'])
async def test_missing_control_shape_is_immediate_not_readiness(monkeypatch, member):
    fake_clock(monkeypatch)
    page = application('lever',inventory_verified=False)
    page['controls'][0].pop(member)
    _, _, session, decide = world(monkeypatch,'lever',[page])
    result = await attempt('lever')
    assert 'control inventory is malformed' in result['question']
    assert session.pages == 1 and not decide.await_count


@pytest.mark.parametrize('start', [0.0, 122.43808771])
async def test_read_unavailable_uses_remaining_single_budget(monkeypatch, start):
    clock = fake_clock(monkeypatch)
    clock[0] = start
    _, _, session, decide = world(monkeypatch,'lever')
    async def unavailable(s):
        raise OSError('synthetic inventory read unavailable')
    session.on_page = unavailable
    result = await attempt('lever')
    assert 'read timed out/unavailable after bounded wait' in result['question']
    # Compare absolute deadlines: subtracting a fractional origin can round
    # even when the controller stopped at exactly the intended deadline.
    assert clock[0] == start + 15
    assert not decide.await_count
    no_mutations(session)


async def test_independent_provider_identity_mismatch_resets_admission(monkeypatch):
    _, _, raw, _ = world(monkeypatch, 'lever', [application('lever'),application('lever')])
    provenance = forms._configured_alias(PK, COMPANY, posting('lever'))
    session = forms._EntrySession(raw, lambda u: True, provenance)
    page = await session.page()
    async def different_provider(s):
        s.current['entry']['frames'] = [{'selector':'#late-anchor','status':'R'}]
    raw.on_evaluate = different_provider
    with pytest.raises(forms._EntryNotReady, match='unstable application form inventory'):
        await forms._observe_controls(session, page, readiness=True)
    no_mutations(raw)
# Issue #120: use the actual two read-only production JS readers (not fabricated
# candidate/job metadata) to exercise native ownership and raw HTML attributes.
def native_observed(**changes):
    spec = {'url': entry('ashby'), **changes}
    page = dom(spec)
    fixed = dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)
    return page, fixed


class NativeReadSession:
    def __init__(self, page, fixed):
        self.current, self.fixed = deepcopy(page), deepcopy(fixed)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call(self, method, *args, **kwargs):
        self.calls.append((method, *args))
        if method == 'navigate':
            return {}
        if method == 'evaluate':
            if args == (forms.ENTRY_PAGE,):
                return {'value': deepcopy(self.current)}
            assert args == (forms._ENTRY_FIXED_CONTROL_WITNESS,)
            return {'value': deepcopy(self.fixed)}
        if method == 'fill':
            selector, _, value = args
            for snapshot in (self.current, self.fixed):
                for c in snapshot['controls']:
                    if c['selector'] == selector:
                        c['value'] = value
                        c['has_value'] = True
            return {}
        if method == 'click' and args == ('#native-choice',):
            for snapshot in (self.current, self.fixed):
                for c in snapshot['controls']:
                    if c['selector'] == '#native-choice':
                        c['checked'] = True
            return {}
        pytest.fail(f'Unexpected mutating IPC: {method}')


def native_real_world(monkeypatch, **changes):
    row, configs, _, decide = world(monkeypatch)
    page, fixed = native_observed(**changes)
    raw = NativeReadSession(page, fixed)
    monkeypatch.setattr(bsk, 'Session', lambda *a: raw)
    proof = forms._application_entry(row, PK, COMPANY, posting('ashby'), configs)
    return row, raw, decide, proof


@pytest.mark.parametrize('pair', [False, True])
async def test_ashby_raw_action_job_binding_independent_native_readers(monkeypatch, pair):
    row, raw, decide, proof = native_real_world(monkeypatch, nativePair=pair)
    for snapshot in (raw.current, raw.fixed):
        candidates = snapshot['entry']['native_candidates']
        assert len(candidates) == (2 if pair else 1)
        assert candidates[0]['action_attr_present'] is True
        assert candidates[0]['action_attr'] == proof.url
        if pair:
            assert candidates[1]['action_attr_present'] is True
            assert candidates[1]['action_attr'] != proof.url
            assert len(candidates[1]['controls']) == 3
            assert {c['selector'] for c in candidates[1]['controls']} == {
                '#foreign-email', '#foreign-resume', '#foreign-submit'}
    # Root admission and the field/human-question boundary are separately
    # observable. A later ordinary form gate must not be mistaken for failure
    # to identify the tracked native owner.
    session = forms._EntrySession(raw, lambda u: u == proof.url, proof)
    admitted = await forms._stable_application_form(
        session, session.allowed, proof,
        deadline=asyncio.get_running_loop().time() + 10)
    assert admitted['entry']['selected_native_owner'] == '#application-form'
    assert {c['selector'] for c in admitted['controls']} == {'#email', '#submit'}
    assert admitted['entry']['quarantined_native_owners'] == (
        ['#foreign-application'] if pair else [])
    prepared = await forms._bootstrap(
        session, admitted, PK, {}, posting('ashby'), provenance=proof)
    assert {c['selector'] for c in prepared['controls']} == {'#email', '#submit'}
    assert not prepared['controls'][0]['has_value']
    # No early question or write, but the final-form decision still rejects
    # the missing required answer before any committing click.
    with pytest.raises(forms.Gate, match='Required field is empty: Email'):
        forms.check_form(prepared, {}, uploaded=True)
    no_mutations(raw)
    result = await attempt()
    assert result['status'] == 'gate', result
    assert not any(reason in result['question'] for reason in (
        'ambiguous application roots', 'root lacks tracked job binding',
        'incomplete native candidate/control census'))
    if result['form_question'] is not None:
        assert result['form_question']['label'] == 'Email', result
    if decide.await_count:
        model_page = decide.await_args.args[1]
        assert {c['selector'] for c in model_page['controls']} == {'#email', '#submit'}
        assert 'native_candidates' not in model_page['entry']
    no_mutations(raw)


@pytest.mark.parametrize('raw_action', ['absent', 'empty'])
async def test_ashby_native_defaulted_action_is_not_job_witness(monkeypatch, raw_action):
    _, raw, decide, proof = native_real_world(monkeypatch, actionAttr=raw_action)
    for snapshot in (raw.current, raw.fixed):
        owner = snapshot['entry']['native_candidates'][0]
        assert snapshot['entry']['root'] is None  # unproven resolved action selects nothing
        assert owner['action_attr_present'] == (raw_action != 'absent')
        assert owner['action_attr'] == ('' if raw_action == 'empty' else None)
        assert owner['action'] == proof.url
    result = await attempt()
    assert result['status'] == 'gate', result
    # Raw action attributes remain independently absent/empty above. The
    # removed pre-step native certificate is not the submission authority.
    assert result['form_question'] is None
    assert 'not been submitted' in result['question']
    assert decide.await_count == 1
    no_mutations(raw)


@pytest.mark.parametrize('visibility', ['opacity-zero', 'zero-width', 'zero-height'])
async def test_native_receiver_visibility_must_agree_with_page_and_fixed_witnesses(
        monkeypatch, visibility):
    _, raw, decide, _ = native_real_world(
        monkeypatch, nativeVisibility=visibility)
    for snapshot in (raw.current, raw.fixed):
        candidate = snapshot['entry']['native_candidates'][0]
        email = next(c for c in candidate['controls'] if c['selector'] == '#email')
        assert email['visible'] is False
    assert any(c['selector'] == '#email' for c in raw.current['controls'])
    fixed_email = next(c for c in raw.fixed['controls'] if c['selector'] == '#email')
    assert fixed_email['visible'] is False
    # Both fixed readers keep this test receiver invisible; no late hydration.
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    result = await attempt()
    assert result['status'] == 'gate', (visibility, result)
    assert 'no qualifying application form after bounded wait' in result['question']
    assert not decide.await_count
    no_mutations(raw)


async def test_native_hidden_machine_control_remains_separate_from_visible_receivers(
        monkeypatch):
    row, raw, decide, proof = native_real_world(monkeypatch, nativeHidden=True)
    session = forms._EntrySession(raw, lambda url: url == proof.url, proof)
    admitted = await forms._stable_application_form(
        session, session.allowed, proof,
        deadline=asyncio.get_running_loop().time() + 10)
    machine = next(c for c in admitted['controls'] if c['selector'] == '#native-machine')
    assert machine['type'] == 'hidden'
    candidate_machine = next(
        c for c in raw.current['entry']['native_candidates'][0]['controls']
        if c['selector'] == '#native-machine')
    fixed_machine = next(c for c in raw.fixed['controls'] if c['selector'] == '#native-machine')
    assert candidate_machine['visible'] is False
    assert fixed_machine['visible'] is False
    prepared = await forms._bootstrap(
        session, admitted, PK, {}, posting('ashby'), provenance=proof)
    assert {c['selector'] for c in prepared['controls']} >= {
        '#native-machine', '#email', '#submit'}
    assert prepared['controls'][0]['selector'] != '#native-machine'
    with pytest.raises(forms.Gate):
        forms.check_form(prepared, {}, uploaded=True)
    assert not decide.await_count
    no_mutations(raw)

@pytest.mark.parametrize('case,options,cause', [
    ('contrary-job', {'action':
        'https://jobs.ashbyhq.com/example-co/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/application'},
     'lacks tracked job binding'),
    ('indistinguishable', {'nativePair': True, 'foreignAction': entry('ashby')},
     'ambiguous application roots'),
    ('cross-owner-borrow', {'nativePair': True, 'foreignBorrow': True},
     'cross-owned or contradictory'),
    ('foreign-override', {'nativePair': True, 'foreignOverride': entry('ashby')},
     'formaction override conflicts'),
])
async def test_ashby_complete_native_graph_negatives_gate_without_ipc(
        monkeypatch, case, options, cause):
    _, raw, decide, _ = native_real_world(monkeypatch, **options)
    assert raw.current['entry']['root'] is None
    result = await attempt()
    assert result['status'] == 'gate', (case, result)
    if case == 'indistinguishable':
        # ARCH 07:34: a shape-ambiguous root does not make already-approved
        # reversible writes or choices an application Submit. The final IPC
        # guard remains the only commit authority.
        assert 'ambiguous application roots' not in result['question']
    else:
        # The old per-step native-census refusal words were intentionally
        # retired. None of these contradictory observed roots can be treated as
        # an approved final application or trigger a writing IPC.
        assert result['form_question'] is None
        assert 'not been submitted' in result['question']
        # A definite foreign posting ID refuses before the model or any effectful IPC.
        assert decide.await_count == (0 if case == 'contrary-job' else 1)
    no_mutations(raw)


async def test_ashby_unavailable_or_contradictory_fixed_native_witness(monkeypatch):
    # This permanent synthetic disagreement is separate from the 20 ARCH13:32
    # targets; the real 90s wait remains covered by fake-clock hydration tests.
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _, raw, decide, _ = native_real_world(monkeypatch, nativePair=True)
    owner = raw.fixed['entry']['native_candidates'][0]
    owner['action_attr_present'] = False
    owner['action_attr'] = None
    # The absent raw attribute and the defaulted resolved property are
    # independently observed; keep the native control evidence coherent.
    for control_item in raw.fixed['controls']:
        if control_item['form_selector'] == owner['selector']:
            control_item['form_action_attr_present'] = False
            control_item['form_action_raw'] = None
    result = await attempt()
    assert result['status'] == 'gate'
    # Raw action attributes remain independently absent/empty above. The
    # removed pre-step native certificate is not the submission authority.
    assert result['form_question'] is None
    assert ('after bounded wait' in result['question']
            or 'not been submitted' in result['question'])
    assert not decide.await_count
    no_mutations(raw)


async def native_admitted_with_one_safe_fill(monkeypatch):
    row, raw, _, provenance = native_real_world(monkeypatch, nativePair=True)
    session = forms._EntrySession(raw, lambda u: u == provenance.url, provenance)
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    item = next(c for c in page['controls'] if c['selector'] == '#email')
    fresh = {c['selector']: c for c in raw.fixed['controls']}
    question = forms._disclosure_question(
        page, item, fresh, posting('ashby'), provenance=provenance)
    row['human_approved_answers'][question['label']] = {
        'value': 'test@example-co.test', 'label': question['control_label'],
        **{k: question[k] for k in ('selector', 'url', 'question', 'tag',
                                    'type', 'form_selector', 'form_action')}}
    filled = {}
    receipt = await forms.execute(
        session, page, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
        facts={'Email': 'test@example-co.test'}, filled=filled,
        resume_path='Resume.pdf', company=COMPANY, jd_text='', resume_tex='',
        github='', pk=PK, job_url=posting('ashby'), provenance=provenance)
    assert receipt['action'] == 'fill'
    assert [c for c in raw.calls if c[0] == 'fill'] == [
        ('fill', '#email', '--value', 'test@example-co.test')]
    return raw, session, filled


@pytest.mark.parametrize('at', ['before-next-fill', 'before-submit'])
async def test_ashby_native_drift_after_safe_field_gates_zero_further_ipc(monkeypatch, at):
    raw, session, filled = await native_admitted_with_one_safe_fill(monkeypatch)
    assert filled == {'#email': 'test@example-co.test'}
    foreign_route = (
        'https://jobs.ashbyhq.com/example-co/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/application')
    for snapshot in (raw.current, raw.fixed):
        candidate = snapshot['entry']['native_candidates'][0]
        candidate['action_attr'] = candidate['action'] = foreign_route
        # Model the complete observed DOM changing rather than only one
        # correlated witness slot; the earlier safe fill stays recorded.
        for control_item in snapshot['controls']:
            if control_item['form_selector'] == candidate['selector']:
                control_item['form_action'] = foreign_route
                control_item['form_action_raw'] = foreign_route
    before = len([c for c in raw.calls if c[0] in {'fill', 'upload', 'click', 'select'}])
    next_verb, selector = ('fill', '#email') if at == 'before-next-fill' else ('click', '#submit')
    with pytest.raises(forms.Gate, match='native form receiver not proven|tracked job binding|root identity drift'):
        await session.call(next_verb, selector, '--value', 'another' if at == 'before-next-fill' else '')
    assert len([c for c in raw.calls if c[0] in {'fill', 'upload', 'click', 'select'}]) == before == 1
    assert not [c for c in raw.calls if c[0] == 'click']


async def test_ashby_complete_foreign_form_precedes_tracked_form_without_positional_choice(
        monkeypatch):
    _, raw, decide, proof = native_real_world(monkeypatch, nativePair=True,
                                               foreignFirst=True)
    for snapshot in (raw.current, raw.fixed):
        candidates = snapshot['entry']['native_candidates']
        assert [c['selector'] for c in candidates] == [
            '#foreign-application', '#application-form']
        assert candidates[0]['action_attr'] != proof.url
        assert candidates[1]['action_attr'] == proof.url
    session = forms._EntrySession(raw, lambda u: u == proof.url, proof)
    admitted = await forms._stable_application_form(
        session, session.allowed, proof,
        deadline=asyncio.get_running_loop().time() + 10)
    assert admitted['entry']['selected_native_owner'] == '#application-form'
    assert admitted['entry']['quarantined_native_owners'] == ['#foreign-application']
    prepared = await forms._bootstrap(
        session, admitted, PK, {}, posting('ashby'), provenance=proof)
    assert {c['selector'] for c in prepared['controls']} == {'#email', '#submit'}
    with pytest.raises(forms.Gate, match='Required field is empty: Email'):
        forms.check_form(prepared, {}, uploaded=True)
    result = await attempt()
    assert result['status'] == 'gate', result
    assert not any(reason in result['question'] for reason in (
        'ambiguous application roots', 'root lacks tracked job binding',
        'incomplete native candidate/control census'))
    if result['form_question'] is not None:
        assert result['form_question']['label'] == 'Email'
    if decide.await_count:
        assert {c['selector'] for c in decide.await_args.args[1]['controls']} == {
            '#email', '#submit'}
    no_mutations(raw)


async def test_ashby_approved_native_checkbox_is_not_mistaken_for_submit(monkeypatch):
    """The real guarded choose path must not arm an uncertain submit hold."""
    row, raw, decide, provenance = native_real_world(monkeypatch, nativeCheckbox=True)
    session = forms._EntrySession(raw, lambda u: u == provenance.url, provenance)
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    committing = []
    session.on_committing_click = lambda: committing.append(True)
    target = next(c for c in page['controls'] if c['selector'] == '#native-choice')
    witness = {c['selector']: c for c in raw.fixed['controls']}
    question = forms._disclosure_question(
        page, target, witness, posting('ashby'), provenance=provenance)
    row['human_approved_answers'][question['label']] = {
        'value': 'yes', 'label': question['control_label'],
        'visible_text': question['visible_text'],
        **{k: question[k] for k in ('selector', 'url', 'question', 'tag',
                                    'type', 'form_selector', 'form_action')}}
    receipt = await forms.execute(
        session, page, {'action': 'choose', 'selector': '#native-choice',
                        'fact': question['label']},
        facts={}, filled={}, resume_path='Resume.pdf', company=COMPANY,
        jd_text='', resume_tex='', github='', pk=PK,
        job_url=posting('ashby'), provenance=provenance)
    assert receipt['action'] == 'choose'
    assert [call for call in raw.calls if call[0] == 'click'] == [
        ('click', '#native-choice')]
    assert committing == [] and session.committing is False
    assert any(c['selector'] == '#native-choice' and c['checked']
               for c in (await session.page())['controls'])
    assert not decide.await_count

@pytest.mark.parametrize('ats', ['ashby', 'lever'])
def test_real_browser_dispatch_checks_current_posting_generation_before_any_lease(
    monkeypatch, ats,
):
    """The existing entry world cannot bypass an unreviewed row by mere URL/PK."""
    from types import SimpleNamespace

    import fakeredis
    from core.models import JobRecord, Status
    from core.storage.local import RedisTracking
    from tools import posting_guard as pg

    actual_check = forms.check_dispatch
    row, configs, session, decision = world(monkeypatch, ats)
    monkeypatch.setattr(forms, 'check_dispatch', actual_check)
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    long_body = "Build useful software systems for product teams. " * 20
    guarded = pg.analyze(long_body, source_required=True)
    assert tracking.put_new(JobRecord(
        company=COMPANY, job_id=JOB, title="Test Engineer",
        jd_url=posting(ats), jd_text=guarded['text'],
        posting_guard=guarded['posting_guard'], ats=ats))
    tracking.set_status(PK, Status.SUBMITTING)
    monkeypatch.setattr('core.stores.make_stores',
                        lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr('tools.browser_apply._duplicate_refusal', lambda pk: False)
    with pytest.raises(forms.Gate) as rejected:
        actual_check(PK, 'synthetic-resume.pdf')
    assert rejected.value.reason == 'posting_guard_review_required'
    assert not session.calls and not decision.await_count


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
def test_exact_owner_review_passes_guard_then_still_requires_queue_and_resume(
    monkeypatch, ats,
):
    from types import SimpleNamespace

    import fakeredis
    from core.models import JobRecord, Status
    from core.storage.local import RedisTracking
    from tools import posting_guard as pg

    actual_check = forms.check_dispatch
    world(monkeypatch, ats)
    monkeypatch.setattr(forms, 'check_dispatch', actual_check)
    tracking = RedisTracking(fakeredis.FakeRedis(decode_responses=True))
    body = "Build useful software systems for product teams. " * 20
    incoming = pg.analyze(body, source_required=True)
    assert tracking.put_new(JobRecord(
        company=COMPANY, job_id=JOB, title="Test Engineer",
        jd_url=posting(ats), jd_text=incoming['text'],
        posting_guard=incoming['posting_guard'], ats=ats))
    tracking.set_status(PK, Status.TAILORED, gate_reason='approval')
    observed = tracking.get_current(PK)
    generation = observed['posting_guard']['generation']
    assert tracking.update_posting_guard(PK, observed,
                                         reviewed_generation=generation)
    tracking.set_status(PK, Status.SUBMITTING, gate_reason='',
                        resume_tex_key='synthetic.tex', resume_seed='synthetic-seed')
    monkeypatch.setattr('core.stores.make_stores',
                        lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr('agent.run.seed_fingerprint', lambda: 'synthetic-seed')
    monkeypatch.setattr('tools.browser_apply._duplicate_refusal', lambda pk: False)
    from core import apply_queue

    class GrantedLease:
        def __init__(self, client):
            assert client is tracking.r

        def in_flight(self):
            return {PK}

        def depth(self):
            return {'running': [COMPANY]}

    monkeypatch.setattr(apply_queue, 'ApplyQueue', GrantedLease)
    with pytest.raises(forms.Gate) as rejected:
        actual_check(PK, 'synthetic-resume-does-not-exist.pdf')
    assert rejected.value.reason != 'posting_guard_review_required'
    assert 'PDF is missing' in str(rejected.value)


def test_page_reader_reports_native_inline_requestsubmit_as_committing():
    """The actual PAGE producer, not a synthetic boolean alone, detects inline submit."""
    from tools.browser_skill_dom import PAGE

    spec = dict(url=entry('ashby'), nativeCheckbox=True)
    script = (
        "document.querySelector('#native-choice').setAttribute("
        "'onclick', 'this.form.requestSubmit()');\n" + PAGE
    )
    observed = dom(spec, script)
    choice = next(c for c in observed["controls"]
                  if c["selector"] == "#native-choice")
    assert choice["type"] == "checkbox"
    assert choice["submit"] is False
    assert choice["commits_application"] is True
    independent = dom(spec, script.replace(PAGE, forms._FIXED_CONTROL_WITNESS))
    assert next(c for c in independent["controls"]
                if c["selector"] == "#native-choice")["commits_application"] is True
    # Ordinary form-owned checkbox without a submitting handler remains prep.
    for reader in (PAGE, forms._FIXED_CONTROL_WITNESS):
        normal = dom(spec, reader)
        assert next(c for c in normal["controls"]
                    if c["selector"] == "#native-choice")["commits_application"] is False


@pytest.mark.asyncio
async def test_native_hydration_after_first_field_does_not_reuse_old_root_identity(monkeypatch):
    """A native Ashby re-render preserves safe reversible writes after an approved field."""
    raw, session, filled = await native_admitted_with_one_safe_fill(monkeypatch)
    assert filled == {"#email": "test@example-co.test"}
    old_owner, new_owner = "#application-form", "#hydrated-application"
    for snapshot in (raw.current, raw.fixed):
        candidate = next(c for c in snapshot["entry"]["native_candidates"]
                         if c["selector"] == old_owner)
        candidate["selector"] = new_owner
        for control_item in snapshot["controls"]:
            if control_item.get("form_selector") == old_owner:
                control_item["form_selector"] = new_owner
                control_item["native_form_owner_selector"] = new_owner
        for control_item in candidate["controls"]:
            if control_item.get("native_owner") == old_owner:
                control_item["native_owner"] = new_owner
        snapshot["entry"]["selected_native_owner"] = new_owner
    # Only the historic root identity changed. Destination stays exact and safe.
    hydrated = await session.page()
    assert hydrated["entry"]["selected_native_owner"] == new_owner
    await session.call("fill", "#email", "--value", "test@example-co.test")
    assert [item for item in raw.calls if item[0] == "fill"] == [
        ("fill", "#email", "--value", "test@example-co.test"),
        ("fill", "#email", "--value", "test@example-co.test"),
    ]
    assert not any(item[0] == "click" for item in raw.calls)


@pytest.mark.asyncio
async def test_preprivate_js_continue_then_confirmation_creates_existing_hold(
        monkeypatch, tmp_path):
    """Round-2: JS Continue -> new thanks page -> uncertain, never second Submit.

    Real producer, code-owned entry reader and durable offline tracking adapter;
    only CLI and employer DOM are synthetic. No live application is sent.
    """
    from tests.tools.test_browser_controller_support import install_controller_session

    before = application('ashby')
    owner = before['controls'][0]['form_selector']
    continue_button = field('Continue', '#continue', required=False, tag='button',
                            type='button', form_selector=owner,
                            form_action=entry('ashby'), submit=False)
    before['controls'].insert(-1, continue_button)
    row, _, endpoint, decide = world(monkeypatch, 'ashby', [before])
    _, tracking, pdf = install_controller_session(monkeypatch, row, endpoint, tmp_path)
    # Only skip field bootstrap. The stable-entry producer and actual raw
    # application guard remain unmocked.
    monkeypatch.setattr(forms, '_bootstrap',
                        AsyncMock(side_effect=lambda _s, page, *a, **kw: page))
    decide.side_effect = [
        {'action': 'click', 'selector': '#continue'},
        {'action': 'submit', 'selector': '#submit'},
    ]
    after = inventory(url=entry('ashby'))
    after.update(text='Thank you for applying', title='Application received')
    endpoint.after_submit = after
    result = await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert result['status'] == 'uncertain', result
    assert 'check the employer portal before retrying' in result['detail'].lower()
    assert decide.await_count == 1
    assert [c for c in endpoint.calls if c[0] == 'click'] == [
        ('click', '#continue')]
    assert tracking.r.get(f'submit_hold:{PK}') is not None
    assert row['status'] == 'needs_human'
    assert row.get('gate_reason') == 'submit_uncertain'


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('shape,failed', [
    ('query', 'literal_canonical_ats_host_board_uuid_route'),
    ('credentials', 'literal_canonical_ats_host_board_uuid_route'),
    ('encoded-board', 'literal_canonical_ats_host_board_uuid_route'),
    ('wrong-job', 'canonical_ats_job_id_equals_tracked_id'),
])
async def test_tracked_ats_literal_route_gate_identifies_predicate_without_secrets(
        monkeypatch, ats, shape, failed):
    """Source row authority is required even before host-only preparation."""
    row, _, session, decision = world(monkeypatch, ats)
    original = posting(ats)
    alternate = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    if shape == 'query':
        changed = original + '?key=QUERY_SENTINEL#FRAGMENT_SENTINEL'
    elif shape == 'credentials':
        changed = original.replace('https://',
                                   'https://USER_SENTINEL:PW_SENTINEL@')
    elif shape == 'encoded-board':
        changed = original.replace('/example-co/', '/example%2dco/')
    else:
        changed = original.replace(JOB, alternate)
    row['jd_url'] = changed
    report = await forms.apply(changed, COMPANY, {}, 'synthetic', pk=PK,
                               resume_path='Resume.pdf')
    assert report['status'] == 'gate'
    assert report['reason'] == 'defect_provenance'
    assert report['navigation_provenance_diagnostic'] is True
    assert 'The tracked ATS posting is not a literal canonical job route.' in report['question']
    assert 'Failed predicate: ' + failed in report['question']
    assert 'Tracked host/path: ' + forms._APPLICATION_ENTRY[ats][0] in report['question']
    assert 'destination host/path:' in report['question']
    for secret in ('QUERY_SENTINEL', 'FRAGMENT_SENTINEL', 'USER_SENTINEL',
                   'PW_SENTINEL', '%2d', 'https://'):
        assert secret not in report['question']
    assert report['step']['url'] == 'unavailable'
    assert session.calls == [] and not decision.await_count


def test_canonical_ats_job_binding_keeps_real_refusals_and_existing_entry():
    """Diagnostic text cannot become authority for a new ATS or job route."""
    for ats in ('ashby', 'lever'):
        current = posting(ats)
        proof = forms.ApplicationEntryProvenance(
            PK, COMPANY, ats, JOB, current, 'example-co', entry(ats))
        assert forms.navigation_allowed(entry(ats), provenance=proof)
        for href in (entry(ats).replace('example-co/', 'other-co/'),
                     entry(ats).replace(JOB, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
                     entry(ats).replace('https://', 'https://user:secret@'),
                     entry(ats) + '?job=another',
                     entry(ats).replace('/example-co/', '/example%2dco/')):
            assert not forms.navigation_allowed(href, provenance=proof)



@pytest.mark.parametrize('ats', ['ashby', 'lever'])
def test_preparation_and_raw_ipc_reject_other_tenant_and_job_before_pdf(ats):
    tracked = posting(ats)
    good = entry(ats)
    wrong_tenant = good.replace('/example-co/', '/other-co/')
    wrong_job = good.replace(JOB, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    guard = forms.ApplicationIPCController(
        PK, COMPANY, tracked, '', {}, {}, [], {}, lambda: None)
    for destination in (wrong_tenant, wrong_job):
        assert not forms._preparation_origin(destination, tracked)
        assert not forms._preparation_destination_allowed(destination, tracked)
        with pytest.raises(forms.Gate, match='proven same-job'):
            guard._fresh_preparation(destination)
    assert forms._preparation_origin(good, tracked)
    assert forms._preparation_destination_allowed(good, tracked)


def test_custom_origin_cannot_borrow_foreign_ats_host_for_pdf():
    tracked = 'https://careers.example-co.test/jobs/123456'
    foreign = ('https://jobs.ashbyhq.com/other-co/'
               'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/application')
    assert not forms._preparation_origin(foreign, tracked)
    assert not forms._preparation_destination_allowed(foreign, tracked)
    assert forms._preparation_origin(tracked, tracked)
    # Raw upload verifies this route before considering any private receiver.
    guard = forms.ApplicationIPCController(
        'example-co#123456', COMPANY, tracked, '', {}, {}, [], {}, lambda: None)
    with pytest.raises(forms.Gate, match='proven same-job'):
        guard._fresh_preparation(foreign)


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
def test_entry_reader_redirect_is_typed_and_bounded(ats):
    tracked = posting(ats)
    proof = forms.ApplicationEntryProvenance(
        PK, COMPANY, ats, JOB, tracked, 'example-co', entry(ats))
    target = ('https://foreign.example.test/other-job'
              '?job=QUERY_SENTINEL#FRAGMENT_SENTINEL')
    page = application(ats)
    page['url'] = target
    with pytest.raises(forms.NavigationProvenanceGate) as caught:
        forms._entry_safety(page, lambda u: forms._preparation_origin(u, tracked), proof)
    detail = str(caught.value)
    assert 'Failed predicate: same_tracked_employer_https_origin' in detail
    assert 'destination host/path: foreign.example.test/other-job' in detail
    assert 'QUERY_SENTINEL' not in detail
    assert 'FRAGMENT_SENTINEL' not in detail


def test_greenhouse_reader_redirect_is_typed_and_bounded():
    from tools.claude_chrome import GreenhouseJobAlias
    tracked = 'https://boards.greenhouse.io/example-co/jobs/123456'
    proof = GreenhouseJobAlias(
        'example-co#123456', COMPANY, '123456', 'greenhouse',
        tracked, tracked, 'example-co',
        'https://job-boards.greenhouse.io/example-co/jobs/123456')
    target = 'https://foreign.example.test/other-job?job=QUERY_SENTINEL'
    with pytest.raises(forms.NavigationProvenanceGate) as caught:
        forms._greenhouse_safety(
            {'url': target},
            lambda u: forms._preparation_origin(u, tracked), proof)
    detail = str(caught.value)
    assert 'Failed predicate: same_tracked_employer_https_origin' in detail
    assert 'destination host/path: foreign.example.test/other-job' in detail
    assert 'QUERY_SENTINEL' not in detail
