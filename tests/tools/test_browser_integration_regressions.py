"""Exercise the production application IPC controller with offline stores/CLI.

The spies preserve preparation ordering and the real hold/approval/PDF checks;
no browser, network, provider, running Redis or owner artifact is involved.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest

from core import apply_queue
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import posting_guard, submit_hold
from tests.tools.test_browser_bootstrap import field, inventory, submit, witness


COMPANY = 'example-co'
PK = COMPANY + '#1'
URL = 'https://employer.test/job/1'


def _world(monkeypatch, tmp_path, *, drift=None, ats=''):
    from agent import run
    from discovery.watchlist import CompanyConfig

    job = '12345678-1234-4234-8234-123456789abc' if ats else '1'
    pk = COMPANY + '#' + job
    url = f'https://{forms._APPLICATION_ENTRY[ats][0]}/{COMPANY}/{job}' if ats else URL
    destination = url + '/' + forms._APPLICATION_ENTRY[ats][1] if ats else url
    configs = [CompanyConfig(name=COMPANY, ats=ats, board=COMPANY)] if ats else []
    monkeypatch.setattr('discovery.watchlist.load_watchlist', lambda path: configs)

    pdf = tmp_path / 'Test User Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\nSynthetic row artifact\n')
    email = field(selector='#email', label='Email', form_action=destination)
    resume = field(selector='#resume', label='Resume', type='file', required=False,
                   form_action=destination, files=[])
    button = submit(form_action=destination)
    page = {**inventory(email, resume, button, url=destination), 'title': 'Synthetic role'}
    checked = posting_guard.analyze('Responsibilities and qualifications for a synthetic role.')
    row = dict(pk=pk, company=COMPANY, job_id=job, jd_url=url,
               title=page['title'], status='submitting', ats=ats,
               resume_tex_key='synthetic', resume_seed='synthetic-seed',
               jd_text=checked['text'], posting_guard=checked['posting_guard'],
               human_approved_answers={'Email': {
                   'value': 'test@example.test', 'selector': '#email', 'url': destination,
                   'label': 'Email', 'question': email['question'], 'tag': email['tag'],
                   'type': email['type'], 'form_selector': email['form_selector'],
                   'form_action': destination}})

    class Tracking:
        r = fakeredis.FakeRedis(decode_responses=True)

        def get(self, observed_pk):
            assert observed_pk == pk
            # Deliberately share nested objects: a hold callback must not be
            # able to rewrite the guard's original comparison evidence.
            return row

        get_current = get

        def set_status(self, observed_pk, status, **attrs):
            assert observed_pk == pk
            row.update(status=getattr(status, 'value', status), **attrs)

        def update_if_status(self, observed_pk, status, updates, *,
                             expected_reader=None):
            # The production store performs a conditional WATCH-backed update.
            # Exercise the same state transition in this offline fake instead
            # of silently replacing it with the old unconditional set_status.
            assert observed_pk == pk
            if row['status'] != getattr(status, 'value', status):
                return False
            if any(row.get(key) != value
                   for key, value in (expected_reader or {}).items()):
                return False
            row.update({key: getattr(value, 'value', value)
                        for key, value in updates.items()})
            # Inject drift *after* this attempt's durable hold/transition, so
            # the raw-click producer must reject it without browser IPC.
            if drift:
                drift(row, page, self.r, configs)
            return True

    tracking = Tracking()
    tracking.r.sadd(apply_queue._BUSY, COMPANY)
    tracking.r.sadd(apply_queue._BUSY_PKS, pk)
    monkeypatch.setattr('core.stores.make_stores', lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(run, 'seed_fingerprint', lambda: 'synthetic-seed')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda current: str(pdf))
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')
    effects = []

    async def command(*args, **kwargs):
        op = args[0]
        if op == 'evaluate':
            script = args[1]
            if script in {forms._FIXED_CONTROL_WITNESS,
                          forms._ENTRY_FIXED_CONTROL_WITNESS}:
                return {'value': witness(page)}
            return {'value': deepcopy(page)}
        if op == 'session':
            assert args[:2] == ('session', 'stop')
            return {}
        effects.append(args[:2])
        if op == 'fill':
            email.update(value=args[3], has_value=True)
        elif op == 'upload':
            resume['files'] = [Path(args[3]).name]
        elif op == 'click':
            assert submit_hold.is_held(pk, tracking=tracking)
            assert row['status'] == 'needs_human'
            page.update(controls=[], text='Thank you for applying')
        else:
            assert op == 'navigate'
        return {}

    monkeypatch.setattr(bsk, 'command', command)

    class OfflineSession(bsk.Session):
        async def __aenter__(self):
            self.id = 'synthetic-session'
            return self

    raw = OfflineSession('apply')
    monkeypatch.setattr(bsk, 'Session', lambda kind: raw)
    return row, page, tracking, raw, effects, str(pdf)


@pytest.mark.asyncio
@pytest.mark.parametrize('drift', [None, 'approval', 'job', 'url', 'ats', 'lease', 'posting',
                                 'answer', 'attachment', 'receiver'])
async def test_actual_apply_raw_producer_checks_own_hold_and_drift(monkeypatch, tmp_path, drift):
    """A valid own hold permits one IPC; hold-time authority changes permit none."""
    def change(row, page, redis, configs):
        if drift == 'approval':
            row['human_approved_answers']['Email']['value'] = 'other@example.test'
        elif drift == 'job':
            row['job_id'] = '2'
        elif drift == 'url':
            row['jd_url'] = URL + '/other'
        elif drift == 'ats':
            row['ats'] = 'ashby'
        elif drift == 'lease':
            redis.srem(apply_queue._BUSY_PKS, PK)
        elif drift == 'posting':
            row['posting_guard'] = {}
        elif drift == 'answer':
            next(c for c in page['controls'] if c['selector'] == '#email')['value'] = 'other@example.test'
        elif drift == 'attachment':
            next(c for c in page['controls'] if c['selector'] == '#resume')['files'] = []
        elif drift == 'receiver':
            next(c for c in page['controls'] if c['selector'] == '#submit')['formaction'] = (
                'https://foreign.example.test/receive')

    row, page, tracking, raw, effects, pdf = _world(
        monkeypatch, tmp_path, drift=change if drift else None)
    actions = iter([
        {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
        {'action': 'upload', 'selector': '#resume'},
        {'action': 'submit', 'selector': '#submit'},
    ])

    async def decision(*args, **kwargs):
        return next(actions)

    async def no_wait(*args):
        return None

    monkeypatch.setattr(bsk, 'decision', decision)
    monkeypatch.setattr(forms.asyncio, 'sleep', no_wait)
    result = await forms.apply(URL, COMPANY, {'Email': 'test@example.test'},
                               'synthetic', pk=PK, resume_path=pdf)
    assert result['status'] == ('gate' if drift else 'applied'), result
    assert effects == [('navigate', URL), ('fill', '#email'), ('upload', '#resume')] + (
        [] if drift else [('click', '#submit')])
    assert submit_hold.is_held(PK, tracking=tracking)
    assert row['status'] == 'needs_human'
    # Session teardown owns only the per-attempt snapshot, never the row PDF.
    assert Path(pdf).is_file()
    assert not Path(raw._application_controller.resume_path).exists()


@pytest.mark.parametrize('change', ['job_id', 'company', 'pk', 'jd_url'])
def test_preparation_current_keeps_canonical_row_identity_without_tenant_pin(monkeypatch, change):
    from tests.tools.test_browser_application_entry import JOB, PK, COMPANY, posting, entry

    row = dict(pk=PK, company=COMPANY, ats='ashby', job_id=JOB,
               jd_url=posting('ashby'), status='submitting')
    provenance = forms.ApplicationEntryProvenance(
        PK, COMPANY, 'ashby', JOB, posting('ashby'), 'example-co', entry('ashby'))
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    monkeypatch.setattr(forms, '_configured_alias', lambda *a, **kw: pytest.fail(
        'Preparation must not require a current tenant pin'))
    assert forms._preparation_current(provenance)
    row[change] = 'other'
    assert not forms._preparation_current(provenance)


def test_tracked_hash_route_allows_unchanged_identity_only(monkeypatch):
    """A tracked SPA fragment is valid; changing its job is never origin proof."""
    source = URL + '#/jobs/one'
    row = dict(pk=PK, company=COMPANY, job_id='1', jd_url=source, status='submitting')
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    forms._tracked_preparation_job(PK, COMPANY, source)
    assert forms._preparation_origin(source, source)
    assert not forms._preparation_origin(URL + '#/jobs/two', source)
    assert not forms._preparation_origin('https://jobs.ashbyhq.com/example-co/#foreign', source)


@pytest.mark.asyncio
async def test_raw_tracked_hash_navigation_keeps_executable_fragments_forbidden(monkeypatch, tmp_path):
    """The raw GET classifier must not reject every otherwise-safe SPA route."""
    row, page, tracking, raw, effects, pdf = _world(monkeypatch, tmp_path)
    source = URL + '#/jobs/one'
    row['jd_url'] = source
    controller = forms.ApplicationIPCController(
        PK, COMPANY, source, pdf, {}, {}, [], {}, lambda: None)
    raw.id = 'synthetic-session'
    raw.bind_application_controller(controller)
    await raw.application_effect('navigate', source)
    assert effects == [('navigate', source)]
    assert forms._committing_get_route(URL + '#requestSubmit')
    with pytest.raises(forms.Gate):
        await raw.application_effect('navigate', URL + '#/jobs/two')
    assert effects == [('navigate', source)]


def test_loopback_observation_does_not_authorize_application_preparation(monkeypatch):
    """Retain offline reader compatibility without expanding raw IPC origins."""
    source = 'http://127.0.0.1:18789/fixture/example-co/job/1'
    row = dict(pk=PK, company=COMPANY, job_id='1', jd_url=source, status='submitting')
    monkeypatch.setattr(forms, '_row', lambda pk: row)
    assert forms._preparation_destination_allowed(source, source)
    assert not forms._preparation_destination_allowed(source + '?job=2', source)
    assert not forms._preparation_origin(source, source)
    with pytest.raises(forms.Gate, match='safe HTTPS'):
        forms._tracked_preparation_job(PK, COMPANY, source)


@pytest.mark.asyncio
@pytest.mark.parametrize('ats', ['ashby', 'lever'])
@pytest.mark.parametrize('drift', [False, True])
async def test_raw_final_guard_rechecks_tenant_after_own_hold(monkeypatch, tmp_path, ats, drift):
    """A formerly valid pin cannot authorize a click after hold-time board drift."""
    def change(row, page, redis, configs):
        if drift:
            configs[0].board = 'other-co'

    row, page, tracking, raw, effects, pdf = _world(
        monkeypatch, tmp_path, ats=ats, drift=change)
    controller = forms.ApplicationIPCController(
        row['pk'], COMPANY, row['jd_url'], pdf, {'Email': 'test@example.test'},
        {}, [], {'url': page['url']}, lambda: None, seal_stage=True)
    raw.id = 'synthetic-session'
    raw.bind_application_controller(controller)
    try:
        await raw.application_effect('fill', '#email', '--value', 'test@example.test')
        controller.filled['#email'] = 'test@example.test'
        controller.history.append(dict(
            action='fill', selector='#email', label='Email', question='',
            fact='Email', approved_value='test@example.test', url=page['url']))
        await raw.application_effect('upload', '#resume', '--file',
                                     controller.resume_path, timeout=130)
        if drift:
            with pytest.raises(forms.Gate, match='tenant or board changed after the hold'):
                await raw.application_effect('click', '#submit')
        else:
            await raw.application_effect('click', '#submit')
        assert effects == [('fill', '#email'), ('upload', '#resume')] + (
            [] if drift else [('click', '#submit')])
        assert submit_hold.is_held(row['pk'], tracking=tracking)
    finally:
        controller.release_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('receiver', ['form_action', 'formaction'])
async def test_final_receiver_change_never_creates_hold_or_click(monkeypatch, tmp_path, receiver):
    """A current job page cannot lend its identity to a foreign submit receiver."""
    row, page, tracking, raw, effects, pdf = _world(monkeypatch, tmp_path)
    button = next(c for c in page['controls'] if c['selector'] == '#submit')
    button[receiver] = 'https://foreign.example.test/receive'
    controller = forms.ApplicationIPCController(
        PK, COMPANY, URL, pdf, {}, {}, [], {}, lambda: None)
    raw.id = 'synthetic-session'
    raw.bind_application_controller(controller)
    with pytest.raises(forms.Gate, match='Final submit receiver'):
        await raw.application_effect('click', '#submit')
    assert effects == []
    assert not submit_hold.is_held(PK, tracking=tracking)


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_formless_apply_uses_bound_raw_controller_not_legacy_permit(
        monkeypatch, tmp_path, confirmed):
    """The real formless facade must reach the same production raw-IPC verdict."""
    from tests.tools.test_browser_formless import (
        RootSession, observed, approve_fields, action_sequence, effect_calls,
    )

    row, _, tracking, raw, _, pdf = _world(monkeypatch, tmp_path, ats='ashby')
    current, fixed = observed()
    spy = RootSession(current, fixed)
    provenance = forms._configured_alias(row['pk'], COMPANY, row['jd_url'])
    facts = approve_fields(row, current, fixed, provenance)
    if confirmed:
        after, after_fixed = deepcopy(current), deepcopy(fixed)
        for snapshot in (after, after_fixed):
            snapshot.update(controls=[], text='Thank you for applying', title='Application received')
            snapshot['entry'].update(root=None, formless=None, root_evidence=[])
        spy.after_click = after, after_fixed

    async def command(*args, **kwargs):
        if args[0] == 'session':
            return {}
        # The real Session owns --session; the fake endpoint sees only its
        # ordinary operation, exactly as the CLI would dispatch it.
        assert args[-2:] == ('--session', 'synthetic-session')
        return await spy.call(*args[:-2], **kwargs)

    actions = iter(action_sequence())

    async def decision(*args, **kwargs):
        return next(actions)

    async def no_wait(*args):
        return None

    monkeypatch.setattr(bsk, 'command', command)
    monkeypatch.setattr(bsk, 'decision', decision)
    monkeypatch.setattr(forms.asyncio, 'sleep', no_wait)
    result = await forms.apply(row['jd_url'], COMPANY, facts, 'synthetic',
                               pk=row['pk'], resume_path=pdf)
    effects = [c[:2] for c in effect_calls(spy.calls)]
    assert effects == [('fill', '#_systemfield_name'), ('fill', '#_systemfield_email'),
                       ('set', Path(pdf).name), ('click', '#structural-submit')], result
    assert result['status'] == ('applied' if confirmed else 'uncertain'), result
    assert submit_hold.is_held(row['pk'], tracking=tracking)
    assert raw._application_controller.committed


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['fill', 'upload'])
@pytest.mark.parametrize('wall', ['password', 'challenge'])
async def test_raw_preparation_never_bypasses_authentication_wall(
        monkeypatch, tmp_path, action, wall):
    """Generic fallback skips old shape certificates, never login/CAPTCHA safety."""
    row, page, tracking, raw, effects, pdf = _world(monkeypatch, tmp_path)

    controller = forms.ApplicationIPCController(
        PK, COMPANY, URL, pdf, {'Email': 'test@example.test'}, {}, [], {}, lambda: None)
    raw.bind_application_controller(controller)
    raw.id = 'synthetic-session'
    if wall == 'password':
        page['controls'].append(field(selector='#password', label='Password', type='password'))
    else:
        page['entry'] = {'helper_challenge': True}
    # Direct producer calls cannot borrow execute()/bootstrap's earlier checks.
    with pytest.raises(forms.Gate, match='Sign in yourself' if wall == 'password' else 'challenge'):
        await raw.application_effect(
            action, '#email' if action == 'fill' else '#resume',
            '--value' if action == 'fill' else '--file',
            'test@example.test' if action == 'fill' else controller.resume_path,
            **({} if action == 'fill' else {'timeout': 130}))
    assert effects == []
    assert not controller.upload_completed
    assert not controller.committed
    assert not submit_hold.is_held(PK, tracking=tracking)


@pytest.mark.asyncio
async def test_missing_greenhouse_metadata_prepares_but_cannot_omit_final_alias(
        monkeypatch, tmp_path):
    """ARCH21:58 / AC14: missing row ATS metadata never waives final tenant proof."""
    row, page, tracking, raw, effects, pdf = _world(monkeypatch, tmp_path)
    url = 'https://job-boards.greenhouse.io/other-co/jobs/1'
    row['jd_url'] = page['url'] = url
    for control in page['controls']:
        control['form_action'] = url
    row['human_approved_answers']['Email'].update(url=url, form_action=url)
    actions = iter([dict(action='fill', selector='#email', fact='Email'),
                    dict(action='upload', selector='#resume'),
                    dict(action='submit', selector='#submit')])
    async def decide(*args, **kwargs):
        return next(actions)
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await forms.apply(url, COMPANY, {'Email': 'test@example.test'},
                               'synthetic', pk=PK, resume_path=pdf)
    assert result['status'] == 'gate', result
    assert result['reason'] == 'defect_tenant'
    assert effects == [('navigate', url), ('fill', '#email'), ('upload', '#resume')]
    assert not raw._application_controller.committed
    assert not submit_hold.is_held(PK, tracking=tracking)


@pytest.mark.asyncio
async def test_upload_preserves_raw_challenge_refusal(monkeypatch, tmp_path):
    """A raw no-IPC CAPTCHA refusal must retain its named manual next step."""
    row, page, tracking, raw, effects, pdf = _world(monkeypatch, tmp_path)
    async def decide(*args, **kwargs):
        page['entry'] = {'helper_challenge': True}
        return dict(action='upload', selector='#resume')
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await forms.apply(URL, COMPANY, {'Email': 'test@example.test'},
                               'synthetic', pk=PK, resume_path=pdf)
    assert result['status'] == 'gate', result
    assert 'Visible application challenge requires manual authentication' in result['question']
    assert effects == [('navigate', URL)]
    assert not raw._application_controller.upload_completed
    assert not submit_hold.is_held(PK, tracking=tracking)