"""Synthetic Ashby upload and final-Submit regression after removing widget recognizers.

Ordinary non-Submit clicks cannot mint a document upload receipt. A row PDF
is attached only through the guarded producer, once per attempt, and a
missing/incomplete final readback still blocks Submit before its raw click.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import (
    WAIT, _no_production_commit, _production_submit_click,
)


async def test_non_submit_upload_widget_click_is_ordinary_preparation():
    from tests.tools.test_browser_formless import observed

    page, _ = observed(movePane=True)
    session = SimpleNamespace(call=AsyncMock())
    await forms.execute(
        session, page, {'action': 'click', 'selector': '#dropzone'},
        facts={}, filled={}, resume_path='', company='example-co',
        jd_text='', resume_tex='', github='', allow_click=True)
    session.call.assert_awaited_once_with('click', '#dropzone')
    # No deprecated upload-tree recognizer, label class or per-action grant.


async def test_second_actual_resume_upload_is_rejected_without_repeat_or_submit(
        monkeypatch, tmp_path):
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    repeated_upload = {'action': 'upload', 'selector': '#_systemfield_resume'}
    # The producer has already uploaded once. Model repetition is a no-op;
    # the next explicit owner inspection must still prohibit any Submit.
    result = await run_apply([
        repeated_upload, {'action': 'gate', 'question': 'Synthetic next decision'}])
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable', result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed
    assert not guard.upload_pending
    assert decide.await_count == 2
    _no_production_commit(raw, transport, row)


async def test_late_unique_resume_receiver_uploads_once_but_incomplete_form_denies_submit(
        monkeypatch, tmp_path):
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    # Initially missing file receiver appears on the first decision. The
    # next iteration uploads it without a model-chosen upload-control click.
    result = await run_apply([WAIT, click], late=True)
    assert result['status'] == 'gate', result
    assert result.get('reason') == 'defect_pre_submit_item_2', result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed
    assert not guard.upload_pending
    _no_production_commit(raw, transport, row)
