"""Synthetic Ashby survey/formless inventory and final-root boundaries.

This is a readback and application-identity suite. Per-field grants and
reversible-scope certificates were deleted by Issue #293; the only substantive
decision belongs to the code-owned final pre-Submit check.
"""
import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill_dom import PAGE
from tests.tools.test_browser_application_entry import dom, entry

_MAIN = {'#_systemfield_name', '#_systemfield_email'}
_SURVEY = '#survey-preference'
_SURVEY_LABEL = 'Synthetic voluntary survey question'
_QUARANTINED = {'#autofill-file', '#autofill-button', '#autofill-upload'}
_LOCATION = 'Synthetic location'
_PLACEHOLDER = 'Synthetic placeholder...'
_CHOICES = {'#survey-choice-0', '#survey-choice-1'}


def _spec(with_pane: bool, **changes) -> dict:
    return dict(root=False, formless=True, survey=True, missingPane=not with_pane,
                url=entry('ashby'), **changes)


def _observed(spec: dict):
    return dom(spec, forms.ENTRY_PAGE), dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)


def _provenance():
    from tests.tools.test_browser_application_entry import COMPANY, JOB, PK, posting
    return forms.ApplicationEntryProvenance(
        PK, COMPANY, 'ashby', JOB, posting('ashby'), 'example-co', entry('ashby'))


def _valid(with_pane: bool):
    page, fixed = _observed(_spec(with_pane))
    for snapshot in (page, fixed):
        root = snapshot['entry']['formless']
        assert root['root'] == '' and root['container'] and root['submit']
        assert root['error'] == ''
        assert bool(root['pane']) is with_pane
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        assert _MAIN | {_SURVEY} <= evidence.keys()
        assert all(evidence[s]['in_container'] is True for s in _MAIN | {_SURVEY})
        assert evidence[_SURVEY]['associated'] is True
        assert evidence[_SURVEY]['label'] == _SURVEY_LABEL
        assert evidence[_SURVEY]['ashby_survey'] is True
        assert 'ashby_survey' not in evidence['#_systemfield_email']
        assert not evidence.keys() & _QUARANTINED
    return page, fixed


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_survey_sibling_retains_main_and_survey_readback(with_pane):
    _valid(with_pane)


@pytest.mark.parametrize('reader', [PAGE, forms.ENTRY_PAGE,
                                   forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['PAGE', 'ENTRY_PAGE', 'FIXED'])
def test_survey_census_does_not_hide_main_labelled_checkbox(reader):
    snapshot = dom(_spec(False, hiddenAttestation=True), reader)
    choices = [c for c in snapshot['controls'] if c['selector'] == '#hidden-attestation']
    assert len(choices) == 1
    assert choices[0]['ashby_hidden_choice'] is True
    assert choices[0]['visible'] is False
    assert choices[0]['associated_label_selector'] == '#hidden-attestation-label'


def _ambiguous(with_pane: bool, **bad):
    _valid(with_pane)
    page, fixed = _observed(_spec(with_pane, **bad))
    for snapshot in (page, fixed):
        root = snapshot['entry']['formless']
        assert root['root'] == '' and root['error'] == 'ambiguous application roots'
    with pytest.raises(forms.Gate):
        forms._formless_boundary(page, _provenance())


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
@pytest.mark.parametrize('changes', [
    {'surveySecondSection': True}, {'surveySubmit': True},
    {'surveyImplicitSubmit': True}, {'surveyOutside': True},
    {'surveyExtraContainer': True},
])
def test_ambiguous_survey_cannot_supply_final_application_root(with_pane, changes):
    _ambiguous(with_pane, **changes)


_LIVE = {
    'openai': dict(liveForm='uploader', survey=True, liveLocation=True,
                   surveyChoices=True),
    'airwallex': dict(liveForm='airwallex', survey=True, liveLocation=True,
                     surveyChoices=True),
    'pane': dict(liveForm='pane', liveLocation=True),
}


def _live(shape: str, **changes):
    return _observed(dict(root=False, formless=True, url=entry('ashby'),
                          **{**_LIVE[shape], **changes}))


def _location(snapshot: dict):
    found = [c for c in snapshot['controls'] if c['type'] == 'combobox']
    assert len(found) == 1
    return found[0]


def _live_valid(shape: str, **changes):
    page, fixed = _live(shape, **changes)
    assert page['entry'] == fixed['entry']
    root = page['entry']['formless']
    assert root['root'] == '#form' and root['error'] == ''
    assert root['container'] == '#application-fields'
    assert root['submit'] == '#structural-submit' and root['pane'] == '#autofill'
    assert _QUARANTINED <= set(root['excluded'])
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    assert not evidence.keys() & _QUARANTINED
    links = [c['selector'] for c in page['controls'] if c['tag'] == 'a']
    assert len(links) == 1 and links[0] not in evidence
    location = _location(page)
    assert (location['label'] == _location(fixed)['label']
            == evidence[location['selector']]['label'] == _LOCATION)
    assert evidence[location['selector']]['associated'] is True
    assert not any(_PLACEHOLDER in str(c.get('label')) for c in page['controls'])
    members = {c['selector'] for c in forms._formless_boundary(page, _provenance())}
    assert _MAIN | {location['selector'], '#_systemfield_resume'} <= members
    assert not members & (_QUARANTINED | {'#structural-submit', *links})
    assert (_SURVEY in members) is ('survey' in _LIVE[shape])
    assert (_CHOICES <= members) is ('surveyChoices' in _LIVE[shape])
    return page, fixed, members, evidence


@pytest.mark.parametrize('shape', sorted(_LIVE))
def test_live_div_form_root_excludes_helper_login_and_non_answer_nodes(shape):
    _live_valid(shape)


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['ENTRY_PAGE', 'FIXED'])
@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_location_caption_comes_from_its_title_not_placeholder(reader, shape):
    spec = dict(root=False, formless=True, url=entry('ashby'), **_LIVE[shape])
    assert _location(dom(spec, reader))['label'] == _LOCATION


def test_standalone_page_location_caption_is_its_title():
    assert _location(dom(_spec(False, liveLocation=True), PAGE))['label'] == _LOCATION


@pytest.mark.parametrize('title', ['second', 'owned'])
def test_location_without_unique_title_has_no_caption(title):
    page, fixed = _live('openai', liveLocationTitle=title)
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    location = _location(page)
    assert location['label'] == _location(fixed)['label'] == ''
    assert evidence[location['selector']]['label'] == ''
    assert evidence[location['selector']]['associated'] is False


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_hidden_survey_radio_remains_accessible_and_readable(shape):
    page, fixed, members, _ = _live_valid(shape)
    assert _CHOICES <= members
    for snapshot in (page, fixed):
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        for selector in _CHOICES:
            item = evidence[selector]
            assert item['visible'] is False and item['label_visible'] is True
            assert item['associated'] is True
            assert item['label'].startswith('Synthetic option')
            assert item['in_container'] is True and item['type'] == 'radio'


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_hidden_survey_radio_invisible_caption_is_reported(shape):
    page, fixed = _live(shape, surveyChoices='label-hidden')
    for snapshot in (page, fixed):
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        assert all(evidence[s]['label_visible'] is False for s in _CHOICES)


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_survey_radio_outside_application_container_is_not_an_answer(shape):
    page, fixed = _live(shape, surveyChoices='outside')
    for snapshot in (page, fixed):
        evidence = {e['selector'] for e in snapshot['entry']['root_evidence']}
        assert not evidence & _CHOICES


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_prechecked_survey_choice_is_not_a_grant_refusal(shape):
    page, _fixed = _live(shape, surveyChoiceChecked=True)
    members = {c['selector']: c for c in forms._formless_boundary(page, _provenance())}
    assert members['#survey-choice-0']['checked'] is True


@pytest.mark.parametrize('shape,bad', [
    ('openai', {'liveExtraSubmit': True}),
    ('airwallex', {'liveExtraSubmit': True}),
    ('pane', {'liveExtraSubmit': True}),
    ('openai', {'liveSecondUploader': True}),
    ('airwallex', {'liveSecondUploader': True}),
    ('openai', {'liveLinkExtra': 'button'}),
    ('airwallex', {'liveLinkExtra': 'button'}),
    ('airwallex', {'liveLinkExtra': 'form'}),
    ('pane', {'liveLinkExtra': 'button'}),
    ('pane', {'liveLinkExtra': 'form'}),
    ('openai', {'liveLinkExtra': 'nested-button'}),
    ('openai', {'surveySecondSection': True}),
    ('airwallex', {'surveySecondSection': True}),
    ('openai', {'surveySubmit': True}),
    ('openai', {'surveyImplicitSubmit': True}),
    ('airwallex', {'surveyImplicitSubmit': True}),
    ('openai', {'surveyOutside': True}),
    ('openai', {'surveyExtraContainer': True}),
    ('airwallex', {'surveyExtraContainer': True}),
], ids=lambda v: v if isinstance(v, str) else '-'.join(map(str, v.items())))
def test_bad_live_container_fails_final_root_check(shape, bad):
    _live_valid(shape)
    page, fixed = _live(shape, **bad)
    for snapshot in (page, fixed):
        assert not (snapshot['entry']['formless'] or {}).get('root')
    with pytest.raises(forms.Gate):
        forms._formless_boundary(page, _provenance())


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['ENTRY_PAGE', 'FIXED'])
def test_application_identity_ignores_one_survey_container(reader):
    spec = dict(root=False, formless=True, url=entry('ashby'), **_LIVE['openai'])
    assert [r['selector'] for r in dom(spec, reader)['application_identity']] == [
        '#application-fields']
    spec['surveyExtraContainer'] = True
    assert dom(spec, reader)['application_identity'] == []


def test_standalone_page_identity_ignores_one_survey_container():
    assert [r['selector'] for r in dom(_spec(False), PAGE)['application_identity']] == [
        '#application-fields']
    assert dom(_spec(False, surveyExtraContainer=True), PAGE)['application_identity'] == []


def test_location_toggle_and_attestations_remain_distinct_readback_controls():
    page, fixed = _live('openai', liveLocationToggle=True, liveAttestations=True,
                        surveyChoices=False)
    toggle = [c for c in page['controls'] if c.get('tag') == 'button'
              and c.get('label') == '' and c.get('question') == _LOCATION]
    assert len(toggle) == 1
    for snapshot in (page, fixed):
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        for selector in ('#attestation-0', '#attestation-1'):
            c = next(c for c in snapshot['controls'] if c['selector'] == selector)
            assert c['type'] == 'checkbox' and c['value'] == 'on'
            assert isinstance(c.get('checked'), bool)
            assert selector in evidence
    members = {c['selector'] for c in forms._formless_boundary(page, _provenance())}
    assert toggle[0]['selector'] in members
    assert '#attestation-0' in members and '#attestation-1' in members
