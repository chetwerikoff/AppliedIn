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
     'value': 'EX', 'selected': False},
    {'selector': '#country-other', 'unique': True, 'label': 'Other Country',
     'value': 'OT', 'selected': False},
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
    question = forms._disclosure_question(
        current, target, {'#country': {'visible': True, 'readonly': False,
                                        'disabled': False, 'inert': False,
                                        'combobox': target['combobox']}}, URL)
    question['pk'] = PK
    row = {'pk': PK, 'status': 'needs_human', 'jd_url': URL,
           'company': 'example-co', 'gate_source': 'applier',
           'gate_call_id': 'direct',
           'gate_pending': {'question': 'Answer the observed field.',
                            'form_question': question}}

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
    with pytest.raises(forms.Gate, match='committed combobox value changed'):
        forms.check_approvals(PK, [receipt], changed_machine_value)


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
    with pytest.raises(forms.Gate, match='committed combobox value changed'):
        forms.check_approvals(PK, [receipt], current)


def test_combobox_machine_value_rejected_even_with_valid_caption_grant(monkeypatch):
    current = page()
    monkeypatch.setattr(forms, '_row', lambda _pk: {
        'pk': PK, 'status': 'submitting',
        'human_approved_answers': {'What country?': grant('Example Country')}})
    session = Session(current)
    with pytest.raises(forms.Gate):
        asyncio.run(forms.execute(
            session, current, {'action': 'combobox', 'selector': '#country',
                               'fact': 'What country?'},
            facts={'What country?': 'EX'}, filled={}, resume_path='',
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
