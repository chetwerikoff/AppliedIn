"""Offline evidence for the two isolated read-only legs and aggregate truthfulness."""
from copy import deepcopy
import inspect
import io
import json
from types import SimpleNamespace

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_phone_block import served_fixture_dom


@pytest.fixture
def state(tmp_path, monkeypatch):
    run = fixture._SyntheticRun(str(tmp_path / 'Synthetic-Resume.pdf'))
    monkeypatch.setattr(fixture, '_daemon_origin', lambda: True)
    token = fixture._SESSION_ENTRY.set(run)
    try:
        yield run
    finally:
        fixture._SESSION_ENTRY.reset(token)


def observed(state, kind):
    state.custom_question = kind
    state.enable_positive_fixture()
    snapshot = served_fixture_dom(fixture._lever_question_form(kind))
    state.observe_page(snapshot['page'])
    state.entry_wait = 'PASS'
    state.navigations = 1
    page = snapshot['page']
    forms._inventory_ok(page)
    target = next(c for c in page['controls'] if c['type'] == kind)
    witness = {c['selector']: c for c in snapshot['witness']['controls']}
    question = forms._disclosure_question(page, target, witness, fixture.URL, bootstrap=True)
    return {'status': 'gate', 'form_question': question}


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_served_custom_card_bytes_reach_real_human_question(state, kind):
    result = observed(state, kind)
    observation = fixture._lever_question_observation(state, result)
    assert observation == {
        'safe_gate': True, 'question': 'Synthetic '+kind+' question *',
        'lever_card_unresolved': False, 'unselected': True, 'choice_ipc': 0,
        'final_stage': 'Lever custom '+kind+' question'}
    assert state.row['human_approved_answers'] == {}
    assert fixture._lever_question_form(kind).count(b'application-question custom-question') == 1


# Each counterfactual removes only the predicate that rejects its defect.
PREDICATES = [
    ("result.get('status') == 'gate'", 'status'),
    ("state.entry_wait == 'PASS'", 'entry'),
    ("len(controls) == (2 if kind == 'radio' else 1)", 'count'),
    ("c.get('question') == expected", 'observed-question'),
    ("c.get('native_form_owner_selector') == '#application-form'", 'owner'),
    ("question.get('selector') == controls[0].get('selector')", 'selector'),
    ("question.get('question') == expected", 'question'),
    ("question.get('type') == kind", 'type'),
    ('unselected', 'selected'),
    ('not unresolved', 'unresolved'),
    ("page.get('inventory_verified') is True", 'inventory'),
    ("not page.get('opaque_controls')", 'opaque'),
    ("not state.row.get('human_approved_answers')", 'approval'),
    ('state.choice_ipc == state.fills == state.decisions == state.ipc_index == 0', 'choice'),
    ('not state.uploaded', 'upload'),
    ('not state.committed', 'commit'),
    ('state.submit_call_entered == state.submit_ipc == state.post_attempts == state.post_count == 0', 'submit'),
    ('not state.post_receipts', 'receipt'),
    ('state.navigations == 1', 'navigation'),
]


def mutate(state, result, defect):
    controls = [c for c in state.last_page['controls'] if c['type'] in {'radio', 'checkbox'}]
    if defect == 'status': result['status'] = 'applied'
    elif defect == 'entry': state.entry_wait = 'PENDING'
    elif defect == 'count': state.last_page['controls'].append(deepcopy(controls[0]))
    elif defect == 'observed-question': controls[0]['question'] = 'Wrong synthetic question'
    elif defect == 'owner': controls[0]['native_form_owner_selector'] = '#other'
    elif defect == 'selector': result['form_question']['selector'] = '#other'
    elif defect == 'question': result['form_question']['question'] = 'Wrong synthetic question'
    elif defect == 'type': result['form_question']['type'] = 'text'
    elif defect == 'selected': controls[0]['checked'] = True
    elif defect == 'unresolved': controls[0]['lever_card_unresolved'] = True
    elif defect == 'inventory': state.last_page['inventory_verified'] = False
    elif defect == 'opaque': state.last_page['opaque_controls'] = True
    elif defect == 'approval': state.row['human_approved_answers']['invented'] = {}
    elif defect == 'choice': state.choice_ipc = 1
    elif defect == 'upload': state.uploaded = True
    elif defect == 'commit': state.committed['invented'] = 'answer'
    elif defect == 'submit': state.submit_ipc = 1
    elif defect == 'receipt': state.post_receipts.append({})
    elif defect == 'navigation': state.navigations = 2
    else: raise AssertionError(defect)


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
@pytest.mark.parametrize('predicate,defect', PREDICATES, ids=[x[1] for x in PREDICATES])
def test_custom_leg_rejects_false_pass_with_each_predicate_removed(state, kind, predicate, defect):
    result = observed(state, kind)
    mutate(state, result, defect)
    assert fixture._lever_question_observation(state, result)['safe_gate'] is False
    assert fixture._lever_question_observation(state, result)['final_stage'] == 'premature'
    source = inspect.getsource(fixture._lever_question_observation)
    # Restrict replacement to the PASS predicate, not its named observations.
    start, tail = source.split('    ok = ', 1)
    expression, end = tail.split('    return ', 1)
    assert expression.count(predicate) == 1
    namespace = dict(fixture.__dict__)
    exec(start+'    ok = '+expression.replace(predicate, 'True')+'    return '+end, namespace)
    assert namespace['_lever_question_observation'](state, result)['safe_gate'] is True
    assert fixture._lever_question_observation(state, result)['safe_gate'] is False


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
def test_custom_leg_served_on_sealed_get_route(state, kind):
    state.custom_question = kind
    handler = object.__new__(fixture._FixtureHandler)
    handler.path, handler.headers = fixture._PATH, {'Host': '127.0.0.1:18789'}
    handler.server = SimpleNamespace(synthetic_run=state)
    handler.wfile = io.BytesIO()
    statuses = []
    handler.send_response = statuses.append
    handler.send_header = lambda *a: None
    handler.end_headers = lambda: None
    handler.do_GET()
    assert statuses == [200]
    assert handler.wfile.getvalue() == fixture._lever_question_form(kind)
    handler.do_POST()
    assert statuses == [200, 405]
    assert state.post_attempts == 1 and state.post_count == 0


@pytest.mark.parametrize('kind', ['radio', 'checkbox'])
async def test_custom_leg_forwards_no_applicant_or_choice_ipc(state, kind):
    state.custom_question = kind
    state.enable_positive_fixture()
    calls = []
    class Raw:
        async def call(self, *args):
            calls.append(args)
            return {}
    facade = state.facade(Raw())
    await facade.call('navigate', fixture.URL)
    for ipc in [('click', '#custom-'+kind+'-first'), ('click', '#submit'),
                ('fill', '#name', '--value', 'Test User')]:
        with pytest.raises(PermissionError, match='observation only'):
            await facade.call(*ipc)
    assert calls == [('navigate', fixture.URL)]
    assert state.choice_ipc == 3 and state.ipc_index == state.submit_ipc == 0
    assert state.row['human_approved_answers'] == {}


@pytest.mark.parametrize('failure', [None, 0, 1, 2, 3, 4, 5, 6, 7, 8])
def test_run_unconditionally_executes_all_fixture_scenarios_and_propagates_each_failure(monkeypatch, capsys, failure):
    calls = []
    def scenario(**kwargs):
        calls.append(kwargs)
        return int(len(calls)-1 == failure)
    monkeypatch.setattr(fixture, '_run_scenario', scenario)
    assert fixture.run() == (0 if failure is None else 1)
    assert calls == [{}, {'greenhouse': True}, {'greenhouse_empty_placeholder': True},
                     {'w_lookalike': True}, {'ashby': True}, {'ashby_upload': True},
                     {'ashby_upload': True, 'ashby_upload_transient': True},
                     {'custom_question': 'radio'}, {'custom_question': 'checkbox'}]
    report = json.loads(capsys.readouterr().out)
    assert report['sessions'] == 9 and len(report['legs']) == 8
    assert report['safe_gate'] is (failure is None)
