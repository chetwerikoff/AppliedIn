"""Whether the configured models have a credential they can actually use.

Orchestration used to refuse to start unless ``OPENAI_API_KEY`` was set. That
is the right gate for the shipped default (``openai/gpt-5-mini``, and the bare
``gpt-5-mini`` browser model, which LiteLLM also routes to OpenAI), and the
wrong one once a stage is pointed at ``chatgpt/``: that provider authenticates
with a ChatGPT subscription token, not the API key. Demanding the key then
blocks a setup that does not need one.

Other providers are left alone. LiteLLM already reports a missing key when the
call is made, and a list of env vars maintained here would drift from it.

The provider is read off the model string. Do not ask LiteLLM to classify a
``chatgpt/`` model: resolving that provider runs the device-code login.
"""

from __future__ import annotations

import json
import os
import sys
from uuid import uuid4
from pathlib import Path

from core.config import Settings, get_settings

# agent_model("") is the applier's field mapper, a real completion() call.
# chrome_model is deliberately absent: it is the Claude CLI's model, not a
# LiteLLM provider string, and a bare name would be misread as OpenAI.
_AGENTS = ("", "scorer", "tailor", "critic", "relevance", "writer")

_OPENAI_PROBLEM = "OPENAI_API_KEY is not set."
_CHATGPT_PROBLEM = (
    "ChatGPT subscription is not signed in. Run ./appliedin login-chatgpt."
)


def configured_models(settings: Settings) -> list[str]:
    """Every distinct model string the app will send to LiteLLM.

    ``browser_model`` is included even though the Chrome apply engine drives
    Claude rather than this string: it is still the model ``apply()`` is given
    (``agent/run.py``, ``agent/graph.py``), and the shipped value is the bare
    name ``gpt-5-mini``. LiteLLM treats an unprefixed model as OpenAI, so a
    subscription-only setup has to point this one at ``chatgpt/`` too or the
    check keeps asking for an API key.
    """
    names = [settings.agent_model(agent) for agent in _AGENTS]
    names.append(settings.litellm_model)
    names.append(settings.browser_model)
    # A blanket override for the ADK agents. The per-stage settings still apply
    # everywhere else, so both have to be covered.
    adk = os.environ.get("APPLIEDIN_ADK_MODEL", "").strip()
    if adk:
        names.append(adk)
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _provider(model: str) -> str:
    """The LiteLLM provider. No slash means OpenAI, matching ``get_llm_provider``
    for the shipped bare ``gpt-5-mini``."""
    provider, sep, _rest = model.partition("/")
    if not sep:
        return "openai"
    return provider.lower()


def chatgpt_token_path() -> Path:
    """Same path LiteLLM's ChatGPT authenticator reads.

    ``CHATGPT_AUTH_FILE`` is a file name inside ``CHATGPT_TOKEN_DIR``, not a
    full path of its own. Defaults: ``~/.config/litellm/chatgpt/auth.json``.
    """
    directory = os.environ.get("CHATGPT_TOKEN_DIR") or os.path.expanduser(
        "~/.config/litellm/chatgpt")
    name = os.environ.get("CHATGPT_AUTH_FILE") or "auth.json"
    return Path(os.path.join(directory, name))


def missing_access(settings: Settings) -> list[str]:
    """Human-readable gaps, one per missing credential, in model order.

    A dozen stages on the same provider are one problem: the owner fixes a
    credential, not a list of model names.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for model in configured_models(settings):
        provider = _provider(model)
        if provider == "chatgpt":
            problem = "" if chatgpt_token_path().is_file() else _CHATGPT_PROBLEM
        elif provider == "openai":
            problem = "" if os.environ.get("OPENAI_API_KEY", "").strip() else _OPENAI_PROBLEM
        else:
            continue
        if problem and problem not in seen:
            seen.add(problem)
            problems.append(problem)
    return problems


def _cached_auth() -> dict:
    """Read the local cache without ever exposing its contents to diagnostics."""
    try:
        with chatgpt_token_path().open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _move_cache_aside() -> None:
    """Preserve the private credential file while removing it from LiteLLM's path."""
    path = chatgpt_token_path()
    if path.exists() or path.is_symlink():
        path.replace(path.with_name(f"{path.name}.recovery-{uuid4().hex}"))


def _verify_cached_token(token: str, account_id: str | None) -> bool:
    """One lightweight authenticated Codex API request; never infer health from JWT expiry.

    A network/endpoint failure is not evidence of revocation and must not
    discard credentials. No response body or authentication header is logged.
    """
    import httpx
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import (
        extract_chatgpt_account_id,
        get_chatgpt_default_headers,
    )

    headers = get_chatgpt_default_headers(
        token, account_id or extract_chatgpt_account_id(token))
    headers["accept"] = "application/json"
    response = httpx.get(
        Authenticator().get_api_base().rstrip("/") + "/models",
        headers=headers, timeout=15, follow_redirects=False,
    )
    # The backend sometimes supplies token_expired in a structured response
    # instead of using an HTTP 401. Never print either response.
    if response.status_code == 401 or "token_expired" in response.text.lower():
        return False
    response.raise_for_status()
    return True


def _fetch_access_token() -> str:
    from litellm.llms.chatgpt.authenticator import Authenticator
    from core.chatgpt_stream import interactive_login

    # Only this explicit owner command is allowed to start a device-code flow.
    # Every daemon/provider call uses the noninteractive Authenticator guard.
    with interactive_login():
        return Authenticator().get_access_token()


def _login(*, force: bool = False) -> int:
    try:
        data = _cached_auth()
        token = data.get("access_token")
        if force:
            _move_cache_aside()
        elif isinstance(token, str) and token:
            account_id = data.get("account_id")
            if _verify_cached_token(
                token, account_id if isinstance(account_id, str) else None,
            ):
                print("ChatGPT login succeeded.")
                return 0
            # The server rejected a still-cached token. Keep a private backup
            # but make the interactive Authenticator ignore the dead record.
            _move_cache_aside()
        elif (data.get("device_code_requested_at") is not None
              and not data.get("access_token") and not data.get("refresh_token")):
            # An older daemon may have left only a device-code cooldown stub.
            # Otherwise LiteLLM waits five minutes without displaying a code.
            _move_cache_aside()

        token = _fetch_access_token()
    except Exception:
        # HTTP exceptions may carry request headers or server response data.
        # Never stringify them, and never print the cached token or its path.
        print("ChatGPT login failed. Check connectivity and retry ./appliedin login-chatgpt.")
        return 1
    if not token:
        print("ChatGPT login failed.")
        return 1
    print("ChatGPT login succeeded.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["check"]:
        problems = missing_access(get_settings())
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    if args == ["login"]:
        return _login()
    if args == ["login", "--force"]:
        return _login(force=True)
    print("usage: python -m core.llm_access check|login [--force]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
