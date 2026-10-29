"""FF-224e: the server-owned résumé upload runs before the first model decision.

Live, every model-intent trigger for the one guarded upload (an upload
decision, a click on the upload control, Submit) waited for the model, and the
model kept finding another route to the final PDF guard first: a click on an
answer button, a click on a field. Each fix closed one route and the next run
found another. The upload now runs right after the owner gate and before the
first decision, under the same one-upload terms, so no model route precedes it.

A Yes/No answer button clicked directly was one of those routes: it reached the
final guard as an unclassified click. It now takes the guarded Boolean choice.

The production cases run the real raw producer and controller (no fixture
context) on the reversible Ashby route; nothing is clicked or committed.
"""
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import (
    WAIT, _no_production_commit, _production_submit_click)
from tests.tools.test_browser_reversible_gates import (
    _answer, _apply, _fixture, _only_required, _record_boolean_press)

UPLOAD = {'action': 'upload', 'selector': '#_systemfield_resume'}
STOP = {'action': 'gate', 'question': 'Synthetic next decision'}


async def test_upload_runs_once_before_the_first_decision(monkeypatch, tmp_path):
    """The first decision already sees the verified attachment."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    seen = []

    def first_decision(task, *_args, **_kwargs):
        seen.append((len(uploads), 'résumé attachment verified=True' in task))
        return STOP

    result = await run_apply(first_decision)
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question']
    assert seen == [(1, True)]
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
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question'], result
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
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question'], result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)] and guard.upload_completed
    _no_production_commit(raw, transport, row)


async def test_set_that_does_not_read_back_is_a_gate_not_uncertain(monkeypatch, tmp_path):
    """The set ran but the input does not hold the row PDF's name: a normal
    Gate, and the producer records no upload for the final guard."""
    async def unread():
        return {'value': {'set': True, 'files': []}}

    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path, upload=unread)
    result = await run_apply([STOP])
    assert result['status'] == 'gate', result
    assert result['question'].startswith(
        'The uploaded résumé was not proven in the current file input.'), result
    guard = transport._application_controller
    assert len(uploads) == 1 and decide.await_count == 0
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
    later = {'upload-decision': [UPLOAD, click],
             'upload-control-click': [{'action': 'click', 'selector': '#dropzone'}],
             'submit-click': [click]}[intent]
    result = await run_apply(later)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert decide.await_count == len(later)
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    _no_production_commit(raw, transport, row)


async def test_pending_owner_question_keeps_priority(monkeypatch, tmp_path):
    """The owner gate runs first. An unanswered receiver is asked of the owner
    with no upload and no decision, never turned into an uncertain upload."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    for page in (raw.current, raw.fixed):
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_name':
                c.update(value='', has_value=False)
    result = await run_apply([STOP])
    assert result['status'] == 'gate' and result['reason'] == 'owner_question', result
    assert result['form_question']['selector'] == '#_systemfield_name'
    assert uploads == [] and decide.await_count == 0
    assert not transport._application_controller.upload_attempted
    _no_production_commit(raw, transport, row)


async def test_field_rendered_after_the_first_decision_is_left_to_the_fallbacks(
        monkeypatch, tmp_path):
    """The upload runs once, before the first decision only. A résumé field
    that renders later is not uploaded by the server on its own; the model's
    upload, upload-control or Submit intent takes the same one upload."""
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([WAIT, STOP], late=True)
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question']
    assert decide.await_count == 2 and uploads == []
    assert not transport._application_controller.upload_attempted
    _no_production_commit(raw, transport, row)


# The Boolean cases mirror the existing choose tests in
# test_browser_reversible_gates.py with a plain click, so the two routes stay
# provably the same guarded path.

@pytest.mark.asyncio
async def test_boolean_click_on_the_approved_member_is_the_guarded_choice(
        monkeypatch, tmp_path):
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    _record_boolean_press(endpoint, '#boolean-no')
    routed = []
    execute = forms.execute

    async def spy(session, page, action, **kwargs):
        routed.append(dict(action))
        return await execute(session, page, action, **kwargs)

    monkeypatch.setattr(forms, 'execute', spy)
    decision.side_effect = [{'action': 'click', 'selector': '#boolean-no'}, STOP]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question']
    assert routed[0] == {'action': 'choose', 'selector': '#boolean-no',
                         'fact': 'Work arrangement?'}
    assert [c for c in endpoint.calls if c[0] == 'click'] == [('click', '#boolean-no')]


@pytest.mark.parametrize('verb', ['click', 'choose'])
@pytest.mark.asyncio
async def test_boolean_click_on_the_unapproved_member_gates_as_choose_does(
        monkeypatch, tmp_path, verb):
    """The owner answered Yes; the model picks No. A click is refused exactly
    as the choose action is, before any browser write."""
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'Yes')
    action = {'action': verb, 'selector': '#boolean-no'}
    if verb == 'choose':
        action['fact'] = 'Work arrangement?'
    decision.return_value = action
    result = await _apply(pdf, {'Work arrangement?': 'Yes'})
    assert result['status'] == 'gate', result
    assert result['question'].startswith(
        'The selected radio does not match the exact owner answer.'), result
    assert [c for c in endpoint.calls if c[0] == 'click'] == []


def test_only_a_member_of_an_exact_boolean_pair_is_routed():
    from copy import deepcopy
    from tests.tools.test_browser_reversible_gates import _NO, _YES, _by_locator, _live_dom

    page, _ = _live_dom()
    yes, no = (_by_locator(page, s)['selector'] for s in (_YES, _NO))
    assert forms._boolean_pair_member(page, yes) and forms._boolean_pair_member(page, no)
    for selector in ('#_systemfield_name', '#additional', '#dropzone', '', 'missing'):
        assert not forms._boolean_pair_member(page, selector)
    # A lone answer button is no exact pair, so its click is left unchanged.
    alone = deepcopy(page)
    alone['controls'] = [c for c in alone['controls'] if c['selector'] != no]
    assert not forms._boolean_pair_member(alone, yes)
