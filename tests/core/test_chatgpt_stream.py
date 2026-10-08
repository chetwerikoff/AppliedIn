"""chatgpt/ models only return text on the stream.

A non-streaming completion raises inside LiteLLM's responses transform, so
install() turns those calls into stream=True and assembles a ModelResponse.
Everything else — an explicit stream, any other provider — must go through
unchanged, and a second install() must not nest another collector.
"""

import litellm

from core.chatgpt_stream import install

_MESSAGES = [{"role": "user", "content": "Reply {\"score\": 7}"}]


def _builder(chunks, messages=None, **_ignored):
    return {"chunks": list(chunks), "messages": messages}


def test_nonstreaming_chatgpt_call_is_streamed_then_assembled(monkeypatch):
    """The callers pass no stream flag. The wrapper has to add one and hand
    back whatever stream_chunk_builder returns, including tool calls and usage,
    which is why the chunks and the original messages both reach it."""
    seen = {}

    def fake(*_args, **kwargs):
        seen["kwargs"] = kwargs
        return iter(["delta-a", "delta-b"])

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "stream_chunk_builder", _builder)
    install()

    # A `from litellm import completion` after install binds the wrapper.
    from litellm import completion

    caller_kwargs = {
        "model": "chatgpt/gpt-6-luna",
        "messages": _MESSAGES,
        "response_format": {"type": "json_object"},
    }
    out = completion(**caller_kwargs)

    assert seen["kwargs"]["stream"] is True
    assert seen["kwargs"]["model"] == "chatgpt/gpt-6-luna"
    assert seen["kwargs"]["response_format"] == {"type": "json_object"}
    assert "stream" not in caller_kwargs
    assert out["chunks"] == ["delta-a", "delta-b"]
    assert out["messages"] is _MESSAGES


def test_explicit_stream_is_left_alone(monkeypatch):
    """stream=True is the path that already works. Assembling it would steal
    the iterator from a caller that wants to read the chunks itself."""
    sentinel = object()
    built = []

    def fake(*_args, **kwargs):
        assert kwargs["stream"] is True
        return sentinel

    def builder(*_args, **_kwargs):
        built.append(True)
        raise AssertionError("explicit stream must not be assembled")

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "stream_chunk_builder", builder)
    install()

    assert litellm.completion(
        model="chatgpt/gpt-6-luna", messages=_MESSAGES, stream=True
    ) is sentinel
    assert built == []


def test_other_providers_are_not_rewritten(monkeypatch):
    seen = {}

    def fake(*_args, **kwargs):
        seen["kwargs"] = kwargs
        return {"choices": [{"message": {"content": "plain"}}]}

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "stream_chunk_builder", _builder)
    install()

    out = litellm.completion(model="openai/gpt-5-mini", messages=_MESSAGES)

    assert "stream" not in seen["kwargs"]
    assert out == {"choices": [{"message": {"content": "plain"}}]}


async def test_async_path_follows_the_same_rules(monkeypatch):
    """ADK's LiteLLMClient awaits acompletion with no stream flag. The async
    wrapper has to collect that stream, and still pass a real stream=True and
    an openai/ model straight through."""
    seen = []
    # The first chatgpt stream is the one the wrapper created. A later
    # explicit stream=True must come back untouched, so the fake only
    # yields chunks for that first call.
    streams = {"n": 0}

    async def fake(*_args, **kwargs):
        seen.append(dict(kwargs))
        if kwargs.get("stream") is True and str(kwargs.get("model", "")).startswith("chatgpt/"):
            streams["n"] += 1
            if streams["n"] == 1:
                async def chunks():
                    yield "async-chunk"
                return chunks()
            return {"left-alone": True}
        return {"passthrough": True}

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setattr(litellm, "stream_chunk_builder", _builder)
    install()

    assembled = await litellm.acompletion(model="chatgpt/gpt-5.4", messages=_MESSAGES)
    explicit = await litellm.acompletion(
        model="chatgpt/gpt-5.4", messages=_MESSAGES, stream=True
    )
    other = await litellm.acompletion(model="openai/gpt-5-mini", messages=_MESSAGES)

    assert assembled == {"chunks": ["async-chunk"], "messages": _MESSAGES}
    assert seen[0]["stream"] is True
    assert explicit == {"left-alone": True}
    assert seen[1]["stream"] is True
    assert other == {"passthrough": True}
    assert "stream" not in seen[2]


async def test_async_delegation_to_sync_completion_assembles_once(monkeypatch):
    """litellm.acompletion runs the sync completion global. Once both are
    wrapped, the sync one must see stream=True and return the chunks, or the
    two wrappers each assemble and the text is lost."""
    calls = {"builder": 0}

    def sync_fake(*_args, **kwargs):
        assert kwargs["stream"] is True
        return iter(["only-chunk"])

    async def async_fake(*args, **kwargs):
        return litellm.completion(*args, **kwargs)

    def builder(chunks, messages=None, **_ignored):
        calls["builder"] += 1
        return {"chunks": list(chunks), "messages": messages}

    monkeypatch.setattr(litellm, "completion", sync_fake)
    monkeypatch.setattr(litellm, "acompletion", async_fake)
    monkeypatch.setattr(litellm, "stream_chunk_builder", builder)
    install()

    out = await litellm.acompletion(model="chatgpt/gpt-6-luna", messages=_MESSAGES)

    assert calls["builder"] == 1
    assert out["chunks"] == ["only-chunk"]


def test_install_is_idempotent():
    install()
    wrapped = litellm.completion
    wrapped_async = litellm.acompletion
    install()
    assert litellm.completion is wrapped
    assert litellm.acompletion is wrapped_async


def test_adk_global_copied_before_install_is_rebound(monkeypatch):
    """LiteLLMClient awaits the name ADK copied from litellm.acompletion, not
    the attribute it reads on each call. A copy that happened first has to be
    replaced or the shim never runs for the agents."""
    import sys
    import types

    install()
    adk = types.ModuleType("google.adk.models.lite_llm")
    adk.completion = litellm.completion._appliedin_original
    adk.acompletion = litellm.acompletion._appliedin_original
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", adk)

    install()

    assert adk.completion is litellm.completion
    assert adk.acompletion is litellm.acompletion
