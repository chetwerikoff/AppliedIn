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
    if case == 'pdf_extra':
        page['controls'].append({
            'selector': '#other-file', 'label': 'Additional attachment',
            'type': 'file', 'files': ['unrelated.pdf'],
            'in_form': True, 'form_selector': '#app',
        })
    if case in {'protected', 'protected_decline'}:
        page['controls'].insert(1, {
            'selector': '#gender',
            'label': ('Prefer not to answer' if case == 'protected_decline'
                      else 'Female'),
            'question': 'Gender', 'tag': 'input', 'type': 'radio',
            'checked': True, 'required': False, 'in_form': True,
            'form_selector': '#app',
        })
    if case in {'protected_button', 'protected_button_decline'}:
        page['controls'].insert(1, {
            'selector': '#gender-button',
            'label': ('Prefer not to answer' if case == 'protected_button_decline'
                      else 'Female'),
            'question': 'Gender', 'tag': 'button', 'type': 'button',
            'checked': True, 'required': False, 'in_form': True,
            'form_selector': '#app', 'submit': False,
        })
    if case in {'protected_select', 'protected_select_decline'}:
        page['controls'].insert(1, {
            'selector': '#gender-select',
            'label': 'Gender', 'question': 'Gender',
            'tag': 'select', 'type': 'select', 'required': False,
            'value': 'skip' if case == 'protected_select_decline' else 'female',
            'options': [
                {'value': 'female', 'label': 'Female'},
                {'value': 'skip', 'label': 'Prefer not to answer'},
            ],
            'in_form': True, 'form_selector': '#app',
        })
    if case in {'salary', 'incomparable', 'salary_above'}:
        facts['Minimum salary floor'] = (
            'EUR 100000 yearly' if case == 'incomparable'
            else 'USD 80000 yearly' if case == 'salary_above'
            else 'USD 100000 yearly')
        page['controls'].insert(1, {
            'selector': '#pay', 'label': 'Salary (USD/year)',
            'question': 'Desired salary (USD/year)',
            'tag': 'input', 'type': 'text', 'value': '90000',
            'required': True, 'in_form': True, 'form_selector': '#app',
        })
    if case in {'sanctions', 'sanctions_missing', 'sanctions_decline'}:
        page['controls'].insert(1, {
            'selector': '#restricted',
            'label': 'No' if case == 'sanctions_decline' else 'Yes',
            'question': 'Are you from a restricted country?',
            'tag': 'input', 'type': 'radio',
            'checked': case != 'sanctions_missing',
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
    ('job', 1), ('pdf', 2), ('pdf_extra', 2), ('protected', 3),
    ('protected_button', 3), ('protected_select', 3),
    ('salary', 4), ('incomparable', 4),
    ('sanctions', 5), ('sanctions_missing', 5),
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


@pytest.mark.asyncio
@pytest.mark.parametrize('case', [
    'ready', 'salary_above', 'sanctions_decline', 'protected_decline',
    'protected_button_decline', 'protected_select_decline',
])
async def test_safe_readback_examples_pass_the_same_final_check(
        monkeypatch, tmp_path, case):
    controller, page, target, fixture = _world(
        monkeypatch, tmp_path, case=case)
    await controller.check_pre_submit(None, page, target)
    assert fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', [
    ('click', '#submit'), ('press', 'Enter'),
])
async def test_successful_commit_classification_cannot_be_replayed(
        monkeypatch, tmp_path, operation):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    calls = []
    controller.mark_click_started = lambda: calls.append('attempt')
    await controller.before_ipc(None, operation, {})
    assert controller.committed
    assert calls == ['attempt']
    assert fixture.held
    with pytest.raises(forms.Gate):
        await controller.before_ipc(None, operation, {})
    assert calls == ['attempt']


@pytest.mark.asyncio
async def test_ordinary_accessible_fill_does_not_issue_a_grant_or_final_check(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    class FakeSession:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}

    session = FakeSession()
    filled = {}
    result = await forms.execute(
        session, page, {'action': 'fill', 'selector': '#email',
                        'value': 'test@example.test'},
        facts={}, filled=filled, resume_path=controller.resume_path,
        company='example-co', jd_text='Synthetic job',
        resume_tex='', github='', pk=PK, job_url=URL)
    assert result['action'] == 'fill'
    assert filled == {'#email': 'test@example.test'}
    assert session.calls == [
        (('fill', '#email', '--value', 'test@example.test'), {})]
    assert not fixture.held


@pytest.mark.asyncio
async def test_missing_choice_fact_asks_owner_without_inventing_consent(
        monkeypatch, tmp_path):
    """An empty choose cannot turn a checkbox caption into an owner answer."""
    controller, page, _, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, {
        'selector': '#consent', 'label': 'I agree',
        'question': 'Optional synthetic acknowledgment',
        'tag': 'input', 'type': 'checkbox', 'checked': False,
        'required': False, 'disabled': False,
        'in_form': True, 'form_selector': '#app', 'form_action': URL,
    })

    class FakeSession:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}

    session = FakeSession()
    filled = {}
    args = dict(facts={}, filled=filled, resume_path=controller.resume_path,
                company='example-co', jd_text='Synthetic role',
                resume_tex='', github='', pk=PK, job_url=URL)
    with pytest.raises(forms.Gate) as error:
        await forms.execute(
            session, page, {'action': 'choose', 'selector': '#consent'},
            **args)
    assert error.value.reason == 'owner_question'
    assert error.value.form_question['selector'] == '#consent'
    assert session.calls == []
    assert filled == {}
    assert not fixture.held

    # A supplied ordinary answer may prepare this field without a grant.
    await forms.execute(
        session, page, {'action': 'choose', 'selector': '#consent',
                        'value': 'Yes'},
        **args)
    assert session.calls == [(('click', '#consent'), {})]
    assert filled == {'#consent': 'Yes'}
    assert not fixture.held
