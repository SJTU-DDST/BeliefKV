from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Awaitable, Callable, Protocol, Sequence

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import count_tokens_approximately, get_buffer_string
from langchain_core.language_models import BaseChatModel
from typing_extensions import NotRequired


CONTEXT_SUMMARY_PROMPT = """You are creating a durable checkpoint for a coding agent.

Preserve all state needed to continue the task correctly:
- the original objective and acceptance criteria;
- the current plan, completed work, and exact next actions;
- files read or modified and the important changes in each file;
- commands and tests already run, including their results;
- unresolved errors, blockers, hypotheses, and rejected approaches;
- child-agent assignments, returned findings, and outstanding JOIN state.

Do not copy verbose tool output when a concise factual record is sufficient. Do
not claim that work or tests completed unless the messages establish that fact.
Return only the checkpoint summary.

<messages>
{messages}
</messages>
"""


CONTEXT_LIFECYCLE_PRIVATE_STATE_KEYS = frozenset({"_summarization_event"})
_EMPTY_SUMMARY_RETRY_SUFFIX = """

The previous summarization attempt returned no checkpoint text. Return a
non-empty checkpoint now. Preserve uncertainty and incomplete work; do not
invent progress or a terminal outcome.
"""


@dataclass(frozen=True)
class ContextLifecyclePolicy:
    """Runtime-owned text context limits; independent of the SGLang KV pool."""

    window_tokens: int = 32_768
    keep_tokens: int = 8_192
    intermediate_output_tokens: int = 1_024
    summary_output_tokens: int = 2_048
    model_context_tokens: int = 262_144

    def __post_init__(self) -> None:
        if min(
            self.window_tokens,
            self.keep_tokens,
            self.intermediate_output_tokens,
            self.summary_output_tokens,
        ) <= 0:
            raise ValueError("context lifecycle token limits must be positive")
        if self.keep_tokens >= self.window_tokens:
            raise ValueError("context keep_tokens must be smaller than window_tokens")
        if self.summary_output_tokens > self.window_tokens - self.keep_tokens:
            raise ValueError("summary output must fit outside the retained context")
        if self.window_tokens > self.model_context_tokens:
            raise ValueError("context window cannot exceed the model context")
        if self.intermediate_output_tokens >= self.model_context_tokens:
            raise ValueError("completion budget must fit inside model context")


@dataclass(frozen=True)
class ContextCompactionRecord:
    source_message_count: int
    retained_message_count: int
    summary_chars: int
    summary_sha256: str
    trigger_tokens: int
    keep_tokens: int


class ContextCompactionSink(Protocol):
    def stage_context_compaction(
        self, record: ContextCompactionRecord
    ) -> AbstractContextManager[None]: ...


class ContextLifecycleState(AgentState[Any]):
    """The sole cross-activation private datum made explicit by this runtime."""

    _summarization_event: Annotated[
        NotRequired[dict[str, Any] | None], PrivateStateAttr
    ]


def _summary_message(messages: list[BaseMessage]) -> BaseMessage | None:
    if not messages:
        return None
    candidate = messages[0]
    if candidate.additional_kwargs.get("lc_source") != "summarization":
        return None
    return candidate


def _response_checkpoint_text(response: BaseMessage) -> str:
    """Extract visible checkpoint text without treating reasoning as a summary."""

    text = response.text.strip()
    if text:
        return text
    parts: list[str] = []
    for block in response.content_blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") not in {"text", "output_text"}:
            continue
        value = block.get("text")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts).strip()


class ContextLifecycleMiddleware(SummarizationMiddleware):
    """Deep Agents compaction with a BeliefKV ownership transition barrier."""

    state_schema = ContextLifecycleState

    def __init__(
        self,
        model: BaseChatModel,
        *,
        backend: Any,
        policy: ContextLifecyclePolicy,
        compaction_sink: ContextCompactionSink,
        summary_callbacks: Sequence[BaseCallbackHandler] = (),
        persist_cursor_across_invocations: bool = False,
        completion_tokens: int | None = None,
    ) -> None:
        self.policy = policy
        self.completion_tokens = (
            max(policy.intermediate_output_tokens, policy.summary_output_tokens)
            if completion_tokens is None
            else completion_tokens
        )
        if not 0 < self.completion_tokens < policy.model_context_tokens:
            raise ValueError("completion budget must fit inside model context")
        super().__init__(
            model,
            backend=backend,
            trigger=("tokens", policy.window_tokens),
            keep=("tokens", policy.keep_tokens),
            summary_prompt=CONTEXT_SUMMARY_PROMPT,
            trim_tokens_to_summarize=policy.window_tokens - policy.keep_tokens,
            truncate_args_settings=None,
            token_counter=partial(count_tokens_approximately, chars_per_token=3.0),
        )
        self.compaction_sink = compaction_sink
        self.summary_callbacks = tuple(summary_callbacks)
        self.persist_cursor_across_invocations = (
            persist_cursor_across_invocations
        )
        self._cursor_lock = threading.Lock()
        self._latest_summarization_event: dict[str, Any] | None = None

    def latest_summarization_event(self) -> dict[str, Any] | None:
        """Return this middleware instance's cross-activation cursor."""

        with self._cursor_lock:
            return (
                dict(self._latest_summarization_event)
                if self._latest_summarization_event is not None
                else None
            )

    def _remember_summarization_event(self, value: Any) -> None:
        event = dict(value) if isinstance(value, Mapping) else None
        with self._cursor_lock:
            self._latest_summarization_event = event

    def _remember_response_event(
        self, response: ModelResponse | ExtendedModelResponse
    ) -> None:
        if not isinstance(response, ExtendedModelResponse):
            return
        update = response.command.update
        if isinstance(update, Mapping) and "_summarization_event" in update:
            self._remember_summarization_event(update["_summarization_event"])

    def _restore_persistent_cursor(self, request: ModelRequest) -> ModelRequest:
        if (
            not self.persist_cursor_across_invocations
            or request.state.get("_summarization_event") is not None
        ):
            return request
        event = self.latest_summarization_event()
        if event is None:
            return request
        state = dict(request.state)
        state["_summarization_event"] = event
        return request.override(state=state)

    def _count_tokens(
        self,
        messages: list[BaseMessage],
        system_message: BaseMessage | None,
        tools: list[Any] | None,
    ) -> int:
        """Count dynamic history with the same estimate as the request preflight.

        Static prompt/schema bytes cannot be compacted, so they reduce the
        available dynamic window without becoming part of the keep budget.
        """

        dynamic_tokens = self.token_counter(messages)
        static_tokens = self.token_counter(
            [system_message] if system_message is not None else [], tools=tools
        )
        prompt_limit = self.policy.model_context_tokens - self.completion_tokens
        dynamic_limit = prompt_limit - static_tokens - 4_096
        return dynamic_tokens + max(0, self.policy.window_tokens - dynamic_limit)

    @staticmethod
    def _sanitize_summarization_event(request: ModelRequest) -> ModelRequest:
        event = request.state.get("_summarization_event")
        if event is None:
            return request
        cutoff_index = event.get("cutoff_index") if isinstance(event, dict) else None
        if isinstance(cutoff_index, int) and 0 <= cutoff_index <= len(request.messages):
            return request
        state = dict(request.state)
        state["_summarization_event"] = None
        return request.override(state=state)

    def _summary_input(self, messages: list[BaseMessage]) -> str:
        trimmed = self._lc_helper._trim_messages_for_summary(messages)
        if trimmed:
            formatted = get_buffer_string(trimmed, format="xml")
        else:
            # LangChain can return an empty set when one indivisible tool turn
            # exceeds the trim budget. Preserve both the original objective and
            # the newest state in a bounded checkpoint input.
            rendered = get_buffer_string(messages, format="xml")
            max_chars = max(
                1_024,
                (self.policy.window_tokens - self.policy.keep_tokens) * 4,
            )
            if len(rendered) > max_chars:
                side = max_chars // 2
                rendered = (
                    rendered[:side]
                    + "\n[... middle of oversized history omitted ...]\n"
                    + rendered[-side:]
                )
            formatted = rendered
        return self._lc_helper.summary_prompt.format(messages=formatted).rstrip()

    def _fallback_checkpoint(self, messages: list[BaseMessage]) -> str:
        """Retain bounded source evidence when two model summaries are empty."""

        rendered = get_buffer_string(messages, format="xml").strip()
        prefix = (
            "Context compaction fallback checkpoint. The summarizer returned "
            "empty output twice. The text below is bounded source history, not "
            "a claim that the task or any test completed.\n\n"
        )
        max_chars = max(1_024, self.policy.summary_output_tokens * 4)
        available = max(256, max_chars - len(prefix))
        if len(rendered) > available:
            separator = "\n[... middle of source history omitted ...]\n"
            side = max(1, (available - len(separator)) // 2)
            rendered = rendered[:side] + separator + rendered[-side:]
        return (prefix + rendered).strip()

    def _create_summary(self, messages_to_summarize: list[BaseMessage]) -> str:
        if not messages_to_summarize:
            raise RuntimeError("context compaction selected an empty history")
        prompt = self._summary_input(messages_to_summarize)
        for attempt in (1, 2):
            response = self.model.invoke(
                prompt if attempt == 1 else prompt + _EMPTY_SUMMARY_RETRY_SUFFIX,
                config={
                    "callbacks": list(self.summary_callbacks),
                    "metadata": {
                        "lc_source": "summarization",
                        "beliefkv_summary_attempt": attempt,
                    },
                },
            )
            summary = _response_checkpoint_text(response)
            if summary:
                return summary
        return self._fallback_checkpoint(messages_to_summarize)

    async def _acreate_summary(
        self, messages_to_summarize: list[BaseMessage]
    ) -> str:
        if not messages_to_summarize:
            raise RuntimeError("context compaction selected an empty history")
        prompt = self._summary_input(messages_to_summarize)
        for attempt in (1, 2):
            response = await self.model.ainvoke(
                prompt if attempt == 1 else prompt + _EMPTY_SUMMARY_RETRY_SUFFIX,
                config={
                    "callbacks": list(self.summary_callbacks),
                    "metadata": {
                        "lc_source": "summarization",
                        "beliefkv_summary_attempt": attempt,
                    },
                },
            )
            summary = _response_checkpoint_text(response)
            if summary:
                return summary
        return self._fallback_checkpoint(messages_to_summarize)

    def _is_new_summary(
        self, request: ModelRequest, modified_request: ModelRequest
    ) -> BaseMessage | None:
        candidate = _summary_message(modified_request.messages)
        if candidate is None:
            return None
        previous_event = request.state.get("_summarization_event")
        previous = (
            previous_event.get("summary_message")
            if isinstance(previous_event, dict)
            else None
        )
        if isinstance(previous, BaseMessage) and previous.text == candidate.text:
            return None
        return candidate

    def _record(
        self,
        request: ModelRequest,
        modified_request: ModelRequest,
        summary: BaseMessage,
    ) -> ContextCompactionRecord:
        text = summary.text or ""
        return ContextCompactionRecord(
            source_message_count=len(request.messages),
            retained_message_count=max(0, len(modified_request.messages) - 1),
            summary_chars=len(text),
            summary_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            trigger_tokens=self.policy.window_tokens,
            keep_tokens=self.policy.keep_tokens,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        request = self._restore_persistent_cursor(request)
        request = self._sanitize_summarization_event(request)
        self._remember_summarization_event(
            request.state.get("_summarization_event")
        )

        def guarded_handler(modified_request: ModelRequest) -> ModelResponse:
            summary = self._is_new_summary(request, modified_request)
            if summary is None:
                return handler(modified_request)
            record = self._record(request, modified_request, summary)
            with self.compaction_sink.stage_context_compaction(record):
                return handler(modified_request)

        response = super().wrap_model_call(request, guarded_handler)
        self._remember_response_event(response)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        request = self._restore_persistent_cursor(request)
        request = self._sanitize_summarization_event(request)
        self._remember_summarization_event(
            request.state.get("_summarization_event")
        )

        async def guarded_handler(modified_request: ModelRequest) -> ModelResponse:
            summary = self._is_new_summary(request, modified_request)
            if summary is None:
                return await handler(modified_request)
            record = self._record(request, modified_request, summary)
            with self.compaction_sink.stage_context_compaction(record):
                return await handler(modified_request)

        response = await super().awrap_model_call(request, guarded_handler)
        self._remember_response_event(response)
        return response


class CompletionBudgetMiddleware(AgentMiddleware[Any, Any, Any]):
    """Use short outputs for tool turns while preserving a larger final budget."""

    def __init__(
        self,
        *,
        intermediate_tokens: int,
        final_tokens: int,
        model_context_tokens: int,
        prompt_floor_tokens: int = 4_096,
        final_mode: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        if min(intermediate_tokens, final_tokens) <= 0:
            raise ValueError("completion token budgets must be positive")
        if intermediate_tokens > final_tokens:
            raise ValueError("intermediate output budget cannot exceed final budget")
        if final_tokens >= model_context_tokens:
            raise ValueError("completion budget must fit inside model context")
        if prompt_floor_tokens <= 0 or prompt_floor_tokens >= model_context_tokens:
            raise ValueError("prompt floor must fit inside model context")
        self.intermediate_tokens = intermediate_tokens
        self.final_tokens = final_tokens
        self.model_context_tokens = model_context_tokens
        self.prompt_floor_tokens = prompt_floor_tokens
        self.final_mode = final_mode or (lambda: False)

    def _request(self, request: ModelRequest) -> ModelRequest:
        settings = dict(request.model_settings)
        runtime_finalization = bool(
            request.state.get("guard_forcing_completion", False)
        )
        settings["max_tokens"] = (
            self.final_tokens
            if self.final_mode() or runtime_finalization
            else self.intermediate_tokens
        )
        return request.override(model_settings=settings)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._request(request))
