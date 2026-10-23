"""Opt-in, stable partition of pytest's fully collected node IDs for CI only."""

from __future__ import annotations

import hashlib

import pytest

SHARD_COUNT = 3
_VALID_SHARDS = {"0", "1", "2"}


def _shard_for_nodeid(nodeid: str) -> int:
    """Hash the full ID, including parametrization, independently of Python hash seeds."""
    return int.from_bytes(hashlib.sha256(nodeid.encode("utf-8")).digest(), "big") % SHARD_COUNT


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ci-shard",
        dest="ci_shard",
        default=None,
        help="Required when the ci_pytest_shard plugin is loaded: 0, 1, or 2.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("ci_shard") not in _VALID_SHARDS:
        raise pytest.UsageError("CI shard must be exactly one of 0, 1, 2 (--ci-shard=N)")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = int(config.getoption("ci_shard"))
    selected = [item for item in items if _shard_for_nodeid(item.nodeid) == shard]
    if not selected:
        raise pytest.UsageError(f"CI shard {shard} selected no test items")

    deselected = [item for item in items if _shard_for_nodeid(item.nodeid) != shard]
    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)
