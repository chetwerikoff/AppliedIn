"""Issue #293: native checkbox preparation and final current-state checks.

Prior per-member approval/derived-grant tests tested a removed feature. These
synthetic tests exercise the remaining native-choice readback and PDF boundary.
"""
import pytest

from tools import browser_skill_apply as forms


URL = 'https://employer.test/job/1'


def _box(selector, caption, *, question='Preferred locations',
         checked=False, required=True, form='#application'):
    return {
        'selector': selector, 'tag': 'input', 'type': 'checkbox',
        'label': caption, 'question': question, 'checked': checked,
        'required': required, 'disabled': False, 'in_form': True,
        'form_selector': form, 'form_action': URL,
    }


def _page(*choices, attachment='Test User.pdf'):
    return {
        'url': URL, 'title': 'Synthetic role', 'inventory_verified': True,
        'truncated': False, 'opaque_controls': False,
        'unsupported_frames': False, 'shadow_roots': False,
        'controls': [
            {'selector': '#resume', 'tag': 'input', 'type': 'file',
             'label': 'Resume', 'question': 'Resume', 'required': True,
             'files': [attachment] if attachment else [],
             'in_form': True, 'form_selector': '#application',
             'form_action': URL},
            *choices,
        ],
    }


@pytest.mark.parametrize('selected', [(), ('North',), ('South',),
                                      ('North', 'South')])
def test_required_native_checkbox_group_uses_current_checked_readback(selected):
    page = _page(_box('#north', 'North', checked='North' in selected),
                 _box('#south', 'South', checked='South' in selected))
    if selected:
        forms.check_form(page, {'#north': 'stale writer value'}, True,
                         'Test User.pdf')
    else:
        with pytest.raises(forms.Gate, match='required native choice'):
            forms.check_form(page, {'#north': 'North'}, True,
                             'Test User.pdf')


def test_required_groups_on_different_forms_do_not_launder_each_other():
    page = _page(_box('#north', 'North', checked=True, form='#application'),
                 _box('#south', 'South', checked=False, form='#other'))
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(page, {}, True, 'Test User.pdf')


def test_optional_native_checkbox_can_remain_unselected():
    page = _page(_box('#marketing', 'Newsletter', question='Marketing',
                      checked=False, required=False))
    forms.check_form(page, {}, True, 'Test User.pdf')


def test_required_checkbox_selection_does_not_prove_pdf_attachment():
    page = _page(_box('#north', 'North', checked=True), attachment='')
    with pytest.raises(forms.Gate, match='Exactly one current résumé'):
        forms.check_form(page, {}, True, 'Test User.pdf')
    page['controls'][0]['files'] = ['different.pdf']
    with pytest.raises(forms.Gate, match='Exactly one current résumé'):
        forms.check_form(page, {}, True, 'Test User.pdf')


def test_other_attached_file_fails_final_pdf_inventory():
    page = _page(_box('#north', 'North', checked=True))
    page['controls'].append({
        'selector': '#extra', 'type': 'file', 'tag': 'input',
        'label': 'Additional file', 'files': ['extra.pdf'],
    })
    with pytest.raises(forms.Gate, match='Exactly one current résumé'):
        forms.check_form(page, {}, True, 'Test User.pdf')


@pytest.mark.asyncio
async def test_ordinary_native_choices_write_without_per_member_grants():
    page = _page(_box('#north', 'North'), _box('#south', 'South'))

    class Session:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {}

    session = Session()
    filled = {}
    for selector, value in (('#north', 'North'), ('#south', 'South')):
        await forms.execute(
            session, page, {'action': 'choose', 'selector': selector,
                            'value': value},
            facts={}, filled=filled, resume_path='synthetic.pdf',
            company='example-co', jd_text='Synthetic role',
            resume_tex='', github='', pk='example-co#1', job_url=URL)
    assert session.calls == [
        (('click', '#north'), {}), (('click', '#south'), {})]
    assert filled == {'#north': 'North', '#south': 'South'}


def test_disabled_checked_member_does_not_satisfy_required_native_choice():
    page = _page(_box('#north', 'North', checked=False),
                 _box('#south', 'South', checked=True))
    page['controls'][-1]['disabled'] = True
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(page, {}, True, 'Test User.pdf')


def test_disabled_resume_input_is_not_a_staged_pdf_attachment():
    page = _page(_box('#north', 'North', checked=True))
    page['controls'][0]['disabled'] = True
    with pytest.raises(forms.Gate, match='Exactly one current résumé'):
        forms.check_form(page, {}, True, 'Test User.pdf')
