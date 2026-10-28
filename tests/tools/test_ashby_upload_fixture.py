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


def _admit_optional_owner_question(raw):
    # This synthetic form has no pending owner question; the next model
    # decision must be reachable after a proven upload, not inferred from Gate.
    for page in (raw.current, raw.fixed):
        for item in page['controls'] + page['entry']['root_evidence']:
            if item['selector'] == '#additional':
                item['required'] = False


def _script_recovery_decisions(monkeypatch, *, first='submit',
                               on_submit=None, timeline=None):
    """Exercise the sealed two-fill IPC prefix before any missing-proof Submit."""
    seen = []
    actions = [
        {'action': 'fill', 'selector': '#_systemfield_name', 'fact': 'Name'},
        {'action': 'fill', 'selector': '#_systemfield_email', 'fact': 'Email'},
        ({'action': 'submit', 'selector': '#structural-submit'} if first == 'submit'
         else {'action': 'upload', 'selector': '#_systemfield_resume'}),
        ({'action': 'gate', 'question': 'Synthetic inspection without a second click'}
         if first == 'submit'
         else {'action': 'submit', 'selector': '#structural-submit'}),
    ]

    async def decide(self, task, page, history, *, model):
        assert model == 'synthetic-fixture'
        index = self.decisions
        assert index < len(actions), 'unexpected automatic retry or extra decision'
        action = actions[index]
        if index == 2 and first == 'submit':
            assert self.ipc_index == 2 and not self.uploaded
            if on_submit:
                on_submit()
        if index == 3 and first == 'submit':
            assert any(item.get('action') == 'upload' and item.get('uploaded') is True
                       for item in history)
        seen.append((action['action'], self.upload_ipc,
                     'résumé attachment verified=True' in task))
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
    pair = observed(sibling=True, **(changes or {}), **params)
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


@pytest.mark.parametrize('transport', ['effect', 'call'])
async def test_reversible_submit_recovers_one_native_input_upload_then_next_gate(
        authority, monkeypatch, transport):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _admit_optional_owner_question(raw)
    _script_recovery_decisions(monkeypatch)
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
async def test_reversible_direct_model_upload_is_refused_before_chooser(
        authority, monkeypatch, transport):
    Path(authority.resume_path).write_bytes(fixture._RESUME_BYTES)
    authority.ashby = authority.ashby_upload = True
    authority.enable_positive_fixture()
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _bind_mismatch_fixture_producer(authority, monkeypatch, transport=transport)
    raw = MismatchFixtureRaw(authority)
    _admit_optional_owner_question(raw)
    _script_recovery_decisions(monkeypatch, first='upload')
    monkeypatch.setattr(forms.bsk, 'Session', lambda *_: raw)
    result = await forms.apply(
        fixture.URL, fixture.COMPANY, dict(fixture._FACTS), 'synthetic-fixture',
        pk=fixture.PK, resume_path=authority.resume_path, fixture_context=authority)
    assert result['status'] == 'gate', result
    assert 'no direct chooser is permitted' in result['question']
    assert authority.fills == 2 and authority.upload_ipc == 0
    assert not raw.uploads
    assert authority.submit_ipc == authority.submit_call_entered == 0


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
