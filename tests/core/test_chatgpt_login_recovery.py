"""Offline regression coverage for the chatgpt/ login-recovery boundary.

No real ChatGPT auth, device flow, network, Redis, or daemon is used.
"""

import json

import httpx
import litellm
import pytest

from core import chatgpt_stream, llm_access


@pytest.fixture(autouse=True)
def _isolated_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("CHATGPT_AUTH_FILE", raising=False)
    monkeypatch.setattr(chatgpt_stream, "_report_login_required", lambda: None)
    chatgpt_stream.install()


def _cache(tmp_path, **record):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_revoked_cache_is_verified_once_moved_and_device_flow_starts(monkeypatch, tmp_path, capsys):
    secret = "synthetic-revoked-access-token"
    path = _cache(tmp_path, access_token=secret, expires_at=9999999999)
    calls = []

    def verify(token, account_id):
        calls.append(("verify", token, account_id))
        return False

    def device_flow():
        assert not path.exists(), "dead cache must be aside before device login"
        calls.append(("device",))
        return "synthetic-new-token"

    monkeypatch.setattr(llm_access, "_verify_cached_token", verify)
    monkeypatch.setattr(llm_access, "_fetch_access_token", device_flow)

    assert llm_access.main(["login"]) == 0
    assert calls == [("verify", secret, None), ("device",)]
    backups = list(tmp_path.glob("auth.json.recovery-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["access_token"] == secret
    out = capsys.readouterr().out
    assert "succeeded" in out.lower()
    assert secret not in out


def test_healthy_cached_token_verifies_without_device_flow(monkeypatch, tmp_path, capsys):
    _cache(tmp_path, access_token="synthetic-valid", expires_at=9999999999)
    calls = []
    monkeypatch.setattr(
        llm_access, "_verify_cached_token",
        lambda token, account: calls.append(token) or True)
    monkeypatch.setattr(
        llm_access, "_fetch_access_token",
        lambda: pytest.fail("healthy cached token must not trigger device flow"))
    assert llm_access.main(["login"]) == 0
    assert calls == ["synthetic-valid"]
    assert "succeeded" in capsys.readouterr().out


def test_force_skips_verification_and_triggers_fresh_device_flow(monkeypatch, tmp_path):
    path = _cache(tmp_path, access_token="synthetic-valid")
    monkeypatch.setattr(
        llm_access, "_verify_cached_token",
        lambda *args: pytest.fail("--force should not probe the old token"))
    triggered = []

    def device_flow():
        assert not path.exists()
        triggered.append(True)
        return "synthetic-replacement"

    monkeypatch.setattr(llm_access, "_fetch_access_token", device_flow)
    assert llm_access.main(["login", "--force"]) == 0
    assert triggered == [True]
    assert len(list(tmp_path.glob("auth.json.recovery-*"))) == 1


def test_old_device_cooldown_stub_does_not_block_owner_login(monkeypatch, tmp_path):
    path = _cache(tmp_path, device_code_requested_at=1730000000)
    monkeypatch.setattr(
        llm_access, "_verify_cached_token",
        lambda *args: pytest.fail("cooldown stub has no token to verify"))

    def device_flow():
        assert not path.exists()
        return "synthetic-new-token"

    monkeypatch.setattr(llm_access, "_fetch_access_token", device_flow)
    assert llm_access.main(["login"]) == 0
    assert len(list(tmp_path.glob("auth.json.recovery-*"))) == 1


def test_verification_is_one_minimal_authenticated_call(monkeypatch):
    requests = []

    def fake_get(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert llm_access._verify_cached_token("synthetic-bearer", None)
    assert len(requests) == 1
    url, kwargs = requests[0]
    assert url.endswith("/models")
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-bearer"
    assert kwargs["follow_redirects"] is False
    assert kwargs["params"] == {"client_version": "1.0.0"}
    assert kwargs["timeout"] == 15


@pytest.mark.parametrize("body,status", [
    ('{"error": "token_expired"}', 200),
    ('{"error": "unauthorized"}', 401),
])
def test_verification_rejects_revoked_server_token(monkeypatch, body, status):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(status, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert llm_access._verify_cached_token("synthetic-bearer", None) is False
    assert len(calls) == 1


def test_verification_outage_is_not_misreported_as_login_success(monkeypatch, tmp_path, capsys):
    path = _cache(tmp_path, access_token="synthetic-token")
    monkeypatch.setattr(
        llm_access, "_verify_cached_token", lambda *args: (_ for _ in ()).throw(
            httpx.ConnectError("synthetic connection failure")))
    monkeypatch.setattr(
        llm_access, "_fetch_access_token",
        lambda: pytest.fail("verification outage must not discard cached login"))
    assert llm_access.main(["login"]) == 1
    assert path.exists()
    output = capsys.readouterr().out
    assert "failed" in output.lower()
    assert "synthetic-token" not in output


def test_daemon_cannot_start_device_flow_even_in_main_thread(monkeypatch):
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    def forbidden(_self):
        pytest.fail("daemon unexpectedly started a device-code flow")

    monkeypatch.setattr(Authenticator, "_login_device_code", forbidden)
    monkeypatch.setattr(Authenticator, "_wait_for_access_token", lambda *_args: forbidden(None))
    with pytest.raises(GetAccessTokenError) as exc:
        Authenticator().get_access_token()
    assert "login-chatgpt" in str(exc.value)


def test_refresh_failure_cannot_fall_through_to_device_flow(monkeypatch, tmp_path):
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    _cache(tmp_path, refresh_token="synthetic-refresh")
    def fail_refresh(_self, _token):
        raise RuntimeError("synthetic refresh rejected")
    monkeypatch.setattr(Authenticator, "_refresh_tokens", fail_refresh)
    monkeypatch.setattr(
        Authenticator, "_login_device_code",
        lambda _self: pytest.fail("refresh error must not open device login"))
    with pytest.raises(GetAccessTokenError):
        Authenticator().get_access_token()


def test_revoked_model_call_fails_closed_with_safe_dashboard_banner(monkeypatch):
    seen = []

    def report():
        seen.append(chatgpt_stream._LOGIN_PROBLEM)

    monkeypatch.setattr(chatgpt_stream, "_report_login_required", report)
    def provider_call(*args, **kwargs):
        raise litellm.AuthenticationError(
            message="synthetic token_expired", llm_provider="chatgpt",
            model="chatgpt/gpt-5.4")

    wrapped = chatgpt_stream._wrap_completion(provider_call)
    with pytest.raises(litellm.AuthenticationError) as exc:
        wrapped(model="chatgpt/gpt-5.4", messages=[{"role": "user", "content": "test"}])
    assert "login-chatgpt" in str(exc.value)
    assert "synthetic token_expired" not in str(exc.value)
    assert seen == [chatgpt_stream._LOGIN_PROBLEM]


def test_only_explicit_login_context_permits_interactive_auth(monkeypatch):
    from litellm.llms.chatgpt.authenticator import Authenticator

    entered = []
    monkeypatch.setattr(
        Authenticator, "_login_device_code",
        lambda _self: entered.append(True) or {"access_token": "synthetic-fresh"})
    with chatgpt_stream.interactive_login():
        assert Authenticator().get_access_token() == "synthetic-fresh"
    assert entered == [True]


def test_daemon_model_boundary_fails_closed_and_records_banner(monkeypatch):
    """A real authenticator call through the completion wrapper must never prompt."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    recorded = []
    monkeypatch.setattr(
        Authenticator, "_login_device_code",
        lambda _self: pytest.fail("model call opened an interactive login"))
    monkeypatch.setattr(
        chatgpt_stream, "_report_login_required",
        lambda: recorded.append(("ChatGPT authentication", chatgpt_stream._LOGIN_PROBLEM)))

    def fake_provider(*_args, **_kwargs):
        # No network: this simulates where the real provider asks LiteLLM for auth.
        Authenticator().get_access_token()

    model_call = chatgpt_stream._wrap_completion(fake_provider)
    with pytest.raises(litellm.AuthenticationError) as exc:
        model_call(model="chatgpt/gpt-5.4", messages=[{"role": "user", "content": "test"}])
    assert "login-chatgpt" in str(exc.value)
    assert recorded
    assert all("login-chatgpt" in message for _, message in recorded)
