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
PK = 'example-co#' + JOB_ID
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
    def __init__(self, *, caption='Testville, United States', options=None, drift='',
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
        state_options = (self.options if self.options is not None else
                         [option(self.caption, value=self.caption)
                          if self.receiver_shape == 'none' else option(self.caption)])
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
            if self.selected:
                # In this variant the named input, not a removed hidden field,
                # submits the option's exact independently observed value.
                combo['selected_value'] = self.caption
                combo['location_selected_option']['value'] = self.caption
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
        if args == ('click', '#current-location'):
            # Generic ARIA picker opening is reversible, not a submitting click.
            self.started = True
            return {}
        if args[:3] == ('fill', '#current-location', '--value'):
            if not self.started or len(args) != 4:
                pytest.fail('Unapproved typing before picker open')
            self.buffer = args[3]
            return {}
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
        if args in {('click', '#location-option'), ('click', '#location-option-2')}:
            if self.click_uncertain:
                raise RuntimeError('simulated uncertain BrowserSkill option click')
            if not self.buffer or self.selected:
                pytest.fail('Option click was not the single authorized selection')
            self.selected = True
            return {}
        pytest.fail(f'Unexpected browser IPC method/arguments: {args}')


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



async def prepare_location(session, answer=ANSWER):
    """Current accessible preparation; no value grant, widget receipt or approval."""
    initial = await session.page()
    filled = {}
    action = {'action': 'combobox', 'selector': '#current-location',
              'fact': 'Current location'}
    result = await forms.execute(
        session, initial, action,
        facts={'Current location': answer} if answer else {}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=POSTING, provenance=PROVENANCE)
    return result, filled


def with_synthetic_pdf(page):
    """An offline form receiver with the one staged filename for final readback."""
    current = deepcopy(page)
    current['controls'].append({
        'selector': '#resume', 'label': 'Resume', 'question': 'Resume',
        'tag': 'input', 'type': 'file', 'required': True, 'disabled': False,
        'in_form': True, 'form_selector': '#application-form',
        'files': ['Resume.pdf'],
    })
    return current


@pytest.mark.asyncio
async def test_ungated_location_preparation_selects_visible_matching_option():
    session = FakeLever(caption=ANSWER)
    receipt, filled = await prepare_location(session)
    assert receipt == {'action': 'combobox', 'selector': '#current-location',
                       'label': 'Current location'}
    assert filled == {'#current-location': ANSWER}
    assert [call for call in session.calls if call[0] != 'evaluate'] == [
        ('click', '#current-location'),
        ('fill', '#current-location', '--value', ANSWER),
        ('click', '#location-option'),
    ]
    observed = await session.page()
    assert observed['controls'][0]['combobox']['selected_text'] == ANSWER
    assert observed['controls'][0]['combobox']['selected_value']
    forms.check_form(with_synthetic_pdf(observed), filled, True, 'Resume.pdf')
    assert not any(c[:2] == ('click', '#submit') for c in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('available', [
    [], [option('Elseville, USA')], [option('Testville, CAN')],
    [option('Testville, USA, Extra')],
])
async def test_unknown_location_option_does_not_invent_a_selection(available):
    session = FakeLever(options=available)
    receipt, filled = await prepare_location(session)
    assert receipt['action'] == 'combobox'
    assert filled == {'#current-location': ANSWER}
    assert not session.selected
    assert [c for c in session.calls if c[0] == 'click'] == [
        ('click', '#current-location')]
    assert not any(c[:2] == ('click', '#submit') for c in session.calls)


@pytest.mark.asyncio
async def test_missing_location_fact_is_an_owner_answer_request():
    session = FakeLever()
    with pytest.raises(forms.Gate) as refusal:
        await prepare_location(session, answer='')
    assert refusal.value.reason == 'owner_question'
    assert refusal.value.form_question['selector'] == '#current-location'
    assert not session.calls


@pytest.mark.asyncio
async def test_interrupted_option_click_is_not_retried_automatically():
    session = FakeLever(click_uncertain=True)
    with pytest.raises(RuntimeError, match='simulated uncertain BrowserSkill option click'):
        await prepare_location(session)
    assert [c for c in session.calls if c[0] == 'click'] == [
        ('click', '#current-location'), ('click', '#location-option')]
    assert not session.selected


@pytest.mark.asyncio
async def test_generic_final_readback_requires_nonempty_current_location_and_pdf():
    session = FakeLever(caption=ANSWER)
    _, filled = await prepare_location(session)
    current = with_synthetic_pdf(await session.page())
    forms.check_form(current, filled, True, 'Resume.pdf')
    cleared = deepcopy(current)
    cleared['controls'][0]['value'] = ''
    cleared['controls'][0]['combobox']['selected_text'] = ''
    with pytest.raises(forms.Gate, match='required accessible picker'):
        forms.check_form(cleared, filled, True, 'Resume.pdf')
    without_pdf = deepcopy(current)
    without_pdf['controls'][-1]['files'] = []
    with pytest.raises(forms.Gate, match='Exactly one current résumé input'):
        forms.check_form(without_pdf, filled, True, 'Resume.pdf')
    duplicate = deepcopy(current)
    duplicate['controls'].append(deepcopy(duplicate['controls'][0]))
    with pytest.raises(forms.Gate, match='unique selectors'):
        forms.check_form(duplicate, filled, True, 'Resume.pdf')


def test_lever_dom_witnesses_are_shared_by_both_production_readers():
    # Both real readers must use the same generic ARIA ownership and actual
    # native form/hidden receiver, not retired Lever-specific shape evidence.
    for script in (dom.PAGE, forms._FIXED_CONTROL_WITNESS):
        assert 'const linkId = e =>' in script
        assert 'const linkedListbox = e =>' in script
        assert 'const currentListbox = e =>' in script
        assert "(listForm && listForm !== nativeForm(e))" in script
        assert "const hiddenId = e.getAttribute('data-value-input')" in script
        assert "const hiddenName = e.getAttribute('data-value-name')" in script
        assert "e.form || e.closest('form')" in script
        assert 'hidden_selector:' in script and 'hidden_value:' in script
        assert 'const leverLocationField = e =>' not in script


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

    # Exact PAGE vs independent fixed-witness equality is still required
    # for a fresh reader; no Lever-only opacity exception can mint authority.
    with pytest.raises(forms.Gate, match='predicate=combobox.equal'):
        await forms._observe_controls(GenericSession(), page)


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
