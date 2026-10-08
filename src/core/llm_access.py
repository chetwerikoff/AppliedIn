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

import os
import sys
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


def _fetch_access_token() -> str:
    from litellm.llms.chatgpt.authenticator import Authenticator

    # Prints the device code itself and blocks until it is approved. The return
    # value is the access token; callers must not print it.
    return Authenticator().get_access_token()


def _login() -> int:
    try:
        token = _fetch_access_token()
    except Exception as exc:
        print(f"ChatGPT login failed: {exc}")
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
    print("usage: python -m core.llm_access check|login", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
