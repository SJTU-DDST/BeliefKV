from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("deepagents")

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult

from beliefkv.runtime.context_lifecycle import (
    CONTEXT_LIFECYCLE_PRIVATE_STATE_KEYS,
    CompletionBudgetMiddleware,
    ContextLifecycleMiddleware,
    ContextLifecyclePolicy,
    ContextLifecycleState,
)
from beliefkv.runtime.deepagents_adapter import BeliefKVChatOpenAI


class _FakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "context-lifecycle-test"

    def _generate(self, messages, **kwargs: Any) -> ChatResult:
        del messages, kwargs
        raise AssertionError("the token accounting test must not invoke the model")


def test_context_lifecycle_defaults_to_32k_with_8k_retention() -> None:
    policy = ContextLifecyclePolicy()

    assert policy.window_tokens == 32_768
    assert policy.keep_tokens == 8_192
    assert policy.intermediate_output_tokens == 1_024
    assert policy.summary_output_tokens == 2_048


def test_context_lifecycle_rejects_invalid_retention() -> None:
    with pytest.raises(ValueError, match="keep_tokens"):
        ContextLifecyclePolicy(window_tokens=8_192, keep_tokens=8_192)


def test_context_lifecycle_rejects_completion_budget_outside_model_context() -> None:
    with pytest.raises(ValueError, match="completion budget"):
        ContextLifecyclePolicy(
            window_tokens=8_192,
            keep_tokens=64,
            intermediate_output_tokens=8_192,
            summary_output_tokens=64,
            model_context_tokens=8_192,
        )


def test_32k_budget_excludes_static_system_prompt_and_tool_schema() -> None:
    model = _FakeModel()
    middleware = ContextLifecycleMiddleware(
        model,
        backend=SimpleNamespace(),
        policy=ContextLifecyclePolicy(),
        compaction_sink=SimpleNamespace(),
    )
    dynamic = [HumanMessage(content="x" * 4_000)]
    static = SystemMessage(content="y" * 80_000)
    tools = [
        {
            "type": "function",
            "function": {"name": "large", "description": "z" * 80_000},
        }
    ]

    dynamic_only = middleware._count_tokens(dynamic, None, None)
    with_static_schema = middleware._count_tokens(dynamic, static, tools)

    assert with_static_schema == dynamic_only
    assert with_static_schema < 32_768


def test_summarization_cursor_is_declared_as_private_graph_state() -> None:
    from deepagents.middleware._state import private_state_field_names

    assert "_summarization_event" in private_state_field_names(
        ContextLifecycleState
    )


def test_completion_budget_separates_tool_turns_from_finalization() -> None:
    final_mode = [False]
    middleware = CompletionBudgetMiddleware(
        intermediate_tokens=1_024,
        final_tokens=4_096,
        model_context_tokens=8_192,
        final_mode=lambda: final_mode[0],
    )
    request = ModelRequest(
        model=_FakeModel(),
        messages=[HumanMessage(content="continue")],
    )
    observed: list[int] = []

    def handler(current: ModelRequest) -> ModelResponse:
        observed.append(int(current.model_settings["max_tokens"]))
        return ModelResponse(result=[HumanMessage(content="ok")])

    middleware.wrap_model_call(request, handler)
    middleware.wrap_model_call(
        request.override(state={"guard_forcing_completion": True}), handler
    )
    final_mode[0] = True
    middleware.wrap_model_call(request, handler)

    assert observed == [1_024, 4_096, 4_096]


def test_completion_budget_does_not_forward_internal_prompt_limit_to_openai() -> None:
    middleware = CompletionBudgetMiddleware(
        intermediate_tokens=1_024,
        final_tokens=4_096,
        model_context_tokens=32_768,
    )
    request = ModelRequest(
        model=_FakeModel(),
        messages=[HumanMessage(content="continue")],
    )

    def handler(current: ModelRequest) -> ModelResponse:
        assert current.model_settings == {"max_tokens": 1_024}
        return ModelResponse(result=[HumanMessage(content="ok")])

    middleware.wrap_model_call(request, handler)


def test_model_preflight_rejects_prompt_above_budget_limit() -> None:
    model = BeliefKVChatOpenAI.model_construct(
        model="test-model",
    )
    object.__setattr__(
        model,
        "_beliefkv_prompt_token_counter",
        lambda messages: 9,
    )
    model.set_beliefkv_prompt_limit(
        model_context_tokens=16,
        completion_tokens=8,
    )

    with pytest.raises(ContextOverflowError, match="prompt context preflight failed"):
        model._preflight_model_context([HumanMessage(content="too long")])


def test_model_preflight_accepts_prompt_at_budget_limit() -> None:
    model = BeliefKVChatOpenAI.model_construct(
        model="test-model",
    )
    object.__setattr__(
        model,
        "_beliefkv_prompt_token_counter",
        lambda messages: 8,
    )
    model.set_beliefkv_prompt_limit(
        model_context_tokens=16,
        completion_tokens=8,
    )

    model._preflight_model_context([HumanMessage(content="at limit")])


def test_context_lifecycle_rejects_foreign_out_of_bounds_summary_event() -> None:
    request = ModelRequest(
        model=_FakeModel(),
        messages=[HumanMessage(content="short child context")],
        state={
            "messages": [HumanMessage(content="short child context")],
            "_summarization_event": {
                "cutoff_index": 11,
                "summary_message": HumanMessage(content="parent summary"),
                "file_path": None,
            },
        },
    )

    sanitized = ContextLifecycleMiddleware._sanitize_summarization_event(request)

    assert request.state["_summarization_event"] is not None
    assert sanitized.state["_summarization_event"] is None
    assert CONTEXT_LIFECYCLE_PRIVATE_STATE_KEYS == frozenset(
        {"_summarization_event"}
    )


def _summary_middleware(responses: list[AIMessage]) -> ContextLifecycleMiddleware:
    return ContextLifecycleMiddleware(
        FakeMessagesListChatModel(responses=responses),
        backend=SimpleNamespace(),
        policy=ContextLifecyclePolicy(),
        compaction_sink=SimpleNamespace(),
    )


def test_context_summary_retries_one_empty_response() -> None:
    middleware = _summary_middleware(
        [
            AIMessage(content=""),
            AIMessage(content=[{"type": "text", "text": "durable checkpoint"}]),
        ]
    )

    assert middleware._create_summary(
        [HumanMessage(content="original objective")]
    ) == "durable checkpoint"


def test_context_summary_uses_bounded_source_fallback_without_claiming_success() -> None:
    middleware = _summary_middleware(
        [AIMessage(content=""), AIMessage(content="")]
    )
    messages = [
        HumanMessage(content="objective: fix the parser"),
        HumanMessage(content="latest state: tests have not run " + "x" * 20_000),
    ]

    summary = middleware._create_summary(messages)

    assert "summarizer returned empty output twice" in summary
    assert "not a claim that the task or any test completed" in summary
    assert "objective: fix the parser" in summary
    assert "latest state: tests have not run" in summary
    assert len(summary) <= middleware.policy.summary_output_tokens * 4


def test_async_context_summary_uses_same_empty_response_recovery() -> None:
    middleware = _summary_middleware(
        [AIMessage(content=""), AIMessage(content="async checkpoint")]
    )

    summary = asyncio.run(
        middleware._acreate_summary([HumanMessage(content="continue safely")])
    )

    assert summary == "async checkpoint"
