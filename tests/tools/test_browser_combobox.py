"""Offline ARIA combobox authorization and commit-readback regressions."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools import browser_skill_apply as forms

URL = 'http://127.0.0.1/fixture/example-co/job/1'
PK = 'example-co#1'
OPTIONS = [
    {'selector': '#country-example', 'unique': True, 'label': 'Example Country',
     'value': 'EX', 'selected': False, 'safe': True},
    {'selector': '#country-other', 'unique': True, 'label': 'Other Country',
     'value': 'OT', 'selected': False, 'safe': True},
]

def control(*, selected_text='', selected_value='', hidden_value='',
            hidden_selector='#country-code', required=True):
    return {
        'selector': '#country', 'label': 'Country', 'question': 'What country?',
        'tag': 'input', 'type': 'combobox', 'required': required, 'disabled': False,
        'value': 'Example Country' if selected_text else '', 'has_value': bool(selected_text),
        'in_form': True, 'form_selector': '#application', 'form_action': URL,
        'formaction': '', 'submit': False, 'checked': False, 'name': 'country',
        'options': [], 'combobox': {
            'linked': True, 'link_id': 'country-list',
            'listbox_selector': '#country-list',
            'options': deepcopy(OPTIONS), 'selected_text': selected_text,
            'displayed_text': selected_text, 'selected_value': selected_value,
            'hidden_selector': hidden_selector, 'hidden_value': hidden_value,
        },
    }

def button():
    return {**control(), 'selector': '#submit', 'label': 'Submit application',
            'question': '', 'tag': 'button', 'type': 'submit', 'required': False,
            'native_form_owner_selector': '#application',
            'submit': True, 'options': [], 'combobox': None}


def page(combo=None):
    target = combo or control()
    controls = [target, button()]
    if target['combobox'] and target['combobox'].get('hidden_selector'):
        value = target['combobox']['hidden_value']
        controls.append({
            'selector': target['combobox']['hidden_selector'],
            'label': 'Country code', 'question': '',
            'tag': 'input', 'type': 'hidden', 'required': False, 'disabled': False,
            'value': value, 'has_value': bool(value),
            'in_form': True, 'form_selector': '#application', 'form_action': URL,
            'formaction': '', 'submit': False, 'checked': False, 'name': 'country_code',
            'options': [],
        })
    return {'url': URL, 'text': 'Synthetic form', 'controls': controls,
            'inventory_verified': True, 'truncated': False, 'opaque_controls': False,
            'shadow_roots': False, 'unsupported_frames': []}


def evidence(current):
    return {'url': current['url'], 'controls': [
        {'selector': c['selector'], 'unique': True, 'unique_form': True,
         'tag': c['tag'], 'type': c['type'], 'in_form': c['in_form'],
         'form_selector': c['form_selector'], 'form_action': c['form_action'],
         'native_form_owner_selector': c.get('native_form_owner_selector'),
         'formaction': c['formaction'], 'disabled': c['disabled'],
         'readonly': False, 'inert': False, 'visible': True, 'visible_text': '',
         'options': deepcopy(c['options']), 'combobox': deepcopy(c.get('combobox'))}
        for c in current['controls']]}


class Session:
    def __init__(self, initial, committed=None):
        self.current = initial
        self.committed = committed
        self.calls = []
        self.clicked = False

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            return {'value': evidence(self.committed if self.clicked else self.current)}
        if args == ('click', '#country-example'):
            assert self.committed is not None
            self.clicked = True
            return {}
        pytest.fail(f'Unexpected browser IPC: {args}')

    async def page(self):
        return deepcopy(self.committed if self.clicked else self.current)


class DynamicSession:
    def __init__(self, initial, opened, committed):
        self.initial, self.opened, self.committed = initial, opened, committed
        self.stage = 0
        self.calls = []

    def current(self):
        return (self.initial, self.initial, self.opened, self.committed)[self.stage]

    async def call(self, *args, **kwargs):
        self.calls.append(args)
        if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
            return {'value': evidence(self.current())}
        if args == ('click', '#country'):
            self.stage = 1
            return {}
        if args == ('fill', '#country', '--value', 'Example Country'):
            self.stage = 2
            return {}
        if args == ('click', '#country-example'):
            self.stage = 3
            return {}
        pytest.fail(f'Unexpected browser IPC: {args}')

    async def page(self):
        return deepcopy(self.current())
def grant(answer='Example Country'):
    target = control()
    return {'value': answer, 'selector': target['selector'], 'url': URL,
            'label': target['label'], 'question': target['question'],
            'tag': target['tag'], 'type': target['type'],
            'form_selector': target['form_selector'], 'form_action': URL}


def test_combobox_grant_binds_stable_control_not_transient_options():
    current = page()
    target = current['controls'][0]
    question = forms._disclosure_question(
        current, target, {'#country': {'visible': True, 'readonly': False,
                                        'disabled': False, 'inert': False,
                                        'combobox': target['combobox']}}, URL)
    assert question['label'] == 'What country?'
    assert 'options' not in question

def test_resume_job_mints_the_exact_combobox_grant_from_direct_gate(monkeypatch):
    from agent import run
    current = page()
    target = current['controls'][0]
    row = {'pk': PK, 'status': 'submitting', 'jd_url': URL,
           'company': 'example-co', 'human_approved_answers': {}}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    with pytest.raises(forms.Gate) as raised:
        asyncio.run(forms.execute(
            Session(current), current, {'action': 'combobox',
                                        'selector': '#country',
                                        'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL))
    question = raised.value.form_question
    assert question['selector'] == '#country'
    row.update(status='needs_human', gate_source='applier', gate_call_id='direct',
               gate_pending={'question': 'Answer the observed field.',
                             'form_question': question})

    class Tracking:
        def get(self, pk):
            assert pk == PK
            return deepcopy(row)

        def set_status(self, pk, status, **changes):
            row.update(status=status, **changes)

    stores = SimpleNamespace(tracking=Tracking(), answer_bank=SimpleNamespace(
        put=lambda *args, **kwargs: None))
    monkeypatch.setattr(run.submit_hold, 'blocked', lambda *a, **kw: False)
    monkeypatch.setattr(run, '_enqueue_apply', lambda *a, **kw: {'result': 'queued'})
    assert run.resume_job(PK, 'Example Country', stores)['result'] == 'queued'
    saved = row['human_approved_answers']['What country?']
    assert saved['value'] == 'Example Country'
    assert saved['selector'] == '#country' and saved['form_selector'] == '#application'
    monkeypatch.setattr(forms, '_row', lambda _pk: {**row, 'status': 'submitting'})
    forms.exact_approval(PK, 'What country?', 'Example Country',
                         target=target, url=URL)
    current = page()
    committed = page(control(selected_text='Example Country', selected_value='EX',
                             hidden_value='EX'))
    session = Session(current, committed)
    filled = {}
    receipt = asyncio.run(forms.execute(
        session, current, {'action': 'combobox', 'selector': '#country',
                           'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL))
    forms.check_form(page(committed['controls'][0]), filled, True,
                     receipts=[receipt])
    forms.check_approvals(PK, [receipt], committed)


def test_combobox_choice_requires_caption_not_machine_value_and_exact_grant(monkeypatch):
    current = page()
    row = {'pk': PK, 'status': 'submitting', 'human_approved_answers': {}}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    session = Session(current)
    with pytest.raises(forms.Gate):
        asyncio.run(forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'EX'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='', pk=PK,
            job_url=URL))
    assert not any(call[0] == 'click' for call in session.calls)


async def test_committed_caption_and_hidden_machine_value_are_verified(monkeypatch):
    current = page()
    committed = page(control(selected_text='Example Country', selected_value='EX',
                             hidden_value='EX'))
    row = {'pk': PK, 'status': 'submitting',
           'human_approved_answers': {'What country?': grant()}}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    session = Session(current, committed)
    filled = {}
    receipt = await forms.execute(
        session, current, {'action': 'combobox', 'selector': '#country',
                           'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL)
    assert receipt['approved_value'] == 'Example Country'
    assert receipt['hidden_value_before'] == ''
    assert receipt['hidden_value'] == receipt['committed_value'] == 'EX'
    assert filled == {'#country': 'Example Country'}
    assert ('click', '#country-example') in session.calls
    forms.check_form(page(committed['controls'][0]), filled, True,
                     receipts=[receipt])
    forms.check_approvals(PK, [receipt], committed)

    changed_hidden = page(control(selected_text='Example Country',
                                selected_value='EX', hidden_value='OTHER'))
    with pytest.raises(forms.Gate, match='A hidden form value is not independently authorized'):
        forms.check_form(changed_hidden, filled, True, receipts=[receipt])

    changed_machine_value = page(control(
        selected_text='Example Country', selected_value='OT', hidden_value='OT'))
    with pytest.raises(forms.Gate, match='The committed accessible choice or form value changed\.'):
        forms.check_approvals(PK, [receipt], changed_machine_value)


async def test_popup_link_may_disappear_after_verified_form_commit(monkeypatch):
    """A closed listbox need not retain aria-controls through final Submit."""
    current = page()
    committed = page(control(selected_text='Example Country',
                             selected_value='EX', hidden_value='EX'))
    chosen = committed['controls'][0]['combobox']
    chosen.update(linked=False, link_id='', listbox_selector='', options=[])
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    session = Session(current, committed)
    filled = {}
    receipt = await forms.execute(
        session, current, {'action': 'combobox', 'selector': '#country',
                           'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL)
    assert receipt['committed_value'] == receipt['hidden_value'] == 'EX'
    assert ('click', '#country-example') in session.calls
    # A bare PAGE projection may not pretend a formerly mounted listbox
    # remained bound. Only the actual verified action receipt permits final.
    with pytest.raises(forms.Gate, match='Unverified or changed combobox'):
        forms.check_form(committed, filled, True)
    forms.check_form(committed, filled, True, receipts=[receipt])
    forms.check_approvals(PK, [receipt], committed)
    wrong_form_value = deepcopy(committed)
    wrong_form_value['controls'][-1]['value'] = 'OTHER'
    with pytest.raises(forms.Gate, match='hidden form value'):
        forms.check_form(wrong_form_value, filled, True, receipts=[receipt])


async def test_unbound_form_receiver_denies_display_only_combobox(monkeypatch):
    """A visible choice with no named input or hidden receiver cannot Submit."""
    current = page(control(hidden_selector=''))
    committed = page(control(selected_text='Example Country',
                             selected_value='EX', hidden_selector=''))
    committed['controls'][0]['name'] = ''  # no submitted form input name
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    session = Session(current, committed)
    filled = {}
    with pytest.raises(forms.Gate, match='no verified form-own value receiver'):
        await forms.execute(
            session, current,
            {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled=filled,
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=URL)
    assert ('click', '#country-example') in session.calls
    assert filled == {}
    with pytest.raises(forms.Gate, match='no verified form-own value receiver'):
        forms.check_form(committed, {'#country': 'Example Country'}, True,
                         receipts=[{'action': 'combobox', 'selector': '#country',
                                    'committed_caption': 'Example Country',
                                    'committed_value': 'EX',
                                    'hidden_selector': '', 'hidden_value': ''}])


@pytest.mark.parametrize(('before', 'after'), [
    ('EX', 'EX'),  # A pre-existing value unchanged by this selection is not authority.
    ('', 'OTHER'),  # A changed value must equal the selected option's machine value.
])
async def test_hidden_value_must_change_to_the_selected_machine_value(
        monkeypatch, before, after):
    current = page(control(hidden_value=before))
    committed = page(control(selected_text='Example Country',
                             selected_value='EX', hidden_value=after))
    row = {'pk': PK, 'status': 'submitting',
           'human_approved_answers': {'What country?': grant()}}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    session = Session(current, committed)
    with pytest.raises(forms.Gate,
                       match='A hidden form value is not independently authorized'):
        await forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)


def test_unlinked_or_changed_without_selection_hidden_values_gate():
    unlinked = page(control(hidden_value='EX', hidden_selector=''))
    unlinked['controls'].append({
        'selector': '#country-code', 'label': 'Unrelated country value',
        'question': '', 'tag': 'input', 'type': 'hidden', 'required': False,
        'disabled': False, 'value': 'EX', 'has_value': True, 'in_form': True,
        'form_selector': '#application', 'form_action': URL, 'formaction': '',
        'submit': False, 'checked': False, 'name': 'country_value', 'options': [],
    })
    with pytest.raises(forms.Gate, match='Unverified or changed combobox value'):
        forms.check_form(unlinked, {'#country': 'Example Country'}, True)

    changed_without_selection = page(control(hidden_value='EX'))
    with pytest.raises(forms.Gate,
                       match='A hidden form value is not independently authorized'):
        forms.check_form(changed_without_selection, {}, True)



async def test_closed_picker_loads_options_after_exact_approved_filter(monkeypatch):
    closed = control()
    closed['combobox'].update(linked=False, listbox_selector='', options=[])
    opened = control()
    committed = control(selected_text='Example Country', selected_value='EX',
                        hidden_value='EX')
    row = {'pk': PK, 'status': 'submitting',
           'human_approved_answers': {'What country?': grant()}}
    monkeypatch.setattr(forms, '_row', lambda _pk: row)
    session = DynamicSession(page(closed), page(opened), page(committed))
    filled = {}
    receipt = await forms.execute(
        session, page(closed), {'action': 'combobox', 'selector': '#country',
                                'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled=filled, resume_path='',
        company='example-co', jd_text='', resume_tex='', github='', pk=PK,
        job_url=URL)
    assert receipt['approved_value'] == 'Example Country'
    assert ('fill', '#country', '--value', 'Example Country') in session.calls
    assert filled == {'#country': 'Example Country'}

@pytest.mark.parametrize(('caption', 'value', 'hidden'), [
    ('Example Country', 'EX', 'WRONG'),
    ('Example Country', '', 'EX'),
    ('Typed only', 'EX', 'EX'),
])
def test_combobox_pre_submit_requires_persistent_matching_commit(caption, value, hidden):
    current = page(control(selected_text=caption, selected_value=value,
                           hidden_value=hidden))
    with pytest.raises(forms.Gate, match='hidden form value|combobox'):
        forms.check_form(current, {'#country': 'Example Country'}, True)


def test_orphan_or_ambiguous_combobox_link_gates_before_option_ipc(monkeypatch):
    target = control()
    target['combobox']['linked'] = False
    target['combobox']['link_id'] = ''
    current = page(target)
    session = Session(current)
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    with pytest.raises(forms.Gate):
        asyncio.run(forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='', pk=PK,
            job_url=URL))
    assert not any(call[0] == 'click' for call in session.calls)

@pytest.mark.parametrize('options', [
    [{'selector': '#only', 'unique': True, 'label': 'Other Country', 'value': 'OT'}],
    [OPTIONS[0], {**OPTIONS[0], 'selector': '#duplicate'}],
    [{**OPTIONS[0], 'submit': True}],
    [{**OPTIONS[0], 'navigates': True}],
])
async def test_unmatched_ambiguous_or_dangerous_option_never_clicks(monkeypatch, options):
    current = page()
    current['controls'][0]['combobox']['options'] = deepcopy(options)
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    session = Session(current)
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
    assert not any(call == ('click', '#country-example') for call in session.calls)

@pytest.mark.parametrize('field', ['label', 'value'])
def test_combobox_material_assent_options_require_human_review(field):
    current = page()
    target = current['controls'][0]
    target['combobox']['options'] = [
        {'selector': '#yes', 'unique': True,
         'label': 'I consent to arbitration' if field == 'label' else 'Other',
         'value': 'I consent to arbitration' if field == 'value' else 'YES'},
    ]
    witness = {'#country': {'visible': True, 'readonly': False,
                            'disabled': False, 'inert': False,
                            'combobox': target['combobox']}}
    with pytest.raises(forms.Gate, match='Legal or material assent'):
        forms._disclosure_question(current, target, witness, URL)


def test_optional_empty_combobox_and_hidden_control_keep_native_availability():
    combo = control(required=False, hidden_selector='')
    combo['combobox'].update(linked=False, link_id='optional-list',
                             listbox_selector='', options=[])
    hidden = {'selector': '#optional-hidden', 'label': 'Optional code',
              'question': '', 'tag': 'input', 'type': 'hidden',
              'required': False, 'disabled': False, 'value': '',
              'has_value': False, 'in_form': True,
              'form_selector': '#application', 'form_action': URL,
              'formaction': '', 'submit': False, 'checked': False,
              'name': 'optional_code', 'options': []}
    current = page(combo)
    current['controls'].append(hidden)
    forms.check_form(current, {}, uploaded=True)


def test_typed_visible_combobox_drift_gates_pre_submit():
    target = control(selected_text='Example Country', selected_value='EX',
                     hidden_value='EX')
    target['combobox']['displayed_text'] = 'Different Country'
    current = page(target)
    receipt = {'action': 'combobox', 'selector': '#country', 'label': 'Country',
               'question': 'What country?', 'url': URL, 'fact': 'What country?',
               'approved_value': 'Example Country', 'committed_caption': 'Example Country',
               'committed_value': 'EX', 'hidden_selector': '#country-code',
               'hidden_value_before': '', 'hidden_value': 'EX'}
    with pytest.raises(forms.Gate, match='combobox'):
        forms.check_form(current, {'#country': 'Example Country'},
                         uploaded=True, receipts=[receipt])
    with pytest.raises(forms.Gate, match='The committed accessible choice or form value changed\.'):
        forms.check_approvals(PK, [receipt], current)


def test_combobox_machine_value_rejected_even_with_valid_caption_grant(monkeypatch):
    current = page()
    current['controls'][0]['combobox']['options'] = [{
        'selector': '#country-example', 'unique': True,
        'label': 'Other Country', 'value': 'Example Country', 'selected': False}]
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant('Example Country')}})
    session = Session(current)
    with pytest.raises(forms.Gate):
        asyncio.run(forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL))
    assert not any(call == ('click', '#country-example') for call in session.calls)

async def test_async_option_timeout_never_clicks_an_option(monkeypatch):
    closed = control()
    closed['combobox'].update(linked=False, listbox_selector='', options=[])
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(forms.asyncio, 'sleep', no_wait)
    class TimeoutSession:
        def __init__(self):
            self.current = page(closed)
            self.calls = []
        async def call(self, *args, **kwargs):
            self.calls.append(args)
            if args == ('evaluate', forms._FIXED_CONTROL_WITNESS):
                return {'value': evidence(self.current)}
            if args in {('click', '#country'),
                        ('fill', '#country', '--value', 'Example Country')}:
                return {}
            pytest.fail(f'Unexpected browser IPC: {args}')
        async def page(self):
            return deepcopy(self.current)
    session = TimeoutSession()
    with pytest.raises(forms.Gate, match='did not expose current selectable options'):
        await forms.execute(
            session, page(closed), {'action': 'combobox', 'selector': '#country',
                                    'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={}, resume_path='',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
    assert not any(call[0] == 'click' and call[1] != '#country'
                   for call in session.calls)

async def test_closed_aria_picker_binds_exclusive_listbox_after_open(monkeypatch):
    """A closed ARIA combobox has no aria-controls until its options mount."""
    closed = control()
    closed['combobox'].update(linked=False, link_id='',
                              listbox_selector='', options=[])
    opened = page()
    committed = page(control(selected_text='Example Country',
                             selected_value='EX', hidden_value='EX'))
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})

    class LazyLinkSession(DynamicSession):
        def current(self):
            # The actual linked listbox first appears after the picker click.
            return (self.initial, self.opened, self.opened, self.committed)[self.stage]

    session = LazyLinkSession(page(closed), opened, committed)
    filled = {}
    receipt = await forms.execute(
        session, page(closed),
        {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL)
    mutations = [call for call in session.calls if call[0] != 'evaluate']
    assert mutations == [
        ('click', '#country'),
        ('fill', '#country', '--value', 'Example Country'),
        ('click', '#country-example'),
    ]
    assert receipt['committed_value'] == receipt['hidden_value'] == 'EX'
    assert filled == {'#country': 'Example Country'}
    forms.check_form(committed, filled, True, receipts=[receipt])
    forms.check_approvals(PK, [receipt], committed)


async def test_closed_aria_picker_without_rendered_link_never_selects(monkeypatch):
    """Opening alone cannot confer a listbox/option identity or approval."""
    closed = control()
    closed['combobox'].update(linked=False, link_id='',
                              listbox_selector='', options=[])
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(forms.asyncio, 'sleep', no_wait)

    class NeverLinked(DynamicSession):
        def current(self):
            return self.initial

    session = NeverLinked(page(closed), page(), page())
    with pytest.raises(forms.Gate, match='did not expose current selectable options'):
        await forms.execute(
            session, page(closed),
            {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={},
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=URL)
    assert not any(call == ('click', '#country-example') for call in session.calls)


async def test_dynamic_combobox_rechecks_provenance_before_each_mutation(monkeypatch):
    """Reversible ARIA writes must retain a tracked canonical ATS route."""
    job_id = '12345678-1234-4234-8234-123456789abc'
    pk = 'example-co#' + job_id
    posting = 'https://jobs.lever.co/example-co/' + job_id
    form_url = posting + '/apply'

    def on_form(snapshot):
        snapshot['url'] = form_url
        for item in snapshot['controls']:
            item['form_action'] = form_url
        return snapshot

    closed = control()
    closed['combobox'].update(linked=False, listbox_selector='', options=[])
    initial = on_form(page(closed))
    opened = on_form(page())
    committed = on_form(page(control(
        selected_text='Example Country', selected_value='EX', hidden_value='EX')))
    approved = grant()
    approved.update(url=form_url, form_action=form_url)
    row = {
        'pk': pk, 'company': 'example-co', 'job_id': job_id,
        'ats': 'lever', 'jd_url': posting, 'status': 'submitting',
        'possible_submission': False,
        'human_approved_answers': {'What country?': approved},
    }
    monkeypatch.setattr(forms, '_row', lambda row_pk: row if row_pk == pk else {})
    token = forms.ApplicationEntryProvenance(
        pk=pk, company='example-co', ats='lever', job_id=job_id,
        wrapper=posting, board='example-co', url=form_url)
    navigation = forms.navigation_allowed
    checked = []

    def route(destination, *origins, provenance=None):
        if provenance is not None:
            checked.append(provenance)
        return navigation(destination, *origins, provenance=provenance)

    monkeypatch.setattr(forms, 'navigation_allowed', route)
    session = DynamicSession(initial, opened, committed)
    receipt = await forms.execute(
        session, initial, {'action': 'combobox', 'selector': '#country',
                           'fact': 'What country?'},
        facts={'What country?': 'Example Country'}, filled={}, resume_path='',
        company='example-co', jd_text='', resume_tex='', github='', pk=pk,
        job_url=posting, provenance=token)
    assert receipt['action'] == 'combobox'
    assert checked and all(item is token for item in checked)
    assert ('click', '#country-example') in session.calls


async def test_apply_rechecks_dispatch_before_combobox_mutation(monkeypatch):
    from tools import browser_skill as bsk
    from unittest.mock import AsyncMock

    job_id = '12345678-1234-4234-8234-123456789abc'
    pk = 'example-co#' + job_id
    posting = 'https://jobs.lever.co/example-co/' + job_id
    form_url = posting + '/apply'

    def on_form(snapshot):
        snapshot['url'] = form_url
        for item in snapshot['controls']:
            item['form_action'] = form_url
        return snapshot

    calls = []
    monkeypatch.setattr(forms, 'check_dispatch',
                        lambda *args, **kwargs: calls.append('dispatch'))
    approved = grant()
    approved.update(url=form_url, form_action=form_url)
    row = {'pk': pk, 'company': 'example-co', 'job_id': job_id,
           'jd_url': posting, 'ats': 'lever', 'status': 'submitting',
           'possible_submission': False,
           'human_approved_answers': {'What country?': approved}}
    monkeypatch.setattr(forms, '_row',
                        lambda selected_pk: row if selected_pk == pk else {})
    # The missing optional tenant pin must not block safe host preparation.
    # No actual watchlist or owner configuration is read by this offline test.
    monkeypatch.setattr(forms, '_configured_alias', lambda *args, **kwargs: None)
    monkeypatch.setattr('tools.claude_chrome._stage_resume', lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')
    monkeypatch.setattr('core.events.emit', lambda *args, **kwargs: None)

    initial = on_form(page())
    committed = on_form(page(control(
        selected_text='Example Country', selected_value='EX', hidden_value='EX')))

    class ApplySession(Session):
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def navigate(self, _url):
            return deepcopy(self.current)

    session = ApplySession(initial, committed)
    monkeypatch.setattr(bsk, 'Session', lambda _kind: session)
    monkeypatch.setattr(bsk, 'decision', AsyncMock(side_effect=[
        {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
        {'action': 'gate', 'question': 'Stop after the synthetic committed choice.'}]))
    outcome = await forms.apply(
        posting, 'example-co', {'What country?': 'Example Country'}, 'synthetic',
        pk=pk, resume_path='Resume.pdf')
    assert outcome['status'] == 'gate'
    assert ('click', '#country-example') in session.calls
    assert calls.count('dispatch') >= 4


async def test_option_without_positive_safety_witness_never_clicks(monkeypatch):
    """An omitted safety field is not a safe ARIA option, even in a fake DOM."""
    current = page()
    current['controls'][0]['combobox']['options'][0].pop('safe')
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    session = Session(current)
    with pytest.raises(forms.Gate, match='unsafe navigating option'):
        await forms.execute(
            session, current,
            {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={},
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=URL)
    assert ('click', '#country-example') not in session.calls


async def test_unsafe_nested_option_effect_never_clicks(monkeypatch):
    """A nested action is unsafe even without a submit/link on the option node."""
    current = page()
    chosen = current['controls'][0]['combobox']['options'][0]
    chosen.update(safe=False, submit=False, navigates=False)
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant()}})
    session = Session(current)
    with pytest.raises(forms.Gate, match='unsafe navigating option'):
        await forms.execute(
            session, current,
            {'action': 'combobox', 'selector': '#country', 'fact': 'What country?'},
            facts={'What country?': 'Example Country'}, filled={},
            resume_path='', company='example-co', jd_text='', resume_tex='',
            github='', pk=PK, job_url=URL)
    assert ('click', '#country-example') not in session.calls
