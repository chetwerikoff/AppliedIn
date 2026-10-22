"""Final native-radio approval must bind a live, unique form/name/question.

Only synthetic in-memory controls and the production raw Submit guard are used.
An earlier approved writer cannot make conflicting current radio receivers safe.
"""
import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import (
    field, secure_inventory, prepared_without_unapproved_write,
    final_submit_denied,
)


def _selected_group():
    return secure_inventory(
        field('Yes', '#yes', tag='input', type='radio',
              name='work_authorization', question='Work authorization',
              value='yes', has_value=True, checked=True),
        field('No', '#no', tag='input', type='radio',
              name='work_authorization', question='Work authorization',
              value='no', has_value=True, checked=False),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('drift,reason', [
    ('missing-name', 'A native radio group needs a form, name and visible question'),
    ('missing-question', 'A native radio group needs a form, name and visible question'),
    ('foreign-form', 'Radio group members or captions are incomplete or ambiguous'),
    ('duplicate-value', 'Radio group members or captions are incomplete or ambiguous'),
    ('duplicate-caption', 'Radio group members or captions are incomplete or ambiguous'),
    ('two-selected', 'Native radio group has conflicting checked members'),
    ('unassociated', 'Radio group members or captions are incomplete or ambiguous'),
])
async def test_radio_group_drift_refuses_real_final_submit(
        monkeypatch, tmp_path, drift, reason):
    page = _selected_group()
    browser, row, filled, history = await prepared_without_unapproved_write(
        monkeypatch, page)
    # Baseline: the current native selection has one form, name, visible
    # question, unique machine values and exactly one checked radio.
    filled['#yes'] = 'Yes'
    forms.check_form(page, filled, uploaded=True)

    yes, no = page['controls'][:2]
    if drift == 'missing-name':
        yes.pop('name')
    elif drift == 'missing-question':
        yes['question'] = ''
    elif drift == 'foreign-form':
        no['form_selector'] = '#other-form'
    elif drift == 'duplicate-value':
        no['value'] = yes['value']
    elif drift == 'duplicate-caption':
        no['label'] = yes['label']
    elif drift == 'two-selected':
        no['checked'] = True
    elif drift == 'unassociated':
        yes['in_form'] = False

    await final_submit_denied(
        monkeypatch, tmp_path, page, row, filled, history,
        reason=reason)
    assert not any(call[0] in {'fill', 'select', 'click', 'upload'}
                   for call in browser.calls)
