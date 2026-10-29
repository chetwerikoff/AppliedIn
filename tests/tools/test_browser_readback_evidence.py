"""FF-243a: the formless execute-readback names what failed, never a value.

Offline doubles only: the PAGE and fixed witness come from the synthetic DOM
readers used by test_browser_formless. No browser, profile or network.
"""
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, posting
from tests.tools.test_browser_formless import action_sequence, admitted, approve_fields

_PRIVATE = 'PRIVATE_SYNTHETIC_VALUE'


async def test_readback_gate_names_the_failed_comparison_never_a_value(monkeypatch, caplog):
    # A bare Gate stalled every formless Ashby row for an evening with no
    # evidence of which receiver kind failed. The evidence is shape only:
    # values are the owner's personal data.
    row, raw, session, page, provenance = await admitted(monkeypatch)
    facts = approve_fields(row, page, raw.fixed, provenance)
    original = raw.call

    async def corrupt(method, *args, **kw):
        result = await original(method, *args, **kw)
        if method == 'fill':
            for snapshot in (raw.current, raw.fixed):
                for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                    if c['selector'] == args[0]:
                        c['value'] = _PRIVATE
        return result

    raw.call = corrupt
    caplog.set_level(logging.WARNING)
    with pytest.raises(forms.Gate, match='did not read back exactly') as refusal:
        await forms.execute(session, page, action_sequence()[0], facts=facts, filled={},
                            resume_path='Resume.pdf', company=COMPANY, jd_text='',
                            resume_tex='', github='', pk=PK, job_url=posting('ashby'),
                            provenance=provenance)
    text = str(refusal.value)
    assert 'verb=fill; tag=input;' in text
    assert 'failed=value;' in text and 'folded_equal=False' in text
    assert f'read_len={len(_PRIVATE)};' in text
    logged = '\n'.join(r.getMessage() for r in caplog.records
                       if 'execute-readback mismatch' in r.getMessage())
    assert 'failed=value;' in logged and 'label=' in logged
    approved = [v for v in facts.values() if isinstance(v, str) and len(v) > 2]
    assert approved
    for surface in (text, logged):
        assert _PRIVATE not in surface
        assert not [v for v in approved if v in surface]
