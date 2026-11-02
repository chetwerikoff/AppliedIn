"""Issue #293: the reviewed, post-redesign literal Gate-site budget.

The count is intentionally a literal on the current code, not a moving
threshold or a self-calibrating assertion. Changes require explicit review.
"""
import ast
import inspect

from tools import browser_skill_apply as forms


# Gate and its known subclasses whose constructor is directly raised in
# browser_skill_apply.py. This is an AST count, not a regex over comments.
_GATE_CLASSES = frozenset({
    'Gate', 'PreSubmitGate', 'ControlGate', 'CaptchaGate',
    'CaptchaArtifactGate', 'NavigationProvenanceGate',
    'MaterialAssentGate', '_EntryNotReady',
})
_REVIEWED_GATE_SITES = 156


def test_literal_reviewed_gate_site_budget():
    module = ast.parse(inspect.getsource(forms))
    raised = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id in _GATE_CLASSES
    ]
    assert len(raised) == _REVIEWED_GATE_SITES


def test_only_one_substantive_final_check_and_no_grant_layer():
    module = ast.parse(inspect.getsource(forms))
    controllers = [
        node for node in module.body if isinstance(node, ast.ClassDef)
        and node.name == 'ApplicationIPCController'
    ]
    assert len(controllers) == 1
    decisions = [
        node for node in controllers[0].body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == 'check_pre_submit'
    ]
    assert len(decisions) == 1
    names = {node.name for node in module.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not names.intersection({
        'exact_approval', 'check_approvals', '_final_exact_grant',
        '_auto_grant_from_global', '_derive_group_member_grants',
        '_radio_group', '_checkbox_group', '_boolean_pair',
        '_greenhouse_phone_country_proven',
    })
    before_ipc = next(n for n in controllers[0].body
                      if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == 'before_ipc')
    submit_calls = [n for n in ast.walk(before_ipc)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'check_pre_submit']
    assert len(submit_calls) == 1
