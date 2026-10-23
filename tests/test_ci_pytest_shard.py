"""Offline coverage and fail-closed checks for the CI-only pytest lanes."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ci_pytest_shard import SHARD_COUNT, _shard_for_nodeid

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# This is the exact baseline main workflow concurrency stanza, not a parsed
# approximation: the PR/main cancellation behavior must not change.
BASELINE_CONCURRENCY = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event_name == 'pull_request' && 'pr' || format('{0}-{1}', github.run_id, github.run_attempt) }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)


def _pytest_process(*args: str) -> subprocess.CompletedProcess[str]:
    # Subprocess collection is offline; clear ambient pytest/xDist options so
    # the comparison uses the same unsharded starting population every time.
    env = os.environ.copy()
    for key in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_WORKER_COUNT",
    ):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _nodeids(process: subprocess.CompletedProcess[str]) -> list[str]:
    assert process.returncode == 0, (
        f"collect-only failed ({process.returncode}):\n"
        f"{process.stdout[-2000:]}\n{process.stderr[-2000:]}"
    )
    # -q --collect-only emits a complete nodeid per line, including [param IDs].
    # Ignore warnings and the collection totals rather than comparing counts.
    result = [
        line.strip()
        for line in process.stdout.splitlines()
        if ".py::" in line and not line.startswith((" ", "\t"))
    ]
    assert result, f"no collected node IDs in:\n{process.stdout[-2000:]}"
    assert len(result) == len(set(result)), "the same nodeid was collected more than once"
    return result


def test_sha256_assignments_include_distinct_parametrized_ids() -> None:
    nodeids = [
        "tests/test_sample.py::test_plain",
        "tests/test_sample.py::test_param[alpha]",
        "tests/test_sample.py::test_param[beta]",
        "tests/test_sample.py::test_param[space in id]",
        "tests/nested/test_other.py::TestCases::test_value[unicode-λ]",
    ]
    assert SHARD_COUNT == 3
    assert len(nodeids) == len(set(nodeids))
    for nodeid in nodeids:
        expected = int.from_bytes(hashlib.sha256(nodeid.encode("utf-8")).digest(), "big") % 3
        assert _shard_for_nodeid(nodeid) == expected
        assert _shard_for_nodeid(nodeid) == _shard_for_nodeid(nodeid)
    selected = [
        {nodeid for nodeid in nodeids if _shard_for_nodeid(nodeid) == shard}
        for shard in range(SHARD_COUNT)
    ]
    assert set.union(*selected) == set(nodeids)
    assert all(selected[i].isdisjoint(selected[j]) for i in range(3) for j in range(i + 1, 3))


def test_synthetic_parametrized_collection_partition(tmp_path: Path) -> None:
    specimen = tmp_path / "test_ci_partition_sample.py"
    specimen.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', range(60))\n"
        "def test_example(value):\n"
        "    assert value >= 0\n",
        encoding="utf-8",
    )
    full = set(_nodeids(_pytest_process("--collect-only", str(specimen))))
    assert len(full) == 60
    assert all("[" in nodeid and "]" in nodeid for nodeid in full)
    shards = []
    for shard in range(SHARD_COUNT):
        selected = set(
            _nodeids(
                _pytest_process(
                    "--collect-only", "-p", "ci_pytest_shard", f"--ci-shard={shard}",
                    str(specimen),
                )
            )
        )
        assert selected == {item for item in full if _shard_for_nodeid(item) == shard}
        shards.append(selected)
    assert set.union(*shards) == full
    assert all(shards[i].isdisjoint(shards[j]) for i in range(3) for j in range(i + 1, 3))


@pytest.mark.parametrize("selector", [None, "", "bogus", "1.0", "00", "-1", "3"])
def test_plugin_rejects_missing_or_invalid_selector(selector: str | None) -> None:
    args = ["-p", "ci_pytest_shard"]
    if selector is not None:
        args.append(f"--ci-shard={selector}")
    process = _pytest_process("--collect-only", *args, "tests/test_ci_pytest_shard.py")
    assert process.returncode != 0
    assert "CI shard must be exactly one of 0, 1, 2" in process.stdout + process.stderr


def test_plugin_rejects_empty_selection(tmp_path: Path) -> None:
    specimen = tmp_path / "test_ci_single.py"
    specimen.write_text("def test_only():\n    assert True\n", encoding="utf-8")
    unsharded = _nodeids(_pytest_process("--collect-only", str(specimen)))
    assert len(unsharded) == 1
    empty_shard = (_shard_for_nodeid(unsharded[0]) + 1) % SHARD_COUNT
    process = _pytest_process(
        "--collect-only", "-p", "ci_pytest_shard", f"--ci-shard={empty_shard}",
        str(specimen),
    )
    assert process.returncode != 0
    assert "selected no test items" in process.stdout + process.stderr


def test_full_offline_suite_collection_is_disjoint_and_complete() -> None:
    # A real suite collection (no tests executed), not a count-only inference.
    # With xdist the same filtering runs independently on each worker's items.
    full = set(_nodeids(_pytest_process("--collect-only", "tests")))
    assert len(full) > 1000  # catch accidental partial collection
    shards = [
        set(
            _nodeids(
                _pytest_process(
                    "--collect-only", "-p", "ci_pytest_shard",
                    f"--ci-shard={shard}", "tests",
                )
            )
        )
        for shard in range(SHARD_COUNT)
    ]
    assert all(shards)
    assert all(shards[i].isdisjoint(shards[j]) for i in range(3) for j in range(i + 1, 3))
    assert set.union(*shards) == full
    assert sum(len(part) for part in shards) == len(full)


def test_workflow_has_three_native_jobs_and_original_concurrency() -> None:
    # Native named jobs expose four separate needs results; a matrix aggregate
    # cannot establish the individual outcome of every expected lane.
    raw = WORKFLOW.read_text(encoding="utf-8")
    concurrency = re.search(r"(?m)^concurrency:\n(?:[ \t]+[^\n]*\n)+", raw)
    assert concurrency is not None
    assert concurrency.group(0) == BASELINE_CONCURRENCY

    workflow = yaml.safe_load(raw)
    assert workflow["name"] == "CI"
    assert SHARD_COUNT == 3
    jobs = workflow["jobs"]
    shard_names = [f"pytest-shard-{index}" for index in range(SHARD_COUNT)]
    assert set(jobs) == {*shard_names, "node", "checks"}

    for index, job_name in enumerate(shard_names):
        job = jobs[job_name]
        assert "strategy" not in job
        assert job["runs-on"] == "ubuntu-latest"
        assert job["timeout-minutes"] == 30
        assert job["steps"] == [
            {"uses": "actions/checkout@v4"},
            {"uses": "astral-sh/setup-uv@v6", "with": {"python-version": "3.12"}},
            {"run": "uv sync --locked"},
            {
                "run": (
                    "uv run --frozen python -m pytest -q -p ci_pytest_shard "
                    f"--ci-shard={index} -n auto --durations=25"
                )
            },
        ]

    # Retain the original Node job exactly; no separate Node test changes.
    assert jobs["node"] == {
        "runs-on": "ubuntu-latest",
        "timeout-minutes": 30,
        "steps": [
            {"uses": "actions/checkout@v4"},
            {"uses": "actions/setup-node@v4", "with": {"node-version": "24"}},
            {"run": "node --test tests/web/*.test.cjs tests/web/*.test.mjs"},
        ],
    }

    checks = jobs["checks"]
    assert checks["needs"] == [*shard_names, "node"]
    assert checks["if"] == "${{ always() }}"
    assert checks["runs-on"] == "ubuntu-latest"
    assert len(checks["steps"]) == 1
    lines = [line.strip() for line in checks["steps"][0]["run"].splitlines() if line.strip()]
    assert lines == [
        'test "${{ needs[\'pytest-shard-0\'].result == \'success\' }}" = "true"',
        'test "${{ needs[\'pytest-shard-1\'].result == \'success\' }}" = "true"',
        'test "${{ needs[\'pytest-shard-2\'].result == \'success\' }}" = "true"',
        'test "${{ needs.node.result == \'success\' }}" = "true"',
    ]
