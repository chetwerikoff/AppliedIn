"""Offline Lever, Greenhouse and Ashby application observations.

Synthetic transport/ATS fixtures remain available to other in-scope tests.
The former per-widget grant/settlement phase and pre-write proof tests were
retired in Issue #293. Only the raw final check decides application submission.
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
GH_WRAP = 'https://careers.example-co.test/openings/platform-engineer?gh_jid=123456'
GH_APPLY = 'https://job-boards.greenhouse.io/embed/job_app?for=example-co&token=123456'
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
            {'selector': c['selector'], 'caption': c['label'], 'label': c['label'], 'value': c['value']}
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
    config_dir = '/synthetic-only'
    if route == 'greenhouse':
        # A real reviewed YAML tuple is required by #250. Keep the temporary
        # directory alive with this synthetic case (no network/feed needed).
        from pathlib import Path
        from tempfile import TemporaryDirectory
        case._watchlist_dir = TemporaryDirectory(prefix='appliedin-gh-fixture-')
        config_dir = case._watchlist_dir.name
        (Path(config_dir) / 'watchlist.yaml').write_text(
            'companies:\n'
            '  - name: example-co\n'
            '    careers_url: https://careers.example-co.test/openings\n'
            '    ats: greenhouse\n'
            '    board: example-co\n'
            '    discovery: feed\n'
            '    mode: gated\n')
    monkeypatch.setattr('core.config.get_settings',
                        lambda: SimpleNamespace(config_dir=config_dir))
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

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,action,expected', [
    ('select', {'action': 'select', 'selector': '#mode',
                'value': 'Remote'}, ('select', '#mode')),
    ('checkbox', {'action': 'choose', 'selector': '#choice',
                  'value': 'Yes'}, ('click', '#choice')),
])
async def test_lever_form_preparation_is_ordinary_non_submit_ipc(
        monkeypatch, kind, action, expected):
    """No grant is needed to fill a bank-backed native choice."""
    case, raw = _world(monkeypatch, 'lever', kind=kind)
    result = await forms.execute(
        raw, case.page, action, facts={}, filled={}, resume_path='',
        company=COMPANY, jd_text='Synthetic role', resume_tex='', github='',
        pk=case.pk, job_url=case.wrapper)
    assert result['action'] == action['action']
    assert raw.writes == [expected]


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

