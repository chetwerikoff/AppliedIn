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
        'options': [dict(option, safe=True) for option in OPTIONS],
        'selected_text': '', 'displayed_text': '',
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
            target = next((c for c in controls if c['selector'] == selector), None)
            if target is not None and target.get('type') == 'radio':
                # Mirror a native radio owner/name even for synthetic #o0/#o1.
                for c in controls:
                    if (c.get('type') == 'radio'
                            and c.get('name') == target.get('name')
                            and c.get('form_selector') == target.get('form_selector')
                            and c.get('in_form') == target.get('in_form')):
                        c['checked'] = c is target
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
            elif target is not None and target.get('type') == 'checkbox':
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
async def test_owner_choice_clicks_stored_member_not_model_target_or_value(monkeypatch):
    form = page(*eligibility())
    guarded = []
    original_guard = forms.guarded_value

    def guarded_member(member, value):
        guarded.append((member['selector'], value))
        return original_guard(member, value)

    monkeypatch.setattr(forms, 'guarded_value', guarded_member)
    for action in (
        {'action': 'choose', 'selector': '#yes', 'fact': 'Eligibility *',
         'value': 'Yes'},
        {'action': 'choose', 'selector': '#no', 'fact': 'Eligibility *',
         'value': 'Yes'},
    ):
        session = MemorySession(form)
        result, filled, evidence = await act(
            session, form, action, {'Eligibility': 'No'})
        assert result == {'action': 'choose', 'selector': '#no', 'label': 'No'}
        assert filled == {'#no': 'No'} and evidence['performed'] is True
        assert [args for args, _ in session.calls] == [('click', '#no')]
        assert guarded[-1] == ('#no', 'No')
        assert forms._fields_report([result], filled) == [
            {'label': 'No', 'value': 'No'}]
        original = next(c for c in form['controls'] if c['selector'] == '#no')
        intent = forms._write_intent(form, original, result, filled, evidence)
        assert intent is not None and intent['original']['selector'] == '#no'
        assert forms._completed_write_receipts(await session.page(), [intent]) == [
            {'action': 'choose', 'verified': True}]
        assert forms._write_intent(form, form['controls'][0], result,
                                   filled, evidence) is None
        assert forms._model_projection(form, [result])[1] == [{'action': 'choose'}]

    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#no',
                        'fact': 'Eligibility *'},
        {'Eligibility': 'No'})
    assert evidence['performed'] and filled == {'#no': 'No'}
    assert [args for args, _ in session.calls] == [('click', '#no')]

    # A value-only choose still follows the model's requested member.
    session = MemorySession(form)
    result, _, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#yes', 'value': 'Yes'})
    assert result['selector'] == '#yes' and evidence['performed']
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
    base = {'turn', 'verb', 'selector_present', 'fact_key_present_in_facts'}
    assert all(set(r) == base | ({'question_present', 'question_length', 'question_class'}
                                 if r['verb'] in {'gate', 'finish'} else set())
               for r in logs)
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
    assert views[1][0].endswith(NO_REPLAY_TAIL)
    assert APPLY_KEY_LITERAL + repr(forms._safe_fact_keys(
        dict(fixture._FACTS), current)) + NO_REPLAY_TAIL in views[1][0]
    assert 'Available fact keys:' not in views[1][0]
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


# ---- Issue #314: observed-question fact keys and form-level gate detail ----

LONG_KEY = ('Please describe, in a few sentences, your experience integrating large '
            'language models into production systems (tools, scale, outcomes)')
assert len(LONG_KEY) > 80 and ',' in LONG_KEY


def _long_control(**extra):
    extra = {'label': 'Experience', 'question': LONG_KEY, **extra}
    return field('#experience', tag='textarea', kind='textarea', **extra)


def test_long_comma_key_is_offered_only_for_a_current_enabled_exact_match():
    facts = {LONG_KEY: 'SYNTHETIC-ANSWER', 'Name': 'Test User'}
    assert forms._safe_fact_keys(facts, page(_long_control())) == sorted([LONG_KEY, 'Name'])
    # Normalized (case, edge decoration, separators) equality, question or label.
    shifted = _long_control(question=LONG_KEY.upper().replace(',', ' ') + ' *')
    assert LONG_KEY in forms._safe_fact_keys(facts, page(shifted))
    by_label = _long_control(question='', label=LONG_KEY)
    assert LONG_KEY in forms._safe_fact_keys(facts, page(by_label))
    # Legacy short keys do not need a page.
    assert forms._safe_fact_keys(facts) == ['Name']
    assert forms._safe_fact_keys(facts, None) == ['Name']
    assert forms._safe_fact_keys(facts, {'controls': 'not a list'}) == ['Name']
    # Key only: no value reaches the offered list.
    assert 'SYNTHETIC-ANSWER' not in repr(forms._safe_fact_keys(facts, page(_long_control())))


@pytest.mark.parametrize('control', [
    None,
    _long_control(disabled=True),
    _long_control(question='Something else entirely'),
    _long_control(question=LONG_KEY + ' and more'),       # superstring is not a match
    _long_control(question=LONG_KEY[:-12]),                # substring is not a match
])
def test_long_key_is_excluded_without_a_current_enabled_exact_match(control):
    current = page(control) if control else page()
    assert forms._safe_fact_keys({LONG_KEY: 'x', 'Name': 'y'}, current) == ['Name']


def test_unknown_enabled_state_is_not_enabled():
    control = _long_control()
    del control['disabled']
    assert forms._safe_fact_keys({LONG_KEY: 'x'}, page(control)) == []


def test_separator_only_key_never_matches_an_empty_unlabeled_control():
    key = '-,-,/' * 30
    assert len(key) > 80 and forms.normalize_label(key) == ''
    blank = field('#blank', question='', label='')
    assert forms._safe_fact_keys({key: 'x'}, page(blank)) == []
    assert forms._safe_fact_keys({' ': 'x', '': 'y'}, page(blank)) == []


@pytest.mark.parametrize('bad', [123, None, ['x'], {'a': 1}, b'bytes', 1.5, True])
def test_malformed_observed_question_or_label_is_a_nonmatch_and_never_normalized(
        bad, monkeypatch):
    seen = []
    real = forms.normalize_label

    def spy(value):
        seen.append(value)
        return real(value)
    monkeypatch.setattr(forms, 'normalize_label', spy)
    control = _long_control(question=bad, label=bad)
    assert forms._safe_fact_keys({LONG_KEY: 'x', 'Name': 'y'}, page(control)) == ['Name']
    assert all(isinstance(v, str) for v in seen)
    # A well-formed sibling still works.
    assert LONG_KEY in forms._safe_fact_keys({LONG_KEY: 'x'}, page(control, _long_control()))


@pytest.mark.parametrize('suffix', [
    'gender', 'disability status', 'sanctioned by an embargo', 'password',
    'API token', 'secret phrase', 'client secret'])
def test_current_match_never_overrides_sensitive_sanctions_or_credential_exclusions(suffix):
    key = f'{LONG_KEY} {suffix}'
    assert forms._safe_fact_keys({key: 'x'}, page(_long_control(question=key))) == []
    agree = f'I agree {LONG_KEY}'
    assert forms._safe_fact_keys({agree: 'x'}, page(_long_control(question=agree))) == []


def test_only_the_length_and_charset_limits_are_relaxed():
    for key in ('Name☃ of the “thing”', 'x' * 81):
        assert forms._safe_fact_keys(
            {key: 'v'}, page(_long_control(question='other'))) == []
        assert forms._safe_fact_keys(
            {key: 'v'}, page(_long_control(question=key))) == [key]


def _form_gate(action, current=None, facts=None):
    return forms._form_gate_message(action, current or page(field('#name', value='')),
                                    facts or {'Name': 'Test User'})


def test_form_gate_detail_keeps_a_safe_bounded_reason():
    message = _form_gate({'action': 'gate', 'question': 'Login is required to continue.'})
    assert message == f'{forms._GENERIC_GATE} Model reason: Login is required to continue.'
    long_reason = 'The form is incomplete ' * 30
    bounded = _form_gate({'action': 'finish', 'question': long_reason})
    assert bounded.startswith(forms._GENERIC_GATE) and len(bounded) < 260
    # Short field names from the page are ordinary words, not leaked content.
    assert 'Name' in _form_gate({'question': 'Missing answer for Name'})


@pytest.mark.parametrize('question', [
    None, 7, ['x'], {'a': 1}, '', '   ',
    'Open https://employer.test/apply?token=abc',
    'Click #submit-application > button',
    'Use input[name=email] please',
    'Password is SyntheticSecretValue1',
    'Reach me at someone@example.test',
    'Test User needs a call',                      # owner fact value
    'Prefilled SYNTHETIC-PREFILL here',            # control value
    'Employee reference 1234567',
    'api key sk1234abcd5678 is needed',
    'A' * 40,
    'Needs the gender answer',                     # sensitive text
    'Synthetic application form that is copied verbatim',   # page text run
])
def test_form_gate_detail_falls_back_for_unsafe_or_missing_reason(question):
    current = page(field('#name', value='SYNTHETIC-PREFILL'))
    current['text'] = 'Synthetic application form that is copied verbatim'
    assert _form_gate({'action': 'gate', 'question': question}, current) == forms._GENERIC_GATE


@pytest.mark.parametrize('question,expected', [
    ('Please log in first', 'login'), ('Password page', 'login'),
    ('Solve the CAPTCHA', 'captcha'), ('Missing fact for salary', 'missing_fact'),
    ('The form is incomplete', 'incomplete'), ('Weird unrelated words', 'other'),
    (None, 'other'), (5, 'other'), ('', 'other'),
])
def test_question_class_is_one_closed_value(question, expected):
    assert forms._question_class(question) == expected


@pytest.mark.asyncio
async def test_gate_finish_log_has_only_presence_length_and_closed_class(caplog):
    canaries = ['SYNTHETIC-SECRET-CANARY', '#submit > button', 'https://x.test/a?t=1',
                'Test User', 'Please log in first']
    responses = [{'action': 'gate', 'question': c} for c in canaries]
    responses += [{'action': 'finish', 'question': 'Solve the CAPTCHA'},
                  {'action': 'gate'}, {'action': 'gate', 'question': ['bad']},
                  {'action': 'fill', 'selector': '#name', 'question': 'ignored'}]

    async def decision(_task, _page, _history, *, model):
        return responses.pop(0)

    with caplog.at_level('INFO', logger=forms._LOG.name):
        for turn in range(1, 10):
            await forms._application_decision_with_log(
                decision, '', {}, [], model='m', facts={'Name': 'Test User'}, turn=turn)
    logs = [r.args for r in caplog.records if r.msg == 'application decision %s']
    assert len(logs) == 9
    assert [r['question_class'] for r in logs[:5]] == [
        'other', 'other', 'other', 'other', 'login']
    assert logs[5]['question_class'] == 'captcha'
    assert [(r['question_present'], r['question_length']) for r in logs[5:8]] == [
        (True, len('Solve the CAPTCHA')), (False, 0), (False, 0)]
    assert logs[0]['question_length'] == len(canaries[0])
    assert 'question_class' not in logs[8]
    for record in logs:
        assert all(type(v) in (int, bool, str) for v in record.values())
    text = '\n'.join(r.getMessage() for r in caplog.records
                     if r.msg == 'application decision %s')
    for canary in canaries[:4] + ['Solve the CAPTCHA', '#name']:
        assert canary not in text


class _AfterUploadHarness:
    """Closed daemon fixture: one résumé receiver, one long-question textarea."""

    def __init__(self, tmp_path, monkeypatch, decisions):
        self.document = tmp_path / 'Synthetic-Resume.pdf'
        self.document.write_bytes(b'%PDF-1.4\nsynthetic only\n')
        self.authority = fixture._SyntheticRun(str(self.document))
        # The stock facade permits one fixed fill; this offline test needs the one
        # staged-resume upload and a long-question fill, never a Submit.
        monkeypatch.setattr(fixture._SyntheticRun, 'matches', lambda self_, **kw: True)
        monkeypatch.setattr(fixture._SyntheticRun, 'facade', lambda self_, raw: raw)
        monkeypatch.setattr(fixture, '_daemon_origin', lambda: True)
        resume = field('#resume', kind='file', label='Resume', question='Resume',
                       files=[], required=False)
        self.current = page(resume, _long_control(), field('#name'))
        self.current['url'] = fixture.URL
        for c in self.current['controls']:
            c['form_action'] = fixture.URL
        self.events, self.views, self.decisions = [], [], list(decisions)
        harness = self

        async def scripted(self_, task, model_page, history, *, model):
            harness.events.append('decision')
            harness.views.append((task, deepcopy(model_page)))
            return harness.decisions.pop(0)

        class RawSession:
            def __init__(self, kind):
                assert kind == 'apply'

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def navigate(self, url):
                return deepcopy(harness.current)

            async def page(self):
                harness.events.append('page')
                return deepcopy(harness.current)

            async def call(self, *args, **kwargs):
                harness.events.append(args[0])
                harness.calls = getattr(harness, 'calls', []) + [args]
                control = next(c for c in harness.current['controls']
                               if c['selector'] == args[1])
                if args[0] == 'upload':
                    control['files'] = [Path(args[-1]).name]
                elif args[0] == 'fill':
                    control['value'], control['has_value'] = args[-1], bool(args[-1])
                return {}

        async def no_sleep(*_args, **_kwargs):
            harness.events.append('sleep')

        monkeypatch.setattr(fixture._SyntheticRun, 'decision', scripted)
        monkeypatch.setattr(bsk, 'Session', RawSession)
        monkeypatch.setattr(forms.asyncio, 'sleep', no_sleep)
        self.calls = []

    async def run(self, facts):
        token = fixture._SESSION_ENTRY.set(self.authority)
        try:
            return await forms.apply(
                fixture.URL, fixture.COMPANY, facts, 'synthetic-fixture',
                pk=fixture.PK, resume_path=self.authority.resume_path,
                fixture_context=self.authority)
        finally:
            fixture._SESSION_ENTRY.reset(token)


@pytest.mark.asyncio
async def test_long_key_reaches_the_model_after_the_single_upload_without_extra_reads(
        tmp_path, monkeypatch):
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [
        {'action': 'fill', 'selector': '#experience', 'fact': LONG_KEY},
        {'action': 'gate', 'question': 'Missing fact for the second question.'},
    ])
    result = await harness.run({**fixture._FACTS, LONG_KEY: 'SYNTHETIC-LONG-ANSWER'})
    task = harness.views[0][0]
    assert repr(LONG_KEY) in task and 'SYNTHETIC-LONG-ANSWER' not in task
    assert 'SYNTHETIC-LONG-ANSWER' not in repr(harness.views[0][1])
    assert [a[0] for a in harness.calls] == ['upload', 'fill']
    assert harness.calls[1][-1] == 'SYNTHETIC-LONG-ANSWER'
    assert 'sleep' not in harness.events
    # Upload, then only execute()'s own readback and the loop's normal observation.
    first_decision = harness.events.index('decision')
    after_upload = harness.events[harness.events.index('upload') + 1:first_decision]
    assert after_upload == ['page', 'page']
    assert result['status'] == 'gate' and result['reason'] == 'agent_review_unavailable'
    assert 'Model reason: Missing fact for the second question.' in result['question']
    assert not result.get('form_question')
    assert 'submit' not in harness.events and 'click' not in harness.events


@pytest.mark.asyncio
async def test_long_key_is_not_offered_when_the_matching_control_is_disabled(
        tmp_path, monkeypatch):
    harness = _AfterUploadHarness(tmp_path, monkeypatch, [{'action': 'gate'}])
    harness.current['controls'][1]['disabled'] = True
    result = await harness.run({**fixture._FACTS, LONG_KEY: 'SYNTHETIC-LONG-ANSWER'})
    assert repr(LONG_KEY) not in harness.views[0][0]
    assert result['reason'] == 'agent_review_unavailable'
    assert forms._GENERIC_GATE in result['question']
    assert 'Model reason' not in result['question']


@pytest.mark.asyncio
async def test_selector_less_vs_selector_bearing_gate_and_finish_paths(
        tmp_path, monkeypatch):
    cases = [
        ({'action': 'gate', 'question': 'Please log in'}, 'agent_review_unavailable', True),
        ({'action': 'finish', 'question': 'The form is incomplete'},
         'agent_review_unavailable', True),
        ({'action': 'gate', 'selector': '#experience', 'question': 'Please log in'},
         'owner_question', None),
        ({'action': 'finish', 'selector': '#experience', 'question': 'Please log in'},
         'agent_review_unavailable', False),
        ({'action': 'gate', 'selector': '#missing', 'question': 'Please log in'},
         None, None),
        ({'action': 'finish', 'selector': '#missing', 'question': 'Please log in'},
         None, None),
        ({'action': 'gate', 'selector': '#experience-disabled', 'question': 'x'},
         None, None),
    ]
    for action, reason, with_reason in cases:
        # Absent selectors now receive one fresh decision before the same gate.
        harness = _AfterUploadHarness(tmp_path, monkeypatch, [action, action])
        harness.current['controls'].append(
            _long_control(disabled=True, question='Disabled question', label='Disabled')
            | {'selector': '#experience-disabled', 'form_action': fixture.URL})
        result = await harness.run({**fixture._FACTS, LONG_KEY: 'SYNTHETIC-LONG-ANSWER'})
        assert result['status'] == 'gate'
        if reason is None:
            # Absent/disabled selector keeps the existing ControlGate refusal.
            assert result['reason'] != 'agent_review_unavailable', action
            assert 'Model reason' not in result['question']
            continue
        assert result['reason'] == reason, action
        if reason == 'owner_question':
            assert result['form_question']['selector'] == '#experience'
        else:
            assert not result.get('form_question')
            assert ('Model reason' in result['question']) is with_reason
        assert [a[0] for a in harness.calls] == ['upload']


def _page_with(**extra):
    current = page(field('#name', value=''), field(
        '#code', question='Private access code', label='Code'),
        field('#resume', kind='file', label='Resume', question='Resume',
              files=['CandidateCV.pdf']))
    current['text'] = 'Reference code 4321\nSupport desk opens at the side door'
    current.update(extra)
    return current


@pytest.mark.parametrize('question', [
    'Reference code 4321',                       # short copied page identifier
    'Open jobs.example.co',                     # bare host name, unlisted TLD
    'CandidateCV.pdf needs review',             # attachment name with extension
    'CandidateCV needs review',                 # attachment stem
    'Private access code',                      # exact short page question
    'The private access code is missing',       # page phrase embedded in prose
    'Support desk opens at the side door',      # copied page line, no digits
    'see ./file/path or a/b',                   # slashes are not in the charset
    'Code 12 needed',                           # any digit
])
def test_form_gate_detail_never_copies_short_page_or_attachment_content(question):
    current = _page_with(attached_file_displays=['CandidateCV.pdf'])
    assert forms._form_gate_message({'action': 'gate', 'question': question},
                                    current, {'Name': 'Test User'}) == forms._GENERIC_GATE


def test_form_gate_detail_blocks_resume_path_name_and_display_only_attachment():
    current = page(field('#name', value=''))
    current['attached_file_displays'] = [{'name': 'DisplayOnlyName.pdf'}]
    for reason in ('DisplayOnlyName needs a look', 'StagedResumeFile needs a look'):
        assert forms._form_gate_message(
            {'action': 'gate', 'question': reason}, current, {},
            '/tmp/x/StagedResumeFile.pdf') == forms._GENERIC_GATE


def test_form_gate_detail_still_allows_a_plain_generic_reason_next_to_page_content():
    message = forms._form_gate_message(
        {'action': 'gate', 'question': 'Login is required to continue.'}, _page_with(), {})
    assert message.endswith('Model reason: Login is required to continue.')


# ---------------------------------------------------------------------------
# Issue #316: Ashby-shaped choice questions repeat their option labels.
# Everything below is synthetic and offline; no owner fact, browser or Submit.
# ---------------------------------------------------------------------------

APPLY_KEY_LITERAL = (
    '\nAvailable fact keys (each is a stored, owner-approved answer; values are '
    'deliberately withheld from you and filled by the server when you return '
    'action fill/choose/combobox with fact set to the exact key; essay keys too, '
    'with essay true. A listed key is never a missing fact; gate only for a '
    'required control with no matching key): ')
NO_REPLAY_TAIL = (
    '\nDo not replay completed writes. Receipts are advisory; '
    'the server independently checks the final form and attachment.')
SKEY = 'Synthetic choice question, with a comma and no short form?'
LABELS = ('Option Alpha', 'Option Beta')


def choice_group(kind='radio', labels=LABELS, *, key=SKEY, name='g1', prefix='o',
                 repeat=None, **extra):
    question = key + ' ' + ' '.join(repeat if repeat is not None else labels)
    return [field(f'#{prefix}{i}', kind=kind, label=label, question=question,
                  name=name, value=label.lower().replace(' ', '-'), **extra)
            for i, label in enumerate(labels)]


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_stripped_question_key_is_offered_for_enabled_group(kind):
    form = page(*choice_group(kind))
    facts = {SKEY: 'Option Beta', 'Unrelated synthetic, key with comma': 'x'}
    assert forms._safe_fact_keys(facts, form) == [SKEY]
    assert forms._safe_fact_keys(facts, None) == []
    # Option-label-only concatenation is never promoted to a key.
    joined = {'Option Alpha, Option Beta': 'Option Beta'}
    assert forms._safe_fact_keys(joined, form) == []


@pytest.mark.parametrize('controls', [
    # mismatched suffix
    choice_group(repeat=('Option Alpha', 'Option Gamma')),
    # question is only the joined labels (empty stripped result)
    choice_group(key=''),
    # all controls disabled: no enabled anchor
    choice_group(disabled=True),
    # non-string question
    [field('#a', kind='radio', label='Option Alpha', question=None, name='g'),
     field('#b', kind='radio', label='Option Beta', question=None, name='g')],
])
def test_stripped_question_negatives_offer_nothing(controls):
    assert forms._safe_fact_keys({SKEY: 'x'}, page(*controls)) == []


def test_suffix_never_borrows_labels_from_other_type_or_question():
    radios = choice_group('radio')
    boxes = choice_group('checkbox', prefix='c', name='c1',
                         repeat=('Option Alpha', 'Option Beta'))
    # A radio anchor whose question needs checkbox labels does not match.
    mixed = [dict(radios[0]), dict(radios[1], question=radios[0]['question'])]
    mixed[0]['question'] = mixed[1]['question'] = SKEY + ' Option Gamma Option Delta'
    mixed.append(dict(boxes[0], question=mixed[0]['question']))
    assert forms._safe_fact_keys({SKEY: 'x'}, page(*mixed[:2])) == []
    # Different questions never contribute labels to one another.
    other = choice_group(key='Another synthetic question, different', prefix='p')
    assert forms._safe_fact_keys({SKEY: 'x'}, page(
        radios[0], dict(other[1]))) == []


def test_lexical_candidate_for_singleton_cross_name_and_disabled_sibling_is_not_authority():
    facts = {SKEY: 'Option Beta'}
    # Enabled anchor + disabled sibling: key offered, no eligible group.
    sibling = choice_group()
    sibling[1]['disabled'] = True
    form = page(*sibling)
    assert forms._safe_fact_keys(facts, form) == [SKEY]
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []
    # Same question, different radio names: lexical key, no receiving group.
    cross = choice_group(name='g1')[:1] + choice_group(name='g2', prefix='q')[1:]
    form = page(*cross)
    assert forms._safe_fact_keys(facts, form) == [SKEY]
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []
    # Same question across two native forms.
    split = choice_group()[:1] + choice_group(prefix='q', form_selector='#other')[1:]
    form = page(*split)
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []
    # Singleton whose question needs only its own label.
    one = [choice_group(labels=('Option Alpha',))[0]]
    form = page(*one)
    assert forms._safe_fact_keys(facts, form) == [SKEY]
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
async def test_suffix_backed_metadata_and_choose_write_stored_option(kind):
    form = page(*choice_group(kind))
    facts = {SKEY: 'Option Beta'}
    keys = forms._safe_fact_keys(facts, form)
    approved = forms._approved_choice_metadata(form, facts, keys)
    assert approved == [{'fact_key': SKEY, 'answer_present': True}]
    assert 'Option Beta' not in repr(approved)
    for requested in ('#o1', '#o0'):
        session = MemorySession(form)
        result, filled, evidence = await act(
            session, form,
            {'action': 'choose', 'selector': requested, 'fact': SKEY,
             'value': 'Option Alpha'}, facts)
        assert result == {'action': 'choose', 'selector': '#o1',
                          'label': 'Option Beta'}
        assert evidence['performed'] is True and filled == {'#o1': 'Option Beta'}
        assert [args for args, _ in session.calls] == [('click', '#o1')]
        current = await session.page()
        assert [c['selector'] for c in current['controls']
                if c['checked']] == ['#o1']
        intent = forms._write_intent(
            form, form['controls'][1], result, filled, evidence)
        assert intent is not None
        assert forms._completed_write_receipts(current, [intent]) == [
            {'action': 'choose', 'verified': True}]
        assert forms._model_projection(current, [result])[1] == [
            {'action': 'choose'}]

@pytest.mark.asyncio
async def test_suffix_fallback_is_refused_for_ineligible_receivers():
    facts = {SKEY: 'Option Beta'}
    cases = []
    single = page(choice_group(labels=('Option Beta',))[0])
    cases.append((single, '#o0'))
    sibling = choice_group()
    sibling[0]['disabled'] = True
    cases.append((page(*sibling), '#o1'))
    cross = choice_group(name='g1')[:1] + choice_group(name='g2', prefix='q')[1:]
    cases.append((page(*cross), '#q1'))
    duplicate = choice_group(labels=('Option Beta', 'Option Beta'))
    cases.append((page(*duplicate), '#o1'))
    absent = choice_group(labels=('Option Gamma', 'Option Delta'))
    cases.append((page(*absent), '#o1'))
    for form, selector in cases:
        assert forms._approved_choice_metadata(form, facts, [SKEY]) == []
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form, {'action': 'choose', 'selector': selector,
                                      'fact': SKEY}, facts)
        assert caught.value.reason == 'owner_question' and session.calls == []


def test_group_two_equal_groups_are_not_a_unique_receiver():
    first, second = choice_group(), choice_group(prefix='q', name='g2')
    form = page(*first, *second)
    assert forms._approved_choice_metadata(form, {SKEY: 'Option Beta'}, [SKEY]) == []


def test_fact_key_precedence_and_collisions():
    members = choice_group()
    form = page(*members)
    question = members[0]['question']
    # Exact and complete-question normalized lookups stay first.
    assert forms._fact_key({question: 'a', SKEY: 'b'}, question, members, form) == question
    full_norm = question.upper()
    assert forms._fact_key({full_norm: 'a', SKEY: 'b'}, question, members, form) == full_norm
    # Conflicting full-question collision is ambiguous, never a stripped retry.
    collision = {question.upper() + ' *': 'a', question.upper(): 'b', SKEY: 'c'}
    assert forms._fact_key(collision, question, members, form) is None
    # Stripped retry needs the receiving group; model-style lookups never get it.
    assert forms._fact_key({SKEY: 'a'}, question) is None
    assert forms._fact_key({SKEY: 'a'}, question, members) is None  # no page witness
    assert forms._fact_key({SKEY: 'a'}, question, members, form) == SKEY
    assert forms._fact_key({SKEY: 'a'}, question, members[:1], form) is None
    assert forms._fact_key({SKEY: 'a'}, question, [], form) is None
    # Equal values choose the sorted first; differing values are ambiguous.
    assert forms._fact_key({SKEY.upper(): ' a ', SKEY: 'a'}, question, members, form) \
        == min(SKEY, SKEY.upper())
    assert forms._fact_key({SKEY.upper(): 'b', SKEY: 'a'}, question, members, form) is None


@pytest.mark.asyncio
async def test_model_assertion_gets_no_suffix_inference():
    form = page(*choice_group())
    question = form['controls'][0]['question']
    facts = {SKEY: 'Option Beta'}
    # Asserting the full observed question text is not a stored key.
    session = MemorySession(form)
    with pytest.raises(forms.Gate):
        await act(session, form, {'action': 'choose', 'selector': '#o1',
                                  'fact': question}, facts)
    assert session.calls == []
    # Generic fill/combobox lookups keep exact/normalized behaviour only.
    assert forms._owner_action_answer({'fact': question}, facts) == ''
    assert forms._owner_action_answer({'fact': SKEY}, facts) == 'Option Beta'


@pytest.mark.asyncio
async def test_suffix_backed_python_none_is_ineligible_even_for_literal_none_option():
    form = page(*choice_group(labels=('None', 'Option Beta')))
    facts = {SKEY: None}
    keys = forms._safe_fact_keys(facts, form)
    assert keys == [SKEY]  # visibility is unchanged
    assert forms._approved_choice_metadata(form, facts, keys) == []
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'selector': '#o0',
                                  'fact': SKEY}, facts)
    assert caught.value.reason == 'owner_question' and session.calls == []
    # Empty string answer follows the same no-write path.
    facts = {SKEY: ''}
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []


def test_exact_normalized_metadata_is_unchanged_for_none_answer():
    group = [field('#a', kind='radio', label='None', question='Eligibility', name='e'),
            field('#b', kind='radio', label='Yes', question='Eligibility', name='e')]
    form = page(*group)
    assert forms._approved_choice_metadata(
        form, {'Eligibility': None}, ['Eligibility']) == [
        {'fact_key': 'Eligibility', 'answer_present': True}]


@pytest.mark.parametrize('bad_key', [
    'Account password, saved login', 'Sanctions screening, export controls',
    'I agree to the terms, accept all', 'Secret token, private credential'])
def test_protected_keys_stay_withheld_even_when_stripped_question_matches(bad_key):
    form = page(*choice_group(key=bad_key))
    facts = {bad_key: 'Option Beta'}
    keys = forms._safe_fact_keys(facts, form)
    assert keys == []
    assert forms._approved_choice_metadata(form, facts, keys) == []



SHORT_KEY = 'Synthetic preference Alpha Beta'


@pytest.mark.asyncio
async def test_suffix_spanning_two_radio_groups_authorizes_nothing():
    """2+2 split inventory: g2's own labels must not strip a g1+g2 suffix."""
    question = 'Synthetic preference Alpha Beta Gamma Delta'
    controls = [
        field('#a', kind='radio', label='Alpha', question=question, name='g1'),
        field('#b', kind='radio', label='Beta', question=question, name='g1'),
        field('#c', kind='radio', label='Gamma', question=question, name='g2'),
        field('#d', kind='radio', label='Delta', question=question, name='g2'),
    ]
    form = page(*controls)
    facts = {SHORT_KEY: 'Delta'}
    assert forms._approved_choice_metadata(form, facts, [SHORT_KEY]) == []
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'selector': '#d',
                                  'fact': SHORT_KEY}, facts)
    assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
async def test_suffix_same_question_radio_and_checkbox_groups_are_not_unique():
    form = page(*choice_group('radio'), *choice_group('checkbox', prefix='c', name='c1'))
    facts = {SKEY: 'Option Beta'}
    assert forms._approved_choice_metadata(form, facts, [SKEY]) == []
    for selector in ('#o1', '#c1'):
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form, {'action': 'choose', 'selector': selector,
                                      'fact': SKEY}, facts)
        assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
async def test_suffix_duplicate_identical_groups_cannot_be_chosen():
    form = page(*choice_group(), *choice_group(prefix='q', name='g2'))
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'selector': '#o1',
                                  'fact': SKEY}, {SKEY: 'Option Beta'})
    assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_key', [
    'I agree to the terms, accept all', 'Sanctions screening, export controls',
    'Account password, saved login'])
async def test_suffix_backed_choose_keeps_protected_key_exclusions(bad_key):
    form = page(*choice_group(key=bad_key))
    session = MemorySession(form)
    with pytest.raises(forms.Gate):
        await act(session, form, {'action': 'choose', 'selector': '#o1',
                                  'fact': bad_key}, {bad_key: 'Option Beta'})
    assert session.calls == []


# ---------------------------------------------------------------------------
# Issue #318: fact-backed choose is bound to the stored-answer group member.
# These are synthetic controls and a sealed, offline two-decision apply loop.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
async def test_matched_member_already_checked_has_no_new_effect_or_receipt(kind):
    form = page(*choice_group(kind))
    form['controls'][1]['checked'] = True
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#o0',
                        'fact': SKEY, 'value': 'Option Alpha'},
        {SKEY: 'Option Beta'})
    assert result['selector'] == '#o1' and result['label'] == 'Option Beta'
    assert filled == {'#o1': 'Option Beta'}
    assert evidence == {'performed': False} and session.calls == []
    assert forms._write_intent(form, form['controls'][1], result,
                               filled, evidence) is None
    assert forms._completed_write_receipts(await session.page(), []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('case', [
    'missing-match', 'duplicate-answer', 'disabled-sibling', 'unowned',
    'split-radio-name', 'split-form', 'unrelated-fact', 'outside-group',
    'duplicate-matched-selector',
])
async def test_owner_choice_rejects_invalid_group_or_matched_receiver(case):
    members = choice_group()
    facts = {SKEY: 'Option Beta'}
    requested = '#o0'
    if case == 'missing-match':
        facts[SKEY] = 'Option Gamma'
    elif case == 'duplicate-answer':
        members = choice_group(labels=('Option Beta', 'Option Beta'))
    elif case == 'disabled-sibling':
        members[1]['disabled'] = True
    elif case == 'unowned':
        members[0].update(in_form=False, form_selector='')
    elif case == 'split-radio-name':
        members[1]['name'] = 'different'
    elif case == 'split-form':
        members[1]['form_selector'] = '#another-form'
    elif case == 'unrelated-fact':
        facts['Unrelated'] = 'Option Beta'
    elif case == 'outside-group':
        requested = '#other'
        members.append(field('#other', kind='radio', name='other',
                             label='Yes', question='Other question'))
    elif case == 'duplicate-matched-selector':
        members.append(field('#o1', label='Unrelated field',
                             question='Unrelated field'))
    form = page(*members)
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form,
                  {'action': 'choose', 'selector': requested,
                   'fact': 'Unrelated' if case == 'unrelated-fact' else SKEY,
                   'value': 'Option Alpha'}, facts)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == requested
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['absent', 'disabled', 'duplicated'])
async def test_requested_selector_retains_control_gate_not_owner_question(case):
    members = choice_group()
    requested = '#o0'
    if case == 'absent':
        requested = '#nonexistent'
    elif case == 'disabled':
        members[0]['disabled'] = True
    else:
        members.append(field('#o0', label='Other control', question='Other question'))
    form = page(*members)
    session = MemorySession(form)
    with pytest.raises(forms.ControlGate) as caught:
        await act(session, form,
                  {'action': 'choose', 'selector': requested, 'fact': SKEY},
                  {SKEY: 'Option Beta'})
    assert caught.value.diagnostics['reason'].startswith(
        {'absent': 'absent', 'disabled': 'disabled',
         'duplicated': 'ambiguous'}[case])
    assert session.calls == []


@pytest.mark.asyncio
async def test_choose_without_selector_keeps_generic_missing_selector_gate():
    form = page(*choice_group())
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'fact': SKEY},
                  {SKEY: 'Option Beta'})
    assert not isinstance(caught.value, forms.ControlGate)
    assert caught.value.reason != 'owner_question'
    assert 'selector is missing' in str(caught.value)
    assert session.calls == []


@pytest.mark.asyncio
async def test_complete_same_question_groups_keep_exact_target_owned_authority():
    first = choice_group()
    second = choice_group(name='other', prefix='q', form_selector='#other-form')
    form = page(*first, *second)
    full = first[0]['question']
    facts = {full: 'Option Beta'}
    assert forms._approved_choice_metadata(form, facts, [full]) == []
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#o0',
                        'fact': full, 'value': 'Option Alpha'}, facts)
    assert result['selector'] == '#o1' and filled == {'#o1': 'Option Beta'}
    assert [args for args, _ in session.calls] == [('click', '#o1')]
    assert evidence['performed'] is True
    intent = forms._write_intent(form, first[1], result, filled, evidence)
    assert forms._completed_write_receipts(await session.page(), [intent]) == [
        {'action': 'choose', 'verified': True}]
    # The page-wide lexical suffix witness is still not valid for 2+2 groups.
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form, {'action': 'choose', 'selector': '#o0',
                                  'fact': SKEY}, {SKEY: 'Option Beta'})
    assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
async def test_equal_full_and_stripped_keys_accept_only_observed_witness():
    form = page(*choice_group())
    full = form['controls'][0]['question']
    cases = (
        ({SKEY: ' Option Beta ', full: 'Option Beta'}, SKEY),
        ({SKEY: 'Option Beta', full: 'Option Beta'}, full),
        ({SKEY: 'Option Beta', full.upper() + ' *': 'Option Beta'}, SKEY),
        ({SKEY: 'Option Beta', full: 'Option Beta',
          full.upper() + ' *': ' Option Beta '}, SKEY),
    )
    for facts, asserted in cases:
        session = MemorySession(form)
        result, filled, evidence = await act(
            session, form, {'action': 'choose', 'selector': '#o0',
                            'fact': asserted, 'value': 'Option Alpha'}, facts)
        assert result['selector'] == '#o1' and result['label'] == 'Option Beta'
        assert filled == {'#o1': 'Option Beta'} and evidence['performed']
        assert [args for args, _ in session.calls] == [('click', '#o1')]
        assert forms._completed_write_receipts(
            await session.page(),
            [forms._write_intent(form, form['controls'][1],
                                 result, filled, evidence)]) == [
            {'action': 'choose', 'verified': True}]
    assert forms._owner_action_answer({'fact': full}, {SKEY: 'Option Beta'}) == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('protected', [
    'I agree to terms',
    'I have a professional licence',
])
async def test_full_stripped_join_refuses_protected_question_aliases(protected):
    """An exact full key cannot launder protected stripped-key assertions."""
    form = page(*choice_group(key=protected, labels=('Yes', 'No')))
    full = form['controls'][0]['question']
    facts = {protected: 'Yes', full: 'Yes'}
    # The full observed key resolves exactly, not via the old suffix fallback.
    assert forms._fact_key_lookup(facts, full, form['controls'], form) == (full, False)
    assert forms._group_stripped_question(full, form['controls'], form) == (
        forms.normalize_label(protected))
    assert forms._approved_choice_metadata(
        form, facts, forms._safe_fact_keys(facts, form)) == []

    session = MemorySession(form)
    filled, evidence = {}, {}
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            session, form,
            {'action': 'choose', 'selector': '#o1', 'fact': protected,
             'value': 'No'},
            facts=facts, filled=filled, resume_path='', company='example-co',
            jd_text='', resume_tex='', github='', pk=PK, job_url=URL,
            write_evidence=evidence)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == '#o1'
    assert session.calls == [] and filled == {} and evidence == {}
    assert all(not c['checked'] for c in (await session.page())['controls'])


@pytest.mark.asyncio
async def test_new_equivalence_join_refuses_conflicting_full_aliases():
    form = page(*choice_group())
    full = form['controls'][0]['question']
    collision = full.upper() + ' *'
    cases = (
        {SKEY: 'Option Beta', full: 'Option Alpha'},
        {SKEY: 'Option Beta', full: 'Option Beta', collision: 'Option Alpha'},
        {SKEY: 'Option Beta', full.upper(): 'Option Beta',
         collision: 'Option Alpha'},
    )
    for facts in cases:
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form,
                      {'action': 'choose', 'selector': '#o0',
                       'fact': SKEY, 'value': 'Option Alpha'}, facts)
        assert caught.value.reason == 'owner_question' and session.calls == []
    # The exact lookup precedence outside the new join must not change.
    assert forms._fact_key({full: 'Option Beta', collision: 'Option Alpha'},
                           full, form['controls'], form) == full
    assert forms._owner_action_answer(
        {'fact': full}, {full: 'Option Beta', collision: 'Option Alpha'}) == 'Option Beta'


@pytest.mark.asyncio
async def test_equivalence_rejects_python_none_empty_and_unrelated_equal_fact():
    members = choice_group(labels=('None', 'Option Beta'))
    form = page(*members)
    full = members[0]['question']
    cases = (
        ({SKEY: 'None', full: None}, SKEY),
        ({SKEY: None, full: 'None'}, SKEY),
        ({SKEY: 'None', full: ''}, SKEY),
        ({SKEY: 'None', full: 'None', 'Unrelated': 'None'}, 'Unrelated'),
    )
    for facts, asserted in cases:
        session = MemorySession(form)
        with pytest.raises(forms.Gate) as caught:
            await act(session, form, {'action': 'choose', 'selector': '#o1',
                                      'fact': asserted, 'value': 'Option Beta'}, facts)
        assert caught.value.reason == 'owner_question' and session.calls == []


@pytest.mark.asyncio
async def test_competing_prechecked_checkbox_never_forges_matched_choice_receipt():
    form = page(*choice_group('checkbox'))
    form['controls'][0]['checked'] = True
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#o0', 'fact': SKEY},
        {SKEY: 'Option Beta'})
    assert evidence['performed'] is True
    assert [args for args, _ in session.calls] == [('click', '#o1')]
    intent = forms._write_intent(
        form, form['controls'][1], result, filled, evidence)
    assert intent is not None
    assert [c['checked'] for c in (await session.page())['controls']] == [True, True]
    assert forms._completed_write_receipts(await session.page(), [intent]) == []
    stale = await session.page()
    stale['controls'][1]['checked'] = False
    assert forms._completed_write_receipts(stale, [intent]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('readback', ['verified', 'already-checked', 'no-effect'])
async def test_two_decision_apply_receipts_bind_the_actual_choice_member(
        tmp_path, monkeypatch, readback):
    """No Submit, live browser, Redis or owner data: sealed fixture and stub."""
    document = tmp_path / 'Synthetic-Resume.pdf'
    document.write_bytes(b'%PDF-1.4\nsynthetic only\n')
    authority = fixture._SyntheticRun(str(document))
    monkeypatch.setattr(fixture, '_daemon_origin', lambda: True)
    # This fixture extends the stock closed synthetic fact map with Eligibility.
    monkeypatch.setattr(fixture._SyntheticRun, 'matches', lambda self_, **kw: True)
    monkeypatch.setattr(fixture._SyntheticRun, 'facade', lambda self, raw: raw)
    current = page(*eligibility())
    current['url'] = fixture.URL
    for c in current['controls']:
        c['form_action'] = fixture.URL
    if readback == 'already-checked':
        current['controls'][1]['checked'] = True
    memory = MemorySession(current)
    views = []

    async def scripted(self, task, model_page, history, *, model):
        assert self is authority and model == 'synthetic-fixture'
        views.append((task, deepcopy(model_page), deepcopy(history)))
        if len(views) == 1:
            return {'action': 'choose', 'selector': '#yes',
                    'fact': 'Eligibility', 'value': 'Yes'}
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
            return await memory.page()

        async def page(self):
            return await memory.page()

        async def call(self, *args, **kwargs):
            if readback == 'no-effect':
                memory.calls.append((args, kwargs))
                return {}
            return await memory.call(*args, **kwargs)

    monkeypatch.setattr(fixture._SyntheticRun, 'decision', scripted)
    monkeypatch.setattr(bsk, 'Session', RawSession)
    token = fixture._SESSION_ENTRY.set(authority)
    try:
        result = await forms.apply(
            fixture.URL, fixture.COMPANY,
            {**fixture._FACTS, 'Eligibility': 'No'}, 'synthetic-fixture',
            pk=fixture.PK, resume_path=authority.resume_path,
            fixture_context=authority)
    finally:
        fixture._SESSION_ENTRY.reset(token)
    assert result['status'] == 'gate' and len(views) == 2
    assert views[0][1]['completed_writes'] == []
    expected = ([{'action': 'choose', 'verified': True}]
                if readback == 'verified' else [])
    assert views[1][1]['completed_writes'] == expected
    assert views[1][2] == [{'action': 'choose'}]
    assert views[1][1]['attachment_completed'] is False
    assert views[1][1]['approved_choices'] == [
        {'fact_key': 'Eligibility', 'answer_present': True}]
    assert [args for args, _ in memory.calls] == (
        [('click', '#no')] if readback != 'already-checked' else [])
    assert 'No' not in repr(views[1][1]['completed_writes'])
    assert 'No' not in repr(views[1][2])


@pytest.mark.asyncio
@pytest.mark.parametrize('split', ['radio-name', 'form-owner'])
async def test_exact_full_key_cannot_turn_split_sibling_into_group_authority(split):
    members = choice_group()
    full = members[0]['question']
    if split == 'radio-name':
        members[1]['name'] = 'another-name'
    else:
        members[1]['form_selector'] = '#other-form'
    form = page(*members)
    session = MemorySession(form)
    with pytest.raises(forms.Gate) as caught:
        await act(session, form,
                  {'action': 'choose', 'selector': '#o1', 'fact': full,
                   'value': 'Option Beta'}, {full: 'Option Beta'})
    assert caught.value.reason == 'owner_question'
    assert session.calls == []


@pytest.mark.asyncio
async def test_independent_exact_key_single_radio_preserves_existing_choice_path():
    """A standalone radio has no split sibling and still uses its stored fact."""
    form = page(field('#only', kind='radio', label='Yes',
                      question='Synthetic standalone', name='standalone', value='yes'))
    session = MemorySession(form)
    result, filled, evidence = await act(
        session, form, {'action': 'choose', 'selector': '#only',
                        'fact': 'Synthetic standalone', 'value': 'No'},
        {'Synthetic standalone': 'Yes'})
    assert result['selector'] == '#only' and evidence['performed'] is True
    assert filled == {'#only': 'Yes'}
    assert [args for args, _ in session.calls] == [('click', '#only')]
    intent = forms._write_intent(form, form['controls'][0],
                                 result, filled, evidence)
    assert forms._completed_write_receipts(await session.page(), [intent]) == [
        {'action': 'choose', 'verified': True}]
