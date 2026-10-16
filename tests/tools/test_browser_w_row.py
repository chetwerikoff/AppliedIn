"""Exact W admission: real reader scripts, synthetic DOM, and no browser IPC.

Each negative changes one property of the link-present positive. Guard sensitivity
executes the same production reader with only the named predicate removed in
memory; the removal is never written to production or used by the controller.
"""
import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import (
    Session, attempt, dom, entry, fake_clock, posting, world, PK, COMPANY,
)

READERS = [forms.ENTRY_PAGE, forms._ENTRY_FIXED_CONTROL_WITNESS]
GATE = 'unknown/untrusted application frame'
WIDGETS = {'#w-button', '#w-link', '#w-extra'}


def extra(tag='div', **attrs):
    return {'extra': [{'tag': tag, 'attrs': attrs}]}


NEGATIVES = {
    'w_second_button': extra('button', type='button'),
    'w_missing_button': {'missingButton': True},
    'w_wrong_button_class': {'button': {'class': 'other'}},
    'w_nonhtml_button': {'buttonNamespace': 'http://www.w3.org/2000/svg'},
    'w_nonhtml_anchor': {'linkNamespace': 'http://www.w3.org/2000/svg'},
    'w_button_aria_label': {'button': {'aria-label': 'Synthetic consent'}},
    'w_button_labelledby': {'labelledby': True},
    'w_button_visible_name': {'child': {'attrs': {'text': 'Synthetic consent'}}},
    'w_button_external_label': {'externalLabel': True},
    'w_button_img_alt': {'child': {'tag': 'img', 'attrs': {'alt': 'Synthetic consent'}}},
    'w_button_title_name': {'button': {'title': 'Synthetic consent'}},
    'w_button_name_attr': {'button': {'name': 'consent'}},
    'w_button_form_attr': {'button': {'form': 'application-form'}},
    'w_button_formaction': {'button': {'formaction': entry('lever')}},
    'w_button_submit': {'button': {'type': 'submit'}},
    'w_button_missing_type': {'missingType': True},
    'w_link_same_origin': {'link': {'href': entry('lever')}},
    'w_link_relative': {'link': {'href': '/learn'}},
    'w_link_http': {'link': {'href': 'http://widgets.example-co.test'}},
    'w_link_protocol_relative': {'link': {'href': '//widgets.example-co.test'}},
    'w_link_empty': {'missingHref': True},
    'w_link_empty_value': {'link': {'href': ''}},
    'w_link_non_https_scheme': {'link': {'href': 'mailto:synthetic@example-co.test'}},
    'w_second_learn_more': extra('a', **{'class': 'learn-more-link', 'href': 'https://widgets.example-co.test'}),
    'w_other_anchor': extra('a', href='https://widgets.example-co.test'),
    'w_other_anchor_no_href': extra('a'),
    'w_extra_tabbable_control': extra(tabindex='0'),
    'w_extra_inline_click_control': extra(onclick='synthetic()'),
    'w_row_tabindex': {'row': {'tabindex': '0'}},
    'w_row_inline_click': {'row': {'onclick': 'synthetic()'}},
    'w_unassociated_custom_element': extra('synthetic-control'),
    'w_extra_form_member': extra('output'),
    'w_open_shadow_host': {'extra': [{'tag': 'div', 'shadow': True}]},
    'w_button_aria_role': {'button': {'role': 'checkbox'}},
    'w_link_aria_role': {'link': {'role': 'checkbox'}},
    **{f'w_role_{role}': extra(role=role) for role in
       ['button', 'checkbox', 'switch', 'textbox', 'radio', 'option', 'combobox', 'slider']},
    **{f'w_{tag}': extra(tag) for tag in ['input', 'textarea', 'select']},
    'w_contenteditable': extra(contenteditable='false'),
    'w_descendant_required': extra(required=''),
    'w_descendant_aria_required': extra(**{'aria-required': 'true'}),
    'w_row_dom_required': {'rowPropertyRequired': True},
    'w_row_required_attr': {'rowAttributeRequired': True},
    'w_row_aria_required': {'row': {'aria-required': 'true'}},
    'w_row_role': {'row': {'role': 'checkbox'}},
    'w_native_escape_summary': {'extra': [{'parent': 'details', 'tag': 'summary'}]},
    'w_native_escape_area': extra('area', href='https://widgets.example-co.test'),
    'w_native_escape_audio': extra('audio', controls=''),
    'w_native_escape_video': extra('video', controls=''),
    'w_native_escape_object': extra('object'),
    'w_native_escape_embed': extra('embed'),
    'w_compound_anchor': extra('a', tabindex='0', onclick='synthetic()'),
    'w_button_generated_name': {'button': {'pseudo': {'::before': '"Synthetic consent"'}}},
    'w_button_owns': {'button': {'aria-owns': 'external'}},
    'w_button_wrapping_label': {'wrappingLabel': True},
    **{f'w_unknown_{prop}': {'unknown': prop} for prop in ['labels', 'elements', 'textContent']},
    **{f'w_{part}_{signal}': {part: {signal: '0' if signal == 'tabindex' else 'synthetic'}}
       for part in ['field', 'container', 'span', 'iframe']
       for signal in ['role', 'tabindex', 'onkeydown']},
}


def observe(w=None, script=None, **spec):
    return dom({'frames': ['W'], 'w': w or {}, **spec}, script)


def rejected(page):
    assert page['entry']['frames'][0]['status'] == 'unknown'
    with pytest.raises(forms.Gate, match=GATE):
        forms._entry_safety(page, lambda url: url == entry('lever'), None)


def passive(page):
    assert page['entry']['frames'][0]['status'] == 'W'
    assert {c['selector'] for c in page['controls']} == {'#email', '#submit'}
    assert not (WIDGETS & {c['selector'] for c in page['controls']})
    assert 'widgets.example-co.test' not in repr(page['controls'])


@pytest.mark.parametrize('script', READERS, ids=['PAGE', 'fixed-witness'])
@pytest.mark.parametrize('no_link', [False, True], ids=['link', 'no-link'])
def test_w_exact_positive_and_pregraph_quarantine(script, no_link):
    page = observe({'noLink': no_link}, script)
    passive(page)
    assert page['entry']['root']['selector'] == '#application-form'
    assert page['url'] == entry('lever')  # baseURI in the fake is example-co.test.


@pytest.mark.parametrize('script', READERS, ids=['PAGE', 'fixed-witness'])
@pytest.mark.parametrize('cause', NEGATIVES)
def test_w_named_single_mutation_matrix(script, cause):
    page = observe(NEGATIVES[cause], script)
    rejected(page)
    # Unknown W is never an exemption for a neighboring real applicant field.
    if not cause.startswith('w_unknown_'):
        assert '#email' in {c['selector'] for c in page['controls']}


@pytest.mark.parametrize('script', READERS, ids=['PAGE', 'fixed-witness'])
@pytest.mark.parametrize('spec', [
    {'edit': {'src': ''}}, {'edit': {'title': 'LinkedIn Embedded Content '}},
    {'wOutside': True}, {'brokenEdge': True},
    {'edit': {'rect': {'width': 401, 'height': 48}}},
    {'edit': {'rect': {'width': 236, 'height': 101}}},
    {'edit': {'rect': {'width': -1, 'height': 48}}}, {'edit': {'rect': None}},
])
def test_w_original_signature_stays_conjunctive(script, spec):
    rejected(observe(script=script, **spec))


class DOMSession(Session):
    """Every PAGE and fixed witness runs the actual script, including nested reads."""
    def __init__(self, w=None, *, later=None, after=4):
        super().__init__([])
        self.w = w or {}
        self.later, self.after = later, after
        self.observations = 0

    async def call(self, method, *args):
        self.calls.append((method, *args))
        if method == 'navigate':
            assert args == (entry('lever'),)
            return {}
        # Reject ALL targeted evaluation, click, focus, fill and navigation steps,
        # rather than just a known subset of widget selectors.
        assert method == 'evaluate' and len(args) == 1 and args[0] in READERS
        self.observations += 1
        change = self.later if self.later is not None and self.observations > self.after else self.w
        return {'value': observe(change, args[0])}


def zero_target_ipc(session):
    assert all(call == ('navigate', entry('lever')) or
               (call[0] == 'evaluate' and len(call) == 2 and call[1] in READERS)
               for call in session.calls)


@pytest.mark.parametrize('cause', NEGATIVES)
async def test_w_negative_real_application_path_gates_without_target_ipc(monkeypatch, cause):
    world(monkeypatch, 'lever')
    session = DOMSession(NEGATIVES[cause])
    monkeypatch.setattr(bsk, 'Session', lambda *args: session)
    result = await attempt('lever')
    assert result['status'] == 'gate' and GATE in result['question']
    zero_target_ipc(session)


@pytest.mark.parametrize('cause', NEGATIVES)
@pytest.mark.parametrize('reader_index', [0, 1], ids=['fresh-PAGE', 'fresh-witness'])
async def test_w_invalidating_later_read_revokes_before_next_action(monkeypatch, cause, reader_index):
    world(monkeypatch, 'lever')
    raw = DOMSession(later=NEGATIVES[cause], after=4+reader_index)
    proof = forms._configured_alias(PK, COMPANY, posting('lever'))
    scoped = forms._EntrySession(raw, lambda url: url == entry('lever'), proof)
    # Two complete independent pairs precede the mutation. No initial grant is
    # carried into any nested/pre-fill/pre-action/pre-submit/receipt observation.
    for script in READERS * 2:
        passive((await scoped.call('evaluate', script))['value'])
    if reader_index:
        passive(await scoped.page())
    with pytest.raises(forms.Gate, match=GATE):
        await scoped.call('evaluate', READERS[reader_index])
    zero_target_ipc(raw)


@pytest.mark.parametrize('before,after', [
    ({}, {'noLink': True}), ({'noLink': True}, {}),
    ({}, {'link': {'href': 'https://other-widgets.example-co.test/learn'}}),
], ids=['w_link_removed_stays_w', 'w_link_added_stays_w', 'w_link_external_href_changed_stays_w'])
async def test_w_valid_later_changes_rebuild_exact_exclusions(monkeypatch, before, after):
    world(monkeypatch, 'lever')
    raw = DOMSession(before, later=after)
    proof = forms._configured_alias(PK, COMPANY, posting('lever'))
    scoped = forms._EntrySession(raw, lambda url: url == entry('lever'), proof)
    for script in READERS * 5:
        passive((await scoped.call('evaluate', script))['value'])
    zero_target_ipc(raw)


# Each removal is narrowly bound to its actual production text. An anchor change
# must fail loudly, as silently missing script.replace rewrites broke entry once.
SENSITIVE = [
    ('w_other_anchor_no_href', 'if (anchors.length!==(link ? 1 : 0) || (link && anchors[0]!==link)) return null;'),
    ('w_extra_tabbable_control', "if (names.includes('tabindex')) return null;"),
    ('w_extra_inline_click_control', "if (names.some(a=>a.startsWith('on'))) return null;"),
    ('w_role_switch', "if (role!==null && (typeof role!=='string' || role.trim()!=='')) return null;"),
    ('w_extra_form_member', 'if (list(root.elements).some(n=>row.contains(n) && n!==button)) return null;'),
    ('w_button_external_label', "if (list(button.labels).length || button.closest('label')) return null;"),
    ('w_button_img_alt', 'if (attrs(n).some(a=>nameAttrs.includes(a))) return null;'),
    ('w_button_visible_name', "if (typeof n.textContent!=='string' || n.textContent.trim() ||\n            typeof n.innerText!=='string' || n.innerText.trim()) return null;"),
    ('w_button_generated_name', "if (typeof content!=='string' || !['none','normal','\"\"',\"''\"].includes(content)) return null;"),
    ('w_native_escape_summary', "if (n!==row && n.matches('summary,area[href],audio[controls],video[controls],object,embed')) return null;"),
    ('w_open_shadow_host', "if (typeof n.localName!=='string' || !n.localName || n.localName.includes('-') ||\n            n.shadowRoot!==null) return null;"),
    ('w_row_dom_required', '!row.required &&'),
    ('w_row_required_attr', "&& !row.hasAttribute('required')"),
    ('w_row_aria_required', "row.getAttribute('aria-required')!=='true' &&"),
    ('w_descendant_required', "!row.querySelector('[contenteditable],[required],[aria-required=\"true\"]')"),
    ('w_button_name_attr', "if (button.getAttribute('type')!=='button' || button.type!=='button' ||\n          ['name','form','formaction'].some(a=>button.hasAttribute(a))) return null;"),
    ('w_link_relative', 'if (!valid) return null;'),
    ('w_wrong_button_class', "if (buttons.length!==1 || !buttons[0].matches('button.awli-button') || links.length>1) return null;"),
    ('w_unassociated_custom_element', "if (typeof n.localName!=='string' || !n.localName || n.localName.includes('-') ||\n            n.shadowRoot!==null) return null;"),
    ('w_nonhtml_anchor', "if ([button,...links].some(n=>n.namespaceURI!=='http://www.w3.org/1999/xhtml')) return null;"),
]


@pytest.mark.parametrize('script', READERS, ids=['PAGE', 'fixed-witness'])
@pytest.mark.parametrize('cause,guard', SENSITIVE, ids=[item[0] for item in SENSITIVE])
def test_w_separable_guard_removal_is_red_restoration_green(script, cause, guard):
    assert script.count(guard) == 1
    replacement = 'true' if guard.startswith('!row.querySelector') else ''
    # Removing a conjunction operand must preserve the surrounding expression.
    removed = script.replace(guard, replacement)
    admitted = observe(NEGATIVES[cause], removed)
    assert admitted['entry']['frames'][0]['status'] == 'W'
    with pytest.raises(AssertionError):
        rejected(admitted)  # RED: the normal regression assertion fails.
    rejected(observe(NEGATIVES[cause], script))  # GREEN with guard restored.


@pytest.mark.parametrize('script', READERS, ids=['PAGE', 'fixed-witness'])
def test_w_exclusion_itself_is_sensitive(script):
    guard = 'widget.forEach(n=>excluded.add(n));'
    assert script.count(guard) == 1
    with pytest.raises(AssertionError):
        passive(observe(script=script.replace(guard, '')))
    passive(observe(script=script))


async def test_w_positive_readiness_reaches_original_human_gate(monkeypatch):
    _, _, _, decision = world(monkeypatch, 'lever')
    decision.return_value = {'action': 'fill', 'selector': '#email', 'fact': 'Email'}
    clock = fake_clock(monkeypatch)
    clock[0] = 1000.0
    raw = DOMSession()
    monkeypatch.setattr(bsk, 'Session', lambda *args: raw)
    result = await attempt('lever')
    assert result['status'] == 'gate'
    assert GATE not in result['question']
    assert (result.get('form_question') or {}).get('selector') == '#email', result
    assert raw.observations >= 4
    zero_target_ipc(raw)


@pytest.mark.parametrize('cause', ['w_other_anchor_no_href', 'w_extra_tabbable_control',
                                  'w_extra_inline_click_control', 'w_button_external_label'])
async def test_w_actual_prefill_and_presubmit_witness_revokes(monkeypatch, cause):
    world(monkeypatch, 'lever')
    proof = forms._configured_alias(PK, COMPANY, posting('lever'))
    raw = DOMSession(later=NEGATIVES[cause], after=4)
    scoped = forms._EntrySession(raw, lambda url: url == entry('lever'), proof)
    initial = await scoped.page()
    for script in [READERS[1], *READERS]:
        passive((await scoped.call('evaluate', script))['value'])
    # execute uses the original independent pre-fill observer; the same observer
    # also owns pre-submit and receipt rechecks. It must stop before any fill IPC.
    with pytest.raises(forms.Gate, match=GATE):
        await forms.execute(
            scoped, initial, {'action': 'fill', 'selector': '#email', 'fact': 'Email'},
            facts={'Email': 'synthetic@example-co.test'}, filled={}, resume_path='',
            company=COMPANY, jd_text='', resume_tex='', github='', pk=PK,
            job_url=posting('lever'), provenance=proof)
    zero_target_ipc(raw)
