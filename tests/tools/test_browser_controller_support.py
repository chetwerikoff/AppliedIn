"""Offline endpoint adapter for tests of the real application IPC producer.

Only session startup and the CLI endpoint are replaced. Dispatch, value, PDF,
hold, final-submit and raw-call protections remain production code.
"""
from copy import deepcopy
from types import SimpleNamespace

import fakeredis

from core import apply_queue
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import posting_guard
from tests.tools.test_browser_bootstrap import witness


_SESSION = bsk.Session
_CHECK_DISPATCH = forms.check_dispatch


def install_controller_session(monkeypatch, row, endpoint, tmp_path, *, scope='entry'):
    """Supply a complete synthetic dispatch and bind through normal apply()."""
    from agent import run

    pdf = tmp_path / 'Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\nSynthetic row-owned artifact\n')
    row.setdefault('job_id', row['pk'].partition('#')[2])
    row.setdefault('resume_tex_key', 'synthetic-artifact')
    row.setdefault('resume_seed', 'synthetic-seed')
    checked = posting_guard.analyze('Responsibilities and qualifications for a synthetic role.')
    row.setdefault('jd_text', checked['text'])
    row.setdefault('posting_guard', checked['posting_guard'])
    row.setdefault('title', (getattr(endpoint, 'current', None) or {}).get('title', 'Synthetic role'))

    class Tracking:
        r = fakeredis.FakeRedis(decode_responses=True)

        def get(self, pk):
            assert pk == row['pk']
            return deepcopy(row)

        get_current = get

        def set_status(self, pk, status, **attrs):
            assert pk == row['pk']
            row.update(status=getattr(status, 'value', status), **attrs)

        def update_if_status(self, pk, expected_status, updates, *,
                             expected_reader=None):
            # Model the production storage adapter's conditional post-hold
            # transition, not a permissive unconditional status overwrite.
            # The real final-click guard must still independently re-read
            # status, hold, approved answers, posting and leased dispatch.
            assert pk == row['pk']
            if row.get('status') != getattr(expected_status, 'value', expected_status):
                return False
            if any(row.get(key) != value
                   for key, value in (expected_reader or {}).items()):
                return False
            row.update({key: getattr(value, 'value', value)
                        for key, value in updates.items()})
            return True

    tracking = Tracking()
    tracking.r.sadd(apply_queue._BUSY, row['company'].strip().lower())
    tracking.r.sadd(apply_queue._BUSY_PKS, row['pk'])
    monkeypatch.setattr('core.stores.make_stores', lambda: SimpleNamespace(tracking=tracking))
    monkeypatch.setattr(run, 'seed_fingerprint', lambda: 'synthetic-seed')
    monkeypatch.setattr(run, '_resume_pdf_path', lambda current: str(pdf))
    monkeypatch.setattr(forms, 'check_dispatch', _CHECK_DISPATCH)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')

    async def command(*args, **kwargs):
        if args[:2] == ('session', 'stop'):
            return {}
        assert args[-2:] == ('--session', 'synthetic-session')
        operation = args[:-2]
        if scope == 'plain':
            if operation[0] == 'evaluate':
                page = await endpoint.page()
                return {'value': witness(page) if operation[1] == forms._FIXED_CONTROL_WITNESS
                        else deepcopy(page)}
            if operation[0] == 'navigate':
                await endpoint.navigate(operation[1])
                return {}
        elif operation == ('evaluate', bsk.PAGE):
            operation = ('evaluate', forms.ENTRY_PAGE)
        elif operation == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            operation = ('evaluate', forms._ENTRY_FIXED_CONTROL_WITNESS)
        return await endpoint.call(*operation, **kwargs)

    class OfflineSession(_SESSION):
        async def __aenter__(self):
            self.id = 'synthetic-session'
            return self

    session = OfflineSession('apply')
    monkeypatch.setattr(bsk, 'command', command)
    monkeypatch.setattr(bsk, 'Session', lambda *args: session)
    return session, tracking, str(pdf)
