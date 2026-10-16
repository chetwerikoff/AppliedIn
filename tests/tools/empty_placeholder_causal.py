"""Run unchanged negatives with their guard removed only in a child interpreter.

Nothing edits production files; a red child is the expected causal evidence.
"""
import inspect
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TEST = 'tests/tools/test_browser_skill_empty_placeholder.py::'
CASES = {
    'fallback': ("visible(exactNodes.display) || (() => {", "visible(exactNodes.display) || (false && (() => {", 'test_greenhouse_empty_zero_height_placeholder_visible_v_passes'),
    'box': ('!visible(V) || ', '', 'test_greenhouse_empty_zero_height_placeholder_zero_size_v_gates'),
    'text': (" || clean(display.innerText) !== ''", '', 'test_greenhouse_nonempty_zero_height_placeholder_gates'),
    'label': (' || !visible(exactNodes.label)', '', 'test_greenhouse_non_strict_label_gates'),
    'geometry': ('rects.some(r=>r.width > 0 && r.height === 0) && rects.every(r=>r.height === 0)', 'true', 'test_greenhouse_positive_height_opacity_zero_placeholder_gates'),
    'positive-width': ('r.width > 0 && r.height === 0', 'r.height === 0', 'test_placeholder_geometry_is_exact_and_complete[rects1]'),
    'all-rects': (' && rects.every(r=>r.height === 0)', '', 'test_placeholder_geometry_is_exact_and_complete[rects2]'),
    'c2-readback': (None, None, 'test_greenhouse_zero_height_single_value_gates'),
}


def disable(name):
    sys.path.insert(0, str(ROOT / 'src'))
    from tools import browser_skill_apply as forms
    from tools.browser_skill_dom import greenhouse_reader, entry_reader
    old, new, _ = CASES[name]
    if name == 'c2-readback':
        source = inspect.getsource(forms._require_graph_visibility)
        source = source.replace("raise Gate('This control is not visibly editable.')", 'pass')
        exec(compile(source, '<removed-readback-guard>', 'exec'), forms.__dict__)
    else:
        assert old in forms._FIXED_CONTROL_WITNESS
        forms._FIXED_CONTROL_WITNESS = forms._FIXED_CONTROL_WITNESS.replace(old, new)
        if name == 'fallback':
            forms._FIXED_CONTROL_WITNESS = forms._FIXED_CONTROL_WITNESS.replace('})())} : {}),', '})()))} : {}),')
        forms._GREENHOUSE_FIXED_CONTROL_WITNESS = greenhouse_reader(forms._FIXED_CONTROL_WITNESS)
        forms._ENTRY_FIXED_CONTROL_WITNESS = entry_reader(forms._FIXED_CONTROL_WITNESS)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        name = sys.argv[1]
        disable(name)
        import pytest
        raise SystemExit(pytest.main(['-q', '--tb=short', TEST + CASES[name][2]]))
    killed = []
    for name in CASES:
        result = subprocess.run([sys.executable, __file__, name], cwd=ROOT, text=True, capture_output=True)
        red = result.returncode == 1 and ' failed' in result.stdout and 'SyntaxError' not in result.stdout + result.stderr
        print(f'{name}: {"RED (guard removed)" if red else "NOT PROVEN"}', flush=True)
        if not red:
            print(result.stdout + result.stderr, flush=True)
        killed.append(red)
    raise SystemExit(0 if all(killed) else 1)
