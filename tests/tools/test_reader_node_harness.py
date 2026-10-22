"""Differential checks run shipped JavaScript and independent witnesses offline.

The one-shot fixture processes are the oracle; the worker must not cache a
page, fixture, dependency, scenario, or a poisoned VM global.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import browser_skill_apply as forms
from tools import browser_skill_dom as production
from tests.tools import reader_node_harness as harness


def one_shot(fixture, spec, *scripts):
    path = harness.HERE / fixture
    return subprocess.check_output(
        ["node", str(path), json.dumps(spec), *scripts], text=True)


def reused(fixture, spec, *scripts):
    return harness.reader_output(fixture, spec, *scripts)


@pytest.mark.parametrize("spec", [
    {},
    {"phases": {"location": "C0"}},
    {"phases": {"location": "C1"}, "fullPhone": True},
    {"phases": {"location": "C2"}, "requiredProxy": True},
    {"requiredProxy": True, "fullPhone": True, "svgParts": "positive"},
    {"badGraph": True, "phases": {"school": "C1"}},
])
def test_closed_real_page_and_fixed_witness_one_shot_parity(spec):
    readers = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    expected = one_shot("closed_widget_dom.cjs", spec, *readers)
    actual = reused("closed_widget_dom.cjs", spec, *readers)
    assert json.loads(actual) == json.loads(expected)


@pytest.mark.parametrize("reader", [
    production.PAGE, production.ENTRY_PAGE, production.GREENHOUSE_PAGE,
    forms._FIXED_CONTROL_WITNESS,
], ids=["PAGE", "ENTRY_PAGE", "GREENHOUSE_PAGE", "FIXED"])
def test_entry_fixture_relative_lever_card_real_reader_parity(reader):
    spec = {"url": "https://careers.example-co.test/apply",
            "lever": {"kinds": ["radio", "checkbox"], "defect": "visible-span"}}
    assert json.loads(reused("entry_dom_fixture.js", spec, reader)) == json.loads(
        one_shot("entry_dom_fixture.js", spec, reader))


def test_original_mutated_restored_reader_and_poisoned_dom_isolation():
    spec = {"phases": {"location": "C1"}, "fullPhone": True}
    readers = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    original = reused("closed_widget_dom.cjs", spec, *readers)
    worker_pid = harness._client().process.pid
    # The real original page runs in the mutated expression, but the altered
    # selected bytes must be observed instead of a cached PAGE constant.
    changed_page = ('(()=>{globalThis.__poison="B"; document.__poison="B"; '
                    'global.__graphs.push("poison"); '
                    'const value=(' + readers[0] +
                    '); value.__changed="B"; return value;})()')
    mutated = reused("closed_widget_dom.cjs", spec, changed_page, readers[1])
    assert json.loads(mutated)["page"]["__changed"] == "B"
    assert "poison" in json.loads(mutated)["graphs"]
    restored = reused("closed_widget_dom.cjs", spec, *readers)
    assert restored == original
    assert harness._client().process.pid == worker_pid


def test_runtime_and_syntax_throw_match_checked_subprocess_and_recover():
    witness = forms._FIXED_CONTROL_WITNESS
    good = reused("closed_widget_dom.cjs", {}, production.PAGE, witness)
    worker_pid = harness._client().process.pid
    for script, marker in [
        ('(()=>{throw Error("SYNTHETIC_GUARD_THROW")})()', "SYNTHETIC_GUARD_THROW"),
        ("const =", "SyntaxError"),
    ]:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            reused("closed_widget_dom.cjs", {}, script, witness)
        err = caught.value
        assert err.returncode != 0
        assert marker in err.stderr
        assert not err.output or not err.output.endswith('"page":null}\n')
        assert harness._client().process.pid == worker_pid
        assert reused("closed_widget_dom.cjs", {}, production.PAGE, witness) == good


def test_fixture_console_output_is_not_protocol_output():
    spec = {"phases": {"location": "C0"}}
    source = '(console.log("fixture-log"), ' + production.PAGE + ')'
    scripts = (source, forms._FIXED_CONTROL_WITNESS)
    assert reused("closed_widget_dom.cjs", spec, *scripts) == one_shot(
        "closed_widget_dom.cjs", spec, *scripts)
    assert reused("closed_widget_dom.cjs", spec, production.PAGE,
                  forms._FIXED_CONTROL_WITNESS).startswith('{"page":')


def test_real_guard_removal_and_restoration_are_not_cached():
    # The exact native-form ownership predicate is exercised in both *real*
    # independent scripts; no fixture fake implements a replacement guard.
    guard = "nativeForm(ref)!==form || "
    scripts = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    assert all(script.count(guard) == 1 for script in scripts)
    spec = {"fullPhone": True, "countryErrorForeignOwner": True}
    good = reused("closed_widget_dom.cjs", spec, *scripts)
    changed = reused(
        "closed_widget_dom.cjs", spec,
        *(script.replace(guard, "") for script in scripts))
    assert json.loads(changed) != json.loads(good)
    assert reused("closed_widget_dom.cjs", spec, *scripts) == good



def test_reader_url_prototype_poison_cannot_change_next_clean_entry_request():
    # A host-derived RequestURL.prototype used to share the real URL.prototype
    # across VM requests; the fixture's Ashby branch exposed the contamination.
    spec = {"url": "https://jobs.ashbyhq.com/example-co/application"}
    expected = one_shot("entry_dom_fixture.js", spec, production.PAGE)
    assert json.loads(reused("entry_dom_fixture.js", spec, production.PAGE)) == json.loads(expected)
    worker_pid = harness._client().process.pid
    poisoned_page = (
        '(()=>{Object.defineProperty(Object.getPrototypeOf(URL.prototype),'
        '"hostname",{configurable:true,get(){return "poisoned.example-co.test"}});'
        'return (' + production.PAGE + ');})()'
    )
    reused("closed_widget_dom.cjs", {}, poisoned_page,
           forms._FIXED_CONTROL_WITNESS)
    assert harness._client().process.pid == worker_pid
    assert json.loads(reused("entry_dom_fixture.js", spec, production.PAGE)) == json.loads(expected)


def test_lever_commonjs_module_and_exports_prototypes_are_request_local(tmp_path):
    fixture = tmp_path / "entry_dom_fixture.js"
    dependency_file = tmp_path / "lever_card_fixture.cjs"
    fixture.write_text((harness.HERE / fixture.name).read_text())
    dependency = (harness.HERE / dependency_file.name).read_text()
    spec = {"url": "https://careers.example-co.test/apply", "lever": {}}
    dependency_file.write_text(
        'Object.getPrototypeOf(module).__leaked = true;\n'
        'Object.getPrototypeOf(exports).__leaked = true;\n' + dependency)
    assert json.loads(reused(fixture, spec, production.PAGE))["controls"]
    worker_pid = harness._client().process.pid

    # The independent Node invocation begins with clean CommonJS prototypes.
    # The resident child must agree after the deliberately poisoned dependency.
    dependency_file.write_text(
        'if (Object.getPrototypeOf(module).__leaked || '
        'Object.getPrototypeOf(exports).__leaked) '
        'throw Error("SHARED MODULE PROTOTYPE");\n' + dependency)
    assert reused(fixture, spec, production.PAGE) == one_shot(
        fixture, spec, production.PAGE)
    dependency_file.write_text(dependency)
    assert reused(fixture, spec, production.PAGE) == one_shot(
        fixture, spec, production.PAGE)
    assert harness._client().process.pid == worker_pid


@pytest.mark.parametrize("reader", [
    forms.GREENHOUSE_PAGE, forms._GREENHOUSE_FIXED_CONTROL_WITNESS,
], ids=["GREENHOUSE_PAGE", "GREENHOUSE_FIXED_CONTROL_WITNESS"])
def test_greenhouse_claim_token_pre_read_failure_matches_one_shot_and_recovers(reader):
    # This is the same selected real-reader guard as the retained direct-Node
    # negative, with the concrete synthetic claim-bearing response token.
    guard = "if (greenhouse.response_controls) return {url:location.href, greenhouse};"
    assert reader.count(guard) == 1
    spec = {
        "frames": ["R"], "visible": True, "badge": True,
        "url": "https://job-boards.greenhouse.io/example-co/jobs/123",
        "tokens": ["g-recaptcha-response"], "textarea": False, "claim": True,
    }
    good = reused("entry_dom_fixture.js", spec, reader)
    assert json.loads(good) == json.loads(one_shot("entry_dom_fixture.js", spec, reader))
    worker_pid = harness._client().process.pid
    unguarded = reader.replace(guard, "")
    command = ["node", str(harness.HERE / "entry_dom_fixture.js"),
               json.dumps(spec), unguarded]
    with pytest.raises(subprocess.CalledProcessError) as original:
        subprocess.run(command, capture_output=True, text=True, check=True)
    with pytest.raises(subprocess.CalledProcessError) as resident:
        reused("entry_dom_fixture.js", spec, unguarded)
    for caught in (original, resident):
        assert caught.value.returncode != 0
        assert "PROTECTED TOKEN VALUE READ" in caught.value.stderr
        assert not caught.value.output  # No JSON success from a failed read.
    assert harness._client().process.pid == worker_pid
    assert reused("entry_dom_fixture.js", spec, reader) == good


def test_closed_fixture_bytes_are_reread_between_requests(tmp_path):
    fixture = tmp_path / "closed_widget_dom.cjs"
    original = (harness.HERE / fixture.name).read_text()
    fixture.write_text(original)
    spec = {"phases": {"location": "C0"}}
    scripts = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    base = harness.reader_output(fixture, spec, *scripts)
    fixture.write_text('console.log("modified-closed-fixture");\n' + original)
    assert harness.reader_output(fixture, spec, *scripts).startswith(
        "modified-closed-fixture\n")
    fixture.write_text(original)
    assert harness.reader_output(fixture, spec, *scripts) == base


def test_read_current_fixture_and_relative_dependency_bytes(tmp_path):
    fixture = tmp_path / "entry_dom_fixture.js"
    dep = tmp_path / "lever_card_fixture.cjs"
    original = (harness.HERE / fixture.name).read_text()
    dependency = (harness.HERE / dep.name).read_text()
    fixture.write_text(original)
    dep.write_text(dependency)
    spec = {"url": "https://careers.example-co.test/apply", "lever": {}}
    baseline = harness.reader_output(fixture, spec, production.PAGE)
    assert json.loads(baseline)["controls"]
    fixture.write_text('console.log("fresh-fixture");\n' + original)
    assert harness.reader_output(fixture, spec, production.PAGE).startswith(
        "fresh-fixture\n")
    fixture.write_text(original)
    assert harness.reader_output(fixture, spec, production.PAGE) == baseline
    dep.write_text('console.log("fresh-dependency");\n' + dependency)
    assert harness.reader_output(fixture, spec, production.PAGE).startswith(
        "fresh-dependency\n")
    dep.write_text(dependency)
    assert harness.reader_output(fixture, spec, production.PAGE) == baseline


@pytest.mark.parametrize("response", [
    'process.stdout.write(\'{"id":1\');',
    'process.stdout.write("not-json\\n");',
    'process.stdout.write(\'{"id":2,"ok":true,"stdout":"","stderr":""}\\n\');',
])
def test_partial_malformed_and_mismatched_framing_reaped(tmp_path, response):
    script = tmp_path / "broken_worker.cjs"
    script.write_text(
        "require('node:readline').createInterface({input:process.stdin})"
        ".once('line',()=>{" + response + "process.exit(0);});")
    worker = harness._Worker(script)
    try:
        with pytest.raises(RuntimeError):
            worker.request(harness.HERE / "closed_widget_dom.cjs",
                           ["{}", "1", "2"], 2)
    finally:
        worker.close()
    assert worker.process.poll() is not None


def test_bounded_timeout_reaps_and_next_request_uses_fresh_child(
        tmp_path, monkeypatch):
    script = tmp_path / "silent_worker.cjs"
    script.write_text("process.stdin.resume();setInterval(()=>{},1000);")
    harness._cleanup()
    original = harness._Worker
    with monkeypatch.context() as patch:
        patch.setattr(harness, "_Worker", lambda: original(script))
        with pytest.raises(subprocess.TimeoutExpired):
            harness.reader_output("closed_widget_dom.cjs", {},
                                  production.PAGE, forms._FIXED_CONTROL_WITNESS,
                                  timeout=0.1)
        assert harness._CLIENT is None
    result = reused("closed_widget_dom.cjs", {}, production.PAGE,
                    forms._FIXED_CONTROL_WITNESS)
    assert json.loads(result)["page"]
    harness._cleanup()


def test_killed_child_is_not_reused_and_subsequent_call_succeeds():
    spec = {}
    scripts = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    expected = reused("closed_widget_dom.cjs", spec, *scripts)
    old = harness._client().process
    old.kill()
    old.wait()
    assert reused("closed_widget_dom.cjs", spec, *scripts) == expected
    assert harness._client().process.pid != old.pid


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork requires POSIX")
def test_forked_interpreter_has_distinct_child_and_no_parent_pipe_reuse():
    scripts = (production.PAGE, forms._FIXED_CONTROL_WITNESS)
    reused("closed_widget_dom.cjs", {}, *scripts)
    original_pid = harness._client().process.pid
    rd, wr = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(rd)
        try:
            reused("closed_widget_dom.cjs", {}, *scripts)
            worker_pid = harness._client().process.pid
            os.write(wr, str(worker_pid).encode())
        finally:
            os.close(wr)
            os._exit(0)
    os.close(wr)
    try:
        other_pid = int(os.read(rd, 64))
        _, status = os.waitpid(child, 0)
    finally:
        os.close(rd)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert other_pid != original_pid
    assert harness._client().process.pid == original_pid
