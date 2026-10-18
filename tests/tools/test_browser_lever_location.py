"""Offline Lever Current location safety: real controller, fake key-shaped IPC.

No network, browser, Redis, employer profile or submission. The fake only
commits the synthetic fixture after an option click, never after text fill.
"""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as dom
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
            'options': current_options,
            'location_owner': {'valid': True, 'link_id': 'location-options',
                               'listbox_selector': '#location-options'},
            'location_receiver': {'declared': True, 'count': 1,
                                  'id_reference': 'location-token',
                                  'name_reference': '', 'valid': True},
            'location_selected_option': ({
                'selector': '#location-option', 'label': caption,
                'value': 'testville-verified', 'tag': 'li',
                'selected': True, 'safe': True,
            } if selected else None),
            'selected_text': caption if selected else '',
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
                 failed_commit='', press_unsupported=False, visible_label=True,
                 click_uncertain=False, receiver_shape='', owner_shape='',
                 selected_option_shape='', witness_receiver_drift=False):
        self.fixture = _lever_location_form(caption=caption)
        self.caption, self.options, self.drift = caption, options, drift
        self.failed_commit, self.press_unsupported = failed_commit, press_unsupported
        self.click_uncertain = click_uncertain
        self.visible_label = visible_label
        self.receiver_shape, self.owner_shape = receiver_shape, owner_shape
        self.selected_option_shape = selected_option_shape
        self.witness_receiver_drift = witness_receiver_drift
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
        page = location_page(typed=typed, selected=self.selected,
                             caption=self.caption, options=state_options,
                             changed=changed)
        combo = page['controls'][0]['combobox']
        shape = self.receiver_shape
        if shape.startswith('post_') and not self.selected:
            shape = ''
        else:
            shape = shape.removeprefix('post_')
        if shape in {'dangling', 'dangling_name', 'duplicate'}:
            combo['hidden_selector'] = ''
            combo['hidden_value'] = ''
            combo['location_receiver'] = {
                'declared': True, 'count': 2 if shape == 'duplicate' else 0,
                'id_reference': '' if shape == 'dangling_name' else 'location-token',
                'name_reference': 'location_token' if shape == 'dangling_name' else '',
                'valid': False,
            }
            if shape == 'duplicate':
                # Two distinct structural inventory selectors for duplicate
                # same-form id="location-token" elements in the synthetic DOM.
                second = deepcopy(page['controls'][1])
                second['selector'] = 'form > input:nth-of-type(3)'
                page['controls'].append(second)
            else:
                page['controls'].pop(1)
        elif shape == 'none':
            combo['hidden_selector'] = ''
            combo['hidden_value'] = ''
            combo['location_receiver'] = {
                'declared': False, 'count': 0,
                'id_reference': '', 'name_reference': '', 'valid': True,
            }
            page['controls'].pop(1)
        if self.owner_shape:
            combo['location_owner'] = {'valid': False, 'link_id': 'location-options'}
        if self.selected and self.selected_option_shape == 'lost':
            combo['location_selected_option'] = None
        elif self.selected and self.selected_option_shape == 'replaced':
            combo['location_selected_option'] = {
                'selector': '#different-location-option',
                'label': self.caption, 'value': 'testville-verified',
                'tag': 'li', 'selected': True, 'safe': True,
            }
        return page

    async def page(self):
        if self.buffer and not self.selected:
            self.typed_reads += 1
        return deepcopy(self._snapshot())

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            observed = evidence(self._snapshot(), visible_label=self.visible_label)
            if self.witness_receiver_drift and self.buffer and not self.selected:
                observed['controls'][0]['combobox']['location_receiver']['count'] = 0
            return {'value': observed}
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
            if self.click_uncertain:
                raise RuntimeError('simulated uncertain BrowserSkill option click')
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
async def test_uncertain_option_ipc_gates_without_a_second_click(grant_row):
    session = FakeLever(click_uncertain=True)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert [call for call in session.calls if call[0] == 'click'] == [
        ('click', '#location-option')]
    assert not any(call[0] in {'submit', 'navigate'} for call in session.calls)


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
async def test_submit_checks_reject_missing_receipt_or_changed_grant(grant_row):
    session = FakeLever()
    receipt, filled = await run_choice(session)
    committed = await session.page()
    with pytest.raises(forms.Gate, match='selection receipt'):
        forms.check_form(committed, filled, uploaded=True)
    # A copied dict cannot impersonate the controller's witnessed commit.
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [{**receipt}], committed, provenance=PROVENANCE)
    grant_row['human_approved_answers']['Current location']['value'] = 'Changed'
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [receipt], committed, provenance=PROVENANCE)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    pytest.param({'combo.selected_text': ''}, id='lost-selection'),
    pytest.param({'combo.displayed_text': ''}, id='cleared-display'),
    pytest.param({'value': ''}, id='cleared-native-input'),
    pytest.param({'has_value': False}, id='native-value-flag-lost'),
    pytest.param({'disabled': True}, id='disabled-location'),
    pytest.param({'combo.selected_value': ''}, id='machine-value-lost'),
    pytest.param({'combo.hidden_value': 'OTHER'}, id='changed-hidden-commit'),
    pytest.param({'combo.link_id': 'orphan'}, id='lost-listbox-owner'),
    pytest.param({'receiver.value': 'OTHER'}, id='changed-hidden-receiver'),
    pytest.param({'label': 'Another location'}, id='relabelled-location'),
    pytest.param({'type': 'text'}, id='combobox-type-lost'),
    pytest.param({'selector': '#replaced-location'}, id='replaced-control'),
    pytest.param({'form_action': 'https://example.invalid/apply'}, id='changed-destination'),
])
async def test_submit_checks_reject_each_location_drift(grant_row, change):
    session = FakeLever()
    receipt, filled = await run_choice(session)
    altered = location_page(typed=ANSWER, selected=True, changed=change)
    # Neither pre-submit check may let a changed/disabled control escape the
    # original witnessed selection just because its native loop would skip it.
    with pytest.raises(forms.Gate):
        forms.check_form(altered, filled, uploaded=True, receipts=[receipt])
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [receipt], altered, provenance=PROVENANCE)


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


def test_lever_dom_witnesses_are_shared_by_both_production_readers():
    # Both production scripts embed the same fixed, read-only graph. The fake
    # IPC tests below exercise the response schema; the HTML is synthetic.
    for script in (dom.PAGE, forms._FIXED_CONTROL_WITNESS):
        assert 'const leverLocationField = e =>' in script
        assert 'const leverLocationReceiver = (e, form) =>' in script
        assert 'const leverLocationOwner = e =>' in script
        assert 'const leverLocationSelected = (e, owner) =>' in script
        assert 'const identifiers=new Set([id,...options.map' in script
        assert "[aria-controls],[aria-owns],[aria-activedescendant]" in script
        assert "document.querySelectorAll('#'+CSS.escape(id)).length===1" in script
        assert 'location_receiver:leverLocationReceiver(e, form)' in script
        assert 'location_selected_option:leverLocationSelected(e, locationOwner)' in script
        assert 'listbox_visible:leverLocationVisible(list)' in script


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['dangling', 'dangling_name', 'duplicate'])
async def test_declared_receiver_must_resolve_uniquely_before_option_ipc(grant_row, shape):
    html = _lever_location_form().decode('utf-8')
    hidden = '<input id="location-token" type="hidden" name="location_token">'
    assert hidden in html
    if shape == 'duplicate':
        html = html.replace(hidden, hidden + hidden)
        assert html.count('id="location-token"') == 2
    elif shape == 'dangling_name':
        html = html.replace('data-value-input="location-token"',
                            'data-value-name="location_token"').replace(hidden, '')
        assert 'data-value-name="location_token"' in html
    else:
        html = html.replace(hidden, '')
    session = FakeLever(receiver_shape=shape)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert not any(call[0] == 'click' for call in session.calls)
    assert not any(call[0] == 'submit' for call in session.calls)


@pytest.mark.asyncio
async def test_receiver_witness_disagreement_has_no_option_click(grant_row):
    # PAGE still reports the expected single hidden token; the independent
    # fixed witness now reports a missing token. No PAGE-only admission.
    session = FakeLever(witness_receiver_drift=True)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert all(call[0] != 'click' for call in session.calls)


@pytest.mark.asyncio
async def test_only_undeclared_location_receiver_can_be_absent(grant_row):
    html = _lever_location_form().decode('utf-8')
    html = html.replace(' data-value-input="location-token"', '')
    html = html.replace('<input id="location-token" type="hidden" name="location_token">', '')
    assert 'data-value-input=' not in html and 'id="location-token"' not in html
    session = FakeLever(receiver_shape='none')
    receipt, filled = await run_choice(session)
    assert receipt['hidden_selector'] == ''
    assert receipt['hidden_value'] == ''
    final = await session.page()
    forms.check_form(final, filled, uploaded=True, receipts=[receipt])
    forms.check_approvals(PK, [receipt], final, provenance=PROVENANCE)
    assert [call for call in session.calls if call[0] == 'click'] == [
        ('click', '#location-option')]


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['post_dangling', 'post_duplicate'])
async def test_receiver_disappearing_after_option_click_cannot_submit(grant_row, shape):
    session = FakeLever(receiver_shape=shape)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert [c for c in session.calls if c[0] == 'click'] == [
        ('click', '#location-option')]
    assert not any(c[0] == 'submit' for c in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['post_dangling', 'post_duplicate'])
async def test_receiver_loss_at_final_submit_gates_both_checks(grant_row, shape):
    session = FakeLever()
    receipt, filled = await run_choice(session)
    session.receiver_shape = shape
    altered = await session.page()
    with pytest.raises(forms.Gate):
        forms.check_form(altered, filled, uploaded=True, receipts=[receipt])
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [receipt], altered, provenance=PROVENANCE)
    assert [c for c in session.calls if c[0] == 'click'] == [
        ('click', '#location-option')]


@pytest.mark.asyncio
@pytest.mark.parametrize('claimant', ['outside', 'conflicting'])
async def test_nonexclusive_listbox_owner_gates_before_option_click(grant_row, claimant):
    html = _lever_location_form().decode('utf-8')
    if claimant == 'outside':
        html = html.replace('</form>', '</form><div id="other-widget" '
                            'aria-controls="location-options"></div>')
        assert '<div id="other-widget" aria-controls="location-options">' in html
    else:
        html = html.replace('aria-controls="location-options"',
                            'aria-controls="location-options" aria-owns="another-listbox"')
        assert 'aria-owns="another-listbox"' in html
    session = FakeLever(owner_shape=claimant)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert all(c[0] != 'click' for c in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['lost', 'replaced'])
async def test_selected_option_missing_immediately_after_click_gates(grant_row, shape):
    session = FakeLever(selected_option_shape=shape)
    with pytest.raises(forms.Gate) as err:
        await run_choice(session)
    assert err.value.form_question['selector'] == '#current-location'
    assert [c for c in session.calls if c[0] == 'click'] == [
        ('click', '#location-option')]
    assert all(c[0] != 'submit' for c in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['lost', 'replaced'])
async def test_selected_option_removed_or_replaced_before_submit_gates(grant_row, shape):
    session = FakeLever()
    receipt, filled = await run_choice(session)
    session.selected_option_shape = shape
    altered = await session.page()
    # The native selected caption, machine value and hidden receiver all
    # remain unchanged; only the original selected option proof was lost.
    assert altered['controls'][0]['combobox']['selected_value'] == 'testville-verified'
    assert altered['controls'][1]['value'] == 'testville-verified'
    with pytest.raises(forms.Gate):
        forms.check_form(altered, filled, uploaded=True, receipts=[receipt])
    with pytest.raises(forms.Gate):
        forms.check_approvals(PK, [receipt], altered, provenance=PROVENANCE)
    assert not any(c[0] == 'submit' for c in session.calls)


@pytest.mark.asyncio
async def test_generic_opacity_zero_listbox_does_not_add_a_new_gate():
    # An unrelated generic combobox's PAGE sees layout rectangles (visible),
    # while the independent strict witness sees ancestor opacity:0 (not visible).
    # This Lever-only field cannot change the preexisting generic parity contract.
    page = location_page(typed='Synthetic', selected=False)
    page['url'] = 'https://example.test/jobs/synthetic'
    page['controls'][0]['label'] = 'Skills'
    combo = page['controls'][0]['combobox']
    for key in ('location_owner', 'location_receiver', 'location_selected_option'):
        combo.pop(key)
    combo['listbox_visible'] = True
    witness = deepcopy(page)
    witness['controls'][0]['combobox']['listbox_visible'] = False

    class GenericSession:
        async def call(self, *args):
            assert args == ('evaluate', forms._FIXED_CONTROL_WITNESS)
            return {'value': evidence(witness)}

        async def page(self):
            return deepcopy(page)

    observed, _ = await forms._observe_controls(GenericSession(), page)
    assert observed['controls'][0]['combobox']['listbox_visible'] is True


@pytest.mark.asyncio
async def test_lever_opacity_disagreement_still_gates():
    page = location_page(typed='Testville, United States', selected=False)
    witness = deepcopy(page)
    witness['controls'][0]['combobox']['listbox_visible'] = False

    class DisagreeSession:
        async def call(self, *args):
            assert args == ('evaluate', forms._FIXED_CONTROL_WITNESS)
            return {'value': evidence(witness)}

        async def page(self):
            return deepcopy(page)

    with pytest.raises(forms.Gate):
        await forms._observe_controls(DisagreeSession(), page)
