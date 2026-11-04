"""Issue #326: synthetic typeahead observations, with no browser or network."""
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_absent_decision import name_combo
from tests.tools.test_browser_apply_decision_regression import page


def option(label, selector='#city-option', **flags):
    return dict(label=label, value='', selector=selector, safe=True, **flags)


class TypeaheadSession:
    def __init__(self, observations):
        self.observations = observations
        self.reads = 0
        self.calls = []

    async def page(self):
        result = self.observations[min(self.reads, len(self.observations) - 1)]
        self.reads += 1
        return deepcopy(result)

    async def call(self, *args):
        self.calls.append(args)
        return {}


def snapshot(options, tag='input'):
    target = name_combo()
    target.update(tag=tag)
    target['combobox']['options'] = options
    return page(target)


async def run_picker(answer, options, *, delayed=0, monkeypatch, caplog):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(forms.asyncio, 'sleep', sleep)
    empty = snapshot([])
    ready = snapshot(options)
    session = TypeaheadSession([empty] * (1 + delayed) + [ready])
    evidence, filled = {}, {}
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        await forms._execute_combobox(
            session, empty, {'action': 'combobox', 'selector': '#name-combo', 'fact': 'City'},
            facts={'City': answer}, filled=filled, pk='example-co#1',
            job_url='https://example.test/jobs/1', ctx=None, provenance=None,
            write_evidence=evidence)
        forms._log_combobox_outcome(None, '#name-combo', evidence)
    expected = (evidence['selected_option']['label']
                if evidence['performed'] and evidence['match'] == 'segment' else answer.strip())
    assert filled == {'#name-combo': expected}
    return session, evidence, sleeps


@pytest.mark.asyncio
@pytest.mark.parametrize('answer,options,selected,match', [
    ('Testville', [option('Testville, Test Region, Testland')], '#city-option', 'segment'),
    (' Testville , TESTLAND ', [option('Testville, Otherland', '#other'),
                               option(' testville , Test Region, Testland')], '#city-option', 'segment'),
    ('Testville', [option('Testville, Testland'),
                  option('Testville, Otherland', '#other')], None, 'segment'),
    ('Testville', [option('Testville, Testland'),
                  option('Testville', '#exact')], '#exact', 'exact'),
    ('Testville', [dict(option('Other caption'), value='Testville'),
                  option('Testville, Testland', '#other')], '#city-option', 'exact'),
    ('Testville', [option('Testville, Testland'), option('Testville'),
                  option('Testville', '#twin')], None, 'exact'),
    ('Testville, Testland', [option('Testville, Not Testland')], None, 'none'),
    ('Testville', [option('New Testville, Testland')], None, 'none'),
])
async def test_unique_segment_and_exact_precedence(answer, options, selected, match, monkeypatch, caplog):
    session, evidence, sleeps = await run_picker(
        answer, options, monkeypatch=monkeypatch, caplog=caplog)
    clicks = [call[1] for call in session.calls if call[0] == 'click']
    assert clicks == ['#name-combo'] + ([selected] if selected else [])
    assert session.calls[1] == ('fill', '#name-combo', '--value', answer.split(',')[0].strip())
    assert evidence['performed'] is (selected is not None)
    assert evidence['match'] == match
    assert evidence['options_seen'] == len(options)
    assert sleeps == [] and session.reads == 2
    assert f'match={match} options_seen={len(options)}' in caplog.text
    assert 'Testville' not in caplog.text and 'Testland' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('flag', ['safe-false', 'safe-missing', 'submit', 'navigates', 'commits_application'])
@pytest.mark.parametrize('caption', ['Testville', 'Testville, Testland'])
async def test_ineligible_option_is_never_clicked(flag, caption, monkeypatch, caplog):
    candidate = option(caption)
    if flag == 'safe-false':
        candidate['safe'] = False
    elif flag == 'safe-missing':
        candidate.pop('safe')
    else:
        candidate[flag] = True
    session, evidence, _ = await run_picker(
        'Testville', [candidate], monkeypatch=monkeypatch, caplog=caplog)
    assert [call for call in session.calls if call[0] == 'click'] == [('click', '#name-combo')]
    assert evidence['performed'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('delayed', [1, 3, 4])
async def test_delayed_options_and_exhausted_budget(delayed, monkeypatch, caplog):
    session, evidence, sleeps = await run_picker(
        'Testville, Testland', [option('Testville, Test Region, Testland')],
        delayed=delayed, monkeypatch=monkeypatch, caplog=caplog)
    assert sleeps == [0.5] * min(delayed, 3)
    assert session.reads == 2 + min(delayed, 3)
    assert evidence['performed'] is (delayed <= 3)
    assert evidence['options_seen'] == (1 if delayed <= 3 else 0)
    assert evidence['match'] == ('segment' if delayed <= 3 else 'none')


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [
    [],
    [option('Testville, Testland'), option('Testville, Testland', '#other')],
    [option('Testville, Testland', submit=True)],
])
async def test_unselected_query_restores_full_answer_before_final_readback(options, monkeypatch):
    async def sleep(_seconds):
        pass

    monkeypatch.setattr(forms.asyncio, 'sleep', sleep)
    initial = snapshot([])
    initial['controls'][0]['combobox']['hidden_selector'] = ''
    initial['controls'].append(dict(
        selector='#resume', label='Resume', type='file', in_form=True,
        form_selector='#application', disabled=False, files=['Resume.pdf']))

    class FreeTextSession(TypeaheadSession):
        async def call(self, *args):
            await super().call(*args)
            if args[0] == 'fill':
                control = self.observations[0]['controls'][0]
                control.update(value=args[3], has_value=bool(args[3]))
                control['combobox']['options'] = options

    session = FreeTextSession([initial])
    filled, evidence = {}, {}
    answer = 'Testville, Testland'
    await forms._execute_combobox(
        session, deepcopy(initial), {'selector': '#name-combo', 'fact': 'City'},
        facts={'City': answer}, filled=filled, pk='example-co#1',
        job_url='https://example.test/jobs/1', ctx=None, provenance=None,
        write_evidence=evidence)
    observed = await session.page()
    assert observed['controls'][0]['value'] == answer
    assert session.calls[-1] == ('fill', '#name-combo', '--value', answer)
    assert evidence['performed'] is False
    assert not any(call[0] == 'click' and call[1] != '#name-combo'
                   for call in session.calls)
    # A receiverless free-text combo can pass the existing final form check;
    # prove the actual DOM answer it accepts is full, not the search prefix.
    forms.check_form(observed, filled, True, 'Resume.pdf')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['segment', 'exact-label', 'exact-value', 'no-click'])
async def test_segment_caption_reaches_existing_verified_receipt(mode):
    from tests.tools.test_browser_apply_decision_regression import MemorySession

    answer = 'Testville, Testland'
    caption = answer if mode == 'exact-label' else 'Testville, Test Region, Testland'
    candidate = option(caption)
    candidate['value'] = answer if mode == 'exact-value' else 'city-42'
    if mode == 'no-click':
        candidate['safe'] = False
    initial = snapshot([candidate])
    original = deepcopy(initial['controls'][0])
    session = MemorySession(initial)
    filled, evidence = {}, {}
    result = await forms._execute_combobox(
        session, initial, {'selector': '#name-combo', 'fact': 'City'},
        facts={'City': answer}, filled=filled, pk='example-co#1',
        job_url='https://example.test/jobs/1', ctx=None, provenance=None,
        write_evidence=evidence)
    expected = caption if mode == 'segment' else answer
    assert filled == {'#name-combo': expected}
    intent = forms._write_intent(initial, original, result, filled, evidence)
    if mode == 'no-click':
        assert intent is None
    else:
        assert intent is not None
        assert forms._completed_write_receipts(await session.page(), [intent]) == [
            {'action': 'combobox', 'selector': '#name-combo', 'verified': True}]


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [0, 1])
@pytest.mark.parametrize('fact_mode', [
    'resolved', 'missing-key', 'null-key', 'empty-key', 'unknown-key',
    'null-value', 'empty-value', 'whitespace-value', 'omitted',
    'exact-label', 'exact-value',
])
async def test_explicit_nested_choice_binds_ambiguous_segments(selected, fact_mode, caplog):
    from tests.tools.test_browser_apply_decision_regression import MemorySession, act

    options = [option('Testville, North Region, Testland', '#north', unique=True),
               option('Testville, South Region, Testland', '#south', unique=True)]
    chosen = options[selected]
    action = {'action': 'click', 'selector': chosen['selector'], 'fact': 'City',
              'value': 'Untrusted model answer'}
    facts = {'City': 'Testville, Testland'}
    if fact_mode == 'missing-key':
        facts.clear()
    elif fact_mode in {'null-key', 'empty-key', 'unknown-key'}:
        action['fact'] = {'null-key': None, 'empty-key': '', 'unknown-key': 'Unknown'}[fact_mode]
    elif fact_mode in {'null-value', 'empty-value', 'whitespace-value'}:
        facts['City'] = {'null-value': None, 'empty-value': '',
                         'whitespace-value': '   '}[fact_mode]
    elif fact_mode == 'omitted':
        action.pop('fact')
    elif fact_mode == 'exact-label':
        facts['City'] = chosen['label']
    elif fact_mode == 'exact-value':
        chosen['value'] = facts['City']
    form = snapshot(options)
    before = deepcopy((form, action, facts))
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        resolved = forms._resolve_decision_option(form, action, facts)
        assert resolved == {'action': 'combobox', 'selector': '#name-combo',
                            'value': chosen['label']}
        session = MemorySession(form)
        _, filled, evidence = await act(session, form, resolved, facts)
        forms._log_combobox_outcome(None, '#name-combo', evidence)
    assert session.calls == [(('click', chosen['selector']), {})]
    assert filled == {'#name-combo': chosen['label']}
    assert evidence['selected_option']['selector'] == chosen['selector']
    assert evidence['match'] == 'exact'
    assert (form, action, facts) == before
    assert 'segment_candidates=0' in caplog.text
    assert 'Testville' not in caplog.text and 'Testland' not in caplog.text


@pytest.mark.parametrize('mode', [
    'unsafe', 'safe-missing', 'disabled', 'invisible', 'not-unique',
    'submit', 'navigates', 'commits_application', 'mismatch',
    'wrong-first-segment', 'partial-segment', 'empty-segment',
    'duplicate-label', 'duplicate-value', 'duplicate-selector',
    'duplicate-fact-value',
])
def test_explicit_segment_choice_preserves_refusals(mode):
    chosen = option('Testville, North Region, Testland', '#north', unique=True)
    options = [chosen, option('Testville, South Region, Testland', '#south', unique=True)]
    facts = {'City': 'Testville, Testland'}
    if mode == 'unsafe':
        chosen['safe'] = False
    elif mode == 'safe-missing':
        chosen.pop('safe')
    elif mode == 'disabled':
        chosen['disabled'] = True
    elif mode == 'invisible':
        chosen['visible'] = False
    elif mode == 'not-unique':
        chosen['unique'] = False
    elif mode in {'submit', 'navigates', 'commits_application'}:
        chosen[mode] = True
    elif mode in {'mismatch', 'wrong-first-segment', 'partial-segment', 'empty-segment'}:
        facts['City'] = {'mismatch': 'Other City, Testland',
                         'wrong-first-segment': 'North Region, Testland',
                         'partial-segment': 'Testville, Test',
                         'empty-segment': 'Testville,'}[mode]
    elif mode == 'duplicate-label':
        options.append(dict(chosen, selector='#twin'))
    elif mode == 'duplicate-value':
        options[1]['value'] = chosen['label']
    elif mode == 'duplicate-selector':
        options[1]['selector'] = chosen['selector']
    elif mode == 'duplicate-fact-value':
        chosen['value'] = options[1]['value'] = facts['City']
    form = snapshot(options)
    action = {'action': 'click', 'selector': '#north', 'fact': 'City'}
    assert forms._resolve_decision_option(form, action, facts) is action


@pytest.mark.asyncio
async def test_unresolved_segment_ambiguity_reports_only_candidate_count(monkeypatch, caplog):
    session, evidence, _ = await run_picker(
        'Testville, Testland',
        [option('Testville, North Region, Testland', '#north'),
         option('Testville, South Region, Testland', '#south')],
        monkeypatch=monkeypatch, caplog=caplog)
    assert evidence['performed'] is False
    assert evidence['segment_candidates'] == 2
    assert 'segment_candidates=2' in caplog.text
    assert [call for call in session.calls if call[0] == 'click'] == [('click', '#name-combo')]
