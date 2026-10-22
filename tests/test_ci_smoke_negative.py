"""Disposable synthetic CI gate probe. Never merge this file."""


def test_synthetic_pytest_failure_for_ci_gate():
    assert False, "synthetic pytest failure: CI aggregate must fail closed"
