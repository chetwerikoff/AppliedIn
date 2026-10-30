"""Independent generic accessible-control readers on offline synthetic DOM nodes.

Opacity, geometry, labels, required proxies and ARIA relationships remain
observable where the current readers expose them. Obsolete per-step graph
certificates grant neither a write nor the final application Submit.
"""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_closed_widgets import Session, choose, dom, grants


def instrument(script):
    return script.replace('const pickerParts = new Set();', '''
      global.__graphs.push({graphs:Array.from(newGraphs,([e,g])=>[selector(e),g]),
        proxies:Array.from(requiredProxies,selector), quarantined:Array.from(quarantined,selector)});
      const pickerParts = new Set();''')


def observe(kind='greenhouse', **scenario):
    # Run the two real production readers; do not fabricate old graph captures.
    readers = ((forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS) if kind == 'entry'
               else (forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS))
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(scenario), *readers], text=True))


async def assert_unapproved_submit_is_refused(snapshot, monkeypatch, tmp_path, *,
                                              require_incomplete=True, reason=''):
    """Use the production application raw-click guard, not a test-only classifier.

    A loopback-only fixture is copied onto a synthetic HTTPS tracked row before
    final-origin validation. All form fields, control opacity, required values
    and ambiguous picker relationships stay exactly as the readers observed.
    """
    from tools import browser_skill as bsk

    page = copy.deepcopy(snapshot['page'])
    if require_incomplete:
        assert page['opaque_controls'] is True
        assert page['inventory_verified'] is False
    elif reason:
        # Issue 256: a complete Greenhouse inventory (the unrendered .iti
        # picker admitted as one phone receiver) must still stop at the one
        # final guard for the named per-field reason, not an opaque gate.
        assert page['opaque_controls'] is False
        assert page['inventory_verified'] is True
    else:
        # Generic accessible preparation can keep the inventory complete.
        # Even an apparently settled choice has no Submit permission without
        # its current form-own approved value and write receipts.
        assert isinstance(page['opaque_controls'], bool)
        assert isinstance(page['inventory_verified'], bool)
    trusted = 'https://example-co.test/job/1'
    source = page['url']
    if source != trusted:
        page['url'] = trusted
        for target in page['controls']:
            if target.get('form_action'):
                assert target['form_action'] == source
                target['form_action'] = trusted
        for channel in ('entry', 'greenhouse'):
            root = (page.get(channel) or {}).get('root')
            if root and root.get('action') == source:
                root['action'] = trusted

    pdf = tmp_path / 'Test User Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\nSynthetic-only example-co row document\n')
    attachments = [c for c in page['controls'] if c['selector'] == '#resume']
    buttons = [c for c in page['controls'] if c['selector'] == '#submit']
    assert len(attachments) == len(buttons) == 1
    assert attachments[0]['type'] == 'file'
    attachments[0]['files'] = [pdf.name]
    assert buttons[0]['type'] == 'submit' and buttons[0]['in_form'] is True
    assert buttons[0]['form_action'] == trusted
    # Establish the exact production form-level reason first; the actual raw
    # Submit seam must reach this same refusal rather than an unrelated fake.
    with pytest.raises(forms.Gate) as form_denial:
        forms.check_form(page, {}, True, pdf.name)
    if require_incomplete:
        assert 'form inventory is incomplete' in str(form_denial.value)
    elif reason:
        assert str(form_denial.value).startswith(reason)
    # Both synthetic DOM families have a fixed independent heading. Keep
    # the literal PAGE title through the HTTPS rebind; the final guard must
    # compare against that same exact observed row title, not a fuzzy alias.
    assert page['title'] == snapshot['page']['title']
    assert page['title'] in {'Synthetic', 'Synthetic role at example-co'}

    row = {'pk': 'example-co#1', 'company': 'example-co', 'jd_url': trusted,
           'job_id': '1', 'title': page['title'], 'ats': 'custom',
           'status': 'submitting', 'possible_submission': False}
    raw_ipc, started = [], []
    controller = forms.ApplicationIPCController(
        row['pk'], row['company'], trusted, str(pdf), {}, {}, [], {},
        lambda: started.append(True))
    monkeypatch.setattr(controller, '_row', lambda: row)
    # The PDF is present and its identity is stable: only the missing/current
    # form evidence should stop this attempt, not an absent attachment.
    pdf_identity = (pdf.stat().st_size, controller.expected_digest)
    monkeypatch.setattr(controller, '_row_pdf', lambda: pdf_identity)
    controller.upload_identity = pdf_identity
    controller.upload_completed = True

    def check_dispatch(pk, resume_path, **kwargs):
        assert pk == row['pk'] and resume_path == str(pdf.resolve())
        assert not kwargs

    async def read_page(_session):
        return copy.deepcopy(page)

    async def fake_command(*argv, **kwargs):
        raw_ipc.append((argv, kwargs))
        return {}

    monkeypatch.setattr(forms, 'check_dispatch', check_dispatch)
    monkeypatch.setattr(controller, '_fresh_preparation',
                        lambda current: None if current == trusted else
                        pytest.fail('An unexpected origin reached preparation'))
    monkeypatch.setattr(controller, '_page', read_page)
    monkeypatch.setattr(forms, 'hold_possible_submission',
                        lambda *_a, **_kw: pytest.fail(
                            'Incomplete form reached the irreversible hold'))
    monkeypatch.setattr(bsk, 'command', fake_command)
    session = bsk.Session('apply')
    session.id = 'synthetic-offline-session'
    session.bind_application_controller(controller)

    with pytest.raises(forms.Gate) as final_denial:
        await session.application_effect('click', '#submit')
    assert str(final_denial.value) == str(form_denial.value)
    assert raw_ipc == [] and started == []
    assert controller.committed is False
    assert row['status'] == 'submitting' and row['possible_submission'] is False
    assert controller.upload_completed is True


RECT = dict(width=120, height=24, right=120, bottom=24)
CONTRASTS = [
    ('own-opacity', {'style': {'opacity': '0'}}),
    ('ancestor-opacity', {'parentStyle': {'opacity': '0'}}),
    ('own-display', {'style': {'display': 'none'}}),
    ('ancestor-display', {'parentStyle': {'display': 'none'}}),
    ('own-collapse', {'style': {'visibility': 'collapse'}}),
    ('ancestor-collapse', {'parentStyle': {'visibility': 'collapse'}}),
    ('visible-descendant', {'parentStyle': {'visibility': 'hidden'},
                            'style': {'visibility': 'visible'}}),
    ('empty-rects', {'rects': []}),
    ('zero-width', {'rects': [{**RECT, 'width': 0}]}),
    ('zero-height', {'rects': [{**RECT, 'height': 0}]}),
    ('nonpositive-edges', {'rects': [{**RECT, 'right': -20, 'bottom': -20}]}),
    ('scroll-offset', {'rects': [{**RECT, 'right': -20, 'bottom': -20}],
                       'scrollX': 100, 'scrollY': 100}),
    ('fixed-edge', {'rects': [{**RECT, 'right': -20}],
                    'parentStyle': {'position': 'fixed'}, 'scrollX': 100}),
]


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('site', ['input', 'label', 'display', 'rejected-display'])
@pytest.mark.parametrize('name,change', CONTRASTS, ids=[r[0] for r in CONTRASTS])
async def test_generic_accessible_visibility_and_submit_boundary(
        kind, site, name, change, monkeypatch, tmp_path):
    change = copy.deepcopy(change)
    offsets = {k: change.pop(k) for k in ('scrollX', 'scrollY') if k in change}
    suffix = {'input': '', 'label': '-label', 'display': '-display',
              'rejected-display': '-display'}[site]
    spec = dict(requiredProxy=True, proxyKeys=['location', 'school'],
                visibility=[dict(id=k+suffix, **change) for k in ('location', 'school')],
                **offsets)
    if site == 'rejected-display':
        spec.update(badGraph=True, wrongLabel=True, proxyKeys=['school', 'degree'],
                    visibility=[dict(id='location-display', **change)])
    out = observe(kind, **spec)
    page, witness = out['page'], out['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    assert [(c['selector'], c['tag'], c['type'], c['form_selector'])
            for c in page['controls']] == [
        (c['selector'], c['tag'], c['type'], c['form_selector'])
        for c in witness['controls']]
    assert {f'#filler-{i}' for i in range(25)} <= {
        c['selector'] for c in page['controls']}
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    assert page['opaque_count'] > 0 and page['opaque_locators']
    for original, independent in zip(page['controls'], witness['controls']):
        assert original.get('combobox') == independent.get('combobox')
        assert 'graph_label_strict_visible' not in original
        assert 'graph_display_strict_visible' not in original

    # The generic witness protects the actual input and visible ARIA label.
    # A sibling display's legacy shape/geometry is not an approved answer.
    for key in ('location', 'school'):
        current = next(c for c in page['controls'] if c['selector'] == '#'+key)
        independent = next(c for c in witness['controls'] if c['selector'] == '#'+key)
        assert current['type'] == independent['type'] == 'combobox'
        assert current['combobox']['selected_text'] == ''
        assert current['combobox']['selected_value'] == ''
        assert independent['combobox']['linked'] is False
        assert independent['combobox']['options'] == []
        if site == 'input':
            assert independent['visible'] is (name == 'scroll-offset')
        elif site == 'label':
            assert bool(independent['visible_text']) is (name == 'scroll-offset')
        elif site == 'rejected-display' and key == 'location':
            # No label can be inferred from a broken aria-labelledby ID.
            assert independent['visible_text'] == ''
        else:
            # A display sibling shares an ancestor with the input. Hiding
            # that common ancestor is observable; hiding the display itself
            # does not make an approved value appear or disappear.
            ancestor_hidden = name in {
                'ancestor-opacity', 'ancestor-display',
                'ancestor-collapse', 'visible-descendant'}
            affected = site == 'display' or (
                site == 'rejected-display' and key == 'location')
            assert independent['visible'] is not (affected and ancestor_hidden)

    # Unproven form inventory must never become a Commit simply because its
    # former opacity/geometry graph certificate was removed.
    await assert_unapproved_submit_is_refused(out, monkeypatch, tmp_path)


@pytest.mark.parametrize('kind', ['entry', 'greenhouse'])
@pytest.mark.parametrize('mutation', [
    {'shellNested': True}, {'foreign': 'null'}, {'style': {'position': 'static'}},
    {'ownerAttr': ['aria-required', None]},
])
async def test_invalid_proxy_is_preserved_not_blanket_hidden(
        kind, mutation, monkeypatch, tmp_path):
    out = observe(kind, requiredProxy=True, proxyKeys=['location', 'school'],
                  proxyMutation={'key': 'location', **mutation})
    ordinary = observe(kind, requiredProxy=True, proxyKeys=['location', 'school'])
    page, witness = out['page'], out['witness']
    assert [c['selector'] for c in page['controls']] == [
        c['selector'] for c in witness['controls']]
    # A malformed validation-only input must not disappear as though it were
    # an authorized answer: it remains in both generic native inventories.
    def anonymous(reader):
        return [c for c in reader['controls']
                if c['selector'].startswith('html:nth-of-type(')
                and c['tag'] == 'input' and c['type'] == 'text'
                and not c.get('label') and c['in_form']]
    assert len(anonymous(page)) == len(anonymous(witness)) == 2
    assert len(anonymous(ordinary['page'])) == 2
    assert all(c['required'] is True for c in anonymous(page))
    assert all(c['visible'] is False for c in anonymous(witness))
    assert page['opaque_controls'] is True and page['inventory_verified'] is False
    assert next(c for c in page['controls']
                if c['selector'] == '#location')['combobox']['selected_value'] == ''
    await assert_unapproved_submit_is_refused(out, monkeypatch, tmp_path)


@pytest.mark.parametrize('part', ['location', 'location-label', 'location-display'])
@pytest.mark.parametrize('change', [dict(style={'opacity': '0'}), dict(style={'visibility': 'collapse'}),
                                  dict(rects=[{**RECT, 'width': 0}]),
                                  dict(rects=[{**RECT, 'right': -20}], parentStyle={'position': 'fixed'}, scrollX=100)])
async def test_graph_parity_never_grants_strict_invisible_evidence(
        grants, part, change, monkeypatch, tmp_path):
    # r06 deleted graph-label/display certificates for reversible work.
    # They cannot manufacture a current approved form value at final Submit.
    change = copy.deepcopy(change)
    offsets = {k: change.pop(k) for k in ('scrollX', 'scrollY') if k in change}
    session = Session(visibility=[dict(id=part, **change)], **offsets)
    p, w = session.snapshot['page'], session.snapshot['witness']
    target = next(c for c in p['controls'] if c['selector'] == '#location')
    evidence = next(c for c in w['controls'] if c['selector'] == '#location')
    assert target['combobox'] == evidence['combobox']
    assert target['combobox'].get('selected_text', '') == ''
    assert target['combobox'].get('selected_value', '') == ''
    if part != 'location':
        assert evidence['visible'] is True
    assert not {'graph_label_strict_visible', 'graph_display_strict_visible'} & set(evidence)
    assert session.calls == []
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert session.calls == []


@pytest.mark.parametrize('flag', ['graph_label_strict_visible', 'graph_display_strict_visible'])
@pytest.mark.parametrize('value', [None, False, 1, 'true'])
async def test_missing_or_nonboolean_strict_evidence_never_discloses(
        flag, value, monkeypatch, tmp_path):
    # Historic certificate values, even when forged into the fixed witness,
    # do not authorize a submitted answer or any application Submit IPC.
    session = Session()
    p, w = session.snapshot['page'], session.snapshot['witness']
    target = next(c for c in p['controls'] if c['selector'] == '#location')
    evidence = next(c for c in w['controls'] if c['selector'] == '#location')
    evidence[flag] = value
    assert flag not in target and evidence[flag] == value
    assert target['combobox'].get('selected_value', '') == ''
    assert session.calls == []
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert session.calls == []


@pytest.mark.parametrize('phase', ['C0', 'C1', 'C2'])
@pytest.mark.parametrize('part', ['location-label', 'location-display'])
@pytest.mark.parametrize('change', [dict(style={'opacity': '0'}), dict(style={'visibility': 'collapse'}),
                                  dict(rects=[{**RECT, 'height': 0}]),
                                  dict(rects=[{**RECT, 'right': -20}], parentStyle={'position': 'fixed'})])
async def test_every_phase_refuses_strict_evidence_before_commit_or_submit(
        grants, phase, part, change, monkeypatch, tmp_path):
    # A legacy C2 displayed caption is not a final receipt. Earlier phases
    # are likewise never grounds to treat an unanswered form as submitted.
    session = Session()
    session.phases['location'] = phase
    session.snapshot = dom(
        phases=session.phases, visibility=[dict(id=part, **change)], scrollX=100)
    page, fixed = session.snapshot['page'], session.snapshot['witness']
    field = next(c for c in page['controls'] if c['selector'] == '#location')
    independent = next(c for c in fixed['controls'] if c['selector'] == '#location')
    assert field['combobox'] == independent['combobox']
    assert session.calls == []
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert session.calls == []


@pytest.mark.parametrize('flag', ['graph_label_strict_visible', 'graph_display_strict_visible'])
async def test_strict_flag_guard_removal_is_detected(monkeypatch, tmp_path, flag):
    # The current generic reader does not use removed shape flags. An injected
    # field in the independent read still cannot replace a real committed
    # approved form value at the one production Submit guard.
    session = Session()
    evidence = next(c for c in session.snapshot['witness']['controls']
                    if c['selector'] == '#location')
    evidence[flag] = False
    page, lookup = await forms._observe_controls(session, await session.page())
    assert lookup['#location'][flag] is False
    assert flag not in next(c for c in page['controls']
                           if c['selector'] == '#location')
    await assert_unapproved_submit_is_refused(
        session.snapshot, monkeypatch, tmp_path, require_incomplete=False)
    assert session.calls == []
