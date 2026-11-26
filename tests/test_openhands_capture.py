from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
import json

import pytest

from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import CausalReplayError, evaluate_causal_plan, event_node_id
from beliefkv.runtime.openhands_capture import OPENHANDS_VERSION, compile_openhands_capture
from scripts.run_openhands_capture import EVENTS_FILENAME, FIXTURE_FILENAME, smoke


def _delegate_capture():
    events = []
    native = []

    def mark(kind, ts, owner="root", attributes=None, **fields):
        event = RuntimeEvent(
            str(len(events)), ts, kind, "wf", invocation_id=owner,
            context_id=owner, context_epoch=0, attributes={
                "source": "openhands_sdk", **(attributes or {}),
            }, **fields,
        )
        events.append(event)
        return event

    def model(kind, ts, owner, rid, *, complete=None):
        event = mark(kind, ts, owner, {"request_id": rid})
        row = event.to_dict()
        if kind is Kind.LLM_SUBMIT:
            row["attributes"].update(prompt_tokens=8)
        else:
            row["attributes"].update(
                output_tokens=2, complete_monotonic_ms=complete, monotonic_clock_domain="fixture",
            )
        native.append(row)

    mark(Kind.WORKFLOW_START, 0)
    mark(Kind.INVOCATION_CREATE, 1)
    model(Kind.LLM_SUBMIT, 2, "root", "r0")
    model(Kind.LLM_RESULT, 4, "root", "r0", complete=3)
    delegate = {"tool_run_id": "delegation", "sdk_delegate_command": "delegate"}
    tool_start = mark(Kind.TOOL_START, 5, attributes=delegate)
    child = mark(Kind.INVOCATION_CREATE, 6, "child", {"delegate_tool_run_id": "delegation"},
                 parent_invocation_id="root")
    mark(Kind.SPAWN, 7, target_invocation_id="child")
    model(Kind.LLM_SUBMIT, 8, "child", "r1")
    join = {"join_id": "join", "member_invocation_ids": ("child",)}
    join_attributes = {"delegate_tool_run_id": "delegation", "mode": "all"}
    mark(Kind.JOIN_CREATE, 9, attributes=join_attributes, **join)
    mark(Kind.JOIN_WAIT, 10, attributes=join_attributes, **join)
    model(Kind.LLM_RESULT, 12, "child", "r1", complete=11)
    mark(Kind.RETURN, 13, "child")
    joined = mark(Kind.JOIN_SATISFIED, 14, attributes=join_attributes, **join)
    ended = mark(Kind.TOOL_END, 15, attributes=delegate)
    next_tool = mark(Kind.TOOL_START, 16, attributes={"tool_run_id": "file"})
    mark(Kind.TOOL_END, 17, attributes={"tool_run_id": "file"})
    mark(Kind.RETURN, 18)
    mark(Kind.WORKFLOW_END, 19)
    return events, native, (tool_start, child, joined, ended, next_tool)


def test_serial_tool_and_native_join_dependencies():
    events, native, boundaries = _delegate_capture()
    tool_start, child, joined, ended, next_tool = [event_node_id(event.event_id) for event in boundaries]
    plan = compile_openhands_capture(events, native)
    nodes = {node.node_id: node for node in plan.nodes}
    assert tool_start in nodes[child].dependencies
    assert joined in nodes[ended].dependencies
    assert nodes[ended].delay_ms == 1
    assert ended in nodes[next_tool].dependencies
    fast = evaluate_causal_plan(plan, {"r0": 0, "r1": 0})["node_completed_ms"]
    slow = evaluate_causal_plan(plan, {"r0": 0, "r1": 100})["node_completed_ms"]
    assert slow[next_tool] > fast[next_tool] + 90


def test_missing_join_and_foreign_native_identity_fail_closed():
    events, native, _ = _delegate_capture()
    without_join = [event for event in events if event.join_id is None]
    with pytest.raises(CausalReplayError, match="without its observed join"):
        compile_openhands_capture(without_join, native)
    native[-1]["context_id"] = "wrong"
    with pytest.raises(CausalReplayError, match="change identity"):
        compile_openhands_capture(events, native)


def test_censored_child_is_not_a_complete_capture():
    events, native, _ = _delegate_capture()
    events = [
        replace(event, kind=Kind.INVOCATION_CANCEL)
        if event.kind is Kind.RETURN and event.invocation_id == "child" else event
        for event in events
    ]
    with pytest.raises(CausalReplayError, match="unfinished invocation"):
        compile_openhands_capture(events, native)


def test_pinned_sdk_localhost(tmp_path):
    for package in ("openhands-sdk", "openhands-tools"):
        try:
            installed = version(package)
        except PackageNotFoundError:
            pytest.skip("OpenHands v1.14.0 environment is optional")
        if installed != OPENHANDS_VERSION:
            pytest.skip("wrong OpenHands version for the pinned SDK smoke")
    output = tmp_path / "openhands"
    receipt = smoke(output)
    assert receipt["requests"] == 12 and receipt["event_counts"]["join_wait"] == 2
    assert receipt["fixture_completion_boundaries_verified"]
    assert not receipt["native_completion_boundaries_verified"]
    assert not receipt["token_evidence_complete"]
    events = [RuntimeEvent.from_dict(json.loads(line))
              for line in (output / EVENTS_FILENAME).read_text().splitlines()]
    native = [json.loads(line) for line in (output / FIXTURE_FILENAME).read_text().splitlines()]
    assert compile_openhands_capture(events, native).summary()["requests"] == 12
    tool_start_ids = {event.attributes["tool_run_id"] for event in events
                      if event.kind is Kind.TOOL_START}
    observed_ids = {event.attributes["sdk_action_id"] for event in events
                    if event.attributes.get("sdk_event_type") == "ObservationEvent"}
    assert observed_ids == tool_start_ids
    assert sum(event.attributes.get("sdk_action_validated") is False for event in events) == 1
    graph = RuntimeCausalContextGraph()
    for event in events:
        graph.apply(event)
        if event.kind is Kind.JOIN_WAIT and len(event.member_invocation_ids) == 2:
            assert graph.invocations[event.invocation_id].state is InvocationState.WAIT_JOIN
    assert len(graph.invocations) == 4 and len(graph.contexts) == 3
    assert all(invocation.state is InvocationState.DONE for invocation in graph.invocations.values())
