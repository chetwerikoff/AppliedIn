"""Greenhouse uses the existing observed-stability budget, not a hydration oracle."""
import asyncio
from copy import deepcopy

import pytest

from tools import browser_skill as bsk
from tools import browser_skill_apply as forms
from tests.tools.test_browser_bootstrap import field, submit, inventory, witness

URL = 'https://job-boards.greenhouse.io/example-co/jobs/123'
ALIAS = forms.GreenhouseJobAlias(
    pk='example-co#123', company='example-co', ats='greenhouse', job_id='123',
    wrapper='https://careers.example-co.test/job/123', careers_url='https://careers.example-co.test/',
    board='example-co', url=URL)


def page():
    result = inventory(field('Additional question', '#answer', form_action=URL),
                       submit(form_action=URL), url=URL)
    result['greenhouse'] = dict(root=dict(selector='#application', action=URL),
                                frames=[dict(status='R')], response_controls=False, badge_responses=0)
    return result


def combo_page():
    p = page()
    p['controls'].insert(1, field('Location', '#location', type='combobox', form_action=URL,
        combobox=dict(mode='closed-react-select', phase='C0', linked=False, link_id='',
                      listbox_selector='', options=[], selected_text='', displayed_text='',
                      selected_value='', hidden_selector='#receiver', hidden_value='')))
    return p


def fixed(p):
    w = witness(p)
    w['greenhouse'] = deepcopy(p['greenhouse'])
    for original, observed in zip(p['controls'], w['controls']):
        if original['type'] == 'combobox':
            observed.update(combobox=deepcopy(original['combobox']),
                            graph_label_strict_visible=True, graph_display_strict_visible=True)
    return w


class Raw:
    def __init__(self, p=None, hook=None):
        self.current = p or page()
        self.hook = hook
        self.reads = []

    async def call(self, method, script):
        assert method == 'evaluate'
        assert script in {forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS}
        kind = 'page' if script == forms.GREENHOUSE_PAGE else 'witness'
        value = deepcopy(self.current) if kind == 'page' else fixed(self.current)
        index = len(self.reads)
        self.reads.append(kind)
        if self.hook:
            value = await self.hook(index, kind, value)
        return {'value': value}


def session(raw):
    return forms._GreenhouseSession(raw, lambda url: url == URL, ALIAS)


async def wait(scoped, duration=2):
    return await forms._stable_application_form(
        scoped, scoped.allowed, ALIAS, deadline=asyncio.get_running_loop().time()+duration)


@pytest.fixture
def fast(monkeypatch):
    original = asyncio.sleep
    async def sleep(_):
        await original(0)
    monkeypatch.setattr(forms.asyncio, 'sleep', sleep)


@pytest.mark.parametrize('at', [0, 1, 2])
@pytest.mark.parametrize('flag', ['root-null-zero', 'root-null-multiple', 'inventory_verified',
                                 'opaque_controls', 'shadow_roots', 'truncated'])
async def test_each_read_retries_safe_nonqualifying_observation(fast, at, flag, monkeypatch):
    async def transition(index, kind, value):
        if index == at:
            if flag.startswith('root-null'):
                # Current JS cannot distinguish zero from multiple candidates.
                value['greenhouse']['root'] = None
            else:
                value[flag] = flag != 'inventory_verified'
        return value
    raw = Raw(hook=transition)
    scoped = session(raw)
    result = await wait(scoped)
    assert result['controls'][0]['label'] == 'Additional question'
    assert len(raw.reads) >= at+7  # never count the interrupted pair
    assert scoped._readiness is False
    with pytest.raises(forms.Gate, match='unknown/untrusted application frame'):
        forms._greenhouse_safety({**result, 'greenhouse': {**result['greenhouse'], 'root': None}},
                                 scoped.allowed, ALIAS)
    monkeypatch.setattr(forms, '_alias_current', lambda *args, **kwargs: True)
    tracked = {'pk': ALIAS.pk, 'company': ALIAS.company, 'job_id': ALIAS.job_id,
               'jd_url': ALIAS.wrapper, 'ats': 'greenhouse',
               'status': 'submitting', 'possible_submission': False,
               'human_approved_answers': {}}
    monkeypatch.setattr(forms, '_row', lambda *args, **kwargs: tracked)
    # The absent ordinary answer does not create a historic per-step human
    # gate. Preparation may continue; the final guard must reject the current
    # required form value rather than infer an approval from PAGE text.
    prepared = await forms._bootstrap(
        scoped, result, ALIAS.pk, {}, provenance=ALIAS)
    assert prepared['controls'][0]['label'] == 'Additional question'
    assert prepared['controls'][0]['value'] == ''
    assert not prepared['controls'][0]['has_value']
    with pytest.raises(forms.Gate, match='Required field is empty: Additional question'):
        forms.check_form(prepared, {}, uploaded=True)
    assert all(kind in {'page', 'witness'} for kind in raw.reads)


@pytest.mark.parametrize('at', [0, 1, 2])
@pytest.mark.parametrize('error', [OSError, bsk.Unavailable])
@pytest.mark.parametrize('persistent', [False, True])
async def test_transport_retries_same_budget_resets_history(fast, at, error, persistent):
    async def fault(index, kind, value):
        if index == at or (persistent and index > at):
            raise error('synthetic transport failure')
        return value
    raw, scoped = Raw(hook=fault), None
    scoped = session(raw)
    if persistent:
        with pytest.raises(forms.Gate, match='after bounded wait'):
            await wait(scoped, .015)
    else:
        await wait(scoped)
        assert len(raw.reads) >= at+7
    assert scoped._readiness is False
    if at != 0:
        # The same transport error outside the readiness scope retains the old Gate policy.
        raw.reads = ['page'] * at
        with pytest.raises(forms.Gate, match='DOM witness'):
            await forms._observe_controls(scoped, raw.current)


@pytest.mark.parametrize('at', [1, 2])
async def test_inflight_deadline_is_bounded_wait_not_witness_gate(at):
    async def hang(index, kind, value):
        if index == at:
            await asyncio.Event().wait()
        return value
    scoped = session(Raw(hook=hang))
    with pytest.raises(forms.Gate, match='read timed out/unavailable after bounded wait'):
        await wait(scoped, .01)
    assert scoped._readiness is False


@pytest.mark.parametrize('at', [0, 1, 2])
async def test_external_cancellation_propagates_and_restores_context(at):
    entered = asyncio.Event()
    async def hang(index, kind, value):
        if index == at:
            entered.set()
            await asyncio.Event().wait()
        return value
    scoped = session(Raw(hook=hang))
    task = asyncio.create_task(wait(scoped))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scoped._readiness is False


@pytest.mark.parametrize('at', [0, 1, 2])
@pytest.mark.parametrize('unsafe', ['frame', 'captcha', 'response', 'alias', 'destination',
                                  'unsupported', 'malformed-root', 'missing-root'])
async def test_unsafe_observation_is_immediate_before_readiness(fast, at, unsafe):
    async def fault(index, kind, value):
        if index == at:
            if unsafe == 'frame': value['greenhouse']['frames'].append(dict(status='unknown'))
            elif unsafe == 'captcha': value['greenhouse']['frames'].append(dict(status='visible-recaptcha'))
            elif unsafe == 'response': value['greenhouse']['response_controls'] = True
            elif unsafe == 'alias': value['url'] += '/other'
            elif unsafe == 'destination': value['greenhouse']['root']['action'] = 'https://other.example-co.test/'
            elif unsafe == 'unsupported': value['unsupported_frames'] = ['synthetic']
            elif unsafe == 'missing-root': del value['greenhouse']['root']
            else: value['greenhouse']['root'] = dict(selector=3)
        return value
    raw = Raw(hook=fault)
    scoped = session(raw)
    with pytest.raises(forms.Gate) as exc:
        await wait(scoped)
    assert 'bounded wait' not in str(exc.value)
    assert len(raw.reads) == at+1
    assert scoped._readiness is False


# This literal list is independent of the implementation's allowlist. Each name
# below is exercised through the actual comparison and real wrapped session.
CONTROL_EQUALS = ['tag', 'type', 'in_form', 'form_selector', 'form_action', 'formaction',
                  'disabled', 'native_form_owner_selector']
COMBO_EQUALS = ['mode', 'phase', 'quarantined', 'required_proxy_mounted', 'label_selector',
                'field_selector', 'value_container_selector', 'input_wrapper_selector',
                'receiverless_country', 'country_graph', 'link_id', 'linked', 'listbox_selector',
                'options', 'selected_text', 'displayed_text', 'selected_value', 'hidden_selector', 'hidden_value']
RETRY = ['greenhouse.equal', 'controls.length.equal', 'visible_text.required_nonempty',
         'combobox.link_id.required_nonempty', 'selector.equal', 'visible_text.equal', 'options.equal',
         'combobox.equal', 'inventory.identity.equal'] + [k+'.equal' for k in CONTROL_EQUALS] + [
             'combobox.'+k+'.equal' for k in COMBO_EQUALS]
FATAL = ['url.equal', 'selector.unique', 'unique.true', 'unique_form.true', 'witness.object',
         'controls.list', 'control.object', 'combobox.object', 'combobox.PAGE.object',
         'combobox.linked.shape', 'visible.bool', 'readonly.bool', 'inert.bool', 'unlisted',
         'caption-wrong-type', 'link-wrong-type']


RETIRED_COMBOBOX_STEP_PREDICATES = {
    'combobox.link_id.required_nonempty',
    *('combobox.' + key + '.equal' for key in (
        'mode', 'phase', 'quarantined', 'required_proxy_mounted',
        'label_selector', 'field_selector', 'value_container_selector',
        'input_wrapper_selector', 'receiverless_country', 'country_graph')),
}

def drift(predicate, p, w):
    c, wc = p['controls'][1], w['controls'][1]
    if predicate == 'greenhouse.equal': w['greenhouse']['badge_responses'] = 1
    elif predicate == 'controls.length.equal': w['controls'].pop()
    elif predicate == 'selector.equal': wc['selector'] = '#other'
    elif predicate == 'options.equal':
        c.update(tag='select', type='select'); wc.update(tag='select', type='select', options=[dict(value='x', label='x')])
    elif predicate in {'visible_text.equal', 'visible_text.required_nonempty', 'caption-wrong-type'}:
        c.update(tag='input', type='checkbox', label='Synthetic caption')
        wc.update(tag='input', type='checkbox', visible_text=(None if predicate == 'caption-wrong-type' else
                                                           '' if predicate.endswith('required_nonempty') else 'Other caption'))
    elif predicate in {'combobox.link_id.required_nonempty', 'link-wrong-type'}:
        c['combobox'].update(mode='legacy', link_id='' if predicate.endswith('required_nonempty') else None)
        wc['combobox'] = deepcopy(c['combobox'])
    elif predicate.startswith('combobox.') and predicate.endswith('.equal'):
        key = predicate.split('.')[1]
        old = wc['combobox'].get(key)
        wc['combobox'][key] = (not old if key in {'linked', 'quarantined', 'required_proxy_mounted', 'receiverless_country'}
                              else ['#changed'] if key == 'country_graph' else [] if key == 'options' else 'changed')
        if key == 'options': wc['combobox']['options'] = [dict(selector='#o', label='x', value='x')]
    elif predicate == 'combobox.equal': wc['combobox']['extra'] = 'synthetic'
    elif predicate.endswith('.equal') and predicate.split('.')[0] in CONTROL_EQUALS:
        key = predicate.split('.')[0]
        wc[key] = (not wc[key] if key in {'in_form', 'disabled'} else
                   URL+'/apply' if key in {'form_action', 'formaction'} else 'changed')
    elif predicate == 'url.equal': w['url'] += '/other'
    elif predicate == 'selector.unique': p['controls'].append(deepcopy(c)); w['controls'].append(deepcopy(wc))
    elif predicate in {'unique.true', 'unique_form.true', 'visible.bool', 'readonly.bool', 'inert.bool'}:
        key, suffix = predicate.split('.'); wc[key] = False if suffix == 'true' else 'true'
    elif predicate == 'witness.object': return None
    elif predicate == 'controls.list': w['controls'] = None
    elif predicate == 'control.object': w['controls'][1] = None
    elif predicate == 'combobox.object': wc['combobox'] = None
    elif predicate == 'combobox.PAGE.object': c['combobox'] = None
    elif predicate == 'combobox.linked.shape':
        c['combobox']['linked'] = None; wc['combobox']['linked'] = None
    return w


@pytest.mark.parametrize('predicate', RETRY + FATAL)
async def test_every_expanded_predicate_retry_or_immediate_gate(fast, predicate, monkeypatch):
    p = combo_page()
    if predicate == 'options.equal':
        p['controls'][1].update(tag='select', type='select')
    elif predicate in {'visible_text.equal', 'visible_text.required_nonempty', 'caption-wrong-type'}:
        p['controls'][1].update(tag='input', type='checkbox', label='Synthetic caption')
    elif predicate in {'combobox.link_id.required_nonempty', 'link-wrong-type'}:
        p['controls'][1]['combobox'].update(mode='legacy', link_id='')
    elif predicate == 'combobox.PAGE.object':
        p['controls'][1]['combobox'] = None
    elif predicate == 'combobox.options.equal':
        p['controls'][1]['combobox'].update(linked=True, link_id='list', listbox_selector='#list')
    if predicate == 'unlisted':
        def unknown(_): raise ValueError('synthetic unlisted failure')
        monkeypatch.setattr(forms, '_page_identity', unknown)
    async def change(index, kind, value):
        if predicate == 'inventory.identity.equal' and index == 2:
            value['controls'][0]['label'] = 'Changed question'
        elif index == 1:
            value = drift(predicate, raw.current, value)
        return value
    baseline = deepcopy(p)
    raw = Raw(p, change)
    scoped = session(raw)
    if predicate in RETRY:
        # Restore PAGE after the deliberately empty / graphless frame as a later hydration.
        original = combo_page()
        old_hook = raw.hook
        async def recover(index, kind, value):
            value = await old_hook(index, kind, value)
            if index == 1: raw.current = deepcopy(original)
            return value
        raw.hook = recover
        await wait(scoped)
        assert len(raw.reads) >= 8
        if predicate not in RETIRED_COMBOBOX_STEP_PREDICATES:
            # Retained readiness witnesses still have a fatal inverse.
            monkeypatch.setattr(
                forms, '_GREENHOUSE_READINESS_PREDICATES', set(RETRY)-{predicate})
            raw = Raw(deepcopy(baseline), change)
            scoped = session(raw)
            with pytest.raises(forms.Gate, match='predicate='+predicate.replace('.', r'\.')):
                await wait(scoped)
        # Retired shape/phase predicates are not per-step human gates. Their
        # missing form-own receiver is refused by the final Submit guard.
    else:
        with pytest.raises(forms.Gate) as exc:
            await wait(scoped)
        assert 'bounded wait' not in str(exc.value)
        assert len(raw.reads) <= 3
    assert scoped._readiness is False


def test_absent_hidden_form_receiver_is_a_final_submit_negative():
    page = combo_page()
    question = page['controls'][0]
    question.update(value='Approved detail', has_value=True)
    picker = page['controls'][1]
    picker['combobox'].update(
        linked=True, link_id='list', selected_text='Approved location',
        displayed_text='Approved location', selected_value='location-code',
        hidden_selector='#receiver', hidden_value='location-code')
    # All visible values are correct; no actual hidden input exists. A shape
    # phase is irrelevant, but the ONE final guard may not authorize Submit.
    with pytest.raises(forms.Gate, match='hidden form value is not independently authorized'):
        forms.check_form(
            page, {'#answer': 'Approved detail', '#location': 'Approved location'},
            uploaded=True)


async def test_allowlist_is_closed_and_exact():
    assert forms._GREENHOUSE_READINESS_PREDICATES == set(RETRY)
    assert not (forms._GREENHOUSE_READINESS_PREDICATES & set(FATAL))


async def test_null_multiple_root_never_admits_and_times_out(fast):
    p = page(); p['greenhouse']['root'] = None
    scoped = session(Raw(p))
    with pytest.raises(forms.Gate, match='no qualifying application form after bounded wait'):
        await wait(scoped, .01)
    assert scoped._readiness is False


async def test_already_qualifying_partial_can_admit_before_unseen_late_hydration(fast):
    raw = Raw()
    scoped = session(raw)
    admitted = await wait(scoped)
    assert raw.reads == ['page', 'witness', 'page', 'page', 'witness', 'page']
    assert len(admitted['controls']) == 2
    # This is the accepted limitation: two pairs do NOT prove future completeness.
    raw.current['controls'].insert(0, field('Late question', '#late', form_action=URL))
    with pytest.raises(forms.Gate, match='controls.length.equal'):
        await forms._observe_controls(scoped, admitted)
    assert all(kind in {'page', 'witness'} for kind in raw.reads)  # no IPC writes


@pytest.mark.parametrize('pattern', ['one-pair', 'alternating', 'within-pair', 'never-equal'])
async def test_observed_stability_requires_two_consecutive_matching_pairs(fast, pattern):
    async def mutate(index, kind, value):
        if pattern == 'one-pair' and index >= 3:
            value['controls'][0]['label'] = 'Later question'
            if kind == 'page': raw.current = deepcopy(value)
        elif pattern == 'alternating' and kind == 'page':
            raw.current['controls'][0]['label'] = 'Question '+str((index//3)%2)
            value = deepcopy(raw.current)
        elif pattern in {'within-pair', 'never-equal'} and kind == 'witness' and (index == 1 or pattern == 'never-equal'):
            value['controls'].pop()
        return value
    raw = Raw(hook=mutate)
    scoped = session(raw)
    if pattern in {'alternating', 'never-equal'}:
        with pytest.raises(forms.Gate, match='after bounded wait'):
            await wait(scoped, .01)
    else:
        accepted = await wait(scoped)
        assert len(raw.reads) >= 8
        if pattern == 'one-pair': assert accepted['controls'][0]['label'] == 'Later question'
    assert scoped._readiness is False


async def test_nonqualifying_initial_inventory_waits_for_observed_applicant_field(fast):
    shell = page()
    shell['controls'] = [shell['controls'][-1]]  # marker, but no editable applicant field
    async def hydrate(index, kind, value):
        if index == 3:
            raw.current = page()
            return deepcopy(raw.current)
        return value
    raw = Raw(shell, hydrate)
    accepted = await wait(session(raw))
    assert accepted['controls'][0]['label'] == 'Additional question'
    assert len(raw.reads) == 9


@pytest.mark.parametrize('part', ['label', 'question'])
async def test_wrongtyped_page_caption_cannot_hide_behind_unready_inventory(fast, part):
    p = page(); p['controls'][0][part] = None; p['inventory_verified'] = False
    raw = Raw(p)
    with pytest.raises(forms.Gate, match='malformed'):
        await wait(session(raw))
    assert len(raw.reads) == 1
