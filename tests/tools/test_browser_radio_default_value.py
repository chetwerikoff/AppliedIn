"""Attribute-less radio readback with one final Submit decision.

The HTML default value "on" is shared by every attribute-less radio member.
A current checked control and its visible caption, not a retired exact grant,
are the evidence for a required choice. Protected answers are checked at the
single final pre-Submit boundary.
"""
import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as dom


def _page(checked):
    controls = [
        dict(selector=f'#gender-{i}', type='radio', tag='input',
             question='Gender', label=caption,
             name='systemfield_eeoc_gender', value='on', has_value=True,
             value_attr_present=False, required=True, checked=f'#gender-{i}' in checked,
             form_selector='#application', in_form=True, disabled=False)
        for i, caption in enumerate(
            ['Male', 'Female', 'Decline to self-identify'])
    ]
    controls.append(dict(selector='#resume', type='file', tag='input',
                         question='Resume', label='Resume', files=['synthetic.pdf'],
                         disabled=False, form_selector='#application',
                         in_form=True, required=True))
    return dict(url='https://example.test/application',
                controls=controls, inventory_verified=True, truncated=False,
                opaque_controls=[], unsupported_frames=[], shadow_roots=[])


def test_reader_reports_value_attribute_presence_for_inputs():
    assert "value_attr_present:e.hasAttribute('value')" in dom.ENTRY_PAGE
    assert "e.tagName === 'INPUT' ? {value_attr_present" in dom.ENTRY_PAGE


def test_current_radio_readback_uses_caption_not_shared_machine_value():
    page = _page({'#gender-2'})
    assert {c['value'] for c in page['controls'] if c['type'] == 'radio'} == {'on'}
    assert [c['label'] for c in page['controls'] if c.get('checked')] == [
        'Decline to self-identify']
    forms.check_form(page, {}, uploaded=True, attachment='synthetic.pdf')


def test_required_radio_without_current_checked_choice_fails_final_readback():
    with pytest.raises(forms.Gate, match='required native choice'):
        forms.check_form(_page(set()), {}, uploaded=True,
                         attachment='synthetic.pdf')


@pytest.mark.parametrize('caption', ['Male', 'Female'])
def test_affirmative_protected_choice_is_rejected_at_final_value_check(caption):
    field = {'question': 'Gender', 'label': caption, 'type': 'radio'}
    with pytest.raises(forms.Gate, match='Decline or leave unanswered'):
        forms.guarded_value(field, caption)


def test_protected_decline_is_not_refused_as_affirmative():
    field = {'question': 'Gender', 'label': 'Decline to self-identify',
             'type': 'radio'}
    assert forms.guarded_value(field, 'Decline to self-identify') == (
        'Decline to self-identify')
