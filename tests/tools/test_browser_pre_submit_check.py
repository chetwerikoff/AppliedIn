"""Issue #293: synthetic, offline raw-IPC final boundary tests.

No browser, network, real application, live queue or owner artifact is used.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms


URL = 'https://employer.test/job/1'
PK = 'example-co#1'


class HoldFixture:
    def __init__(self, row):
        self.row = row
        self.held = False

    def is_held(self, pk):
        assert pk == PK
        return self.held


def _world(monkeypatch, tmp_path, *, case='ready'):
    resume = tmp_path / 'Test User.pdf'
    resume.write_bytes(b'%PDF-1.4\nsynthetic only\n')
    target = {
        'selector': '#submit', 'label': 'Submit application',
        'tag': 'button', 'type': 'submit', 'native_type': 'submit',
        'submit': True, 'in_form': True, 'form_selector': '#app',
        'native_form_owner_selector': '#app', 'form_action': URL,
        'formaction': '', 'disabled': False,
    }
    file_field = {
        'selector': '#resume', 'label': 'Resume', 'question': 'Resume',
        'tag': 'input', 'type': 'file', 'files': [resume.name],
        'in_form': True, 'form_selector': '#app', 'form_action': URL,
        'required': True,
    }
    email = {
        'selector': '#email', 'label': 'Email', 'question': 'Email',
        'tag': 'input', 'type': 'email', 'value': 'test@example.test',
        'has_value': True, 'in_form': True, 'form_selector': '#app',
        'form_action': URL, 'required': True,
    }
    page = {
        'url': URL, 'title': 'Synthetic role', 'truncated': False,
        'inventory_verified': True, 'opaque_controls': False,
        'unsupported_frames': False, 'shadow_roots': False,
        'controls': [email, file_field, target],
    }
    row = {
        'pk': PK, 'company': 'example-co', 'job_id': '1', 'ats': '',
        'status': 'submitting', 'jd_url': URL, 'title': page['title'],
        'resume_seed': 'synthetic-seed',
    }
    facts = {}
    if case == 'job':
        row['job_id'] = '2'
    if case == 'pdf':
        file_field['files'] = []
    if case == 'protected':
        page['controls'].insert(1, {
            'selector': '#gender', 'label': 'Female',
            'question': 'Gender', 'tag': 'input', 'type': 'radio',
            'checked': True, 'required': False, 'in_form': True,
            'form_selector': '#app',
        })
    if case in {'salary', 'incomparable'}:
        facts['Minimum salary floor'] = (
            'USD 100000 yearly' if case == 'salary' else 'EUR 100000 yearly')
        page['controls'].insert(1, {
            'selector': '#pay', 'label': 'Salary (USD/year)',
            'question': 'Desired salary (USD/year)',
            'tag': 'input', 'type': 'text', 'value': '90000',
            'required': True, 'in_form': True, 'form_selector': '#app',
        })
    if case == 'sanctions':
        page['controls'].insert(1, {
            'selector': '#restricted', 'label': 'Yes',
            'question': 'Are you from a restricted country?',
            'tag': 'input', 'type': 'radio', 'checked': True,
            'required': False, 'in_form': True, 'form_selector': '#app',
        })

    fixture = HoldFixture(row)
    controller = forms.ApplicationIPCController(
        PK, 'example-co', URL, str(resume), facts, {}, [],
        {'url': URL, 'last_button': ''}, lambda: None,
        fixture_context=fixture)
    controller.upload_completed = True
    controller.upload_identity = ('sealed-pdf',)

    monkeypatch.setattr(controller, '_row', lambda: row)
    monkeypatch.setattr(controller, '_row_pdf', lambda: ('sealed-pdf',))
    monkeypatch.setattr(forms, 'check_dispatch', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'check_before_committing_click', lambda *a, **kw: None)
    monkeypatch.setattr(forms, 'scoped_identity_conflict', lambda *a, **kw: False)

    def hold(pk, step, **kwargs):
        fixture.held = True
        row.update(status='needs_human', gate_reason='submit_uncertain')

    monkeypatch.setattr(forms, 'hold_possible_submission', hold)

    async def current(_session):
        return deepcopy(page)

    monkeypatch.setattr(controller, '_page', current)
    return controller, page, target, fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(('case', 'item'), [
    ('job', 1), ('pdf', 2), ('protected', 3),
    ('salary', 4), ('incomparable', 4), ('sanctions', 5),
])
async def test_five_substantive_pre_submit_items_refuse_before_ipc(
        monkeypatch, tmp_path, case, item):
    controller, page, target, fixture = _world(
        monkeypatch, tmp_path, case=case)
    with pytest.raises(forms.PreSubmitGate) as exc:
        await controller.check_pre_submit(None, page, target)
    assert exc.value.item == item
    assert exc.value.reason == f'defect_pre_submit_item_{item}'
    assert not fixture.held
    assert not controller.committed
    if case == 'incomparable':
        assert exc.value.form_question['label'] == 'Minimum salary floor'


@pytest.mark.asyncio
async def test_complete_synthetic_form_acquires_own_hold_once(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    await controller.check_pre_submit(None, page, target)
    assert fixture.held
    assert fixture.row['status'] == 'needs_human'
    assert fixture.row['gate_reason'] == 'submit_uncertain'


@pytest.mark.asyncio
async def test_no_preparation_op_checks_commit_but_enter_does(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, {
        'selector': '#next', 'label': 'Next',
        'type': 'button', 'tag': 'button', 'in_form': True,
        'submit': False, 'disabled': False, 'form_selector': '#app',
    })
    calls = []

    async def current(_session):
        return page

    async def final(_session, read, receiver):
        calls.append((read, receiver))
        raise forms.PreSubmitGate(1, 'synthetic final refusal')

    monkeypatch.setattr(controller, '_page', current)
    monkeypatch.setattr(controller, 'check_pre_submit', final)
    for args in [('fill', '#email', '--value', 'test@example.test'),
                 ('select', '#country', '--value', 'Example'),
                 ('focus', '#email'),
                 ('click', '#next'),
                 ('press', 'Escape')]:
        await controller.before_ipc(None, args, {})
    assert calls == []
    with pytest.raises(forms.PreSubmitGate):
        await controller.before_ipc(None, ('click', '#submit'), {})
    with pytest.raises(forms.PreSubmitGate):
        await controller.before_ipc(None, ('press', 'Enter'), {})
    assert len(calls) == 2
    assert not controller.committed
