"""Native model smoke contract, using a fake server response."""

from __future__ import annotations

import json
import sys

import pytest

from scripts import smoke_qwen35_native_v0520 as smoke


def test_native_chat_disables_thinking_for_bounded_smoke(monkeypatch) -> None:
    seen = []

    def request(_url, payload, _timeout):
        seen.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "ready"}}]}

    monkeypatch.setattr(smoke, "_request", request)
    smoke._chat("http://localhost:18000", "Qwen3.5-35B-A3B", [], 5)
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_tool_round_trip_keeps_assistant_call_identity(monkeypatch, capsys) -> None:
    messages_seen: list[list[dict]] = []
    responses = iter(
        [
            {"role": "assistant", "content": "Hello."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-one",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"text":"ready"}',
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "ready"},
        ]
    )

    def chat(_url, _model, messages, _timeout, **_extra):
        messages_seen.append(list(messages))
        return next(responses)

    monkeypatch.setattr(smoke, "_chat", chat)
    monkeypatch.setattr(sys, "argv", ["smoke_qwen35_native_v0520.py"])
    smoke.main()
    assert messages_seen[2][1]["tool_calls"][0]["id"] == "call-one"
    assert messages_seen[2][2] == {
        "role": "tool",
        "tool_call_id": "call-one",
        "content": "ready",
    }
    assert json.loads(capsys.readouterr().out)["native_smoke"] == "passed"


def test_missing_tool_call_is_a_smoke_failure(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["smoke_qwen35_native_v0520.py"])
    monkeypatch.setattr(
        smoke,
        "_chat",
        lambda *_args, **_kwargs: {"role": "assistant", "content": "no tool"},
    )
    with pytest.raises(RuntimeError, match="parsed native tool call"):
        smoke.main()
