"""Issue 288: an unrelated header button's disabled state at the execute witness.

Live evidence: after a granted country checkbox-group answer, the next execute
on a public Greenhouse embed gated with

    exception=ValueError; predicate=disabled.equal;
    control=div:nth-of-type(2) > div:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1);
    site=execute

That locator is the header "Autofill my application" button
(.application--header--buttons > .application--header--autofill-with-greenhouse
> .autofill_button > button), outside the application form. PROVEN: its PAGE
`disabled` differed from the fixed witness at the execute site. NOT proven: why
it changed (no live sample was taken), so the fix does not rely on any timing.

PAGE and the witness collect every visible document button, so execute
compares this button although no action touches it. At the execute site only,
the disabled comparison skips a native button that is not the action target,
is not submission-classified, does not commit the application, and is outside
every form on a page without a formless structural root. Its presence,
selector, form ownership and other keys stay exact, and the page identity
reread still sees it. The target, every submission button and every in-form
control keep the exact disabled.equal gate. The pages are synthetic.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.tools.test_browser_bootstrap import PK, field, submit, witness
from tests.tools.test_browser_checkbox_group import (
    ToggleBrowser, URL, answered, by, group_page, row,  # noqa: F401  (row is a fixture)
)
from tools import browser_skill_apply as forms

DISABLED_GATE = 'exception=ValueError; predicate=disabled.equal'
CHANGED = 'The current DOM witness is unavailable or changed; exception=ValueError'
TARGET = '#region-1'


def autofill(**extra):
    """The out-of-form header button that no application action touches."""
    values = dict(required=False, tag='button', type='button', in_form=False,
                  form_selector='', form_action='', native_form_owner_selector='')
    values.update(extra)
    return field('Autofill my application', '#autofill', **values)


def outside_submit():
    """A submission-classified button that is also outside the form."""
    return field('Submit application', '#outside-submit', required=False,
                 tag='button', type='button', in_form=False, form_selector='',
                 form_action='', native_form_owner_selector='')


def in_form_button():
    return field('Add another', '#add-another', required=False, tag='button',
                 type='button', form_action=URL)


def live_page():
    page = group_page()
    page['controls'] += [autofill(), outside_submit(), in_form_button()]
    return page


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(forms.asyncio, 'sleep', instant)


class LiveBrowser(ToggleBrowser):
    """PAGE and the fixed witness are both read from one live DOM.

    `flip` names a control whose disabled state toggles right after every
    witness read (a change the caller cannot see).
    """

    def __init__(self, page, flip=None):
        super().__init__(page)
        self.allow_mutations = True
        self.flip = flip
        self.reads = 0

    async def page(self):
        self.pages += 1
        return deepcopy(self.current)

    async def call(self, *args, **kwargs):
        if args[0] == 'evaluate':
            self.calls.append(args)
            assert args[1] == forms._FIXED_CONTROL_WITNESS
            value = witness(self.current)
            self.reads += 1
            if self.flip:
                control = by(self.current, self.flip)
                control['disabled'] = not control['disabled']
            return {'value': value}
        return await super().call(*args, **kwargs)


def stale_with(selector):
    """The caller's PAGE, read before `selector`'s disabled state changed."""
    caller = live_page()
    browser = LiveBrowser(deepcopy(caller))
    by(browser.current, selector)['disabled'] = True
    return caller, browser


async def test_header_button_change_rereads_page_and_passes():
    caller, browser = stale_with('#autofill')
    current, lookup = await forms._observe_controls(
        browser, caller, site='execute', execute_target=TARGET)
    assert by(current, '#autofill')['disabled'] is True
    assert lookup['#autofill']['disabled'] is True
    # The changed page identity forced one fresh PAGE + witness reread.
    assert browser.reads == 2


async def test_header_button_flipping_on_every_read_still_gates_on_identity():
    page = live_page()
    browser = LiveBrowser(deepcopy(page), flip='#autofill')
    with pytest.raises(forms.Gate, match=CHANGED + '; predicate=inventory.identity.equal'):
        await forms._observe_controls(browser, page, site='execute', execute_target=TARGET)
    assert browser.reads == 4


@pytest.mark.parametrize('selector', [
    TARGET,                # the action target
    '#outside-submit',     # submission-classified, outside the form
    '#submit',             # the form's own submit button
    '#add-another',        # an in-form button
    '#region-0',           # an in-form control that is not the target
])
async def test_every_other_disabled_change_keeps_the_exact_gate(selector):
    caller, browser = stale_with(selector)
    with pytest.raises(forms.Gate, match=DISABLED_GATE):
        await forms._observe_controls(browser, caller, site='execute', execute_target=TARGET)
    assert browser.reads == 1


async def test_header_button_as_the_action_target_keeps_the_gate():
    caller, browser = stale_with('#autofill')
    with pytest.raises(forms.Gate, match=DISABLED_GATE):
        await forms._observe_controls(browser, caller, site='execute',
                                      execute_target='#autofill')


@pytest.mark.parametrize('site', ['group-readback', 'execute-readback', ''])
async def test_other_sites_keep_the_exact_gate_for_the_header_button(site):
    caller, browser = stale_with('#autofill')
    with pytest.raises(forms.Gate, match=DISABLED_GATE):
        await forms._observe_controls(browser, caller, site=site, execute_target=TARGET)


async def test_execute_without_a_known_target_keeps_the_gate():
    caller, browser = stale_with('#autofill')
    with pytest.raises(forms.Gate, match=DISABLED_GATE):
        await forms._observe_controls(browser, caller, site='execute')


@pytest.mark.parametrize('change', [
    {'in_form': True}, {'form_selector': '#application'},
    {'native_form_owner_selector': '#application'}, {'submit': True},
    {'commits_application': True}, {'tag': 'input', 'type': 'checkbox'},
])
def test_exemption_needs_every_condition(change):
    page = live_page()
    header = by(page, '#autofill')
    observed = witness(page)['controls'][page['controls'].index(header)]
    assert forms._unrelated_header_button(page, header, observed, 'execute', TARGET)
    assert not forms._unrelated_header_button(
        page, {**header, **change}, observed, 'execute', TARGET)
    if 'tag' not in change:
        assert not forms._unrelated_header_button(
            page, header, {**observed, **change}, 'execute', TARGET)


def test_formless_structural_root_keeps_the_gate(monkeypatch):
    page = live_page()
    header = by(page, '#autofill')
    observed = witness(page)['controls'][page['controls'].index(header)]
    monkeypatch.setattr(forms, '_formless_root', lambda _page: {'root': True})
    assert not forms._unrelated_header_button(page, header, observed, 'execute', TARGET)


async def test_group_answer_then_execute_survives_the_header_change(row):
    """The live sequence: the header button changed after the caller's PAGE."""
    page = live_page()
    answered(row, page, ['Europe'])
    browser = LiveBrowser(page)
    stale = deepcopy(page)
    by(page, '#autofill')['disabled'] = True
    filled = {}
    await forms.execute(
        browser, stale,
        {'action': 'choose', 'selector': TARGET,
         'fact': forms._member_key(by(stale, TARGET))},
        facts={}, filled=filled, resume_path='Resume.pdf',
        company='example-co', jd_text='', resume_tex='', github='',
        pk=PK, job_url=URL)
    assert browser.clicks == [TARGET]
    assert filled == {TARGET: by(page, TARGET)['value']}


@pytest.mark.parametrize('selector', [TARGET, '#outside-submit', '#add-another'])
async def test_execute_refuses_when_a_guarded_control_changed(row, selector):
    page = live_page()
    answered(row, page, ['Europe'])
    browser = LiveBrowser(page)
    stale = deepcopy(page)
    by(page, selector)['disabled'] = True
    with pytest.raises(forms.Gate, match=DISABLED_GATE):
        await forms.execute(
            browser, stale,
            {'action': 'choose', 'selector': TARGET,
             'fact': forms._member_key(by(stale, TARGET))},
            facts={}, filled={}, resume_path='Resume.pdf',
            company='example-co', jd_text='', resume_tex='', github='',
            pk=PK, job_url=URL)
    assert browser.clicks == []


def test_submit_fixture_is_submission_classified():
    assert forms.is_submission(outside_submit())
    assert forms.is_submission(submit(form_action=URL))
    assert not forms.is_submission(autofill())
