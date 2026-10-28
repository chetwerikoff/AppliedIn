"""Sealed fixture wiring proof; all browser/HTTP edges are offline doubles."""
from pathlib import Path
from copy import deepcopy
import json
import re
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_formless import observed, RootSession
from tests.tools.test_browser_skill_fixture import authority


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
        if method != 'upload':
            return await super().call(method, *args, **kwargs)
        self.uploads.append(((method, *args), kwargs))
        after = snapshot(self.authority, attached=True)
        for old, new in zip((self.current, self.fixed), after):
            lookup = {c['selector']: c for c in old['controls'] + old['entry']['root_evidence']}
            for c in new['controls'] + new['entry']['root_evidence']:
                for key in ('value', 'has_value', 'required'):
                    if key in lookup.get(c['selector'], {}):
                        c[key] = lookup[c['selector']][key]
        self.current, self.fixed = after
        return {}


async def test_separate_sealed_grants_reach_existing_next_human_gate(authority, monkeypatch):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = authority.ashby_upload_transient = True
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
    assert raw.uploads == [(('upload', '#ashby-upload-dropzone', '--file', authority.resume_path),
                            {'timeout': 130})]
    assert set(authority.row['human_approved_answers']) == {'Name', 'Email'}
    pairs = authority.upload_pair_trace
    assert len(pairs) == 3
    assert [p['page']['entry'] == p['fixed']['entry'] for p in pairs] == [False, True, True]
    assert [p['page']['entry']['upload_tree']['complete'] for p in pairs] == [False, False, True]
    assert pairs[-1]['page']['entry']['upload_tree']['attached'] is True
    assert all(p['page']['url'] == p['fixed']['url'] == fixture.URL for p in pairs)


@pytest.mark.parametrize('attempt', ['logical-input', 'mode', 'click', 'wrong-file', 'repeat'])
async def test_fixture_wrapper_admits_only_exact_dropzone_tuple(authority, attempt):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    authority.navigations = 1
    authority.ipc_index = 2
    raw = FixtureRaw(authority)
    facade = authority.facade(raw)
    args = ('upload', '#ashby-upload-dropzone', '--file', authority.resume_path)
    kwargs = {'timeout': 130}
    if attempt == 'logical-input':
        args = ('upload', '#_systemfield_resume', '--file', authority.resume_path)
    elif attempt == 'mode':
        args += ('--mode', 'drop')
    elif attempt == 'click':
        args, kwargs = ('click', '#ashby-upload-dropzone'), {}
    elif attempt == 'wrong-file':
        args = ('upload', '#ashby-upload-dropzone', '--file', '/synthetic/Other.pdf')
    else:
        await facade.call(*args, **kwargs)
    before = len(raw.uploads)
    with pytest.raises(PermissionError):
        await facade.call(*args, **kwargs)
    assert len(raw.uploads) == before


def test_all_fixture_legs_are_unconditional(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(fixture, '_run_scenario', lambda **kwargs: calls.append(kwargs) or 0)
    assert fixture.run() == 0
    assert calls == [{}, {'greenhouse': True}, {'greenhouse_empty_placeholder': True},
                     {'w_lookalike': True}, {'ashby': True},
                     {'ashby_upload': True},
                     {'ashby_upload': True, 'ashby_upload_transient': True},
                     {'custom_question': 'radio'}, {'custom_question': 'checkbox'}]
    report = json.loads(capsys.readouterr().out)
    assert report['sessions'] == len(calls) == 9
    assert len(report['legs']) == 8
    assert set(report['legs']) == {
        'entry', 'greenhouse', 'greenhouse_empty_placeholder',
        'w_button_aria_label', 'ashby', 'ashby-upload',
        'lever_custom_radio', 'lever_custom_checkbox',
    }
    assert set(report['legs'].values()) == {'PASS'}


@pytest.mark.parametrize('unwrap', [False, True])
def test_real_local_handler_opens_chooser_and_renders_only_passive_item(unwrap):
    original = fixture._ashby_form()
    assert b'Application answer' in original
    assert b'addEventListener' not in original
    document = fixture._ashby_upload_form(unwrap=unwrap).decode()
    assert ('<span><span>Upload</span></span>' in document) is unwrap
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
                     'text': '', 'iconRemoved': True, 'unwrapped': unwrap}


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
    if cancel:
        assert result['status'] == 'uncertain'
        assert detail == 'Résumé upload interrupted after the browser command; inspect manually'
        assert is_retryable(result) == (False, 'uncertain')
    else:
        assert result['status'] == 'gate'
        assert 'Upload structural/file witness is unavailable' in detail
        assert is_retryable(result) == (False, 'waiting on the owner')
    assert len(raw.uploads) == 1


@pytest.mark.parametrize('recovery', [False, True])
async def test_definitive_no_effect_refusal_keeps_its_gate_reason(authority, monkeypatch, recovery):
    if recovery:
        _script_recovery_decisions(monkeypatch)
    from core.apply_queue import is_retryable
    from tools.browser_skill import Unavailable
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = FixtureRaw(authority)
    original = raw.call
    async def refused(method, *args, **kwargs):
        if method == 'upload':
            raw.uploads.append(((method, *args), kwargs))
            exc = Unavailable('synthetic definitive refusal')
            exc.code, exc.effect_state = 'unsupported', 'none'
            raise exc
        return await original(method, *args, **kwargs)
    raw.call = refused
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate', result
    assert ('BrowserSkill upload unsupported with no effect; the original input is empty. '
            'Complete the attachment manually; no retry was attempted.') in result['question']
    assert is_retryable(result) == (False, 'waiting on the owner')
    assert len(raw.uploads) == 1


async def _timed_upload(authority, monkeypatch, *, settles, recovery=False):
    from copy import deepcopy
    from tests.tools.test_ashby_upload import UploadClock
    from core.apply_queue import is_retryable
    clock = UploadClock()
    monkeypatch.setattr(forms, '_upload_clock', lambda: clock.now)
    monkeypatch.setattr(forms, '_upload_sleep', clock.sleep)
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = FixtureRaw(authority)
    # Real independent readers supply both pending and attached observations.
    pending = observed(disabledDrop=True, files=[Path(authority.resume_path).name])
    attached = observed(attached=True, files=[Path(authority.resume_path).name],
                        displayName=Path(authority.resume_path).name,
                        filenameChild='span', filenameSvgs=1)
    for pair in (pending, attached):
        for page in pair:
            page['url'] = fixture.URL
            for c in page['controls'] + page['entry']['root_evidence']:
                if c['selector'] == '#dropzone':
                    c['selector'] = '#ashby-upload-dropzone'
            for n in page['entry']['upload_tree']['nodes']:
                if n.get('target_selector') == '#dropzone':
                    n['target_selector'] = '#ashby-upload-dropzone'
    original = raw.call
    async def timed(method, *args, **kwargs):
        if method == 'evaluate' and raw.uploads:
            pair = attached if settles and clock.now >= 1.8 else pending
            for source, target in zip((raw.current, raw.fixed), pair):
                lookup = {c['selector']: c for c in source['entry']['root_evidence']}
                for c in target['controls'] + target['entry']['root_evidence']:
                    for key in ('value', 'has_value'):
                        if c.get('type') != 'file' and key in lookup.get(c['selector'], {}):
                            c[key] = lookup[c['selector']][key]
            raw.current, raw.fixed = deepcopy(pair)
        return await original(method, *args, **kwargs)
    raw.call = timed
    promotions, sessions = [], []
    parent = forms._EntrySession
    class TrackedEntry(parent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

        def __setattr__(self, key, value):
            if key == 'formless_identity' and getattr(self, 'upload_pending', False):
                promotions.append(value)
            super().__setattr__(key, value)
    monkeypatch.setattr(forms, '_EntrySession', TrackedEntry)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert len(raw.uploads) == authority.upload_ipc == 1
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert all(not session.upload_pending for session in sessions)
    assert all(step == 0.25 for step in clock.steps)
    if settles:
        assert result['status'] == 'gate', result
        if recovery:
            # With ctx there is no producer-side owner gate after upload. The
            # synthetic second decision intentionally requests a non-click Gate.
            assert result['form_question'] is None, result
            assert 'Synthetic inspection without a second click' in result['question']
            assert authority.decisions == 4
        else:
            assert result['form_question']['selector'] == '#additional', result
        assert 1.8 <= clock.now < 2.25
        assert len(promotions) == 1
        assert sessions[0].upload_result['entry']['upload_tree']['attached']
    else:
        assert clock.now == 8.0 and len(clock.steps) == 32
        assert result['status'] == 'uncertain', result
        assert result['detail'] == 'Résumé upload interrupted after the browser command; inspect manually'
        assert is_retryable(result) == (False, 'uncertain')
        assert promotions == [] and sessions[0].upload_result is None


@pytest.mark.parametrize('recovery', [False, True])
async def test_ashby_upload_disabled_dropzone_1_8s_settles_one_ipc(authority, monkeypatch, recovery):
    if recovery:
        _script_recovery_decisions(monkeypatch)
    await _timed_upload(authority, monkeypatch, settles=True, recovery=recovery)


@pytest.mark.parametrize('recovery', [False, True])
async def test_ashby_upload_never_settles_8s_terminal_one_ipc(authority, monkeypatch, recovery):
    if recovery:
        _script_recovery_decisions(monkeypatch)
    await _timed_upload(authority, monkeypatch, settles=False, recovery=recovery)


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


async def _transient_fixture_run(authority, monkeypatch, *, persistent=False):
    from core.apply_queue import is_retryable
    from tests.tools.test_ashby_upload import UploadClock
    clock = UploadClock()
    monkeypatch.setattr(forms, '_upload_clock', lambda: clock.now)
    monkeypatch.setattr(forms, '_upload_sleep', clock.sleep)
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = authority.ashby_upload_transient = True
    authority.enable_positive_fixture()
    raw = FixtureRaw(authority)
    original = raw.call
    samples = 0
    before_upload = None

    async def sequenced(method, *args, **kwargs):
        nonlocal samples, before_upload
        if method == 'upload':
            before_upload = deepcopy(raw.current)
        if (method == 'evaluate' and raw.uploads and args == (forms.ENTRY_PAGE,)
                and not authority.upload_pair_complete):
            # Follow-up answer reads do not belong to upload settling and
            # must not inflate the three deterministic paired observations.
            samples += 1
            # The local fixture boundary, not elapsed wall time, owns the
            # first split and the later opaque-to-complete sequence.
            pair = snapshot(authority, pending=True) if persistent or samples <= 2 else (
                snapshot(authority, attached=True))
            if persistent:
                pair[1]['entry']['upload_tree']['nodes'][0]['text'] = 'still transitioning'
            _update_read_values(raw, pair)
        return await original(method, *args, **kwargs)

    raw.call = sequenced
    promotions = []
    parent = forms._EntrySession

    class TrackedEntry(parent):
        def __setattr__(self, key, value):
            if key == 'formless_identity' and getattr(self, 'upload_pending', False):
                promotions.append((len(authority.upload_pair_trace), value))
            super().__setattr__(key, value)

    monkeypatch.setattr(forms, '_EntrySession', TrackedEntry)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_args: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    pairs = authority.upload_pair_trace
    assert before_upload is not None
    assert len(raw.uploads) == authority.upload_ipc == 1
    assert raw.uploads == [(('upload', '#ashby-upload-dropzone', '--file', authority.resume_path),
                            {'timeout': 130})]
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert not authority.post_receipts
    assert all(p['page']['url'] == p['fixed']['url'] == before_upload['url']
               and forms._formless_root(p['page']) == forms._formless_root(p['fixed'])
               == forms._formless_root(before_upload)
               and p['page']['inventory_verified'] is True for p in pairs)
    assert pairs[0]['page']['entry'] != pairs[0]['fixed']['entry']
    assert pairs[0]['page']['entry']['upload_tree']['complete'] is False
    assert pairs[0]['fixed']['entry']['upload_tree']['complete'] is True
    if persistent:
        assert result['status'] == 'uncertain', result
        assert result['detail'] == 'Résumé upload interrupted after the browser command; inspect manually'
        assert is_retryable(result) == (False, 'uncertain')
        assert clock.now == 8.0 and clock.steps == [0.25] * 32
        assert samples == len(pairs) == 32
        assert all(p['page']['entry'] != p['fixed']['entry'] for p in pairs)
        assert promotions == []
    else:
        assert result['status'] == 'gate', result
        assert result['form_question']['selector'] == '#additional'
        assert result['form_question']['owner_kind'] == 'ashby-formless-root'
        assert authority.entry_wait == 'PASS'
        assert authority.fills == 2 and authority.ipc_index == 3
        assert samples == len(pairs) == 3
        assert [p['page']['entry'] == p['fixed']['entry'] for p in pairs] == [
            False, True, True]
        assert [p['page']['entry']['upload_tree']['complete'] for p in pairs] == [
            False, False, True]
        assert pairs[-1]['page']['entry']['upload_tree']['attached'] is True
        assert pairs[-1]['page']['entry'] == pairs[-1]['fixed']['entry']
        assert forms._upload_delta(
            before_upload, pairs[-1]['page'], Path(authority.resume_path).name) is True
        assert promotions == [(3, forms._root_identity(pairs[-1]['page']))]
        assert clock.steps == [0.25, 0.25]
    return result


async def test_transient_fixture_ordered_split_agreement_and_exact_attachment(
        authority, monkeypatch):
    await _transient_fixture_run(authority, monkeypatch)


async def test_persistent_fixture_entry_split_terminal_uncertain_and_no_retry(
        authority, monkeypatch):
    await _transient_fixture_run(authority, monkeypatch, persistent=True)


WAIT = {'action': 'wait'}


def _late_resume_field(pages):
    """FF-224e: the résumé field renders after the first decision.

    The one server-owned upload runs just before the model's first decision.
    When the résumé field has not rendered by then, both real readers report
    no upload tree (the DOM reader emits null until the container's upload
    root is complete), so only the model-intent fallbacks can upload. The
    returned callable renders the field on both readers.
    """
    trees = [page['entry']['upload_tree'] for page in pages]
    for page in pages:
        page['entry']['upload_tree'] = None

    def render():
        for page, tree in zip(pages, trees):
            page['entry']['upload_tree'] = tree

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
        if method in {'fill', 'upload', 'click'}:
            timeline.append(method)
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
    assert raw.uploads == [(('upload', '#ashby-upload-dropzone', '--file',
                            authority.resume_path), {'timeout': 130})]
    assert authority.fills == 2 and authority.ipc_index == 3
    assert authority.uploaded and authority.upload_ipc == 1
    assert result['status'] == 'gate', result
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    assert not [call for call in raw.calls if call[0] == 'click']


@pytest.mark.parametrize('late', [False, True])
async def test_resumed_retained_attachment_never_replaces_or_retries(
        authority, monkeypatch, late):
    raw = FixtureRaw(authority)
    if not late:
        _update_read_values(raw, snapshot(authority, attached=True))

    def attach_after_submit():
        _update_read_values(raw, snapshot(authority, attached=True))

    result, _, seen = await _run_recovery(
        authority, monkeypatch, raw=raw,
        on_submit=attach_after_submit if late else None)
    assert result['status'] == 'gate', result
    assert len(raw.uploads) == authority.upload_ipc == 0
    assert authority.submit_ipc == authority.submit_call_entered == 0
    assert authority.post_attempts == authority.post_count == 0
    if late:
        # This is an admitted Submit and a late receiver refusal, not preflight.
        assert [action for action, _, _ in seen] == ['fill', 'fill', 'submit']
        assert 'The original empty résumé input changed before upload.' in result['question']


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
async def test_recovery_guarded_transport_error_gates_without_replay(
        authority, monkeypatch, transport):
    import asyncio
    raw = FixtureRaw(authority)
    original = raw.call

    async def broken(method, *args, **kwargs):
        if method == 'upload':
            raw.uploads.append(((method, *args), kwargs))
            if transport == 'cancel':
                raise asyncio.CancelledError()
            raise OSError('synthetic transport fault')
        return await original(method, *args, **kwargs)

    raw.call = broken
    result, _, seen = await _run_recovery(authority, monkeypatch, raw=raw)
    assert [action for action, _, _ in seen] == ['fill', 'fill', 'submit']
    assert result['status'] == 'gate', result
    assert 'The résumé upload outcome is unclear; inspect the attachment manually.' in result['question']
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
        if method != 'upload':
            return await RootSession.call(self, method, *args, **kwargs)
        self.uploads.append(((method, *args), kwargs))
        after = mismatch_snapshot(self.authority, attached=True)
        for old, new in zip((self.current, self.fixed), after):
            lookup = {c['selector']: c for c in old['controls'] + old['entry']['root_evidence']}
            for c in new['controls'] + new['entry']['root_evidence']:
                for key in ('value', 'has_value', 'required'):
                    if c.get('type') != 'file' and key in lookup.get(c['selector'], {}):
                        c[key] = lookup[c['selector']][key]
        self.current, self.fixed = after
        return {}


def _bind_mismatch_fixture_producer(authority, monkeypatch, *, transport='effect'):
    """Test-local real controller bound to the existing sealed fixture delegate."""
    from tools.browser_skill_apply import ApplicationIPCController

    original_facade = fixture._SyntheticRun.facade
    original_ipc = fixture._SyntheticRun._positive_ipc

    def native_tuple(self):
        seq = original_ipc(self)
        if not self.ashby_upload:
            return seq
        assert seq[-1][0] == 'upload'
        return (*seq[:-1], ('upload', '#_systemfield_resume', '--file', self.resume_path))

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
                if args and args[0] == 'upload':
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
                if args and args[0] == 'upload':
                    return await self._guarded_upload(*args, **kwargs)
                return await sealed.call(*args, **kwargs)

        return Proxy()

    monkeypatch.setattr(fixture._SyntheticRun, '_positive_ipc', native_tuple)
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
    assert raw.uploads == [(('upload', '#_systemfield_resume', '--file',
                            authority.resume_path), {'timeout': 130})]
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
    assert raw.uploads == [(('upload', '#_systemfield_resume', '--file',
                            authority.resume_path), {'timeout': 130})]
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

@pytest.mark.parametrize('mutation', [
    'readonly', 'duplicate', 'retained', 'root', 'uploadtree', 'change-url',
])
async def test_reversible_upload_preflight_rejects_drift_before_any_effect(
        authority, monkeypatch, mutation):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    raw = MismatchFixtureRaw(authority)
    original = raw.current, raw.fixed
    selector = '#_systemfield_resume'
    for page in original:
        if mutation == 'readonly':
            for c in page['controls'] + page['entry']['root_evidence']:
                if c.get('selector') == selector:
                    c['readonly'] = True
        elif mutation == 'duplicate':
            page['entry']['root_evidence'].append(
                deepcopy(next(c for c in page['entry']['root_evidence']
                              if c.get('selector') == selector)))
        elif mutation == 'retained':
            for c in page['controls'] + page['entry']['root_evidence']:
                if c.get('selector') == selector:
                    c['files'] = ['prior-file.pdf']
        elif mutation == 'root':
            page['entry']['formless']['error'] = 'ambiguous application roots'
        elif mutation == 'uploadtree':
            page['entry']['upload_tree']['complete'] = False
        else:
            page['url'] = 'https://example-co.test/other-job'
    provenance = authority.entry_provenance()
    adapter = forms._ReversibleEntryReader(raw)
    with pytest.raises(forms.Gate):
        await forms._reversible_upload_pair(
            adapter, allowed=lambda url: url == fixture.URL,
            provenance=provenance, selector=selector, empty=True)
    assert raw.uploads == []
    assert not any(c[0] in {'upload', 'click'} for c in raw.calls)


async def test_reversible_upload_delta_rejects_unexcluded_attached_delete(
        authority):
    raw = MismatchFixtureRaw(authority)
    before = raw.current
    after = mismatch_snapshot(authority, attached=True)[0]
    filename = Path(authority.resume_path).name
    assert forms._reversible_upload_settled(before, after, filename) is True
    after['entry']['formless']['excluded'].pop()
    with pytest.raises(forms.Gate, match='attached-file passive delete'):
        forms._reversible_upload_settled(before, after, filename)


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
        ('upload', '#_systemfield_resume', '--file', self.resume_path),))
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
        if method in {'fill', 'upload', 'click'}:
            timeline.append(method)
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
    assert raw.uploads == [(('upload', '#_systemfield_resume', '--file',
                            authority.resume_path), {'timeout': 130})]
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


def _two_retained(pair):
    for page in pair:
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_resume':
                c['files'] = [PRIOR_RUN_PDF, 'Second.pdf']


def _split_witness(pair):
    for c in pair[1]['controls'] + pair[1]['entry']['root_evidence']:
        if c['selector'] == '#_systemfield_resume':
            c['files'] = ['Other Synthetic Resume.pdf']


def _display_disagrees(pair):
    for page in pair:
        page['entry']['upload_tree']['filename'] = 'Other Synthetic Resume.pdf'


def _input_without_item(pair):
    for page in pair:
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_resume':
                c['files'] = [PRIOR_RUN_PDF]


# The proactive upload takes only an empty receiver or one retained,
# differently named PDF; any other receiver is left to the Submit decision,
# whose recovery refuses it the same way.
@pytest.mark.parametrize('case,prior,mutate,refusal,proactive', [
    ('staged-name', 'Synthetic-Resume.pdf', None, 'has the staged filename', False),
    ('not-pdf', 'Earlier Synthetic Resume.docx', None,
     'no exact original attachment proof', False),
    ('two-files', PRIOR_RUN_PDF, _two_retained, 'original empty résumé input changed', False),
    ('split-witness', PRIOR_RUN_PDF, _split_witness, 'no exact original attachment proof', True),
    ('display-disagrees', PRIOR_RUN_PDF, _display_disagrees,
     'no exact original attachment proof', True),
    ('input-without-item', None, _input_without_item,
     'no exact original attachment proof', True),
])
async def test_resumed_ineligible_retained_attachment_gates_before_any_upload(
        authority, monkeypatch, case, prior, mutate, refusal, proactive):
    """A visible filename is never eligibility. Wrong, ambiguous or unwitnessed
    retained files keep the existing Gate with no chooser effect at all."""
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    if case == 'staged-name':
        assert prior == Path(authority.resume_path).name
    raw = _resumed_reversible_raw(authority, prior=prior)
    late = case == 'split-witness'
    if mutate and not late:
        mutate((raw.current, raw.fixed))
    if late:
        # A split reader pair is already refused by bootstrap; inject it as
        # the recovery starts so its own paired preflight is what refuses.
        recover = forms._reversible_recover_upload

        async def split_then_recover(*args, **kwargs):
            mutate((raw.current, raw.fixed))
            return await recover(*args, **kwargs)

        monkeypatch.setattr(forms, '_reversible_recover_upload', split_then_recover)
    result, raw, timeline, controllers = await _resumed_submit_first(
        authority, monkeypatch, raw=raw)
    assert result['status'] == 'gate', result
    assert refusal in result['question'], result
    assert timeline == ([] if proactive else [('decision0', 0, False)])
    assert raw.uploads == [] and authority.upload_ipc == 0
    assert not controllers[0].upload_attempted
    _no_commit(authority, raw)


async def test_resumed_retained_attachment_drift_before_effect_gates_without_upload(
        authority, monkeypatch):
    """The PAGE/fixed pair is re-read at the effect edge; a retained file that
    changed after preflight is refused before the chooser is opened."""
    original = forms._reversible_upload_pair
    calls = []

    async def drift_after_preflight(session, **kwargs):
        page = await original(session, **kwargs)
        calls.append(kwargs)
        if len(calls) == 1:
            changed = mismatch_snapshot(authority, changes={
                'attached': True, 'files': ['Swapped Synthetic Resume.pdf'],
                'displayName': 'Swapped Synthetic Resume.pdf'})
            raw = session.raw._original_raw
            _update_read_values(raw, changed)
        return page

    monkeypatch.setattr(forms, '_reversible_upload_pair', drift_after_preflight)
    result, raw, timeline, controllers = await _resumed_submit_first(
        authority, monkeypatch, prior=PRIOR_RUN_PDF)
    assert result['status'] == 'gate', result
    assert len(calls) == 1, 'the effect edge must refuse inside its fresh pair'
    assert timeline == []
    assert raw.uploads == [] and authority.upload_ipc == 0
    assert not controllers[0].upload_attempted
    _no_commit(authority, raw)


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
        if method == 'upload':
            raw.uploads.append(((method, *args), kwargs))
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


@pytest.mark.parametrize('prior', [None, PRIOR_RUN_PDF])
async def test_resumed_upload_that_never_settles_is_uncertain_once(
        authority, monkeypatch, prior):
    """The retained item can stay on screen after the chooser; it is never
    taken for the new attachment, and the 8 s budget ends uncertain."""
    from tests.tools.test_ashby_upload import UploadClock
    clock = UploadClock()
    monkeypatch.setattr(forms, '_upload_clock', lambda: clock.now)
    monkeypatch.setattr(forms, '_upload_sleep', clock.sleep)
    raw = _resumed_reversible_raw(authority, prior=prior)

    async def unchanged(method, *args, **kwargs):
        if method == 'upload':
            raw.uploads.append(((method, *args), kwargs))
            return {}
        return await RootSession.call(raw, method, *args, **kwargs)

    raw.call = unchanged
    result, raw, timeline, controllers = await _resumed_submit_first(
        authority, monkeypatch, raw=raw)
    assert result['status'] == 'uncertain', result
    assert clock.now == 8.0
    assert timeline == ['upload']
    assert len(raw.uploads) == 1
    _no_commit(authority, raw)


def test_retained_attachment_replacement_needs_the_exact_one_item_swap(authority):
    raw = _resumed_reversible_raw(authority, prior=PRIOR_RUN_PDF)
    before = deepcopy(raw.current)
    filename = Path(authority.resume_path).name
    _update_read_values(raw, mismatch_snapshot(authority, attached=True))
    after = raw.current
    assert forms._reversible_upload_settled(
        before, after, filename, prior=PRIOR_RUN_PDF) is True
    # The untouched retained item is not settlement of a new upload.
    assert forms._reversible_upload_settled(
        before, deepcopy(before), filename, prior=PRIOR_RUN_PDF) is False
    extra = deepcopy(after)
    extra['entry']['formless']['excluded'].append('#another-excluded')
    with pytest.raises(forms.Gate, match='attached-file passive delete'):
        forms._reversible_upload_settled(before, extra, filename, prior=PRIOR_RUN_PDF)
    moved = deepcopy(after)
    for c in moved['controls'] + moved['entry']['root_evidence']:
        if c['selector'] == '#_systemfield_name':
            c['value'] = 'Changed Synthetic Name'
    with pytest.raises(forms.Gate):
        forms._reversible_upload_settled(before, moved, filename, prior=PRIOR_RUN_PDF)


# ARCH21:58: production models click the Submit control instead of choosing
# submit. Only the offline fixture converts that click, so these cases run the
# real raw producer and controller (no fixture context) on the reversible route.

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
        if method == 'upload':
            uploads.append((method, *args))
            raw.calls.append((method, *args))
            if upload is not None:
                return await upload()
            name = Path(args[2]).name
            raw.current, raw.fixed = observed(
                movePane=True, attached=True, files=[name], displayName=name)
            prefilled((raw.current, raw.fixed))
            return {}
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
    assert uploads == [('upload', '#_systemfield_resume', '--file', guard.resume_path)]
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
