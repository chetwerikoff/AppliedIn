"""Real three-child Ashby structure, exercised offline by both production readers.

No fixture marker supplies authority: the normal tracked entry must admit two
independent complete observations before the direct human question is reachable.
"""
from copy import deepcopy
import asyncio
import logging
import re
import sys
import time
from pathlib import Path

import pytest

from tools import browser_skill_apply as forms
from tools.browser_skill import Session as ProductionBrowserSession
from tools.browser_skill_dom import PAGE, _ENTRY_OBSERVATION
from tests.tools.test_browser_application_entry import (
    COMPANY, PK, dom, entry, posting, world, attempt, no_mutations,
)

def _entry_js_const(script, name):
    """Extract a top-level JavaScript const without evaluating either reader."""
    found = re.search(rf'(?m)^  const {re.escape(name)}\s*=', script)
    assert found, f'Missing JS binding: {name}'
    tail = script[found.start():]
    if re.match(r'  const \w+ = \w+ => \{', tail):
        end = tail.index('\n  };') + len('\n  };')
    else:
        end = tail.index(';') + 1
    return tail[:end]


def _entry_host_identifiers(script, observation=_ENTRY_OBSERVATION):
    """Discover host references without mistaking entry-local names for them."""
    prefix, separator, _ = script.partition(observation)
    assert separator and script.count(observation) == 1
    host = set(re.findall(
        r'(?m)^  (?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', prefix))
    # Preserve offsets and braces when masking strings/comments; lexical scopes
    # matter because PAGE's nodes is shadowed inside entry's inertSibling.
    code = re.sub(
        r"""(?s)'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*""",
        lambda match: re.sub(r'[^\n]', ' ', match.group()), observation)
    parents, locals_by_scope, stack = [None], [set()], [0]
    token_scopes, references = {}, []
    for token in re.finditer(
            r'(?P<brace>[{}])|(?P<name>(?<![\w$.])[A-Za-z_$][\w$]*)', code):
        brace = token.group('brace')
        if brace == '{':
            parents.append(stack[-1])
            locals_by_scope.append(set())
            stack.append(len(parents) - 1)
        elif brace == '}':
            assert len(stack) > 1, 'Unbalanced entry JavaScript scope'
            stack.pop()
        else:
            token_scopes[token.start('name')] = stack[-1]
            if not re.match(r'\s*:', code[token.end():]):
                references.append((token.group('name'), stack[-1]))
    assert stack == [0], 'Unbalanced entry JavaScript scope'
    for declaration in re.finditer(
            r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?==|\bof\b|\bin\b|;)', code):
        locals_by_scope[token_scopes[declaration.start(1)]].add(declaration.group(1))

    def from_host(name, scope):
        while scope is not None:
            if name in locals_by_scope[scope]:
                return False
            scope = parents[scope]
        return True

    used = {name for name, scope in references if name in host and from_host(name, scope)}
    return {name: _entry_js_const(script, name) for name in used}


def test_entry_readers_have_identical_host_bindings():
    """No new host predicate may silently change the independently read entry."""
    page = _entry_host_identifiers(forms.ENTRY_PAGE)
    fixed = _entry_host_identifiers(forms._ENTRY_FIXED_CONTROL_WITNESS)
    assert page == fixed
    assert set(page) == {'clean', 'selector', 'query'}
    # These other entry dependencies are defined inside the identical injected
    # block, not borrowed from either host.
    shared = {'rectOf', 'container', 'structuralRoot', 'exactSubmit', 'forms',
              'containers', 'submitters', 'excluded', 'entryLabel',
              'candidateVisible', 'entryVisible', 'entryEvidenceVisible'}
    declarations = set(re.findall(
        r'(?m)^  (?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', _ENTRY_OBSERVATION))
    assert shared <= declarations


def test_entry_host_audit_distinguishes_shadowed_and_real_host_reads():
    """A local nodes is safe; using PAGE's nodes must fail parity."""
    assert 'nodes' not in _entry_host_identifiers(forms.ENTRY_PAGE)
    extra = _ENTRY_OBSERVATION.replace(
        '  const reservedButtons = ',
        '  const syntheticHostRead = nodes.length;\n  const reservedButtons = ', 1)
    assert extra != _ENTRY_OBSERVATION
    page = _entry_host_identifiers(
        forms.ENTRY_PAGE.replace(_ENTRY_OBSERVATION, extra), extra)
    fixed = _entry_host_identifiers(
        forms._ENTRY_FIXED_CONTROL_WITNESS.replace(_ENTRY_OBSERVATION, extra), extra)
    assert 'nodes' in page and 'nodes' not in fixed
    assert page != fixed


def test_entry_host_audit_detects_changed_host_definitions():
    """A real dependency with diverging definitions must break the parity test."""
    changed_page = forms.ENTRY_PAGE.replace(
        '  const clean = s =>', '  const clean = changed =>', 1)
    assert changed_page != forms.ENTRY_PAGE
    assert (_entry_host_identifiers(changed_page) !=
            _entry_host_identifiers(forms._ENTRY_FIXED_CONTROL_WITNESS))


def test_entry_visibility_uses_exact_fixed_witness_predicate():
    """Keep the root proof strict without altering ordinary host control reads."""
    local = _entry_js_const(_ENTRY_OBSERVATION, 'entryEvidenceVisible')
    fixed = _entry_js_const(forms._FIXED_CONTROL_WITNESS, 'visible')
    assert local.replace('const entryEvidenceVisible', 'const visible', 1) == fixed
    assert 'if (!entryEvidenceVisible(e)) return false;' in _ENTRY_OBSERVATION
    assert 'visible:entryVisible(e)' in _ENTRY_OBSERVATION
    assert 'visible:entryEvidenceVisible(e)' in _ENTRY_OBSERVATION
    assert '!entryEvidenceVisible(drop)' in _ENTRY_OBSERVATION
    assert not re.search(r'(?<![\w$.])visible\s*\(', _ENTRY_OBSERVATION)


@pytest.mark.parametrize('case, clause', [
    ('opacity:0 ancestor', '!(Number(style.opacity) > 0)'),
    ('display:none ancestor', "style.display === 'none'"),
])
def test_root_evidence_visibility_rejects_hidden_ancestors(case, clause):
    """Static ancestor proof: no browser or JavaScript execution is necessary."""
    predicate = _entry_js_const(_ENTRY_OBSERVATION, 'entryEvidenceVisible')
    assert 'for (let n=e; n && n.nodeType===1; n=n.parentElement)' in predicate
    assert clause in predicate, case
    assert 'return false;' in predicate
    assert 'visible:entryVisible(e)' in _ENTRY_OBSERVATION

from tests.tools.test_browser_controller_support import install_controller_session



@pytest.fixture(autouse=True)
def _arch1332_formless_timing(request):
    """Measure the fifteen original slow permanent-negatives in exact-head CI."""
    name = getattr(request.node, 'originalname', None) or request.node.name.split('[')[0]
    params = request.node.callspec.params if hasattr(request.node, 'callspec') else {}
    cause = params.get('cause')
    targeted = (
        (name == 'test_real_dom_root_edges_and_hidden_fields_gate_before_model'
         and cause in {'sibling identity mismatch', 'ambiguous application roots'})
        or (name == 'test_normal_apply_preserves_specific_danger_before_model_or_ipc'
            and cause in {'ambiguous application roots', 'unsupported radio',
                          'unsupported checkbox', 'unsupported autocomplete/combobox',
                          'unsupported contenteditable'})
        or name in {'test_complete_two_formless_roots_gate_before_model_and_ipc',
                    'test_nested_interactive_sibling_gates_before_model_and_ipc'}
    )
    if not targeted:
        yield
        return
    started = time.perf_counter()
    yield
    elapsed = time.perf_counter() - started
    assert elapsed <= 5.0, f'ARCH13:32 {request.node.nodeid}: {elapsed:.3f}s exceeds 5s'
    request.node.warn(pytest.PytestWarning(
        f'ARCH13:32 measured {request.node.nodeid} = {elapsed:.3f}s (<=5s)'))

def observed(**changes):
    # Let each real production reader observe the literal tracked PAGE URL,
    # rather than asserting route identity on a post-processed fixture result.
    spec = dict(root=False, formless=True, url=entry('ashby'), **changes)
    page = dom(spec)
    fixed = dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)
    return page, fixed


def resume_set_name(script):
    """The file name of the code-owned direct résumé set, or None for any other script."""
    import json
    prefix = '(() => { const p = '
    if not isinstance(script, str) or not script.startswith(prefix):
        return None
    payload, _ = json.JSONDecoder().raw_decode(script[len(prefix):])
    return payload['name'] if payload.get('selector') == forms._ASHBY_RESUME_SELECTOR else None


def effect_calls(calls):
    """Non-read IPC in order, with the direct résumé set shown as ('set', name)."""
    out = []
    for c in calls:
        name = resume_set_name(c[1]) if c[0] == 'evaluate' and len(c) > 1 else None
        if name:
            out.append(('set', name))
        elif c[0] not in {'evaluate', 'navigate'}:
            out.append(c)
    return out


def set_resume_files(snapshots, name):
    """Live Ashby keeps the same input node and only its files change (FF-224g probe)."""
    for snapshot in snapshots:
        for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
            if c['selector'] == forms._ASHBY_RESUME_SELECTOR:
                c['files'] = [name]


class RootSession:
    def __init__(self, page, fixed):
        self.current, self.fixed = deepcopy(page), deepcopy(fixed)
        self.calls = []
        self.after_click = None
        self.before_click = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call(self, method, *args, **kwargs):
        self.calls.append((method, *args))
        if method == 'evaluate' and resume_set_name(args[0]):
            # The page assigns File.name, a basename, even though the bytes
            # come from an absolute per-attempt snapshot path.
            name = resume_set_name(args[0])
            set_resume_files((self.current, self.fixed), name)
            return {'value': {'set': True, 'files': [name]}}
        if method == 'evaluate':
            if args == (forms.ENTRY_PAGE,):
                return {'value': deepcopy(self.current)}
            assert args == (forms._ENTRY_FIXED_CONTROL_WITNESS,)
            return {'value': deepcopy(self.fixed)}
        if method == 'navigate':
            return {}
        if method == 'fill':
            selector, _, value = args
            for snapshot in (self.current, self.fixed):
                for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                    if c['selector'] == selector:
                        c.update(value=value, has_value=True)
            return {}
        if method == 'upload':
            pytest.fail('FF-224g: the Ashby résumé is a direct file set, never a chooser upload')
        if method == 'click':
            if self.before_click:
                self.before_click()
            if self.after_click:
                self.current, self.fixed = deepcopy(self.after_click)
            return {}
        pytest.fail(f'Unexpected mutating IPC: {method}')


def root_world(monkeypatch, **changes):
    row, configs, _, decision = world(monkeypatch)
    page, fixed = observed(**changes)
    raw = RootSession(page, fixed)
    monkeypatch.setattr(forms.bsk, 'Session', lambda *a: raw)
    provenance = forms._application_entry(row, PK, COMPANY, posting('ashby'), configs)
    return row, raw, decision, provenance


async def admitted(monkeypatch):
    row, raw, decision, provenance = root_world(monkeypatch)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=asyncio.get_running_loop().time() + 10)
    return row, raw, session, page, provenance


async def prepared_shape_refusal(monkeypatch, tmp_path, changes, cause):
    """ARCH21:18 / AC2/AC9: obsolete root proof is final evidence only."""
    # Production permits late hydration up to 90s; this fake never changes shape.
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    row, raw, decide, provenance = root_world(monkeypatch, **changes)
    transport, tracking, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    file = next(c for c in raw.current['controls']
                if c['type'] == 'file' and c['label'] == 'Resume')
    button = next(c for c in raw.current['controls'] if c['label'] == 'Submit application')
    decide.side_effect = [dict(action='upload', selector=file['selector']),
                          dict(action='submit', selector=button['selector'])]
    result = await forms.apply(posting('ashby'), COMPANY, {}, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert result['status'] == 'gate', result
    # The model's upload may take the one reversible direct set, exactly as the
    # chooser did before FF-224g; the structural refusal still stops Submit.
    assert (cause in result['question']
            or 'missing unique labelled resume receiver' in result['question']), result
    effects = effect_calls(raw.calls)
    assert effects in ([], [('set', Path(transport._application_controller.resume_path).name)])
    assert not transport._application_controller.committed


def _hidden_enterprise_anchor(kind='R'):
    # Scrubbed 256x60 enterprise-anchor shape, with no provider child reads.
    return dict(frames=[kind], edit={
        'rect': {'width': 256, 'height': 60, 'right': 256, 'bottom': 60},
        'style': {'visibility': 'hidden', 'display': 'inline'},
    })


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_hidden_enterprise_without_native_or_structural_root_still_gates(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(root=False, formless=True, missingPane=True, url=entry('ashby'),
                    **_hidden_enterprise_anchor()), script)
    assert page['entry']['root'] is None
    assert page['entry']['formless']['root'] == ''
    frame = page['entry']['frames'][0]
    assert frame['status'] == 'unknown'
    assert frame['root'] == '' and frame['outside'] is False
    assert frame['form_controls'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_hidden_enterprise_inside_existing_ashby_structural_root_still_gates(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    opener = '(() => {\n'
    assert script.startswith(opener)
    # Reparent only the synthetic iframe inside the container. The real
    # reader still resolves the same complete three-child structural root.
    script = script.replace(opener, opener + """
  {
    const anchor = document.querySelector('iframe');
    const container = document.querySelector('.ashby-application-form-container');
    const siblings = anchor.parentElement.children;
    siblings.splice(siblings.indexOf(anchor), 1);
    anchor.parentElement = container;
    container.children.push(anchor);
  }
""", 1)
    page = dom(dict(root=False, formless=True, url=entry('ashby'),
                    **_hidden_enterprise_anchor()), script)
    assert page['entry']['formless']['root'] == '#application-root'
    frame = page['entry']['frames'][0]
    assert frame['root'] == '#application-root' and frame['outside'] is False
    assert frame['status'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_non_ashby_formless_lookalike_cannot_borrow_hidden_r_fallback(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(root=False, formless=True, url=entry('lever'),
                    **_hidden_enterprise_anchor()), script)
    assert page['entry']['root'] is None
    assert page['entry']['formless']['root'] == '#application-root'
    frame = page['entry']['frames'][0]
    assert frame['root'] == '#application-root' and frame['outside'] is True
    assert frame['status'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda url: url == entry('lever'), None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('edit', [
    {'src': 'https://www.recaptcha.net/recaptcha/api2/anchor'},
    {'src': 'https://widgets.example.test/recaptcha/enterprise/anchor'},
    {'style': {'visibility': 'visible', 'display': 'inline'}},
])
def test_ashby_structural_hidden_r_keeps_provider_path_and_visibility_rules(reader, edit):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    spec = _hidden_enterprise_anchor()
    spec['edit'].update(edit)
    page = dom(dict(root=False, formless=True, url=entry('ashby'), **spec), script)
    assert page['entry']['formless']['root'] == '#application-root'
    assert page['entry']['frames'][0]['status'] == 'unknown'
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_ashby_hidden_r_does_not_hide_other_frames_or_visible_challenge(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = dom(dict(root=False, formless=True, url=entry('ashby'), sibling=True,
                    **_hidden_enterprise_anchor()), script)
    assert [f['status'] for f in page['entry']['frames']] == ['R', 'unknown']
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)

    shown = dom(dict(root=False, formless=True, url=entry('ashby'),
                     frames=['B', 'R'], visible=True), script)
    assert [f['status'] for f in shown['entry']['frames']] == [
        'visible-recaptcha', 'unknown']
    with pytest.raises(forms.CaptchaGate, match='visible reCAPTCHA Enterprise challenge'):
        forms._entry_safety(shown, lambda url: url == entry('ashby'), None)


def _synthetic_enterprise_pair(*, ats='ashby', missing_root=False, inside=False,
                               sibling=False, visible_bframe=False, kind='R', edit=None):
    shape = _hidden_enterprise_anchor(kind)
    if edit:
        shape['edit'].update(edit)
    shape.update(root=False, formless=True, url=entry(ats))
    if missing_root:
        shape['missingPane'] = True
    if sibling:
        shape['sibling'] = True
    if visible_bframe:
        shape['frames'] = ['R', 'B']
    injection = ''
    if inside:
        injection = """
  {
    const anchor = document.querySelector('iframe');
    const container = document.querySelector('.ashby-application-form-container');
    const siblings = anchor.parentElement.children;
    siblings.splice(siblings.indexOf(anchor), 1);
    anchor.parentElement = container;
    container.children.push(anchor);
  }
"""
    if visible_bframe:
        injection = """
  {
    const frames = document.querySelectorAll('iframe');
    frames[1].style.visibility = 'visible';
  }
"""
    def reader(script):
        if not injection:
            return script
        opener = '(() => {\n'
        assert script.startswith(opener)
        return script.replace(opener, opener + injection, 1)
    return (dom(shape, reader(forms.ENTRY_PAGE)),
            dom(shape, reader(forms._ENTRY_FIXED_CONTROL_WITNESS)))


def _temporal_entry_session(monkeypatch, first, second=None, *, ats='ashby'):
    from tests.tools.test_browser_application_entry import fake_clock
    clock = fake_clock(monkeypatch)
    row, configs, _, decision = world(monkeypatch, ats)
    raw = RootSession(*first)
    reads = [0]
    original = raw.call

    async def stepped(method, *args, **kwargs):
        if method == 'evaluate' and args == (forms.ENTRY_PAGE,):
            reads[0] += 1
            if reads[0] == 2 and second is not None:
                raw.current, raw.fixed = deepcopy(second)
        return await original(method, *args, **kwargs)

    raw.call = stepped
    provenance = forms._application_entry(row, PK, COMPANY, posting(ats), configs)
    scoped = forms._EntrySession(raw, lambda url: url == entry(ats), provenance)
    return clock, raw, scoped, provenance, decision, reads


@pytest.mark.parametrize('kind', ['R', 'B'])
@pytest.mark.parametrize('display_none', [False, True])
async def test_rootless_hidden_enterprise_retries_until_real_structural_root(monkeypatch, kind,
                                                                            display_none):
    edit = {'style': {'visibility': 'visible', 'display': 'none'}} if display_none else None
    first = _synthetic_enterprise_pair(missing_root=True, kind=kind, edit=edit)
    second = _synthetic_enterprise_pair(kind=kind, edit=edit)
    assert first[0]['entry']['root'] is None
    assert first[0]['entry']['formless']['root'] == ''
    assert first[0]['entry']['frames'][0]['status'] == 'unknown'
    assert first[0]['entry']['frames'][0]['rootless_hidden_r'] is True
    assert first[0]['entry'] == first[1]['entry']
    assert second[0]['entry']['formless']['root'] == '#application-root'
    assert second[0]['entry']['frames'][0]['status'] == 'R'
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, first, second)
    start = clock[0]
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert page['entry']['frames'][0]['status'] == 'R'
    assert page['entry']['frames'][0]['outside'] is True
    assert reads[0] >= 4  # Fresh PAGE reads surround both independent witnesses.
    assert start < clock[0] < start + forms._ASHBY_WAIT_SECONDS
    assert session._readiness is False
    assert not decision.await_count
    no_mutations(raw)


async def test_rootless_hidden_enterprise_expiry_keeps_original_gate_details(monkeypatch):
    first = _synthetic_enterprise_pair(missing_root=True)
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(monkeypatch, first)
    start = clock[0]
    # Issue #214 category (a): the hidden exact-origin Enterprise frame is
    # frame-only admitted, so expiry reports the still-missing root instead.
    with pytest.raises(forms.Gate, match='no unique application root after bounded wait') as raised:
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    detail = str(raised.value)
    assert not isinstance(raised.value, (forms._EntryNotReady, forms.CaptchaArtifactGate))
    assert 'read.frames_admitted=true' in detail
    assert 'root.owner_proven=false' in detail
    assert clock[0] - start == pytest.approx(forms._ASHBY_WAIT_SECONDS)
    assert reads[0] > 1 and session._readiness is False
    assert not decision.await_count
    no_mutations(raw)


async def test_root_appears_around_hidden_enterprise_anchor_and_immediately_gates(monkeypatch):
    first = _synthetic_enterprise_pair(missing_root=True)
    second = _synthetic_enterprise_pair(inside=True)
    assert second[0]['entry']['formless']['root'] == '#application-root'
    assert second[0]['entry']['frames'][0]['status'] == 'unknown'
    assert second[0]['entry']['frames'][0].get('rootless_hidden_r') is not True
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, first, second)
    start = clock[0]
    # Issue #214 category (a): the hidden exact-origin Enterprise frame inside
    # the hydrated root is frame-only admitted; it stays reader-status unknown.
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert page['entry']['formless']['root'] == '#application-root'
    assert page['entry']['frames'][0]['status'] == 'unknown'
    assert clock[0] - start < forms._ASHBY_WAIT_SECONDS
    assert session._readiness is False and not decision.await_count
    no_mutations(raw)


async def test_rootless_hidden_enterprise_plus_unknown_gates_on_first_read(monkeypatch):
    first = _synthetic_enterprise_pair(missing_root=True, sibling=True)
    assert [f['status'] for f in first[0]['entry']['frames']] == ['unknown', 'unknown']
    assert first[0]['entry']['frames'][0]['rootless_hidden_r'] is True
    assert first[0]['entry']['frames'][1].get('rootless_hidden_r') is not True
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(monkeypatch, first)
    start = clock[0]
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert reads[0] == 1 and clock[0] == start
    assert not decision.await_count
    no_mutations(raw)


@pytest.mark.parametrize('edit', [
    {'src': 'https://www.recaptcha.net/recaptcha/api2/anchor'},
    {'src': 'https://widgets.example.test/recaptcha/enterprise/anchor'},
    {'src': '/recaptcha/enterprise/anchor'},
    {'style': {'visibility': 'visible', 'display': 'inline'}},
])
async def test_rootless_enterprise_lookalikes_cannot_use_readiness_deferral(monkeypatch, edit):
    first = _synthetic_enterprise_pair(missing_root=True, edit=edit)
    assert first[0]['entry']['frames'][0]['status'] == 'unknown'
    assert first[0]['entry']['frames'][0].get('rootless_hidden_r') is not True
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(monkeypatch, first)
    start = clock[0]
    if edit == {'src': 'https://www.recaptcha.net/recaptcha/api2/anchor'}:
        # Issue #214 category (a): hidden exact-origin api2 is frame-only
        # admitted without readiness deferral; the missing root still gates.
        with pytest.raises(forms.Gate, match='no unique application root after bounded wait'):
            await forms._stable_application_form(
                session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
        assert session._readiness is False
        assert not decision.await_count
        no_mutations(raw)
        return
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert reads[0] == 1 and clock[0] == start
    assert not decision.await_count
    no_mutations(raw)


async def test_rootless_hidden_recap_does_not_defer_non_ashby_entry(monkeypatch):
    first = _synthetic_enterprise_pair(ats='lever', missing_root=True)
    assert first[0]['entry']['frames'][0].get('rootless_hidden_r') is not True
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, first, ats='lever')
    start = clock[0]
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._APPLICATION_WAIT_SECONDS)
    assert reads[0] == 1 and clock[0] == start
    assert not decision.await_count
    no_mutations(raw)


async def test_rootless_hidden_recap_keeps_visible_challenge_precedence(monkeypatch):
    first = _synthetic_enterprise_pair(missing_root=True, visible_bframe=True)
    assert [f['status'] for f in first[0]['entry']['frames']] == [
        'unknown', 'visible-recaptcha']
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(monkeypatch, first)
    start = clock[0]
    with pytest.raises(forms.CaptchaGate, match='visible reCAPTCHA Enterprise challenge'):
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert reads[0] == 1 and clock[0] == start
    assert not decision.await_count
    no_mutations(raw)


async def test_unknown_frames_are_not_a_reversible_per_read_gate(monkeypatch):
    first = _synthetic_enterprise_pair(missing_root=True)
    second = _synthetic_enterprise_pair()
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, first, second)
    await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=clock[0] + forms._ASHBY_WAIT_SECONDS)
    assert session._readiness is False
    raw.current, raw.fixed = deepcopy(first)
    assert (await session.page())['entry']['frames'][0]['status'] == 'unknown'
    session.committing = True
    # Preparation/readback cannot restore the retired per-action frame gate.
    for _ in range(2):
        assert (await session.page())['entry']['frames'][0]['status'] == 'unknown'
    assert not decision.await_count
    no_mutations(raw)


async def test_preparation_can_continue_when_fixed_reader_disagrees(monkeypatch):
    pair = _synthetic_enterprise_pair()
    pair[1]['controls'][0]['disabled'] = not pair[1]['controls'][0].get('disabled', False)
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, pair)
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=clock[0] + forms._ASHBY_WAIT_SECONDS)
    assert page['url'] == entry('ashby')
    assert any(call[1] == forms._ENTRY_FIXED_CONTROL_WITNESS
               for call in raw.calls)
    assert reads[0] >= 4 and not decision.await_count
    no_mutations(raw)


def test_readers_independently_agree_on_real_shape_and_never_read_autofill():
    page, fixed = observed()
    assert page['entry'] == fixed['entry']
    root = page['entry']['formless']
    assert root['root'] == '#application-root'
    assert root['submit'] == '#structural-submit'
    assert root['excluded'] == ['#autofill-file', '#autofill-button']
    assert not {c['selector'] for c in page['controls']} & set(root['excluded'])
    resume = next(c for c in page['entry']['root_evidence'] if c['id'] == '_systemfield_resume')
    assert resume['associated'] and resume['required'] and resume['in_container']
    assert resume['files'] == []
    assert page['entry']['root'] is None


async def test_formless_apply_without_staged_pdf_stops_before_model(monkeypatch):
    _, raw, decision, _ = root_world(monkeypatch)
    result = await attempt()
    assert result['status'] == 'gate', result
    assert result['reason'] == 'unknown_field'
    assert 'row-tailored staged PDF is missing' in result['question']
    assert result['form_question'] is None
    assert decision.await_count == 0
    no_mutations(raw)

@pytest.mark.parametrize('changes', [
    {'duplicate': True},
    {'outside': '_systemfield_email'},
    {'hiddenOutside': True},
    {'duplicateLabel': True},
    {'extraType': 'select'},
    {'extraType': 'radio'},
    {'extraType': 'checkbox'},
    {'extraType': 'hidden'},
    {'extraType': 'file'},
    {'extraType': 'text', 'extraAttrs': {'role': 'combobox'}},
    {'extraType': 'text', 'extraAttrs': {'contenteditable': 'true'}},
])
def test_untrusted_formless_shapes_do_not_authorize_the_final_root(monkeypatch, changes):
    _, raw, _, provenance = root_world(monkeypatch, **changes)
    # Structural disagreements are represented in the current reader's
    # final root evidence. No per-control mutation refusal is reintroduced.
    root = raw.current['entry'].get('formless') or {}
    assert isinstance(root, dict)
    if root.get('error'):
        with pytest.raises(forms.Gate, match='final Ashby application container'):
            forms._formless_boundary(raw.current, provenance)

async def test_changed_root_after_admission_can_be_reobserved_before_final_submit(monkeypatch):
    """A changed historical root sibling is not an irreversible application effect."""
    _, raw, session, _, _ = await admitted(monkeypatch)
    raw.current['entry']['formless']['pane'] = '#changed-pane'
    fresh = await session.page()
    assert fresh['entry']['formless']['pane'] == '#changed-pane'
    no_mutations(raw)


def approve_fields(row, page, fixed, provenance):
    """Historical synthetic fixture entrypoint: ordinary bank facts only.

    No per-control grant, witness or authorization is issued.
    """
    return {'Name': 'Test User', 'Email': 'test@example-co.test'}


def action_sequence():
    # The application loop stages the unique PDF before its first decision.
    return [dict(action='fill', selector='#_systemfield_name', fact='Name'),
            dict(action='fill', selector='#_systemfield_email', fact='Email'),
            dict(action='submit', selector='#structural-submit')]


@pytest.mark.parametrize('outcome', ['confirmed', 'unconfirmed', 'foreign', 'click-fault', 'witness-mismatch'])
async def test_real_controller_fills_uploads_holds_and_classifies_fake_submit(monkeypatch, tmp_path, outcome):
    row, raw, decide, provenance = root_world(monkeypatch)
    transport, tracking, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    facts = approve_fields(row, raw.current, raw.fixed, provenance)
    decide.side_effect = action_sequence()
    checkpoints = []
    # #139 AC1/AC8: exercise the real producer and its own durable hold,
    # never an offline facade permit or a no-op authorization stub.
    real_hold, real_check = forms.hold_possible_submission, forms.check_before_committing_click
    def hold(*a, **kw):
        checkpoints.append('held')
        real_hold(*a, **kw)
    def check(*a, **kw):
        checkpoints.append('checked')
        real_check(*a, **kw)
    monkeypatch.setattr(forms, 'hold_possible_submission', hold)
    monkeypatch.setattr(forms, 'check_before_committing_click', check)
    def clicked():
        assert checkpoints == ['held', 'checked']
        # The committing boundary consumes its own current PAGE. A second
        # all-control fixed witness is no longer a Submit precondition.
        page_reads = [i for i, call in enumerate(raw.calls)
                      if call == ('evaluate', forms.ENTRY_PAGE)]
        assert page_reads
        if outcome == 'click-fault':
            raise OSError('Synthetic transport lost after possibly committing')
    raw.before_click = clicked
    if outcome in {'confirmed', 'foreign', 'witness-mismatch'}:
        after, fixed = deepcopy(raw.current), deepcopy(raw.fixed)
        for snapshot in (after, fixed):
            snapshot.update(controls=[], text='Thank you for applying', title='Application received')
            snapshot['entry'].update(root=None, formless=None, root_evidence=[])
            if outcome == 'foreign':
                snapshot['url'] += '/other-job'
        if outcome == 'witness-mismatch':
            fixed['url'] += '/unrelated'
        raw.after_click = after, fixed
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(forms.asyncio, 'sleep', fast_sleep)
    result = await forms.apply(posting('ashby'), COMPANY, facts, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert result['status'] == ('applied' if outcome == 'confirmed' else 'uncertain'), result
    task = decide.await_args_list[0].args[0]
    # The model receives fact *keys* and may prepare fields without widget
    # permits. The only committing authority remains raw check_pre_submit.
    assert 'Use existing owner fact keys' in task
    assert 'without any per-control approval' in task
    assert 'Never invent missing owner facts' in task
    assert all(value not in task for value in facts.values())
    assert effect_calls(raw.calls) == [
        ('set', Path(transport._application_controller.resume_path).name),
        ('fill', '#_systemfield_name', '--value', 'Test User'),
        ('fill', '#_systemfield_email', '--value', 'test@example-co.test'),
        ('click', '#structural-submit')]
    assert checkpoints == ['held', 'checked']


@pytest.mark.parametrize('failure', ['missing-attachment', 'wrong-job', 'stale-seed', 'duplicate', 'hold'])
async def test_real_presubmit_retains_independent_invariants(monkeypatch, failure):
    row, raw, decide, provenance = root_world(monkeypatch)
    facts = approve_fields(row, raw.current, raw.fixed, provenance)
    actions = action_sequence()
    def decision(*args, **kwargs):
        action = actions.pop(0)
        if action['action'] == 'submit':
            if failure == 'missing-attachment':
                for snapshot in (raw.current, raw.fixed):
                    for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                        if c.get('type') == 'file':
                            c['files'] = []
            elif failure == 'wrong-job':
                row['job_id'] = 'different-job'
            elif failure in {'stale-seed', 'duplicate'}:
                def refuse(*a, **kw):
                    raise forms.Gate('Synthetic ' + failure)
                monkeypatch.setattr(forms, 'check_dispatch', refuse)
        return action
    decide.side_effect = decision
    monkeypatch.setattr(forms, 'hold_possible_submission', lambda *a, **kw: None)
    def final_check(*a, **kw):
        if failure == 'hold':
            raise forms.Gate('Synthetic independent hold refusal')
    monkeypatch.setattr(forms, 'check_before_committing_click', final_check)
    result = await forms.apply(posting('ashby'), COMPANY, facts, 'synthetic',
                               pk=PK, resume_path='Resume.pdf')
    assert result['status'] == 'gate', result
    assert not any(c[0] == 'click' for c in raw.calls)


async def test_submit_producer_cannot_be_bypassed_or_retried(monkeypatch, tmp_path):
    row, raw, _, provenance = root_world(monkeypatch)
    transport, tracking, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    await transport.__aenter__()
    session = forms._EntrySession(transport, lambda u: u == entry('ashby'), provenance)
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=asyncio.get_running_loop().time() + 10)
    with pytest.raises(forms.bsk.Unavailable, match='Unmediated application IPC is forbidden'):
        await session.call('click', '#structural-submit')
    no_mutations(raw)
    # #139 binding 1/AC9: copied facade state is not Submit authority.
    session.submit_authorized = forms._page_identity(await session.page())
    with pytest.raises(forms.bsk.Unavailable, match='Unmediated application IPC is forbidden'):
        await session.call('click', '#structural-submit')
    no_mutations(raw)
    facts = approve_fields(row, page, raw.fixed, provenance)
    filled, history = {}, []
    controller = forms.ApplicationIPCController(
        PK, COMPANY, posting('ashby'), pdf, facts, filled, history,
        {'url': entry('ashby')}, lambda: None, seal_stage=True)
    transport.bind_application_controller(controller)
    session.resume_path = controller.resume_path
    try:
        # The real raw producer needs its one staged PDF before final Submit.
        # Ungated field fills cannot substitute for a verified attachment.
        upload = await forms._ashby_resume_upload(
            session, await session.page(), resume_path=controller.resume_path)
        history.append(upload)
        assert controller.upload_completed
        for action in action_sequence()[:-1]:
            receipt = await forms.execute(
                session, await session.page(), action, facts=facts, filled=filled,
                resume_path=controller.resume_path, company=COMPANY, jd_text='',
                resume_tex='', github='', pk=PK, job_url=posting('ashby'), provenance=provenance)
            history.append(receipt)
        await session.call('click', '#structural-submit')
        assert controller.committed
        with pytest.raises(forms.Gate, match='cannot be repeated'):
            await session.call('click', '#structural-submit')
        assert [c for c in raw.calls if c[0] == 'click'] == [('click', '#structural-submit')]
    finally:
        controller.release_snapshot()


@pytest.mark.parametrize('tag,typ', [('input', 'submit'), ('input', 'image'), ('button', 'submit')])
@pytest.mark.parametrize('owner', ['', '#other', '#native'])
def test_native_submit_needs_actual_independent_form_owner(tag, typ, owner):
    target = dict(tag=tag, type=typ, submit=True, in_form=True, form_selector='#native',
                  native_form_owner_selector=owner)
    assert forms.native_submit(target) is (owner == '#native')


@pytest.mark.parametrize('spec', [
    {'wrapperTag': 'section'}, {'wrapperClass': 'generic'},
    {'paneTag': 'section'}, {'movePane': True},
    {'moveSubmit': True}, {'duplicateSubmit': True},
    {'duplicatePane': True}, {'duplicateId': True},
    {'noResume': True}, {'invisible': '_systemfield_name'},
    {'submitAttrs': {'type': 'button'}},
    {'submitAttrs': {'role': 'button'}},
])
def test_changed_ashby_roots_never_prove_an_unaudited_final_submit(monkeypatch, spec):
    _, raw, _, provenance = root_world(monkeypatch, **spec)
    root = raw.current['entry'].get('formless') or {}
    if root.get('error') or not root.get('root'):
        with pytest.raises(forms.Gate, match='final Ashby application container'):
            forms._formless_boundary(raw.current, provenance)
    else:
        # Changing unrelated controls does not make another raw Submit safe.
        expected = root.get('submit')
        with pytest.raises(forms.Gate, match='unique final application Submit|final application container lost a receiver'):
            forms._formless_boundary(
                raw.current, provenance, submit_selector='#untracked-submit')

@pytest.mark.parametrize('problem', [
    'truncated', 'empty-evidence', 'cross-channel', 'no-resume',
    'root-error', 'root-missing', 'wrong-provenance',
])
def test_final_formless_boundary_does_not_borrow_unproven_roots(monkeypatch, problem):
    _, raw, _, provenance = root_world(monkeypatch)
    page = deepcopy(raw.current)
    root = page['entry']['formless']
    if problem == 'truncated':
        root['truncated'] = True
    elif problem == 'empty-evidence':
        page['entry']['root_evidence'] = []
    elif problem == 'cross-channel':
        page['entry']['root_evidence'].append(
            {'selector': '#synthetic-extra', 'type': 'text'})
    elif problem == 'no-resume':
        # The final PDF check, rather than the root witness's per-input
        # admission, rejects a missing actual staged file receiver.
        page['entry']['root_evidence'] = [
            c for c in page['entry']['root_evidence'] if c.get('type') != 'file']
        page['controls'] = [
            c for c in page['controls'] if c.get('type') != 'file']
        with pytest.raises(forms.Gate, match='résumé input'):
            forms.check_form(page, {}, True, 'Synthetic.pdf')
        return
    elif problem == 'root-error':
        root['error'] = 'ambiguous application roots'
    elif problem == 'root-missing':
        root['root'] = ''
    elif problem == 'wrong-provenance':
        provenance = None
    with pytest.raises(forms.Gate):
        forms._formless_boundary(page, provenance)

async def test_missing_formless_fact_is_a_normal_owner_question_not_a_grant(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch)
    session = forms._EntrySession(
        raw, lambda url: url == entry('ashby'), provenance)
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            session, raw.current, action_sequence()[0],
            facts={}, filled={}, resume_path='Resume.pdf', company=COMPANY,
            jd_text='', resume_tex='', github='', pk=PK,
            job_url=posting('ashby'), provenance=provenance)
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question is not None
    no_mutations(raw)

async def test_independent_entry_witness_disagreement_cannot_authorize(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch)
    raw.fixed['entry']['formless']['container'] = '#different'
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance)
    with pytest.raises(forms.Gate, match='predicate=entry.equal'):
        await forms._observe_controls(session, raw.current)
    no_mutations(raw)


async def test_formless_captcha_does_not_borrow_native_passive_waiver(monkeypatch):
    _, raw, decide, _ = root_world(monkeypatch, frames=['H'])
    result = await attempt()
    assert result['status'] == 'gate'
    assert 'unknown/untrusted application frame' in result['question']
    assert not decide.await_count
    no_mutations(raw)


@pytest.mark.parametrize('spec,admitted', [
    ({'wrapperTag': 'section'}, False), ({'wrapperClass': 'generic'}, False),
    ({'paneTag': 'section'}, False), ({'movePane': True}, False),
    ({'moveSubmit': True}, False), ({'duplicate': True}, False),
    ({'duplicateSubmit': True}, False), ({'duplicatePane': True}, False),
    # These preserve the semantic owner and complete actionable census.
    ({'extraChild': True}, True), ({'wrongOrder': True}, True),
])
def test_dom_root_predicate_is_local_to_each_reader(spec, admitted):
    for page in observed(**spec):
        root = page['entry']['formless']
        assert bool(root['root']) is admitted
        assert bool(root['error']) is not admitted

def test_native_form_has_priority_over_structural_markup():
    spec = dict(formless=True)
    for page in (dom(spec), dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)):
        assert page['entry']['root']['selector'] == '#application-form'
        assert page['entry']['formless'] is None
        assert page['entry']['root_evidence'] == []


def test_empty_native_owner_pair_does_not_prove_actual_ownership():
    assert not forms.native_submit(dict(tag='input', type='submit', submit=True,
                                        native_form_owner_selector='', form_selector=''))


async def test_native_owner_mismatch_preserves_named_witness_diagnostic(monkeypatch):
    # Test the equality-producing seam without an entry wrapper intercepting it.
    page, fixed = observed()
    fixed['controls'][0]['native_form_owner_selector'] = '#foreign'
    raw = RootSession(page, fixed)
    original = raw.call
    async def call(method, *args, **kw):
        if args == (forms._FIXED_CONTROL_WITNESS,):
            return {'value': fixed}
        return await original(method, *args, **kw)
    raw.call = call
    raw.page = lambda: _snapshot(page)
    with pytest.raises(forms.Gate, match='predicate=native_form_owner_selector.equal'):
        await forms._observe_controls(raw, page)


async def _snapshot(page):
    return deepcopy(page)


async def test_description_link_is_not_a_form_answer_receiver(monkeypatch):
    _, raw, decide, _ = root_world(monkeypatch, descriptionLink=True)
    assert '#description-link' in {c['selector'] for c in raw.current['controls']}
    assert '#description-link' not in {c['selector'] for c in raw.current['entry']['root_evidence']}
    result = await attempt()
    assert result['status'] == 'gate' and result['form_question'] is None
    assert not decide.await_count
    no_mutations(raw)


def test_only_exact_structural_submit_matches_commit_target(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch)
    # The single final guard calls the existing structural root census; a
    # removed legacy helper is not an additional per-step submit authority.
    assert raw.current['entry']['formless']['submit'] == '#structural-submit'
    forms._formless_boundary(
        raw.current, provenance, submit_selector='#structural-submit')
    with pytest.raises(forms.Gate, match='unique final application Submit'):
        forms._formless_boundary(
            raw.current, provenance, submit_selector='#autofill-button')
    for selector in ('#dropzone', '#autofill-button', '#_systemfield_name'):
        drifted = deepcopy(raw.current)
        drifted['entry']['formless']['submit'] = selector
        with pytest.raises(forms.Gate, match='unique final application Submit'):
            forms._formless_boundary(drifted, provenance)
    no_mutations(raw)

def test_structural_receiver_membership_is_guarded_at_destination_seam(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch)
    with pytest.raises(forms.Gate, match='unsupported structural receiver forbidden'):
        forms.check_form_destination(raw.current, {'selector': '#autofill-file'}, provenance=provenance)


def test_structural_presubmit_requires_only_the_staged_attachment(monkeypatch):
    _, raw, _, _ = root_world(monkeypatch)
    resume = next(c for c in raw.current['controls'] if c['type'] == 'file')
    resume['files'] = ['Resume.pdf', 'Unexpected.pdf']
    with pytest.raises(forms.Gate, match='Exactly one current résumé input'):
        forms.check_form(raw.current, {}, True, 'Resume.pdf')


@pytest.mark.parametrize('drift', ['required', 'optional', 'removed-resume', 'replaced-resume', 'extra-resume', 'root'])
async def test_final_submit_producer_rejects_state_drift_after_hold(monkeypatch, tmp_path, drift):
    row, raw, decide, provenance = root_world(monkeypatch)
    transport, tracking, pdf = install_controller_session(monkeypatch, row, raw, tmp_path)
    facts = approve_fields(row, raw.current, raw.fixed, provenance)
    decide.side_effect = action_sequence()
    real_check = forms.check_before_committing_click
    def rerender(*a, **kw):
        real_check(*a, **kw)
        for snapshot in (raw.current, raw.fixed):
            if drift == 'root':
                snapshot['entry']['formless']['pane'] = '#changed-pane'
            for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                if drift in {'required', 'optional'} and c['selector'] == (
                        '#_systemfield_name' if drift == 'required' else '#additional'):
                    c.update(value='Unapproved replacement', has_value=True)
                if c.get('type') == 'file' and drift.endswith('resume'):
                    c['files'] = {'removed-resume': [], 'replaced-resume': ['Other.pdf'],
                                  'extra-resume': ['Resume.pdf', 'Other.pdf']}[drift]
    monkeypatch.setattr(forms, 'check_before_committing_click', rerender)
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr(forms.asyncio, 'sleep', fast_sleep)
    result = await forms.apply(posting('ashby'), COMPANY, facts, 'synthetic',
                               pk=PK, resume_path=pdf)
    assert not any(c[0] == 'click' for c in raw.calls), result
    assert result['status'] == 'gate', result
    # #139 AC8: the same raw final guard checks post-hold form/root drift.
    assert 'changed after the own hold' in result['question']
    # This is a post-admission drift, not a zero-total-IPC admission refusal.
    assert [c for c in raw.calls if c[0] == 'fill'], result
    assert len([c for c in effect_calls(raw.calls) if c[0] == 'set']) == 1
    assert not [c for c in raw.calls if c[0] == 'click']


@pytest.mark.parametrize('style', [{'clip': 'rect(0px, 0px, 0px, 0px)'},
                                   {'clipPath': 'inset(100%)'}])
@pytest.mark.parametrize('ancestor', [False, True])
@pytest.mark.parametrize('target', ['_systemfield_name', '_systemfield_email', 'additional'])
async def test_css_clipped_text_is_not_an_applicant_target(monkeypatch, style, ancestor, target):
    _, raw, decide, _ = root_world(monkeypatch, clipStyle=style, clipAncestor=ancestor, clipTarget=target)
    for snapshot in (raw.current, raw.fixed):
        c = next(c for c in snapshot['entry']['root_evidence'] if c['id'] == target)
        assert not c['visible']
    result = await attempt()
    # Clipping remains independently observable. Without a staged PDF,
    # no Submit can be attempted; this is not a per-control permission gate.
    assert result['status'] == 'gate', result
    assert 'row-tailored staged PDF is missing' in result['question'], result
    assert not decide.await_count
    no_mutations(raw)


def test_clipped_labelled_resume_remains_supported(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch, clipStyle={'clip': 'rect(0px, 0px, 0px, 0px)'},
                                      clipTarget='_systemfield_resume')
    for snapshot in (raw.current, raw.fixed):
        members = forms._formless_boundary(snapshot, provenance)
        assert next(c for c in members if c['type'] == 'file')['selector'] == '#_systemfield_resume'


async def test_ac_z_and_hidden_recaptcha_pair_reach_actual_human_gate(monkeypatch):
    _, raw, decide, _ = root_world(monkeypatch, frames=['R', 'B', 'Z'])
    assert [f['status'] for f in raw.current['entry']['frames']] == ['R', 'R', 'Z']
    assert raw.current['entry'] == raw.fixed['entry']
    result = await attempt()
    assert result['status'] == 'gate' and result['form_question'] is None, result
    assert not decide.await_count
    no_mutations(raw)


@pytest.mark.parametrize('changes', [
    {'edit': {'src': 'about:blank'}}, {'edit': {'src': 'https://foreign.test/frame'}},
    {'edit': {'title': ''}}, {'edit': {'data-extra': ''}}, {'edit': {'aria-hidden': 'true'}},
    {'edit': {'style': {'display': 'block'}}},
    {'edit': {'rect': {'width': 1, 'height': 1}}}, {'zInside': True},
    {'zParentTag': 'section'}, {'zParentAttrs': {'id': 'foreign'}},
    {'zParentAttrs': {'class': 'foreign'}}, {'zNested': True},
])
def test_each_ac_z_signature_condition_is_required(monkeypatch, changes):
    _, raw, _, provenance = root_world(monkeypatch, frames=['Z'], **changes)
    # Issue #214 category (b): a source-less frame still display:none is
    # frame-only admitted on Ashby although it is not the AC Z signature.
    edit = changes.get('edit', {})
    nonrendered = 'src' not in edit and 'style' not in edit
    for snapshot in (raw.current, raw.fixed):
        assert snapshot['entry']['frames'][0]['status'] == 'unknown'
        if nonrendered:
            frame = snapshot['entry']['frames'][0]
            assert (frame['source'], frame['srcdoc'], frame['display'], frame['form_controls']) == (
                'absent', 'absent', 'none', 'unknown')
            forms._entry_safety(snapshot, lambda url: True, provenance)
            continue
        with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
            forms._entry_safety(snapshot, lambda url: True, provenance)


def test_ac_z_cannot_waive_an_unproven_entry(monkeypatch):
    _, raw, _, _ = root_world(monkeypatch, frames=['Z'])
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(raw.current, lambda url: True, None)


async def test_ac_z_becoming_active_is_rejected_on_later_inventory(monkeypatch):
    row, raw, _, provenance = root_world(monkeypatch, frames=['Z'])
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance)
    await forms._stable_application_form(
        session, session.allowed, provenance, deadline=asyncio.get_running_loop().time() + 10)
    raw.current, raw.fixed = observed(frames=['Z'], edit={'src': 'https://foreign.test/frame'})
    # The reversible page read is ungated; the existing frame safety
    # decision still rejects the now-active challenge on inspection.
    fresh = await session.page()
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._entry_safety(fresh, session.allowed, provenance)
    no_mutations(raw)


async def test_missing_autofill_pane_uses_the_existing_entry_wait(monkeypatch):
    _, raw, _, provenance = root_world(monkeypatch, missingPane=True)
    for snapshot in (raw.current, raw.fixed):
        forms._entry_safety(snapshot, lambda url: True, provenance)
        assert forms._entry_not_ready(snapshot) == 'no unique application root'
    complete = observed()
    original = raw.call
    reads = 0
    async def mounts(method, *args, **kwargs):
        nonlocal reads
        if method == 'evaluate' and args == (forms.ENTRY_PAGE,):
            reads += 1
            if reads == 3:
                raw.current, raw.fixed = deepcopy(complete)
        return await original(method, *args, **kwargs)
    raw.call = mounts
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance)
    page = await forms._stable_application_form(
        session, session.allowed, provenance, deadline=asyncio.get_running_loop().time() + 10)
    assert page['entry']['formless']['root'] == '#application-root'
    assert reads >= 4
    no_mutations(raw)


@pytest.mark.parametrize('fact', ['src', 'title', 'data', 'aria', 'display', 'rects',
                                  'outside', 'parent-tag', 'parent-id', 'parent-class', 'grandparent'])
def test_ac_z_predicate_rejects_each_failed_fact(fact):
    # Exercise each clause of the production producer directly. Physical DOM
    # constraints can make clauses redundant; those must not hide a missing check.
    import json
    import subprocess
    from tools.browser_skill_dom import _ENTRY_OBSERVATION
    predicate = 'const z=' + _ENTRY_OBSERVATION.split('const z=', 1)[1].split(';', 1)[0] + ';'
    script = r'''
const fact=process.argv[1], document={body:{}};
const raw=fact==='src' ? '' : null;
const outside=fact!=='outside', style={display:fact==='display' ? 'block':'none'};
const parent={tagName:fact==='parent-tag' ? 'SECTION':'DIV',
  id:fact==='parent-id' ? 'foreign':'',
  getAttribute:()=>fact==='parent-class' ? 'foreign':null,
  parentElement:fact==='grandparent' ? {}:document.body};
const e={getAttribute:()=>fact==='title' ? '':null,
  getAttributeNames:()=>fact==='data' ? ['data-extra']:fact==='aria' ? ['aria-hidden']:[],
  getClientRects:()=>fact==='rects' ? [{}]:[]};
eval(process.argv[2]+'console.log(JSON.stringify(z));');
'''
    result = subprocess.run(['node', '-e', script, fact, predicate],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) is False


@pytest.mark.parametrize('changes', [
    {'wrapperTag': 'section'}, {'wrapperClass': 'foreign'}, {'wrongOrder': True},
    {'extraChild': True}, {'moveSubmit': True},
])
def test_missing_pane_never_authorizes_unsafe_sibling_structure(monkeypatch, changes):
    _, raw, _, provenance = root_world(monkeypatch, missingPane=True, **changes)
    # A missing final container fails closed before the more specific sibling
    # mismatch can be evaluated; both conditions deny raw Submit.
    with pytest.raises(forms.Gate, match='final Ashby application container'):
        forms._formless_boundary(raw.current, provenance)

@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS])
def test_complete_indistinguishable_formless_candidates_are_not_a_bare_empty_duplicate(reader):
    spec = dict(root=False, formless=True, url=entry('ashby'), secondFormlessComplete=True)
    result = dom(spec, reader)
    # Two separate populated label/receiver/file/submit graphs, not an extra
    # empty wrapper. There is no independently observable per-root job ID.
    assert result['entry']['formless']['error'] == 'ambiguous application roots'
    # An ambiguous pair offers no privileged final Submit receiver.
    assert result['entry']['root_evidence'] == []


async def test_complete_two_formless_roots_refuse_final_submit(monkeypatch, tmp_path):
    _, raw, decide, provenance = root_world(monkeypatch, secondFormlessComplete=True)
    with pytest.raises(forms.Gate, match='final Ashby application container'):
        forms._formless_boundary(raw.current, provenance)
    assert not decide.await_count
    no_mutations(raw)


@pytest.mark.parametrize('changes', [
    {'wrongOrder': True},
    {'extraChild': True},
    {'inertSibling': True, 'inertBeforeContainer': True},
    {'idless': True, 'leadingInert': True, 'wrongOrder': True,
     'inertSibling': True, 'inertBeforeContainer': True},
])
async def test_benign_ashby_restructuring_retains_independent_readback(
        monkeypatch, changes):
    _, raw, decide, _ = root_world(monkeypatch, **changes)
    assert raw.current['entry'] == raw.fixed['entry']
    result = await attempt()
    assert result['status'] == 'gate' and result['form_question'] is None, result
    assert not decide.await_count
    no_mutations(raw)

@pytest.mark.parametrize('kind', ['onclick', 'summary'])
async def test_nested_interactive_sibling_gates_before_model_and_ipc(monkeypatch, tmp_path, kind):
    # The sibling violation is permanent in this synthetic page.
    monkeypatch.setattr(forms, '_ASHBY_WAIT_SECONDS', 0.02)
    _, raw, decide, provenance = root_world(
        monkeypatch, inertSibling=True, inertDescendant=kind)
    with pytest.raises(forms.Gate, match='final Ashby application container'):
        forms._formless_boundary(raw.current, provenance)
    result = await attempt()
    assert result['status'] == 'gate', result
    no_mutations(raw)



_UNSAFE = 'application root sibling identity mismatch: unsafe sibling'
_LIVE_SIBLING = dict(inertSibling=True, inertBeforeContainer=True, highlight=True,
                     siblingClass='synthetic-decoration',
                     siblingAttrs={'data-highlight': 'synthetic'})


@pytest.mark.parametrize('changes,error', [
    ({}, 'application root sibling identity mismatch'),
    ({'inertDescendant': 'content'}, 'application root sibling identity mismatch'),
    ({'wrapperAttrs': {'tabindex': '-1', 'role': 'region', 'aria-labelledby': 'synthetic'},
      'inertDescendant': 'content'}, 'application root sibling identity mismatch'),
    ({'siblingAttrs': {'data-highlight': 'synthetic', 'onclick': 'syntheticAction()'}}, _UNSAFE),
    ({'siblingAttrs': {'data-highlight': 'synthetic', 'role': 'note'}}, _UNSAFE),
    ({'siblingAttrs': {'data-highlight': 'synthetic', 'tabindex': '-1'}}, _UNSAFE),
    ({'siblingAttrs': {'data-highlight': 'synthetic', 'contenteditable': 'true'}}, _UNSAFE),
    ({'siblingAttrs': {'data-highlight': 'synthetic', 'href': '#synthetic'}}, _UNSAFE),
    ({'inertDescendant': 'button'}, _UNSAFE),
    ({'inertDescendant': 'onclick'}, _UNSAFE),
    ({'inertDescendant': 'summary'}, _UNSAFE),
])
def test_styled_inert_sibling_keeps_current_reader_scope_without_grants(
        changes, error):
    # Styling does not certify a root, nor create a per-widget admission gate.
    page, fixed = observed(**{**_LIVE_SIBLING, **changes})
    for current in (page, fixed):
        assert current['entry']['formless']['error'] == error
        assert current['entry']['root'] is None
        if error != _UNSAFE:
            for key in ('container', 'submit', 'pane'):
                assert current['entry']['formless'][key]
    assert page['entry'] == fixed['entry']

async def test_real_idless_selectors_rekey_at_final_submit_not_on_field_writes(
        monkeypatch):
    row, raw, decide, provenance = root_world(monkeypatch, idless=True, domOrder=True)
    session = forms._EntrySession(
        raw, lambda u: u == entry('ashby'), provenance, resume_path='Resume.pdf')
    old = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    assert ':nth-of-type(' in old['entry']['formless']['root']
    old_submit = old['entry']['formless']['submit']
    newer = observed(idless=True, domOrder=True, leadingInert=True,
                     wrongOrder=True, inertSibling=True,
                     inertBeforeContainer=True)
    raw.current, raw.fixed = deepcopy(newer)
    fresh = await session.page()
    assert old['entry']['formless']['root'] != fresh['entry']['formless']['root']
    assert old_submit != fresh['entry']['formless']['submit']
    assert forms._root_identity(old) == forms._root_identity(fresh)
    with pytest.raises(forms.Gate, match='unique final application Submit'):
        forms._formless_boundary(
            fresh, provenance, submit_selector=old_submit)
    assert not decide.await_count
    no_mutations(raw)

async def test_one_successful_formless_fill_then_submit_shape_drift_allows_preparation(monkeypatch):
    """The changed Submit witness is for the final guard; fresh fields remain writable."""
    row, raw, session, page, provenance = await admitted(monkeypatch)
    facts = approve_fields(row, page, raw.fixed, provenance)
    filled = {}
    await forms.execute(
        session, page, action_sequence()[0], facts=facts, filled=filled,
        resume_path='Resume.pdf', company=COMPANY, jd_text='', resume_tex='',
        github='', pk=PK, job_url=posting('ashby'), provenance=provenance)
    assert filled == {'#_systemfield_name': 'Test User'}
    for snapshot in (raw.current, raw.fixed):
        snapshot['entry']['formless']['submit'] = '#foreign-submit'
    await session.call('fill', '#_systemfield_email', '--value', facts['Email'])
    assert [c[:2] for c in raw.calls if c[0] == 'fill'] == [
        ('fill', '#_systemfield_name'), ('fill', '#_systemfield_email')]
    with pytest.raises(forms.Gate, match='unique final application Submit'):
        forms._formless_boundary(raw.current, provenance)
    assert not [c for c in raw.calls if c[0] == 'click']


@pytest.mark.asyncio
async def test_forless_witnessed_aria_picker_and_native_choices_reach_real_entry_ipc(monkeypatch):
    """Round-2: actual stable form, recipient, witness and raw entry path.

    Hydrated controls augment both independently observed real Ashby readers.
    No mocked stable-form admission or grant bypass can authorize a Submit.
    """
    _, raw, _, provenance = root_world(monkeypatch)
    variants = [
        ('#work-picker', 'combobox', 'div', 'Work location', 'work_location', ''),
        ('#radio-no', 'radio', 'input', 'No', 'relocation', 'no'),
        ('#radio-yes', 'radio', 'input', 'Yes', 'relocation', 'yes'),
        ('#consent-choice', 'checkbox', 'input', 'Contact permission', 'contact', 'yes'),
    ]
    for snapshot in (raw.current, raw.fixed):
        sample = next(c for c in snapshot['controls'] if c['selector'] == '#_systemfield_name')
        root_sample = next(c for c in snapshot['entry']['root_evidence']
                           if c['id'] == '_systemfield_name')
        for selector, kind, tag, caption, name, value in variants:
            control = deepcopy(sample)
            control.update(selector=selector, type=kind, native_type=kind if tag == 'input' else '',
                           tag=tag, label=caption, question='Relocation' if kind == 'radio' else caption,
                           name=name, value=value, required=True, disabled=False,
                           checked=False, submit=False, in_form=False, form_selector='',
                           native_form_owner_selector='', form_action='', files=[])
            if kind in {'radio', 'checkbox'}:
                control['visible_text'] = caption
            if kind == 'combobox':
                control['combobox'] = {
                    'linked': True, 'link_id': 'work-options',
                    'listbox_selector': '#work-options',
                    'options': [{'selector': '#remote', 'unique': True, 'tag': 'div',
                                 'native_type': '', 'submit': False, 'navigates': False,
                                 'commits_application': False, 'safe': True,
                                 'label': 'Remote', 'value': 'remote', 'selected': False}],
                    'selected_text': '', 'displayed_text': '', 'selected_value': '',
                    'hidden_selector': '', 'hidden_value': '',
                }
            root_control = deepcopy(root_sample)
            root_control.update(selector=selector, id=selector[1:], type=kind,
                                native_type=kind if tag == 'input' else '', tag=tag,
                                role='combobox' if kind == 'combobox' else '',
                                label=caption, value=value, files=[], required=True,
                                disabled=False, readonly=False, visible=True,
                                associated=True, inside=True, in_container=True,
                                unique=True, native_owner='', declared_owner='',
                                formaction=None, dropzone=False, submit=False)
            snapshot['controls'].append(control)
            snapshot['entry']['root_evidence'].append(root_control)
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'),
                                  provenance, resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    selectors = {c['selector'] for c in forms._formless_boundary(page, provenance)}
    assert selectors.issuperset({'#work-picker', '#radio-no', '#radio-yes', '#consent-choice'})
    radio = next(c for c in page['controls'] if c['selector'] == '#radio-no')
    peers = [c for c in page['controls'] if c.get('type') == 'radio'
             and c.get('question') == radio.get('question')
             and c.get('form_selector') == radio.get('form_selector')]
    assert len(peers) == 2
    for selector in ('#work-picker', '#radio-no', '#consent-choice'):
        await session.call('click', selector)
    assert [c for c in raw.calls if c[0] == 'click'] == [
        ('click', '#work-picker'), ('click', '#radio-no'),
        ('click', '#consent-choice')]
    assert not any(c[0] == 'submit' for c in raw.calls)


def _source_less_enterprise_fixture(script, *, variant='exact', child=None):
    """The extra iframe has no src/srcdoc, but no root/document proof is invented.

    This injects only an offline DOM mutation into the two production readers;
    the underlying local Node fixture, browser engine and guards are unchanged.
    """
    spec = dict(root=False, formless=True, missingPane=variant != 'with-root',
                url=entry('ashby'), sibling=True, **_hidden_enterprise_anchor())
    if variant == 'separate-containers':
        spec['badge'] = True
    opener = '(() => {\n'
    assert script.startswith(opener)
    injection = """
  {
    const frames = document.querySelectorAll('iframe');
    const anchor = frames[0], companion = frames[1];
    anchor.setAttribute('src',
      'https://www.recaptcha.net/recaptcha/enterprise/anchor?private=FRAME_QUERY_SENTINEL');
    companion.removeAttribute('src');
    companion.style.display = 'none';
    companion.setAttribute('data-private', 'FRAME_ATTRIBUTE_SENTINEL');
    companion.setAttribute('id', 'FRAME_ID_SENTINEL');
    companion.parentElement.setAttribute('data-private', 'PARENT_ATTRIBUTE_SENTINEL');
  }
"""
    additional = {
        'sourced': """
  document.querySelectorAll('iframe')[1].setAttribute('src',
    'https://widgets.example.test/frame/trace?secret=FRAME_QUERY_SENTINEL');
""",
        'srcdoc': """
  document.querySelectorAll('iframe')[1].setAttribute('srcdoc',
    '<input value="SRCDOC_TOKEN_SENTINEL">');
""",
        'visible': """
  document.querySelectorAll('iframe')[1].style.display = 'block';
  document.querySelectorAll('iframe')[1].style.visibility = 'visible';
""",
    }.get(variant, '')
    reader=script.replace(opener, opener + injection + additional, 1)
    if child is None:
        return dom(spec, reader)
    # Borrow the existing offline current-child fixture, not a live browser.
    from tests.tools.test_browser_application_entry import _dom_with_readable_child
    spec['child'] = {'url': 'about:blank', 'readyState': 'complete', 'control': child}
    return _dom_with_readable_child(spec, reader)


@pytest.mark.parametrize('reader', ['page', 'witness'])
def test_source_less_companion_unproved_root_retains_gate_with_bounded_detail(reader):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = _source_less_enterprise_fixture(script)
    assert page['entry']['formless']['root'] == ''
    assert [f['status'] for f in page['entry']['frames']] == ['unknown', 'unknown']
    anchor, companion = page['entry']['frames']
    assert anchor['rootless_hidden_r'] is True
    assert companion.get('rootless_hidden_r') is not True
    assert companion['source'] == 'absent' and companion['srcdoc'] == 'absent'
    assert companion['visibility'] == 'hidden' and companion['display'] == 'none'
    assert companion['root'] == '' and companion['outside'] is False
    assert companion['form_controls'] == 'unknown'
    assert companion['document_readability'] == 'unreadable'
    assert companion['parent_pattern'] == 'body:nth-of-type(1)'
    assert companion['anchor_sibling'] == 'yes'
    assert companion['shared_recaptcha_container'] == 'unknown'
    with pytest.raises(forms.CaptchaArtifactGate, match='unknown/untrusted application frame') as raised:
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)
    detail = str(raised.value)
    assert 'frame[2]' in detail
    assert 'source_kind=absent' in detail and 'outside_root=unknown' in detail
    assert 'srcdoc=absent parent_pattern=body:nth-of-type(1)' in detail
    assert 'anchor_sibling=yes shared_recaptcha_container=unknown' in detail
    assert 'document_readability=unreadable' in detail
    assert 'Step2 unproven: independent application-root placement' in detail
    assert 'shared reCAPTCHA container' in detail
    for forbidden in ('FRAME_QUERY_SENTINEL', 'FRAME_ATTRIBUTE_SENTINEL',
                      'FRAME_ID_SENTINEL', 'PARENT_ATTRIBUTE_SENTINEL'):
        assert forbidden not in detail


async def test_source_less_companion_does_not_inherit_rootless_r_readiness(monkeypatch):
    first = tuple(_source_less_enterprise_fixture(script) for script in
                  (forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS))
    assert first[0]['entry'] == first[1]['entry']
    clock, raw, session, provenance, decision, reads = _temporal_entry_session(
        monkeypatch, first)
    start = clock[0]
    # Issue #214: the hidden anchor is category (a) and the source-less
    # display:none companion is category (b). Neither defers on readiness;
    # with both frames admitted only the unproven root gates.
    with pytest.raises(forms.Gate, match='no unique application root after bounded wait') as raised:
        await forms._stable_application_form(
            session, session.allowed, provenance, deadline=start + forms._ASHBY_WAIT_SECONDS)
    assert not isinstance(raised.value, forms.CaptchaArtifactGate)
    assert 'read.frames_admitted=true' in str(raised.value)
    assert session._readiness is False
    assert not decision.await_count
    no_mutations(raw)


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('variant', [
    'sourced', 'srcdoc', 'visible', 'separate-containers', 'with-root',
])
def test_source_less_companion_negative_evidence_never_admits(reader, variant):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = _source_less_enterprise_fixture(script, variant=variant)
    first, companion = page['entry']['frames']
    assert companion['status'] == 'unknown'
    if variant == 'sourced':
        assert companion['source'] == 'url'
    elif variant == 'srcdoc':
        assert companion['srcdoc'] == 'present'
    elif variant == 'visible':
        assert companion['display'] == 'block' and companion['visibility'] == 'visible'
    elif variant == 'separate-containers':
        assert companion['anchor_sibling'] == 'no'
        assert companion['shared_recaptcha_container'] == 'no'
    else:
        assert first['status'] == 'R'
        assert companion['outside'] is True and companion['anchor_sibling'] == 'yes'
    with pytest.raises(forms.CaptchaArtifactGate, match='unknown/untrusted application frame') as raised:
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)
    detail = str(raised.value)
    for forbidden in ('FRAME_QUERY_SENTINEL', 'FRAME_ATTRIBUTE_SENTINEL',
                      'FRAME_ID_SENTINEL', 'PARENT_ATTRIBUTE_SENTINEL',
                      'SRCDOC_TOKEN_SENTINEL'):
        assert forbidden not in detail


def test_frame_owner_detail_never_trusts_raw_parent_pattern_or_control_strings():
    # Malformed injected metadata is not a selector/logging escape hatch.
    page = _source_less_enterprise_fixture(forms.ENTRY_PAGE)
    frame = page['entry']['frames'][1]
    frame.update(parent_pattern='div#PRIVATE_CONTROL_SENTINEL',
                 form_controls='PRIVATE_TOKEN_SENTINEL',
                 document_readability='PRIVATE_DOC_SENTINEL',
                 anchor_sibling='PRIVATE_SIBLING_SENTINEL')
    with pytest.raises(forms.CaptchaArtifactGate) as raised:
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)
    detail = str(raised.value)
    assert 'parent_pattern=unknown' in detail
    assert 'form_controls=unknown' in detail
    assert 'document_readability=unknown' in detail
    assert 'anchor_sibling=unknown' in detail
    for token in ('PRIVATE_CONTROL_SENTINEL', 'PRIVATE_TOKEN_SENTINEL',
                  'PRIVATE_DOC_SENTINEL', 'PRIVATE_SIBLING_SENTINEL'):
        assert token not in detail


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('child,expected', [('button', 'yes'), ('none', 'no')])
def test_source_less_readable_child_controls_are_evidence_not_root_admission(reader,
                                                                             child, expected):
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    page = _source_less_enterprise_fixture(script, child=child)
    companion=page['entry']['frames'][1]
    assert companion['source'] == 'absent' and companion['srcdoc'] == 'absent'
    assert companion['document_readability'] == 'readable'
    assert companion['form_controls'] == expected
    assert companion['root'] == '' and companion['outside'] is False
    with pytest.raises(forms.CaptchaArtifactGate, match='unknown/untrusted application frame') as raised:
        forms._entry_safety(page, lambda url: url == entry('ashby'), None)
    detail=str(raised.value)
    assert f'form_controls={expected}' in detail
    assert 'independent application-root placement' in detail
    assert 'shared reCAPTCHA container' in detail


def _issue164_reader(reader, *, position=2, resolved=False, variant='ok',
                     response=False):
    """Execute the real reader against synthetic DOM relationships, not labels."""
    from tests.tools.test_browser_application_entry import _dom_with_readable_child
    spec = dict(url=entry('ashby'), root=resolved, frames=['R'], sibling=True,
                badge=True, tokens=['g-recaptcha-response'] if response else [],
                tokenBadge=response, tokenOutside=True, textarea=True,
                child={'url':'about:blank', 'readyState':'complete', 'control':
                       'button' if variant=='child-control' else 'none'})
    script = forms.ENTRY_PAGE if reader=='page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    opener = '(() => {\n'
    assert script.startswith(opener)
    mutation = r"""
  {
    const move=(e,p)=>{
      if(e.parentElement) {
        const siblings=e.parentElement.children;
        siblings.splice(siblings.indexOf(e),1);
      }
      e.parentElement=p;p.children.push(e);
    };
    const body=document.body, anchor=document.querySelectorAll('iframe')[0],
      companion=document.querySelectorAll('iframe')[1];
    // Build the wrapper from actual DOM nodes; only its position changes.
    const wrapper=node('div',{},body);
    const badge=node('div',{class:'grecaptcha-badge'},wrapper);
    const inner=node('div',{class:'g-recaptcha'},badge);
    move(anchor,INNER ? inner : badge);
    move(companion,wrapper);
    companion.removeAttribute('src');
    companion.style.display='none';
    const responses=document.querySelectorAll('textarea');
    for(const e of responses) {
      if(e.getAttribute('name')?.startsWith('g-recaptcha-response'))
        move(e,badge);
    }
    // Insert inert div siblings before the wrapper (no authority from order).
    for(let i=1;i<POSITION;i++){
      const inert=node('div',{},body);
      move(inert,body);
      body.children.splice(body.children.indexOf(inert),1);
      body.children.splice(body.children.indexOf(wrapper),0,inert);
    }
    if(VARIANT==='src') companion.setAttribute('src','about:blank');
    if(VARIANT==='srcdoc') companion.setAttribute('srcdoc','');
    if(VARIANT==='visible') companion.style.display='block';
    if(VARIANT==='no-badge') move(anchor,wrapper);
    if(VARIANT==='outside-parent')move(companion,node('div',{},body));
    if(VARIANT==='wrong-origin')anchor.setAttribute('src',
      'https://example.test/recaptcha/enterprise/anchor');
    if(VARIANT==='wrong-path')anchor.setAttribute('src',
      'https://www.recaptcha.net/recaptcha/enterprise/other');
    if(VARIANT==='second-anchor') {
      const other=node('iframe',{src:'https://www.google.com/recaptcha/enterprise/anchor'},badge);
      other.style.visibility='hidden';
    }
    if(VARIANT==='explicit-form')responses[0].setAttribute('form','missing-owner');
    if(VARIANT==='ancestor-form') {
      const f=node('form',{},badge);move(responses[0],f);responses[0].form=f;
    }
    if(VARIANT==='form-owner') responses[0].form=document.querySelector('form') ||
      node('form',{},body);
    if(VARIANT==='invalid-suffix')
      responses[0].setAttribute('name','g-recaptcha-response-1234567');
    if(VARIANT==='duplicate') {
      const t=node('textarea',{name:'g-recaptcha-response',id:'g-recaptcha-response-101'},badge);
      Object.defineProperty(t,'value',{get:()=>{throw Error('TOKEN READ');}});
    }
    if(VARIANT==='linked')node('input',{role:'combobox',
      'data-value-name':'g-recaptcha-response'},badge);
    if(VARIANT==='visible-bframe') {
      const f=node('iframe',{src:'https://www.recaptcha.net/recaptcha/enterprise/bframe'},badge);
      f.style.visibility='visible';
    }
  }
"""
    mutation=(mutation.replace('POSITION',str(position))
              .replace('INNER','true' if variant=='nested-container' else 'false')
              .replace('VARIANT',repr(variant).replace("'",'"')))
    script=script.replace(opener,opener+mutation,1)
    return _dom_with_readable_child(spec,script)


@pytest.mark.parametrize('reader', ['page','witness'])
@pytest.mark.parametrize('position', [2,3])
@pytest.mark.parametrize('resolved', [False,True])
@pytest.mark.parametrize('variant', ['ok','nested-container'])
def test_issue164_nested_hidden_companion_actual_dom_proof(reader,position,resolved,variant):
    page=_issue164_reader(reader,position=position,resolved=resolved,variant=variant)
    frames=page['entry']['frames']
    assert frames[1]['status']=='R', frames
    assert frames[1]['source']=='absent' and frames[1]['srcdoc']=='absent'
    assert frames[1]['document_readability']=='readable'
    assert frames[1]['root']==('' if not resolved else frames[1]['root'])
    assert frames[1]['anchor_sibling']=='no'


@pytest.mark.parametrize('reader', ['page','witness'])
@pytest.mark.parametrize('variant', [
    'src','srcdoc','visible','child-control','no-badge','outside-parent',
    'wrong-origin','wrong-path','second-anchor',
])
def test_issue164_unproved_companion_refused_by_reader(reader,variant):
    page=_issue164_reader(reader,variant=variant)
    assert page['entry']['frames'][1]['status']!='R'


@pytest.mark.parametrize('reader', ['page','witness'])
@pytest.mark.parametrize('variant', [
    'ok','explicit-form','ancestor-form','form-owner','invalid-suffix',
    'duplicate','linked','wrong-origin','wrong-path','second-anchor','no-badge',
])
def test_issue164_badge_local_reserved_response_predicates(reader,variant):
    page=_issue164_reader(reader,response=True,variant=variant)
    response=page['entry']['responses'][0]
    if variant=='ok':
        assert response['diagnostic']['provider_frame']=='yes'
        assert not page['entry']['response_error']
        assert all(c.get('name')!='g-recaptcha-response' for c in page['controls'])
    else:
        assert page['entry']['response_error'], (variant,response)
        assert response['diagnostic']['issues']


@pytest.mark.parametrize('reader', ['page','witness'])
def test_issue164_visible_bframe_keeps_challenge_precedence(reader):
    page=_issue164_reader(reader,response=True,variant='visible-bframe')
    assert any(f['status']=='visible-recaptcha' for f in page['entry']['frames'])
    with pytest.raises(forms.CaptchaGate):
        forms._entry_safety(page,lambda u:u==entry('ashby'),None)


@pytest.mark.parametrize('reader', ['entry','greenhouse'])
def test_issue164_suffix_is_entry_only_not_greenhouse_applicant_census(reader):
    from tools import browser_skill_dom
    script=(forms.ENTRY_PAGE if reader=='entry' else browser_skill_dom.GREENHOUSE_PAGE)
    opener='(() => {\n'
    assert script.startswith(opener)
    script=script.replace(opener,opener+"""
  {
    const owned=document.querySelector('form');
    const field=node('input',{id:'ordinary-suffix',type:'text',
      name:'g-recaptcha-response-123'},owned);
    field.value='synthetic';
  }
""",1)
    page=dom(dict(url=entry('ashby'),root=True),script)
    control_names={c.get('name') for c in page['controls']}
    if reader=='greenhouse':
        assert 'g-recaptcha-response-123' in control_names
        assert page['greenhouse']['response_controls'] is False
    else:
        assert 'g-recaptcha-response-123' not in control_names
        assert page['entry']['response_error'] is True


@pytest.mark.parametrize('reader', ['page','witness'])
def test_issue164_invalid_reserved_suffix_fails_before_throwing_value_getter(reader):
    page=_issue164_reader(reader,response=True,variant='invalid-suffix')
    assert page['entry']['response_error'] is True
    assert 'invalid-reserved-name' in page['entry']['responses'][0]['diagnostic']['issues']
    assert not any(c.get('name','').startswith('g-recaptcha-response')
                   for c in page['controls'])
    with pytest.raises(forms.Gate):
        forms._entry_safety(page,lambda u:u==entry('ashby'),None)


def test_issue164_reserved_response_is_not_a_form_control(monkeypatch):
    _, raw, _, _ = root_world(monkeypatch)
    raw.current = _issue164_reader('page', response=True)
    raw.fixed = _issue164_reader('witness', response=True)
    receiver = raw.current['entry']['responses'][0]['selector']
    assert receiver not in {c['selector'] for c in raw.current['controls']}
    assert receiver not in {c['selector']
                            for c in raw.current['entry']['root_evidence']}
    no_mutations(raw)


def _issue174_reader(reader, *, shape=3, variant='ok', resolved=False, ats='ashby'):
    """Offline production-reader pair: badge and body-level bframe do not share a root."""
    script = forms.ENTRY_PAGE if reader == 'page' else forms._ENTRY_FIXED_CONTROL_WITNESS
    opener = '(() => {\n'
    assert script.startswith(opener)
    injection = r"""
  {
    const move=(e,p)=>{
      if(e.parentElement){
        const siblings=e.parentElement.children;
        siblings.splice(siblings.indexOf(e),1);
      }
      e.parentElement=p;p.children.push(e);
    };
    const body=document.body;
    const anchor=document.querySelectorAll('iframe')[0];
    const badgeOuter=node('div',{},body);
    // Parent #3/#4 (or #2/#3) are independent body-level containers.
    const bframeOuter=node('div',{},body);
    const at=(parent,count)=>{
      while(parent.children.filter(e=>e.tagName==='DIV').length<count)
        node('div',{},parent);
      return parent.children.filter(e=>e.tagName==='DIV')[count-1];
    };
    const badgeParent=node('div',{},at(body,BADGE_INDEX));
    const badge=node('div',{class:'grecaptcha-badge'},badgeParent);
    const badgeInner=node('div',{},badge);
    move(anchor,badgeInner);
    anchor.style.visibility='hidden';anchor.style.display='inline';
    const bframeParent=node('div',{},at(body,BFRAME_INDEX));
    const bframe=node('iframe',{
      src:'https://www.google.com/recaptcha/enterprise/bframe'
    },bframeParent);
    bframe.style.visibility='hidden';bframe.style.display='inline';
    if(VARIANT==='visible-bframe')bframe.style.visibility='visible';
    if(VARIANT==='second-anchor'){
      const duplicate=node('iframe',{
        src:'https://www.google.com/recaptcha/enterprise/anchor'
      },badge);
      duplicate.style.visibility='hidden';
    }
    if(VARIANT==='second-bframe' || VARIANT==='second-bframe-srcdoc' ||
       VARIANT==='duplicate-outside-badge'){
      const duplicate=node('iframe',{
        src:'https://www.recaptcha.net/recaptcha/enterprise/bframe'
      },bframeOuter);
      duplicate.style.visibility='hidden';
      if(VARIANT==='second-bframe-srcdoc')duplicate.setAttribute('srcdoc','');
    }
    if(VARIANT==='duplicate-outside-badge')move(anchor,bframeOuter);
    if(VARIANT==='wrong-path')bframe.setAttribute('src',
      'https://www.google.com/recaptcha/api2/bframe');
    if(VARIANT==='wrong-origin')bframe.setAttribute('src',
      'https://widgets.example.test/recaptcha/enterprise/bframe');
    if(VARIANT==='srcdoc')bframe.setAttribute('srcdoc','');
    if(VARIANT==='anchor-srcdoc')anchor.setAttribute('srcdoc','');
    if(VARIANT==='anchor-outside')move(anchor,bframeOuter);
    if(VARIANT==='anchor-wrong-origin')anchor.setAttribute('src',
      'https://widgets.example.test/recaptcha/enterprise/anchor');
    if(VARIANT==='anchor-wrong-path')anchor.setAttribute('src',
      'https://www.recaptcha.net/recaptcha/api2/anchor');
  }
"""
    injection=(injection.replace('BADGE_INDEX',str(shape))
                        .replace('BFRAME_INDEX',str(shape+1))
                        .replace('VARIANT',repr(variant).replace("'",'"')))
    script=script.replace(opener,opener+injection,1)
    # A resolved root uses the proven Ashby three-child structural receiver;
    # a generic native form would introduce an unrelated, unproved action URL.
    return dom(dict(url=entry(ats),root=False,formless=True,
                    missingPane=not resolved,frames=['R']),script)


@pytest.mark.parametrize('reader', ['page', 'witness'])
@pytest.mark.parametrize('ats,variant,expected', [
    ('ashby', 'ok', ('yes', 1, 1, 'yes', 'yes')),
    ('lever', 'ok', ('no', 1, 1, 'yes', 'no')),
    ('ashby', 'second-anchor', ('yes', 2, 1, 'no', 'no')),
    ('ashby', 'second-bframe', ('yes', 1, 2, 'yes', 'no')),
    ('ashby', 'anchor-outside', ('yes', 1, 1, 'no', 'no')),
    ('ashby', 'visible-bframe', ('yes', 1, 1, 'yes', 'no')),
])
def test_issue179_enterprise_pair_diagnostic_fields(reader, ats, variant, expected):
    # Both shipped readers must explain exactly which passive pair evidence is
    # missing, without changing status or publishing page-owned values.
    page = _issue174_reader(reader, ats=ats, variant=variant)
    frames = page['entry']['frames']
    anchor = next(f for f in frames if f['path'] == '/recaptcha/enterprise/anchor')
    fields = ('ashby_native', 'enterprise_anchors', 'enterprise_bframes',
              'anchor_in_badge', 'passive_enterprise')
    assert tuple(anchor[key] for key in fields) == expected
    assert {anchor['ashby_native'], anchor['anchor_in_badge'],
            anchor['passive_enterprise']} <= {'yes', 'no'}
    assert type(anchor['enterprise_anchors']) is int
    assert type(anchor['enterprise_bframes']) is int
    # A bframe (even a visible one) uses the same decision and counts.
    bframe = next(f for f in frames if f['path'] == '/recaptcha/enterprise/bframe')
    assert tuple(bframe[key] for key in fields) == expected
    for frame in (anchor, bframe):
        assert not any(token in str(frame[key]) for key in fields
                       for token in ('example-co', 'Test User', 'QUERY_SENTINEL'))
    if variant == 'ok' and ats == 'ashby':
        assert [f['status'] for f in frames] == ['R', 'R']
    elif variant == 'visible-bframe':
        assert bframe['status'] == 'visible-recaptcha'
    else:
        assert anchor['status'] == 'unknown'


@pytest.mark.parametrize('reader',['page','witness'])
@pytest.mark.parametrize('shape',[2,3])
@pytest.mark.parametrize('resolved',[False,True])
def test_issue174_hidden_pair_root_independent(reader,shape,resolved):
    page=_issue174_reader(reader,shape=shape,resolved=resolved)
    frames=page['entry']['frames']
    assert len(frames)==2
    assert [f['status'] for f in frames]==['R','R'],frames
    assert frames[0]['badge'] is True
    assert frames[1]['badge'] is False
    assert frames[1]['visibility']=='hidden'
    assert frames[0]['srcdoc']==frames[1]['srcdoc']=='absent'
    if not resolved:
        assert all(f['root']=='' and f['outside'] is False for f in frames)
    else:
        assert page['entry']['formless']['root']=='#application-root'
    forms._entry_safety(page,lambda u:u==entry('ashby'),None)


@pytest.mark.parametrize('reader',['page','witness'])
@pytest.mark.parametrize('variant',[
    'visible-bframe','second-anchor','second-bframe','wrong-path','wrong-origin',
    'srcdoc','anchor-srcdoc','anchor-outside','anchor-wrong-origin',
    'anchor-wrong-path','second-bframe-srcdoc',
])
def test_issue174_hidden_pair_negative_cases(reader,variant):
    page=_issue174_reader(reader,variant=variant)
    if variant=='visible-bframe':
        assert any(f['status']=='visible-recaptcha' for f in page['entry']['frames'])
        with pytest.raises(forms.CaptchaGate,match='visible reCAPTCHA'):
            forms._entry_safety(page,lambda u:u==entry('ashby'),None)
    else:
        assert any(f['status']=='unknown' for f in page['entry']['frames']),page['entry']['frames']
        with pytest.raises(forms.Gate,match='unknown/untrusted application frame'):
            forms._entry_safety(page,lambda u:u==entry('ashby'),None)


@pytest.mark.parametrize('reader',['page','witness'])
def test_issue174_duplicate_bframes_outside_badge_with_resolved_ashby_root(reader):
    page=_issue174_reader(reader,variant='duplicate-outside-badge',resolved=True)
    assert page['entry']['formless']['root']=='#application-root'
    assert [f['status'] for f in page['entry']['frames']]==[
        'unknown','unknown','unknown']
    with pytest.raises(forms.Gate,match='unknown/untrusted application frame'):
        forms._entry_safety(page,lambda u:u==entry('ashby'),None)


@pytest.mark.parametrize('reader',['page','witness'])
def test_issue174_second_bframe_with_srcdoc_invalidates_entire_pair(reader):
    page=_issue174_reader(reader,variant='second-bframe-srcdoc')
    assert len(page['entry']['frames'])==3
    assert [f['status'] for f in page['entry']['frames']]==[
        'unknown','unknown','unknown']
    with pytest.raises(forms.Gate,match='unknown/untrusted application frame'):
        forms._entry_safety(page,lambda u:u==entry('ashby'),None)


@pytest.mark.parametrize('reader',['page','witness'])
def test_issue174_non_ashby_hidden_pair_cannot_borrow_ashby_exception(reader):
    # Change only the page route. Both real readers observe unchanged provider
    # DOM proof, but the entry site identity is no longer Ashby.
    # The shared fixture builder supports a site override on the DOM itself.
    page=_issue174_reader(reader,ats='lever')
    assert [f['status'] for f in page['entry']['frames']]==['unknown','unknown']
    with pytest.raises(forms.Gate,match='unknown/untrusted application frame'):
        forms._entry_safety(page,lambda u:u==entry('lever'),None)

# Issue #182: form-free root members may reuse exact GLOBAL bank facts, but
# never company-only facts, unbound selectors or sensitive fields.
async def _issue182_formless_page(monkeypatch, *, label='Name', kind='text'):
    row, raw, _, provenance = root_world(monkeypatch)
    for snapshot in (raw.current, raw.fixed):
        for item in snapshot['controls'] + snapshot['entry']['root_evidence']:
            if item['selector'] == '#_systemfield_name':
                item['label'] = label
                item['tag'] = 'textarea' if kind == 'textarea' else 'input'
                item['type'] = kind
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    return row, raw, session, page, provenance


def _issue182_tracking(monkeypatch, row):
    writes = []

    class Tracking:
        def set_status(self, pk, status, **attrs):
            assert pk == PK and status == 'submitting'
            writes.append(deepcopy(attrs))
            row.update(attrs)

    monkeypatch.setattr(
        'core.stores.make_stores',
        lambda: type('Stores', (), {'tracking': Tracking()})())
    return writes


@pytest.fixture
def bootstrap_caplog(caplog):
    # Production logging is logger-local and never propagates to root caplog.
    forms._LOG.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        forms._LOG.removeHandler(caplog.handler)


def _issue184_reversible_session(monkeypatch):
    row, raw, _, provenance = root_world(monkeypatch, missingPane=True)
    assert forms._formless_root(raw.current)
    assert not forms._formless_root(raw.current).get('root')
    raw._application_controller = object()  # Synthetic producer prerequisite.

    original_call = raw.call

    async def call(method, *args, **kwargs):
        if method == 'evaluate' and args == (PAGE,):
            # The real Session.page() evaluates generic PAGE. It has no
            # entry.formless, even when ENTRY_PAGE sees a rootless Ashby form.
            raw.calls.append((method, *args))
            generic = deepcopy(raw.current)
            generic.pop('entry', None)
            return {'value': generic}
        return await original_call(method, *args, **kwargs)

    raw.call = call
    raw.page = ProductionBrowserSession.page.__get__(raw, RootSession)
    raw._reversible_session = forms._ReversibleEntryReader(raw)
    return row, raw, provenance


def _issue184_add_name_phone_controls(raw):
    for snapshot in (raw.current, raw.fixed):
        first = next(c for c in snapshot['controls']
                     if c['selector'] == '#_systemfield_name')
        first.update(label='First Name', question='')
        # Bank grants now require the independent application-recipient census.
        # These synthetic extra fields belong to the same witnessed container;
        # PAGE-only appended fields would be intentionally unauthorized.
        original = next(e for e in snapshot['entry']['root_evidence']
                        if e['selector'] == '#_systemfield_name')
        original.update(label='First Name')
        for selector, caption, kind in (
                ('#synthetic-last', 'Last Name', 'text'),
                ('#synthetic-phone', 'Phone', 'tel')):
            extra = deepcopy(first)
            extra.update(selector=selector, id=selector[1:],
                         name=selector[1:], label=caption, question='',
                         tag='input', type=kind, native_type=kind)
            snapshot['controls'].append(extra)
            evidence = deepcopy(original)
            evidence.update(selector=selector, id=selector[1:],
                            label=caption, tag='input', type=kind,
                            native_type=kind, associated=True, unique=True,
                            inside=True, in_container=True,
                            required=extra['required'],
                            value=extra['value'], files=extra['files'])
            snapshot['entry']['root_evidence'].append(evidence)


# Opaque synthetic page-controlled diagnostic sentinel; no grant or
# per-action witness helper is reintroduced.
_ISSUE190_PRIVATE_ENTRY = 'SYNTHETIC_PRIVATE_ENTRY_SENTINEL'

@pytest.mark.parametrize('key', [
    'frames', 'responses', 'response_error', 'root', 'native_candidates',
    'formless', 'root_evidence', 'upload_tree', 'helper_challenge', 'other',
])
async def test_issue190_diagnostic_names_only_top_level_entry_key(monkeypatch, key):
    _row, raw, _provenance = _issue184_reversible_session(monkeypatch)
    if key == 'other':
        raw.fixed['entry']['synthetic_unlisted_key'] = _ISSUE190_PRIVATE_ENTRY
    else:
        raw.fixed['entry'][key] = _ISSUE190_PRIVATE_ENTRY
    with pytest.raises(forms.Gate) as failure:
        await forms._observe_controls(
            raw._reversible_session, raw.current, site='execute')
    text = str(failure.value)
    assert f'predicate=entry.equal.{key}; control=unavailable; site=execute' in text
    assert _ISSUE190_PRIVATE_ENTRY not in text
    no_mutations(raw)


@pytest.mark.parametrize('different_keys,expected', [
    (('root', 'frames'), 'frames'),
    (('upload_tree', 'root_evidence'), 'root_evidence'),
    (('synthetic_unlisted_key', 'responses'), 'responses'),
])
async def test_issue190_diagnostic_uses_fixed_priority_not_object_order(
        monkeypatch, different_keys, expected):
    _row, raw, _provenance = _issue184_reversible_session(monkeypatch)
    for key in different_keys:
        raw.fixed['entry'][key] = _ISSUE190_PRIVATE_ENTRY
    with pytest.raises(forms.Gate, match=f'predicate=entry.equal.{expected}'):
        await forms._observe_controls(
            raw._reversible_session, raw.current, site='execute')
    no_mutations(raw)


async def test_issue190_missing_entry_key_is_not_equal_to_null(monkeypatch):
    _row, raw, _provenance = _issue184_reversible_session(monkeypatch)
    assert 'root' in raw.fixed['entry']
    raw.fixed['entry'].pop('root')
    with pytest.raises(forms.Gate, match='predicate=entry.equal.root'):
        await forms._observe_controls(
            raw._reversible_session, raw.current, site='execute')
    no_mutations(raw)


def test_issue196_date_is_a_normal_answerable_input_without_bank_grants():
    target = {
        'selector': '#synthetic-date', 'tag': 'input', 'type': 'date',
        'in_form': False, 'disabled': False, 'required': True,
        'label': 'Start date', 'question': '', 'value': '', 'has_value': False,
    }
    assert forms._native_answer(target)
    assert not hasattr(forms, '_bank_auto_eligible')
    assert not hasattr(forms, '_require_native_action')

async def test_rooted_untyped_yes_no_pair_defers_requiredness_to_final_submit(monkeypatch):
    """#241: rooted Yes/No can be inspected and prepared, not silently approved.

    The separate final-Submit tests reject an unanswered or unapproved pair;
    unreadable requiredness is no longer an early structural admission ban.
    """
    _, raw, decision, provenance = root_world(monkeypatch, yesNo=True)
    assert forms._formless_root(raw.current)['root'] == '#application-root'
    group = [e for e in raw.current['entry']['root_evidence']
             if e.get('boolean_group')]
    assert len(group) == 2 and all(e['in_container'] for e in group)
    assert forms._formless_boundary(raw.current, provenance)
    # Normal Yes/No choices are not committing Submit actions.
    selectors = {e['selector'] for e in group}
    isolated = [c for c in raw.current['controls'] if c['selector'] in selectors]
    assert len(isolated) == 2
    assert all(not forms.is_submission(c) and not forms.native_submit(c)
               for c in isolated)
    assert not decision.await_count
    no_mutations(raw)
    _, plain, _, provenance = root_world(monkeypatch)
    assert forms._formless_boundary(plain.current, provenance)


@pytest.mark.parametrize('render', [
    {},
    {'additionalCaption': 'Synthetic re-rendered caption'},
    {'additionalCaption': 'Synthetic re-rendered caption', 'additionalRequired': True},
], ids=['same-caption', 'changed-caption', 'changed-caption-now-required'])
async def test_prior_rootless_skip_does_not_authorize_missing_owner_fact(
        monkeypatch, render):
    """A stale SKIP cannot turn an unanswered ordinary form field into assent."""
    row, raw, decide, _ = root_world(monkeypatch, **render)
    extra = next(c for c in raw.current['controls']
                 if c['selector'] == '#additional')
    assert extra['required'] is render.get('additionalRequired', False)
    assert not extra.get('has_value')
    row['owner_gate_skips'] = {'synthetic-old-skip': {
        'route': 'ashby-sibling-mismatch', 'generation': 'retired'}}
    with pytest.raises(forms.Gate) as caught:
        await forms.execute(
            raw, raw.current,
            {'action': 'fill', 'selector': extra['selector'],
             'fact': 'Additional information'},
            facts={}, filled={}, resume_path='', company=COMPANY,
            jd_text='', resume_tex='', github='', pk=PK,
            job_url=posting('ashby'))
    assert caught.value.reason == 'owner_question'
    assert caught.value.form_question['selector'] == extra['selector']
    assert not decide.await_count
    no_mutations(raw)


@pytest.mark.parametrize('spec_change', [
    {}, {'duplicateSubmit': True}, {'duplicatePane': True},
    {'secondFormlessComplete': True}, {'missingPane': True},
])
def test_formless_current_readers_agree_on_independent_entry_evidence(spec_change):
    # Historical byte identity to main is intentionally invalid after #293.
    # Compare the two active production readers on the same synthetic DOM.
    spec = dict(root=False, formless=True, url=entry('ashby'), **spec_change)
    page = dom(spec, forms.ENTRY_PAGE)
    fixed = dom(spec, forms._ENTRY_FIXED_CONTROL_WITNESS)
    assert page['entry'] == fixed['entry']
    assert [(c['selector'], c['type']) for c in page['controls']] == [
        (c['selector'], c['type']) for c in fixed['controls']]


@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS])
@pytest.mark.parametrize('attached', [False, True])
def test_formless_current_upload_tree_reports_staged_file_and_passive_delete(
        reader, attached):
    filename = 'Synthetic-Resume.pdf'
    spec = dict(root=False, formless=True, movePane=True, url=entry('ashby'),
                **({'attached': True, 'files': [filename], 'displayName': filename}
                   if attached else {}))
    current = dom(spec, reader)
    root = current['entry']['formless']
    tree = current['entry']['upload_tree']
    assert root['error'] == 'application root sibling identity mismatch'
    assert current['entry']['root'] is None
    assert root['root'] == '' and root['container'] and root['submit'] and root['pane']
    assert tree['complete'] is True and tree['scope'] == 'container'
    assert 1 <= len(tree['nodes']) <= 4096
    assert tree['nodes'][0]['tag'] == 'div'
    assert tree['nodes'][tree['input']]['target_selector'] == '#_systemfield_resume'
    assert tree['nodes'][tree['dropzone']]['target_selector'] == '#dropzone'
    assert tree['attached'] is attached
    if attached:
        assert tree['filename'] == filename and len(tree['items']) == 1
        deleted = '#delete-0'
        assert tree['nodes'][tree['delete']]['selector'] != deleted
        assert deleted not in {c['selector'] for c in current['controls']}
        assert deleted not in {c['selector'] for c in current['entry']['root_evidence']}
        assert deleted in root['excluded']
    else:
        assert tree['items'] == [] and not tree.get('attached')

@pytest.mark.parametrize('reader', [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS])
def test_hidden_ashby_attestation_requires_one_visible_label_and_hidden_input(reader):
    """Both independent readers bind only the hidden receiver's visible label."""
    valid = dom(dict(root=False, formless=True, missingPane=True,
                     hiddenAttestation=True, url=entry('ashby')), reader)
    matches = [c for c in valid['controls']
               if c.get('selector') == '#hidden-attestation']
    assert len(matches) == 1
    assert matches[0]['ashby_hidden_choice'] is True
    assert matches[0]['visible'] is False
    assert matches[0]['associated_label_selector'] == '#hidden-attestation-label'
    assert valid['entry']['formless']['root'] == ''
    for count in (0, 2):
        invalid = dom(dict(root=False, formless=True, missingPane=True,
                           hiddenAttestation=True, attestationLabels=count,
                           url=entry('ashby')), reader)
        assert not any(c.get('selector') == '#hidden-attestation'
                       and c.get('ashby_hidden_choice') is True
                       for c in invalid['controls'])

@pytest.mark.parametrize('reader', [PAGE, forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS])
@pytest.mark.parametrize('interactive', ['link', 'button', 'role-link', 'role-button', 'tabindex'])
def test_hidden_attestation_with_interactive_label_is_not_admitted(reader, interactive):
    """An embedded Privacy Policy link/interactive widget forbids label fallback."""
    observed_page = dom(dict(root=False, formless=True, missingPane=True,
                             hiddenAttestation=True, attestationInteractive=interactive,
                             url=entry('ashby')), reader)
    assert not any(c.get('selector') == '#hidden-attestation'
                   and c.get('ashby_hidden_choice') is True
                   for c in observed_page['controls'])
    if 'entry' in observed_page:
        evidence = [c for c in observed_page['entry']['root_evidence']
                    if c['selector'] == '#hidden-attestation']
        assert len(evidence) == 1
        assert evidence[0]['ashby_hidden_choice'] is False


# Issue #276: sanitized shape captured from two public Ashby applications. PAGE
# ignores opacity, so a certified hidden attestation reaches the rooted bootstrap
# with no hidden-choice flag; only the root evidence and the fixed witness carry it.
_HIDDEN_ATTESTATION = '#hidden-attestation'
_OWNER_ATTESTATION = 'I certify that these synthetic statements are true'


async def _opacity_blind_attestation_world(monkeypatch):
    row, raw, decision, provenance = root_world(
        monkeypatch, hiddenAttestation=True, attestationText=_OWNER_ATTESTATION)
    for snapshot in (raw.current, raw.fixed):
        for c in snapshot['controls']:
            if c['selector'] != _HIDDEN_ATTESTATION and c.get('type') != 'file':
                c.update(required=False)
        for e in snapshot['entry']['root_evidence']:
            if e['selector'] != _HIDDEN_ATTESTATION and e.get('type') != 'file':
                e.update(required=False)
    page_control = next(c for c in raw.current['controls']
                        if c['selector'] == _HIDDEN_ATTESTATION)
    for key in ('ashby_hidden_choice', 'associated_label_selector', 'visible'):
        page_control.pop(key, None)
    evidence = next(e for e in raw.current['entry']['root_evidence']
                    if e['selector'] == _HIDDEN_ATTESTATION)
    witness = next(c for c in raw.fixed['controls']
                   if c['selector'] == _HIDDEN_ATTESTATION)
    assert evidence['ashby_hidden_choice'] is True
    assert witness['ashby_hidden_choice'] is True and witness['visible'] is False
    session = forms._EntrySession(raw, lambda url: url == entry('ashby'), provenance,
                                  resume_path='Resume.pdf')
    page = await forms._stable_application_form(
        session, session.allowed, provenance,
        deadline=asyncio.get_running_loop().time() + 10)
    return row, session, page, provenance


async def test_hidden_attestation_remains_independently_observed_without_member_grant(
        monkeypatch):
    row, session, page, provenance = await _opacity_blind_attestation_world(monkeypatch)
    target = next(c for c in page['controls']
                  if c['selector'] == _HIDDEN_ATTESTATION)
    witness = next(c for c in session.raw.fixed['controls']
                   if c['selector'] == _HIDDEN_ATTESTATION)
    evidence = next(e for e in page['entry']['root_evidence']
                    if e['selector'] == _HIDDEN_ATTESTATION)
    # The model-facing page no longer publishes a special hidden-choice grant
    # flag. Independent form evidence remains present for final readback.
    assert 'ashby_hidden_choice' not in target
    assert witness['ashby_hidden_choice'] is True
    assert evidence['ashby_hidden_choice'] is True
    assert not hasattr(forms, '_disclosure_question')
    assert not hasattr(forms, '_member_witness_predicate')


@pytest.mark.parametrize('problem', [
    'permitted-backing', 'unrelated-missing', 'wrong-question',
    'missing-button', 'foreign-root',
])
def test_final_formless_boundary_ignores_only_hidden_backing_with_real_yes_no(
        monkeypatch, problem):
    """A hidden native backing can disappear from controls when the same
    proven Ashby question is answered by two visible Yes/No buttons. Nothing
    else can disappear from the independent root receiver census.
    """
    _, raw, _, provenance = root_world(monkeypatch)
    page = deepcopy(raw.current)
    base_evidence = next(c for c in page['entry']['root_evidence']
                         if c.get('selector') == '#_systemfield_name')
    base_control = next(c for c in page['controls']
                        if c.get('selector') == '#_systemfield_name')
    entry_id = '#synthetic-boolean-entry'
    question = 'Synthetic work preference'
    backing = {
        **deepcopy(base_evidence),
        'selector': '#hidden-backing', 'type': 'checkbox',
        'field_entry': entry_id, 'entry_label': question,
        'inside': True, 'in_container': True, 'visible': False,
        'yes_no': False,
    }
    for label in ('Yes', 'No'):
        selector = '#synthetic-' + label.lower()
        e = {
            **deepcopy(base_evidence),
            'selector': selector, 'type': 'button', 'tag': 'button',
            'field_entry': entry_id, 'entry_label': question,
            'inside': True, 'in_container': True, 'visible': True,
            'yes_no': True, 'caption': label,
        }
        c = {
            **deepcopy(base_control),
            'selector': selector, 'type': 'button', 'tag': 'button',
            'question': question, 'label': label,
            'checked': label == 'No', 'required': True,
        }
        page['entry']['root_evidence'].append(e)
        page['controls'].append(c)
    page['entry']['root_evidence'].append(backing)
    if problem == 'unrelated-missing':
        page['entry']['root_evidence'].append({
            **deepcopy(backing), 'selector': '#unrelated-missing', 'type': 'text'})
    elif problem == 'wrong-question':
        page['controls'][-1]['question'] = 'Another field'
    elif problem == 'missing-button':
        page['controls'].pop()
    elif problem == 'foreign-root':
        page['entry']['root_evidence'][-1]['inside'] = False

    if problem == 'permitted-backing':
        members = forms._formless_boundary(page, provenance)
        assert {'#synthetic-yes', '#synthetic-no'} <= {
            c['selector'] for c in members}
        assert '#hidden-backing' not in {c['selector'] for c in members}
    else:
        with pytest.raises(forms.Gate, match='lost a receiver'):
            forms._formless_boundary(page, provenance)
