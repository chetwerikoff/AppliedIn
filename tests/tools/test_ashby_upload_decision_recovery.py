"""Issue #230: the model's upload decision runs the one server-owned upload.

Live, on an Ashby page whose formless root carries the sibling identity
mismatch and a container-scoped upload tree, the model asked to upload three
times and the run hit the repeat gate. Its upload was deferred to a Submit it
never chose while the résumé field was empty. These cases run the real raw
producer and controller (no fixture context) on that reversible route.
"""
import logging

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import _no_production_commit, _production_submit_click

UPLOAD = {'action': 'upload', 'selector': '#_systemfield_resume'}


def _skipped(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == forms.__name__
            and record.getMessage().startswith('upload recovery skipped')]


async def test_upload_decision_uploads_once_then_submit_meets_the_guard(monkeypatch, tmp_path):
    """The first upload decision runs the guarded upload itself. The Submit
    click that follows passes the PDF proof and is refused by a later
    final-guard check, so nothing is clicked and nothing commits."""
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([UPLOAD, click])
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert 'Unverified or changed form value' in result['question'], result
    assert decide.await_count == 2
    guard = transport._application_controller
    assert uploads == [('upload', '#_systemfield_resume', '--file', guard.resume_path)]
    assert guard.upload_attempted and guard.upload_completed and not guard.upload_pending
    _no_production_commit(raw, transport, row)


async def test_second_upload_decision_does_not_upload_again(monkeypatch, tmp_path, caplog):
    """After the one upload, another upload decision reaches no chooser, names
    why in the log, and Submit-time recovery does not upload a second time."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([UPLOAD, UPLOAD, click])
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert 'Unverified or changed form value' in result['question'], result
    assert decide.await_count == 3
    guard = transport._application_controller
    assert uploads == [('upload', '#_systemfield_resume', '--file', guard.resume_path)]
    assert _skipped(caplog) == [f'upload recovery skipped pk={guard.pk} term=not_uploaded']
    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'Synthetic value' not in text and guard.resume_path not in text
    _no_production_commit(raw, transport, row)
