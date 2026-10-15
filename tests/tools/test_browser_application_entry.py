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
        pytest.fail('Entry must bypass generic navigation warm-up')

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
                    owners = {c.get('form_selector') for c in controls if isinstance(c, dict)
                              and forms._application_marker([c])}
                    owner = next(iter(owners)) if len(owners) == 1 else None
                    value['entry'] = dict(frames=[], responses=[], response_error=False,
                                          helper_challenge=False, root=(
                        dict(selector=owner, action=value['url']) if owner else None))
                    self.current = deepcopy(value)
                return {'value': value}
            assert args in {(forms._ENTRY_FIXED_CONTROL_WITNESS,), (forms._FIXED_CONTROL_WITNESS,)}
            if self.on_evaluate:
                await self.on_evaluate(self)
            value = witness(self.current)
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
        page['controls'] = [field('Search jobs', type='search', form_action=entry('ashby')),
                            submit('Search jobs', form_action=entry('ashby'))]
    elif shape == 'no-marker':
        page['controls'] = page['controls'][:1]
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
    assert 'no qualifying application form after bounded wait' in report['question']
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
    if drift:
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


def dom(spec, script=None):
    import json
    import subprocess
    from pathlib import Path
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('entry_dom_fixture.js')), json.dumps(spec),
         script or forms.ENTRY_PAGE], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


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
    assert 'route or current row/config changed' in result['question']
    assert session.pages == 1
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
