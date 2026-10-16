"""Sealed fixture wiring proof; all browser/HTTP edges are offline doubles."""
from pathlib import Path
import json
import re
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_formless import observed, RootSession
from tests.tools.test_browser_skill_fixture import authority


def snapshot(authority, attached=False):
    pair = observed(**({'attached': True, 'files': [Path(authority.resume_path).name],
                        'displayName': Path(authority.resume_path).name} if attached else {}))
    for page in pair:
        page['url'] = fixture.URL
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#dropzone':
                c['selector'] = '#ashby-upload-dropzone'
        for n in page['entry']['upload_tree']['nodes']:
            if n['selector'] == '#dropzone':
                n['selector'] = '#ashby-upload-dropzone'
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
                for key in ('value', 'has_value'):
                    if key in lookup.get(c['selector'], {}):
                        c[key] = lookup[c['selector']][key]
        self.current, self.fixed = after
        return {}


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
    assert raw.uploads == [(('upload', '#ashby-upload-dropzone', '--file', authority.resume_path),
                            {'timeout': 130})]
    assert set(authority.row['human_approved_answers']) == {'Name', 'Email'}


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
    assert calls == [{}, {'greenhouse': True}, {'w_lookalike': True},
                     {'ashby': True}, {'ashby_upload': True},
                     {'custom_question': 'radio'}, {'custom_question': 'checkbox'}]
    assert json.loads(capsys.readouterr().out)['sessions'] == 7


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
const make=tag=>({tag,children:[],append(...nodes){this.children.push(...nodes);}});
const icon={remove(){drop.iconRemoved=true;}};
const file=make('div');file.insertBefore=(n,other)=>{if(other!==drop)throw Error('wrong container');file.children.unshift(n);};
const input={files:[],parentElement:file,click(){this.clicked=(this.clicked||0)+1;},addEventListener(e,fn){handlers[e]=fn;}};
const drop={addEventListener(e,fn){handlers['drop-'+e]=fn;},querySelector(q){if(q!=='svg')throw Error('unexpected target');return icon;}};
global.document={getElementById:id=>id==='_systemfield_resume'?input:drop,createElement:make};
eval(process.argv[1]);handlers['drop-click']();
if(input.clicked!==1 || input.files.length)throw Error('chooser was bypassed');
input.files=[{name:'Synthetic-Resume.pdf'}];handlers.change();
const item=file.children[0];
console.log(JSON.stringify({class:item.className,names:item.children.map(n=>n.className),
 name:item.children[0].textContent,title:item.children[1].title,
 text:item.children[1].textContent||'',iconRemoved:drop.iconRemoved}));
'''
    result = subprocess.run(['node', '-e', harness, script], capture_output=True, text=True, check=True)
    proof = json.loads(result.stdout)
    assert proof == {'class': 'ashby-application-form-input-file-item',
                     'names': ['ashby-application-form-input-file-item-name',
                               'ashby-application-form-input-file-item-delete'],
                     'name': 'Synthetic-Resume.pdf', 'title': 'Remove attachment',
                     'text': '', 'iconRemoved': True}
