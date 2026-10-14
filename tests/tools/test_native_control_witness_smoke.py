"""Offline CDP request policy for the manually invoked native witness smoke."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SPEC = spec_from_file_location(
    "native_control_witness_smoke_manual",
    Path(__file__).with_name("native_control_witness_smoke.py"))
assert SPEC is not None and SPEC.loader is not None
smoke = module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)

FIXTURE = "http://127.0.0.1:43117/fixture/native-control-witness"


class RecordingCDP(smoke.CDP):
    def __init__(self):
        super().__init__(-1, -1, FIXTURE)
        self.page_session = "page"
        self.background_sessions.update({"ui", "background"})
        self.browser_ui_sessions.add("ui")
        self.calls = []

    def call(self, method, params=None, session=None):
        self.calls.append((method, params, session))
        return {}


def record_request(cdp, session, url, *, kind="Script"):
    cdp._dispatch({"method": "Fetch.requestPaused", "sessionId": session,
                   "params": {"requestId": "synthetic-request",
                              "request": {"url": url, "method": "GET"},
                              "resourceType": kind}})
    cdp._dispatch({"method": "Network.requestWillBeSent", "sessionId": session,
                   "params": {"request": {"url": url, "method": "GET"},
                              "type": kind}})


@pytest.mark.parametrize("host", ["resources", "theme", "omnibox-popup.top-chrome"])
def test_browser_ui_known_chrome_hosts_are_recorded_without_failure(host):
    cdp = RecordingCDP()
    record_request(cdp, "page", FIXTURE, kind="Document")
    internal = f"chrome://{host}/x"
    record_request(cdp, "ui", internal)

    assert cdp.forbidden == []
    assert cdp.fetch == cdp.network == [
        ("page", FIXTURE, "GET", "Document"),
        ("ui", internal, "GET", "Script"),
    ]
    assert [method for method, _, _ in cdp.calls] == [
        "Fetch.continueRequest", "Fetch.continueRequest"]
    assert cdp.fixture_requests_verified()


@pytest.mark.parametrize(("session", "url"), [
    ("ui", "chrome://resources.example/x"),
    ("ui", "chrome-untrusted://resources/x"),
    ("ui", "chrome://other-host/x"),
    ("ui", "https://example.com"),
    ("ui", "chrome://[broken/x"),
    ("ui", "chrome://resources:not-a-port/x"),
    ("ui", None),
    ("page", "chrome://resources/x"),
    ("background", "chrome://resources/x"),
    ("worker", "chrome://resources/x"),
])
def test_untrusted_requests_remain_forbidden_even_when_chrome_like(session, url):
    cdp = RecordingCDP()
    record_request(cdp, "page", FIXTURE, kind="Document")
    record_request(cdp, session, url)

    assert len(cdp.forbidden) == 2  # Fetch refusal and Network observation
    assert cdp.fetch == cdp.network
    assert [method for method, _, _ in cdp.calls] == [
        "Fetch.continueRequest", "Fetch.failRequest"]
    assert not cdp.fixture_requests_verified()


@pytest.mark.parametrize("target", ["browser_ui", "background_page"])
def test_internal_targets_intercepted_without_page_navigation(target):
    cdp = RecordingCDP()
    cdp.page_session = None
    cdp.background_sessions.clear()
    cdp.browser_ui_sessions.clear()

    cdp._prepare("internal", target, True, "internal-target")

    assert [method for method, _, _ in cdp.calls] == [
        "Network.enable", "Fetch.enable", "Target.setAutoAttach",
        "Runtime.enable", "Runtime.runIfWaitingForDebugger"]
    assert cdp.page_session is None
    assert "internal" in cdp.prepared
    assert "internal" in cdp.background_sessions
    assert ("internal" in cdp.browser_ui_sessions) == (target == "browser_ui")
    assert "internal-target" in cdp.intercepted_targets


def test_unobserved_target_type_stays_fail_closed():
    cdp = RecordingCDP()
    with pytest.raises(smoke.SmokeFailure,
                       match="unverifiable child target type unexpected"):
        cdp._prepare("unknown", "unexpected", False, "unknown-target")
    assert not cdp.calls


def test_missing_fetch_interception_of_chrome_ui_request_cannot_pass():
    cdp = RecordingCDP()
    record_request(cdp, "page", FIXTURE, kind="Document")
    cdp._dispatch({"method": "Network.requestWillBeSent", "sessionId": "ui",
                   "params": {"request": {"url": "chrome://resources/x",
                                          "method": "GET"},
                              "type": "Stylesheet"}})

    assert not cdp.forbidden
    assert not cdp.fixture_requests_verified()
