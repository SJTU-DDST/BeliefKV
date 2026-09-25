from __future__ import annotations

import json
from uuid import uuid4

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.command_class import (
    execute_command_class, execute_command_shape,
)
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from scripts.audit_native_execute_child_pilot import audit


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
    assert events[0].attributes["observed_command_shape"] == "test_suite_targeted"
    assert events[0].attributes["is_child"] is False
    assert "command_class" not in events[0].attributes
    assert "confidential_private_test" not in str(events[0].to_dict())

    task = adapter.declare_runtime_tasks(
        [("explorer", "private task")], group_id="command-class-child"
    )[0]
    task_run = uuid4()
    adapter.on_tool_start(
        {"name": "task"}, "", run_id=task_run,
        inputs={"subagent_type": "explorer", "description": "private task"},
        tool_call_id=task.tool_call_id,
    )
    adapter.on_tool_start(
        {"name": "execute"}, "", run_id=uuid4(), parent_run_id=task_run,
        inputs={"command": "pytest tests/example.py"},
    )
    child = [event for event in trace.events
             if event.kind is RuntimeEventKind.TOOL_START][-1]
    assert child.attributes["is_child"] is True
    assert child.attributes["observed_command_class"] == "test_suite"
    assert child.attributes["observed_command_shape"] == "test_suite_targeted"


def test_command_shape_distinguishes_python_and_test_structure():
    assert execute_command_shape({
        "command": "python -m pytest tests/test_app.py",
    }) == "test_suite_targeted"
    assert execute_command_shape({
        "command": "python tests/runtests.py --help",
    }) == "test_suite_metadata"
    assert execute_command_shape({
        "command": "python tests/runtests.py",
    }) == "test_suite_full"
    assert execute_command_shape({
        "command": "python -c 'print(1)'",
    }) == "python_inline_simple"
    assert execute_command_shape({
        "command": "python -c 'import time; time.sleep(5)'",
    }) == "python_inline_wait"
    assert execute_command_shape({
        "command": "python -c 'import subprocess; subprocess.run([\"pytest\"])'",
    }) == "python_inline_subprocess"
    assert execute_command_shape({
        "command": "python -c 'from django.test import TestCase\n"
                   "class Case(TestCase): pass'",
    }) == "python_inline_test"
    assert execute_command_shape({
        "command": "python -c 'import django; django.setup()'",
    }) == "python_inline_framework"
    assert execute_command_shape({
        "command": "python -c 'not : syntax'",
    }) == "python_inline_unparsed"


def test_execute_audit_pairs_child_tools_and_ignores_open_calls(tmp_path):
    workflows = tmp_path / "workflows" / "astropy__sample"
    workflows.mkdir(parents=True)
    events = [
        {"kind": "tool_start", "ts_ms": 100, "workflow_id": "wf",
         "invocation_id": "deepagents-invocation:child", "attributes": {
             "tool_name": "execute", "tool_call_id": "one",
             "observed_command_class": "test_suite"}},
        {"kind": "tool_end", "ts_ms": 2900, "workflow_id": "wf",
         "invocation_id": "deepagents-invocation:child", "attributes": {
             "tool_name": "execute", "tool_call_id": "one"}},
        {"kind": "tool_start", "ts_ms": 3000, "workflow_id": "wf",
         "invocation_id": "root", "attributes": {
             "tool_name": "execute", "tool_call_id": "open"}},
    ]
    (workflows / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    medians = tmp_path / "medians.json"
    medians.write_text(json.dumps({
        "status": "offline_exploration_not_deployable",
        "global_median_ms": 100,
        "class_median_ms": {"test_suite": 3000},
    }), encoding="utf-8")
    report = audit(tmp_path / "workflows", medians)
    assert report["unpaired_execute_calls"] == 1
    assert report["child"]["matched_execute_calls"] == 1
    assert report["child"]["class_p50_absolute_error_ms"] == 200
    assert report["root"]["matched_execute_calls"] == 0
