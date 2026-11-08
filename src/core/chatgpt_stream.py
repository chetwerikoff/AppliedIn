"""Make ``chatgpt/`` models usable from non-streaming LiteLLM calls.

The ChatGPT subscription backend only puts the completion on the stream.
LiteLLM 1.92's non-streaming path raises ``APIConnectionError``
(``ChatgptException - Unknown items in responses API response: []``) inside
``transform_response``. The same call with ``stream=True`` returns the text,
including when ``response_format`` is ``json_object``. Relevance, the crawler,
field mapping, the writer, and ADK's ``LiteLlm`` all call without ``stream=True``,
so those calls are streamed here and handed back as a normal ``ModelResponse``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_MARKER = "_appliedin_chatgpt_stream"
_ORIGINAL = "_appliedin_original"

# ADK does not look up litellm.acompletion on each call. _ensure_litellm_imported
# copies these two names into google.adk.models.lite_llm once, and LiteLLMClient
# awaits that copy. Importing LiteLlm does not do the copy; the first generate
# does. Replacing the litellm attribute is enough when install() runs first.
_ADK_MODULE = "google.adk.models.lite_llm"
_ADK_NAMES = ("completion", "acompletion")


def install() -> None:
    """Wrap ``litellm.completion`` and ``litellm.acompletion``. Idempotent.

    Safe to call again: a function that already carries the marker is left in
    place, so a second call does not nest another collector. If ADK already
    copied the unwrapped functions into its module globals, those names are
    pointed at the wrappers too.
    """
    import litellm
    import litellm.main as main

    _install_binding(litellm, main, "completion", _wrap_completion)
    _install_binding(litellm, main, "acompletion", _wrap_acompletion)
    _guard_authenticator()
    _rebind_adk(litellm)


# A device code belongs on the owner's terminal, never in a worker log. The
# provider authenticator otherwise falls through to _login_device_code() when
# a cached token is absent or refresh fails (including in a main-thread call).
_INTERACTIVE_LOGIN = ContextVar("appliedin_chatgpt_interactive_login", default=False)
_AUTH_GUARD_MARKER = "_appliedin_noninteractive_auth"
_LOGIN_PROBLEM = "ChatGPT authentication required. Run ./appliedin login-chatgpt."


@contextmanager
def interactive_login():
    """Permit device-code login only for the explicit CLI command."""
    context = _INTERACTIVE_LOGIN.set(True)
    try:
        yield
    finally:
        _INTERACTIVE_LOGIN.reset(context)


def _report_login_required() -> None:
    # This is the existing dashboard's top-level LLM-error banner. It is safe
    # even if Redis is unavailable, and contains no exception or credential data.
    try:
        from core import flags

        flags.note_llm_error("ChatGPT authentication", _LOGIN_PROBLEM)
    except Exception:
        pass


def _noninteractive_access(auth: Any) -> str:
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    record = auth._read_auth_file() or {}
    token = record.get("access_token")
    if isinstance(token, str) and token and not auth._is_token_expired(record, token):
        return token
    refresh = record.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        # Refresh is noninteractive. A failed refresh must NOT fall through to
        # LiteLLM's human device-code procedure or its cooldown/wait loop.
        try:
            fresh = auth._refresh_tokens(refresh).get("access_token")
            if isinstance(fresh, str) and fresh:
                return fresh
        except Exception:
            pass
    _report_login_required()
    raise GetAccessTokenError(message=_LOGIN_PROBLEM, status_code=401)


def _guard_authenticator() -> None:
    from litellm.llms.chatgpt.authenticator import Authenticator

    original = Authenticator.get_access_token
    if getattr(original, _AUTH_GUARD_MARKER, False):
        return

    def get_access_token(self):
        if _INTERACTIVE_LOGIN.get():
            return original(self)
        return _noninteractive_access(self)

    setattr(get_access_token, _AUTH_GUARD_MARKER, True)
    Authenticator.get_access_token = get_access_token


def _chatgpt_model(args: tuple, kwargs: dict) -> str | None:
    model = kwargs["model"] if "model" in kwargs else (args[0] if args else None)
    return model if isinstance(model, str) and model.startswith("chatgpt/") else None


def _sanitize_auth_failure(exc: Exception, args: tuple, kwargs: dict) -> None:
    model = _chatgpt_model(args, kwargs)
    if model is None:
        return
    status = getattr(exc, "status_code", None)
    # A revoked server token may surface as a 401 or as token_expired wrapped
    # by LiteLLM. Do not carry the original response/body into any diagnostics.
    if status != 401 and "token_expired" not in str(exc).lower():
        return
    from litellm import AuthenticationError

    _report_login_required()
    raise AuthenticationError(
        message=_LOGIN_PROBLEM, llm_provider="chatgpt", model=model,
    ) from None


def _install_binding(public: Any, main: Any, name: str, factory: Any) -> None:
    current = getattr(public, name)
    if getattr(current, _MARKER, False):
        wrapped = current
    else:
        wrapped = factory(current)
        setattr(wrapped, _MARKER, True)
        setattr(wrapped, _ORIGINAL, current)
        setattr(public, name, wrapped)
    # litellm.acompletion calls the completion global inside litellm.main, not
    # the package attribute. Keep that binding on the same wrapper when it is
    # still the function we just wrapped. A monkeypatch of litellm.completion
    # alone must not overwrite the real main.completion.
    if getattr(main, name) is getattr(wrapped, _ORIGINAL, None):
        setattr(main, name, wrapped)


def _rebind_adk(litellm_module: Any) -> None:
    adk = sys.modules.get(_ADK_MODULE)
    if adk is None:
        return
    for name in _ADK_NAMES:
        wrapped = getattr(litellm_module, name)
        original = getattr(wrapped, _ORIGINAL, None)
        if original is not None and getattr(adk, name, None) is original:
            setattr(adk, name, wrapped)


def _chatgpt_nonstream(args: tuple, kwargs: dict) -> bool:
    # Explicit streaming already works; the authenticator guard still applies.
    return _chatgpt_model(args, kwargs) is not None and kwargs.get("stream") is not True


def _messages(args: tuple, kwargs: dict) -> Any:
    if "messages" in kwargs:
        return kwargs["messages"]
    if len(args) > 1:
        return args[1]
    return None


def _assemble(chunks: list, messages: Any) -> Any:
    import litellm

    return litellm.stream_chunk_builder(chunks, messages=messages)


def _wrap_completion(original: Any) -> Any:
    def completion(*args: Any, **kwargs: Any) -> Any:
        try:
            if not _chatgpt_nonstream(args, kwargs):
                return original(*args, **kwargs)
            forwarded = dict(kwargs)
            forwarded["stream"] = True
            stream = original(*args, **forwarded)
            return _assemble(_collect_sync(stream), _messages(args, kwargs))
        except Exception as exc:
            _sanitize_auth_failure(exc, args, kwargs)
            raise

    completion.__name__ = "completion"
    completion.__doc__ = original.__doc__
    return completion


def _wrap_acompletion(original: Any) -> Any:
    async def acompletion(*args: Any, **kwargs: Any) -> Any:
        try:
            if not _chatgpt_nonstream(args, kwargs):
                return await original(*args, **kwargs)
            forwarded = dict(kwargs)
            forwarded["stream"] = True
            stream = await original(*args, **forwarded)
            return _assemble(await _collect_async(stream), _messages(args, kwargs))
        except Exception as exc:
            _sanitize_auth_failure(exc, args, kwargs)
            raise

    acompletion.__name__ = "acompletion"
    acompletion.__doc__ = original.__doc__
    return acompletion


def _collect_sync(stream: Any) -> list:
    return list(stream)


async def _collect_async(stream: Any) -> list:
    # CustomStreamWrapper (what acompletion returns for stream=True) implements
    # both iterators. The async one is the path ADK already consumes.
    if hasattr(stream, "__anext__"):
        return [chunk async for chunk in stream]
    return list(stream)
