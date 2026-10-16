"""Upload transition proof uses the real shared DOM readers, never a browser."""
import asyncio
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill import Unavailable
from tests.tools.test_browser_formless import observed, root_world, RootSession
from tests.tools.test_browser_application_entry import entry


class UploadSession(RootSession):
    def __init__(self, before, after, *, unchanged=0, refusal=None):
        super().__init__(*before)
        self.after = after
        self.unchanged = unchanged
        self.refusal = refusal
        self.post_pairs = 0
        self.mutations = []
        self.uploaded = False
        self.pending_action = None

    async def call(self, method, *args, **kwargs):
        if method == 'upload':
            self.mutations.append(((method, *args), kwargs))
            self.uploaded = True
            if self.refusal:
                raise self.refusal
            return {}
        if method == 'evaluate' and self.uploaded and not self.refusal:
            if args == (forms.ENTRY_PAGE,):
                self.post_pairs += 1
                if self.pending_action:
                    await self.pending_action()
                if self.post_pairs > self.unchanged:
                    self.current, self.fixed = deepcopy(self.after)
                else:
                    for snapshot in (self.current, self.fixed):
                        for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                            if c.get('type') == 'file':
                                c['files'] = ['Resume.pdf']
        return await super().call(method, *args, **kwargs)


def setup_upload(monkeypatch, *, before=None, after=None, **kwargs):
    _, _, _, provenance = root_world(monkeypatch)
    before = before or observed()
    after = after or observed(attached=True, files=['Resume.pdf'])
    raw = UploadSession(before, after, **kwargs)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    session.formless_identity = forms._root_identity(before[0])
    return raw, session, before[0]


async def upload(session):
    return await session.call('upload', '#_systemfield_resume', '--file', 'Resume.pdf', timeout=130)


@pytest.mark.parametrize('unchanged', [0, 1, 4])
async def test_exact_default_upload_and_bounded_settle(monkeypatch, unchanged):
    raw, session, before = setup_upload(monkeypatch, unchanged=unchanged)
    promotions = []
    class TrackedEntry(forms._EntrySession):
        def __setattr__(self, key, value):
            if key == 'formless_identity':
                promotions.append(value)
            super().__setattr__(key, value)
    session.__class__ = TrackedEntry
    await upload(session)
    assert raw.mutations == [(('upload', '#dropzone', '--file', 'Resume.pdf'), {'timeout': 130})]
    assert raw.post_pairs == unchanged + 1
    assert promotions == [session.formless_identity]
    identity = session.formless_identity
    page = await session.page()
    assert session.formless_identity == identity == forms._root_identity(page)
    assert promotions == [identity]
    assert not session.upload_pending
    assert not any(c['selector'].startswith('#delete') for c in page['controls'])
    assert sum(forms.is_submission(c) for c in page['controls']) == 1
    assert len(forms._formless_boundary(page, session.provenance)) == 4
    assert not session.submit_authorized
    with pytest.raises(forms.Gate, match='one exact default'):
        await upload(session)
    assert len(raw.mutations) == 1


@pytest.mark.parametrize('args,kwargs', [
    (('upload', '#dropzone', '--file', 'Resume.pdf'), {'timeout': 130}),
    (('upload', '#delete-0', '--file', 'Resume.pdf'), {'timeout': 130}),
    (('click', '#dropzone'), {}),
    (('click', '#delete-0'), {}),
    (('upload', '#_systemfield_resume', '--file', 'Other.pdf'), {'timeout': 130}),
    (('upload', '#_systemfield_resume', '--file', 'Resume.pdf', '--mode', 'drop'), {'timeout': 130}),
    (('upload', '#_systemfield_resume', '--file', 'Resume.pdf', '--mode', 'input'), {'timeout': 130}),
    (('upload', '#_systemfield_resume', '--file', 'Resume.pdf'), {}),
    (('upload', '#_systemfield_resume', '--file', 'Resume.pdf'), {'timeout': 130, 'extra': True}),
])
async def test_forbidden_producer_ipc_never_reaches_transport(monkeypatch, args, kwargs):
    raw, session, _ = setup_upload(monkeypatch)
    with pytest.raises(forms.Gate):
        await session.call(*args, **kwargs)
    assert raw.mutations == []
    assert not session.upload_attempted


@pytest.mark.parametrize('shape', [
    {'extraInert': True}, {'adjacentText': True}, {'changedDrop': True},
    {'twoItems': True}, {'displayName': 'Other.pdf'}, {'deleteText': True},
    {'outsideItem': True}, {'outsideDelete': True}, {'noTitle': True},
    {'secondDrop': True}, {'extraType': 'text'}, {'unreadableTree': True},
    {'treeNodes': 4097},
])
async def test_unexpected_after_structure_never_promotes(monkeypatch, shape):
    after = observed(attached=True, files=['Resume.pdf'], **shape)
    raw, session, _ = setup_upload(monkeypatch, after=after)
    identity = session.formless_identity
    with pytest.raises(forms.Gate):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1
    assert raw.post_pairs == 1
    assert not session.upload_pending


@pytest.mark.parametrize('files', [[], ['Other.pdf'], ['Resume.pdf', 'Other.pdf'], ['Resume.pdf', 'Resume.pdf']])
async def test_display_without_exact_original_file_proof_gates(monkeypatch, files):
    raw, session, _ = setup_upload(monkeypatch, after=observed(attached=True, files=files))
    identity = session.formless_identity
    with pytest.raises(forms.Gate):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1


async def test_sixth_pair_is_forbidden(monkeypatch):
    raw, session, _ = setup_upload(monkeypatch, unchanged=5)
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match='five observation pairs'):
        await upload(session)
    assert raw.post_pairs == 5
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1


async def test_deadline_and_pending_readonly_authority(monkeypatch):
    raw, session, _ = setup_upload(monkeypatch, unchanged=1)
    original = session._upload_pair
    async def delayed_pair():
        if raw.uploaded:
            await asyncio.sleep(2.01)
        return await original()
    monkeypatch.setattr(session, '_upload_pair', delayed_pair)
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match='deadline'):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1


async def test_pending_window_has_no_mutation_authority(monkeypatch):
    raw, session, _ = setup_upload(monkeypatch)
    async def forbidden():
        with pytest.raises(forms.Gate, match='read-only'):
            await session.call('fill', '#_systemfield_name', '--value', 'Test User')
    raw.pending_action = forbidden
    await upload(session)
    assert len(raw.mutations) == 1


async def test_independent_post_witness_disagreement_is_specific(monkeypatch):
    after = observed(attached=True, files=['Resume.pdf'])
    after[1]['entry']['upload_tree']['nodes'][0]['text'] = 'different'
    raw, session, _ = setup_upload(monkeypatch, after=after)
    with pytest.raises(forms.Gate, match='witness disagreement'):
        await upload(session)
    assert len(raw.mutations) == 1


@pytest.mark.parametrize('count, admitted', [(4096, True), (4097, False)])
def test_combined_element_text_cap_in_both_real_readers(count, admitted):
    page, fixed = observed(treeNodes=count)
    tree = page['entry']['upload_tree']
    assert tree == fixed['entry']['upload_tree']
    assert tree['complete'] is admitted
    assert len(tree['nodes']) == min(count, 4096)
    assert any(n['tag'] == '#text' for n in tree['nodes'])


@pytest.mark.parametrize('shape', [{'treeNodes': 4097}, {'unreadableTree': True}, {'secondDrop': True}])
async def test_incomplete_pre_pair_gates_before_ipc(monkeypatch, shape):
    raw, session, _ = setup_upload(monkeypatch, before=observed(**shape))
    with pytest.raises(forms.Gate):
        await upload(session)
    assert raw.mutations == []


async def test_full_tree_is_not_session_identity_during_approved_fill_churn(monkeypatch):
    raw, session, before = setup_upload(monkeypatch)
    after = observed(extraInert=True)
    assert forms._root_identity(before) == forms._root_identity(after[0])
    assert forms._page_identity(before) == forms._page_identity(after[0])
    raw.current, raw.fixed = deepcopy(after)
    await session.call('fill', '#_systemfield_name', '--value', 'Test User')
    assert not raw.mutations
    assert (await session.page())['controls'][0]['value'] == 'Test User'


@pytest.mark.parametrize('code,effect,empty,expected', [
    ('unsupported', 'none', True, 'unsupported with no effect'),
    (None, 'none', True, 'unclear'),
    ('timeout', 'none', True, 'unclear'),
    ('unsupported', '', True, 'unclear'),
    ('unsupported', 'unknown', True, 'unclear'),
    ('unsupported', 'none', False, 'unclear'),
])
async def test_refusal_requires_all_three_no_effect_predicates(monkeypatch, code, effect, empty, expected):
    exc = Unavailable('BrowserSkill operation unavailable: unsupported')
    exc.code, exc.effect_state = code, effect
    raw, session, _ = setup_upload(monkeypatch, refusal=exc)
    if not empty:
        original = raw.call
        async def nonempty(method, *args, **kwargs):
            if raw.uploaded:
                for snapshot in (raw.current, raw.fixed):
                    for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                        if c.get('type') == 'file':
                            c['files'] = ['Resume.pdf']
            return await original(method, *args, **kwargs)
        raw.call = nonempty
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match=expected):
        await upload(session)
    assert session.formless_identity == identity
    assert raw.post_pairs == 0
    assert len(raw.mutations) == 1
    with pytest.raises(forms.Gate):
        await upload(session)
    assert len(raw.mutations) == 1


async def test_file_permission_refusal_retains_specific_gate(monkeypatch):
    exc = Unavailable('synthetic')
    exc.file_access_required = True
    raw, session, _ = setup_upload(monkeypatch, refusal=exc)
    with pytest.raises(forms.Gate, match='Allow access to file URLs'):
        await upload(session)
    assert len(raw.mutations) == 1 and raw.post_pairs == 0


def test_optional_original_icon_removal_only():
    before, _ = observed()
    for keep in (True, False):
        after, _ = observed(attached=True, files=['Resume.pdf'], keepIcon=keep)
        assert forms._upload_delta(before, after, 'Resume.pdf')
        assert all('Test User' not in str(n) for n in after['entry']['upload_tree']['nodes'])


@pytest.mark.parametrize('method', ['click', 'upload'])
async def test_real_attached_delete_stays_passive_at_producer(monkeypatch, method):
    raw, session, _ = setup_upload(monkeypatch)
    await upload(session)
    args = ('click', '#delete-0') if method == 'click' else (
        'upload', '#delete-0', '--file', 'Resume.pdf')
    with pytest.raises(forms.Gate, match='unsupported root target'):
        await session.call(*args, **({'timeout': 130} if method == 'upload' else {}))
    assert len(raw.mutations) == 1


@pytest.mark.parametrize('script', ['document.body.remove()', 'fetch("/submit")'])
async def test_pending_window_refuses_arbitrary_evaluate(monkeypatch, script):
    raw, session, _ = setup_upload(monkeypatch)
    async def forbidden():
        with pytest.raises(forms.Gate, match='read-only'):
            await session.call('evaluate', script)
    raw.pending_action = forbidden
    await upload(session)
    assert len(raw.mutations) == 1


@pytest.mark.parametrize('problem', ['route', 'unreadable', 'original-input'])
async def test_refusal_requires_fresh_same_route_original_input(monkeypatch, problem):
    exc = Unavailable('synthetic')
    exc.code, exc.effect_state = 'unsupported', 'none'
    raw, session, _ = setup_upload(monkeypatch, refusal=exc)
    original = raw.call
    async def changed(method, *args, **kwargs):
        if raw.uploaded and method == 'evaluate':
            if problem == 'unreadable':
                raise OSError('synthetic read unavailable')
            for snapshot in (raw.current, raw.fixed):
                if problem == 'route':
                    snapshot['url'] += '/other'
                else:
                    snapshot['entry']['root_evidence'][2]['id'] = 'other-input'
        return await original(method, *args, **kwargs)
    raw.call = changed
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match='unclear'):
        await upload(session)
    assert session.formless_identity == identity and len(raw.mutations) == 1


async def test_original_input_semantically_rebinds_shifted_selectors(monkeypatch):
    after = observed(attached=True, files=['Resume.pdf'])
    for snapshot in after:
        for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_resume':
                c['selector'] = 'div.ashby-application-form-input-file > input:nth-of-type(1)'
            elif c['selector'] == '#dropzone':
                c['selector'] = 'div.ashby-application-form-input-file > button:nth-of-type(2)'
        for n in snapshot['entry']['upload_tree']['nodes']:
            if n['selector'] == '#_systemfield_resume':
                n['selector'] = 'div.ashby-application-form-input-file > input:nth-of-type(1)'
            elif n['selector'] == '#dropzone':
                n['selector'] = 'div.ashby-application-form-input-file > button:nth-of-type(2)'
    raw, session, _ = setup_upload(monkeypatch, after=after)
    await upload(session)
    assert forms._upload_input(session.upload_result, forms._upload_tree(session.upload_result))['id'] == '_systemfield_resume'
    assert session.formless_identity == forms._root_identity(after[0])
