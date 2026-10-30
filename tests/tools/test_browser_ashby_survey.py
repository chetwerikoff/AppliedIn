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


# Amendment 16:29 and ARCH 17:33: the two public captures' live div#form
# wrapper. 'openai' has the autofill uploader, a blocking disclosure, the
# survey and a link-only paragraph. 'airwallex' has the uploader, the survey
# and a link-only div, with no disclosure. 'pane' keeps the legacy pane class.
# Each main container carries the id-less location combobox whose only caption
# is its field-entry title. All text, labels and links are synthetic; only
# tags, classes and attribute names match the captures.
_LIVE = {
    'openai': dict(liveForm='uploader', survey=True, liveLocation=True, surveyChoices=True),
    'airwallex': dict(liveForm='airwallex', survey=True, liveLocation=True,
                      surveyChoices=True),
    'pane': dict(liveForm='pane', liveLocation=True),
}
_QUARANTINED = {'#autofill-file', '#autofill-button', '#autofill-upload'}
_LOCATION = 'Synthetic location'
_PLACEHOLDER = 'Synthetic placeholder...'
_CHOICES = {'#survey-choice-0', '#survey-choice-1'}


def _live(shape: str, **changes):
    return _observed(dict(root=False, formless=True, url=entry('ashby'),
                          **{**_LIVE[shape], **changes}))


def _provenance():
    from tests.tools.test_browser_application_entry import COMPANY, JOB, PK, posting
    return forms.ApplicationEntryProvenance(
        PK, COMPANY, 'ashby', JOB, posting('ashby'), 'example-co', entry('ashby'))


def _location(snapshot: dict) -> dict:
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
    # One caption rule: the title in every reader, never the placeholder.
    location = _location(page)
    assert (location['label'] == _location(fixed)['label']
            == evidence[location['selector']]['label'] == _LOCATION)
    assert evidence[location['selector']]['associated'] is True
    assert not any(_PLACEHOLDER in str(c.get('label')) for c in page['controls'])
    # The rooted route owns membership: the unchanged boundary passes, and
    # the rootless reversible scope stays empty on a certified root.
    members = {c['selector'] for c in forms._formless_boundary(page, _provenance())}
    assert _MAIN | {location['selector'], '#_systemfield_resume'} <= members
    assert not members & (_QUARANTINED | {'#structural-submit', links[0]})
    assert (_SURVEY in members) is ('survey' in _LIVE[shape])
    # ARCH 17:40: the visually hidden survey radios stay members.
    assert (_CHOICES <= members) is ('surveyChoices' in _LIVE[shape])
    scope, witness = _scope(page, fixed)
    assert scope == frozenset()
    return page, fixed, members, witness


@pytest.mark.parametrize('shape', sorted(_LIVE))
def test_live_div_form_root_passes_unchanged_boundary(shape):
    _live_valid(shape)


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['ENTRY_PAGE', 'FIXED'])
@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_location_caption_is_its_title_in_every_reader(reader, shape):
    spec = dict(root=False, formless=True, url=entry('ashby'), **_LIVE[shape])
    assert _location(dom(spec, reader))['label'] == _LOCATION


def test_standalone_page_location_caption_is_its_title():
    # PAGE does not quarantine the autofill helper, so use the no-pane shape.
    assert _location(dom(_spec(False, liveLocation=True), PAGE))['label'] == _LOCATION


@pytest.mark.parametrize('title', ['second', 'owned'])
def test_location_without_its_own_unique_title_has_no_caption(title):
    page, fixed = _live('openai', liveLocationTitle=title)
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    location = _location(page)
    # Neither reader falls back to the placeholder; both agree on no caption.
    assert location['label'] == _location(fixed)['label'] == ''
    assert evidence[location['selector']]['label'] == ''
    assert evidence[location['selector']]['associated'] is False


def test_live_div_form_survey_gets_no_bank_grant():
    page, _fixed, members, _witness = _live_valid('airwallex', surveyLabel='Email')
    main = next(c for c in page['controls'] if c['selector'] == '#_systemfield_email')
    survey = next(c for c in page['controls'] if c['selector'] == _SURVEY)
    assert main['label'] == survey['label'] == 'Email'
    assert survey['ashby_survey'] is True and 'ashby_survey' not in main
    assert forms._bank_auto_eligible(
        main, 'synthetic@example.test', formless_selectors=frozenset(members))
    assert not forms._bank_auto_eligible(
        survey, 'synthetic@example.test', formless_selectors=frozenset(members))


_FACTS = {'Name': 'Test User', 'Email': 'synthetic@example.test'}


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
async def test_live_entry_bootstrap_grants_main_name_and_email(monkeypatch, shape):
    import asyncio
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    row, raw, _, provenance = root_world(monkeypatch, **_LIVE[shape])
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    writes = _issue182_tracking(monkeypatch, row)
    facts = {**_FACTS, _SURVEY_LABEL: 'Synthetic survey answer'}
    # The survey answer is owner-only: bootstrap asks instead of granting it.
    with pytest.raises(forms.Gate, match='exact human-approved answer is needed'):
        await forms._bootstrap(session, page, PK, facts, posting('ashby'),
                               company=COMPANY, provenance=provenance,
                               global_facts=dict(facts))
    assert len(writes) == 1
    grants = row['human_approved_answers']
    assert set(grants) == set(_FACTS)
    for key, selector in (('Name', '#_systemfield_name'), ('Email', '#_systemfield_email')):
        assert grants[key]['source'] == 'bank-auto'
        assert grants[key]['selector'] == selector
        assert grants[key]['value'] == _FACTS[key]
    no_mutations(raw)


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
async def test_live_controller_bootstrap_takes_the_rooted_route(monkeypatch, shape):
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    row, raw, _, provenance = root_world(monkeypatch, **_LIVE[shape])
    raw._application_controller = object()  # Synthetic producer prerequisite.
    session = forms._ReversibleEntryReader(raw)
    page = await session.page()
    assert forms._formless_root(page)['root'] == '#form'
    writes = _issue182_tracking(monkeypatch, row)
    returned = await forms._bootstrap(
        session, page, PK, dict(_FACTS), posting('ashby'), company=COMPANY,
        provenance=provenance, global_facts=dict(_FACTS))
    assert returned is page
    assert len(writes) == 1
    grants = row['human_approved_answers']
    assert set(grants) == set(_FACTS)
    assert {grants[k]['selector'] for k in _FACTS} == _MAIN
    no_mutations(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', sorted(_LIVE))
async def test_live_div_form_final_submit_reaches_unchanged_guard(monkeypatch, shape):
    from tools import browser_skill as bsk
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting

    page, _fixed, _members, _witness = _live_valid(shape)
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


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_hidden_survey_radio_reports_its_visible_label(shape):
    page, fixed, members, _witness = _live_valid(shape)
    for snapshot in (page, fixed):
        evidence = {e['selector']: e for e in snapshot['entry']['root_evidence']}
        for selector in _CHOICES:
            e = evidence[selector]
            assert e['visible'] is False and e['label_visible'] is True
            assert e['associated'] is True and e['label'].startswith('Synthetic option')
            assert e['in_container'] is True and e['type'] == 'radio'


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_hidden_radio_with_invisible_label_still_refuses(shape):
    _live_valid(shape)
    page, _fixed = _live(shape, surveyChoices='label-hidden')
    assert page['entry']['formless']['root'] == '#form'
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    assert all(evidence[s]['label_visible'] is False for s in _CHOICES)
    with pytest.raises(forms.Gate, match='association or editability changed'):
        forms._formless_boundary(page, _provenance())


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_hidden_radio_outside_container_still_refuses(shape):
    _live_valid(shape)
    page, _fixed = _live(shape, surveyChoices='outside')
    evidence = {e['selector']: e for e in page['entry']['root_evidence']}
    assert all(evidence[s]['in_container'] is False for s in _CHOICES)
    with pytest.raises(forms.Gate):
        forms._formless_boundary(page, _provenance())


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
def test_checked_survey_radio_without_grant_is_refused_at_final_check(shape):
    _live_valid(shape)
    page, _fixed = _live(shape, surveyChoiceChecked=True)
    members = {c['selector']: c for c in forms._formless_boundary(page, _provenance())}
    assert members['#survey-choice-0']['checked'] is True
    # The existing final check, on just the survey radios: no owner grant.
    radios = [c for c in page['controls'] if c['selector'] in _CHOICES]
    with pytest.raises(forms.Gate, match='Unverified preselected answer'):
        forms.check_form(dict(page, controls=radios), {}, True)


async def test_refused_rooted_boundary_prepares_like_main_and_submit_refuses(monkeypatch):
    import asyncio
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    changes = dict(_LIVE['openai'], liveExtraInvisible=True)
    page, _fixed = _live('openai', liveExtraInvisible=True)
    assert page['entry']['formless']['root'] == '#form'
    with pytest.raises(forms.Gate, match='association or editability changed'):
        forms._formless_boundary(page, _provenance())
    row, raw, _, provenance = root_world(monkeypatch, **changes)
    raw._application_controller = object()  # Synthetic producer prerequisite.
    allowed = lambda url: url == entry('ashby')
    scoped = forms._EntrySession(raw, allowed, provenance, resume_path='Resume.pdf')
    session, prepared = await forms._entry_or_reversible_page(
        scoped, raw, allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    # Entry does not gate: the fallback reads the page as main read it.
    assert isinstance(session, forms._ReversibleEntryReader)
    root = forms._formless_root(prepared)
    assert root['root'] == '' and root['error'] == 'application root sibling identity mismatch'
    # Name and email are reversible fill receivers again, as on main.
    _fresh, witness = await forms._observe_controls(session, prepared, site='bootstrap-formless')
    assert _MAIN <= forms._reversible_entry_scope(prepared, witness)
    assert '#extra-invisible' not in forms._reversible_entry_scope(prepared, witness)
    writes = _issue182_tracking(monkeypatch, row)
    returned = await forms._bootstrap(
        session, prepared, PK, dict(_FACTS), posting('ashby'), company=COMPANY,
        provenance=provenance, global_facts=dict(_FACTS))
    assert returned is prepared and len(writes) == 1
    grants = row['human_approved_answers']
    assert {grants[k]['selector'] for k in _FACTS} == _MAIN
    # Final Submit authority is unchanged: the raw rooted page still refuses.
    raw_page = await forms._ReversibleEntryReader(raw).page()
    assert forms._formless_root(raw_page)['root'] == '#form'
    with pytest.raises(forms.Gate):
        forms._formless_boundary(raw_page, provenance)
    no_mutations(raw)


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
def test_live_div_form_negative_shapes_fail_closed(shape, bad):
    _live_valid(shape)
    page, fixed = _live(shape, **bad)
    for snapshot in (page, fixed):
        root = snapshot['entry']['formless']
        assert not (root or {}).get('root')
    with pytest.raises(forms.Gate):
        forms._formless_boundary(page, _provenance())
    scope, _ = _scope(page, fixed)
    assert not scope & _MAIN


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS],
                         ids=['ENTRY_PAGE', 'FIXED'])
def test_application_identity_ignores_one_survey_container(reader):
    spec = dict(root=False, formless=True, url=entry('ashby'), **_LIVE['openai'])
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


# Issue #258: both live captures carry a caption-less picker toggle inside the
# location field entry. It is a boundary member, but it receives no answer.
# The persistence tests leave out the voluntary survey radios: an unanswered
# optional radio group still stops at its own key gate, which is not this fix.
_TOGGLE = dict(liveLocationToggle=True, surveyChoices=False)


async def _entry_bootstrap(monkeypatch, row, raw, provenance, facts):
    import asyncio
    from tests.tools.test_browser_application_entry import COMPANY, PK, posting

    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    return await forms._bootstrap(session, page, PK, facts, posting('ashby'),
                                  company=COMPANY, provenance=provenance,
                                  global_facts=dict(facts))


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
async def test_location_grant_binds_on_the_next_run_past_its_toggle(monkeypatch, shape):
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    row, raw, _, provenance = root_world(monkeypatch, **{**_LIVE[shape], **_TOGGLE})
    toggle = [c for c in raw.current['controls'] if c.get('tag') == 'button'
              and c['label'] == '' and c.get('question') == _LOCATION]
    assert len(toggle) == 1
    members = {c['selector'] for c in forms._formless_boundary(raw.current, _provenance())}
    assert toggle[0]['selector'] in members
    writes = _issue182_tracking(monkeypatch, row)
    facts = {**_FACTS, _LOCATION: 'Synthetic City'}
    # Run 1 lacks one bank answer and stops at its question.
    with pytest.raises(forms.Gate, match='exact human-approved answer is needed for "Name"'):
        await _entry_bootstrap(monkeypatch, row, raw, provenance,
                               {k: v for k, v in facts.items() if k != 'Name'})
    assert set(row['human_approved_answers']) == {'Email', _LOCATION}
    # Run 2 on the unchanged page: the stored grants bind, the toggle borrows
    # no key, and preparation continues without a "Previously approved" gate.
    returned = await _entry_bootstrap(monkeypatch, row, raw, provenance, facts)
    assert returned['url'] == entry('ashby')
    assert set(row['human_approved_answers']) == {'Name', 'Email', _LOCATION}
    assert len(writes) == 2
    no_mutations(raw)


@pytest.mark.parametrize('shape', ['openai', 'airwallex'])
async def test_location_grant_binds_in_the_same_run_past_its_toggle(monkeypatch, shape):
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    row, raw, _, provenance = root_world(monkeypatch, **{**_LIVE[shape], **_TOGGLE})
    writes = _issue182_tracking(monkeypatch, row)
    facts = {**_FACTS, _LOCATION: 'Synthetic City'}
    returned = await _entry_bootstrap(monkeypatch, row, raw, provenance, facts)
    assert returned['url'] == entry('ashby')
    assert set(row['human_approved_answers']) == {'Name', 'Email', _LOCATION}
    assert len(writes) == 1
    no_mutations(raw)


# Issue #258 AC3: the OpenAI capture's two acknowledgement checkboxes. PAGE
# reads them as value "on", unchecked; only the fixed witness sees them hidden.
_ATTESTATIONS = dict(_TOGGLE, liveAttestations=True)


def _attestation_readings(page: dict, fixed: dict):
    assert page['entry']['formless']['root'] == '#form'
    for selector in ('#attestation-0', '#attestation-1'):
        observed = next(c for c in page['controls'] if c['selector'] == selector)
        hidden = next(c for c in fixed['controls'] if c['selector'] == selector)
        assert observed['value'] == 'on' and observed['has_value'] is True
        assert 'ashby_hidden_choice' not in observed
        assert hidden['ashby_hidden_choice'] is True and hidden['visible'] is False
    members = {c['selector'] for c in forms._formless_boundary(page, _provenance())}
    assert {'#attestation-0', '#attestation-1'} <= members


@pytest.mark.parametrize('link', [True, False], ids=['agreement-link', 'no-link'])
async def test_openai_attestation_reaches_its_owner_question(monkeypatch, link):
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    changes = {**_LIVE['openai'], **_ATTESTATIONS,
               **({} if link else {'liveAttestations': 'no-link'})}
    row, raw, _, provenance = root_world(monkeypatch, **changes)
    _attestation_readings(raw.current, raw.fixed)
    _issue182_tracking(monkeypatch, row)
    facts = {**_FACTS, _LOCATION: 'Synthetic City'}
    for _run in range(2):
        # Not a "Previously approved" dead end, in the first run or the next.
        with pytest.raises(forms.Gate, match='exact human-approved answer is needed') as gate:
            await _entry_bootstrap(monkeypatch, row, raw, provenance, facts)
        assert 'Previously approved' not in str(gate.value)
        assert gate.value.reason == 'owner_question'
        question = gate.value.form_question
        assert question['selector'] == '#attestation-0'
        assert question['type'] == 'checkbox' and question['ashby_hidden_choice'] is True
        assert question['visible'] is False
    assert set(row['human_approved_answers']) == {'Name', 'Email', _LOCATION}
    # Nothing is checked: the attestation waits for the owner's exact answer.
    no_mutations(raw)


async def test_checked_attestation_without_grant_is_asked_and_refused_at_submit(monkeypatch):
    from tests.tools.test_browser_formless import _issue182_tracking, no_mutations, root_world

    row, raw, _, provenance = root_world(
        monkeypatch, **{**_LIVE['openai'], **_ATTESTATIONS, 'attestationChecked': 1})
    _issue182_tracking(monkeypatch, row)
    facts = {**_FACTS, _LOCATION: 'Synthetic City'}
    with pytest.raises(forms.Gate) as gate:
        await _entry_bootstrap(monkeypatch, row, raw, provenance, facts)
    assert 'Previously approved' not in str(gate.value)
    assert gate.value.form_question['selector'] == '#attestation-0'
    # The existing final check still refuses the pre-checked, ungranted box.
    boxes = [c for c in raw.current['controls'] if c['selector'] == '#attestation-1']
    assert boxes[0]['checked'] is True
    with pytest.raises(forms.Gate, match='Unverified preselected answer'):
        forms.check_form(dict(raw.current, controls=boxes), {}, True)
    no_mutations(raw)


def test_link_and_toggle_never_collide_but_real_receivers_still_refuse():
    page, _fixed = _live('openai', **_ATTESTATIONS)
    box = next(c for c in page['controls'] if c['selector'] == '#attestation-0')
    link = next(c for c in page['controls'] if c['tag'] == 'a'
                and c.get('question') == box['question'])
    toggle = next(c for c in page['controls'] if c.get('tag') == 'button'
                  and c['label'] == '' and c.get('question') == _LOCATION)
    assert forms._key(page, box) == box['question']
    assert not forms._approval_question_collisions(page, box)
    location = _location(page)
    assert forms._key(page, location) == _LOCATION
    assert toggle not in forms._approval_question_collisions(page, location)
    # A real receiver carrying the same question still refuses at _key.
    twin = dict(box, selector='#attestation-twin', tag='input', type='text', label='Twin')
    with pytest.raises(forms.Gate, match='More than one control uses the same approval question'):
        forms._key(dict(page, controls=[*page['controls'], twin]), box)
    assert link['tag'] == 'a'


def test_disabled_unchecked_choice_is_not_a_filled_control_at_submit():
    page, _fixed = _live('openai', **_ATTESTATIONS)
    box = dict(next(c for c in page['controls'] if c['selector'] == '#attestation-1'),
               disabled=True)
    assert box['has_value'] is True and box['checked'] is False
    forms.check_form(dict(page, controls=[box]), {}, True)
    with pytest.raises(forms.Gate, match='disabled at final Submit'):
        forms.check_form(dict(page, controls=[dict(box, checked=True)]), {}, True)
