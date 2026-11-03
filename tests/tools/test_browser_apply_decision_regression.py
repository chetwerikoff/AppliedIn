"""Issue #311: synthetic, offline fact binding and model decision evidence.

No browser, network, owner records, Redis, application IPC or real Submit is used.
The full-loop test uses the already sealed local fixture's read-only stub seam.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture


URL = 'https://example.test/application'
PK = 'example-co#synthetic'
OPTIONS = [
    {'selector': '#name-wrong-option', 'label': 'Wrong', 'value': 'wrong',
     'selected': False},
    {'selector': '#name-right-option', 'label': 'Test User', 'value': 'user',
     'selected': False},
]


def field(selector, *, tag='input', kind='text', label='Name', question='Name',
          value='', **extra):
    return {
        'selector': selector, 'tag': tag, 'type': kind, 'label': label,
        'question': question, 'value': value, 'has_value': bool(value),
        'checked': False, 'required': True, 'disabled': False,
        'in_form': True, 'form_selector': '#application',
        'form_action': URL, 'name': selector.lstrip('#'),
        **extra,
    }


def page(*controls):
    return {
        'url': URL, 'text': 'Synthetic application form', 'controls': list(controls),
        'inventory_verified': True, 'truncated': False, 'opaque_controls': False,
        'shadow_roots': False, 'unsupported_frames': [],
    }


def name_select(options=None):
    options = OPTIONS if options is None else options
    return field('#name-select', tag='select', kind='select',
                 options=deepcopy(options), selected_options=[])


def name_combo():
    return field('#name-combo', kind='combobox', options=[], combobox={
        'linked': True, 'link_id': 'name-list', 'listbox_selector': '#name-list',
        'options': deepcopy(OPTIONS), 'selected_text': '', 'displayed_text': '',
        'selected_value': '', 'hidden_selector': '#name-code', 'hidden_value': '',
    })


def eligibility():
    return (
        field('#yes', kind='radio', label='Yes', question='Eligibility',
              name='eligibility', value='yes'),
        field('#no', kind='radio', label='No', question='Eligibility',
              name='eligibility', value='no'),
    )


class MemorySession:
    def __init__(self, form):
        self.current, self.calls = deepcopy(form), []

    async def page(self):
        return deepcopy(self.current)

    async def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        verb, selector = args[:2]
        controls = self.current['controls']
        if verb == 'fill':
            target = next(c for c in controls if c['selector'] == selector)
            target['value'] = args[-1]
            target['has_value'] = bool(args[-1])
        elif verb == 'select':
            target = next(c for c in controls if c['selector'] == selector)
            target['value'] = args[-1]
            target['selected_options'] = [
                {'label': o['label'], 'value': o['value']}
                for o in target['options'] if o['value'] == args[-1]]
        elif verb == 'click':
            if selector in {'#yes', '#no'}:
                for c in controls:
                    if c['selector'] in {'#yes', '#no'}:
                        c['checked'] = c['selector'] == selector
            elif selector.endswith('-option'):
                target = next(c for c in controls if c['selector'] == '#name-combo')
                options = target['combobox']['options']
                chosen = next(o for o in options if o['selector'] == selector)
                for option in options:
                    option['selected'] = option is chosen
                target['combobox']['selected_text'] = chosen['label']
                target['combobox']['displayed_text'] = chosen['label']
                target['combobox']['selected_value'] = chosen['value']
                target['combobox']['hidden_value'] = chosen['value']
            else:
                target = next((c for c in controls if c['selector'] == selector), None)
                if target is not None and target.get('type') == 'checkbox':
                    target['checked'] = True
        return {}


async def act(session, form, action, facts=None, *, provenance=None):
    filled, evidence = {}, {}
    result = await forms.execute(
        session, form, action, facts=facts or {}, filled=filled,
        resume_path='', company='example-co', jd_text='', resume_tex='',
        github='', pk=PK, job_url=URL, provenance=provenance,
        write_evidence=evidence,
    )
    # Public execute() receipts must never include private write/option data.
    assert not {'_write_performed', '_selected_option'} & result.keys()
    return result, filled, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize('fact_key', ['Full Name', 'name', 'Name *', 'Full Name *'])
@pytest.mark.parametrize('kind', ['fill', 'combobox'])
async def test_exact_and_normalized_facts_override_model_value(kind, fact_key):
    target = field('#name') if kind == 'fill' else name_combo()
    form = page(target)
    session = MemorySession(form)
    action = {'action': kind, 'selector': target['selector'], 'fact': fact_key,
              'value': 'Wrong'}
    result, filled, evidence = await act(
        session, form, action, {'Name': 'Test User', 'Full Name': 'Test User'})
    assert result['action'] == kind
    assert filled[target['selector']] == 'Test User'
    if kind == 'fill':
        assert [a for a, _ in session.calls] == [
            ('fill', '#name', '--value', 'Test User')]
    else:
        assert [a for a, _ in session.calls] == [
            ('click', '#name-right-option')]


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['Missing', [], {}, None, '', '   '])
@pytest.mark.parametrize('kind,selector', [
    ('fill', '#name'), ('select', '#name-select'),
    ('choose', '#yes'), ('combobox', '#name-combo'),
])
async def test_present_unresolved_fact_never_falls_back_to_a_model_guess(
        invalid, kind, selector):
    form = page(field('#name'), name_select(), *eligibility(), name_combo())
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {
            'action': kind, 'selector': selector, 'fact': invalid,
            'value': 'Wrong'}, {'Name': 'Test User', 'Eligibility': 'No'})
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == selector
    assert not session.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,selector', [
    ('fill', '#name'), ('select', '#name-select'),
    ('choose', '#yes'), ('combobox', '#name-combo'),
])
async def test_normalization_collision_asks_owner_without_a_write(kind, selector):
    form = page(field('#name'), name_select(), *eligibility(), name_combo())
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': kind, 'selector': selector,
                                  'fact': 'name', 'value': 'Wrong'},
                  {'Name': 'Test User', 'Name *': 'Conflict'})
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == selector
    assert not session.calls


@pytest.mark.asyncio
async def test_native_select_uses_only_the_single_owner_matching_option():
    form = page(name_select())
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'select', 'selector': '#name-select',
                        'fact': 'Name *', 'value': 'Wrong'},
        {'Name': 'Test User'})
    assert evidence['performed'] is True
    assert filled == {'#name-select': 'user'}
    assert [args for args, _ in session.calls] == [
        ('select', '#name-select', '--value', 'user')]

    for options in (
        [OPTIONS[0]],
        [OPTIONS[0], OPTIONS[1], dict(OPTIONS[1], selector='#same-answer-twin')],
    ):
        form = page(name_select(options))
        session = MemorySession(form)
        result, filled, evidence = await act(
            session, form, {'action': 'select', 'selector': '#name-select',
                            'fact': 'Name *', 'value': 'Wrong'},
            {'Name': 'Test User'})
        assert not session.calls and evidence['performed'] is False
        assert filled == {'#name-select': 'Test User'}

    session = MemorySession(page(name_select()))
    result, filled, evidence = await act(
        session, session.current,
        {'action': 'select', 'selector': '#name-select', 'value': 'Wrong'})
    assert filled == {'#name-select': 'wrong'}
    assert [args[-1] for args, _ in session.calls] == ['wrong']


@pytest.mark.asyncio
async def test_owner_choice_cannot_click_a_different_model_selected_member():
    form = page(*eligibility())
    for action in (
        {'action': 'choose', 'selector': '#yes', 'fact': 'Eligibility *'},
        {'action': 'choose', 'selector': '#no', 'fact': 'Eligibility *',
         'value': 'Yes'},
    ):
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form, action, {'Eligibility': 'No'})
        assert caught.value.reason == 'owner_question'
        assert caught.value.form_question['selector'] == action['selector']
        assert not session.calls

    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#no',
                        'fact': 'Eligibility *'},
        {'Eligibility': 'No'})
    assert evidence['performed'] and filled == {'#no': 'No'}
    assert [args for args, _ in session.calls] == [('click', '#no')]

    session = MemorySession(form)
    result, _, evidence = await act(session, form,
                          {'action': 'choose', 'selector': '#yes', 'value': 'Yes'})
    assert evidence['performed']
    assert [args for args, _ in session.calls] == [('click', '#yes')]

    for altered in (page(field('#yes', kind='radio', label='Yes',
                               question='Eligibility', name='eligibility', value='yes')),
                    page(*eligibility(), field('#no-copy', kind='radio', label='No',
                                               question='Eligibility',
                                               name='eligibility', value='no'))):
        session = MemorySession(altered)
        with pytest.raises(forms.Gate) as caught:
            await act(session, altered,
                      {'action': 'choose', 'selector': '#no'
                       if len(altered['controls']) > 1 else '#yes',
                       'fact': 'Eligibility'}, {'Eligibility': 'No'})
        assert caught.value.reason == 'owner_question' and not session.calls


@pytest.mark.asyncio
async def test_single_labelled_checkbox_needs_explicit_matching_owner_agreement():
    """An observed single checkbox can use the owner's affirmative, not a guess."""
    question = 'I consent to sharing personal data with marketing partners'
    form = page(field('#marketing', kind='checkbox', label=question,
                      question='', value='yes'))
    action = {'action': 'choose', 'selector': '#marketing', 'fact': question}
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, action, {question: 'I agree'})
    assert result == {'action': 'choose', 'selector': '#marketing',
                      'label': question}
    assert filled == {'#marketing': 'I agree'} and evidence['performed'] is True
    assert [args for args, _ in session.calls] == [('click', '#marketing')]
    intent = forms._write_intent(form, form['controls'][0], result,
                                 filled, evidence)
    assert intent is not None
    assert forms._completed_write_receipts(await session.page(), [intent]) == [
        {'action': 'choose', 'verified': True}]
    # A lone Yes/agree checkbox cannot disclose the selected choice in context.
    assert forms._approved_choice_metadata(form, {question: 'I agree'},
                                           [question]) == []

    for facts, attempted in (
        ({}, action),
        ({question: 'I decline'}, action),
        ({'Unrelated': 'I agree'}, {**action, 'fact': 'Unrelated'}),
        ({question: 'I agree'}, {**action, 'value': 'No'}),
    ):
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form, attempted, facts)
        assert caught.value.reason == 'owner_question'
        assert caught.value.form_question['selector'] == '#marketing'
        assert session.calls == []

    ambiguous = page(
        field('#marketing', kind='checkbox', label=question,
              question='', value='yes'),
        field('#marketing-copy', kind='checkbox', label=question,
              question='', value='yes'))
    session = MemorySession(ambiguous)
    with pytest.raises(forms.Gate) as caught:
        await act(session, ambiguous, action, {question: 'I agree'})
    assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
async def test_omitted_fact_combobox_remains_a_value_only_accessible_action():
    form = page(name_combo())
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'combobox', 'selector': '#name-combo',
                        'value': 'Test User'})
    assert evidence['performed'] is True
    assert filled == {'#name-combo': 'Test User'}
    assert [args for args, _ in session.calls] == [('click', '#name-right-option')]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,selector,action', [
    ('fill', '#name', {'action': 'fill', 'selector': '#name', 'fact': 'Name'}),
    ('select', '#name-select', {'action': 'select', 'selector': '#name-select',
                                'fact': 'Name'}),
    ('choose', '#no', {'action': 'choose', 'selector': '#no', 'fact': 'Eligibility'}),
    ('combobox', '#name-combo', {'action': 'combobox', 'selector': '#name-combo',
                                 'fact': 'Name'}),
])
async def test_completed_receipt_requires_effect_and_current_private_readback(
        kind, selector, action):
    form = page(field('#name'), name_select(), *eligibility(), name_combo())
    session = MemorySession(form)
    original = deepcopy(next(c for c in form['controls'] if c['selector'] == selector))
    result, filled, evidence = await act(
        session, form, action, {'Name': 'Test User', 'Eligibility': 'No'})
    intent = forms._write_intent(form, original, result, filled, evidence)
    assert intent is not None and evidence['performed']
    receipts = forms._completed_write_receipts(await session.page(), [intent])
    assert len(receipts) == 1
    assert set(receipts[0]) <= {'action', 'selector', 'verified'}
    assert receipts[0]['action'] == kind and receipts[0]['verified'] is True
    if kind == 'choose':
        assert 'selector' not in receipts[0]
    else:
        assert receipts[0]['selector'] == selector

    current = await session.page()
    if kind == 'fill':
        next(c for c in current['controls'] if c['selector'] == selector)['value'] = ''
    elif kind == 'select':
        next(c for c in current['controls'] if c['selector'] == selector)['selected_options'] = []
    elif kind == 'choose':
        next(c for c in current['controls'] if c['selector'] == '#yes')['checked'] = True
    else:
        next(c for c in current['controls'] if c['selector'] == selector)['combobox']['selected_text'] = ''
    assert forms._completed_write_receipts(current, [intent]) == []

    current = await session.page()
    next(c for c in current['controls'] if c['selector'] == selector)['question'] = 'Changed group'
    assert forms._completed_write_receipts(current, [intent]) == []
    current = await session.page()
    current['controls'].append(deepcopy(next(
        c for c in current['controls'] if c['selector'] == selector)))
    assert forms._completed_write_receipts(current, [intent]) == []


def test_attempt_history_never_proves_a_field_write():
    form = page(field('#name', value='Test User'), *eligibility())
    for kind in ('focus', 'press', 'click', 'navigate'):
        assert forms._write_intent(
            form, form['controls'][0], {'action': kind, 'selector': '#name'},
            {'#name': 'Test User'}) is None
    assert forms._completed_write_receipts(form, []) == []


def test_attachment_receipt_needs_producer_completion_and_unique_current_file():
    receiver = field('#resume', kind='file', label='Resume',
                     question='Resume', files=['Synthetic-Resume.pdf'])
    form = page(receiver)
    assert forms._attachment_completed_now(
        form, receiver, True, None, '/tmp/Synthetic-Resume.pdf') is True
    assert not forms._attachment_completed_now(
        form, receiver, False, None, '/tmp/Synthetic-Resume.pdf')
    for mutation in (
        lambda f: f['controls'][0].update(files=[]),
        lambda f: f['controls'][0].update(files=['Replaced.pdf']),
        lambda f: f['controls'].clear(),
        lambda f: f['controls'].append(deepcopy(receiver)),
    ):
        changed = deepcopy(form)
        mutation(changed)
        assert not forms._attachment_completed_now(
            changed, receiver, True, None, '/tmp/Synthetic-Resume.pdf')


def test_choice_metadata_is_owner_only_non_disclosing_and_unambiguous():
    form = page(*eligibility())
    assert forms._approved_choice_metadata(
        form, {'Eligibility': 'No'}, ['Eligibility']) == [
            {'fact_key': 'Eligibility', 'answer_present': True}]
    for facts in ({}, {'Eligibility': 'Unknown'}):
        assert forms._approved_choice_metadata(form, facts, ['Eligibility']) == []
    assert forms._approved_choice_metadata(form, {}, []) == []  # PAGE-only

    ambiguous = page(*eligibility(), field('#maybe', kind='radio',
                       label='No', question='Eligibility',
                       name='eligibility', value='no'))
    assert forms._approved_choice_metadata(
        ambiguous, {'Eligibility': 'No'}, ['Eligibility']) == []
    duplicated = page(*eligibility(), *(
        field('#other-'+c['selector'][1:], kind='radio', label=c['label'],
              question='Eligibility', name='eligibility',
              form_selector='#another-form', value=c['value']) for c in eligibility()))
    assert forms._approved_choice_metadata(
        duplicated, {'Eligibility': 'No'}, ['Eligibility']) == []
    sensitive = page(field('#male', kind='radio', label='Male', question='Gender',
                           name='gender', value='male'),
                     field('#decline', kind='radio', label='Prefer not to answer',
                           question='Gender', name='gender', value='decline'))
    assert forms._approved_choice_metadata(
        sensitive, {'Gender': 'Prefer not to answer'}, ['Gender']) == []


def test_projection_never_reveals_selected_option_or_private_nested_values():
    form = page(name_combo(), name_select(), *eligibility())
    form['controls'][0]['combobox']['options'][1]['selected'] = True
    form['controls'][0]['combobox']['selected_value'] = 'private-machine'
    form['controls'][0]['combobox']['selected_text'] = 'private-caption'
    form['controls'][1]['selected_options'] = [
        {'value': 'private-machine', 'label': 'private-caption'}]
    form['controls'][2]['checked'] = True
    form['approved_choices'] = [{'approved_answer': {
        'selector': '#private-option', 'value': 'private-machine'}}]
    visible, history = forms._model_projection(form, [
        {'action': 'choose', 'selector': '#private-option',
         'value': 'private-machine', 'fact': 'Eligibility'}])
    visible['approved_choices'] = forms._approved_choice_metadata(
        form, {'Eligibility': 'No'}, ['Eligibility'])
    visible['completed_writes'] = []
    visible['attachment_completed'] = False
    rendered = repr((visible, history))
    for secret in ('private-machine', 'private-caption', '#private-option'):
        assert secret not in rendered
    assert 'selected_options' not in rendered
    assert 'checked' not in rendered
    assert history == [{'action': 'choose'}]


@pytest.mark.asyncio
async def test_scalar_diagnostic_for_every_started_model_decision(caplog):
    responses = [
        {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
        {'action': 'fill', 'selector': '#name', 'fact': 'name *'},
        {'action': 'fill', 'selector': '#name', 'fact': 'Missing'},
        {'action': 'choose', 'selector': '#no', 'fact': []},
        {'action': 'gate'}, {'action': 'finish'}, {'action': 'wait'},
        {'action': 'unexpected', 'selector': ''}, ['malformed'],
    ]

    async def decision(_task, _page, _history, *, model):
        assert model == 'synthetic'
        return responses.pop(0)

    with caplog.at_level('INFO', logger=forms._LOG.name):
        for turn in range(1, 10):
            await forms._application_decision_with_log(
                decision, 'SYNTHETIC-SECRET-TASK', {}, [], model='synthetic',
                facts={'Name': 'Test User'}, turn=turn)

        async def thrown(*_args, **_kwargs):
            raise ValueError('SYNTHETIC-SECRET-CANARY')
        with pytest.raises(ValueError, match='SYNTHETIC-SECRET-CANARY'):
            await forms._application_decision_with_log(
                thrown, '', {}, [], model='synthetic', facts={'Name': 'Test User'},
                turn=10)
    records = [r for r in caplog.records
               if r.msg == 'application decision %s']
    # logging.LogRecord preserves a single mapping argument as a mapping,
    # rather than a positional tuple; assert its exact scalar-only shape.
    assert all(isinstance(r.args, dict) for r in records)
    logs = [r.args for r in records]
    assert len(logs) == 10 and [r['turn'] for r in logs] == list(range(1, 11))
    assert all(set(r) == {'turn', 'verb', 'selector_present',
                           'fact_key_present_in_facts'} for r in logs)
    assert [r['fact_key_present_in_facts'] for r in logs] == [
        True, False, False, False, False, False, False, False, False, False]
    assert [r['verb'] for r in logs] == [
        'fill', 'fill', 'fill', 'choose', 'gate', 'finish', 'wait',
        'unknown', 'unknown', 'error']
    assert [r['selector_present'] for r in logs] == [
        True, True, True, True, False, False, False, False, False, False]
    all_lines = '\n'.join(r.getMessage() for r in caplog.records
                          if r.msg == 'application decision %s')
    for secret in ('SYNTHETIC-SECRET', '#name', '#no', 'Test User',
                   'Missing', 'name *', 'private-machine'):
        assert secret not in all_lines


@pytest.mark.asyncio
async def test_two_actual_apply_decisions_receive_only_verified_advisory_progress(
        tmp_path, monkeypatch, caplog):
    """Closed daemon fixture, one synthetic fill and a human handoff; no Submit."""
    document = tmp_path / 'Synthetic-Resume.pdf'
    document.write_bytes(b'%PDF-1.4\nsynthetic only\n')
    authority = fixture._SyntheticRun(str(document))
    monkeypatch.setattr(fixture, '_daemon_origin', lambda: True)
    token = fixture._SESSION_ENTRY.set(authority)
    current = page(
        field('#name'), field('#country-a', kind='radio', label='Synthetic Country',
                              question='Country', value='country',
                              name='country'),
        field('#country-b', kind='radio', label='Other Country',
              question='Country', value='other', name='country'))
    current['url'] = fixture.URL
    for c in current['controls']:
        c['form_action'] = fixture.URL
    views = []

    async def scripted(self, task, model_page, history, *, model):
        assert self is authority and model == 'synthetic-fixture'
        views.append((task, deepcopy(model_page), deepcopy(history)))
        if len(views) == 1:
            return {'action': 'fill', 'selector': '#name', 'fact': 'Name'}
        return {'action': 'gate'}

    class RawSession:
        def __init__(self, kind):
            assert kind == 'apply'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def navigate(self, url):
            assert url == fixture.URL
            return deepcopy(current)

        async def page(self):
            return deepcopy(current)

        async def call(self, *args, **kwargs):
            assert args == ('fill', '#name', '--value', 'Test User') and not kwargs
            current['controls'][0]['value'] = 'Test User'
            current['controls'][0]['has_value'] = True
            return {}

    monkeypatch.setattr(fixture._SyntheticRun, 'decision', scripted)
    monkeypatch.setattr(bsk, 'Session', RawSession)
    try:
        with caplog.at_level('INFO', logger=forms._LOG.name):
            result = await forms.apply(
                fixture.URL, fixture.COMPANY, dict(fixture._FACTS),
                'synthetic-fixture', pk=fixture.PK, resume_path=authority.resume_path,
                fixture_context=authority)
    finally:
        fixture._SESSION_ENTRY.reset(token)
    assert result['status'] == 'gate' and len(views) == 2
    first, second = views[0][1], views[1][1]
    assert first['completed_writes'] == []
    assert second['completed_writes'] == [
        {'action': 'fill', 'selector': '#name', 'verified': True}]
    assert second['attachment_completed'] is False
    assert {'fact_key': 'Country', 'answer_present': True} in second['approved_choices']
    assert 'Do not replay completed writes' in views[1][0]
    assert 'Available fact keys:' in views[1][0]
    assert 'Test User' not in repr(second)
    assert 'Test User' not in views[1][0]
    assert all('value' not in receipt for receipt in second['completed_writes'])
    assert len([r for r in caplog.records if r.msg == 'application decision %s']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('unrelated', ['Relocation', 'Availability'])
async def test_choice_fact_must_name_its_current_question_not_another_valid_fact(
        unrelated):
    """Two real keys do not let a model rebind Eligibility to another question."""
    form = page(*eligibility())
    facts = {'Eligibility': 'No', unrelated: 'Yes'}
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'selector': '#yes',
                                  'fact': unrelated, 'value': 'Yes'}, facts)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == '#yes'
    assert session.calls == []

    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#no',
                        'fact': 'Eligibility *'}, facts)
    assert result['action'] == 'choose' and evidence['performed'] is True
    assert filled == {'#no': 'No'}
    assert [args for args, _ in session.calls] == [('click', '#no')]


@pytest.mark.asyncio
async def test_choice_receipt_requires_unchanged_machine_values_and_option_identity():
    """A checked caption is not proof of an unchanged submitted radio value."""
    form = page(*eligibility())
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#no',
                        'fact': 'Eligibility'}, {'Eligibility': 'No'})
    original = next(c for c in form['controls'] if c['selector'] == '#no')
    intent = forms._write_intent(form, original, result, filled, evidence)
    assert forms._completed_write_receipts(await session.page(), [intent]) == [
        {'action': 'choose', 'verified': True}]
    for selector, change in (
        ('#no', {'value': 'yes'}),
        ('#yes', {'value': 'changed-machine'}),
        ('#no', {'id': 'changed-option-id'}),
        ('#yes', {'field_entry': 'another-group'}),
    ):
        changed = await session.page()
        member = next(c for c in changed['controls'] if c['selector'] == selector)
        member.update(change)
        assert member['label'] in {'Yes', 'No'}
        assert next(c for c in changed['controls']
                    if c['selector'] == '#no')['checked'] is True
        assert forms._completed_write_receipts(changed, [intent]) == []


def synthetic_ashby_formless(*receivers):
    """Synthetic entry.root_evidence with the existing Ashby root/submit proof."""
    job = '00000000-0000-0000-0000-000000000001'
    posting = f'https://jobs.ashbyhq.com/example-co/{job}'
    proof = forms.ApplicationEntryProvenance(
        PK, 'example-co', 'ashby', job, posting, 'example-co',
        posting + '/application')
    submit = field('#submit', tag='button', kind='button',
                   label='Submit application', question='', submit=True)
    controls = deepcopy([*receivers, submit])
    for c in controls:
        c.update(in_form=False, form_selector='', form_action='')
    current = page(*controls)
    current['url'] = proof.url
    current['entry'] = {
        'root': None,
        'formless': {'root': '#synthetic-root', 'submit': '#submit', 'pane': '#pane',
                     'error': '', 'truncated': False, 'excluded': []},
        'root_evidence': [
            dict(c, inside=True, in_container=True, associated=True, visible=True)
            for c in controls],
    }
    return current, proof


@pytest.mark.asyncio
async def test_validated_ashby_formless_choice_and_progress_receipts():
    """No native form is needed when the current Ashby root proves membership."""
    form, proof = synthetic_ashby_formless(
        field('#name'), *eligibility(),
        field('#resume', kind='file', label='Resume', question='Resume',
              files=['Synthetic-Resume.pdf']))
    assert forms._formless_boundary(form, proof)
    session = MemorySession(form)

    name = next(c for c in form['controls'] if c['selector'] == '#name')
    result, filled, evidence = await act(
        session, form, {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
        {'Name': 'Test User'}, provenance=proof)
    fill_intent = forms._write_intent(
        form, name, result, filled, evidence, provenance=proof)
    assert fill_intent is not None

    before_choice = await session.page()
    no = next(c for c in before_choice['controls'] if c['selector'] == '#no')
    result, filled, evidence = await act(
        session, before_choice,
        {'action': 'choose', 'selector': '#no', 'fact': 'Eligibility *'},
        {'Eligibility': 'No', 'Relocation': 'Yes'}, provenance=proof)
    choice_intent = forms._write_intent(
        before_choice, no, result, filled, evidence, provenance=proof)
    assert choice_intent is not None
    assert [args for args, _ in session.calls] == [
        ('fill', '#name', '--value', 'Test User'), ('click', '#no')]
    current = await session.page()
    assert forms._completed_write_receipts(
        current, [fill_intent, choice_intent], provenance=proof) == [
        {'action': 'fill', 'selector': '#name', 'verified': True},
        {'action': 'choose', 'verified': True},
    ]
    assert forms._approved_choice_metadata(
        current, {'Eligibility': 'No'}, ['Eligibility'], provenance=proof) == [
        {'fact_key': 'Eligibility', 'answer_present': True}]
    resume = next(c for c in form['controls'] if c['selector'] == '#resume')
    assert forms._attachment_completed_now(
        current, resume, True, None, '/tmp/Synthetic-Resume.pdf',
        provenance=proof) is True

    # A changed root cannot inherit old receipts, even if selectors and
    # all private readbacks happen to remain equal.
    changed = deepcopy(current)
    changed['entry']['formless']['root'] = '#different-root'
    assert forms._completed_write_receipts(
        changed, [fill_intent, choice_intent], provenance=proof) == []
    # The root census may still be valid but a file no longer present is
    # never an attachment completion.
    current['controls'][-2]['files'] = []
    assert not forms._attachment_completed_now(
        current, resume, True, None, '/tmp/Synthetic-Resume.pdf',
        provenance=proof)


@pytest.mark.asyncio
async def test_unproved_formless_membership_asks_owner_and_omits_receipts():
    form, proof = synthetic_ashby_formless(
        field('#name'), *eligibility(),
        field('#resume', kind='file', label='Resume', question='Resume',
              files=['Synthetic-Resume.pdf']))
    session = MemorySession(form)
    unproved = deepcopy(form)
    unproved['entry']['root_evidence'] = [
        e for e in unproved['entry']['root_evidence']
        if e['selector'] != '#no']
    with pytest.raises(forms.Gate) as caught:
        await act(session, unproved, {
            'action': 'choose', 'selector': '#no', 'fact': 'Eligibility'},
            {'Eligibility': 'No'}, provenance=proof)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == '#no'
    assert session.calls == []

    result, filled, evidence = await act(
        session, form, {'action': 'fill', 'selector': '#name', 'fact': 'Name'},
        {'Name': 'Test User'}, provenance=proof)
    name = next(c for c in form['controls'] if c['selector'] == '#name')
    intent = forms._write_intent(
        form, name, result, filled, evidence, provenance=proof)
    assert intent is not None
    unreadable = await session.page()
    unreadable['entry']['formless']['error'] = 'ambiguous application roots'
    assert forms._completed_write_receipts(
        unreadable, [intent], provenance=proof) == []
    resume = next(c for c in form['controls'] if c['selector'] == '#resume')
    assert not forms._attachment_completed_now(
        unreadable, resume, True, None, '/tmp/Synthetic-Resume.pdf',
        provenance=proof)
    assert forms._approved_choice_metadata(
        unreadable, {'Eligibility': 'No'}, ['Eligibility'],
        provenance=proof) == []
    unproved_before = deepcopy(form)
    unproved_before['entry']['root_evidence'] = []
    assert forms._write_intent(
        unproved_before, name, result, filled, evidence,
        provenance=proof) is None
