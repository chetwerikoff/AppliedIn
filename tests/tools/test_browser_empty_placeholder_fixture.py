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
    'lever_custom_radio': 'Lever custom radio question',
    'lever_custom_checkbox': 'Lever custom checkbox question',
}


def traversal(state, *, premature=False):
    state.last_page = {'controls': [], 'inventory_verified': True, 'opaque_controls': False}
    state.combo_observations['Country'] = {'outcome': 'not_reached'}
    state.entry_wait = state.visible_badge_anchor = 'PASS'
    if state.custom_question:
        return observed(state, state.custom_question)
    if state.w_lookalike:
        state.entry_observations = [{'frames': ['unknown']}]
        return dict(status='gate', question='unknown/untrusted application frame')
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
    assert reports[-1]['sessions'] == len(STAGES)
    assert {r['scenario'] for r in reports[:-1]} == set(STAGES)
    for report in reports[:-1]:
        failed = premature and report['scenario'] in {'entry', 'greenhouse', 'greenhouse_empty_placeholder'}
        assert report['final_stage'] == ('premature' if failed else STAGES[report['scenario']])
        assert report['safe_gate'] is (not failed)
        assert report['status'] == 'gate' and report['submitted'] is False
        assert report['submit']['ipc_returned'] is False
        assert report['submit']['post_attempts'] == 0 and report['submit']['receipts'] == []
        assert report['post_received'] is False


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
