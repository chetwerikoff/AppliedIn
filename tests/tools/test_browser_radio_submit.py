"""Issue #293: native-radio selection is evaluated once at final Submit.

No previous per-radio grants, selection receipts or pre-write witnesses.
Tests use the synthetic raw-IPC decision fixture and never submit a form.
"""
import pytest

from tests.tools.test_browser_pre_submit_check import _world
from tools import browser_skill_apply as forms


def _radio(label, selector, *, checked=False, required=True,
           question='Work authorization', form='#app'):
    return {
        'selector': selector, 'label': label, 'question': question,
        'tag': 'input', 'type': 'radio', 'name': 'work_authorization',
        'checked': checked, 'value': label.lower(),
        'required': required, 'disabled': False,
        'in_form': True, 'form_selector': form,
    }


@pytest.mark.asyncio
async def test_one_current_native_radio_choice_is_sufficient_without_a_grant(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'][:0] = [
        _radio('Yes', '#yes', checked=True),
        _radio('No', '#no'),
    ]
    await controller.check_pre_submit(None, page, target)
    assert fixture.held
    assert controller.step['last_button'] == 'Submit application'


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', [
    'none', 'unselected_required', 'foreign_checked',
])
async def test_current_required_radio_without_a_checked_form_peer_refuses_item_2(
        monkeypatch, tmp_path, choice):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    yes = _radio('Yes', '#yes', checked=False)
    no = _radio('No', '#no', checked=False, required=False)
    if choice == 'foreign_checked':
        no['checked'] = True
        no['form_selector'] = '#other-form'
    if choice == 'unselected_required':
        no['checked'] = False
    page['controls'][:0] = [yes, no]
    with pytest.raises(forms.PreSubmitGate) as error:
        await controller.check_pre_submit(None, page, target)
    assert error.value.item == 2
    assert not fixture.held


@pytest.mark.asyncio
async def test_required_group_uses_current_form_not_model_action_history(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'][:0] = [
        _radio('Yes', '#yes', checked=False),
        _radio('No', '#no', checked=True, required=False),
    ]
    controller.filled['#yes'] = 'Yes'  # stale model ledger does not authorize
    await controller.check_pre_submit(None, page, target)
    assert fixture.held


@pytest.mark.asyncio
async def test_preselected_protected_characteristic_affirmative_is_item_3(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, _radio(
        'Female', '#gender', question='Gender', checked=True, required=False))
    with pytest.raises(forms.PreSubmitGate) as error:
        await controller.check_pre_submit(None, page, target)
    assert error.value.item == 3
    assert not fixture.held


@pytest.mark.asyncio
async def test_native_choice_click_is_preparation_and_not_a_final_grant(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, _radio('No', '#no', checked=False))
    class Session:
        def __init__(self):
            self.calls = []
        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}
    session = Session()
    result = await forms.execute(
        session, page, {'action': 'choose', 'selector': '#no', 'value': 'No'},
        facts={}, filled={}, resume_path=controller.resume_path,
        company='example-co', jd_text='Synthetic role', resume_tex='',
        github='', pk='example-co#1', job_url='https://employer.test/job/1')
    assert result['action'] == 'choose'
    assert session.calls == [(('click', '#no'), {})]
    assert not fixture.held
    # The model's click is never proof that the native input is now checked.
    with pytest.raises(forms.PreSubmitGate) as error:
        await controller.check_pre_submit(None, page, target)
    assert error.value.item == 2
