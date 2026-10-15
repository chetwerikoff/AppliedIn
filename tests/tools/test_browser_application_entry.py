"""Canonical ATS entry uses independent persisted authority, never page hints.

All records, DOM and sessions are synthetic. The fixed loopback fixture cannot
prove HTTPS ATS routing, so both vendor shapes are exercised offline here.
"""
from copy import deepcopy
import asyncio
from unittest.mock import AsyncMock

import pytest

from core.ids import make_pk
from discovery.watchlist import CompanyConfig
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import field, submit, inventory, witness

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
        self.calls.append(('navigate', url))
        self.current = deepcopy(next(self.snapshots, self.current))
        return deepcopy(self.current)

    async def page(self):
        self.pages += 1
        self.current = deepcopy(next(self.snapshots, self.current))
        if self.on_page:
            await self.on_page(self)
        return deepcopy(self.current)

    async def call(self, method, *args):
        self.calls.append((method, *args))
        if method == 'evaluate':
            assert args == (forms._FIXED_CONTROL_WITNESS,)
            if self.on_evaluate:
                await self.on_evaluate(self)
            return {'value': witness(self.current)}
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
    assert 'explicit ATS board' in result['question']
    assert session.calls == []
    assert not decision.await_count


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
    assert 'native form receiver not proven' in report['question']
    assert not decide.await_count
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
    elif shape == 'search':
        page['controls'] = [field('Search jobs', type='search'), submit('Search jobs')]
    elif shape == 'no-marker':
        page['controls'] = page['controls'][:1]
    _, _, session, decide = world(monkeypatch, pages=[page])
    if shape == 'readonly':
        original = witness
        async def call(method, *args):
            assert method == 'evaluate'
            session.calls.append((method, *args))
            result = original(session.current)
            result['controls'][0]['readonly'] = True
            return {'value': result}
        session.call = call
    monkeypatch.setattr(forms, '_APPLICATION_WAIT_SECONDS', 0.02)
    report = await attempt()
    assert report['status'] == 'gate'
    assert 'no qualifying application form after bounded wait' in report['question']
    assert not decide.await_count
    no_mutations(session)


async def test_shell_then_late_changing_form_needs_own_equal_fresh_pair(monkeypatch):
    shell = application('lever', controls=[])
    first, second = application('lever'), application('lever')
    second['controls'][0]['label'] = 'Email address'
    # navigate shell, fresh shell+witness, fresh first+witness, second twice.
    _, _, session, _ = world(monkeypatch, 'lever',
                            [shell, shell, shell, first, first, second, second, second, second])
    bootstrap = AsyncMock(side_effect=forms.Gate('ready'))
    monkeypatch.setattr(forms, '_bootstrap', bootstrap)
    report = await attempt('lever')
    assert 'ready' in report['question']
    assert session.pages == 8
    assert bootstrap.call_args.args[1]['controls'][0]['label'] == 'Email address'
    no_mutations(session)


async def test_changing_qualifying_forms_end_in_unstable_gate(monkeypatch):
    _, _, session, _ = world(monkeypatch)
    async def change(s):
        # Keep each PAGE/witness pair coherent but change between probes.
        s.current['controls'][0]['label'] = f'Email {(s.pages - 1) // 2}'
    session.on_page = change
    monkeypatch.setattr(forms, '_APPLICATION_WAIT_SECONDS', 0.03)
    # Short sleeps allow several qualifying but different probes in the budget.
    original_sleep = asyncio.sleep
    async def short_sleep(delay):
        await original_sleep(0.001)
    monkeypatch.setattr(forms.asyncio, 'sleep', short_sleep)
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
    report = await asyncio.wait_for(attempt(), timeout=0.3)
    assert report['status'] == 'gate'
    assert 'application form inventory read timed out/unavailable' in report['question']
    assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('unsafe', ['inventory_verified', 'truncated', 'opaque_controls',
                                   'shadow_roots', 'unsupported_frames', 'controls'])
async def test_unsafe_inventory_gates_immediately(monkeypatch, unsafe):
    page = application('ashby')
    page[unsafe] = (False if unsafe == 'inventory_verified' else
                    [None] if unsafe == 'controls' else True)
    _, _, session, decide = world(monkeypatch, pages=[page])
    report = await attempt()
    assert report['status'] == 'gate'
    assert 'inventory' in report['question']
    assert session.pages == 0  # unsafe navigate result also Gates immediately
    assert not decide.await_count
    no_mutations(session)


@pytest.mark.parametrize('drift', ['board', 'ats', 'pk', 'company', 'job_id', 'jd_url', 'route'])
@pytest.mark.parametrize('when', ['read', 'resume'])
async def test_current_identity_rechecked_after_read_and_on_resume(monkeypatch, drift, when):
    row, configs, session, decide = world(monkeypatch)
    if when == 'resume':
        first = await attempt()
        assert first['form_question']
        q = first['form_question']
        row['human_approved_answers'] = {q['label']: {**q, 'value': 'test@example.test'}}
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
    report = await attempt()
    assert report['status'] == 'gate'
    assert report.get('form_question') is None  # identity Gate, not a fresh answer prompt
    assert not decide.await_count
    no_mutations(session)
    if when == 'resume' and drift != 'route':
        assert session.calls == []


@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('redirect', [False, True])
async def test_submit_confirmation_needs_exact_route_and_independent_new_proof(
        monkeypatch, ats, redirect):
    _, _, session, decide = world(monkeypatch, ats)
    # Isolate result classification: existing pre-submit guards are tested in the
    # original controller suite. The real exact-route provenance stays active.
    monkeypatch.setattr(forms, '_bootstrap', AsyncMock(side_effect=lambda s, p, *a, **kw: p))
    monkeypatch.setattr(forms, 'check_form', lambda *a: None)
    monkeypatch.setattr(forms, 'check_approvals', lambda *a, **kw: None)
    hold = []
    monkeypatch.setattr(forms, 'hold_possible_submission', lambda *a, **kw: hold.append(a))
    monkeypatch.setattr(forms, 'check_before_committing_click', lambda *a, **kw: None)
    decide.return_value = {'action': 'submit', 'selector': '#submit'}
    session.after_submit = inventory(url=entry(ats) + ('/thanks' if redirect else ''))
    session.after_submit['text'] = 'Thank you for applying'
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(forms.asyncio, 'sleep', fast_sleep)
    result = await attempt(ats)
    assert result['status'] == ('uncertain' if redirect else 'applied')
    assert len(hold) == 1
    assert [c for c in session.calls if c[0] == 'click'] == [('click', '#submit')]
    if redirect:
        assert 'Check the portal before retrying' in result['detail']


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
    original_navigate = session.navigate
    async def slow_navigation(url):
        clock[0] += 40
        return await original_navigate(url)
    session.navigate = slow_navigation
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
        else:
            await forms.execute(
                session, page, {'action': verb, 'selector': page['controls'][0]['selector'],
                                'fact': 'Email'},
                facts={'Email': 'test@example.test'}, filled={}, resume_path='Resume.pdf',
                company=COMPANY, jd_text='', resume_tex='', github='', pk=PK,
                job_url=posting('ashby'), provenance=proof)
    no_mutations(session)
