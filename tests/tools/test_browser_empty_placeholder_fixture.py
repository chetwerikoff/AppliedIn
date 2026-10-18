"""All fixture sessions and real report logic, with no listener or browser.

Stub the application traversal with synthetic completed observations; the fixture's
stage and safety adjudication, per-leg output, and all-leg dispatch remain real.
"""
import io
import json
from types import SimpleNamespace

import pytest

from tools import browser_skill_apply as forms, browser_skill_fixture as fixture
from tests.tools.test_browser_lever_fixture import observed
from tests.tools.test_browser_greenhouse_fixture import Tree
from tests.tools.test_ashby_upload_fixture import snapshot
from tests.tools.test_ashby_upload_unwrap import tree_pair


def test_empty_fixture_is_separate_required_blank_with_zero_height_box():
    original = fixture._greenhouse_form()
    empty = fixture._greenhouse_empty_placeholder_form()
    assert b'id="empty-placeholder"' not in original
    assert b'<div id="empty-placeholder" class="select__placeholder"></div>' in empty
    assert b'display:block;width:120px;height:0;padding:0;border:0' in empty
    assert empty.count(b'>Choose</div>') == original.count(b'>Choose</div>') - 1
    tree = Tree()
    tree.feed(empty.decode())
    def find(node, ident):
        return node if node['attrs'].get('id') == ident else next(
            (found for child in node['children'] if (found := find(child, ident))), None)
    v = find(tree.root, 'location-value')
    assert find(v, 'location')['attrs']['aria-required'] == 'true'
    assert v['children'][-1]['text'] == ''
    assert find(tree.root, 'location-label')['text'].strip()
    assert find(tree.root, 'additional')['tag'] == 'textarea'
    # Styles apply only while blank: the existing option click replaces this class.
    assert b"display.className='react-select__single-value'" in empty


STAGES = {
    'entry': 'Additional question', 'greenhouse': 'Additional question',
    'greenhouse_empty_placeholder': 'Additional question',
    'w_button_aria_label': 'unknown-frame-gate', 'ashby': 'direct-human-question',
    'ashby-upload': 'ashby-upload-attached-human-gate',
    'lever_custom_radio': 'Lever custom radio question',
    'lever_custom_checkbox': 'Lever custom checkbox question',
}


def traversal(state, *, premature=False):
    state.last_page = {'controls': [], 'inventory_verified': True, 'opaque_controls': False}
    state.combo_observations['Country'] = {'outcome': 'not_reached'}
    state.entry_wait = state.visible_badge_anchor = 'PASS'
    if state.greenhouse:
        # The traversal stubs IPC but must exercise the selected country/location
        # inventory through the same two production readers as fixture smoke.
        # Populating only combo_observations bypasses the inventory safety gate.
        from tests.tools.test_browser_greenhouse_selected_inventory import selected_snapshot
        state.combo_touched.add('Country')
        state.observe_page(selected_snapshot()['page'])
        state.last_page = {'controls': [], 'inventory_verified': True, 'opaque_controls': False}
    if state.custom_question:
        return observed(state, state.custom_question)
    if state.w_lookalike:
        state.entry_observations = [{'frames': ['unknown']}]
        return dict(status='gate', question='unknown/untrusted application frame')
    if state.ashby_upload:
        filename = fixture.Path(state.resume_path).name
        state.last_page['entry'] = {'upload_tree': dict(
            complete=True, attached=True, filename=filename, svgs=[])}
        state.last_page['controls'] = [dict(selector='#_systemfield_resume', files=[filename])]
        state.fills, state.decisions, state.ipc_index, state.upload_ipc = 2, 4, 3, 1
        state.uploaded = True
        if state.ashby_upload_unwrap:
            # The stubbed upload must retain the exact one-span shape proof.
            before, after = tree_pair()
            state.upload_before_tree = before['entry']['upload_tree']
            state.last_page['entry']['upload_tree'] = after['entry']['upload_tree']
            state.last_page['entry']['upload_tree']['filename'] = filename
        if state.ashby_upload_transient:
            # The aggregate traversal mocks the apply loop. Feed its real
            # fixture adjudicator the same three ordered pairs proven by the
            # separate facade-reader test; a missing trace must fail closed.
            first = snapshot(state, pending=True)
            first[1]['entry']['upload_tree'] = snapshot(state)[1]['entry']['upload_tree']
            pending = snapshot(state, pending=True)
            complete = snapshot(state, attached=True)
            state.upload_pair_trace = [
                {'page': page, 'fixed': fixed}
                for page, fixed in (first, pending, complete)]
        return dict(status='gate', form_question=dict(
            owner_kind='ashby-formless-root', selector='#additional'))
    if state.ashby:
        state.last_page['entry'] = {'formless': {'root': '#root', 'submit': '#send'}}
        return dict(status='gate', form_question=dict(
            owner_kind='ashby-formless-root', selector='#_systemfield_name',
            form_selector='#root', form_action='#send'))
    state.fills, state.decisions = 2, 8
    state.native_validity = dict.fromkeys(fixture._CHOICE_SELECTORS.values(), True)
    state.committed = {k: (fixture._FACTS[k], v) for k, v in fixture._MACHINE_VALUES.items()}
    state.combo_observations = {k: dict(outcome='committed', visible_text=fixture._FACTS[k],
                                      selected_value=v, hidden_value=v)
                                for k, v in fixture._MACHINE_VALUES.items()}
    state.combo_observations['Country'] = {'outcome': 'committed'}
    state.ipc_index = len(state._positive_ipc())
    state.input_values = {'#name': 'Test User', '#phone': fixture._FACTS['Phone']}
    state.last_page['controls'] = [dict(selector='#phone', value=fixture._FACTS['Phone'])]
    for key, selector in fixture._CHOICE_SELECTORS.items():
        answer = fixture._FACTS[key]
        target = dict(selector=selector, tag='input', type='combobox', label=key,
                      form_selector='#application-form', form_action=fixture.URL,
                      combobox=dict(mode='closed-react-select', phase='C2',
                                    hidden_selector=selector+'-code',
                                    **dict.fromkeys(('selected_text', 'displayed_text', 'selected_value', 'hidden_value'), answer)))
        state.last_page['controls'].append(target)
        state.widget_receipts.append(forms._ClosedReceipt(dict(
            selector=selector, approved_value=answer, committed_caption=answer,
            committed_value=answer, hidden_value=answer,
            hidden_selector=selector+'-code', hidden_value_before='',
            closed_admission=forms._closed_key(target))))
    return dict(status='gate', form_question={'selector': '#wrong' if premature else '#additional'})


@pytest.mark.parametrize('premature', [False, True])
def test_actual_all_leg_dispatch_reports_each_stage_and_safety(monkeypatch, capsys, premature):
    class Server:
        def __init__(self, address, handler):
            assert address == ('127.0.0.1', 18789)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def serve_forever(self): raise AssertionError('No socket/thread may run')
        def shutdown(self): pass
        def server_close(self): pass
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def join(self, timeout): assert timeout == 3
        def is_alive(self): return False
    async def apply(*args, fixture_context, **kwargs):
        return traversal(fixture_context, premature=premature)
    monkeypatch.setattr(fixture, 'HTTPServer', Server)
    monkeypatch.setattr(fixture.threading, 'Thread', Thread)
    monkeypatch.setattr(fixture, '_daemon_origin', lambda: True)
    monkeypatch.setattr(forms, 'apply', apply)
    assert fixture.run() == int(premature)
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[-1]['sessions'] == 9
    assert len(reports[-1]['legs']) == len(STAGES) == 8
    assert set(reports[-1]['legs']) == set(STAGES)
    assert {r['scenario'] for r in reports[:-1]} == set(STAGES)
    upload_reports = [r for r in reports[:-1] if r['scenario'] == 'ashby-upload']
    assert {r['variant'] for r in upload_reports} == {'ordinary', 'transient+unwrap'}
    for report in reports[:-1]:
        if report['scenario'] in {'greenhouse', 'greenhouse_empty_placeholder'}:
            choice = report['country_choice_inventory']
            assert choice is not None and choice['verified'] is True
            assert choice['opaque_count'] == 0 and choice['opaque_locators'] == []
            assert choice['country_phase'] == choice['location_phase'] == 'C2'
            assert choice['country_selected'] == fixture._FACTS['Country']
            assert choice['location_selected'] == fixture._FACTS['Where are you located?']
        else:
            assert report['country_choice_inventory'] is None
        failed = premature and report['scenario'] in {'entry', 'greenhouse', 'greenhouse_empty_placeholder'}
        assert report['final_stage'] == ('premature' if failed else STAGES[report['scenario']])
        assert report['safe_gate'] is (not failed)
        assert report['status'] == 'gate' and report['submitted'] is False
        assert report['submit']['ipc_returned'] is False
        assert report['submit']['post_attempts'] == 0 and report['submit']['receipts'] == []
        assert report['post_received'] is False
        if report['scenario'] == 'ashby-upload':
            assert report['upload_ipc'] == 1
            trace = report['upload_pair_trace']
            if report['variant'] == 'transient+unwrap':
                assert [pair['entry_agrees'] for pair in trace] == [False, True, True]
                assert [pair['page_complete'] for pair in trace] == [False, False, True]
                assert [pair['fixed_complete'] for pair in trace] == [True, False, True]
                assert all(pair['page_url'] == pair['fixed_url'] == fixture.URL
                           and pair['original_root_agrees']
                           and pair['page_inventory_verified'] for pair in trace)
                assert trace[-1]['page_attached'] is True
            else:
                assert trace == []
        else:
            assert report['upload_ipc'] == 0
    assert reports[-1]['legs'] == {
        name: ('FAIL' if premature and name in {
            'entry', 'greenhouse', 'greenhouse_empty_placeholder'} else 'PASS')
        for name in STAGES
    }


def test_fixed_route_serves_empty_leg_only_when_requested(monkeypatch, tmp_path):
    state = fixture._SyntheticRun(str(tmp_path / 'Synthetic-Resume.pdf'))
    state.greenhouse = state.greenhouse_empty_placeholder = True
    handler = object.__new__(fixture._FixtureHandler)
    handler.path, handler.headers = fixture._PATH, {'Host': '127.0.0.1:18789'}
    handler.server, handler.wfile = SimpleNamespace(synthetic_run=state), io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_GET()
    assert handler.wfile.getvalue() == fixture._greenhouse_empty_placeholder_form()
