"""Issue #252: the Ashby voluntary survey shares one reversible application root.

Exercise the shipped PAGE, ENTRY_PAGE and independent fixed witness on a
synthetic copy of the public-safe sibling/container DOM shape. No browser,
network, real application or operator evidence is used.
"""
import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill_dom import PAGE
from tests.tools.test_browser_application_entry import dom, entry


_MAIN = {'#_systemfield_name', '#_systemfield_email'}
_SURVEY = '#survey-preference'
_SURVEY_LABEL = 'Synthetic voluntary survey question'


def _spec(with_pane: bool, **changes) -> dict:
    return dict(root=False, formless=True, survey=True,
                missingPane=not with_pane, url=entry('ashby'),
                **changes)


def _observed(spec: dict):
    return (dom(spec, forms.ENTRY_PAGE),
            dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS))


def _scope(page: dict, fixed: dict):
    witness = {c['selector']: c for c in fixed['controls']}
    return forms._reversible_entry_scope(page, witness), witness


def _valid(with_pane: bool):
    page, fixed = _observed(_spec(with_pane))
    expected_error = ''
    for snapshot in (page, fixed):
        root = snapshot['entry']['formless']
        assert root['root'] == '' and root['container'] and root['submit']
        assert root['error'] == expected_error
        assert bool(root['pane']) is with_pane
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        assert _MAIN | {_SURVEY} <= evidence.keys()
        assert all(evidence[selector]['in_container'] is True
                   for selector in _MAIN | {_SURVEY})
        assert evidence[_SURVEY]['associated'] is True
        assert evidence[_SURVEY]['label'] == _SURVEY_LABEL
        assert evidence[_SURVEY]['ashby_survey'] is True
        assert 'ashby_survey' not in evidence['#_systemfield_email']
        assert next(c for c in snapshot['controls'] if c['selector'] == _SURVEY)[
            'ashby_survey'] is True
        assert evidence[_SURVEY]['native_owner'] == ''
        assert evidence[_SURVEY]['declared_owner'] == ''
    scope, witness = _scope(page, fixed)
    assert _MAIN | {_SURVEY} <= scope
    assert '#structural-submit' not in scope
    for target in page['controls']:
        if target['selector'] in _MAIN | {_SURVEY}:
            # Each survey receiver has its own independent PAGE/ENTRY/witness
            # identity; a sibling wrapper alone grants no bank value.
            forms._reversible_entry_recipient(page, target, witness)
    return page, fixed


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_survey_sibling_preserves_main_and_individual_survey_scope(with_pane):
    _valid(with_pane)


@pytest.mark.parametrize('reader', [PAGE, forms.ENTRY_PAGE,
                                    forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['PAGE', 'ENTRY_PAGE', 'FIXED'])
def test_survey_census_does_not_hide_main_labelled_checkbox(reader):
    snapshot = dom(_spec(False, hiddenAttestation=True), reader)
    choices = [c for c in snapshot['controls']
               if c['selector'] == '#hidden-attestation']
    assert len(choices) == 1
    assert choices[0]['ashby_hidden_choice'] is True
    assert choices[0]['visible'] is False
    assert choices[0]['associated_label_selector'] == '#hidden-attestation-label'


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_survey_email_collision_is_not_auto_granted(with_pane):
    # A voluntary survey question can carry the same label as a globally
    # approved fact. Receiver scope does not make it eligible for bank-auto.
    page, fixed = _observed(_spec(with_pane, surveyLabel='Email'))
    scope, witness = _scope(page, fixed)
    assert _MAIN | {_SURVEY} <= scope
    main = next(c for c in page['controls']
                if c['selector'] == '#_systemfield_email')
    survey = next(c for c in page['controls']
                  if c['selector'] == _SURVEY)
    assert main['label'] == survey['label'] == 'Email'
    forms._reversible_entry_recipient(page, survey, witness)
    assert forms._bank_auto_eligible(
        main, 'synthetic@example.test', formless_selectors=scope)
    assert not forms._bank_auto_eligible(
        survey, 'synthetic@example.test', formless_selectors=scope)


def _refused(with_pane: bool, **bad):
    # The positive control makes each negative regression fail on main through
    # live reader behavior, not a new string-constant or marker assertion.
    _valid(with_pane)
    page, fixed = _observed(_spec(with_pane, **bad))
    for snapshot in (page, fixed):
        structural = snapshot['entry']['formless']
        assert structural['root'] == ''
        assert structural['error'] == 'ambiguous application roots'
    scope, _ = _scope(page, fixed)
    assert not scope


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_two_survey_sections_fail_closed(with_pane):
    _refused(with_pane, surveySecondSection=True)


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_explicit_survey_submit_button_fails_closed(with_pane):
    _refused(with_pane, surveySubmit=True)


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_implicit_survey_submit_button_fails_closed(with_pane):
    _refused(with_pane, surveyImplicitSubmit=True)


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_survey_outside_application_wrapper_fails_closed(with_pane):
    _refused(with_pane, surveyOutside=True)


@pytest.mark.parametrize('with_pane', [True, False], ids=['pane', 'no-pane'])
def test_two_containers_inside_survey_fail_closed(with_pane):
    _refused(with_pane, surveyExtraContainer=True)


# Amendment 16:29: the two public captures' live div#form wrapper. One shape
# has the autofill uploader, a blocking disclosure, the survey and a link-only
# paragraph; the other has the legacy pane and a link-only div. All text,
# labels and links are synthetic; only tags, classes and attribute names match.
_LIVE = {
    'uploader': dict(liveForm='uploader', survey=True),
    'pane': dict(liveForm='pane'),
}
_QUARANTINED = {'#autofill-file', '#autofill-button', '#autofill-upload'}


def _live(shape: str, **changes):
    return _observed(dict(root=False, formless=True, url=entry('ashby'),
                          **_LIVE[shape], **changes))


def _live_valid(shape: str, **changes):
    page, fixed = _live(shape, **changes)
    assert page['entry'] == fixed['entry']
    root = page['entry']['formless']
    assert root['root'] == '#form' and root['error'] == ''
    assert root['container'] == '#application-fields'
    assert root['submit'] == '#structural-submit' and root['pane'] == '#autofill'
    assert _QUARANTINED <= set(root['excluded'])
    evidence = {e['selector'] for e in page['entry']['root_evidence']}
    assert not evidence & _QUARANTINED
    links = [c['selector'] for c in page['controls'] if c['tag'] == 'a']
    assert len(links) == 1 and links[0] not in evidence
    scope, witness = _scope(page, fixed)
    assert _MAIN <= scope
    assert not scope & (_QUARANTINED | {'#structural-submit', links[0]})
    assert (_SURVEY in scope) is (shape == 'uploader')
    return page, fixed, scope, witness


@pytest.mark.parametrize('shape', sorted(_LIVE))
def test_live_div_form_wrapper_admits_structural_root(shape):
    _live_valid(shape)


def test_live_div_form_survey_gets_no_bank_grant():
    page, _fixed, scope, witness = _live_valid('uploader', surveyLabel='Email')
    main = next(c for c in page['controls'] if c['selector'] == '#_systemfield_email')
    survey = next(c for c in page['controls'] if c['selector'] == _SURVEY)
    assert main['label'] == survey['label'] == 'Email'
    assert survey['ashby_survey'] is True and 'ashby_survey' not in main
    forms._reversible_entry_recipient(page, survey, witness)
    assert forms._bank_auto_eligible(
        main, 'synthetic@example.test', formless_selectors=scope)
    assert not forms._bank_auto_eligible(
        survey, 'synthetic@example.test', formless_selectors=scope)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', sorted(_LIVE))
async def test_live_div_form_final_submit_reaches_unchanged_guard(monkeypatch, shape):
    from tools import browser_skill as bsk
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting

    page, _fixed, _scope_, _witness = _live_valid(shape)
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), '', {}, {}, [], {'url': ''}, lambda: None)
    calls, reached = [], []
    guard = controller.authorize_final_submit

    async def fake_command(*argv, **kwargs):
        calls.append(argv)
        return {}

    async def fake_page(_session):
        return page

    async def observed(session, current, target):
        reached.append(target.get('selector'))
        return await guard(session, current, target)

    dispatched = []

    def refuse_dispatch(pk, _resume_path, **_kwargs):
        # Offline: the unchanged guard's first store-backed check refuses here.
        dispatched.append(pk)
        raise forms.Gate('synthetic unqueued row')

    monkeypatch.setattr(forms, 'check_dispatch', refuse_dispatch)
    monkeypatch.setattr(bsk, 'command', fake_command)
    monkeypatch.setattr(controller, '_fresh_preparation', lambda _url, **_kwargs: None)
    monkeypatch.setattr(controller, '_page', fake_page)
    monkeypatch.setattr(controller, 'authorize_final_submit', observed)
    session = bsk.Session('apply')
    session.id = 'synthetic-session'
    session.bind_application_controller(controller)
    with pytest.raises(forms.Gate, match='synthetic unqueued row'):
        await session.application_effect('click', '#structural-submit')
    assert reached == ['#structural-submit'] and dispatched == [PK]
    assert not any(argv[:1] == ('click',) for argv in calls)


@pytest.mark.parametrize('shape,bad', [
    ('uploader', {'liveExtraSubmit': True}),
    ('pane', {'liveExtraSubmit': True}),
    ('uploader', {'liveSecondUploader': True}),
    ('uploader', {'liveLinkExtra': 'button'}),
    ('pane', {'liveLinkExtra': 'button'}),
    ('pane', {'liveLinkExtra': 'form'}),
    ('uploader', {'liveLinkExtra': 'nested-button'}),
    ('uploader', {'surveySecondSection': True}),
    ('uploader', {'surveySubmit': True}),
    ('uploader', {'surveyImplicitSubmit': True}),
    ('uploader', {'surveyOutside': True}),
    ('uploader', {'surveyExtraContainer': True}),
], ids=lambda v: v if isinstance(v, str) else '-'.join(map(str, v.items())))
def test_live_div_form_negative_shapes_fail_closed(shape, bad):
    _live_valid(shape)
    page, fixed = _live(shape, **bad)
    for snapshot in (page, fixed):
        root = snapshot['entry']['formless']
        assert not (root or {}).get('root')
    scope, _ = _scope(page, fixed)
    assert not scope & _MAIN


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['ENTRY_PAGE', 'FIXED'])
def test_application_identity_ignores_one_survey_container(reader):
    spec = dict(root=False, formless=True, url=entry('ashby'), **_LIVE['uploader'])
    assert [r['selector'] for r in dom(spec, reader)['application_identity']] == [
        '#application-fields']
    spec['surveyExtraContainer'] = True
    assert dom(spec, reader)['application_identity'] == []


def test_standalone_page_identity_ignores_one_survey_container():
    # PAGE does not quarantine the autofill helper, so use the no-pane shape.
    spec = _spec(False)
    assert [r['selector'] for r in dom(spec, PAGE)['application_identity']] == [
        '#application-fields']
    assert dom(_spec(False, surveyExtraContainer=True), PAGE)['application_identity'] == []
