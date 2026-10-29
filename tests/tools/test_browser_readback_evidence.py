"""FF-243a: the formless execute-readback names what failed, never a value.

Offline doubles only: the PAGE and fixed witness come from the synthetic DOM
readers used by test_browser_formless. No browser, profile or network.
"""
import logging

import pytest

from tools import browser_skill_apply as forms
from tests.tools.test_browser_application_entry import COMPANY, PK, posting
from tests.tools.test_browser_final_submit_grants import (
    _check, _grant, _isolated_reader_boolean, _rooted_boolean_reader, _rooted_live_dom)
from tests.tools.test_browser_formless import action_sequence, admitted, approve_fields
from tests.tools.test_browser_reversible_gates import (
    _answer, _apply, _fixture, _only_required, _record_boolean_press)

_PRIVATE = 'PRIVATE_SYNTHETIC_VALUE'


async def test_readback_gate_names_the_failed_comparison_never_a_value(monkeypatch, caplog):
    # A bare Gate stalled every formless Ashby row for an evening with no
    # evidence of which receiver kind failed. The evidence is shape only:
    # values are the owner's personal data.
    row, raw, session, page, provenance = await admitted(monkeypatch)
    facts = approve_fields(row, page, raw.fixed, provenance)
    original = raw.call

    async def corrupt(method, *args, **kw):
        result = await original(method, *args, **kw)
        if method == 'fill':
            for snapshot in (raw.current, raw.fixed):
                for c in snapshot['controls'] + snapshot['entry']['root_evidence']:
                    if c['selector'] == args[0]:
                        c['value'] = _PRIVATE
        return result

    raw.call = corrupt
    caplog.set_level(logging.WARNING)
    with pytest.raises(forms.Gate, match='did not read back exactly') as refusal:
        await forms.execute(session, page, action_sequence()[0], facts=facts, filled={},
                            resume_path='Resume.pdf', company=COMPANY, jd_text='',
                            resume_tex='', github='', pk=PK, job_url=posting('ashby'),
                            provenance=provenance)
    text = str(refusal.value)
    assert 'verb=fill; tag=input;' in text
    assert 'failed=value;' in text and 'folded_equal=False' in text
    assert f'read_len={len(_PRIVATE)};' in text
    logged = '\n'.join(r.getMessage() for r in caplog.records
                       if 'execute-readback mismatch' in r.getMessage())
    assert 'failed=value;' in logged and 'label=' in logged
    approved = [v for v in facts.values() if isinstance(v, str) and len(v) > 2]
    assert approved
    for surface in (text, logged):
        assert _PRIVATE not in surface
        assert not [v for v in approved if v in surface]


@pytest.mark.parametrize('pressed', [None, '#boolean-yes'],
                         ids=['click-not-registered', 'other-member-pressed'])
@pytest.mark.asyncio
async def test_boolean_readback_requires_the_clicked_member_pressed(
        monkeypatch, tmp_path, pressed):
    # A <button> never reads back a value, so its proof is the pressed member
    # of the bound pair; any other outcome of the click is a Gate.
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    if pressed:
        _record_boolean_press(endpoint, pressed)
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['status'] == 'gate'
    assert 'did not read back exactly' in result['question']
    assert 'type=button;' in result['question'] and 'failed=selected;' in result['question']
    assert [c for c in endpoint.calls if c[0] == 'click'] == [('click', '#boolean-no')]


def _record_lagging_press(endpoint, selector, lag):
    """FF-247a: the probed pressed shape, rendered only after `lag` observations.

    A background tab can lag Ashby's re-render after the click; the pressed
    member then appears on a later observation without any further click.
    One observation is PAGE, fixed witness, PAGE: the render lands between two.
    """
    previous = endpoint.call
    state = {'clicked': False, 'reads': 0}

    async def call(method, *args, **kwargs):
        if state['clicked'] and method == 'evaluate':
            state['reads'] += 1
            if state['reads'] % 3 == 1 and state['reads'] // 3 >= lag:
                for snapshot in (endpoint.current, endpoint.fixed):
                    for control in snapshot['controls']:
                        if control['selector'] == selector:
                            control.update(value='', has_value=False, checked=True)
        outcome = await previous(method, *args, **kwargs)
        if method == 'click' and args == (selector,):
            state['clicked'] = True
        return outcome

    endpoint.call = call


@pytest.mark.parametrize('lag', [1, 3], ids=['one-lagging-read', 'three-lagging-reads'])
@pytest.mark.asyncio
async def test_lagging_no_press_reads_back_without_a_second_click(
        monkeypatch, tmp_path, lag):
    # Before FF-247a the readback took one observation, so a No rendered
    # pressed a moment after the click gated every row answering No.
    monkeypatch.setattr(forms, '_BOOLEAN_READBACK_INTERVAL', 0)
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    _record_lagging_press(endpoint, '#boolean-no', lag)
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['status'] == 'gate'
    assert 'did not read back exactly' not in result['question']
    assert result['question'].startswith('Synthetic verification complete')
    # A second click would toggle the pressed member off (live probe).
    assert [c for c in endpoint.calls if c[0] == 'click'] == [('click', '#boolean-no')]


@pytest.mark.asyncio
async def test_no_press_that_never_renders_still_gates_after_bounded_rereads(
        monkeypatch, tmp_path):
    monkeypatch.setattr(forms, '_BOOLEAN_READBACK_INTERVAL', 0, raising=False)
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    _record_lagging_press(endpoint, '#boolean-no', 10_000)
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert 'failed=selected;' in result['question']
    assert [c for c in endpoint.calls if c[0] == 'click'] == [('click', '#boolean-no')]


def _press(endpoint, selector):
    for snapshot in (endpoint.current, endpoint.fixed):
        for control in snapshot['controls']:
            if control['selector'] == selector:
                control.update(value='', has_value=False, checked=True)


@pytest.mark.asyncio
async def test_pre_pressed_no_is_not_clicked_again_and_reads_back(monkeypatch, tmp_path):
    # FF-247a: a click on an already pressed Ashby member toggles it off
    # (live probe), which read back as failed=selected with checked=False.
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    _press(endpoint, '#boolean-no')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert result['question'].startswith('Synthetic verification complete')
    assert [c for c in endpoint.calls if c[0] == 'click'] == []


@pytest.mark.asyncio
async def test_pre_pressed_other_member_is_not_treated_as_answered(monkeypatch, tmp_path):
    # Only the granted member pressed alone skips the click; a pressed partner
    # still refuses before any browser write.
    row, endpoint, decision, stores, pdf = _fixture(monkeypatch, tmp_path)
    _only_required(endpoint, {'#boolean-yes', '#boolean-no'})
    gate = await _apply(pdf)
    _answer(row, stores, gate, 'No')
    _press(endpoint, '#boolean-yes')
    decision.side_effect = [
        {'action': 'choose', 'selector': '#boolean-no', 'fact': 'Work arrangement?'},
        {'action': 'gate', 'question': 'Synthetic verification complete'}]
    result = await _apply(pdf, {'Work arrangement?': 'No'})
    assert not result['question'].startswith('Synthetic verification complete')
    assert [c for c in endpoint.calls if c[0] == 'click'] == []


def test_boolean_member_carrying_a_value_is_not_a_live_ashby_shape():
    # The fabricated double once wrote value/has_value onto the pressed
    # button. No live page produces that, so it fails closed.
    page = _isolated_reader_boolean(_rooted_boolean_reader(selected=True))
    pressed = next(c for c in page['controls'] if c.get('checked') is True)
    assert pressed['value'] == '' and pressed['has_value'] is False
    pressed.update(value=pressed['label'].casefold(), has_value=True)
    with pytest.raises(forms.Gate, match='pair is incomplete or unsafe'):
        forms._boolean_pair(page, pressed)


def _rooted_pair(mutation):
    """The rooted reader page for the probed live Yes/No shape plus a mutation.

    Probe (FF-243a): the chosen member gains aria-pressed, and the pair's one
    named, invisible checkbox is checked exactly when Yes is pressed.
    """
    page, witness = _rooted_live_dom(
        "group.children[2].setAttribute('name','synthetic-companion');" + mutation)
    assert page['entry'] == witness['entry'] and page['entry']['formless']['root']
    return _isolated_reader_boolean(page)


_PRESS_YES = ("group.children[0].setAttribute('aria-pressed','true');"
              "group.children[2].checked=true;")
_PRESS_NO = "group.children[1].setAttribute('aria-pressed','true');"


def _yes(page):
    return next(c for c in page['controls'] if c['label'] == 'Yes')


def _granted(monkeypatch, page, answer):
    yes = _yes(page)
    _check(monkeypatch, page, facts={}, values={},
           grants={forms._key(page, yes): _grant(page, yes, answer)})


@pytest.mark.parametrize('mutation, answer', [(_PRESS_YES, 'Yes'), (_PRESS_NO, 'No')],
                         ids=['yes-answered', 'no-answered'])
def test_rooted_pair_with_agreeing_companion_passes_final_submit(
        monkeypatch, mutation, answer):
    page = _rooted_pair(mutation)
    companion = [e for e in page['entry']['root_evidence']
                 if e.get('boolean_group') and e['type'] == 'checkbox']
    assert len(companion) == 1 and companion[0]['name']
    assert companion[0]['checked'] is (answer == 'Yes')
    _granted(monkeypatch, page, answer)


def test_rooted_pair_unanswered_is_refused(monkeypatch):
    with pytest.raises(forms.Gate, match='A Yes/No answer is empty or conflicting'):
        _granted(monkeypatch, _rooted_pair(''), 'Yes')


@pytest.mark.parametrize('mutation, answer', [
    ("group.children[0].setAttribute('aria-pressed','true');", 'Yes'),
    (_PRESS_NO + "group.children[2].checked=true;", 'No'),
    ("group.children[2].checked=true;", 'Yes'),
], ids=['yes-pressed-unchecked', 'no-pressed-checked', 'none-pressed-checked'])
def test_rooted_companion_disagreeing_with_the_pressed_member_is_refused(
        monkeypatch, mutation, answer):
    with pytest.raises(forms.Gate, match='disagrees with its pressed member'):
        _granted(monkeypatch, _rooted_pair(mutation), answer)


@pytest.mark.parametrize('stray', [
    "node('input',{type:'checkbox',name:'stray',style:{display:'none'},rect:null},group)"
    ".checked=true;",
    "node('input',{type:'checkbox',name:'stray',style:{display:'none'},rect:null},"
    "node('div',{class:'ashby-application-form-input-yesno'},"
    "entry('Synthetic orphan question','synthetic-orphan'))).checked=true;",
], ids=['second-in-pair', 'outside-any-pair'])
def test_rooted_stray_checked_checkbox_is_refused(monkeypatch, stray):
    with pytest.raises(forms.Gate, match='not the single companion of one verified pair'):
        _granted(monkeypatch, _rooted_pair(_PRESS_YES + stray), 'Yes')


_SECOND_PAIR = (
    "const other=node('div',{class:'ashby-application-form-input-yesno'},"
    "entry('Synthetic second Boolean question','synthetic-second'));"
    "untyped(other,{text:'Yes'});untyped(other,{text:'No'});"
    "node('input',{type:'checkbox',name:'synthetic-companion-2',"
    "style:{display:'none'},rect:null},other);"
    "other.children[1].setAttribute('aria-pressed','true');")


@pytest.mark.parametrize('second_checked', [False, True], ids=['agree', 'disagree'])
def test_two_rooted_pairs_each_need_their_own_agreeing_companion(
        monkeypatch, second_checked):
    page = _rooted_pair(_PRESS_YES + _SECOND_PAIR
                        + ("other.children[2].checked=true;" if second_checked else ''))
    first, second = [c for c in page['controls'] if c['label'] == 'Yes']
    grants = {forms._key(page, first): _grant(page, first, 'Yes'),
              forms._key(page, second): _grant(page, second, 'No')}
    if second_checked:
        with pytest.raises(forms.Gate, match='disagrees with its pressed member'):
            _check(monkeypatch, page, facts={}, values={}, grants=grants)
    else:
        _check(monkeypatch, page, facts={}, values={}, grants=grants)
