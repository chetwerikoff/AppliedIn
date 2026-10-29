"""FF-243a: the formless execute-readback names what failed, never a value.

Offline doubles only: the PAGE and fixed witness come from the synthetic DOM
readers used by test_browser_formless. No browser, profile or network.
"""
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, posting
from tests.tools.test_browser_final_submit_grants import (
    _isolated_reader_boolean, _rooted_boolean_reader)
from tests.tools.test_browser_formless import action_sequence, admitted, approve_fields
from tests.tools.test_browser_reversible_gates import (
    _answer, _apply, _fixture, _only_required, _record_boolean_press)

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


@pytest.mark.parametrize('pressed', [None, '#boolean-yes'],
                         ids=['click-not-registered', 'other-member-pressed'])
@pytest.mark.asyncio
async def test_boolean_readback_requires_the_clicked_member_pressed(
        monkeypatch, tmp_path, pressed):
    # A <button> never reads back a value, so its proof is the pressed member
    # of the bound pair; any other outcome of the click is a Gate.
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    if pressed:
        _record_boolean_press(endpoint, pressed)
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['status'] == 'gate'
    assert 'did not read back exactly' in result['question']
    assert 'type=button;' in result['question'] and 'failed=selected;' in result['question']
    assert [c for c in endpoint.calls if c[0] == 'click'] == [('click', '#boolean-no')]


def test_boolean_member_carrying_a_value_is_not_a_live_ashby_shape():
    # The fabricated double once wrote value/has_value onto the pressed
    # button. No live page produces that, so it fails closed.
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    pressed = next(c for c in page['controls'] if c.get('checked') is True)
    assert pressed['value'] == '' and pressed['has_value'] is False
    pressed.update(value=pressed['label'].casefold(), has_value=True)
    with pytest.raises(forms.Gate, match='pair is incomplete or unsafe'):
        forms._boolean_pair(page, pressed)
