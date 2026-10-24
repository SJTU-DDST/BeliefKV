import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from beliefkv.runtime.context_token_counter import ChatTemplateTokenCounter


@pytest.fixture
def fake_counter(monkeypatch):
    import json
    from transformers import AutoTokenizer

    class Tokenizer:
        chat_template = "template-a"
        render_calls = 0

        def encode(self, text, **kwargs):
            tokens = []
            for index, block in enumerate(text.split("<|im_start|>")):
                if index:
                    tokens.append(999)
                tokens.extend(block.encode())
            return tokens

        def apply_chat_template(self, wire, **kwargs):
            self.render_calls += 1
            return "<|im_start|>" + json.dumps(
                [self.chat_template, wire, kwargs["tools"]], ensure_ascii=False,
            )

    tokenizer = Tokenizer()
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", lambda *args, **kwargs: tokenizer)
    return ChatTemplateTokenCounter("/fake", cache_requests=True), tokenizer


@pytest.fixture(scope="module")
def counter():
    path = os.environ.get("BELIEFKV_CONTEXT_TOKENIZER_TEST", "")
    if not path:
        pytest.skip("requires explicit local serving tokenizer")
    assert Path(path).is_dir()
    return ChatTemplateTokenCounter(path, cache_chars=2_000_000, cache_requests=True)


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


def test_repeated_count_reuses_content_across_distinct_message_objects(fake_counter):
    counter, tokenizer = fake_counter
    expected = counter([HumanMessage(content="Inspect the source.")])
    assert counter([HumanMessage(content="Inspect the source.")]) == expected
    assert tokenizer.render_calls == 1
    changed = counter([HumanMessage(content="Inspect additional source evidence.")])
    assert changed != expected and tokenizer.render_calls == 2


@pytest.mark.parametrize("values", ((0.0, -0.0), (1, 1.0), (False, 0)))
def test_mutable_tool_arguments_preserve_numeric_types_and_signed_zero(fake_counter, values):
    counter, tokenizer = fake_counter
    message = AIMessage(content="", tool_calls=[{
        "name": "inspect", "args": {"value": values[0]}, "id": "one",
    }])
    first = counter([message])
    message.tool_calls[0]["args"]["value"] = values[1]
    second = counter([message])
    assert tokenizer.render_calls == 2 and first != second
    assert counter([message]) == second and tokenizer.render_calls == 2


def test_argument_order_schema_and_template_changes_invalidate_count(fake_counter):
    counter, tokenizer = fake_counter
    message = AIMessage(content="", tool_calls=[{
        "name": "inspect", "args": {"a": 1, "b": 2}, "id": "one",
    }])
    tools = [{"type": "function", "function": {
        "name": "inspect", "description": "Inspect evidence.",
        "parameters": {"type": "object", "properties": {}},
    }}]
    counter([message], tools=tools)
    message.tool_calls[0]["args"] = {"b": 2, "a": 1}
    counter([message], tools=tools)
    assert tokenizer.render_calls == 2
    tools[0]["function"]["description"] = "Inspect a different source."
    counter([message], tools=tools)
    assert tokenizer.render_calls == 3
    tokenizer.chat_template = "a different template"
    counter([message], tools=tools)
    assert tokenizer.render_calls == 4


@pytest.mark.parametrize("unsupported", ("over_budget", "nonfinite"))
def test_unrepresentable_count_keys_keep_the_original_count_path(fake_counter, unsupported):
    counter, tokenizer = fake_counter
    if unsupported == "over_budget":
        counter.cache_chars = 8
        messages = [HumanMessage(content="A long source that cannot fit this cache.")]
    else:
        messages = [AIMessage(content="", tool_calls=[{
            "name": "inspect", "args": {"value": float("nan")}, "id": "one",
        }])]
    expected = counter(messages)
    assert counter(messages) == expected
    assert counter._last_request is None and tokenizer.render_calls == 2


def test_concurrent_different_requests_never_reuse_another_count(fake_counter):
    from concurrent.futures import ThreadPoolExecutor

    counter, _ = fake_counter
    messages = [[HumanMessage(content="source " * index)] for index in range(1, 33)]
    counter.cache_requests = False
    expected = [counter(row) for row in messages]
    counter.cache_requests = True
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(counter, messages)) == expected


def test_compression_and_completion_budget_share_the_same_exact_count(fake_counter, monkeypatch):
    from beliefkv.runtime.context_lifecycle import (
        CompletionBudgetMiddleware, ContextLifecycleMiddleware, ContextLifecyclePolicy,
    )
    from tests.test_context_lifecycle import _FakeModel

    counter, tokenizer = fake_counter
    monkeypatch.setattr(
        "beliefkv.runtime.context_lifecycle.context_token_counter", lambda _: counter,
    )
    policy = ContextLifecyclePolicy(
        window_tokens=262144, compression_trigger_ratio=.95,
    )
    lifecycle = ContextLifecycleMiddleware(
        _FakeModel(), backend=SimpleNamespace(), policy=policy,
        compaction_sink=SimpleNamespace(),
    )
    budget = CompletionBudgetMiddleware(
        intermediate_tokens=16384, final_tokens=16384,
        model_context_tokens=262144, adaptive_context_budget=True,
    )
    messages, system = [HumanMessage(content="Inspect the source.")], SystemMessage(content="source")
    expected = lifecycle._count_tokens(messages, system, [])
    request = SimpleNamespace(
        messages=messages, system_message=system, tools=[], state={}, model_settings={},
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    adjusted = budget._request(request)
    assert adjusted.model_settings["max_tokens"] == min(16384, 262144 - expected - 256)
    assert tokenizer.render_calls == 1


def test_retention_counts_fragments_without_rendering_a_request(fake_counter, monkeypatch):
    from beliefkv.runtime.context_lifecycle import (
        ContextLifecycleMiddleware, ContextLifecyclePolicy,
    )
    from tests.test_context_lifecycle import _FakeModel

    counter, tokenizer = fake_counter
    monkeypatch.setattr(
        "beliefkv.runtime.context_lifecycle.context_token_counter", lambda _: counter,
    )
    lifecycle = ContextLifecycleMiddleware(
        _FakeModel(), backend=SimpleNamespace(),
        policy=ContextLifecyclePolicy(
            window_tokens=4096, keep_tokens=128, summary_output_tokens=256,
            compression_trigger_ratio=.95,
        ),
        compaction_sink=SimpleNamespace(),
    )
    messages = [
        HumanMessage(content="Investigate the parser."),
        AIMessage(content="", tool_calls=[{
            "name": "execute", "args": {"command": "pytest -q"}, "id": "one",
        }]),
        ToolMessage(content="earlier evidence " * 1000, tool_call_id="one"),
        AIMessage(content="", tool_calls=[{
            "name": "execute", "args": {"command": "git diff"}, "id": "two",
        }]),
        ToolMessage(content="latest evidence", tool_call_id="two"),
    ]

    cutoff = lifecycle._determine_cutoff_index(messages)
    assert cutoff == 3
    assert lifecycle.token_counter(messages[cutoff:]) > 0
    assert lifecycle.token_counter([SystemMessage(content="system only")], tools=[]) > 0
    assert tokenizer.render_calls == 0
    assert "Investigate the parser." in lifecycle._summary_input(messages[:cutoff])
    assert tokenizer.render_calls == 0
    lifecycle._count_tokens(messages, SystemMessage(content="source"), [])
    assert tokenizer.render_calls == 1


def test_real_template_stays_strict_while_compression_accepts_fragments(counter, monkeypatch):
    from jinja2.exceptions import TemplateError
    from beliefkv.runtime.context_lifecycle import (
        ContextLifecycleMiddleware, ContextLifecyclePolicy,
    )
    from tests.test_context_lifecycle import _FakeModel

    monkeypatch.setattr(
        "beliefkv.runtime.context_lifecycle.context_token_counter", lambda _: counter,
    )
    lifecycle = ContextLifecycleMiddleware(
        _FakeModel(), backend=SimpleNamespace(),
        policy=ContextLifecyclePolicy(
            window_tokens=4096, keep_tokens=128, summary_output_tokens=256,
            compression_trigger_ratio=.95,
        ),
        compaction_sink=SimpleNamespace(),
    )
    fragments = [
        AIMessage(content="Investigating " * 1000),
        AIMessage(content="", tool_calls=[{
            "name": "execute", "args": {"command": "git diff"}, "id": "one",
        }]),
        ToolMessage(content="latest evidence", tool_call_id="one"),
    ]
    if "No user query" in counter.tokenizer.chat_template:
        with pytest.raises(TemplateError, match="No user query"):
            counter(fragments)
    else:
        # Qwen3-Coder accepts assistant-led fragments in its native template.
        assert counter(fragments) > 0
    cutoff = lifecycle._determine_cutoff_index(fragments)
    assert cutoff == 1
    full = [HumanMessage(content="Investigate the parser."), *fragments]
    assert lifecycle._count_tokens(full, None, []) == counter(full)
    assert lifecycle._count_tokens(full, None, []) == counter(full)


def test_frontend_flag_keeps_native_counter_separate_from_predictive(monkeypatch):
    import beliefkv.runtime.context_token_counter as module

    module._cached_context_token_counter.cache_clear()
    monkeypatch.setattr(
        module, "ChatTemplateTokenCounter",
        lambda _, **kwargs: SimpleNamespace(cache_requests=kwargs["cache_requests"]),
    )
    try:
        monkeypatch.setenv("BELIEFKV_CHAT_TOKEN_BLOCK_CACHE", "0")
        native = module.context_token_counter("/counter-flag-fixture")
        monkeypatch.setenv("BELIEFKV_CHAT_TOKEN_BLOCK_CACHE", "1")
        predictive = module.context_token_counter("/counter-flag-fixture")
        assert not native.cache_requests and predictive.cache_requests
        assert native is not predictive
        monkeypatch.setenv("BELIEFKV_CHAT_TOKEN_BLOCK_CACHE", "0")
        assert module.context_token_counter("/counter-flag-fixture") is native
    finally:
        module._cached_context_token_counter.cache_clear()


def test_concurrent_workflows_initialize_one_counter(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time
    import beliefkv.runtime.context_token_counter as module

    barrier = Barrier(8)
    created = []

    def constructor(_, **kwargs):
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
