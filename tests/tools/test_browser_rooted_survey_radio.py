"""Synthetic Ashby hidden-choice census on the surviving DOM readers.

Old grant/witness authorization checks are gone; these tests preserve which
native radios and checkboxes the readback actually reports.
"""
import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_ashby_survey import _CHOICES, _live


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


# ARCH 20:45 narrowing: main-container admission stays checkbox-only.

def _main_flags(**changes):
    from tests.tools.test_browser_application_entry import dom, entry
    out = {}
    for name, reader in (('ENTRY', forms.ENTRY_PAGE),
                         ('FIXED', forms._ENTRY_FIXED_CONTROL_WITNESS)):
        snapshot = dom(dict(root=False, formless=True, url=entry('ashby'), **changes), reader)
        out[name] = {c['selector']: c.get('ashby_hidden_choice')
                     for c in snapshot['controls']
                     if c['selector'].startswith(('#main-choice', '#hidden-attestation'))}
        if name == 'ENTRY':
            out['EVIDENCE'] = {e['selector']: e.get('ashby_hidden_choice')
                               for e in snapshot['entry']['root_evidence']
                               if e['selector'].startswith(('#main-choice', '#hidden-attestation'))}
    return out


@pytest.mark.parametrize('survey', [False, True], ids=['no-survey', 'with-survey'])
def test_hidden_radio_in_the_main_container_is_not_flagged(survey):
    flags = _main_flags(mainRadio=True, **(dict(survey=True) if survey else {}))
    for reader in ('ENTRY', 'FIXED', 'EVIDENCE'):
        assert {s for s in flags[reader] if s.startswith('#main-choice')} == {
            '#main-choice-0', '#main-choice-1'}, (reader, flags)
        assert not any(v for s, v in flags[reader].items()
                       if s.startswith('#main-choice')), (reader, flags)


def test_hidden_main_container_checkbox_is_still_flagged():
    flags = _main_flags(hiddenAttestation=True)
    assert flags['FIXED']['#hidden-attestation'] is True
    assert flags['EVIDENCE']['#hidden-attestation'] is True
