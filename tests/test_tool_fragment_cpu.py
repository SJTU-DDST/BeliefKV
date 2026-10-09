from __future__ import annotations

import itertools
import json
from types import SimpleNamespace

from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import AIMessageChunk, HumanMessageChunk
from langchain_openai import ChatOpenAI
import pytest

from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI, DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


@pytest.fixture
def model():
    metadata = BeliefKVRequestMetadata("fixture", "root", "context", 0)
    adapter = DeepAgentsRuntimeAdapter(
        SimpleNamespace(emit_batch=lambda events: None), metadata,
    )
    return BeliefKVChatOpenAI(
        beliefkv_adapter=adapter, model="fixture", api_key="EMPTY",
        base_url="http://fixture.invalid/v1",
    )


def _raw(calls, **delta):
    return {
        "id": "fixture", "model": "fixture", "choices": [{
            "index": 0, "delta": {"tool_calls": calls, **delta},
            "finish_reason": "tool_calls",
        }],
    }


def _call(args, **kwargs):
    return {
        "index": 0, "id": "call-1",
        "function": {"name": "execute", "arguments": args}, **kwargs,
    }


def _equal(model, chunk, default_class=AIMessageChunk):
    original = json.dumps(chunk)
    baseline = ChatOpenAI._convert_chunk_to_generation_chunk(
        model, chunk, default_class, {"headers": {"x-fixture": "value"}},
    )
    optimized = model._convert_chunk_to_generation_chunk(
        chunk, default_class, {"headers": {"x-fixture": "value"}},
    )
    assert optimized.model_dump() == baseline.model_dump(), repr(chunk)
    assert json.dumps(chunk) == original


def test_short_prefix_space_matches_inherited_partial_json(model):
    values = [
        "".join(chars)
        for length in range(5)
        for chars in itertools.product('{[x" }', repeat=length)
    ]
    values.extend([
        "   ", "null", "false", "true", "NaN", "[1,2]", "1.2", '"a"',
        "echo " * 820, 'abc {"x":1}', "\\u1234" * 64, "\ufeff{}",
        "\t\ncontent", "\u00a0{}", "code\nwith\twhitespace",
    ])
    for value in values:
        _equal(model, _raw([_call(value)], content="Result.", role="assistant"))


@pytest.mark.parametrize("fragment_size", [1, 7, 32, 128, 4096])
@pytest.mark.parametrize("arguments", [
    json.dumps({"command": "python -m pytest", "text": "abc def " * 40}),
    '{"command": "echo unfinished',
    "null",
])
def test_final_merged_tool_arguments_keep_inherited_semantics(
    model, fragment_size, arguments,
):
    outputs = []
    for convert in (
        lambda chunk: ChatOpenAI._convert_chunk_to_generation_chunk(
            model, chunk, AIMessageChunk, {},
        ),
        lambda chunk: model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {}),
    ):
        chunks = []
        for offset in range(0, len(arguments), fragment_size):
            call = _call(arguments[offset:offset + fragment_size])
            if offset:
                call.pop("id")
                call["function"].pop("name")
            chunks.append(convert(_raw([call])))
        outputs.append(generate_from_stream(iter(chunks)).model_dump())
    assert outputs[0] == outputs[1]


def test_parallel_fragments_and_nonassistant_deltas_keep_semantics(model):
    calls = [
        _call("fragment"),
        _call('"text"', index=1, id="call-2"),
    ]
    for variant in (
        calls, [*calls, _call('{"open":', index=2)], [*calls, _call("", index=2)],
    ):
        _equal(model, _raw(variant))
        _equal(model, _raw(variant, role="user"))
        _equal(model, _raw(variant), HumanMessageChunk)


def test_noncanonical_indices_keep_inherited_validation(model):
    for index in (None, "0", True):
        _equal(model, _raw([_call("fragment", index=index)]))
    for call in ({"function": {"arguments": "fragment"}}, _call(None)):
        _equal(model, _raw([call]))
