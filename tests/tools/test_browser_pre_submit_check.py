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


def _world(monkeypatch, tmp_path, *, case='ready', resume_name='Test User.pdf'):
    resume = tmp_path / resume_name
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


@pytest.mark.asyncio
async def test_missing_file_input_cannot_borrow_stale_complete_upload_tree(
        monkeypatch, tmp_path):
    """A form display alone cannot contradict an independent complete tree
    that still observes a live file receiver absent from controls.
    """
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    page['attached_file_displays'] = [{
        'filename': 'Test User.pdf', 'field_label': 'Resume',
        'valid': True, 'in_form': True, 'form_selector': '#app',
    }]
    page['entry'] = {'upload_tree': {
        'complete': True, 'input': 0,
        'nodes': [{'target_selector': '#resume'}],
    }}
    with pytest.raises(forms.PreSubmitGate) as refusal:
        await controller.check_pre_submit(None, page, target)
    assert refusal.value.item == 2
    assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('caption', 'mode', 'item'), [
    ('Male', 'selected', 3),
    ('Decline To Self Identify', 'selected', None),
    ('Male', 'missing', 3),
    ('Male', 'outside', 3),
    ('Male', 'ambiguous', 3),
])
async def test_observed_local_gender_selection_uses_only_existing_final_item_three(
        monkeypatch, tmp_path, caption, mode, item):
    """Observed React-select markup has blank input and no hidden receiver.
    A single same-field caption is the only selected answer; ambiguous,
    outside-field and live-region-only captions cannot invent safe consent.
    """
    from tests.tools.test_browser_combobox import _read_local_react_select_gender

    _, gender = _read_local_react_select_gender(caption, state=mode)
    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    # A proven local selected caption must satisfy required readback so
    # affirmative Gender reaches item 3; ambiguous selections remain optional
    # here to isolate the protected-answer refusal from required-field item 2.
    gender['required'] = mode == 'selected'
    gender['form_selector'] = '#app'
    gender['form_action'] = URL
    page['controls'].insert(0, gender)
    assert gender['value'] == ''
    assert gender['combobox'].get('selected_value') == ''
    if item is None:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held
    else:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == item
        assert not fixture.held


def _read_current_file_upload_group(*, case='matching'):
    """Run production PAGE against the sanitized file-upload group, offline.

    No file input remains after upload; the only surviving evidence is a
    same-group filename and explicit Remove file control.
    """
    import json
    import subprocess
    from pathlib import Path
    from tools.browser_skill_dom import PAGE

    fixture = Path(__file__).with_name('entry_dom_fixture.js').read_text()
    marker = '// Keep the 4096-node synthetic census cheap without weakening duplicate-ID proof.'
    assert fixture.count(marker) == 1
    injection = r"""
const kind=MODE;
const filename='Test-User-Resume.pdf';
const makeUploadGroup=(key, targetForm) => {
  const group=node('div',{
    id:key+'-upload',class:'file-upload',role:'group',
    'aria-labelledby':key+'-label',
  },targetForm);
  node('div',{id:key+'-label',text:kind==='other_label' ?
    'Supporting documents' : 'Resume/CV'},group);
  const placeName=parent => {
    const file=node('div',{class:'file-upload__filename'},parent);
    node('p',{class:'body body__secondary',
      text:kind==='wrong_filename' ? 'Unrelated.pdf' : filename},file);
  };
  if(kind==='outside_caption')placeName(body);
  else placeName(group);
  if(kind==='duplicate_within')placeName(group);
  if(kind!=='missing_remove')
    node('button',{type:'button','aria-label':'Remove file'},group);
};
if(kind!=='none'){
  const target=kind==='wrong_form' ?
    node('form',{id:'profile-form'},body) : form;
  makeUploadGroup('upload-resume',target);
  if(kind==='multiple')makeUploadGroup('upload-second',form);
}
"""
    script = fixture.replace(
        marker, injection.replace('MODE', json.dumps(case)) + '\n' + marker)
    completed = subprocess.run(
        ['node', '-e', script, 'synthetic',
         json.dumps({'url': URL}), PAGE],
        capture_output=True, text=True, check=True, timeout=30)
    return json.loads(completed.stdout)


@pytest.mark.asyncio
@pytest.mark.parametrize(('case', 'accepted'), [
    ('matching', True),
    ('none', False),
    ('multiple', False),
    ('duplicate_within', False),
    ('wrong_filename', False),
    ('outside_caption', False),
    ('wrong_form', False),
    ('other_label', False),
    ('missing_remove', False),
])
async def test_real_file_upload_group_reader_requires_single_bound_removable_pdf(
        monkeypatch, tmp_path, case, accepted):
    """The observed post-upload group, not a stale file input or global text,
    must prove one exact, removable attachment on the committing form.
    """
    observed = _read_current_file_upload_group(case=case)
    assert not any(c.get('type') == 'file' for c in observed['controls'])
    controller, page, target, fixture = _world(
        monkeypatch, tmp_path, resume_name='Test-User-Resume.pdf')
    # Only the isolated native application form is the submitting owner.
    target['form_selector'] = '#application-form'
    target['native_form_owner_selector'] = '#application-form'
    for c in page['controls']:
        if c.get('in_form') and c is not target:
            c['form_selector'] = '#application-form'
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    page['attached_file_displays'] = observed['attached_file_displays']
    if accepted:
        assert len(page['attached_file_displays']) == 1
        assert page['attached_file_displays'][0]['valid'] is True
        await controller.check_pre_submit(None, page, target)
        assert fixture.held
    else:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 2
        assert not fixture.held


@pytest.mark.asyncio
@pytest.mark.parametrize(('upload_completed', 'same_identity'), [
    (False, True), (True, False),
])
async def test_real_file_group_cannot_waive_upload_or_staged_pdf_identity(
        monkeypatch, tmp_path, upload_completed, same_identity):
    """A valid visible filename never substitutes for the original code-owned
    upload completion and unchanged row-PDF identity.
    """
    observed = _read_current_file_upload_group()
    controller, page, target, fixture = _world(
        monkeypatch, tmp_path, resume_name='Test-User-Resume.pdf')
    target['form_selector'] = '#application-form'
    target['native_form_owner_selector'] = '#application-form'
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    page['attached_file_displays'] = observed['attached_file_displays']
    controller.upload_completed = upload_completed
    if not same_identity:
        controller.upload_identity = ('different-synthetic-pdf',)
    with pytest.raises(forms.PreSubmitGate) as refused:
        await controller.check_pre_submit(None, page, target)
    assert refused.value.item == 2
    assert not fixture.held


@pytest.mark.parametrize('root_selector,accepted', [
    ('#proven-application', True), ('#unrelated-profile', False),
])
def test_removed_file_display_keeps_existing_formless_root_binding(
        monkeypatch, tmp_path, root_selector, accepted):
    """An attached filename may belong only to the proven committing root,
    never an adjacent formless profile section.
    """
    controller, page, target, _fixture = _world(
        monkeypatch, tmp_path, resume_name='Test-User-Resume.pdf')
    target.update(in_form=False, form_selector='', native_form_owner_selector='')
    page['entry'] = {'formless': {
        'root': '#proven-application', 'container': '#proven-application'}}
    page['controls'] = [c for c in page['controls'] if c.get('type') != 'file']
    page['attached_file_displays'] = [{
        'filename': 'Test-User-Resume.pdf', 'field_label': 'Resume/CV',
        'valid': True, 'in_form': False, 'form_selector': '',
        'root_selector': root_selector,
    }]
    args = dict(submit_target=target, formless_members=[page['controls'][0]])
    if accepted:
        forms.check_form(page, {}, True, 'Test-User-Resume.pdf', **args)
    else:
        with pytest.raises(forms.Gate, match='committing form'):
            forms.check_form(page, {}, True, 'Test-User-Resume.pdf', **args)


@pytest.mark.asyncio
@pytest.mark.parametrize(('scenario', 'accepted'), [
    ('resume_empty_cover', True),
    ('resume_other_cover', True),
    ('staged_cover_only', False),
    ('two_resume', False),
    ('resume_and_cover_staged', False),
    ('cover_without_resume', False),
])
async def test_final_resume_display_candidates_ignore_unrelated_upload_fields(
        monkeypatch, tmp_path, scenario, accepted):
    """An ATS can show multiple attached-file groups. Only the Resume/CV
    field proves the staged PDF; an empty or differently named Cover Letter
    must not block it, and a staged filename in Cover Letter must not pass.
    """
    observed = _read_current_file_upload_group()
    resume_displays = observed['attached_file_displays']
    assert len(resume_displays) == 1
    resume = deepcopy(resume_displays[0])
    assert resume['valid'] is True
    assert resume['field_label'] == 'Resume/CV'
    assert resume['filename'] == 'Test-User-Resume.pdf'

    # Both displays are synthetic projections of the same observed, scoped
    # file-upload shape. They differ only in their question and filename.
    cover = deepcopy(resume)
    cover['field_label'] = 'Cover Letter'
    cover['filename'] = 'Unrelated-Cover.pdf'
    if scenario == 'resume_empty_cover':
        cover.update(filename='', valid=False)
        displays = [resume, cover]
    elif scenario == 'resume_other_cover':
        displays = [resume, cover]
    elif scenario == 'staged_cover_only':
        resume.update(filename='', valid=False)
        cover['filename'] = 'Test-User-Resume.pdf'
        displays = [resume, cover]
    elif scenario == 'two_resume':
        displays = [resume, deepcopy(resume)]
    elif scenario == 'resume_and_cover_staged':
        cover['filename'] = 'Test-User-Resume.pdf'
        displays = [resume, cover]
    else:
        assert scenario == 'cover_without_resume'
        cover['filename'] = 'Test-User-Resume.pdf'
        displays = [cover]

    controller, page, target, fixture = _world(
        monkeypatch, tmp_path, resume_name='Test-User-Resume.pdf')
    # The synthetic reader owns #application-form, matching the Submit's
    # proven native owner. The upload input is deliberately removed.
    target['form_selector'] = '#application-form'
    target['native_form_owner_selector'] = '#application-form'
    page['controls'] = [
        c for c in page['controls'] if c.get('type') != 'file']
    for c in page['controls']:
        if c.get('in_form') is True:
            c['form_selector'] = '#application-form'
    assert all(d.get('form_selector') == '#application-form' for d in displays)
    page['attached_file_displays'] = displays

    if accepted:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held and not controller.committed
    else:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 2
        assert refused.value.reason == 'defect_pre_submit_item_2'
        assert not fixture.held and not controller.committed


def _read_greenhouse_style_react_select_choice(question, caption, state, reader):
    """Actual PAGE/fixed reader over one offline, form-owned React-select shape.

    The search input stays empty. Selected evidence exists only in exactly one
    local div.select__single-value beside it; no hidden value or live-region
    message grants submission authority.
    """
    import json
    import subprocess
    from pathlib import Path

    fixture = Path(__file__).with_name('entry_dom_fixture.js').read_text()
    marker = '// Keep the 4096-node synthetic census cheap without weakening duplicate-ID proof.'
    assert fixture.count(marker) == 1
    injection = r"""
const state=SCENARIO, question=QUESTION, chosen=CAPTION;
const field=node('div',{class:'form-group'},form);
node('label',{id:'work-choice-label','for':'work-choice',text:question},field);
const selectRoot=node('div',{class:'select__container'},field);
const valueBox=node('div',{class:state==='placeholder' ?
  'select__value-container' :
  'select__value-container select__value-container--has-value'},selectRoot);
if(state==='selected' || state==='ambiguous' || state==='hidden')
  node('div',{class:'select__single-value',text:chosen,
    ...(state==='hidden' ? {rect:null} : {})},valueBox);
if(state==='ambiguous')
  node('div',{class:'select__single-value',text:'Synthetic second choice'},valueBox);
if(state==='placeholder')
  node('div',{class:'select__placeholder',text:'Select...'},valueBox);
const inputBox=node('div',{class:'select__input-container','data-value':''},valueBox);
const fieldInput=node('input',{
  id:'work-choice',type:'text',name:'work_choice',role:'combobox',required:true,
  'aria-required':'true','aria-labelledby':'work-choice-label',
  'aria-expanded':'false',
},inputBox);
fieldInput.value='';
"""
    injection = (injection.replace('SCENARIO', json.dumps(state))
                 .replace('QUESTION', json.dumps(question))
                 .replace('CAPTION', json.dumps(caption)))
    script = fixture.replace(marker, injection + '\n' + marker)
    completed = subprocess.run(
        ['node', '-e', script, 'synthetic',
         json.dumps({'url': URL}), reader],
        check=True, capture_output=True, text=True, timeout=30)
    observed = json.loads(completed.stdout)
    field = next(c for c in observed['controls']
                 if c.get('selector') == '#work-choice')
    return observed, field


@pytest.mark.asyncio
@pytest.mark.parametrize(('question', 'caption'), [
    ('Country', 'Synthetic Country'),
    ('Work authorization', 'Authorized for synthetic work'),
])
@pytest.mark.parametrize(('state', 'passes_item_2'), [
    ('selected', True),
    ('placeholder', False),
    ('ambiguous', False),
    ('hidden', False),
])
async def test_required_nonprotected_local_react_select_final_readback(
        monkeypatch, tmp_path, question, caption, state, passes_item_2):
    """A required form-owned country/work-authorization picker can submit
    only a unique visible same-field selection, never its blank search input
    or a placeholder/ambiguous or invisible local caption.
    """
    from tools.browser_skill_dom import PAGE

    _, chosen = _read_greenhouse_style_react_select_choice(
        question, caption, state, PAGE)
    _, fixed = _read_greenhouse_style_react_select_choice(
        question, caption, state, forms._FIXED_CONTROL_WITNESS)
    assert chosen['combobox'] == fixed['combobox']
    assert chosen['tag'] == 'input' and chosen['type'] == 'combobox'
    assert chosen['required'] is True
    assert chosen['value'] == '' and chosen['has_value'] is False
    assert chosen['in_form'] is True
    assert chosen['form_selector'] == '#application-form'
    if passes_item_2:
        assert chosen['combobox']['selected_text'] == caption
        assert chosen['combobox']['displayed_text'] == caption
        assert chosen['combobox']['local_selected_proven'] is True
        assert chosen['combobox']['selected_caption_unreadable'] is False
    else:
        assert not chosen['combobox'].get('selected_text')
        assert chosen['combobox'].get('local_selected_proven') is not True
        # A selected-looking but missing/ambiguous/hidden caption is unreadable;
        # an unselected placeholder has no claimed selection in the first place.
        if state != 'placeholder':
            assert chosen['combobox']['selected_caption_unreadable'] is True

    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    target['form_selector'] = '#application-form'
    target['native_form_owner_selector'] = '#application-form'
    for control in page['controls']:
        if control.get('in_form') is True:
            control['form_selector'] = '#application-form'
    page['controls'].insert(0, chosen)
    if passes_item_2:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held
    else:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 2
        assert refused.value.reason == 'defect_pre_submit_item_2'
        assert not fixture.held


def _read_greenhouse_phone_country_react_select(state, reader):
    """Production reader over a synthetic Greenhouse-style phone-country field.

    The phone-country #country search input remains blank. Only its own one
    visible select__single-value in the same selected value-container counts.
    """
    import json
    import subprocess
    from pathlib import Path

    fixture = Path(__file__).with_name('entry_dom_fixture.js').read_text()
    marker = '// Keep the 4096-node synthetic census cheap without weakening duplicate-ID proof.'
    assert fixture.count(marker) == 1
    injection = r"""
const countryState=COUNTRY_STATE;
const phoneField=node('fieldset',{class:'phone-input'},form);
node('label',{'for':'phone',text:'Phone'},phoneField);
const phone=node('input',{
  id:'phone',name:'phone',type:'tel',required:true,
},phoneField);
phone.value='+15550101010';
node('label',{'for':'country',id:'phone-country-label',text:'Phone country'},phoneField);
const countryWrap=node('div',{class:'phone-input__country'},phoneField);
const selectRoot=node('div',{class:'select__container'},countryWrap);
const valueBox=node('div',{
  class:countryState==='placeholder'
    ? 'select__value-container'
    : 'select__value-container select__value-container--has-value',
},selectRoot);
if(countryState==='selected' || countryState==='ambiguous')
  node('div',{class:'select__single-value',text:'+1'},valueBox);
if(countryState==='ambiguous')
  node('div',{class:'select__single-value',text:'+99'},valueBox);
if(countryState==='placeholder')
  node('div',{class:'select__placeholder',text:'Select...'},valueBox);
const inputBox=node('div',{
  class:'select__input-container','data-value':'',
},valueBox);
const country=node('input',{
  id:'country',type:'text',role:'combobox',required:true,
  'aria-required':'true','aria-labelledby':'phone-country-label',
  'aria-expanded':'false',
},inputBox);
country.value='';
"""
    injection = injection.replace('COUNTRY_STATE', json.dumps(state))
    script = fixture.replace(marker, injection + '\n' + marker)
    completed = subprocess.run(
        ['node', '-e', script, 'synthetic', json.dumps({'url': URL}), reader],
        check=True, capture_output=True, text=True, timeout=30)
    observed = json.loads(completed.stdout)
    field = next(c for c in observed['controls']
                 if c.get('selector') == '#country')
    return observed, field


@pytest.mark.asyncio
@pytest.mark.parametrize(('state', 'passes_item_two'), [
    ('selected', True),
    ('placeholder', False),
    ('ambiguous', False),
])
async def test_greenhouse_phone_country_local_choice_item_two(
        monkeypatch, tmp_path, state, passes_item_two):
    """Selected +1 must survive both independent reads on #application-form;
    blank placeholders and two local captions never satisfy required item 2.
    """
    from tools.browser_skill_dom import PAGE

    observed, country = _read_greenhouse_phone_country_react_select(state, PAGE)
    _, fixed = _read_greenhouse_phone_country_react_select(
        state, forms._FIXED_CONTROL_WITNESS)
    assert country['combobox'] == fixed['combobox']
    assert {'#country', '#phone'} <= {
        c['selector'] for c in observed['controls']}
    assert country['form_selector'] == '#application-form'
    assert country['in_form'] is True and country['required'] is True
    assert country['type'] == 'combobox' and country['tag'] == 'input'
    assert country['value'] == '' and country['has_value'] is False
    assert not country['combobox'].get('selected_value')
    if passes_item_two:
        assert country['combobox']['selected_text'] == '+1'
        assert country['combobox']['displayed_text'] == '+1'
        assert country['combobox']['local_selected_proven'] is True
        assert country['combobox']['selected_caption_unreadable'] is False
    else:
        assert not country['combobox'].get('selected_text')
        assert country['combobox'].get('local_selected_proven') is not True
        if state == 'ambiguous':
            assert country['combobox']['selected_caption_unreadable'] is True

    controller, page, target, fixture = _world(monkeypatch, tmp_path)
    for existing in page['controls']:
        if existing.get('in_form') is True:
            existing['form_selector'] = '#application-form'
    target['native_form_owner_selector'] = '#application-form'
    page['controls'].insert(0, country)
    if passes_item_two:
        await controller.check_pre_submit(None, page, target)
        assert fixture.held and not controller.committed
    else:
        with pytest.raises(forms.PreSubmitGate) as refused:
            await controller.check_pre_submit(None, page, target)
        assert refused.value.item == 2
        assert refused.value.reason == 'defect_pre_submit_item_2'
        assert not fixture.held and not controller.committed
