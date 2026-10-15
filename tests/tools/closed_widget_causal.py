"""Isolated in-memory guard removals: unchanged negatives must turn red.

Run with .venv/bin/python tests/tools/closed_widget_causal.py. No production
file is edited, and each mutation lives in one fresh child interpreter.
"""
import inspect
import json
from pathlib import Path
import subprocess
import sys

ROOT = str(Path(__file__).resolve().parents[2])
BASE = 'tests/tools/test_browser_closed_widgets.py::'
CASES = {
 'literal-caption': ('_execute_closed_combobox', "if option['label'] != answer:", 'if False:', 'test_unsafe_choice_never_clicks_an_option[scenario0]'),
 'literal-spacing': ('_execute_closed_combobox', "if option['label'] != answer:", 'if False:', 'test_unsafe_choice_never_clicks_an_option[scenario1]'),
 'literal-option-code': ('_execute_closed_combobox', "if not isinstance(codes, list) or any(code != answer for code in codes):", 'if False:', 'test_unsafe_choice_never_clicks_an_option[scenario2]'),
 'initial-admission': ('_observe_closed_admissions', "if phase != 'C0':", 'if False:', 'test_observation_itself_refuses_unproved_continuation[initial]'),
 'stable-anchors': ('_observe_closed_admissions', "prior['key'] != key or ", '', 'test_observation_itself_refuses_unproved_continuation[identity]'),
 'ordered-phase': ('_observe_closed_admissions', "phase != prior['phase']", 'False', 'test_observation_itself_refuses_unproved_continuation[phase]'),
 'no-legacy-downgrade': ('_observe_closed_admissions', 'if prior:', 'if False:', 'test_observation_itself_refuses_unproved_continuation[downgrade]'),
 'readback-selected': ('_check_closed_commit', "'selected_text', ", '', 'test_each_current_readback_is_literal_and_independent[selected_text]'),
 'readback-displayed': ('_check_closed_commit', "'displayed_text', ", '', 'test_each_current_readback_is_literal_and_independent[displayed_text]'),
 'readback-code': ('_check_closed_commit', "'selected_value', ", '', 'test_each_current_readback_is_literal_and_independent[selected_value]'),
 'readback-hidden': ('_check_closed_commit', ", 'hidden_value'))", '))', 'test_each_current_readback_is_literal_and_independent[hidden_value]'),
 'code-owned-receipt': ('_check_closed_commit', "or not isinstance(receipt, _ClosedReceipt)\n            or receipt._witnessed != tuple(sorted(receipt.items()))", '', 'test_forged_plain_receipt_is_not_code_witnessed'),
 'fingerprint': ('JS', "attr(e,'aria-haspopup') !== 'true' || ", '', 'test_unproven_graph_or_picker_retains_opacity[scenario0]'),
 'autocomplete': ('JS', "attr(e,'aria-autocomplete') !== 'list'", 'false', 'test_each_structural_admission_boundary[noAutocomplete]'),
 'display-branch': ('JS', '(singleBranch(n) || placeholderBranch(n))', 'true', 'test_each_structural_admission_boundary[displayQuery]'),
 'receiver-count': ('JS', 'hidden.length > 1 || ', '', 'test_unproven_graph_or_picker_retains_opacity[scenario1]'),
 'explicit-receiver-owner': ('JS', "if ((explicit && (!H || byId(explicit)!==H)) || (name && (!H || H.name!==name)))", 'if (false)', 'test_each_structural_admission_boundary[explicitOutside]'),
 'picker-nesting': ('JS', "wrapper.querySelectorAll('.iti').length || ", '', 'test_unproven_graph_or_picker_retains_opacity[scenario4]'),
 'picker-single-tel': ('JS', 'tels.length!==1 || ', '', 'test_each_structural_admission_boundary[multipleTel]'),
 'option-ancestry': ('JS', 'const unsafeOption = o => {', 'const unsafeOption = o => { return false;', 'test_unsafe_choice_never_clicks_an_option[scenario5]'),
 'receiver-required-before-open': ('_execute_closed_combobox', "or not answer or not target['combobox'].get('hidden_selector')", 'or not answer', 'test_unsafe_choice_never_clicks_an_option[scenario3]'),
 'consistent-popup-claims': ('JS', ' || claims.some(n=>n!==claims[0])', '', 'test_popup_relationship_itself_refuses_ambiguous_ownership[conflict]'),
 'exclusive-popup-owner': ('JS', 'if (others.some(c=> {', 'if (false && others.some(c=> {', 'test_popup_relationship_itself_refuses_ambiguous_ownership[shared]'),
 'same-form-popup': ('JS', '(nativeForm(list) && nativeForm(list)!==form) ||', '', 'test_popup_relationship_itself_refuses_ambiguous_ownership[foreign]'),
 'no-nested-popup': ('JS', " || list.querySelectorAll('[role=\"listbox\"]').length", '', 'test_popup_relationship_itself_refuses_ambiguous_ownership[nestedList]'),
 'unique-popup-id': ('JS', 'hits.length === 1 ? hits[0] : null', 'hits.length > 0 ? hits[0] : null', 'test_popup_relationship_itself_refuses_ambiguous_ownership[duplicate]'),
 'full-tel-readback': ('execute', "or phone.get('value') != write_value", '', 'test_full_phone_readback_mismatch_gates_after_only_tel_fill[False]'),
 'missing-admitted-control': ('_observe_closed_admissions', "if set(admissions) - {c['selector'] for c in page['controls']}:", 'if False:', 'test_observation_itself_refuses_unproved_continuation[missing]'),
 'non-submitting-picker': ('JS', " || button.type!=='button'", '', 'test_unproven_graph_or_picker_retains_opacity[scenario5]'),
 'nonempty-tel-readback': ('execute', " or phone.get('has_value') is not True", '', 'test_full_phone_readback_mismatch_gates_after_only_tel_fill[True]'),
 'nonempty-tel-presubmit': ('check_form', "if typ == 'tel' and expected is not None and (", 'if False and (', 'test_cleared_optional_phone_cannot_evade_presubmit'),
 'conflicting-option-attributes': ('_execute_closed_combobox', "if not isinstance(codes, list) or any(code != answer for code in codes):", 'if False:', 'test_every_present_option_attribute_must_be_approved[attrs0]'),
 'empty-data-option-attribute': ('_execute_closed_combobox', "if not isinstance(codes, list) or any(code != answer for code in codes):", 'if False:', 'test_every_present_option_attribute_must_be_approved[attrs1]'),
 'empty-value-option-attribute': ('_execute_closed_combobox', "if not isinstance(codes, list) or any(code != answer for code in codes):", 'if False:', 'test_every_present_option_attribute_must_be_approved[attrs2]'),
 'different-data-option-attribute': ('_execute_closed_combobox', "if not isinstance(codes, list) or any(code != answer for code in codes):", 'if False:', 'test_every_present_option_attribute_must_be_approved[attrs3]'),
 'direct-submit-c3-witness': ('apply', 'page, _ = await _observe_controls(session, page)', 'pass', 'test_direct_route_commit_requires_latest_fixed_witness[submit]'),
 'direct-continue-c3-witness': ('apply', 'page, _ = await _observe_controls(session, page)', 'pass', 'test_direct_route_commit_requires_latest_fixed_witness[click]'),
}


def disable(name):
    sys.path.insert(0, ROOT+'/src')
    from tools import browser_skill_apply as forms, browser_skill_dom as dom
    function, old, new, _ = CASES[name]
    if function == 'JS':
        assert old in dom.PAGE and old in forms._FIXED_CONTROL_WITNESS
        dom.PAGE = dom.PAGE.replace(old, new)
        forms._FIXED_CONTROL_WITNESS = forms._FIXED_CONTROL_WITNESS.replace(old, new)
    else:
        source = inspect.getsource(getattr(forms, function))
        assert old in source, (name, old)
        exec(compile(source.replace(old, new), '<disabled-'+name+'>', 'exec'), forms.__dict__)


if __name__ == '__main__':
    if len(sys.argv)>1:
        name=sys.argv[1]
        disable(name)
        import pytest
        raise SystemExit(pytest.main(['-q', '--tb=short', BASE+CASES[name][3]]))
    results=[]
    for name in CASES:
        run=subprocess.run([sys.executable, __file__, name], cwd=ROOT, capture_output=True, text=True)
        killed=run.returncode==1 and '1 failed' in run.stdout
        results.append({'guard':name,'negative_red_when_removed':killed})
        print(json.dumps(results[-1]), flush=True)
        if not killed:
            print(run.stdout[-4500:]+run.stderr[-1500:], flush=True)
    print(json.dumps({'causal_red':sum(x['negative_red_when_removed'] for x in results),'total':len(results)}))
    raise SystemExit(0 if all(x['negative_red_when_removed'] for x in results) else 1)
