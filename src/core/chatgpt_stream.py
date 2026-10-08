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
    _rebind_adk(litellm)


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
    model = kwargs["model"] if "model" in kwargs else (args[0] if args else None)
    if not isinstance(model, str) or not model.startswith("chatgpt/"):
        return False
    # Explicit streaming is the path that already works. Leave it alone.
    return kwargs.get("stream") is not True


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
        if not _chatgpt_nonstream(args, kwargs):
            return original(*args, **kwargs)
        forwarded = dict(kwargs)
        forwarded["stream"] = True
        stream = original(*args, **forwarded)
        return _assemble(_collect_sync(stream), _messages(args, kwargs))

    completion.__name__ = "completion"
    completion.__doc__ = original.__doc__
    return completion


def _wrap_acompletion(original: Any) -> Any:
    async def acompletion(*args: Any, **kwargs: Any) -> Any:
        if not _chatgpt_nonstream(args, kwargs):
            return await original(*args, **kwargs)
        forwarded = dict(kwargs)
        forwarded["stream"] = True
        stream = await original(*args, **forwarded)
        return _assemble(await _collect_async(stream), _messages(args, kwargs))

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
