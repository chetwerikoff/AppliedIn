"""Offline Lever Current location safety: real controller, fake key-shaped IPC.

No network, browser, Redis, employer profile or submission. The fake only
commits the synthetic fixture after an option click, never after text fill.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill_fixture import _lever_location_form
from tools.lever_location import equivalent_location, parse_location, press_keys


JOB_ID = '00000000-0000-0000-0000-000000000001'
POSTING = f'https://jobs.lever.co/example-co/{JOB_ID}'
URL = POSTING + '/apply'
PK = 'example-co#synthetic-job'
ANSWER = 'Testville, United States'
PROVENANCE = forms.ApplicationEntryProvenance(
    pk=PK, company='example-co', ats='lever', job_id=JOB_ID,
    wrapper=POSTING, board='example-co', url=URL,
)


def option(caption='Testville, USA', *, selector='#location-option',
           value='testville-verified', **changes):
    return {'selector': selector, 'unique': True, 'tag': 'li', 'native_type': '',
            'label': caption, 'value': value, 'submit': False, 'navigates': False,
            'safe': True, 'selected': False, **changes}


def location_page(*, typed='', selected=False, caption='Testville, USA',
                  options=None, changed=None):
    current_options = (deepcopy(options) if options is not None
                       else [option(caption)])
    if not typed or selected:
        current_options = []
    commit_value = 'testville-verified' if selected else ''
    field = {
        'selector': '#current-location', 'label': 'Current location',
        'question': '', 'tag': 'input', 'type': 'combobox', 'required': True,
        'disabled': False, 'in_form': True, 'form_selector': '#application-form',
        'native_form_owner_selector': '#application-form',
        'form_action': URL, 'formaction': '', 'name': 'location',
        'submit': False, 'checked': False, 'options': [],
        'value': caption if selected else typed, 'has_value': bool(typed or selected),
        'combobox': {
            'linked': True, 'link_id': 'location-options',
            'listbox_selector': '#location-options',
            'listbox_visible': bool(typed and not selected),
            'options': current_options, 'selected_text': caption if selected else '',
            'displayed_text': caption if selected else typed,
            'selected_value': commit_value,
            'hidden_selector': '#location-token',
            'hidden_value': commit_value,
        },
    }
    submit = {
        'selector': '#submit', 'label': 'Submit application',
        'question': '', 'tag': 'button', 'type': 'submit',
        'required': False, 'disabled': False, 'in_form': True,
        'form_selector': '#application-form',
        'native_form_owner_selector': '#application-form',
        'form_action': URL, 'formaction': '',
        'submit': True, 'checked': False, 'options': [],
        'name': '', 'value': '', 'has_value': False, 'combobox': None,
    }
    receiver = {
        'selector': '#location-token', 'label': '', 'question': '',
        'tag': 'input', 'type': 'hidden', 'required': False, 'disabled': False,
        'in_form': True, 'form_selector': '#application-form',
        'native_form_owner_selector': '#application-form',
        'form_action': URL, 'formaction': '', 'name': 'location_token',
        'submit': False, 'checked': False, 'options': [], 'combobox': None,
        'value': commit_value, 'has_value': bool(commit_value),
    }
    if changed:
        for key, value in changed.items():
            if key.startswith('combo.'):
                field['combobox'][key.removeprefix('combo.')] = value
            elif key.startswith('receiver.'):
                receiver[key.removeprefix('receiver.')] = value
            else:
                field[key] = value
    return {
        'url': URL, 'text': 'Synthetic Lever application', 'title': 'Testville job',
        'entry': {'frames': [], 'response_error': False,
                  'root': {'selector': '#application-form', 'action': URL}},
        'controls': [field, receiver, submit], 'inventory_verified': True,
        'truncated': False, 'opaque_controls': False,
        'shadow_roots': False, 'unsupported_frames': [],
    }


def evidence(page, *, visible_label=True):
    return {'url': page['url'], 'entry': deepcopy(page['entry']), 'controls': [
        {'selector': c['selector'], 'unique': True, 'unique_form': True,
         'tag': c['tag'], 'type': c['type'], 'in_form': c['in_form'],
         'form_selector': c['form_selector'], 'form_action': c['form_action'],
         'native_form_owner_selector': c.get('native_form_owner_selector'),
         'formaction': c['formaction'], 'disabled': c['disabled'],
         'readonly': False, 'inert': False, 'visible': True,
         'visible_text': ('Current location' if visible_label else '')
             if c['selector'] == '#current-location' else '',
         'options': deepcopy(c['options']), 'combobox': deepcopy(c.get('combobox'))}
        for c in page['controls']]}


class FakeLever:
    """Fixture-shaped fake: press opens options, only an option click commits."""
    def __init__(self, *, caption='Testville, USA', options=None, drift='',
                 failed_commit='', press_unsupported=False, visible_label=True):
        self.fixture = _lever_location_form(caption=caption)
        self.caption, self.options, self.drift = caption, options, drift
        self.failed_commit, self.press_unsupported = failed_commit, press_unsupported
        self.visible_label = visible_label
        self.buffer, self.selected, self.typed_reads = '', False, 0
        self.calls = []
        self.started = False

    def _snapshot(self):
        typed = self.buffer
        state_options = self.options if self.options is not None else [option(self.caption)]
        changed = {}
        if self.drift and self.typed_reads >= 3 and typed and not self.selected:
            if self.drift == 'option':
                state_options = [option('Elseville, USA')]
            elif self.drift == 'field':
                changed['label'] = 'Other field'
            elif self.drift == 'listbox':
                changed['combo.listbox_selector'] = '#swapped-listbox'
        if self.selected:
            if self.failed_commit == 'typed-only':
                changed['combo.selected_text'] = ''
            elif self.failed_commit == 'value':
                changed['combo.selected_value'] = ''
            elif self.failed_commit == 'cleared':
                changed.update(value='', has_value=False,
                               **{'combo.displayed_text': ''})
            elif self.failed_commit == 'receiver':
                changed['receiver.value'] = 'wrong'
            elif self.failed_commit == 'hidden':
                changed['combo.hidden_value'] = 'wrong'
        return location_page(typed=typed, selected=self.selected,
                             caption=self.caption, options=state_options,
                             changed=changed)

    async def page(self):
        if self.buffer and not self.selected:
            self.typed_reads += 1
        return deepcopy(self._snapshot())

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            return {'value': evidence(self._snapshot(), visible_label=self.visible_label)}
        if args == ('focus', '#current-location'):
            self.started = True
            return {}
        if args[:1] == ('press',) and args[2:] == ('--selector', '#current-location'):
            if self.press_unsupported:
                raise RuntimeError('unsupported simulated BrowserSkill press command')
            if not self.started:
                pytest.fail('Press dispatched without focus')
            if args[1] == 'Ctrl+A':
                self.buffer = ''
            elif args[1] == 'Backspace':
                self.buffer = ''
            else:
                self.buffer += ' ' if args[1] == 'Space' else args[1]
            return {}
        if args == ('click', '#location-option'):
            if not self.buffer or self.selected:
                pytest.fail('Option click was not the single authorized selection')
            self.selected = True
            return {}
        pytest.fail(f'Unexpected browser IPC method/arguments: {args}')


@pytest.fixture
def grant_row(monkeypatch):
    row = {
        'pk': PK, 'status': 'submitting', 'possible_submission': False,
        'human_approved_answers': {
            'Current location': {
                'value': ANSWER, 'selector': '#current-location', 'url': URL,
                'label': 'Current location', 'question': '', 'tag': 'input',
                'type': 'combobox', 'form_selector': '#application-form',
                'form_action': URL,
            },
        },
    }
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    # Synthetic row/config are not the owner's real tracking or watchlist.
    monkeypatch.setattr(forms, '_alias_current', lambda *_args, **_kw: True)
    return row


async def run_choice(session, *, approved=ANSWER, verb='combobox'):
    initial = await session.page()
    filled = {}
    receipt = await forms.execute(
        session, initial, {'action': verb, 'selector': '#current-location',
                           'fact': 'Current location'},
        facts={'Current location': approved}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=POSTING, provenance=PROVENANCE)
    return receipt, filled


def test_strict_iso_names_whole_city_and_supported_press_keys():
    assert parse_location(ANSWER) == ('testville', 'USA')
    assert equivalent_location(ANSWER, 'Testville, USA')
    assert equivalent_location(ANSWER, '  TESTVILLE,   united states ')
    assert not equivalent_location(ANSWER, 'New Testville, USA')
    assert not equivalent_location('York, United States', 'New York, USA')
    assert not equivalent_location(ANSWER, 'Testville, CAN')
    assert not equivalent_location(ANSWER, 'Testville, ZZZ')
    assert not equivalent_location(ANSWER, 'Testville, U.S.A.')
    assert not equivalent_location(ANSWER, 'Testville, USA, Extra')
    assert not equivalent_location('Testville United States', 'Testville, USA')
    assert not equivalent_location('', 'Testville, USA')
    assert press_keys(ANSWER) == tuple('Space' if c == ' ' else c for c in ANSWER)
    assert press_keys('A' * 129) is None
    assert press_keys('Téstville, United States') is None


@pytest.mark.parametrize('caption', ['Testville, USA', 'Testville, United States'])
def test_offline_location_fixture_only_commits_on_option_click(caption):
    html = _lever_location_form(caption=caption).decode('utf-8')
    assert '<label for="current-location">Current location</label>' in html
    assert 'role="combobox"' in html and 'aria-controls="location-options"' in html
    assert 'role="listbox"' in html and 'role="option"' in html
    assert 'data-value-input="location-token"' in html
    assert "input.addEventListener('input'" in html
    assert "option.addEventListener('mousedown'" in html
    assert "option.addEventListener('click'" in html
    assert "input.addEventListener('blur'" in html
    assert "input.removeAttribute('data-selected-value')" in html
    assert "token.value = ''" in html
    assert "event.preventDefault()" in html
    assert caption in html


@pytest.mark.asyncio
@pytest.mark.parametrize('caption', ['Testville, USA', 'Testville, United States'])
@pytest.mark.parametrize('verb', ['fill', 'combobox'])
async def test_typed_choice_preserves_grant_and_final_report(grant_row, caption, verb):
    session = FakeLever(caption=caption)
    grant_before = deepcopy(grant_row['human_approved_answers'])
    receipt, filled = await run_choice(session, verb=verb)
    assert receipt['approved_value'] == ANSWER
    assert receipt['committed_caption'] == caption
    assert receipt['committed_value'] == 'testville-verified'
    assert filled == {'#current-location': caption}
    assert grant_row['human_approved_answers'] == grant_before
    actions = [call for call in session.calls if call[0] != 'evaluate']
    expected = [('focus', '#current-location'),
                ('press', 'Ctrl+A', '--selector', '#current-location'),
                ('press', 'Backspace', '--selector', '#current-location')]
    expected += [('press', 'Space' if c == ' ' else c,
                  '--selector', '#current-location') for c in ANSWER]
    assert actions == expected + [('click', '#location-option')]
    assert sum(call[0] == 'click' for call in session.calls) == 1
    assert all(call[0] not in {'fill', 'select', 'navigate'} for call in session.calls)
    assert sum(call[0] == 'evaluate' for call in session.calls) >= 4
    final = await session.page()
    forms.check_form(final, filled, uploaded=True, receipts=[receipt])
    forms.check_approvals(PK, [receipt], final, provenance=PROVENANCE)
    assert forms._fields_report([receipt], filled) == [
        {'label': 'Current location', 'value': caption}]
    if caption.endswith('USA'):
        assert receipt['committed_caption'] != receipt['approved_value']
    else:
        assert receipt['committed_caption'] == receipt['approved_value']


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [
    [],
    [option('York, USA')],
    [option('Testville, CAN')],
    [option('Testville, ZZZ')],
    [option('Testville, USA, Extra')],
    [option('Testville, USA'), option('Testville, United States',
                                    selector='#location-option-2',
                                    value='another-verified')],
    [option('Testville, USA'), option('Testville, USA',
                                    selector='#location-option-2',
                                    value='another-verified')],
    [option('Testville, USA', safe=False)],
    [option('Testville, USA', navigates=True)],
    [option('Testville, USA', submit=True)],
    [option('Testville, USA', unique=False)],
])
async def test_rejected_candidates_gate_same_field_before_click(grant_row, options):
    session = FakeLever(options=options)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    question = err.value.form_question
    assert question and question['pk'] == PK
    assert {k: question[k] for k in ('selector', 'label', 'form_selector',
                                    'form_action', 'url')} == {
        'selector': '#current-location', 'label': 'Current location',
        'form_selector': '#application-form', 'form_action': URL, 'url': URL}
    assert all(call[0] != 'click' for call in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('drift', ['option', 'field', 'listbox'])
async def test_control_or_option_drift_gates_before_click(grant_row, drift):
    session = FakeLever(drift=drift)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert all(call[0] != 'click' for call in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_commit', ['typed-only', 'value', 'cleared',
                                            'receiver', 'hidden'])
async def test_post_click_uncertain_commit_never_retries_or_submits(grant_row, failed_commit):
    session = FakeLever(failed_commit=failed_commit)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert [call for call in session.calls if call[0] == 'click'] == [
        ('click', '#location-option')]
    assert all(call[0] != 'submit' for call in session.calls)


@pytest.mark.asyncio
async def test_no_fallback_when_keyboard_or_label_witness_is_missing(grant_row):
    for session in [FakeLever(press_unsupported=True),
                    FakeLever(visible_label=False)]:
        with pytest.raises(forms.Gate):
            await run_choice(session)
        assert not any(call[0] in {'fill', 'click'} for call in session.calls)


@pytest.mark.asyncio
async def test_unapproved_input_never_dispatches_keyboard(grant_row):
    grant_row['human_approved_answers'].clear()
    session = FakeLever()
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert not any(call[0] in {'press', 'focus', 'click'} for call in session.calls)


@pytest.mark.asyncio
async def test_unsupported_city_key_gates_before_input(grant_row):
    value = 'Téstville, United States'
    grant_row['human_approved_answers']['Current location']['value'] = value
    session = FakeLever()
    with pytest.raises(forms.Gate) as err:
        await run_choice(session, approved=value)
    assert err.value.form_question['selector'] == '#current-location'
    assert not any(call[0] in {'press', 'click'} for call in session.calls)


@pytest.mark.asyncio
async def test_missing_provenance_refuses_before_key_or_option_ipc(grant_row):
    session = FakeLever()
    initial = await session.page()
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, initial, {'action': 'fill', 'selector': '#current-location',
                               'fact': 'Current location'},
            facts={'Current location': ANSWER}, filled={},
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=POSTING, provenance=None)
    assert not any(call[0] in {'press', 'fill', 'click'} for call in session.calls)


@pytest.mark.asyncio
async def test_submit_checks_reject_missing_or_changed_selection_and_grant(grant_row):
    session = FakeLever()
    receipt, filled = await run_choice(session)
    committed = await session.page()
    with pytest.raises(forms.Gate, match='selection receipt'):
        forms.check_form(committed, filled, uploaded=True)
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [{**receipt}], committed, provenance=PROVENANCE)
    for change in (
        {'combo.selected_text': ''},
        {'combo.displayed_text': ''},
        {'value': ''},
        {'has_value': False},
        {'disabled': True},
        {'combo.selected_value': ''},
        {'combo.hidden_value': 'OTHER'},
        {'combo.link_id': 'orphan'},
        {'receiver.value': 'OTHER'},
        {'label': 'Another location'},
        {'form_action': 'https://example.invalid/apply'},
    ):
        altered = location_page(typed=ANSWER, selected=True, changed=change)
        with pytest.raises(forms.Gate):
            forms.check_form(altered, filled, uploaded=True, receipts=[receipt])
        with pytest.raises(forms.Gate):
            forms.check_approvals(PK, [receipt], altered, provenance=PROVENANCE)
    grant_row['human_approved_answers']['Current location']['value'] = 'Changed'
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [receipt], committed, provenance=PROVENANCE)


def test_other_combobox_cannot_borrow_location_equivalence():
    other = location_page(typed=ANSWER, selected=True)
    other['controls'][0]['label'] = 'Country'
    assert not forms._lever_location_attempt(other, other['controls'][0])
    fake = {
        'action': 'combobox', 'selector': '#current-location',
        'label': 'Country', 'question': '', 'url': URL,
        'fact': 'Country', 'approved_value': ANSWER,
        'committed_caption': 'Testville, USA',
        'committed_value': 'testville-verified',
        'hidden_selector': '#location-token', 'hidden_value': 'testville-verified',
    }
    # This cannot pass the ordinary exact approved-to-committed check.
    with pytest.raises(forms.Gate, match='committed combobox value changed'):
        forms.check_approvals(PK, [fake], other)
