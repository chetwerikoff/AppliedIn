"""Upload transition proof uses the real shared DOM readers, never a browser."""
import asyncio
import json
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill import Unavailable
from tests.tools.test_browser_formless import observed, root_world, RootSession
from tests.tools.test_browser_application_entry import entry


class UploadClock:
    def __init__(self):
        self.now = 0.0
        self.steps = []

    async def sleep(self, seconds):
        self.steps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def upload_clock(monkeypatch):
    clock = UploadClock()
    monkeypatch.setattr(forms, '_upload_clock', lambda: clock.now)
    monkeypatch.setattr(forms, '_upload_sleep', clock.sleep)
    return clock


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


@pytest.mark.parametrize('other_drift', [None, 'value', 'has_value'])
async def test_chooser_fakepath_only_exempts_verified_resume_value(monkeypatch, other_drift):
    after = observed(attached=True, files=['Resume.pdf'])
    # Chromium populates both properties when the chooser selects the file.
    for page in after:
        for c in page['controls'] + page['entry']['root_evidence']:
            if c['selector'] == '#_systemfield_resume':
                c.update(value=r'C:\fakepath\Resume.pdf', has_value=True)
            elif c['selector'] == '#_systemfield_name' and other_drift:
                c[other_drift] = 'Changed answer' if other_drift == 'value' else True
    raw, session, _ = setup_upload(monkeypatch, after=after)
    identity = session.formless_identity
    if other_drift:
        with pytest.raises(forms.Gate, match='Application control semantics changed'):
            await upload(session)
        assert session.formless_identity == identity
    else:
        await upload(session)
        page = await session.page()
        current = forms._upload_input(page, forms._upload_tree(page))
        assert current['value'] == r'C:\fakepath\Resume.pdf'
        assert current['has_value'] is True and current['files'] == ['Resume.pdf']
        assert session.formless_identity == forms._root_identity(page)
        assert not session.submit_authorized
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
    {'treeNodes': 4097}, {'deleteIcon': True, 'iconAction': True},
])
async def test_unexpected_after_structure_never_promotes(monkeypatch, shape):
    after = observed(attached=True, files=['Resume.pdf'], **shape)
    raw, session, _ = setup_upload(monkeypatch, after=after)
    identity = session.formless_identity
    # An attached but unadmitted delete is independently unsafe even when the
    # tree is opaque; the item-free opaque cases below isolate read-only settling.
    with pytest.raises(forms.Gate):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1
    assert raw.post_pairs == 1
    assert not session.upload_pending


@pytest.mark.parametrize('shape', [
    {'secondDrop': True}, {'unreadableTree': True}, {'treeNodes': 4097},
    {'disabledDrop': True},
])
async def test_opaque_post_ipc_only_observes_until_deadline(monkeypatch, upload_clock, shape):
    raw, session, _ = setup_upload(monkeypatch, after=observed(files=['Resume.pdf'], **shape))
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert upload_clock.now == 8.0 and upload_clock.steps == [0.25] * 32
    assert raw.post_pairs == 32 and len(raw.mutations) == 1
    assert session.formless_identity == identity and session.upload_result is None
    assert not session.upload_pending


async def test_unchanged_empty_pending_tree_waits_without_promotion(monkeypatch, upload_clock):
    raw, session, _ = setup_upload(monkeypatch, after=observed())
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert upload_clock.steps == [0.25] * 32
    assert session.formless_identity == identity and len(raw.mutations) == 1


@pytest.mark.parametrize('files', [[], ['Other.pdf'], ['Resume.pdf', 'Other.pdf'], ['Resume.pdf', 'Resume.pdf']])
async def test_display_without_exact_original_file_proof_gates(monkeypatch, files):
    raw, session, _ = setup_upload(monkeypatch, after=observed(attached=True, files=files))
    identity = session.formless_identity
    with pytest.raises(forms.Gate):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1


async def test_sixth_pair_is_forbidden(monkeypatch, upload_clock):
    # The old five-pair cap is replaced by time, never extra mutation authority.
    raw, session, _ = setup_upload(monkeypatch, unchanged=32)
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert raw.post_pairs == 32
    assert upload_clock.steps == [0.25] * 32
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1


async def test_deadline_and_pending_readonly_authority(monkeypatch, upload_clock):
    raw, session, _ = setup_upload(monkeypatch, unchanged=1)
    original = session._upload_pair
    async def delayed_pair(*, settle_before=None):
        if raw.uploaded:
            upload_clock.now += 8.0
        return await original(settle_before=settle_before)
    monkeypatch.setattr(session, '_upload_pair', delayed_pair)
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert session.formless_identity == identity
    assert len(raw.mutations) == 1
    assert not session.upload_pending


async def test_pending_window_has_no_mutation_authority(monkeypatch):
    raw, session, _ = setup_upload(monkeypatch)
    async def forbidden():
        with pytest.raises(forms.Gate, match='read-only'):
            await session.call('fill', '#_systemfield_name', '--value', 'Test User')
    raw.pending_action = forbidden
    await upload(session)
    assert len(raw.mutations) == 1


async def test_independent_post_witness_disagreement_never_proves_attachment(monkeypatch, upload_clock):
    after = observed(attached=True, files=['Resume.pdf'])
    after[1]['entry']['upload_tree']['nodes'][0]['text'] = 'different'
    raw, session, _ = setup_upload(monkeypatch, after=after)
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert upload_clock.steps == [0.25] * 32
    assert raw.post_pairs == 32 and len(raw.mutations) == 1
    assert session.formless_identity == identity and session.upload_result is None


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


@pytest.mark.parametrize('delete_icon', [False, True])
def test_optional_original_icon_removal_only(delete_icon):
    before, _ = observed()
    for keep in (True, False):
        after, _ = observed(attached=True, files=['Resume.pdf'], keepIcon=keep, deleteIcon=delete_icon)
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
            if n.get('target_selector') == '#_systemfield_resume':
                n['target_selector'] = 'div.ashby-application-form-input-file > input:nth-of-type(1)'
            elif n.get('target_selector') == '#dropzone':
                n['target_selector'] = 'div.ashby-application-form-input-file > button:nth-of-type(2)'
    raw, session, _ = setup_upload(monkeypatch, after=after)
    await upload(session)
    assert forms._upload_input(session.upload_result, forms._upload_tree(session.upload_result))['id'] == '_systemfield_resume'
    assert session.formless_identity == forms._root_identity(after[0])


@pytest.mark.parametrize('after_ipc', [False, True])
async def test_unavailable_paired_reader_is_a_gate_not_unknown(monkeypatch, after_ipc):
    raw, session, _ = setup_upload(monkeypatch)
    original = raw.call
    async def unavailable(method, *args, **kwargs):
        if method == 'evaluate' and raw.uploaded == after_ipc:
            # Let the ordinary producer check finish; break its final pre-pair.
            if after_ipc or sum(c[0] == 'evaluate' for c in raw.calls) >= 3:
                raise OSError('synthetic reader failure')
        return await original(method, *args, **kwargs)
    raw.call = unavailable
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match='witness is unavailable'):
        await upload(session)
    assert len(raw.mutations) == int(after_ipc)
    assert session.formless_identity == identity


@pytest.mark.parametrize('attrs,name,accepted', [
    ({}, 'Resume.pdf', True),
    ({'role': 'button'}, 'Resume.pdf', False),
    ({'tabindex': '0'}, 'Resume.pdf', False),
    ({}, 'Other.pdf', False),
])
async def test_filename_child_is_passive_and_exact(monkeypatch, attrs, name, accepted):
    after = observed(attached=True, files=['Resume.pdf'], filenameChild='span',
                     filenameAttrs=attrs, displayName=name)
    if not accepted:
        assert not after[0]['entry']['upload_tree']['attached']
        return
    raw, session, _ = setup_upload(monkeypatch, after=after)
    await upload(session)
    assert session.upload_result['entry']['upload_tree']['attached']
    assert len(raw.mutations) == 1


def test_upload_tree_hashes_inert_applicant_like_id():
    page, _ = observed(extraInert=True, inertId='test-user-private-contact')
    tree = page['entry']['upload_tree']
    assert tree['complete']
    assert 'test-user-private-contact' not in json.dumps(tree)


async def _filename_case(monkeypatch, attrs=None, count=1, accepted=False):
    after = observed(attached=True, files=['Resume.pdf'], filenameChild='span',
                     filenameSvgs=count, filenameSvgAttrs=attrs or {})
    assert after[0]['entry'] == after[1]['entry']
    raw, session, _ = setup_upload(monkeypatch, after=after)
    identity = session.formless_identity
    if accepted:
        await upload(session)
        assert session.upload_result['entry']['upload_tree']['attached']
    else:
        with pytest.raises(forms.Gate):
            await upload(session)
        assert session.formless_identity == identity
    assert len(raw.mutations) == 1
    assert not session.submit_authorized


async def test_ashby_filename_one_passive_svg_plus_span_passes(monkeypatch):
    await _filename_case(monkeypatch, accepted=True)


async def test_ashby_filename_two_svgs_gate(monkeypatch):
    await _filename_case(monkeypatch, count=2)


async def test_ashby_filename_svg_role_gate(monkeypatch):
    await _filename_case(monkeypatch, attrs={'role': 'img'})


async def test_ashby_filename_svg_handler_gate(monkeypatch):
    await _filename_case(monkeypatch, attrs={'onclick': 'syntheticAction()'})


class PairSequence(RootSession):
    """Two genuinely independent read values per fake-clock upload sample."""
    def __init__(self, before, pairs):
        super().__init__(*before)
        self.pairs = deepcopy(pairs)
        self.post_pairs = 0
        self.mutations = []

    async def call(self, method, *args, **kwargs):
        if method == 'upload':
            self.mutations.append(((method, *args), kwargs))
            return {}
        if method == 'evaluate' and self.mutations and args == (forms.ENTRY_PAGE,):
            self.current, self.fixed = deepcopy(self.pairs[
                min(self.post_pairs, len(self.pairs) - 1)])
            self.post_pairs += 1
        return await super().call(method, *args, **kwargs)


def sequenced_upload(monkeypatch, pairs):
    _, _, _, provenance = root_world(monkeypatch)
    before = observed()
    raw = PairSequence(before, pairs)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'),
                                  provenance, resume_path='Resume.pdf')
    session.formless_identity = forms._root_identity(before[0])
    return raw, session, before[0]


@pytest.mark.parametrize('drift,gate', [
    ('entry', 'Upload PAGE/fixed structural witness disagreement'),
    ('url', 'Application entry route or current row/config changed'),
])
async def test_pre_upload_pair_retains_two_distinct_immediate_gates(monkeypatch, drift, gate):
    before = observed()
    if drift == 'entry':
        before[1]['entry']['upload_tree']['nodes'][0]['text'] = 'different'
    else:
        before[1]['url'] += '/changed'
    raw, session, _ = setup_upload(monkeypatch, before=before)
    with pytest.raises(forms.Gate, match=gate):
        await upload(session)
    assert raw.mutations == [] and not session.upload_attempted


async def test_no_effect_fresh_pair_disagreement_keeps_unclear_outcome(monkeypatch):
    exc = Unavailable('synthetic unsupported')
    exc.code, exc.effect_state = 'unsupported', 'none'
    raw, session, _ = setup_upload(monkeypatch, refusal=exc)
    original = raw.call

    async def disagree_on_refusal(method, *args, **kwargs):
        if method == 'upload':
            raw.fixed['entry']['upload_tree']['nodes'][0]['text'] = 'different'
        return await original(method, *args, **kwargs)

    raw.call = disagree_on_refusal
    with pytest.raises(forms.Gate, match='The résumé upload outcome is unclear'):
        await upload(session)
    assert len(raw.mutations) == 1 and raw.post_pairs == 0
    assert not session.upload_pending


async def test_one_entry_split_then_opaque_agreement_then_exact_attachment(monkeypatch, upload_clock):
    first = observed(disabledDrop=True, files=['Resume.pdf'])
    first[1]['entry']['upload_tree']['nodes'][0]['text'] = 'pre-transition'
    pending = observed(disabledDrop=True, files=['Resume.pdf'])
    complete = observed(attached=True, files=['Resume.pdf'], displayName='Resume.pdf',
                        filenameChild='span', filenameSvgs=1)
    raw, session, before = sequenced_upload(monkeypatch, [first, pending, complete])
    promotions = []

    class TrackedEntry(forms._EntrySession):
        def __setattr__(self, key, value):
            if key == 'formless_identity':
                promotions.append(value)
            super().__setattr__(key, value)

    session.__class__ = TrackedEntry
    assert first[0]['url'] == first[1]['url'] == before['url']
    assert forms._formless_root(first[0]) == forms._formless_root(first[1]) == forms._formless_root(before)
    assert first[0]['entry'] != first[1]['entry']
    assert pending[0]['entry'] == pending[1]['entry']
    assert complete[0]['entry'] == complete[1]['entry']
    await upload(session)
    assert raw.post_pairs == 3 and upload_clock.steps == [0.25, 0.25]
    assert raw.mutations == [(('upload', '#dropzone', '--file', 'Resume.pdf'), {'timeout': 130})]
    assert len(promotions) == 1 and session.formless_identity == promotions[0]
    assert session.upload_result == complete[0]
    assert forms._upload_delta(before, session.upload_result, 'Resume.pdf') is True
    assert session.upload_result['entry']['upload_tree']['attached'] is True
    assert not any(c[0] in {'click', 'submit'} for c in raw.calls)
    assert not session.upload_pending and not session.submit_authorized


async def test_persistent_benign_entry_split_hits_original_eight_second_deadline(
        monkeypatch, upload_clock):
    disagree = observed(disabledDrop=True, files=['Resume.pdf'])
    disagree[1]['entry']['upload_tree']['nodes'][0]['text'] = 'pre-transition'
    raw, session, _ = sequenced_upload(monkeypatch, [disagree])
    identity = session.formless_identity
    with pytest.raises(TimeoutError):
        await upload(session)
    assert upload_clock.now == 8.0 and upload_clock.steps == [0.25] * 32
    assert raw.post_pairs == 32 and len(raw.mutations) == 1
    assert session.formless_identity == identity and session.upload_result is None
    assert not session.upload_pending and not session.submit_authorized
    assert not any(c[0] in {'click', 'submit'} for c in raw.calls)


@pytest.mark.parametrize('drift,reason', [
    ('page-root', 'Original application root or submit changed during upload'),
    ('fixed-root', 'Original application root or submit changed during upload'),
    ('page-submit', 'exact structural submit changed'),
    ('fixed-submit', 'exact structural submit changed'),
    ('page-inventory', 'The current form inventory is incomplete'),
    ('page-url', 'Application entry route or current row/config changed'),
    ('fixed-url', 'Application entry route or current row/config changed'),
    ('page-candidate', 'Application control semantics changed during upload'),
    ('fixed-candidate', 'Application control semantics changed during upload'),
    ('page-wrong-file', '(Only the exact staged résumé|unsupported custom control)'),
    ('fixed-wrong-file', '(Only the exact staged résumé|unsupported custom control)'),
    ('page-wrong-tree-filename', '(The exact passive attached-file item|unsupported custom control)'),
    ('fixed-wrong-tree-filename', '(The exact passive attached-file item|unsupported custom control)'),
])
async def test_entry_split_never_masks_one_sided_definitive_gate(
        monkeypatch, upload_clock, drift, reason):
    if drift.endswith('wrong-file'):
        first = list(observed(attached=True, files=['Other.pdf']))
        normal = observed(attached=True, files=['Resume.pdf'])
        target = 0 if drift.startswith('page-') else 1
        first[1 - target] = normal[1 - target]
    elif drift.endswith('wrong-tree-filename'):
        first = list(observed(attached=True, files=['Resume.pdf'], displayName='Other.pdf'))
        normal = observed(attached=True, files=['Resume.pdf'], displayName='Resume.pdf')
        target = 0 if drift.startswith('page-') else 1
        first[1 - target] = normal[1 - target]
    else:
        first = list(observed(disabledDrop=True, files=['Resume.pdf']))
    first[1]['entry']['upload_tree']['nodes'][0]['text'] = 'pre-transition'
    page = first[0] if drift.startswith('page-') else first[1]
    if drift.endswith('root'):
        page['entry']['formless']['root'] = '#changed-application-root'
    elif drift.endswith('submit'):
        page['entry']['formless']['submit'] = '#changed-submit'
    elif drift == 'page-inventory':
        page['inventory_verified'] = False
    elif drift.endswith('url'):
        page['url'] += '/changed'
    elif drift.endswith('candidate'):
        resume = next(c for c in page['entry']['root_evidence']
                      if c.get('id') == '_systemfield_name')
        selector = resume['selector']
        for control in page['controls'] + page['entry']['root_evidence']:
            if control.get('selector') == selector:
                control['label'] = 'Changed synthetic candidate'
    raw, session, _ = sequenced_upload(monkeypatch, [first])
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match=reason):
        await upload(session)
    assert raw.post_pairs == 1 and upload_clock.steps == []
    assert len(raw.mutations) == 1
    assert session.formless_identity == identity and session.upload_result is None
    assert not session.upload_pending and not session.submit_authorized
    assert not any(c[0] in {'click', 'submit'} for c in raw.calls)
    if drift.endswith('root'):
        page['entry']['formless']['root'] = '#changed-application-root'
    elif drift.endswith('submit'):
        page['entry']['formless']['submit'] = '#changed-submit'
    elif drift == 'page-inventory':
        page['inventory_verified'] = False
    elif drift.endswith('url'):
        page['url'] += '/changed'
    elif drift.endswith('candidate'):
        for collection in (page['controls'], page['entry']['root_evidence']):
            for control in collection:
                if control.get('id') == '_systemfield_name':
                    control['label'] = 'Changed synthetic candidate'
    raw, session, _ = sequenced_upload(monkeypatch, [first])
    identity = session.formless_identity
    with pytest.raises(forms.Gate, match=reason):
        await upload(session)
    assert raw.post_pairs == 1 and upload_clock.steps == []
    assert len(raw.mutations) == 1
    assert session.formless_identity == identity and session.upload_result is None
    assert not session.upload_pending and not session.submit_authorized
    assert not any(c[0] in {'click', 'submit'} for c in raw.calls)
