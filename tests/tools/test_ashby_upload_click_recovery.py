"""Issue #232: a model click on the résumé upload control takes the recovery.

Live, a resumed Ashby run's first decision was a plain click on a non-Submit
button inside the résumé upload control. The page already held values, so the
raw producer treated the click as possibly committing and refused it at the
final guard for the missing row PDF. A click on a member of the structural
upload tree is an upload request: it runs the one-shot guarded upload and is
never dispatched. These cases run the real raw producer (no fixture context).

FF-224e uploads before the first decision, so this path is the fallback for a
résumé field rendered after it: the first decision waits, then the field appears.
"""
from copy import deepcopy
import logging

from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_ashby_upload_fixture import (
    WAIT, _no_production_commit, _production_submit_click)

INNER = '#dropzone-attach'


def _with_inner_button(page):
    """The live shape: a separately clickable button inside the dropzone."""
    tree = page['entry']['upload_tree']
    tree['nodes'].append({'tag': 'button', 'parent': tree['dropzone'],
                          'target_selector': INNER, 'attrs': [], 'text': ''})
    dropzone = next(c for c in page['controls'] if c['selector'] == '#dropzone')
    page['controls'].insert(page['controls'].index(dropzone) + 1,
                            {**deepcopy(dropzone), 'selector': INNER, 'label': 'Upload File'})


def _pdf_lines(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == forms.__name__
            and record.getMessage().startswith('final pdf guard')]


@pytest.mark.parametrize('selector', ['#dropzone', INNER, '#_systemfield_resume'])
async def test_upload_control_click_uploads_once_never_clicks_then_meets_the_guard(
        monkeypatch, tmp_path, caplog, selector):
    """The upload-control click runs the one guarded upload and is not sent.
    The Submit click that follows passes the PDF proof and is refused by a
    later final-guard check, so nothing is clicked and nothing commits."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    if selector == INNER:
        _with_inner_button(raw.current)
        _with_inner_button(raw.fixed)
        upload_call = raw.call

        async def keep_inner_button(method, *args, **kwargs):
            # The fake chooser rebuilds the page; the live button stays put.
            result = await upload_call(method, *args, **kwargs)
            if method == 'upload':
                _with_inner_button(raw.current)
                _with_inner_button(raw.fixed)
            return result

        raw.call = keep_inner_button
    result = await run_apply([WAIT, {'action': 'click', 'selector': selector}, click],
                             late=True)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert 'Unverified or changed form value' in result['question'], result
    assert decide.await_count == 3
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    assert guard.upload_attempted and guard.upload_completed and not guard.upload_pending
    assert _pdf_lines(caplog) == []
    _no_production_commit(raw, transport, row)


async def test_second_upload_control_click_does_not_upload_again(monkeypatch, tmp_path):
    """After the one upload, another upload-control click is an ordinary click
    again; it never reaches the chooser a second time."""
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    dropzone = {'action': 'click', 'selector': '#dropzone'}
    result = await run_apply([WAIT, dropzone, dropzone, click], late=True)
    assert result['status'] == 'gate', result
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    _no_production_commit(raw, transport, row)


async def test_non_upload_button_click_still_meets_the_final_guard(monkeypatch, tmp_path, caplog):
    """Every other click is unchanged: a plain button outside the upload tree
    on a prefilled page still reaches the PDF refusal, and the evidence line
    names it as no tree member with a fixed label class, never its text."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    run_apply, _, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    for page in (raw.current, raw.fixed):
        dropzone = next(c for c in page['controls'] if c['selector'] == '#dropzone')
        page['controls'].append({**deepcopy(dropzone), 'selector': '#autofill-pane',
                                 'label': 'Autofill from resume'})
    result = await run_apply([WAIT, {'action': 'click', 'selector': '#autofill-pane'}],
                             late=True)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF has not been verified' in result['question']
    assert decide.await_count == 2 and uploads == []
    [line] = _pdf_lines(caplog)
    assert line.endswith('target=button/button submit_control=False private=True '
                         'upload_tree_member=False label_class=autofill'), line
    assert 'Autofill from resume' not in line
    _no_production_commit(raw, transport, row)


@pytest.mark.parametrize(('label', 'expected'), [
    ('Upload File', 'upload'), ('Attach', 'attach'), ('Autofill with résumé', 'autofill'),
    ('Yes', 'yes'), ('No', 'no'), ('Apply for this job', 'apply'),
    ('Submit Application', 'submit'), ('Next', 'next'), ('Continue', 'next'),
    ('Something else', 'other'), ('', 'other'),
])
def test_label_class_is_a_fixed_name(label, expected):
    assert forms._label_class({'label': label}) == expected


def test_upload_tree_membership_is_structural():
    from tests.tools.test_browser_formless import observed
    page, _ = observed(movePane=True)
    assert forms._upload_tree_member(page, '#_systemfield_resume')
    assert forms._upload_tree_member(page, '#dropzone')
    for selector in ('#structural-submit', '#additional', '#_systemfield_name', '', None):
        assert not forms._upload_tree_member(page, selector)
    page['entry']['upload_tree']['complete'] = False
    assert not forms._upload_tree_member(page, '#dropzone')
