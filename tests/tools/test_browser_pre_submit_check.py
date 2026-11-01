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
    ('click', '#submit'),
    *[('press', key) for key in ('Enter', 'Return', 'NumpadEnter', ' ',
                                 'Space', 'Spacebar')],
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
@pytest.mark.parametrize('key', [
    'Enter', 'Return', 'NumpadEnter', ' ', 'Space', 'Spacebar',
])
async def test_selector_targeted_in_form_press_uses_the_same_final_check(
        monkeypatch, tmp_path, key):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    starts = []
    controller.mark_click_started = lambda: starts.append('raw-commit')
    await controller.before_ipc(
        None, ('press', key, '--selector', '#email'), {})
    assert fixture.held and controller.committed
    assert starts == ['raw-commit']
    with pytest.raises(forms.Gate):
        await controller.before_ipc(
            None, ('press', key, '--selector', '#email'), {})
    assert starts == ['raw-commit']


@pytest.mark.asyncio
async def test_unknown_non_submit_click_stays_ungated_preparation(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    await controller.before_ipc(None, ('click', '#not-in-fresh-page'), {})
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
async def test_malformed_known_raw_submit_does_not_bypass_final_check(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    with pytest.raises(forms.PreSubmitGate) as denied:
        await controller.before_ipc(
            None, ('click', '#submit', '--unclassified-argument'), {})
    assert denied.value.item == 1
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
async def test_unknown_targeted_form_enter_fails_closed_before_implicit_submit(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.before_ipc(
            None, ('press', 'Enter', '--selector', '#not-in-fresh-page'), {})
    assert refusal.value.item == 1
    assert not fixture.held and not controller.committed

@pytest.mark.asyncio
async def test_duplicate_submit_selector_cannot_skip_raw_check(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].append({**target, 'label': 'Duplicate Submit'})
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.before_ipc(None, ('click', '#submit'), {})
    assert refusal.value.item == 1
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
async def test_nested_accessible_option_click_remains_ungated_preparation(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].append({
        'selector': '#picker', 'label': 'Location',
        'type': 'combobox', 'tag': 'input', 'in_form': True,
        'form_selector': '#app', 'combobox': {
            'options': [{'selector': '#picker-option',
                         'label': 'Example', 'submit': False}],
        },
    })
    await controller.before_ipc(None, ('click', '#picker-option'), {})
    assert not fixture.held and not controller.committed


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



@pytest.mark.asyncio
@pytest.mark.parametrize('selected,accepted', [
    ('Yes', False), ('No', True), ('', False),
])
async def test_checked_yes_no_sanctions_caption_is_final_item_five(
        monkeypatch, tmp_path, selected, accepted):
    """Actual checked buttons, not historical grants, decide restricted choice."""
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    question = 'Are you from a restricted country?'
    for answer in ('Yes', 'No'):
        page['controls'].insert(0, {
            'selector': '#restricted-' + answer.lower(), 'label': answer,
            'question': question, 'tag': 'button', 'type': 'button',
            'checked': selected == answer, 'required': True, 'disabled': False,
            'in_form': True, 'form_selector': '#app', 'form_action': URL,
            'submit': False,
        })
    if accepted:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held
    else:
        with pytest.raises(forms.PreSubmitGate) as error:
            await controller.check_pre_submit(None, page, target)
        assert error.value.item == 5
        assert not fixture.held


@pytest.mark.asyncio
async def test_yes_no_button_preparation_uses_no_widget_grant(
        monkeypatch, tmp_path):
    """An ordinary button answer is a direct click, not a permission token."""
    controller, page, _, fixture = _world(monkeypatch, tmp_path)
    page['controls'].append({
        'selector': '#decline', 'label': 'No',
        'question': 'Synthetic preference', 'type': 'button',
        'tag': 'button', 'checked': False, 'disabled': False,
        'in_form': True, 'form_selector': '#app', 'submit': False,
    })
    class Session:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}

    session = Session()
    filled = {}
    await forms.execute(
        session, page, {'action': 'choose', 'selector': '#decline',
                        'value': 'No'},
        facts={}, filled=filled, resume_path=controller.resume_path,
        company='example-co', jd_text='Synthetic role', resume_tex='',
        github='', pk=PK, job_url=URL)
    assert session.calls == [(('click', '#decline'), {})]
    assert filled == {'#decline': 'No'}
    assert not fixture.held



@pytest.mark.asyncio
@pytest.mark.parametrize('failure,item', [
    ('The tailored résumé is missing or its base changed; re-tailor before applying.', 2),
    ('The tailored PDF is missing; nothing may be submitted without it.', 2),
    ('This job is already applied; no duplicate is permitted.', 1),
])
async def test_dispatch_stage_and_duplicate_failures_keep_substantive_item(
        monkeypatch, tmp_path, failure, item):
    """Existing dispatch evidence maps PDF/seed to 2, duplicate/job to 1."""
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    def refuse_dispatch(*_args, **_kwargs):
        raise forms.Gate(failure)
    monkeypatch.setattr(forms, 'check_dispatch', refuse_dispatch)
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.check_pre_submit(None, page, target)
    assert refused.value.item == item
    assert refused.value.reason == f'defect_pre_submit_item_{item}'
    assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [
    'Enter', 'Return', 'NumpadEnter', ' ', 'Space', 'Spacebar',
])
async def test_focused_submit_key_uses_single_refusing_pre_submit_check(
        monkeypatch, tmp_path, key):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    calls = []

    async def refusal(_session, observed, receiver):
        calls.append((observed, receiver))
        raise forms.PreSubmitGate(2, 'Synthetic row PDF check failed.')

    monkeypatch.setattr(controller, 'check_pre_submit', refusal)
    await controller.before_ipc(None, ('focus', '#submit'), {})
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.before_ipc(None, ('press', key), {})
    assert refused.value.item == 2
    assert calls == [(page, target)]
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [
    'Escape', 'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    'Home', 'End', 'PageUp', 'PageDown', 'Backspace', 'Delete',
])
async def test_navigation_and_editing_keys_stay_ungated(
        monkeypatch, tmp_path, key):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    calls = []

    async def refusal(*args):
        calls.append(args)
        raise AssertionError('Ordinary keyboard preparation ran final Submit check.')

    monkeypatch.setattr(controller, 'check_pre_submit', refusal)
    await controller.before_ipc(None, ('focus', '#submit'), {})
    await controller.before_ipc(None, ('press', key), {})
    assert not calls and not fixture.held and not controller.committed


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [
    'F5', 'a', 'Control+Enter', 'ArrowUp+Enter', '', 'Shift',
])
async def test_unsupported_application_key_is_refused_exactly(
        monkeypatch, tmp_path, key):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    with pytest.raises(forms.Gate, match=r'^Unsupported key for application IPC\.$'):
        await controller.before_ipc(None, ('press', key), {})
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
async def test_pdf_attached_to_different_form_cannot_satisfy_item_two(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    file_input = next(c for c in page['controls'] if c.get('type') == 'file')
    # The unrelated profile form has no Submit; the application Submit is
    # unique but its form does not have the PDF.
    file_input['form_selector'] = '#profile'
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.check_pre_submit(None, page, target)
    assert refused.value.item == 2
    assert not fixture.held and not controller.committed


def test_formless_pdf_requires_membership_in_proven_application_root(
        monkeypatch, tmp_path):
    controller, page, target, _fixture = _world(monkeypatch, tmp_path)
    file_input = next(c for c in page['controls'] if c.get('type') == 'file')
    other_input = next(c for c in page['controls'] if c.get('type') == 'email')
    target['in_form'] = False
    file_input['in_form'] = False
    # The production caller obtains these members from _formless_boundary.
    forms.check_form(
        page, {}, True, file_input['files'][0],
        submit_target=target, formless_members=[file_input])
    with pytest.raises(forms.Gate, match='committing form'):
        forms.check_form(
            page, {}, True, file_input['files'][0],
            submit_target=target, formless_members=[other_input])


@pytest.mark.asyncio
@pytest.mark.parametrize('tag', ['input', 'select'])
@pytest.mark.parametrize(('caption', 'machine', 'blocked'), [
    ('Male', 'male', True),
    ('Prefer not to answer', 'skip', False),
])
async def test_selected_protected_combobox_caption_is_checked(
        monkeypatch, tmp_path, caption, machine, blocked, tag):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, {
        'selector': '#gender-picker', 'label': 'Gender', 'question': 'Gender',
        'tag': tag, 'type': 'combobox', 'required': True, 'value': '',
        'in_form': True, 'form_selector': '#app',
        'combobox': {
            'selected_text': caption, 'selected_value': machine,
            'hidden_selector': '#gender-value', 'hidden_value': machine,
        },
    })
    page['controls'].insert(1, {
        'selector': '#gender-value', 'tag': 'input', 'type': 'hidden',
        'value': machine, 'in_form': True, 'form_selector': '#app',
    })
    if blocked:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 3
        assert not fixture.held
    else:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held


@pytest.mark.asyncio
async def test_optional_protected_combobox_hidden_receiver_drift_fails_closed(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, {
        'selector': '#gender-picker', 'label': 'Gender', 'question': 'Gender',
        'tag': 'input', 'type': 'combobox', 'required': False, 'value': '',
        'in_form': True, 'form_selector': '#app',
        'combobox': {
            'selected_text': 'Prefer not to answer', 'selected_value': 'skip',
            'hidden_selector': '#gender-value', 'hidden_value': 'skip',
        },
    })
    page['controls'].insert(1, {
        'selector': '#gender-value', 'tag': 'input', 'type': 'hidden',
        'value': 'male', 'in_form': True, 'form_selector': '#app',
    })
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.check_pre_submit(None, page, target)
    assert refused.value.item == 3
    assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('value', 'blocked'), [
    ('90000', True), ('110000', False),
])
async def test_salary_label_numeric_example_never_overrides_entered_amount(
        monkeypatch, tmp_path, value, blocked):
    controller, page, target, fixture = _world(monkeypatch, tmp_path, case='salary')
    pay = next(c for c in page['controls'] if c.get('selector') == '#pay')
    pay.update(question='Desired salary (USD/year; example 150000)',
               label='Salary (USD/year; example 150000)', value=value)
    if blocked:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 4
        assert not fixture.held
    else:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held


@pytest.mark.asyncio
async def test_same_job_committing_get_navigation_is_refused_without_submit(
        monkeypatch, tmp_path):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    with pytest.raises(forms.Gate, match='Navigation outside'):
        await controller.before_ipc(
            None, ('navigate', URL + '/submit'), {})
    await controller.before_ipc(
        None, ('navigate', URL + '/application'), {})
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [
    'Enter', 'Return', 'NumpadEnter', ' ', 'Space', 'Spacebar',
])
async def test_implicit_submit_key_without_proven_receiver_fails_closed(
        monkeypatch, tmp_path, key):
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'] = [{
        'selector': '#outside', 'label': 'Outside the application',
        'tag': 'input', 'type': 'text', 'in_form': False,
        'value': 'synthetic', 'required': False,
    }]
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.before_ipc(None, ('press', key), {})
    assert refused.value.item == 1
    assert not fixture.held and not controller.committed


@pytest.mark.asyncio
@pytest.mark.parametrize(('value', 'blocked'), [
    ('1,500,000', True), ('2,500,000', False),
])
async def test_comma_grouped_salary_keeps_its_leading_millions_digit(
        monkeypatch, tmp_path, value, blocked):
    controller, page, target, fixture = _world(monkeypatch, tmp_path, case='salary')
    controller.facts['Minimum salary floor'] = 'VND 2,000,000 monthly'
    pay = next(c for c in page['controls'] if c.get('selector') == '#pay')
    pay.update(question='Desired salary (VND/month)',
               label='Salary (VND/month)', value=value)
    if blocked:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 4
        assert not fixture.held
    else:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('display_case', 'accepted'), [
    ('matching', True), ('none', False), ('two', False),
    ('wrong_filename', False), ('wrong_form', False), ('unproven', False),
])
async def test_removed_resume_input_uses_only_one_proven_current_file_display(
        monkeypatch, tmp_path, display_case, accepted):
    """After upload, a removed input is permitted only with the single exact
    resume display from the committing form, never an unrelated profile form.
    """
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    display = {
        'filename': 'Test User.pdf', 'field_label': 'Resume',
        'valid': True, 'in_form': True, 'form_selector': '#app',
        'root_selector': '',
    }
    if display_case == 'wrong_filename':
        display['filename'] = 'other.pdf'
    elif display_case == 'wrong_form':
        display['form_selector'] = '#profile'
    elif display_case == 'unproven':
        display['valid'] = False
    page['attached_file_displays'] = (
        [] if display_case == 'none'
        else [display, deepcopy(display)] if display_case == 'two'
        else [display])
    if accepted:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held
    else:
        with pytest.raises(forms.PreSubmitGate) as refusal:
            await controller.check_pre_submit(None, page, target)
        assert refusal.value.item == 2
        assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('uploaded', 'identity_ok'), [
    (False, True), (True, False),
])
async def test_file_display_does_not_replace_upload_completion_or_pdf_identity(
        monkeypatch, tmp_path, uploaded, identity_ok):
    """Passive filename display does not prove a successful upload or row PDF."""
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    page['attached_file_displays'] = [{
        'filename': 'Test User.pdf', 'field_label': 'Resume',
        'valid': True, 'in_form': True, 'form_selector': '#app',
        'root_selector': '',
    }]
    controller.upload_completed = uploaded
    if not identity_ok:
        controller.upload_identity = ('different-pdf',)
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.check_pre_submit(None, page, target)
    assert refusal.value.item == 2
    assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('caption', 'value'), [
    ('Male', ''), ('Male', 'skip'), ('Prefer not to answer', ''),
])
async def test_unreadable_selected_protected_combobox_refuses_item_three(
        monkeypatch, tmp_path, caption, value):
    """A rendered non-placeholder answer without a proven selection is not a safe decline."""
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'].insert(0, {
        'selector': '#gender-unknown', 'label': 'Gender', 'question': 'Gender',
        'type': 'combobox', 'tag': 'select', 'required': False,
        'in_form': True, 'form_selector': '#app', 'value': value,
        'combobox': {'selected_text': '', 'selected_value': '',
                     'displayed_text': caption, 'options': []},
    })
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.check_pre_submit(None, page, target)
    assert refusal.value.item == 3
    assert not fixture.held


def test_file_display_readback_drifts_across_own_hold_identity():
    """Changing passive attached-file evidence after the hold is a changed form."""
    page = {'url': URL, 'controls': [], 'attached_file_displays': [{
        'filename': 'Test User.pdf', 'form_selector': '#app',
        'field_label': 'Resume', 'valid': True,
    }]}
    changed = deepcopy(page)
    changed['attached_file_displays'][0]['filename'] = 'other.pdf'
    assert forms._page_identity(page) != forms._page_identity(changed)
