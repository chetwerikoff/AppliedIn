"""Issue #293: one server-owned Ashby PDF upload and ungated preparation.

The raw application producer permits one staged résumé file set and records
its result from current input readback. Ordinary fields, Yes/No buttons and
other non-Submit clicks have no per-control approval; only the final
code-owned Submit check can reject substantive form evidence.
The tests use synthetic offline fixtures, never a real application.
"""
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import (
    WAIT, _no_production_commit, _production_submit_click)

UPLOAD = {'action': 'upload', 'selector': '#_systemfield_resume'}
STOP = {'action': 'gate', 'question': 'Synthetic next decision'}


async def test_upload_runs_once_before_the_first_decision(monkeypatch, tmp_path):
    """The first decision already sees the verified attachment."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    seen = []

    def first_decision(task, *_args, **_kwargs):
        seen.append(len(uploads))
        return STOP

    result = await run_apply(first_decision)
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable'
    assert seen == [1]
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed and not guard.upload_pending
    _no_production_commit(raw, transport, row)


async def test_upload_is_a_direct_set_on_the_clipped_input_with_no_chooser(
        monkeypatch, tmp_path):
    """FF-224g: BrowserSkill opens a file chooser by clicking an opener, and
    live Ashby clips the input and varies the opener, so every chooser design
    (PRs 227 to 238) stopped at the résumé. The upload is now one direct file
    set on the exact input, and the producer records it from its own readback.
    The fake CLI fails any chooser upload, as no chooser may run here."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    tree = raw.current['entry']['upload_tree']
    assert tree['nodes'][tree['input']]['target_selector'] == forms._ASHBY_RESUME_SELECTOR
    result = await run_apply([STOP])
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable', result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert not [c for c in raw.calls if c[0] == 'upload']
    assert guard.upload_completed and guard.upload_identity is not None
    _no_production_commit(raw, transport, row)


async def test_an_incomplete_upload_tree_no_longer_stops_the_upload(monkeypatch, tmp_path):
    """FF-224g: live, the run gated on 'Incomplete or overflowing upload
    structural witness' before any upload, because the DOM upload tree read
    complete=false. The direct set reads no upload tree."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    for page in (raw.current, raw.fixed):
        page['entry']['upload_tree']['complete'] = False
    result = await run_apply([STOP])
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable', result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)] and guard.upload_completed
    _no_production_commit(raw, transport, row)


async def test_set_that_does_not_read_back_is_a_gate_not_uncertain(monkeypatch, tmp_path):
    """The set ran but the input does not hold the row PDF's name: a normal
    Gate, and the producer records no upload for the final guard."""
    async def unread():
        return {'value': {'set': True, 'files': []}}

    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path, upload=unread)
    result = await run_apply([click])
    assert result['status'] == 'gate' and result.get('item') == 2, result
    assert result['reason'] == 'defect_pre_submit_item_2'
    guard = transport._application_controller
    assert len(uploads) == 1 and decide.await_count == 1
    assert guard.upload_attempted and not guard.upload_completed
    assert guard.upload_identity is None
    _no_production_commit(raw, transport, row)


async def test_interrupted_upload_logs_class_and_source_line_only(
        monkeypatch, tmp_path, caplog):
    """FF-224f: the uncertain return after the effect edge logged nothing, so
    the live exception had to be inferred. It now names the class and the
    innermost src/ line, never the message, which can carry page text."""
    import logging

    async def broken():
        raise OSError('synthetic secret page text')

    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path, upload=broken)
    with caplog.at_level(logging.INFO, logger=forms.__name__):
        result = await run_apply([STOP])
    assert result['status'] == 'uncertain' and decide.await_count == 0
    lines = [r.getMessage() for r in caplog.records if 'upload interrupted' in r.getMessage()]
    assert len(lines) == 1, lines
    assert ' exc=OSError at=src/' in lines[0] and '.py:' in lines[0]
    assert 'secret' not in caplog.text
    _no_production_commit(raw, transport, row)


@pytest.mark.parametrize('intent', ['upload-decision', 'upload-control-click', 'submit-click'])
async def test_no_later_intent_uploads_a_second_time(monkeypatch, tmp_path, intent):
    """Each model-intent trigger is only a fallback for the not-yet-uploaded
    run; after the upload it never reaches the chooser again."""
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    # A repeated model upload is inert, so give the model a subsequent
    # inspection decision rather than expecting a removed per-action Gate.
    later = {'upload-decision': [UPLOAD, STOP],
             'upload-control-click': [
                 {'action': 'click', 'selector': '#dropzone'}, STOP],
             'submit-click': [click]}[intent]
    result = await run_apply(later)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert decide.await_count == len(later)
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    # A helper click is ordinary preparation; unlike a classified Submit, it
    # may reach the page without authorizing a new application outcome.
    guard = transport._application_controller
    assert not guard.committed
    assert not row.get('possible_submission')
    assert not any(c[0] == 'click' and c[1] == click['selector']
                   for c in raw.calls)


async def test_missing_owner_name_is_asked_without_per_field_grant(
        monkeypatch, tmp_path):
    """Preparation may upload once; an observed missing fact is still owner-only.

    A model points at the exact current Name receiver. No historic grant is
    issued, and the file is never submitted or uploaded a second time.
    """
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    for page in (raw.current, raw.fixed):
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_name':
                c.update(value='', has_value=False)
    result = await run_apply([{
        'action': 'gate', 'selector': '#_systemfield_name',
        'question': 'What is the synthetic applicant name?',
    }])
    assert result['status'] == 'gate' and result['reason'] == 'owner_question', result
    assert result['form_question']['selector'] == '#_systemfield_name'
    assert result['review_route'] == 'owner'
    assert decide.await_count == 1
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed
    _no_production_commit(raw, transport, row)


async def test_late_resume_receiver_is_uploaded_once_on_first_observation(
        monkeypatch, tmp_path):
    """The form may mount its file receiver after a Wait; prepare it once
    when observed, without inventing a new per-control grant or extra upload."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([WAIT, STOP], late=True)
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable'
    guard = transport._application_controller
    assert decide.await_count == 2
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed and not guard.upload_pending
    _no_production_commit(raw, transport, row)


@pytest.mark.asyncio
async def test_plain_boolean_click_is_ungated_preparation():
    """A Yes/No button is no longer a widget-grant or choice recognizer."""
    class Session:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}

    session = Session()
    page = {'controls': [
        {'selector': '#yes', 'label': 'Yes', 'question': 'Work arrangement?',
         'tag': 'button', 'type': 'button', 'disabled': False},
        {'selector': '#no', 'label': 'No', 'question': 'Work arrangement?',
         'tag': 'button', 'type': 'button', 'disabled': False},
    ]}
    filled = {}
    result = await forms.execute(
        session, page, {'action': 'click', 'selector': '#no'},
        facts={}, filled=filled, resume_path='synthetic.pdf',
        company='example-co', jd_text='Synthetic role',
        resume_tex='', github='', pk='example-co#synthetic',
        job_url='https://example.test/apply', allow_click=True)
    assert result['action'] == 'click'
    assert session.calls == [(('click', '#no'), {})]
    assert filled == {}
