"""Offline native settlement across Lever, Greenhouse and the Ashby model-only boundary.

Every job, applicant, PAGE, grant and native IPC in this module is synthetic.
No browser, owner records, network, daemon or submission is used.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import logging
from types import SimpleNamespace

import pytest

from core.ids import make_pk
from discovery.watchlist import CompanyConfig
from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import field, inventory, submit, witness


COMPANY = 'example-co'
JOB = '12345678-1234-4234-8234-123456789abc'
LEVER_PK = make_pk(COMPANY, JOB)
LEVER_POSTING = f'https://jobs.lever.co/{COMPANY}/{JOB}'
LEVER_APPLY = LEVER_POSTING + '/apply'
ASHBY_PK = make_pk(COMPANY, JOB)
ASHBY_POSTING = f'https://jobs.ashbyhq.com/{COMPANY}/{JOB}'
ASHBY_APPLY = ASHBY_POSTING + '/application'
GH_PK = 'example-co#123456'
GH_WRAP = 'https://careers.example-co.test/openings?gh_jid=123456'
GH_APPLY = 'https://job-boards.greenhouse.io/example-co/jobs/123456'
PHONE = '+15550101010'


def _grant(item, url, value, *, group=()):
    record = {
        'value': value, 'selector': item['selector'], 'url': url,
        'label': item['label'], 'question': item.get('question', ''),
        'tag': item['tag'], 'type': item['type'],
        'form_selector': item['form_selector'],
        'form_action': item['form_action'],
    }
    if item['type'] == 'select':
        record['options'] = deepcopy(item['options'])
    elif item['type'] == 'checkbox':
        record['visible_text'] = item['label']
    elif item['type'] == 'radio':
        record['radio_name'] = item['name']
        record['radio_members'] = [
            {'selector': c['selector'], 'caption': c['label'], 'value': c['value']}
            for c in group
        ]
    return record

def _ashby_native_candidate(url, controls):
    """Build one explicit synthetic native proof from the reader's control census."""
    owner = '#application'
    members = []
    for control in controls:
        if not control.get('in_form') or control.get('form_selector') != owner:
            continue
        members.append({
            'selector': control['selector'], 'tag': control['tag'],
            'type': control['type'], 'submit': bool(control.get('submit')),
            'caption': control.get('label', ''), 'owned': True,
            'native_owner': control.get('native_form_owner_selector'),
            'declared_owner': control.get('declared_owner'),
            'label_count': 0 if control.get('submit') else 1,
            'labels_owned': True, 'disabled': control.get('disabled', False),
            'required': control.get('required', False),
            'visible': control.get('visible', True), 'readonly': False,
            'override_present': control.get('formaction_attr_present', False),
            'override_raw': control.get('formaction_raw'),
            'override_resolved': control.get('formaction', ''),
        })
    return {
        'selector': owner, 'plausible': True, 'action': url,
        'action_attr_present': True, 'action_attr': url, 'controls': members,
    }



def _fixture(route, *, kind=None, noisy=False):
    """The two routes have different configured origin and session contracts."""
    if route == 'lever':
        url, wrapper, pk = LEVER_APPLY, LEVER_POSTING, LEVER_PK
        config = CompanyConfig(name=COMPANY, ats='lever', board=COMPANY)
        row = {'pk': pk, 'company': COMPANY, 'ats': 'lever', 'job_id': JOB,
               'jd_url': wrapper, 'status': 'submitting',
               'possible_submission': False, 'human_approved_answers': {}}
    elif route == 'ashby':
        url, wrapper, pk = ASHBY_APPLY, ASHBY_POSTING, ASHBY_PK
        config = CompanyConfig(name=COMPANY, ats='ashby', board=COMPANY)
        row = {'pk': pk, 'company': COMPANY, 'ats': 'ashby', 'job_id': JOB,
               'jd_url': wrapper, 'status': 'submitting',
               'possible_submission': False, 'human_approved_answers': {}}
    elif route == 'greenhouse':
        url, wrapper, pk = GH_APPLY, GH_WRAP, GH_PK
        config = CompanyConfig(
            name=COMPANY, ats='greenhouse', board=COMPANY,
            careers_url='https://careers.example-co.test/openings')
        row = {'pk': pk, 'company': COMPANY, 'ats': 'greenhouse',
               'greenhouse_board': COMPANY, 'job_id': '123456',
               'jd_url': wrapper, 'status': 'submitting',
               'possible_submission': False, 'human_approved_answers': {}}
    else:
        raise AssertionError(route)
    phone_label = 'Phone - PRIVATE-LABEL-SENTINEL' if noisy else 'Phone'
    phone = field(phone_label, '#phone', type='tel', form_action=url)
    controls = [phone]
    facts = {phone_label: PHONE, 'Full name': 'Test User'}
    row['human_approved_answers'][phone_label] = _grant(phone, url, PHONE)
    picker = None
    kind = kind or ('combobox' if route == 'greenhouse' else 'select')
    if kind == 'select':
        picker = field(
            'Work mode', '#mode', tag='select', type='select',
            options=[{'value': 'remote', 'label': 'Remote'},
                     {'value': 'office', 'label': 'Office'}], form_action=url)
        controls.append(picker)
        facts['Work mode'] = 'Remote'
        row['human_approved_answers']['Work mode'] = _grant(picker, url, 'Remote')
    elif kind == 'combobox':
        combo = {
            'mode': 'linked', 'phase': '', 'linked': True, 'quarantined': False,
            'required_proxy_mounted': True, 'receiverless_country': False,
            'country_graph': [], 'link_id': 'city-options',
            'listbox_selector': '#city-options',
            'options': [{'selector': '#city-choice', 'label': 'Test City',
                         'value': 'Test City', 'unique': True,
                         'safe': True, 'submit': False, 'navigates': False}],
            'selected_text': '', 'displayed_text': '', 'selected_value': '',
            'hidden_selector': '#city-hidden', 'hidden_value': '',
        }
        picker = field('Location City', '#city', type='combobox',
                       tag='input', combobox=combo, form_action=url)
        controls.extend([picker, field(
            'City machine value', '#city-hidden', required=False,
            type='hidden', form_action=url)])
        facts['Location City'] = 'Test City'
        row['human_approved_answers']['Location City'] = _grant(picker, url, 'Test City')
    elif kind == 'checkbox':
        picker = field('Contact preference', '#choice', type='checkbox',
                       question='Preferred contact', value='on',
                       has_value=True, form_action=url)
        controls.append(picker)
        facts['Preferred contact'] = 'yes'
        row['human_approved_answers']['Preferred contact'] = _grant(picker, url, 'yes')
    elif kind == 'radio':
        morning = field('Morning', '#morning', type='radio', name='schedule',
                        question='Schedule preference', value='am',
                        has_value=True, form_action=url)
        evening = field('Evening', '#evening', type='radio', name='schedule',
                        question='Schedule preference', value='pm',
                        has_value=True, form_action=url)
        controls.extend([morning, evening])
        facts['Schedule preference'] = 'Evening'
        row['human_approved_answers']['Schedule preference'] = _grant(
            morning, url, 'Evening', group=[morning, evening])
    else:
        raise AssertionError(kind)

    file_control = field('Resume', '#resume', type='file', form_action=url)
    controls.extend([file_control, submit(form_action=url)])
    page = inventory(*controls, url=url)
    if route in {'lever', 'ashby'}:
        page['entry'] = {
            'root': {'selector': '#application', 'action': url},
            'frames': [], 'responses': [], 'response_error': False,
            'helper_challenge': False,
        }
    else:
        page['greenhouse'] = {
            'root': {'selector': '#application', 'action': url},
            'frames': [], 'response_controls': False, 'badge_responses': 0,
        }
    return SimpleNamespace(
        row=row, config=config, page=page, url=url, wrapper=wrapper, pk=pk,
        facts=facts, phone=phone, picker=picker, kind=kind)


class NativeTransport:
    """Independent PAGE and fixed-witness copies, never model-crafted receipts."""
    def __init__(self, page):
        self.state = deepcopy(page)
        self.calls = []
        self.writes = []
        self.filled = {}
        self.witnesses = 0
        self.ignore_commit = False
        self.invalid_witness_after_write = False
        self.invalid_witness_always = False
        self.drift_at_witness = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def target(self, selector):
        found = [c for c in self.state['controls'] if c['selector'] == selector]
        assert len(found) == 1, selector
        return found[0]

    async def call(self, command, *args, **kwargs):
        self.calls.append((command, *args))
        if command == 'navigate':
            assert args[0] == self.state['url']
            return {}
        if command == 'evaluate':
            script = args[0]
            if script in {forms.ENTRY_PAGE, forms.GREENHOUSE_PAGE}:
                return {'value': deepcopy(self.state)}
            assert script in {forms._ENTRY_FIXED_CONTROL_WITNESS,
                              forms._GREENHOUSE_FIXED_CONTROL_WITNESS,
                              forms._FIXED_CONTROL_WITNESS}
            self.witnesses += 1
            if self.drift_at_witness == self.witnesses:
                self.state['controls'][0]['form_action'] += '/changed'
            source = deepcopy(self.state)
            independent = witness(source)
            for index, control in enumerate(source['controls']):
                if control['type'] == 'combobox':
                    independent['controls'][index]['combobox'] = deepcopy(control['combobox'])
            if source['url'] == ASHBY_APPLY and source.get('entry', {}).get('native_candidates'):
                # The fixed witness independently reconstructs its candidate census from
                # its own observed controls; it does not echo PAGE's native_candidates.
                for observed, control in zip(independent['controls'], source['controls']):
                    observed.update(
                        label=control['label'], submit=bool(control.get('submit')),
                        required=control.get('required', False),
                        form_action_attr_present=control['form_action_attr_present'],
                        form_action_raw=control['form_action_raw'],
                        formaction_attr_present=control['formaction_attr_present'],
                        formaction_raw=control['formaction_raw'],
                    )
                independent['entry'] = {
                    key: deepcopy(value) for key, value in source['entry'].items()
                    if key != 'native_candidates'
                }
                independent['entry']['native_candidates'] = [
                    _ashby_native_candidate(source['url'], independent['controls'])
                ]
            else:
                for category in ('entry', 'greenhouse'):
                    if category in source:
                        independent[category] = deepcopy(source[category])
            if self.invalid_witness_always or (self.invalid_witness_after_write and self.writes):
                independent['controls'][0]['unique'] = False
            return {'value': independent}
        assert command in {'fill', 'select', 'click', 'upload'}, command
        selector = args[0]
        assert selector not in {'#submit', '#continue'}, 'No submit/Continue IPC'
        self.writes.append((command, selector))
        if self.ignore_commit:
            return {}
        if command in {'fill', 'select'}:
            assert args[1] == '--value'
            target = self.target(selector)
            assert ((command == 'fill' and target['type'] == 'tel')
                    or (command == 'select' and target['type'] == 'select'))
            target['value'] = args[2]
            target['has_value'] = True
            self.filled[selector] = args[2]
        elif command == 'upload':
            assert args[1] == '--file' and selector == '#resume'
            self.target(selector)['files'] = [args[2].rsplit('/', 1)[-1]]
            self.target(selector)['has_value'] = True
        elif selector == '#city-choice':
            target = self.target('#city')
            option = target['combobox']['options'][0]
            combo = target['combobox']
            for key, value in (('selected_text', option['label']),
                               ('displayed_text', option['label']),
                               ('selected_value', option['value']),
                               ('hidden_value', option['value'])):
                combo[key] = value
            self.target('#city-hidden')['value'] = option['value']
            self.target('#city-hidden')['has_value'] = True
            self.filled['#city'] = option['label']
        else:
            target = self.target(selector)
            assert target['type'] in {'radio', 'checkbox'}
            if target['type'] == 'radio':
                for c in self.state['controls']:
                    if c.get('type') == 'radio' and c.get('name') == target['name']:
                        c['checked'] = c is target
            else:
                target['checked'] = True
            self.filled[selector] = target['value'] or target['label']
        return {}


def _world(monkeypatch, route, *, kind=None, noisy=False, explicit_ashby_native=False):
    case = _fixture(route, kind=kind, noisy=noisy)
    if explicit_ashby_native:
        if route != 'ashby':
            raise AssertionError('Explicit native evidence is Ashby-only')
        for control in case.page['controls']:
            if control.get('in_form') and control.get('form_selector') == '#application':
                control.update(
                    form_action_attr_present=True, form_action_raw=ASHBY_APPLY,
                    formaction_attr_present=False, formaction_raw=None,
                )
        case.page['entry']['native_candidates'] = [
            _ashby_native_candidate(ASHBY_APPLY, case.page['controls'])
        ]
    raw = NativeTransport(case.page)
    monkeypatch.setattr('core.config.get_settings',
                        lambda: SimpleNamespace(config_dir='/synthetic-only'))
    monkeypatch.setattr('discovery.watchlist.load_watchlist', lambda path: [case.config])
    monkeypatch.setattr(forms, '_row', lambda pk, *args, **kw: case.row)
    monkeypatch.setattr(forms, 'check_dispatch', lambda *args, **kw: None)
    monkeypatch.setattr('tools.claude_chrome._stage_resume',
                        lambda path, owner: path)
    monkeypatch.setattr('tools.browser_apply._site_rules', lambda *args: '')
    monkeypatch.setattr('core.events.emit', lambda *args, **kw: None)
    monkeypatch.setattr(bsk, 'Session', lambda kind: raw)
    return case, raw


async def _apply(case, facts=None, *, resume_path='Resume.pdf'):
    return await forms.apply(case.wrapper, COMPANY,
                             case.facts if facts is None else facts,
                             'synthetic', pk=case.pk, resume_path=resume_path)

async def test_greenhouse_unpinned_host_prepares_but_final_guard_refuses(monkeypatch):
    """A missing board pin is not an early gate, but can never authorize Submit."""
    case, raw = _world(monkeypatch, 'greenhouse')
    case.row.pop('greenhouse_board')
    visited = []

    async def navigate(url):
        visited.append(url)
        return deepcopy(raw.state)

    async def stop_after_navigation(_session, page, *_args, **_kwargs):
        assert page['url'] == case.url
        raise forms.Gate('Synthetic stop after allowed host-only preparation.')

    raw.navigate = navigate
    monkeypatch.setattr(forms, '_bootstrap', stop_after_navigation)
    outcome = await _apply(case)
    assert outcome['status'] == 'gate'
    assert 'Synthetic stop after allowed host-only preparation' in outcome['question']
    assert visited == [case.wrapper]
    assert forms._preparation_origin(case.url, case.wrapper)
    assert raw.writes == []

    # This is the original production final-click decision. Even after
    # permitted entry the missing persisted board must deny with zero Submit.
    controller = forms.ApplicationIPCController(
        case.pk, COMPANY, case.wrapper, '', case.facts, {}, [], {}, lambda: None)
    submitter = next(c for c in case.page['controls'] if c['selector'] == '#submit')
    with pytest.raises(forms.Gate, match='tenant or board') as denied:
        await controller.authorize_final_submit(raw, case.page, submitter)
    assert denied.value.reason == 'defect_tenant'
    assert raw.writes == []
    assert not any(c[0] == 'click' and c[1] == '#submit' for c in raw.calls)


def _decisions(caplog):
    return [ast.literal_eval(record.getMessage().split('bootstrap field decision ', 1)[1])
            for record in caplog.records if record.name == forms.__name__
            and record.getMessage().startswith('bootstrap field decision ')]


@pytest.mark.parametrize(('route', 'kind', 'session_type'), [
    ('lever', 'select', forms._EntrySession),
    ('greenhouse', 'combobox', forms._GreenhouseSession),
])
async def test_actual_vendor_apply_commits_before_first_decision_and_upload_stays_separate(
        monkeypatch, caplog, route, kind, session_type):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, route, kind=kind)
    # These are additional bank-backed native choices, not model-picked options.
    if route == 'lever':
        box = field('Contact preference', '#choice', type='checkbox',
                    question='Preferred contact', value='on',
                    has_value=True, form_action=case.url)
        case.page['controls'].insert(-2, box)
        raw.state['controls'].insert(-2, deepcopy(box))
        case.facts['Preferred contact'] = 'yes'
        case.row['human_approved_answers']['Preferred contact'] = _grant(box, case.url, 'yes')
    else:
        morning = field('Morning', '#morning', type='radio', name='schedule',
                        question='Schedule preference', value='am',
                        has_value=True, form_action=case.url)
        evening = field('Evening', '#evening', type='radio', name='schedule',
                        question='Schedule preference', value='pm',
                        has_value=True, form_action=case.url)
        case.page['controls'][-2:-2] = [morning, evening]
        raw.state['controls'][-2:-2] = [deepcopy(morning), deepcopy(evening)]
        case.facts['Schedule preference'] = 'Evening'
        case.row['human_approved_answers']['Schedule preference'] = _grant(
            morning, case.url, 'Evening', group=[morning, evening])
    sessions, first_page, snapshots = [], [], []
    bootstrap = forms._bootstrap

    async def observed_bootstrap(session, *args, **kwargs):
        sessions.append(type(session))
        return await bootstrap(session, *args, **kwargs)

    monkeypatch.setattr(forms, '_bootstrap', observed_bootstrap)

    async def decide(prompt, page, history, *, model):
        snapshots.append(deepcopy(history))
        assert 'For the required résumé file input, use upload' in prompt
        assert page['approved_choices'] == []
        if len(snapshots) == 1:
            assert not any(c.get('selector') == '#resume' and c.get('files')
                           for c in page['controls'])
        if len(snapshots) == 1:
            first_page.append(deepcopy(page))
            assert raw.target('#phone')['value'] == PHONE
            assert raw.target('#phone')['has_value']
            assert not raw.target('#resume').get('files')
            assert len(history) == 3
            assert {r['action'] for r in history} == (
                {'fill', 'select', 'choose'} if route == 'lever'
                else {'fill', 'combobox', 'choose'})
            forms.check_approvals(
                case.pk, history, page,
                provenance=forms._configured_alias(case.pk, COMPANY, case.wrapper))
            with pytest.raises(forms.Gate, match='attachment'):
                forms.check_form(page, raw.filled, False, 'Resume.pdf', history)
            return {'action': 'upload', 'selector': '#resume'}
        assert len(snapshots) == 2
        assert len([r for r in history if r.get('fact')]) == 3
        assert any(r['action'] == 'upload' for r in history)
        forms.check_approvals(
            case.pk, history, page,
            provenance=forms._configured_alias(case.pk, COMPANY, case.wrapper))
        forms.check_form(page, raw.filled, True, 'Resume.pdf', history)
        return {'action': 'gate', 'question': 'Synthetic stop before submit'}

    monkeypatch.setattr(bsk, 'decision', decide)
    outcome = await _apply(case)
    assert outcome['status'] == 'gate' and outcome['reason'] == 'unknown_field'
    assert sessions == [session_type, session_type]
    assert len(first_page) == 1
    assert [c['selector'] for c in first_page[0]['controls']] == [
        c['selector'] for c in case.page['controls']]
    assert sum(x[0] == 'fill' and x[1] == '#phone' for x in raw.writes) == 1
    expected_write = (('select', '#mode') if route == 'lever'
                      else ('click', '#city-choice'))
    assert raw.writes.count(expected_write) == 1
    assert raw.writes.count(('click', '#choice' if route == 'lever' else '#evening')) == 1
    assert raw.writes.count(('upload', '#resume')) == 1
    assert not any(call[0] == 'click' and call[1] in {'#submit', '#continue'}
                   for call in raw.calls)
    assert raw.witnesses >= 6
    decisions = _decisions(caplog)
    assert [d['decision'] for d in decisions] == (
        ['settled-from-bank'] * 3 + ['already-satisfied'] * 3)
    assert all(set(d) == {'label', 'type', 'required', 'decision'}
               and d['required'] is True for d in decisions)
    assert 'Resume.pdf' not in str(decisions)
    assert 'example-co' not in str(decisions)


@pytest.mark.parametrize(('route', 'case_kind', 'expected'), [
    ('lever', 'no-grant', 'needs-human'),
    ('greenhouse', 'no-bank', 'needs-human'),
    ('lever', 'incomplete', 'needs-human'),
    ('greenhouse', 'stale-selector', 'blocked'),
    ('lever', 'stale-answer', 'blocked'),
    ('greenhouse', 'cross-job', None),
    ('lever', 'unsafe', 'blocked'),
    ('greenhouse', 'no-witness', None),
    ('lever', 'option-ambiguous', 'blocked'),
])
async def test_bank_grant_and_failure_matrix_never_writes_without_authority(
        monkeypatch, caplog, route, case_kind, expected):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, route)
    if case_kind == 'no-grant':
        case.row['human_approved_answers'].pop(case.phone['label'])
    elif case_kind == 'no-bank':
        case.facts.pop(case.phone['label'])
        case.row['human_approved_answers'].pop(case.phone['label'])
    elif case_kind == 'incomplete':
        case.row['human_approved_answers'][case.phone['label']].pop('form_action')
    elif case_kind == 'stale-selector':
        case.row['human_approved_answers'][case.phone['label']]['selector'] = '#other'
    elif case_kind == 'stale-answer':
        case.row['human_approved_answers'][case.phone['label']]['value'] = 'old'
    elif case_kind == 'cross-job':
        case.row['pk'] = 'other-job#1'
    elif case_kind == 'unsafe':
        # Protected self-identification is never affirmatively disclosed even
        # with a current grant.
        case.phone['label'] = 'Gender identification'
        case.facts.pop('Phone')
        case.facts['Gender identification'] = 'Yes'
        case.row['human_approved_answers'] = {
            'Gender identification': _grant(case.phone, case.url, 'Yes')}
        raw.state['controls'][0]['label'] = 'Gender identification'
    elif case_kind == 'no-witness':
        raw.invalid_witness_always = True
    elif case_kind == 'option-ambiguous':
        case.facts.pop('Phone')
        case.row['human_approved_answers'].pop('Phone')
        case.page['controls'] = case.page['controls'][1:]
        raw.state['controls'] = raw.state['controls'][1:]
        control = next(c for c in raw.state['controls'] if c['type'] == 'select')
        control['options'].append({'value': 'elsewhere', 'label': 'Remote'})
        case.row['human_approved_answers']['Work mode']['options'] = deepcopy(control['options'])
        case.facts['Work mode'] = 'Remote'
    async def no_decision(*args, **kwargs):
        pytest.fail('A blocked or ungranted field must gate before model decision')
    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case)
    assert result['status'] == 'gate'
    if case_kind in {'no-grant', 'no-bank', 'incomplete'}:
        assert result['form_question']['selector'] == '#phone'
    else:
        assert result['form_question'] is None
    assert raw.writes == []
    data = _decisions(caplog)
    if expected is not None:
        assert len(data) == 1 and data[0]['decision'] == expected
    else:
        assert data == []


@pytest.mark.parametrize(('route', 'failure'), [
    ('lever', 'uncommitted'), ('greenhouse', 'witness-drift'),
])
async def test_post_authorized_ipc_without_commit_proof_stops_without_replay(
        monkeypatch, caplog, route, failure):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, route)
    if failure == 'uncommitted':
        raw.ignore_commit = True
    else:
        raw.invalid_witness_after_write = True
    async def no_decision(*args, **kwargs):
        pytest.fail('Uncommitted control cannot reach decision')
    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case)
    assert result['status'] == 'gate' and result['form_question'] is None
    assert raw.writes == [('fill', '#phone')]
    assert _decisions(caplog)[0]['decision'] == 'blocked'


async def test_grant_only_choice_is_model_visible_and_guarded_not_pre_settled(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, 'lever', kind='checkbox')
    case.facts.pop('Phone')
    case.row['human_approved_answers'].pop('Phone')
    case.page['controls'] = case.page['controls'][1:]
    raw.state['controls'] = raw.state['controls'][1:]
    case.facts.pop('Preferred contact')
    calls = 0

    async def decide(prompt, page, history, *, model):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert page['approved_choices'][0]['key'] == 'Preferred contact'
            assert page['approved_choices'][0]['approved_answer']['checked'] is True
            assert not raw.writes
            return {'action': 'choose', 'selector': '#choice', 'fact': 'Preferred contact',
                    'value': 'no'}
        return {'action': 'gate', 'question': 'Synthetic review'}

    monkeypatch.setattr(bsk, 'decision', decide)
    result = await _apply(case)
    assert result['status'] == 'gate' and calls == 2
    assert raw.writes == [('click', '#choice')]
    assert [d['decision'] for d in _decisions(caplog)] == [
        'left-to-model', 'already-satisfied']


async def test_private_labels_and_gate_field_logging_never_emit_untrusted_values(
        monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, 'lever', noisy=True)
    previous = case.row['human_approved_answers'].pop(case.phone['label'])
    case.facts.pop(case.phone['label'])
    case.facts['PRIVATE-QUESTION-SENTINEL'] = PHONE
    previous['question'] = 'PRIVATE-QUESTION-SENTINEL'
    previous['synthetic_secret'] = 'PRIVATE-GRANT-SENTINEL'
    case.row['human_approved_answers']['PRIVATE-QUESTION-SENTINEL'] = previous
    case.page['text'] = 'PRIVATE-PAGE-SENTINEL'
    raw.state['text'] = 'PRIVATE-PAGE-SENTINEL'
    case.page['controls'][0]['question'] = 'PRIVATE-QUESTION-SENTINEL'
    raw.state['controls'][0]['question'] = 'PRIVATE-QUESTION-SENTINEL'
    case.facts.pop('Work mode')
    case.row['human_approved_answers'].pop('Work mode')
    async def no_decision(*args, **kwargs):
        pytest.fail('Second unanswered field must gate')
    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case, resume_path='PRIVATE-FILE-SENTINEL.pdf')
    assert result['status'] == 'gate'
    assert result['form_question']['selector'] == '#mode'
    data = _decisions(caplog)
    assert [entry['decision'] for entry in data] == [
        'settled-from-bank', 'needs-human']
    assert data[0]['label'] == 'field-1'
    assert data[1]['label'] == 'field-2'
    for token in ('PRIVATE-LABEL-SENTINEL', 'PRIVATE-GRANT-SENTINEL',
                  'PRIVATE-PAGE-SENTINEL', 'PRIVATE-QUESTION-SENTINEL',
                  'PRIVATE-FILE-SENTINEL', PHONE, case.wrapper):
        assert token not in str(data)
    assert set(data[0]) == {'label', 'type', 'required', 'decision'}
    assert raw.writes == [('fill', '#phone')]


@pytest.mark.parametrize('route', ['lever', 'greenhouse'])
async def test_model_captcha_prose_is_not_observed_challenge(monkeypatch, route):
    case, raw = _world(monkeypatch, route)
    case.page['controls'] = [c for c in case.page['controls']
                             if c['type'] not in {'tel', 'select', 'combobox', 'hidden'}]
    # A non-required native input is enough to prove an application-form
    # receiver without provoking a new bootstrap question.
    case.page['controls'].insert(0, field(
        'Optional note', '#note', required=False, form_action=case.url))
    raw.state['controls'] = deepcopy(case.page['controls'])
    async def decide(*args, **kwargs):
        return {'action': 'gate', 'question': 'A synthetic CAPTCHA might be here'}
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await _apply(case)
    assert result['status'] == 'gate'
    assert result['reason'] == 'unknown_field'
    assert not raw.writes


@pytest.mark.parametrize(('route', 'positive'), [
    ('lever', 'helper'), ('lever', 'visible-frame'),
    ('greenhouse', 'visible-frame'),
])
async def test_positive_observed_challenge_keeps_captcha_reason(
        monkeypatch, route, positive):
    case, raw = _world(monkeypatch, route)
    if route == 'lever':
        if positive == 'helper':
            raw.state['entry']['helper_challenge'] = True
        else:
            raw.state['entry']['frames'] = [{'status': 'visible-hcaptcha'}]
    else:
        raw.state['greenhouse']['frames'] = [{'status': 'visible-recaptcha'}]
    async def no_decision(*args, **kwargs):
        pytest.fail('Positive observed challenge must gate')
    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case)
    assert result['status'] == 'gate' and result['reason'] == 'captcha'
    assert not raw.writes

@pytest.mark.parametrize('route', ['lever', 'greenhouse'])
async def test_changed_native_receiver_at_prewrite_witness_never_authorizes_ipc(
        monkeypatch, route):
    """Readiness alone is not permission: every write needs its own current receiver."""
    case, raw = _world(monkeypatch, route)
    # The stable entry uses two witness pairs, bootstrap one, and execute()
    # must independently witness the raw receiver again before writing.
    raw.drift_at_witness = 4

    async def no_decision(*args, **kwargs):
        pytest.fail('A drifting current receiver must gate before model access')

    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case)
    assert result['status'] == 'gate' and result['form_question'] is None
    assert raw.witnesses >= 4
    assert raw.writes == []
    assert not any(c[0] == 'click' for c in raw.calls)


async def test_ashby_native_entry_retains_prior_model_only_behavior(monkeypatch):
    """An Ashby native application form must not gain Lever/Greenhouse prefill."""
    case, raw = _world(monkeypatch, 'ashby', kind='select', explicit_ashby_native=True)
    admitted_sessions = []
    original_bootstrap = forms._bootstrap

    async def observe_bootstrap(session, *args, **kwargs):
        admitted_sessions.append(type(session))
        return await original_bootstrap(session, *args, **kwargs)

    async def decide(prompt, page, history, *, model):
        assert history == []
        assert raw.writes == []
        assert {c['selector'] for c in page['controls']} >= {'#phone', '#mode'}
        assert all(not next(c for c in page['controls'] if c['selector'] == key)['has_value']
                   for key in ('#phone', '#mode'))
        return {'action': 'gate', 'question': 'Synthetic stop before writing'}

    monkeypatch.setattr(forms, '_bootstrap', observe_bootstrap)
    monkeypatch.setattr(bsk, 'decision', decide)
    result = await _apply(case)
    assert result['status'] == 'gate' and result['reason'] == 'unknown_field'
    assert admitted_sessions == [forms._EntrySession]
    assert raw.writes == []


@pytest.mark.parametrize(('route', 'kind', 'picker_ipc'), [
    ('lever', 'select', ('select', '#mode')),
    ('greenhouse', 'combobox', ('click', '#city-choice')),
])
async def test_later_picker_clears_earlier_phone_and_cumulative_readback_gates(
        monkeypatch, caplog, route, kind, picker_ipc):
    """A reactive picker may erase an earlier fact despite a valid latest receipt."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, route, kind=kind)
    native_call = raw.call

    async def reactive_call(command, *args, **kwargs):
        result = await native_call(command, *args, **kwargs)
        if (command, args[0] if args else None) == picker_ipc:
            phone = raw.target('#phone')
            phone['has_value'] = False
            phone['value'] = ''
        return result

    raw.call = reactive_call

    async def review_before_final_submit(_prompt, page, history, *, model):
        # A later approved picker may clear a prior fact. Preparation reaches
        # the model; the real final form check still forbids Submit.
        assert raw.target('#phone')['has_value'] is False
        with pytest.raises(forms.Gate, match='Required field is empty: Phone'):
            forms.check_form(page, {'#phone': PHONE}, uploaded=True, receipts=history)
        return {'action': 'gate', 'question': 'Synthetic final form requires phone re-entry'}

    monkeypatch.setattr(bsk, 'decision', review_before_final_submit)
    result = await _apply(case)
    assert result['status'] == 'gate' and result['form_question'] is None
    assert raw.writes == [('fill', '#phone'), picker_ipc]
    assert raw.target('#phone')['has_value'] is False
    if kind == 'select':
        assert raw.target('#mode')['has_value'] is True
    else:
        assert raw.target('#city')['combobox']['selected_text'] == 'Test City'
    assert [d['decision'] for d in _decisions(caplog)] == [
        'settled-from-bank', 'settled-from-bank']


async def test_reordered_radio_after_phone_fill_still_gets_one_bound_gate(
        monkeypatch, caplog):
    """Group membership is counted only after its current first member is visited."""
    caplog.set_level(logging.INFO, logger=forms.__name__)
    case, raw = _world(monkeypatch, 'lever', kind='radio')
    case.facts.pop('Schedule preference')
    case.row['human_approved_answers'].pop('Schedule preference')
    native_call = raw.call

    async def reorder_after_phone(command, *args, **kwargs):
        result = await native_call(command, *args, **kwargs)
        if command == 'fill' and args[0] == '#phone':
            controls = raw.state['controls']
            radio_indices = [i for i, c in enumerate(controls) if c['type'] == 'radio']
            assert len(radio_indices) == 2
            first, second = radio_indices
            controls[first], controls[second] = controls[second], controls[first]
        return result

    raw.call = reorder_after_phone

    async def no_decision(*args, **kwargs):
        pytest.fail('An unapproved reordered required radio must gate before model')

    monkeypatch.setattr(bsk, 'decision', no_decision)
    result = await _apply(case)
    assert result['status'] == 'gate'
    assert result['form_question']['label'] == 'Schedule preference'
    assert result['form_question']['selector'] == '#evening'
    assert [c['selector'] for c in raw.state['controls'] if c['type'] == 'radio'] == [
        '#evening', '#morning']
    assert raw.writes == [('fill', '#phone')]
    decisions = _decisions(caplog)
    assert [d['decision'] for d in decisions] == [
        'settled-from-bank', 'needs-human']
    assert [d['type'] for d in decisions] == ['tel', 'radio']
    assert all(set(d) == {'label', 'type', 'required', 'decision'}
               and d['required'] is True for d in decisions)
