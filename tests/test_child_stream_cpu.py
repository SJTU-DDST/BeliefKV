from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import APIError, AsyncOpenAI, OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI, DeepAgentsRuntimeAdapter
from beliefkv.runtime.openai_stream import (
    DecodedAsyncChatCompletions,
    DecodedChatCompletions,
)
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


def _sse(*, error=False):
    def chunk(delta, *, finish=None):
        return {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0,
            "model": "fixture", "system_fingerprint": "fp-fixture",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    frames = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"reasoning_content": "Inspecting the evidence."}),
        chunk({"content": "Result:\n"}),
        chunk({"content": "The cause is known.", "refusal": None}),
        chunk({"tool_calls": [{
            "index": 0, "id": "call-1", "type": "function",
            "function": {"name": "execute", "arguments": '{"command":'},
        }]}),
        chunk({"tool_calls": [{
            "index": 0, "function": {"arguments": '"python -m pytest"}'},
        }]}),
        chunk({}, finish="tool_calls"),
        {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0,
            "model": "fixture", "choices": [], "usage": {
                "prompt_tokens": 30, "completion_tokens": 11, "total_tokens": 41,
                "completion_tokens_details": {"reasoning_tokens": 4},
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        },
    ]
    frames[3]["choices"][0]["logprobs"] = {
        "content": [{
            "token": ".", "logprob": -0.1, "bytes": [46],
            "top_logprobs": [{
                "token": "<|im_end|>", "logprob": -1.2, "bytes": None,
            }],
        }],
        "refusal": None,
    }
    frames[3]["beliefkv_eos_logprobs"] = {"eos_probability": 0.3}
    if error:
        frames.insert(4, {"error": {"message": "fixture failure", "type": "server_error"}})
    return (
        "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
        + "data: [DONE]\n\n"
    ).encode()


def _models(http, http_async, *, include_headers=False):
    metadata = BeliefKVRequestMetadata(
        "fixture", "root", "context", 0, full_prompt_replay_guaranteed=True,
    )
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    settings = dict(
        model="fixture", api_key="EMPTY", base_url="http://fixture.invalid/v1",
        http_client=http, http_async_client=http_async, max_retries=0,
        include_response_headers=include_headers,
    )
    typed = BeliefKVChatOpenAI(beliefkv_adapter=adapter, **settings)
    typed.client = typed.root_client.chat.completions
    typed.async_client = typed.root_async_client.chat.completions
    decoded = BeliefKVChatOpenAI(beliefkv_adapter=adapter, **settings)
    return typed, decoded, {"extra_body": {"beliefkv_metadata": metadata.to_wire()}}


@pytest.mark.parametrize("async_stream", [False, True])
@pytest.mark.parametrize("include_headers", [False, True])
def test_langchain_stream_and_final_tool_result_remain_identical(
    async_stream, include_headers,
):
    bodies = []
    responses = []

    def respond(request):
        bodies.append(json.loads(request.content))
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream", "x-fixture": "same"},
            stream=httpx.ByteStream(_sse()),
        )
        responses.append(response)
        return response

    async def compare():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_async:
            with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                typed, decoded, kwargs = _models(
                    http, http_async, include_headers=include_headers,
                )
                outputs = []
                for model in (typed, decoded):
                    if async_stream:
                        chunks = [chunk async for chunk in ChatOpenAI._astream(
                            model, [HumanMessage(content="Inspect.")], **kwargs,
                        )]
                    else:
                        chunks = list(ChatOpenAI._stream(
                            model, [HumanMessage(content="Inspect.")], **kwargs,
                        ))
                    wire_chunks = [chunk.model_dump() for chunk in chunks]
                    result = generate_from_stream(iter(chunks))
                    outputs.append((wire_chunks, result.model_dump()))
                assert outputs[0] == outputs[1]
                message = result.generations[0].message
                assert message.tool_calls[0]["args"] == {"command": "python -m pytest"}
                assert message.usage_metadata["output_tokens"] == 11
                assert result.generations[0].generation_info["finish_reason"] == "tool_calls"

    asyncio.run(compare())
    assert bodies[0] == bodies[1]
    assert all(response.is_closed for response in responses)


@pytest.mark.parametrize("async_stream", [False, True])
def test_stream_errors_keep_sdk_exception_and_close_response(async_stream):
    responses = []

    def respond(request):
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_sse(error=True)),
        )
        responses.append(response)
        return response

    async def consume():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_async:
            with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                _, model, kwargs = _models(http, http_async)
                if async_stream:
                    async for _ in ChatOpenAI._astream(
                        model, [HumanMessage(content="Inspect.")], **kwargs,
                    ):
                        pass
                else:
                    list(ChatOpenAI._stream(
                        model, [HumanMessage(content="Inspect.")], **kwargs,
                    ))

    with pytest.raises(APIError, match="fixture failure"):
        asyncio.run(consume())
    assert all(response.is_closed for response in responses)


def test_nonstream_and_untagged_resource_calls_keep_typed_sdk_results():
    def respond(request):
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=_sse(),
            )
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 0,
            "model": "fixture", "choices": [{
                "index": 0, "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }],
        })

    payload = {"model": "fixture", "messages": [{"role": "user", "content": "Inspect."}]}

    async def compare():
        async with AsyncOpenAI(
            api_key="EMPTY", base_url="http://fixture.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            resource = DecodedAsyncChatCompletions(client.chat.completions)
            assert isinstance(await resource.create(**payload), ChatCompletion)
            async with await resource.create(**payload, stream=True) as stream:
                assert isinstance(await anext(stream), ChatCompletionChunk)
        with OpenAI(
            api_key="EMPTY", base_url="http://fixture.invalid/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as client:
            resource = DecodedChatCompletions(client.chat.completions)
            assert isinstance(resource.create(**payload), ChatCompletion)
            with resource.create(**payload, stream=True) as stream:
                assert isinstance(next(iter(stream)), ChatCompletionChunk)

    asyncio.run(compare())


@pytest.mark.parametrize("async_stream", [False, True])
def test_decoded_stream_closes_response_when_consumer_stops_early(async_stream):
    responses = []

    def respond(request):
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_sse()),
        )
        responses.append(response)
        return response

    payload = {
        "model": "fixture", "messages": [{"role": "user", "content": "Inspect."}],
        "stream": True, "extra_body": {"beliefkv_metadata": {"context_id": "fixture"}},
    }

    async def consume():
        if async_stream:
            async with AsyncOpenAI(
                api_key="EMPTY", base_url="http://fixture.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ) as client:
                resource = DecodedAsyncChatCompletions(client.chat.completions)
                async with await resource.create(**payload) as stream:
                    assert not responses[-1].is_closed
                    assert isinstance(await anext(stream), dict)
        else:
            with OpenAI(
                api_key="EMPTY", base_url="http://fixture.invalid/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            ) as client:
                resource = DecodedChatCompletions(client.chat.completions)
                with resource.create(**payload) as stream:
                    assert not responses[-1].is_closed
                    assert isinstance(next(iter(stream)), dict)

    asyncio.run(consume())
    assert responses[-1].is_closed


def test_custom_resources_are_preserved():
    custom = SimpleNamespace(create=lambda **kwargs: None)
    metadata = BeliefKVRequestMetadata("fixture", "root", "context", 0)
    adapter = DeepAgentsRuntimeAdapter(SimpleNamespace(emit_batch=lambda events: None), metadata)
    model = BeliefKVChatOpenAI(
        beliefkv_adapter=adapter, model="fixture", api_key="EMPTY",
        client=custom, async_client=custom,
    )
    assert model.client is custom
    assert model.async_client is custom
