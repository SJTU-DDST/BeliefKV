import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.control.causal_graph import InvocationState, RuntimeCausalContextGraph
from beliefkv.experiments.causal_replay import (
    CausalReplayError, NativeReplayCompletion, evaluate_causal_plan, event_node_id, execute_causal_replay,
    request_node_id,
)
from beliefkv.experiments.harness_source import NATIVE_EVENTS, NATIVE_STATUS, NATIVE_TOKENS, register_harness_source
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.opencode_capture import (
    OpenCodeCaptureError, build_opencode_capture, compile_opencode_replay, notification_hash,
)
from beliefkv.runtime.replay_token_capture import capture_record
from beliefkv.traces.runtime_validation import read_jsonl, validate_runtime_trace
from scripts.audit_opencode_capture import audit
from scripts.run_causal_gpu_replay import source_plan
from scripts.run_opencode_capture import event_payload, task_result_inputs


def test_sse_frames_and_exact_task_result_hash():
    data = []
    assert event_payload(b"event: message\r\n", data) is None
    assert event_payload(b'data: {"a":\r\n', data) is None
    assert event_payload(b"data: 1}\n", data) is None
    assert json.loads(event_payload(b"\r\n", data)) == {"a": 1}
    assert not data
    result = '<task id="ses_test" state="completed">\n<task_result>\nexample\n</task_result>\n</task>'
    body = {"messages": [
        {"role": "assistant", "content": result + " other"},
        {"role": "user", "content": [{"type": "text", "text": result}]},
        {"role": "user", "content": "ordinary user prompt"},
    ]}
    assert task_result_inputs(body) == [notification_hash(result)]
    assert notification_hash(result) != notification_hash(result + " other")


def root_protocol():
    root = "ses_root"

    def event(ts, index, kind, **properties):
        return {
            "ts_ms": ts, "kind": "opencode_sse",
            "event": {"id": f"evt_{index}", "type": kind, "properties": properties},
        }

    return [
        {"ts_ms": 0., "kind": "root_session", "session": {"id": root, "agent": "build"}},
        {
            "ts_ms": 1., "kind": "provider_request", "request_id": "rid",
            "session_id": root, "parent_session_id": None, "user_message_id": "msg_user", "model": "fixture",
        },
        {
            "ts_ms": 2., "kind": "provider_done", "request_id": "rid",
            "finish_reason": "stop", "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
        event(3., 1, "message.updated", info={
            "role": "assistant", "id": "msg_assistant", "sessionID": root, "parentID": "msg_user",
            "finish": "stop", "time": {"completed": 3},
        }),
        event(4., 2, "session.status", sessionID=root, status={"type": "idle"}),
        {"ts_ms": 5., "kind": "capture_end", "session_id": root},
    ]


def test_provider_parent_mismatch_and_missing_done_fail_closed():
    rows = root_protocol()
    rows[1]["parent_session_id"] = "ses_other"
    with pytest.raises(OpenCodeCaptureError, match="parent headers"):
        build_opencode_capture(rows, "fixture")
    rows = root_protocol()
    with pytest.raises(OpenCodeCaptureError, match="incomplete provider"):
        build_opencode_capture(rows[:2] + rows[3:], "fixture")


def test_native_audit_uses_original_tokens_and_finish_reason(tmp_path):
    result = build_opencode_capture(root_protocol(), "fixture")
    with (tmp_path / "opencode-events.jsonl").open("w") as stream:
        for event in result.events:
            stream.write(json.dumps(event.to_dict()) + "\n")
    summary = tmp_path / "opencode-summary.json"
    summary.write_text(json.dumps({"scenario": "foreground", "monotonic_clock_domain": "fixture"}))
    with pytest.raises(ValueError, match="real provider capture"):
        audit(tmp_path, tmp_path)
    summary.write_text(json.dumps({"scenario": None, "monotonic_clock_domain": "fixture"}))
    (tmp_path / "native_telemetry_status.json").write_text(json.dumps({
        "replay_token_capture": True, "writer_error": None, "failed_records": 0,
        "dropped_records": 0, "writer_queue_depth": 0, "pending_batch_count": 0,
    }))
    native = tmp_path / "runtime_events.sglang.jsonl"
    native.write_text("".join(json.dumps(row) + "\n" for row in result.provider_boundaries))
    with pytest.raises(ValueError, match="SGLang telemetry"):
        audit(tmp_path, tmp_path)
    for row in result.provider_boundaries:
        row["attributes"].pop("completion_source", None)
    native.write_text("".join(json.dumps(row) + "\n" for row in result.provider_boundaries))
    records = []
    for boundary in result.provider_boundaries:
        submit = boundary["kind"] == "llm_submit"
        records.append(capture_record(
            {
                **boundary, "request_id": "rid",
                "_replay_request_parameters": {
                    "max_new_tokens": 8, "min_new_tokens": 0, "ignore_eos": False,
                    "stop": None, "stop_regex": None, "stop_token_ids": None,
                },
                "_replay_finish_reason": {"type": "length"},
            },
            [13, 29] if submit else [7], event="request_prompt" if submit else "request_output",
        ))
    tokens = tmp_path / "request_tokens.jsonl"
    tokens.write_text("".join(json.dumps(row) + "\n" for row in records))
    report = audit(tmp_path, tmp_path)
    assert report["graph"]["native_completion_boundaries_verified"]
    assert report["tokens"]["original_finish_reasons_complete"]
    records[-1]["native_finish_reason"] = "stop"
    tokens.write_text("".join(json.dumps(row) + "\n" for row in records))
    with pytest.raises(ValueError, match="original non-aborted finish reason"):
        audit(tmp_path, tmp_path)


@pytest.mark.parametrize(
    "scenario,children,joined,messages,cancelled",
    [
        ("foreground", 2, 2, 0, 0),
        ("mixed", 2, 1, 1, 0),
        ("background", 2, 0, 2, 0),
        ("promote", 1, 0, 1, 0),
        ("cancel", 0, 0, 0, 1),
        ("cancel-task", 1, 1, 0, 2),
    ],
)
def test_pinned_opencode_workflow(tmp_path, scenario, children, joined, messages, cancelled):
    output, summary = pinned_capture(tmp_path, scenario)
    events = tuple(RuntimeEvent.from_dict(row) for row in read_jsonl(output / "opencode-events.jsonl"))
    select = lambda kind: [event for event in events if event.kind is kind]
    root = next(event.invocation_id for event in select(Kind.INVOCATION_CREATE) if not event.parent_invocation_id)
    spawns = select(Kind.SPAWN)
    assert len({event.target_invocation_id for event in spawns}) == children
    assert len(select(Kind.MESSAGE)) == messages
    assert len(select(Kind.INVOCATION_CANCEL)) == cancelled
    waits = select(Kind.JOIN_WAIT)
    assert [len(event.member_invocation_ids) for event in waits] == ([joined] if joined else [])
    assert not any("native_finish_reason" in event.attributes for event in events)
    rows = read_jsonl(output / "opencode-protocol.jsonl")
    requests = [row for row in rows if row["kind"] == "provider_request"]
    assert {row["request_id"] for row in requests} == {event.attributes["request_id"] for event in select(Kind.LLM_SUBMIT)}
    assert {row["native_metadata"]["invocation_id"] for row in requests} == {
        event.invocation_id for event in select(Kind.LLM_SUBMIT)
    }
    if cancelled:
        [censored] = select(Kind.CALL_CENSORED)
        assert censored.attributes["transport_reason"] == "downstream_disconnected"
        assert censored.attributes["request_id"] not in {event.attributes["request_id"] for event in select(Kind.LLM_RESULT)}
        assert "abort_ack" in summary["protocol_records"]
        assert not select(Kind.JOIN_SATISFIED)
        assert not select(Kind.JOIN_TIMEOUT)
        assert summary["replay_status"] == "censored_provider_request"
        return
    assert validate_runtime_trace(output / "opencode-events.jsonl").controller_replay_valid
    assert len(select(Kind.JOIN_SATISFIED)) == bool(joined)
    if scenario == "promote":
        assert [event.attributes["action"] for event in select(Kind.STRUCTURED_ACTION)] == [
            "task_promoted_to_background",
        ]
    assert all(event.attributes["duration_ms"] >= 0. for event in select(Kind.TOOL_END))
    assert all(event.attributes["opencode_wall_duration_ms"] >= 0. for event in select(Kind.TOOL_END))
    provider = read_jsonl(output / "opencode-provider-boundaries.jsonl")
    plan = compile_opencode_replay(events, provider)
    graph = plan.dependency_graph()
    done = {event.invocation_id: event for event in select(Kind.RETURN)}
    submitted = select(Kind.LLM_SUBMIT)
    fixture = evaluate_causal_plan(plan, {
        event.attributes["request_id"]: 2000. if event.invocation_id != root else 10. for event in submitted
    })["node_completed_ms"]

    def node(event):
        return event_node_id(event.event_id)

    for end in select(Kind.TOOL_END):
        child_done = node(done[end.attributes["opencode_child_invocation_id"]])
        if end.attributes["task_release"] == "foreground":
            assert child_done in graph[node(end)]
            assert fixture[child_done] <= fixture[node(end)]
        else:
            assert child_done not in graph[node(end)]
            assert fixture[node(end)] < fixture[child_done]
    for message in select(Kind.MESSAGE):
        earliest = next(
            event for event in submitted if event.invocation_id == root
            and message.attributes["content_sha256"] in event.attributes["opencode_task_result_inputs"]
        )
        assert graph[node(message)] == (node(done[message.invocation_id]),)
        assert node(message) in graph[node(earliest)]
        assert fixture[node(earliest)] >= fixture[node(message)]
    if scenario == "mixed":
        badly_bound = tuple(
            replace(event, attributes={**event.attributes, "content_sha256": "foreign"})
            if event.kind is Kind.MESSAGE else event for event in events
        )
        with pytest.raises(CausalReplayError, match="matching parent request"):
            compile_opencode_replay(badly_bound, provider)
        emitted = []

        async def backend(rid, submit):
            return NativeReplayCompletion(
                rid, time.monotonic() * 1000., local_monotonic_clock_domain(), *plan.request_usage[rid],
            )

        async def append(event):
            emitted.append(event)

        instantaneous = replace(plan, nodes=tuple(
            replace(node, delay_ms=0.) for node in plan.nodes
        ))
        asyncio.run(execute_causal_replay(instantaneous, backend, append))
        assert {event.event_id for event in emitted} == {event.event_id for event in events}


def pinned_capture(tmp_path, scenario):
    binary = os.environ.get("OPENCODE_CAPTURE_BINARY")
    if not binary:
        pytest.skip("set OPENCODE_CAPTURE_BINARY to the pinned release executable")
    output = tmp_path / "capture"
    completed = subprocess.run(
        [
            sys.executable, "-m", "scripts.run_opencode_capture",
            "--binary", binary, "--workspace", str(tmp_path), "--output", str(output),
            "--fake", scenario, "--timeout", "15",
        ],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=35,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return output, json.loads(completed.stdout)


def register_cpu_fixture(output, summary, workflow):
    native = read_jsonl(output / "opencode-provider-boundaries.jsonl")
    (output / NATIVE_EVENTS).parent.mkdir()
    tokens = []
    for row in native:
        row["attributes"].pop("completion_source", None)
        submit = row["kind"] == "llm_submit"
        count = row["attributes"]["prompt_tokens" if submit else "output_tokens"]
        tokens.append(capture_record({
            **row, "request_id": row["attributes"]["request_id"],
            "_replay_request_parameters": {
                "max_new_tokens": 15, "min_new_tokens": 0, "ignore_eos": False,
                "stop": None, "stop_regex": None, "stop_token_ids": None,
            },
            "_replay_finish_reason": {"type": "length"},
        }, [71] * count, event="request_prompt" if submit else "request_output"))
    for name, rows in ((NATIVE_EVENTS, native), (NATIVE_TOKENS, tokens)):
        (output / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
    (output / NATIVE_STATUS).write_text(json.dumps({
        "replay_token_capture": True, "writer_error": None, "failed_records": 0,
        "dropped_records": 0, "writer_queue_depth": 0, "pending_batch_count": 0,
    }))
    evidence = register_harness_source(
        output, harness="opencode", harness_revision=summary["revision"],
        dataset="cpu-fixture", dataset_revision="fixture",
        client_event_files=["opencode-events.jsonl"], workflow_task_ids={workflow: "fixture"},
    )
    assert "opencode-protocol.jsonl" in evidence["artifacts_sha256"]
    plan, _, _ = source_plan(output)
    return plan


@pytest.mark.parametrize("scenario", ["reuse", "stream-tool"])
def test_pinned_nested_task_reuses_context_or_follows_published_progress(tmp_path, scenario):
    output, summary = pinned_capture(tmp_path, scenario)
    path = output / "opencode-events.jsonl"
    events = tuple(RuntimeEvent.from_dict(row) for row in read_jsonl(path))
    assert validate_runtime_trace(path).controller_replay_valid
    plan = register_cpu_fixture(output, summary, events[0].workflow_id)
    graph = plan.dependency_graph()
    creates = [event for event in events if event.kind is Kind.INVOCATION_CREATE and event.parent_invocation_id]
    if scenario == "reuse":
        first, second = creates
        assert first.context_id == second.context_id and first.invocation_id != second.invocation_id
        assert second.attributes["resumed"] is True
        earlier_return = next(
            event for event in events if event.kind is Kind.RETURN and event.invocation_id == first.invocation_id
        )
        assert event_node_id(earlier_return.event_id) in graph[event_node_id(second.event_id)]
        child_requests = [
            event for event in events if event.kind is Kind.LLM_SUBMIT and event.context_id == first.context_id
        ]
        assert [event.context_epoch for event in child_requests] == [0, 1]
        assert [event.invocation_id for event in child_requests] == [first.invocation_id, second.invocation_id]
        assert not plan.output_boundaries()
        return

    [tool_start] = [event for event in events if event.kind is Kind.TOOL_START]
    parent_rid = tool_start.attributes["request_id"]
    boundary = tool_start.attributes["model_output_boundary"]
    [child] = creates
    child_request = next(
        event for event in events if event.kind is Kind.LLM_SUBMIT and event.invocation_id == child.invocation_id
    )
    parent_result = next(
        event for event in events if event.kind is Kind.LLM_RESULT and event.attributes["request_id"] == parent_rid
    )
    assert boundary["output_tokens"] == 5
    assert boundary["published_ms"] < tool_start.ts_ms < child_request.ts_ms < parent_result.ts_ms
    [boundary_node] = plan.output_boundaries()[parent_rid].values()
    assert graph[event_node_id(tool_start.event_id)] == (boundary_node,)
    assert request_node_id(parent_rid) not in graph[event_node_id(child.event_id)]
    rows = read_jsonl(output / "opencode-protocol.jsonl")
    altered = [
        {**row, "usage": None} if row["kind"] == "provider_tool_delta" else row for row in rows
    ]
    with pytest.raises(OpenCodeCaptureError, match="cumulative output evidence"):
        build_opencode_capture(altered, "fixture")
    with pytest.raises(OpenCodeCaptureError, match="matching input frame"):
        build_opencode_capture(
            [row for row in rows if row["kind"] != "provider_tool_delta"], "fixture",
        )

    state = RuntimeCausalContextGraph()
    for event in events:
        state.apply(event)
        if event.event_id == tool_start.event_id:
            assert state.invocations[tool_start.invocation_id].state is InvocationState.RUNNING_LLM
    replay = replace(plan, nodes=tuple(replace(node, delay_ms=0.) for node in plan.nodes))

    async def execute():
        child_started = asyncio.Event()
        observed = []

        async def backend(rid, _submit):
            if rid == parent_rid:
                raise AssertionError("parent requires streaming")
            if rid == child_request.attributes["request_id"]:
                child_started.set()
            return receipt(rid)

        def receipt(rid):
            return NativeReplayCompletion(
                rid, time.monotonic() * 1000., local_monotonic_clock_domain(), *plan.request_usage[rid],
            )

        async def stream(rid, _submit, publish):
            publish(2, time.monotonic() * 1000.)
            await asyncio.sleep(0)
            assert tool_start.event_id not in observed
            publish(5, time.monotonic() * 1000.)
            await asyncio.wait_for(child_started.wait(), timeout=2.)
            assert tool_start.event_id in observed and parent_result.event_id not in observed
            return receipt(rid)

        async def emit(event):
            observed.append(event.event_id)

        report = await execute_causal_replay(replay, backend, emit, execute_streaming_request=stream)
        assert report["completed_requests"] == len(plan.request_usage)

        async def missing(rid, _submit, publish):
            publish(2, time.monotonic() * 1000.)
            return receipt(rid)

        with pytest.raises(CausalReplayError, match="omitted a required"):
            await execute_causal_replay(replay, backend, emit, execute_streaming_request=missing)

    asyncio.run(execute())
