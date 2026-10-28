"""Issue #228: evidence for a resumed Ashby run refused at the final PDF guard.

Live, three resumed reversible rows were refused with "protected tailored PDF
has not been verified" on the first decision after bootstrap, with no upload
and no recovery line in the log, so nothing showed which click got there.
Offline, the same signature comes from a first click on any non-Submit control
of a prefilled page: the raw producer sends every unclassified click on a page
with private values to the final guard, while the upload recovery only runs
for a Submit decision. These tests pin the two log lines that tell those paths
apart and keep the refusal itself unchanged: the logs are evidence, not a gate.
"""
import logging
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import _no_production_commit, _production_submit_click


def _lines(caplog, prefix):
    return [record.getMessage() for record in caplog.records
            if record.name == forms.__name__ and record.getMessage().startswith(prefix)]


def _no_values(caplog, guard):
    text = '\n'.join(record.getMessage() for record in caplog.records
                     if record.name == forms.__name__)
    assert 'Synthetic value' not in text
    assert guard.resume_path not in text
    assert Path(guard.resume_path).name not in text


@pytest.mark.parametrize(('selector', 'kind'), [
    ('#_systemfield_name', 'input/text'),
    ('#additional', 'textarea/textarea'),
    ('#_systemfield_resume', 'input/file'),
])
async def test_non_submit_first_click_reaches_pdf_guard_and_says_so(
        monkeypatch, tmp_path, caplog, selector, kind):
    """The reproduced live signature: one decision, no upload, PDF refusal.
    The final guard log names a non-Submit target on a private page, and no
    recovery-skipped line appears, because this was never a Submit decision."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([{'action': 'click', 'selector': selector}])
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF has not been verified' in result['question']
    assert decide.await_count == 1 and uploads == []
    pk = transport._application_controller.pk
    assert _lines(caplog, 'final pdf guard') == [
        f'final pdf guard pk={pk} attempted=False completed=False pending=False '
        f'identity_match=False target={kind} submit_control=False private=True']
    assert _lines(caplog, 'submit recovery skipped') == []
    _no_values(caplog, transport._application_controller)
    _no_production_commit(raw, transport, row)


async def test_submit_click_recovers_and_a_later_submit_names_the_skipped_term(
        monkeypatch, tmp_path, caplog):
    """The first Submit click takes the recovery, so it logs no skip. The second
    one skips it because this run already uploaded, and the PDF guard passes."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([click, click])
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert len(uploads) == 1 and decide.await_count == 2
    pk = transport._application_controller.pk
    assert _lines(caplog, 'submit recovery skipped') == [
        f'submit recovery skipped pk={pk} term=not_uploaded']
    assert _lines(caplog, 'final pdf guard') == []
    _no_values(caplog, transport._application_controller)
    _no_production_commit(raw, transport, row)


async def test_submit_click_after_a_producer_upload_names_the_producer_term(
        monkeypatch, tmp_path, caplog):
    """A model upload that went through the producer outside the recovery
    leaves the producer's flag set; the skip line must name that term, the
    candidate the live log could not rule out."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    bind = transport.bind_application_controller

    def bind_after_producer_upload(controller):
        bind(controller)
        controller.upload_attempted = True

    monkeypatch.setattr(transport, 'bind_application_controller', bind_after_producer_upload)
    result = await run_apply([click])
    guard = transport._application_controller
    assert result['status'] == 'gate', result
    assert uploads == []
    assert _lines(caplog, 'submit recovery skipped') == [
        f'submit recovery skipped pk={guard.pk} term=producer_upload_not_attempted']
    _no_values(caplog, guard)
    _no_production_commit(raw, transport, row)
