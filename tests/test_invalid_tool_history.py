"""Malformed model arguments must not poison later requests or invent tool work."""

import json
from unittest.mock import Mock

import orjson
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai.chat_models.base import _convert_message_to_dict

from beliefkv.experiments.deepagents_swebench import EmptyReasoningRecoveryMiddleware


def history(*, valid_calls=True, raw_copy=False, blocks=False):
    invalid = {
        "id": "bad-call", "name": "task",
        "args": '{"description":"physics"},"description":"scripting"}',
        "type": "invalid_tool_call",
    }
    calls = [{
        "id": "valid-call", "name": "task", "type": "tool_call",
        "args": {"description": "implement command", "subagent_type": "implementation-agent"},
    }] if valid_calls else []
    assistant = AIMessage(
        content=[{"type": "text", "text": "original"}] if blocks else "original",
        id="assistant-output", tool_calls=calls, invalid_tool_calls=[invalid],
        additional_kwargs={"reasoning_content": "retained", **({
            "tool_calls": [
                {"id": "bad-call", "type": "function", "function": {
                    "name": "task", "arguments": invalid["args"],
                }},
            ],
        } if raw_copy else {})},
    )
    return [
        HumanMessage(content="Implement the task"), assistant,
        *([ToolMessage(content="actual child result", tool_call_id="valid-call")]
          if valid_calls else []),
    ]


def request(messages):
    return ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=messages, model_settings={"seed": 21, "max_tokens": 16384},
    )


@pytest.mark.parametrize("valid_calls", (False, True))
@pytest.mark.parametrize("raw_copy", (False, True))
@pytest.mark.parametrize("blocks", (False, True))
def test_malformed_history_becomes_text_while_valid_calls_and_original_graph_survive(
    valid_calls, raw_copy, blocks,
):
    messages = history(valid_calls=valid_calls, raw_copy=raw_copy, blocks=blocks)
    original = [message.model_dump() for message in messages]
    middleware = EmptyReasoningRecoveryMiddleware(audit=Mock(), scope="root")
    source = request(messages)
    with pytest.raises(orjson.JSONDecodeError):
        orjson.loads(_convert_message_to_dict(messages[1])["tool_calls"][-1]["function"]["arguments"])
    cleaned = middleware._serializable_history(source)
    payload = [_convert_message_to_dict(message) for message in cleaned.messages]
    assert cleaned is not source
    assert [message.model_dump() for message in source.messages] == original
    assert cleaned.model_settings == source.model_settings
    assert cleaned.messages[0] is messages[0]
    if valid_calls:
        assert cleaned.messages[-1] is messages[-1]
    assert cleaned.messages[1].tool_calls == messages[1].tool_calls
    assert cleaned.messages[1].invalid_tool_calls == []
    assert cleaned.messages[1].additional_kwargs == {"reasoning_content": "retained"}
    content = payload[1]["content"][-1]["text"] if blocks else payload[1]["content"]
    assert "not executed" in content
    assert json.dumps(messages[1].invalid_tool_calls[0]["args"]) in content
    actual_calls = payload[1].get("tool_calls", [])
    assert len(actual_calls) == int(valid_calls)
    for call in actual_calls:
        assert isinstance(orjson.loads(call["function"]["arguments"]), dict)
    assert sum(row["role"] == "tool" for row in payload) == int(valid_calls)
    assert middleware._serializable_history(cleaned) is cleaned
    middleware.audit.emit.assert_called_once()


def test_ordinary_history_returns_the_identical_request_without_audit_or_rewriting():
    source = request([
        HumanMessage(content="inspect"),
        AIMessage(content="done", tool_calls=[{
            "id": "call", "name": "execute", "args": {"command": "true"},
        }]),
        ToolMessage(content="actual result", tool_call_id="call"),
    ])
    middleware = EmptyReasoningRecoveryMiddleware(audit=Mock(), scope="child")
    assert middleware._serializable_history(source) is source
    middleware.audit.emit.assert_not_called()


def test_empty_terminal_retry_reuses_the_sanitized_history_without_mutating_graph():
    source = request(history())
    middleware = EmptyReasoningRecoveryMiddleware(audit=Mock(), scope="child")
    blank = ModelResponse(result=[AIMessage(content="", response_metadata={"finish_reason": "stop"})])
    complete = ModelResponse(result=[AIMessage(content="actual model report")])
    attempts = []

    def handle(current):
        attempts.append(current)
        assert current.messages[1].invalid_tool_calls == []
        return blank if len(attempts) == 1 else complete

    assert middleware.wrap_model_call(source, handle) is complete
    assert len(attempts) == 2
    assert attempts[1].messages is attempts[0].messages
    assert source.messages[1].invalid_tool_calls
    assert attempts[1].model_settings["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_mixed_valid_and_malformed_output_finishes_a_real_agent_without_400_or_extra_tools():
    class StrictHistoryModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            for message in messages:
                for call in _convert_message_to_dict(message).get("tool_calls", []):
                    assert isinstance(orjson.loads(call["function"]["arguments"]), dict)
            return super()._generate(messages, stop, run_manager, **kwargs)

    executions = []

    @tool
    def inspect_file(file_path: str) -> str:
        """Inspect a repository file."""
        executions.append(file_path)
        return "actual evidence"

    malformed = history()[1].model_copy(update={"tool_calls": [{
        "id": "valid-call", "name": "inspect_file", "type": "tool_call",
        "args": {"file_path": "/workspace/example.py"},
    }]})
    model = StrictHistoryModel(responses=[malformed, AIMessage(content="model's final report")])
    agent = create_agent(
        model=model, tools=[inspect_file],
        middleware=[EmptyReasoningRecoveryMiddleware(audit=Mock(), scope="root")],
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="inspect the issue")]},
        config={"recursion_limit": 20},
    )
    assert result["messages"][-1].content == "model's final report"
    assert executions == ["/workspace/example.py"]
    assert len([row for row in result["messages"] if isinstance(row, ToolMessage)]) == 1
    assert result["messages"][1].invalid_tool_calls == malformed.invalid_tool_calls
    assert result["messages"][1].content == malformed.content
