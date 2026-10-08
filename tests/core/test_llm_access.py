"""An OpenAI key and a ChatGPT subscription are different credentials.

Startup used to look only for OPENAI_API_KEY. That reports a false gap once
every configured model is ``chatgpt/`` (the token file is the credential), and
it would hide a real gap if a bare ``gpt-5-mini`` — LiteLLM's spelling of
OpenAI — were ignored. These pin both directions, and that some other
provider is LiteLLM's problem rather than ours.
"""

import pytest

from core.config import Settings
from core.llm_access import configured_models, main, missing_access

_CHATGPT = "chatgpt/gpt-6-luna"
_OPENAI = "openai/gpt-5-mini"


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch, tmp_path):
    """Importing settings loads the owner's .env, and this machine may already
    hold a ChatGPT token. Neither may decide whether a test sees a gap."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("CHATGPT_AUTH_FILE", raising=False)


def _settings(**overrides: str) -> Settings:
    return Settings(mode="local", **overrides)


def test_a_bare_browser_model_is_one_of_the_models_we_call():
    """The shipped browser model has no provider prefix. It still has to be
    checked: LiteLLM reads ``gpt-5-mini`` as OpenAI, so dropping it would let
    a subscription-only setup look complete while this one still needs a key."""
    models = configured_models(_settings())
    assert "gpt-5-mini" in models
    assert _OPENAI in models


def test_openai_key_present_clears_the_shipped_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert missing_access(_settings()) == []


def test_openai_key_missing_is_reported_for_the_shipped_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    problems = missing_access(_settings())
    assert problems == ["OPENAI_API_KEY is not set."]


def test_chatgpt_token_present_needs_no_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    (tmp_path / "auth.json").write_text("{}")
    settings = _settings(orchestrator_model=_CHATGPT, browser_model=_CHATGPT)
    assert missing_access(settings) == []


def test_chatgpt_token_missing_names_the_login_command(monkeypatch, tmp_path):
    """The message has to name the command. 'Token file not found' does not
    tell the owner what to run, and the file's location is an implementation
    detail of LiteLLM."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    settings = _settings(
        orchestrator_model=_CHATGPT,
        scorer_model=_CHATGPT,
        tailor_model=_CHATGPT,
        browser_model=_CHATGPT,
    )
    problems = missing_access(settings)
    assert problems == [
        "ChatGPT subscription is not signed in. Run ./appliedin login-chatgpt."]
    assert "OPENAI_API_KEY" not in problems[0]


def test_mixed_models_report_each_missing_credential_once(monkeypatch, tmp_path):
    """One key serves every OpenAI stage, one token file every chatgpt/ stage.
    Listing the gap once per model would read as several broken things."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    settings = _settings(
        orchestrator_model=_OPENAI,
        tailor_model=_CHATGPT,
        writer_model=_CHATGPT,
        browser_model="gpt-5-mini",
    )
    assert missing_access(settings) == [
        "OPENAI_API_KEY is not set.",
        "ChatGPT subscription is not signed in. Run ./appliedin login-chatgpt.",
    ]


def test_an_unknown_provider_is_left_for_litellm(monkeypatch, tmp_path):
    """Anthropic (and anything else) reports its own missing key at call time.
    Checking it here would mean a second list of env var names to keep true."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    settings = _settings(
        orchestrator_model="anthropic/claude-haiku-4-5",
        browser_model="anthropic/claude-haiku-4-5",
    )
    assert missing_access(settings) == []


def test_the_adk_override_is_checked_too(monkeypatch, tmp_path):
    """APPLIEDIN_ADK_MODEL replaces the model the ADK agents call, but the
    other call sites still use the settings. Both credentials are required."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("APPLIEDIN_ADK_MODEL", _CHATGPT)
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    problems = missing_access(_settings())
    assert any("login-chatgpt" in problem for problem in problems)
    assert "OPENAI_API_KEY" not in " ".join(problems)


def test_check_is_silent_when_access_is_configured(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert main(["check"]) == 0
    assert capsys.readouterr().out == ""


def test_check_exits_nonzero_and_prints_the_gap(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["check"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_login_reports_success_without_printing_the_token(monkeypatch, capsys):
    token = "secret-access-token-value"

    monkeypatch.setattr("core.llm_access._fetch_access_token", lambda: token)
    assert main(["login"]) == 0
    out = capsys.readouterr().out
    assert "succeeded" in out.lower()
    assert token not in out
