"""Issue 282 / PR 286: a captured formless-root Ashby survey radio group.

The synthetic shape matches the public capture: no <form>, opacity-0 native
radios with no value attribute, each drawn only through its own visible label,
inside the one validated survey container. PAGE ignores opacity, so only the
root evidence and the fixed witness carry the hidden-choice flag.

The offline DOM stub reports an empty IDL value; a real browser reports "on".
The tests set that fact, the group question and requiredness explicitly (the
captured radios are optional; execute() only selects a required choice).
"""
import asyncio
from copy import deepcopy
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import PK, entry, posting
from tests.tools.test_browser_ashby_survey import _CHOICES, _LIVE, _live
from tests.tools.test_browser_formless import root_world

DECLINE = 'Decline to self-identify'
OTHER = 'Synthetic option 0'
QUESTION = 'Gender'
FIRST, SECOND = '#survey-choice-0', '#survey-choice-1'


def _prepare(snapshot, required=True):
    for c in snapshot['controls']:
        if c['selector'] in _CHOICES:
            c.update(value='on', has_value=True, question=QUESTION, required=required)
            if c['label'] == 'Synthetic option 1':
                c['label'] = DECLINE
            if c.get('visible_text') == 'Synthetic option 1':
                c['visible_text'] = DECLINE
    for e in snapshot['entry']['root_evidence']:
        if e['selector'] in _CHOICES:
            e.update(value='on', required=required)
            if e['label'] == 'Synthetic option 1':
                e['label'] = DECLINE


async def survey_world(monkeypatch, required=True):
    row, raw, decision, provenance = root_world(monkeypatch, **_LIVE['openai'])
    for snapshot in (raw.current, raw.fixed):
        _prepare(snapshot, required)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    witness = {c['selector']: c for c in raw.fixed['controls']}
    survey_world.session = session
    return row, raw, page, witness, provenance


def radio(page, selector):
    return next(c for c in page['controls'] if c['selector'] == selector)


def ask(page, witness, provenance, selector=FIRST):
    return forms._disclosure_question(
        page, deepcopy(radio(page, selector)), witness, posting('ashby'),
        provenance=provenance)


def grant_of(question, answer=DECLINE):
    return question['label'], {
        'value': answer, 'selector': question['selector'], 'url': question['url'],
        'label': question['control_label'], 'question': question['question'],
        'tag': question['tag'], 'type': question['type'],
        'form_selector': question['form_selector'],
        'form_action': question['form_action'],
        'radio_name': question['radio_name'],
        'radio_members': deepcopy(question['radio_members'])}


async def test_page_does_not_carry_the_flag_but_both_readers_do(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    for selector in _CHOICES:
        assert 'ashby_hidden_choice' not in radio(page, selector)
        assert witness[selector]['ashby_hidden_choice'] is True
        assert witness[selector]['visible'] is False
        evidence = [e for e in page['entry']['root_evidence'] if e['selector'] == selector]
        assert len(evidence) == 1 and evidence[0]['ashby_hidden_choice'] is True


async def test_hidden_survey_radios_reach_the_decline_question(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    question = ask(page, witness, provenance)
    assert question['ashby_hidden_choice'] is True and question['visible'] is False
    assert question['radio_name'] == 'survey-choice'
    assert [m['caption'] for m in question['radio_members']] == [OTHER, DECLINE]
    assert [m['selector'] for m in question['radio_members']] == [FIRST, SECOND]
    assert question['form_selector'] == '#form'


async def test_bound_rooted_radio_carries_group_identity(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    bound = forms._bound_target(page, radio(page, FIRST))
    assert bound['owner_kind'] == 'ashby-formless-root'
    assert bound['radio_name'] == 'survey-choice'
    assert [m['selector'] for m in bound['radio_members']] == [FIRST, SECOND]
    assert bound['selector'] == FIRST


async def test_granted_decline_passes_exact_approval_and_the_final_check(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    key, grant = grant_of(ask(page, witness, provenance))
    row['human_approved_answers'] = {key: grant}
    row['status'] = 'submitting'
    target = radio(page, SECOND)
    bound = forms._bound_target(page, target)
    forms.exact_approval(PK, key, DECLINE, target=bound, url=page['url'])
    receipt = {'fact': key, 'action': 'choose', 'selector': SECOND, 'url': page['url'],
               'label': DECLINE, 'question': QUESTION, 'approved_value': DECLINE}
    radio(page, SECOND)['checked'] = True
    forms.check_approvals(PK, [receipt], page)


@pytest.mark.parametrize('checked', [{FIRST}, {FIRST, SECOND}, set()])
async def test_final_check_rejects_any_other_selection(monkeypatch, checked):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    key, grant = grant_of(ask(page, witness, provenance))
    row['human_approved_answers'] = {key: grant}
    row['status'] = 'submitting'
    for selector in _CHOICES:
        radio(page, selector)['checked'] = selector in checked
    receipt = {'fact': key, 'action': 'choose', 'selector': SECOND, 'url': page['url'],
               'label': DECLINE, 'question': QUESTION, 'approved_value': DECLINE}
    with pytest.raises(forms.Gate, match='no longer selected'):
        forms.check_approvals(PK, [receipt], page)


async def test_another_member_never_matches_the_decline_answer(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    members = forms._radio_group(page, radio(page, FIRST))
    assert [m['selector'] for m in members
            if forms._radio_answer_matches(DECLINE, m)] == [SECOND]
    assert [m['selector'] for m in members
            if forms._radio_answer_matches('on', m)] == []
    key, grant = grant_of(ask(page, witness, provenance))
    row['human_approved_answers'] = {key: grant}
    row['status'] = 'submitting'
    # The grant names the group, not the value "on": a drifted member set is refused.
    bound = forms._bound_target(page, radio(page, FIRST))
    bound['radio_members'] = bound['radio_members'][:1]
    with pytest.raises(forms.Gate, match='exact human-approved answer'):
        forms.exact_approval(PK, key, DECLINE, target=bound, url=page['url'])


@pytest.mark.parametrize('damage', ['evidence', 'witness-flag', 'witness-label',
                                    'witness-visible'])
@pytest.mark.parametrize('broken', [FIRST, SECOND])
async def test_hidden_radio_without_exact_evidence_is_not_visibly_editable(
        monkeypatch, caplog, damage, broken):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    page, witness = deepcopy(page), deepcopy(witness)
    if damage == 'evidence':
        for e in page['entry']['root_evidence']:
            if e['selector'] == broken:
                e['ashby_hidden_choice'] = False
    elif damage == 'witness-flag':
        witness[broken]['ashby_hidden_choice'] = False
    elif damage == 'witness-label':
        witness[broken]['associated_label_selector'] = '#other'
    else:
        witness[broken]['visible'] = True
        witness[broken]['visible_text'] = 'Different caption'
    with caplog.at_level(logging.INFO, logger=forms._LOG.name):
        with pytest.raises(forms.Gate, match='not visibly editable') as gate:
            ask(page, witness, provenance)
    assert gate.value.reason == 'defect_witness'
    assert gate.value.form_question is None


async def test_granted_decline_is_written_by_selector_and_read_back(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    session = survey_world.session
    key, grant = grant_of(ask(page, witness, provenance))
    row['human_approved_answers'] = {key: grant}
    row['status'] = 'submitting'
    chosen = deepcopy((raw.current, raw.fixed))
    for snapshot in chosen:
        for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
            if c['selector'] == SECOND:
                c['checked'] = True
    raw.after_click = chosen
    filled = {}
    await forms.execute(
        session, page, {'action': 'choose', 'selector': SECOND, 'fact': key,
                        'value': DECLINE},
        facts={}, filled=filled, resume_path='', company=provenance.company, jd_text='',
        resume_tex='', github='', pk=PK, job_url=provenance.wrapper, provenance=provenance)
    assert [c for c in raw.calls if c[0] == 'click'] == [('click', SECOND)]
    assert filled == {SECOND: 'on'}


async def test_write_of_another_member_than_the_grant_is_refused(monkeypatch):
    row, raw, page, witness, provenance = await survey_world(monkeypatch)
    session = survey_world.session
    key, grant = grant_of(ask(page, witness, provenance))
    row['human_approved_answers'] = {key: grant}
    row['status'] = 'submitting'
    with pytest.raises(forms.Gate):
        await forms.execute(
            session, page, {'action': 'choose', 'selector': FIRST, 'fact': key,
                            'value': DECLINE},
            facts={}, filled={}, resume_path='', company=provenance.company, jd_text='',
            resume_tex='', github='', pk=PK, job_url=provenance.wrapper, provenance=provenance)
    assert not [c for c in raw.calls if c[0] == 'click']


async def test_optional_group_is_recognised_left_unanswered_and_never_clicked(monkeypatch):
    # ARCH 20:01: the captured EEOC radios are required=False. Such a group is
    # recognised, needs no answer, and is neither asked nor declined for the owner.
    row, raw, page, witness, provenance = await survey_world(monkeypatch, required=False)
    members = forms._radio_group(page, radio(page, FIRST))
    assert [m['selector'] for m in members] == [FIRST, SECOND]
    assert not any(m['required'] or m['checked'] for m in members)
    question = ask(page, witness, provenance)
    assert question['radio_name'] == 'survey-choice'
    assert not any(forms._unanswered(page, radio(page, s)) for s in _CHOICES)
    row['human_approved_answers'] = {}
    radios = [c for c in page['controls'] if c['selector'] in _CHOICES]
    forms.check_form(dict(page, controls=radios), {}, True)
    forms.check_approvals(PK, [], page)
    assert not [c for c in raw.calls if c[0] in {'click', 'fill', 'select', 'set'}]
    assert not any(radio(page, s)['checked'] for s in _CHOICES)
    assert row['human_approved_answers'] == {}


# Reader proof: the flag exists only for a radio inside the one validated survey.

def _flags(**changes):
    page, fixed = _live('openai', **changes)
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    witness = {c['selector']: c for c in fixed['controls']}
    return ({s: evidence[s].get('ashby_hidden_choice') for s in _CHOICES if s in evidence},
            {s: witness[s].get('ashby_hidden_choice') for s in _CHOICES if s in witness})


def test_hidden_radio_in_the_validated_survey_is_flagged_by_both_readers():
    evidence, witness = _flags()
    assert evidence == {s: True for s in _CHOICES}
    assert witness == {s: True for s in _CHOICES}


@pytest.mark.parametrize('changes', [
    dict(surveyChoices='outside'),
    dict(surveyChoices='label-hidden'),
    dict(surveySecondSection=True),
    dict(surveySubmit=True),
    dict(surveyImplicitSubmit=True),
    dict(surveyExtraContainer=True),
], ids=['outside-survey', 'label-not-visible', 'second-survey', 'survey-submit',
        'survey-implicit-submit', 'second-container-in-survey'])
def test_hidden_radio_outside_a_validated_survey_is_not_flagged(changes):
    evidence, witness = _flags(**changes)
    assert not any(evidence.values()), evidence
    assert not any(witness.values()), witness


def test_page_reader_only_flags_a_radio_inside_the_single_survey_container():
    from tools.browser_skill_dom import PAGE
    assert "e.type==='radio' && pageSurveyWrappers.length===1" in PAGE
    assert "querySelector('.ashby-application-form-container')?.contains(e)" in PAGE
