"""What a fresh clone gets, and that the environment can change it.

These pin the SHIPPED defaults — the experience of someone who clones the repo
and runs it — so a change to what the product does out of the box has to be
deliberate rather than incidental.
"""

from core.config import Settings


def test_all_litellm_requests_have_a_finite_provider_budget():
    """Use LiteLLM's own resolver: merely setting an unused attribute is no cap.

    Five LiteLLM attempts, each with up to three OpenAI SDK attempts, allow
    1800 seconds in HTTP requests. Shared throttling and backoff are additional.
    """
    import litellm
    import openai
    from litellm.litellm_core_utils.request_timeout_resolver import get_configured_request_timeout

    assert get_configured_request_timeout() == 120
    assert litellm.num_retries == 4
    assert openai.DEFAULT_MAX_RETRIES == 2


def test_shipped_defaults():
    s = Settings()
    assert s.mode == "local"                    # runs on your machine, not a cloud
    assert s.apply_engine == "chrome"           # the owner's own browser, not a bot-shaped one
    assert s.chrome_model == "claude-sonnet-5"  # the hands; orchestration stays on OpenAI
    assert s.apply_mode == "gated"              # never applies to anything unasked
    assert s.litellm_model == "openai/gpt-5-mini"


def test_no_headless_browser_setting_survives():
    """The headless engines are gone: an application goes out through the owner's
    real Chrome or not at all. A `browser_headless` knob would imply a driven
    browser is still an option, and it is not."""
    assert not hasattr(Settings(), "browser_headless")


def test_the_environment_overrides_a_default(monkeypatch):
    monkeypatch.setenv("APPLIEDIN_CHROME_MODEL", "claude-opus-5")
    s = Settings()
    assert s.chrome_model == "claude-opus-5"


def test_gated_is_the_default_so_nothing_applies_unasked():
    """The one default that must never drift: applying is opt-in."""
    assert Settings().apply_mode == "gated"
