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
    assert filled == {'#name-combo': answer.strip()}
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
