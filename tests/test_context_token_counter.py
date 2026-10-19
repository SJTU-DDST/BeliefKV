import os
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from beliefkv.runtime.context_token_counter import ChatTemplateTokenCounter


@pytest.fixture(scope="module")
def counter():
    path = os.environ.get("BELIEFKV_CONTEXT_TOKENIZER_TEST", "")
    if not path:
        pytest.skip("requires explicit local serving tokenizer")
    assert Path(path).is_dir()
    return ChatTemplateTokenCounter(path, cache_chars=2_000_000)


def test_cached_boundary_count_matches_full_tokenization(counter):
    messages = [
        SystemMessage(content="Long project source\n" * 100_000),
        HumanMessage(content="Inspect the project."),
        AIMessage(content="", tool_calls=[{
            "name": "execute", "args": {"command": "pytest -q"}, "id": "one",
        }]),
        ToolMessage(content="1 passed", tool_call_id="one"),
        AIMessage(content="Verified."),
    ]
    tools = [{
        "type": "function", "function": {
            "name": "execute", "description": "Run a command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
    }]
    from langchain_core.messages import convert_to_openai_messages
    import json
    wire = convert_to_openai_messages(messages)
    for message in wire:
        for call in message.get("tool_calls", ()):
            call["function"]["arguments"] = json.loads(call["function"]["arguments"])
    rendered = counter.tokenizer.apply_chat_template(
        wire, tools=tools, tokenize=False, add_generation_prompt=True, enable_thinking=True,
    )
    expected = len(counter.tokenizer.encode(rendered, add_special_tokens=False))
    assert counter.rendered_count(rendered) == expected
    assert counter(messages, tools=tools) == expected
    assert counter(messages, tools=tools) == expected
    assert counter._chars <= counter.cache_chars


def test_concurrent_workflows_initialize_one_counter(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time
    import beliefkv.runtime.context_token_counter as module

    barrier = Barrier(8)
    created = []

    def constructor(_):
        time.sleep(.01)
        value = object()
        created.append(value)
        return value

    def start(_):
        barrier.wait()
        return module.context_token_counter("/counter-concurrency-fixture")

    module._cached_context_token_counter.cache_clear()
    monkeypatch.setattr(module, "ChatTemplateTokenCounter", constructor)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            counters = list(executor.map(start, range(8)))
        assert len(created) == 1
        assert all(counter is created[0] for counter in counters)
    finally:
        module._cached_context_token_counter.cache_clear()
