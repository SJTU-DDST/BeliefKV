from __future__ import annotations

from uuid import uuid4

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.command_class import execute_command_class
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class Sink:
    def __init__(self):
        self.events = []

    def emit_batch(self, events):
        self.events.extend(events)


def test_execute_command_categories_do_not_record_command_text():
    assert execute_command_class({
        "command": "cd /workspace && python tests/runtests.py tests.test_app"
    }) == "test_suite"
    assert execute_command_class({
        "command": "python -m pytest tests/test_app.py"
    }) == "test_suite"
    assert execute_command_class({
        "command": "python -c 'print(1)'"
    }) == "python_inline"
    assert execute_command_class({"command": "git status --short"}) == "git"
    assert execute_command_class({"command": "python 'unclosed"}) == "unparsed"
    assert execute_command_class({"command": "x" * 65_537}) == "execute"

    trace = Sink()
    adapter = DeepAgentsRuntimeAdapter(
        trace, BeliefKVRequestMetadata("wf", "root", "ctx", 0),
    )
    adapter.start()
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=uuid4(),
        inputs={"command": "python -m pytest confidential_private_test"},
    )
    events = [event for event in trace.events
              if event.kind is RuntimeEventKind.TOOL_START]
    assert len(events) == 1
    assert events[0].attributes["observed_command_class"] == "test_suite"
    assert "command_class" not in events[0].attributes
    assert "confidential_private_test" not in str(events[0].to_dict())
