"""Sealed fixture wiring proof; all browser/HTTP edges are offline doubles."""
from pathlib import Path
from copy import deepcopy
import json
import re
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_formless import (
    observed, resume_set_name, set_resume_files, RootSession)
from tests.tools.test_browser_skill_fixture import authority


def is_set(method, args):
    return method == 'evaluate' and len(args) == 1 and resume_set_name(args[0]) is not None


def snapshot(authority, attached=False, pending=False):
    name = Path(authority.resume_path).name
    changes = ({'attached': True, 'files': [name], 'displayName': name}
               if attached else {'disabledDrop': True, 'files': [name]} if pending else {})
    pair = observed(**changes)
    for page in pair:
        page['url'] = fixture.URL
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#dropzone':
                c['selector'] = '#ashby-upload-dropzone'
        for n in page['entry']['upload_tree']['nodes']:
            if n.get('target_selector') == '#dropzone':
                n['target_selector'] = '#ashby-upload-dropzone'
    return pair


class FixtureRaw(RootSession):
    def __init__(self, authority):
        super().__init__(*snapshot(authority))
        self.authority = authority
        self.uploads = []

    async def call(self, method, *args, **kwargs):
        if not is_set(method, args):
            return await super().call(method, *args, **kwargs)
        name = resume_set_name(args[0])
        self.uploads.append((('set', name), kwargs))
        after = snapshot(self.authority, attached=True)
        for old, new in zip((self.current, self.fixed), after):
            lookup = {c['selector']: c for c in old['controls'] + old['entry']['root_evidence']}
            for c in new['controls'] + new['entry']['root_evidence']:
                for key in ('value', 'has_value', 'required'):
                    if key in lookup.get(c['selector'], {}):
                        c[key] = lookup[c['selector']][key]
        self.current, self.fixed = after
        return {'value': {'set': True, 'files': [name]}}


async def test_separate_sealed_grants_reach_existing_next_human_gate(authority, monkeypatch):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = FixtureRaw(authority)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    result = await forms.apply(fixture.URL, fixture.COMPANY, dict(fixture._FACTS),
                               'synthetic-fixture', pk=fixture.PK,
                               resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate', result
    assert result['form_question']['selector'] == '#additional', result
    assert result['form_question']['owner_kind'] == 'ashby-formless-root'
    assert authority.entry_wait == 'PASS'
    assert authority.fills == 2 and authority.ipc_index == 3
    assert authority.upload_ipc == 1 and authority.uploaded
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert raw.uploads == [(('set', Path(authority.resume_path).name), {})]
    assert set(authority.row['human_approved_answers']) == {'Name', 'Email'}


@pytest.mark.parametrize('attempt', ['chooser', 'other-file', 'click', 'repeat'])
async def test_fixture_wrapper_admits_only_the_exact_direct_set(authority, attempt):
    # The loopback leg must exercise the production call, so a chooser upload
    # or a set carrying any other bytes is refused, never simulated.
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.navigations = 1
    authority.ipc_index = 2
    raw = FixtureRaw(authority)
    facade = authority.facade(raw)
    name = Path(authority.resume_path).name
    args = ('evaluate', forms._resume_file_set_script(name, fixture._RESUME_BYTES))
    if attempt == 'chooser':
        args = ('upload', '#_systemfield_resume', '--file', authority.resume_path)
    elif attempt == 'other-file':
        args = ('evaluate', forms._resume_file_set_script(name, b'%PDF-1.4 other'))
    elif attempt == 'click':
        args = ('click', '#ashby-upload-dropzone')
    else:
        await facade.call(*args)
    before = len(raw.uploads)
    with pytest.raises(PermissionError):
        await facade.call(*args)
    assert len(raw.uploads) == before


def test_all_fixture_legs_are_unconditional(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(fixture, '_run_scenario', lambda **kwargs: calls.append(kwargs) or 0)
    assert fixture.run() == 0
    assert calls == [{}, {'greenhouse': True}, {'greenhouse_empty_placeholder': True},
                     {'w_lookalike': True}, {'ashby': True},
                     {'ashby_upload': True},
                     {'custom_question': 'radio'}, {'custom_question': 'checkbox'}]
    report = json.loads(capsys.readouterr().out)
    assert report['sessions'] == len(calls) == 8
    assert len(report['legs']) == 8
    assert set(report['legs']) == {
        'entry', 'greenhouse', 'greenhouse_empty_placeholder',
        'w_button_aria_label', 'ashby', 'ashby-upload',
        'lever_custom_radio', 'lever_custom_checkbox',
    }
    assert set(report['legs'].values()) == {'PASS'}


def test_real_local_handler_opens_chooser_and_renders_only_passive_item():
    original = fixture._ashby_form()
    assert b'Application answer' in original
    assert b'addEventListener' not in original
    document = fixture._ashby_upload_form().decode()
    script = re.search(r'<script>(.*?)</script>', document, re.S).group(1)
    # The handler observes files from the chooser; it never fabricates FileList.
    assert not re.search(r'input\.files\s*=', script)
    harness = r'''
const handlers={}, elements=[];
const make=tag=>({tag,children:[],append(...nodes){this.children.push(...nodes);},setAttribute(k,v){this[k]=v;}});
let settle, delay;global.setTimeout=(fn,ms)=>{settle=fn;delay=ms;};
const icon={remove(){drop.iconRemoved=true;}};
const file=make('div');file.insertBefore=(n,other)=>{if(other!==drop)throw Error('wrong container');file.children.unshift(n);};
const input={files:[],parentElement:file,click(){this.clicked=(this.clicked||0)+1;},addEventListener(e,fn){handlers[e]=fn;}};
const text={textContent:'Upload'};
const inner={childNodes:[text],replaceWith(...nodes){if(nodes.length!==1||nodes[0]!==text)throw Error('text changed');drop.unwrapped=true;}};
const drop={addEventListener(e,fn){handlers['drop-'+e]=fn;},querySelector(q){if(q==='svg')return icon;if(q==='span > span')return inner;throw Error('unexpected target');}};
global.document={getElementById:id=>id==='_systemfield_resume'?input:drop,createElement:make,
 createElementNS:(ns,tag)=>({...make(tag),namespaceURI:ns})};
eval(process.argv[1]);handlers['drop-click']();
if(input.clicked!==1 || input.files.length)throw Error('chooser was bypassed');
input.files=[{name:'Synthetic-Resume.pdf'}];handlers.change();
if(!drop.disabled || delay!==1800 || file.children.length)throw Error('missing disabled pending window');
settle();if(drop.disabled)throw Error('dropzone stayed disabled');
const item=file.children[0];
console.log(JSON.stringify({class:item.className,names:item.children.map(n=>n.className),
 name:item.children[0].children[1].textContent,title:item.children[1].title,
 nameChildren:item.children[0].children.map(n=>n.tag),
 iconNS:item.children[0].children[0].namespaceURI,
 text:item.children[1].textContent||'',iconRemoved:drop.iconRemoved,unwrapped:!!drop.unwrapped}));
'''
    result = subprocess.run(['node', '-e', harness, script], capture_output=True, text=True, check=True)
    proof = json.loads(result.stdout)
    assert proof == {'class': 'ashby-application-form-input-file-item',
                     'names': ['ashby-application-form-input-file-item-name',
                               'ashby-application-form-input-file-item-delete'],
                     'name': 'Synthetic-Resume.pdf', 'title': 'Remove attachment',
                     'nameChildren': ['svg', 'span'],
                     'iconNS': 'http://www.w3.org/2000/svg',
                     'text': '', 'iconRemoved': True, 'unwrapped': False}


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('recovery', [False, True])
async def test_post_upload_interruption_is_terminal_before_cancellation(authority, monkeypatch, cancel, recovery):
    if recovery:
        _script_recovery_decisions(monkeypatch)
    import asyncio
    from core.apply_queue import is_retryable
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = FixtureRaw(authority)
    reached = asyncio.Event()
    original = raw.call
    async def interrupted(method, *args, **kwargs):
        if method == 'evaluate' and raw.uploads:
            reached.set()
            if cancel:
                await asyncio.Future()
            raise OSError('synthetic post-upload fault')
        return await original(method, *args, **kwargs)
    raw.call = interrupted
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    task = asyncio.create_task(forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority))
    if cancel:
        await asyncio.wait_for(reached.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = {'status': authority.row.get('fail_kind')}
        detail = authority.row.get('fail_reason')
    else:
        result = await task
        detail = result.get('question')
    # A fault after the set call may have attached the file, so both the
    # cancellation and the failed readback stay uncertain, never retried.
    assert result['status'] == 'uncertain'
    assert (detail or result.get('detail')) == (
        'Résumé upload interrupted after the browser command; inspect manually')
    assert is_retryable(result) == (False, 'uncertain')
    assert len(raw.uploads) == 1


def _update_read_values(raw, pair):
    # The transition may alter upload shape, not previously approved answers.
    for old, new in zip((raw.current, raw.fixed), pair):
        lookup = {c['selector']: c for c in old['controls'] + old['entry']['root_evidence']}
        for c in new['controls'] + new['entry']['root_evidence']:
            if c.get('type') == 'file':
                continue
            for key in ('value', 'has_value'):
                if key in lookup.get(c['selector'], {}):
                    c[key] = lookup[c['selector']][key]
    raw.current, raw.fixed = deepcopy(pair)


WAIT = {'action': 'wait'}


def _late_resume_field(pages):
    """FF-224e: the résumé field renders after the first decision.

    The one server-owned upload runs just before the model's first decision.
    When the résumé input has not rendered by then, neither real reader sees
    it, so only the model-intent fallbacks can upload. The returned callable
    renders the input (and its upload tree) on both readers.
    """
    saved = []
    for page in pages:
        held = {}
        for key, items in (('controls', page['controls']),
                           ('root_evidence', page['entry']['root_evidence'])):
            held[key] = [(i, c) for i, c in enumerate(items)
                         if c['selector'] == forms._ASHBY_RESUME_SELECTOR]
            items[:] = [c for c in items if c['selector'] != forms._ASHBY_RESUME_SELECTOR]
        held['tree'] = page['entry']['upload_tree']
        page['entry']['upload_tree'] = None
        saved.append(held)

    def render():
        for page, held in zip(pages, saved):
            for key, items in (('controls', page['controls']),
                               ('root_evidence', page['entry']['root_evidence'])):
                for i, c in held[key]:
                    items.insert(i, c)
            page['entry']['upload_tree'] = held['tree']

    return render


def _admit_optional_owner_question(raw):
    # This synthetic form has no pending owner question; the next model
    # decision must be reachable after a proven upload, not inferred from Gate.
    for page in (raw.current, raw.fixed):
        for item in page['controls'] + page['entry']['root_evidence']:
            if item['selector'] == '#additional':
                item['required'] = False


def _script_recovery_decisions(monkeypatch, *, first='submit',
                               on_submit=None, timeline=None, after_upload='submit',
                               upload_decisions=1, tasks=None, on_first_decision=None):
    """Exercise the sealed two-fill IPC prefix before any missing-proof Submit."""
    seen = []
    actions = [
        {'action': 'fill', 'selector': '#_systemfield_name', 'fact': 'Name'},
        {'action': 'fill', 'selector': '#_systemfield_email', 'fact': 'Email'},
    ]
    if first == 'submit':
        actions.extend([
            {'action': 'submit', 'selector': '#structural-submit'},
            {'action': 'gate', 'question': 'Synthetic inspection without a second click'},
        ])
    else:
        actions.extend([
            {'action': 'upload', 'selector': '#_systemfield_resume'}
        ] * upload_decisions)
        actions.append(
            {'action': 'submit', 'selector': '#structural-submit'}
            if after_upload == 'submit'
            else {'action': 'gate', 'question': 'Synthetic next decision'})
        if after_upload == 'submit':
            actions.append({'action': 'gate', 'question':
                            'Synthetic inspection without a second click'})

    async def decide(self, task, page, history, *, model):
        assert model == 'synthetic-fixture'
        index = self.decisions
        assert index < len(actions), 'unexpected automatic retry or extra decision'
        action = actions[index]
        if index == 0 and on_first_decision:
            on_first_decision()
        if index == 2 and first == 'submit':
            assert self.ipc_index == 2 and not self.uploaded
            if on_submit:
                on_submit()
        if index == 3 and first == 'submit':
            assert any(item.get('action') == 'upload' and item.get('uploaded') is True
                       for item in history)
        seen.append((action['action'], self.upload_ipc,
                     'résumé attachment verified=True' in task))
        if tasks is not None:
            tasks.append(task)
        if timeline is not None:
            timeline.append(f'decision{index}')
        self.decisions += 1
        return action

    monkeypatch.setattr(fixture._SyntheticRun, 'decision', decide)
    return seen


async def _run_recovery(authority, monkeypatch, *, raw=None, first='submit',
                        on_submit=None, timeline=None, admit_owner=False):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = raw or FixtureRaw(authority)
    if admit_owner:
        _admit_optional_owner_question(raw)
    seen = _script_recovery_decisions(
        monkeypatch, first=first, on_submit=on_submit, timeline=timeline)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    return result, raw, seen


async def test_submit_without_current_run_upload_recovers_one_empty_ashby_receiver(
        authority, monkeypatch):
    raw = FixtureRaw(authority)
    for page in (raw.current, raw.fixed):
        page['text'] = 'Earlier displayed résumé: prior-file.pdf'
    timeline, receipts = [], []
    original_call = raw.call

    async def traced(method, *args, **kwargs):
        if method in {'fill', 'click'} or is_set(method, args):
            timeline.append('upload' if method == 'evaluate' else method)
        if method == 'evaluate' and args == (forms.ENTRY_PAGE,) and receipts:
            timeline.append('page-after-proof')
        return await original_call(method, *args, **kwargs)

    raw.call = traced
    original_execute = forms.execute

    async def record_receipt(*args, **kwargs):
        receipt = await original_execute(*args, **kwargs)
        if receipt.get('uploaded'):
            receipts.append(receipt)
            timeline.append('upload-proof')
        return receipt

    monkeypatch.setattr(forms, 'execute', record_receipt)
    result, _, seen = await _run_recovery(
        authority, monkeypatch, raw=raw, admit_owner=True, timeline=timeline)
    assert [item for item in timeline if item in {
        'decision0', 'decision1', 'decision2', 'decision3',
        'fill', 'upload', 'click', 'upload-proof'}] == [
            'decision0', 'fill', 'decision1', 'fill',
            'decision2', 'upload', 'upload-proof', 'decision3']
    assert timeline.index('upload-proof') < timeline.index('page-after-proof')
    assert timeline.index('page-after-proof') < timeline.index('decision3')
    assert seen == [('fill', 0, False), ('fill', 0, False),
                    ('submit', 0, False), ('gate', 1, True)]
    assert len(receipts) == 1
    assert receipts[0]['action'] == 'upload' and receipts[0]['uploaded'] is True
    assert re.search(r'resume|résumé|cv', receipts[0]['label'], re.I)
    assert raw.uploads == [(('set', Path(authority.resume_path).name), {})]
    assert authority.fills == 2 and authority.ipc_index == 3
    assert authority.uploaded and authority.upload_ipc == 1
    assert result['status'] == 'gate', result
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert not [call for call in raw.calls if call[0] == 'click']


@pytest.mark.parametrize('kind,refusal', [
    ('duplicate', 'missing unique labelled resume receiver'),
    ('wrong-label', 'missing unique labelled resume receiver'),
    ('disabled', 'application label/control association or editability changed'),
    ('wrong-type', 'missing unique labelled resume receiver'),
    ('missing', 'missing unique labelled resume receiver'),
])
async def test_late_submit_receiver_selection_rejects_ambiguous_or_wrong_label(
        authority, monkeypatch, kind, refusal):
    raw = FixtureRaw(authority)

    def late_receiver_change():
        for page in (raw.current, raw.fixed):
            file_input = next(c for c in page['controls']
                              if c['selector'] == '#_systemfield_resume')
            if kind == 'duplicate':
                duplicate = deepcopy(file_input)
                duplicate['selector'] = '#other-resume'
                page['controls'].append(duplicate)
            elif kind == 'missing':
                for key in ('controls',):
                    page[key] = [c for c in page[key] if c['selector'] != '#_systemfield_resume']
                page['entry']['root_evidence'] = [
                    c for c in page['entry']['root_evidence']
                    if c['selector'] != '#_systemfield_resume']
            else:
                for c in page['controls'] + page['entry']['root_evidence']:
                    if c['selector'] == '#_systemfield_resume':
                        if kind == 'wrong-label':
                            # The independently observed question may still say
                            # Resume; change both semantic captions to be wrong.
                            c['label'] = c['question'] = 'Supporting document'
                        elif kind == 'disabled':
                            c['disabled'] = True
                        else:
                            c['type'] = 'text'

    result, _, seen = await _run_recovery(
        authority, monkeypatch, raw=raw, on_submit=late_receiver_change)
    assert [action for action, _, _ in seen] == ['fill', 'fill', 'submit']
    assert result['status'] == 'gate', result
    assert refusal in result['question']
    assert authority.upload_ipc == len(raw.uploads) == 0
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0


@pytest.mark.parametrize('transport', ['fault', 'cancel'])
async def test_recovery_set_transport_error_is_uncertain_without_replay(
        authority, monkeypatch, transport):
    import asyncio
    raw = FixtureRaw(authority)
    original = raw.call

    async def broken(method, *args, **kwargs):
        if is_set(method, args):
            raw.uploads.append((('set', resume_set_name(args[0])), kwargs))
            if transport == 'cancel':
                raise asyncio.CancelledError()
            raise OSError('synthetic transport fault')
        return await original(method, *args, **kwargs)

    raw.call = broken
    # The set call may have reached the page, so a fault or a cancellation in
    # it ends the run uncertain rather than as a repeatable Gate.
    if transport == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await _run_recovery(authority, monkeypatch, raw=raw)
        assert authority.row.get('fail_kind') == 'uncertain'
    else:
        result, _, seen = await _run_recovery(authority, monkeypatch, raw=raw)
        assert [action for action, _, _ in seen] == ['fill', 'fill', 'submit']
        assert result['status'] == 'uncertain', result
    assert len(raw.uploads) == authority.upload_ipc == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0


async def test_already_proven_resume_submit_does_not_replay_upload(
        authority, monkeypatch):
    result, raw, seen = await _run_recovery(
        authority, monkeypatch, first='upload', admit_owner=True)
    assert [action for action, _, _ in seen] == ['fill', 'fill', 'upload', 'submit']
    assert seen[-1][1:] == (1, True)
    assert result['status'] == 'gate', result  # Existing final guard, no fixture Submit.
    assert len(raw.uploads) == authority.upload_ipc == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0


async def test_recovery_proof_reenters_bootstrap_before_another_decision(
        authority, monkeypatch):
    raw = FixtureRaw(authority)
    original_execute = forms.execute
    proven = []

    async def change_next_page_after_guarded_readback(*args, **kwargs):
        receipt = await original_execute(*args, **kwargs)
        if receipt.get('uploaded'):
            proven.append(receipt)
            # A changed structural Submit is rejected by the next real
            # _bootstrap/_formless_boundary, not a fixture facade permission check.
            for page in (raw.current, raw.fixed):
                page['entry']['formless']['submit'] = '#changed-structural-submit'
        return receipt

    monkeypatch.setattr(forms, 'execute', change_next_page_after_guarded_readback)
    result, _, seen = await _run_recovery(
        authority, monkeypatch, raw=raw, admit_owner=True)
    assert len(proven) == 1 and proven[0]['uploaded'] is True
    assert [action for action, _, _ in seen] == ['fill', 'fill', 'submit']
    assert result['status'] == 'gate', result
    assert 'exact structural submit changed; inspect manually.' in result['question']
    assert len(raw.uploads) == authority.upload_ipc == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0


def mismatch_snapshot(authority, *, attached=False, pending=False, changes=None):
    """Actual PAGE/fixed DOM readers, never a manually fabricated upload tree."""
    name = Path(authority.resume_path).name
    params = ({'attached': True, 'files': [name], 'displayName': name}
              if attached else {'disabledDrop': True, 'files': [name]} if pending else {})
    pair = observed(movePane=True, **(changes or {}), **params)
    for page in pair:
        page['url'] = fixture.URL
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#dropzone':
                c['selector'] = '#ashby-upload-dropzone'
        for n in page['entry']['upload_tree']['nodes']:
            if n.get('target_selector') == '#dropzone':
                n['target_selector'] = '#ashby-upload-dropzone'
    return pair


class MismatchFixtureRaw(FixtureRaw):
    def __init__(self, authority):
        super().__init__(authority)
        self.current, self.fixed = mismatch_snapshot(authority)

    async def call(self, method, *args, **kwargs):
        if not is_set(method, args):
            return await RootSession.call(self, method, *args, **kwargs)
        self.uploads.append((('set', resume_set_name(args[0])), kwargs))
        after = mismatch_snapshot(self.authority, attached=True)
        for old, new in zip((self.current, self.fixed), after):
            lookup = {c['selector']: c for c in old['controls'] + old['entry']['root_evidence']}
            for c in new['controls'] + new['entry']['root_evidence']:
                for key in ('value', 'has_value', 'required'):
                    if c.get('type') != 'file' and key in lookup.get(c['selector'], {}):
                        c[key] = lookup[c['selector']][key]
        self.current, self.fixed = after
        return {'value': {'set': True, 'files': [Path(self.authority.resume_path).name]}}


def _bind_mismatch_fixture_producer(authority, monkeypatch, *, transport='effect'):
    """Test-local real controller bound to the existing sealed fixture delegate."""
    from tools.browser_skill_apply import ApplicationIPCController

    original_facade = fixture._SyntheticRun.facade

    def closed_with_controller(self, raw):
        sealed = original_facade(self, raw)
        controller = ApplicationIPCController(
            fixture.PK, fixture.COMPANY, fixture.URL, self.resume_path,
            dict(fixture._FACTS), {}, [], {'url': fixture.URL}, lambda: None,
            fixture_context=self, entry_provenance=self.entry_provenance())

        class Proxy:
            def __getattribute__(self, key):
                if key == 'application_effect' and transport == 'call':
                    raise AttributeError(key)
                return object.__getattribute__(self, key)

            _sealed_delegate = sealed
            _original_raw = raw
            _application_controller = controller

            async def page(self):
                return (await sealed.call('evaluate', forms.ENTRY_PAGE))['value']

            async def call(self, *args, **kwargs):
                if args and is_set(args[0], args[1:]):
                    if transport == 'call':
                        return await self._guarded_upload(*args, **kwargs)
                    # Make both guarded transport methods run through the
                    # identical one-shot test-local controller.
                    raise PermissionError('Unexpected unmediated fixture upload')
                return await sealed.call(*args, **kwargs)

            async def _guarded_upload(self, *args, **kwargs):
                await controller.before_ipc(self, args, kwargs)
                succeeded = False
                try:
                    result = await sealed.call(*args, **kwargs)
                    succeeded = True
                    return result
                finally:
                    await controller.after_ipc(
                        self, args, kwargs, succeeded=succeeded)

            async def application_effect(self, *args, **kwargs):
                if args and is_set(args[0], args[1:]):
                    return await self._guarded_upload(*args, **kwargs)
                return await sealed.call(*args, **kwargs)

        return Proxy()

    monkeypatch.setattr(fixture._SyntheticRun, 'facade', closed_with_controller)


def _seed_mismatch_synthetic_owner_grants(authority, raw):
    """Offline owner-approved facts bind to their exact original PAGE receivers.

    The unrequested optional textarea is already valued before this scenario;
    the mismatch route otherwise correctly asks its owner before model planning.
    No production grant/approval guard is patched or bypassed here.
    """
    for page in (raw.current, raw.fixed):
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#additional':
                c['value'] = 'Synthetic supplied context'
                c['has_value'] = True
    for key, selector in [('Name', '#_systemfield_name'),
                          ('Email', '#_systemfield_email')]:
        original = next(c for c in raw.current['controls'] if c['selector'] == selector)
        target = forms._bound_target(raw.current, original)
        grant = {field: target.get(field) for field in
                 ('selector', 'label', 'question', 'tag', 'type',
                  'form_selector', 'form_action')}
        grant.update(value=fixture._FACTS[key], url=fixture.URL)
        assert grant['label'] == key
        authority.row['human_approved_answers'][key] = grant


@pytest.mark.parametrize('transport', ['effect', 'call'])
async def test_reversible_submit_recovers_one_native_input_upload_then_next_gate(
        authority, monkeypatch, transport):
    """Submit-time fallback: the résumé field rendered after the first
    decision, so the Submit decision takes the one guarded upload."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    # Recovery enters the production same-job check at the guarded IPC edge.
    authority.row['jd_url'] = fixture.URL
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _seed_mismatch_synthetic_owner_grants(authority, raw)
    _script_recovery_decisions(
        monkeypatch, on_first_decision=_late_resume_field((raw.current, raw.fixed)))
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate', result
    assert 'Synthetic inspection without a second click' in result['question'], result
    assert authority.fills == 2 and authority.upload_ipc == 1
    assert authority.ipc_index == 3 and authority.uploaded
    assert raw.uploads == [(('set', Path(authority.resume_path).name), {})]
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert authority.decisions == 4
    assert not any(call[0] == 'click' for call in raw.calls)


@pytest.mark.parametrize('transport', ['effect', 'call'])
async def test_reversible_model_upload_decision_runs_the_one_shot_recovery(
        authority, monkeypatch, transport):
    """Issue #230: the model never chooses Submit while the résumé is empty, so
    deferring its upload decision to Submit looped into the repeat gate live.
    The decision itself now takes the one guarded recovery upload. FF-224e:
    this is the fallback for a résumé field rendered after the first decision."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.row['jd_url'] = fixture.URL
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _seed_mismatch_synthetic_owner_grants(authority, raw)
    tasks = []
    _script_recovery_decisions(
        monkeypatch, first='upload', after_upload='gate', tasks=tasks,
        on_first_decision=_late_resume_field((raw.current, raw.fixed)))
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate' and 'Synthetic next decision' in result['question']
    assert 'résumé attachment verified=True' in tasks[3]
    assert 'runs before Submit' not in tasks[3]
    assert authority.fills == 2 and authority.upload_ipc == 1 and authority.uploaded
    assert raw.uploads == [(('set', Path(authority.resume_path).name), {})]
    assert authority.decisions == 4
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert not any(call[0] == 'click' for call in raw.calls)


@pytest.mark.parametrize('transport', ['effect', 'call'])
async def test_repeated_model_upload_after_recovery_never_uploads_twice(
        authority, monkeypatch, transport):
    """After the one recovery upload, further model upload decisions only get
    feedback, never a second chooser, and three of them still gate. The
    résumé field renders after the first decision (FF-224e fallback shape)."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.row['jd_url'] = fixture.URL
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _seed_mismatch_synthetic_owner_grants(authority, raw)
    tasks = []
    _script_recovery_decisions(
        monkeypatch, first='upload', upload_decisions=4, after_upload='gate', tasks=tasks,
        on_first_decision=_late_resume_field((raw.current, raw.fixed)))
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate'
    assert ('Repeated model résumé upload decisions after the server-owned upload'
            in result['question'])
    assert authority.decisions == 6 and len(tasks) == 6
    assert 'The résumé is already attached by the server' in tasks[4]
    assert authority.fills == 2 and authority.upload_ipc == 1
    assert len(raw.uploads) == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert not any(call[0] == 'click' for call in raw.calls)


@pytest.mark.parametrize('transport', ['effect', 'call'])
async def test_reversible_upload_feedback_then_submit_recovery_never_clicks_submit1(
        authority, monkeypatch, transport):
    """After the upload decision's recovery, Submit is no second upload: it
    meets the final guard with the PDF proven, which still refuses here on a
    later check of this fixture, and nothing is clicked. The résumé field
    renders after the first decision (FF-224e fallback shape)."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.row['jd_url'] = fixture.URL
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _seed_mismatch_synthetic_owner_grants(authority, raw)
    _script_recovery_decisions(
        monkeypatch, first='upload', after_upload='submit',
        on_first_decision=_late_resume_field((raw.current, raw.fixed)))
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert authority.decisions == 4 and authority.fills == 2
    assert authority.upload_ipc == 1 and authority.ipc_index == 3
    assert len(raw.uploads) == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert not any(call[0] == 'click' for call in raw.calls)


PRIOR_RUN_PDF = 'Earlier Synthetic Resume.pdf'


def _resumed_reversible_raw(authority, *, prior=None):
    """A resumed reversible form: every other field was filled before this run.

    With prior, the real readers also see a passive attachment that an earlier
    run left behind. Neither the filename nor the attachment is current-run
    proof; only the guarded chooser below may produce that.
    """
    raw = MismatchFixtureRaw(authority)
    if prior:
        raw.current, raw.fixed = mismatch_snapshot(authority, changes={
            'attached': True, 'files': [prior], 'displayName': prior})
    _seed_mismatch_synthetic_owner_grants(authority, raw)
    for page in (raw.current, raw.fixed):
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_name':
                c.update(value=fixture._FACTS['Name'], has_value=True)
            elif c['selector'] == '#_systemfield_email':
                c.update(value=fixture._FACTS['Email'], has_value=True)
    return raw


async def _resumed_submit_first(authority, monkeypatch, *, raw=None, prior=None,
                                transport='effect', actions=None, prepare=None,
                                on_first_decision=None):
    """Decision zero is Submit: no fill, no model upload precedes it."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.row['jd_url'] = fixture.URL
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    # The sealed effect sequence is only the native résumé chooser: a
    # prefilled form has nothing for this run to fill.
    monkeypatch.setattr(fixture._SyntheticRun, '_positive_ipc', lambda self: (
        ('evaluate', self.resume_set_script()),))
    controllers = []
    bound = fixture._SyntheticRun.facade

    def observed_facade(self, inner):
        proxy = bound(self, inner)
        controllers.append(proxy._application_controller)
        if prepare:
            prepare(proxy._application_controller)
        return proxy

    monkeypatch.setattr(fixture._SyntheticRun, 'facade', observed_facade)
    raw = raw or _resumed_reversible_raw(authority, prior=prior)
    actions = actions or [
        {'action': 'submit', 'selector': '#structural-submit'},
        {'action': 'gate', 'question': 'Synthetic next decision'}]
    timeline = []
    original_call = raw.call

    async def traced(method, *args, **kwargs):
        if method in {'fill', 'click'} or is_set(method, args):
            timeline.append('upload' if method == 'evaluate' else method)
        return await original_call(method, *args, **kwargs)

    raw.call = traced

    async def decide(self, task, page, history, *, model):
        assert model == 'synthetic-fixture'
        index = self.decisions
        assert index < len(actions), 'unexpected automatic retry or extra decision'
        timeline.append((f'decision{index}', self.upload_ipc,
                         'résumé attachment verified=True' in task))
        if index == 0 and on_first_decision:
            on_first_decision((raw.current, raw.fixed))
        self.decisions += 1
        return actions[index]

    monkeypatch.setattr(fixture._SyntheticRun, 'decision', decide)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    return result, raw, timeline, controllers


def _no_commit(authority, raw):
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert not authority.hold and not authority.row.get('possible_submission')
    assert not any(call[0] == 'click' for call in raw.calls)


@pytest.mark.parametrize('transport', ['effect', 'call'])
@pytest.mark.parametrize('prior', [None, PRIOR_RUN_PDF])
async def test_resumed_prefilled_form_uploads_once_before_the_first_decision(
        authority, monkeypatch, prior, transport):
    """Issues #224 and FF-224e: a resumed, prefilled form.

    On the base revision a retained earlier-run attachment refused in the
    recovery preflight ("original empty résumé input changed"). Live, the
    model then kept finding routes to the PDF guard before any upload. Both
    receivers now take exactly one current-run chooser upload before the
    model's first decision, which already sees the verified attachment.
    """
    result, raw, timeline, controllers = await _resumed_submit_first(
        authority, monkeypatch, prior=prior, transport=transport,
        actions=[{'action': 'gate', 'question': 'Synthetic next decision'}])
    assert result['status'] == 'gate', result
    assert 'Synthetic next decision' in result['question']
    assert timeline == ['upload', ('decision0', 1, True)]
    assert raw.uploads == [(('set', Path(authority.resume_path).name), {})]
    assert authority.upload_ipc == 1 and authority.uploaded
    assert authority.fills == 0 and authority.decisions == 1
    # The raw producer, not the passive item, owns the current-run proof.
    assert len(controllers) == 1
    guard = controllers[0]
    assert guard.upload_attempted and guard.upload_completed
    assert not guard.upload_pending and guard.upload_identity == guard._row_pdf()
    _no_commit(authority, raw)


@pytest.mark.parametrize('prior', [None, PRIOR_RUN_PDF])
async def test_resumed_second_submit_still_meets_the_unchanged_final_guard(
        authority, monkeypatch, prior):
    """The upload is not authorization: the Submit after it goes through the
    full existing final guard, which here refuses on the fixture's own
    invariant rather than inheriting permission from the upload."""
    result, raw, timeline, _ = await _resumed_submit_first(
        authority, monkeypatch, prior=prior, actions=[
            {'action': 'submit', 'selector': '#structural-submit'}])
    assert timeline == ['upload', ('decision0', 1, True)]
    assert result['status'] == 'gate', result
    # Raised inside ApplicationIPCController.authorize_final_submit: the
    # synthetic row has no canonical job id, an invariant unrelated to upload.
    assert result['question'].startswith(
        'Current canonical job ID changed before Submit. Last button: Submit application.')
    assert len(raw.uploads) == authority.upload_ipc == 1
    _no_commit(authority, raw)


# The proactive upload takes only an empty receiver or one retained,
# differently named PDF; any other receiver is left to the Submit decision,
# whose recovery refuses it the same way.
@pytest.mark.parametrize('state', ['attempted', 'pending', 'identity'])
async def test_resumed_earlier_current_run_attempt_refuses_new_upload(
        authority, monkeypatch, state):
    def earlier_attempt(guard):
        if state == 'attempted':
            guard.upload_attempted = True
        elif state == 'pending':
            guard.upload_pending = True
        else:
            guard.upload_identity = guard._row_pdf()

    result, raw, timeline, _ = await _resumed_submit_first(
        authority, monkeypatch, prior=PRIOR_RUN_PDF, prepare=earlier_attempt)
    assert result['status'] == 'gate', result
    assert timeline == []
    assert raw.uploads == [] and authority.upload_ipc == 0
    _no_commit(authority, raw)


@pytest.mark.parametrize('prior', [None, PRIOR_RUN_PDF])
async def test_resumed_upload_transport_fault_is_uncertain_without_replay(
        authority, monkeypatch, prior):
    """A transport fault in the upload before the first decision ends the run
    uncertain, exactly as a fault in the Submit-time recovery did."""
    raw = _resumed_reversible_raw(authority, prior=prior)
    original = raw.call

    async def broken(method, *args, **kwargs):
        if is_set(method, args):
            raw.uploads.append((('set', resume_set_name(args[0])), kwargs))
            raise OSError('synthetic transport fault')
        return await original(method, *args, **kwargs)

    raw.call = broken
    result, raw, timeline, controllers = await _resumed_submit_first(
        authority, monkeypatch, raw=raw)
    assert result['status'] == 'uncertain', result
    assert timeline == ['upload']
    assert len(raw.uploads) == 1
    assert controllers[0].upload_attempted and not controllers[0].upload_completed
    _no_commit(authority, raw)


def _production_submit_click(monkeypatch, tmp_path, *, upload=None):
    from tests.tools.test_browser_application_entry import PK, COMPANY, posting
    from tests.tools.test_browser_formless import root_world
    from tests.tools.test_browser_controller_support import install_controller_session

    def prefilled(snapshots):
        for snapshot in snapshots:
            for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                if c.get('type') not in {'file', 'submit'} and c.get('tag') != 'button':
                    c['value'] = 'Synthetic value'
                    if 'has_value' in c:
                        c['has_value'] = True

    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    row, raw, decide, _ = root_world(monkeypatch, movePane=True)
    prefilled((raw.current, raw.fixed))
    transport, _, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    button = next(c for c in raw.current['controls'] if c['label'] == 'Submit application')
    original_call = raw.call
    uploads = []

    async def call(method, *args, **kwargs):
        if method == 'evaluate' and (name := resume_set_name(args[0])):
            uploads.append(('set', name))
            raw.calls.append(('set', name))
            if upload is not None:
                return await upload()
            set_resume_files((raw.current, raw.fixed), name)
            return {'value': {'set': True, 'files': [name]}}
        return await original_call(method, *args, **kwargs)

    raw.call = call

    async def run_apply(actions, *, late=False):
        decide.side_effect = actions
        if late:
            render, pending = _late_resume_field((raw.current, raw.fixed)), list(actions)

            def rendered_at_first_decision(*_args, **_kwargs):
                if len(pending) == len(actions):
                    render()
                action = pending.pop(0)
                if isinstance(action, BaseException):
                    raise action
                return action

            decide.side_effect = rendered_at_first_decision
        return await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                                 pk=PK, resume_path=pdf)

    click = {'action': 'click', 'selector': button['selector']}
    return run_apply, click, raw, decide, transport, uploads, row


def _no_production_commit(raw, transport, row):
    assert not [c for c in raw.calls if c[0] == 'click']
    assert not transport._application_controller.committed
    assert not row.get('possible_submission')


async def test_production_submit_click_without_upload_recovers_then_meets_final_guard(
        monkeypatch, tmp_path):
    """The live symptom: a model click on Submit right after bootstrap was refused
    at the final guard for the missing row PDF, so the row could never proceed.
    Fallback for a résumé field rendered after the first decision: the click
    takes the one guarded upload, then a fresh decision; the second click
    passes the PDF proof and is refused by a later final-guard check."""
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    result = await run_apply([WAIT, click, click], late=True)
    assert result['status'] == 'gate', result
    assert 'protected tailored PDF' not in result['question']
    assert 'Unverified or changed form value' in result['question'], result
    assert decide.await_count == 3
    guard = transport._application_controller
    assert uploads == [('set', Path(guard.resume_path).name)]
    # The staged PDF is released after the run; recovery compared the
    # producer's upload_identity with it before recording the receipt.
    assert guard.upload_attempted and guard.upload_completed
    assert not guard.upload_pending and guard.upload_identity is not None
    _no_production_commit(raw, transport, row)


@pytest.mark.parametrize('failure', ['fault', 'refused'])
async def test_production_submit_click_upload_failure_stops_without_click(
        monkeypatch, tmp_path, failure):
    """Once the upload before the first decision has passed its effect edge,
    any transport failure, even one reported as no-effect, stays terminal
    uncertain as in the recovery: the chooser may have acted, so nothing is
    retried, recorded as uploaded, decided or clicked."""
    from tools.browser_skill import Unavailable

    async def broken():
        if failure == 'fault':
            raise OSError('synthetic transport fault')
        exc = Unavailable('synthetic definitive refusal')
        exc.code, exc.effect_state = 'unsupported', 'none'
        raise exc

    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path, upload=broken)
    result = await run_apply([click])
    assert result == {
        'status': 'uncertain', 'step': result['step'],
        'detail': 'Résumé upload interrupted after the browser command; inspect manually'}
    assert decide.await_count == 0
    assert len(uploads) == 1
    assert not transport._application_controller.upload_completed
    _no_production_commit(raw, transport, row)


async def test_production_submit_click_never_uploads_a_second_time(monkeypatch, tmp_path):
    """After the one upload before the first decision, neither a model upload
    nor a Submit click can reach the chooser again; the click goes to the
    final guard."""
    run_apply, click, raw, decide, transport, uploads, row = _production_submit_click(
        monkeypatch, tmp_path)
    upload = {'action': 'upload', 'selector': '#_systemfield_resume'}
    result = await run_apply([upload, click])
    assert result['status'] == 'gate', result
    assert 'Unverified or changed form value' in result['question'], result
    assert len(uploads) == 1
    assert decide.await_count == 2
    _no_production_commit(raw, transport, row)
