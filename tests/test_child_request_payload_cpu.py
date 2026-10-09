from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI, OpenAI

from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI, DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


def _models(**kwargs):
    metadata = BeliefKVRequestMetadata(
        "fixture", "root", "context", 0, full_prompt_replay_guaranteed=True,
    )
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    settings = {
        "model": "fixture", "api_key": "EMPTY",
        "base_url": "http://fixture.invalid/v1", "max_retries": 0,
        **kwargs,
    }
    return (
        ChatOpenAI(**settings),
        BeliefKVChatOpenAI(beliefkv_adapter=adapter, **settings),
        metadata,
    )


def _response(*, streaming=False):
    if streaming:
        chunk = {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0,
            "model": "fixture", "choices": [{
                "index": 0, "delta": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }],
        }
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
        )
    return httpx.Response(200, json={
        "id": "fixture", "object": "chat.completion", "created": 0,
        "model": "fixture",
        "choices": [{
            "index": 0, "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }],
    })


def _wire(payload, *, async_client=False):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return _response(streaming=payload.get("stream", False))

    transport = httpx.MockTransport(respond)
    if async_client:
        async def create():
            async with httpx.AsyncClient(transport=transport) as http:
                async with AsyncOpenAI(
                    api_key="EMPTY", base_url="http://fixture.invalid/v1",
                    http_client=http, max_retries=0,
                ) as client:
                    result = await client.chat.completions.create(**payload)
                    if payload.get("stream"):
                        chunks = [chunk async for chunk in result]
                        assert chunks[0].choices[0].delta.content == "OK"
                    else:
                        assert result.choices[0].message.content == "OK"
        asyncio.run(create())
    else:
        with httpx.Client(transport=transport) as http:
            with OpenAI(
                api_key="EMPTY", base_url="http://fixture.invalid/v1",
                http_client=http, max_retries=0,
            ) as client:
                result = client.chat.completions.create(**payload)
                if payload.get("stream"):
                    chunks = list(result)
                    assert chunks[0].choices[0].delta.content == "OK"
                else:
                    assert result.choices[0].message.content == "OK"
    [body] = bodies
    return body


@pytest.mark.parametrize("async_client", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_native_wire_messages_keep_final_sdk_body_identical(async_client, streaming):
    baseline, optimized, metadata = _models(temperature=0, seed=21, max_tokens=8192)
    messages = [
        SystemMessage(content="Inspect and report."),
        HumanMessage(content="Find the cause.", name="requester"),
        AIMessage(content="", tool_calls=[{
            "id": "call-1", "name": "execute",
            "args": {"command": "python -m pytest", "timeout": 600},
        }]),
        ToolMessage(content="3 passed\n", tool_call_id="call-1"),
        AIMessage(content="The tests pass."),
        HumanMessage(content=[
            {"type": "text", "text": "Inspect this output."},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64,ZmFrZQ==", "detail": "low",
            }},
        ]),
    ]
    kwargs = {
        "extra_body": {
            "beliefkv_metadata": metadata.to_wire(),
            "rid": "beliefkv:fixture", "session_id": "session-1",
            "beliefkv_eos_logprobs": True,
            "chat_template_kwargs": {"enable_thinking": True},
        },
        "tools": [{
            "type": "function", "function": {
                "name": "execute", "description": "Execute a command.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "stop": ["END"],
        "stream": streaming,
        **({"stream_options": {"include_usage": True}} if streaming else {}),
    }
    old = baseline._get_request_payload(messages, **kwargs)
    new = optimized._get_request_payload(messages, **kwargs)
    assert new["messages"] == []
    assert new["extra_body"]["messages"] == old["messages"]
    assert _wire(new, async_client=async_client) == _wire(
        old, async_client=async_client,
    )
    assert "messages" not in kwargs["extra_body"]


def test_native_wire_messages_preserve_explicit_extra_body_override():
    baseline, optimized, metadata = _models()
    messages = [HumanMessage(content="Original")]
    kwargs = {"extra_body": {
        "beliefkv_metadata": metadata.to_wire(),
        "messages": [{"role": "user", "content": "Explicit override"}],
    }}
    old = baseline._get_request_payload(messages, **kwargs)
    new = optimized._get_request_payload(messages, **kwargs)
    assert _wire(new) == _wire(old)
    assert _wire(new)["messages"] == kwargs["extra_body"]["messages"]


def test_untagged_chat_request_keeps_original_payload():
    baseline, optimized, _ = _models()
    messages = [HumanMessage(content="Original")]
    kwargs = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    old = baseline._get_request_payload(messages, **kwargs)
    new = optimized._get_request_payload(messages, **kwargs)
    assert new == old
    assert new["messages"]


def test_responses_payload_keeps_original_conversion():
    baseline, optimized, metadata = _models(use_responses_api=True)
    messages = [HumanMessage(content="Original")]
    kwargs = {"extra_body": {"beliefkv_metadata": metadata.to_wire()}}
    assert optimized._get_request_payload(messages, **kwargs) == (
        baseline._get_request_payload(messages, **kwargs)
    )
