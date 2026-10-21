from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from beliefkv.runtime.action_frontier import StructuredActionKind
from scripts.audit_join_reentry_service import attach_service
from scripts.audit_tool_reentry_service import (
    audit_arm, main, summarize, tool_reentries,
)
from scripts import run_internal_comparison


def event(kind, when, *, invocation="agent", epoch=1, **values):
    return {
        "kind": kind, "ts_ms": when, "workflow_id": "wf",
        "invocation_id": invocation, "context_id": "ctx", "context_epoch": epoch,
        **values,
    }


def client_events(*, parallel=False):
    events = [
        event("workflow_start", 100.),
        event("llm_submit", 200., attributes={"request_id": "origin"}),
        event("llm_result", 220., attributes={
            "request_id": "origin", "tool_call_count": 2 if parallel else 1,
            "structured_action_kinds": [StructuredActionKind.FUNCTION_CALL.value] * (2 if parallel else 1),
        }),
        event("tool_start", 230., attributes={
            "tool_call_id": "call-1", "tool_run_id": "run-1", "tool_name": "shell",
        }),
        event("tool_end", 430., attributes={
            "tool_call_id": "call-1", "tool_run_id": "run-1",
        }),
        event("llm_submit", 500., epoch=2, attributes={"request_id": "next"}),
    ]
    if parallel:
        events.extend((
            event("tool_start", 240., attributes={
                "tool_call_id": "call-2", "tool_run_id": "run-2", "tool_name": "read_file",
            }),
            event("tool_end", 480., attributes={
                "tool_call_id": "call-2", "tool_run_id": "run-2",
            }),
        ))
    return events


def native_events():
    return [event("llm_submit", 100_000., epoch=2, attributes={
        "request_id": "next", "cached_tokens_host": 192, "cached_tokens_device": 8,
        "mamba_host_hit_slots": 1,
        "native_ingress_timing_ms": {"api_received_ts_ms": 99_970.},
    })]


def services():
    return [{
        "event": "gpu_service_sample", "service_start_ts_ms": 100_040.,
        "complete_ts_ms": 100_060., "request_samples": [{
            "request_id": "next", "workflow_id": "wf", "invocation_id": "agent",
            "context_id": "ctx", "context_epoch": 2,
        }],
    }]


@pytest.mark.parametrize("parallel", (False, True))
def test_independent_tool_round_keeps_client_and_native_clocks_separate(parallel):
    rows, anchor, diagnostics = tool_reentries(client_events(parallel=parallel))
    attach_service(rows, native_events(), services())
    assert anchor == 100.
    assert diagnostics["model_request_rounds"] == 2
    row = rows[0]
    assert row["status"] == "first_service_observed"
    assert row["tool_round_duration_ms"] == (250. if parallel else 200.)
    assert row["client_ready_to_submit_ms"] == (20. if parallel else 70.)
    assert row["native_queue_to_service_ms"] == 40.
    assert row["api_received_to_queue_ms"] == 30.
    assert row["api_received_to_service_ms"] == 70.
    assert "tool_to_service_ms" not in row
    summary = summarize(rows)
    assert summary["tool_request_rounds"] == 1
    assert summary["served_request_logical_host_hit_tokens"] == 192
    assert "h2d_bytes" not in summary


@pytest.mark.parametrize("field,value", (
    ("workflow_id", "other"), ("invocation_id", "other"), ("context_id", "other"),
    ("context_epoch", 3), ("context_epoch", True),
))
def test_tool_service_requires_the_complete_native_identity(field, value):
    rows, _, _ = tool_reentries(client_events())
    native = native_events()
    native[0][field] = value
    attach_service(rows, native, services())
    assert rows[0]["status"] == "native_identity_mismatch"
    assert "native_queue_to_service_ms" not in rows[0]


def test_service_identity_and_earliest_valid_launch_are_checked():
    rows, _, _ = tool_reentries(client_events())
    wrong = deepcopy(services()[0])
    wrong["service_start_ts_ms"] = 100_010.
    wrong["request_samples"][0]["invocation_id"] = "different-child"
    late = deepcopy(services()[0])
    late["service_start_ts_ms"], late["complete_ts_ms"] = 100_100., 100_120.
    attach_service(rows, native_events(), [wrong, late, *services()])
    assert rows[0]["native_queue_to_service_ms"] == 40.


@pytest.mark.parametrize("mutation,status", (
    ("missing_end", "tool_round_not_complete_in_prefix"),
    ("missing_successor", "no_successor_submit_in_prefix"),
    ("missing_result", "origin_result_unproven"),
    ("duplicate_result", "origin_result_unproven"),
    ("mismatched_result", "origin_result_unproven"),
    ("early_result", "origin_result_unproven"),
    ("missing_start", "tool_membership_unproven"),
    ("missing_count", "tool_membership_unproven"),
    ("duplicate_end", "tool_identity_ambiguous"),
    ("end_after_successor", "tool_round_overlaps_following_model_request"),
    ("end_before_start", "tool_end_identity_or_clock_mismatch"),
    ("wrong_call_id", "tool_end_identity_or_clock_mismatch"),
    ("internal_successor", "internal_request_before_agent_reentry"),
    ("same_request_id", "successor_identity_unproven"),
))
def test_incomplete_or_ambiguous_rounds_never_borrow_a_successor(mutation, status):
    events = client_events()
    if mutation == "missing_end":
        events.pop(4)
    elif mutation == "missing_successor":
        events.pop()
    elif mutation == "missing_result":
        events.pop(2)
    elif mutation == "duplicate_result":
        events.append(deepcopy(events[2]))
    elif mutation == "mismatched_result":
        events[2]["context_epoch"] = 2
    elif mutation == "early_result":
        events[2]["ts_ms"] = 190.
    elif mutation == "missing_start":
        events[2]["attributes"]["structured_action_kinds"].append(StructuredActionKind.FUNCTION_CALL.value)
    elif mutation == "missing_count":
        events[2]["attributes"] = {"request_id": "origin"}
    elif mutation == "duplicate_end":
        events.append(deepcopy(events[4]))
    elif mutation == "end_after_successor":
        events[4]["ts_ms"] = 510.
    elif mutation == "end_before_start":
        events[4]["ts_ms"] = 225.
    elif mutation == "wrong_call_id":
        events[4]["attributes"]["tool_call_id"] = "other-call"
    elif mutation == "internal_successor":
        events[-1]["attributes"]["runtime_internal"] = True
    else:
        events[-1]["attributes"]["request_id"] = "origin"
    rows, _, _ = tool_reentries(events)
    attach_service(rows, native_events(), services())
    assert len(rows) == 1
    assert rows[0]["status"] == status
    assert "request_id" not in rows[0]
    assert summarize(rows)["first_service_observed_requests"] == 0


@pytest.mark.parametrize("marker", ("spawn", "join_wait"))
def test_tool_rounds_mixed_with_join_dependencies_remain_separate(marker):
    events = client_events()
    if marker == "spawn":
        events[2]["attributes"]["structured_action_kinds"].append("spawn")
    else:
        events.append(event("join_wait", 450., join_id="prior-delegation"))
    rows, _, _ = tool_reentries(events)
    assert rows[0]["status"] == "mixed_tool_and_delegation_round"
    assert "client_ready_to_submit_ms" not in rows[0]


@pytest.mark.parametrize("kind,status", (
    ("return", "agent_terminal"), ("invocation_cancel", "agent_terminal"),
    ("workflow_end", "workflow_terminal"),
))
def test_terminal_agent_or_workflow_does_not_claim_later_service(kind, status):
    events = client_events()
    events.append(event(kind, 300.))
    rows, _, _ = tool_reentries(events)
    assert rows[0]["status"] == status
    assert "request_id" not in rows[0]


def test_compacted_successor_retains_actual_identity_without_assuming_prefix_reuse():
    events = client_events()
    events.append(event("context_compact", 450.))
    events[-2]["context_id"] = "new-context"
    events[-2]["context_epoch"] = 7
    rows, _, _ = tool_reentries(events)
    assert rows[0]["context_compactions_before_successor"] == 1
    assert rows[0]["context_id"] == "new-context"
    assert rows[0]["context_epoch"] == 7
    assert "full_reuse" not in rows[0]


@pytest.mark.parametrize("location", ("origin", "successor"))
def test_same_time_model_submits_are_ambiguous(location):
    events = client_events()
    duplicate = deepcopy(events[1 if location == "origin" else -1])
    duplicate["attributes"]["request_id"] = "duplicate"
    events.append(duplicate)
    rows, _, _ = tool_reentries(events)
    assert all(row["status"] != "submitted_no_native_service_in_prefix" for row in rows)
    assert not any("request_id" in row for row in rows)


def test_parallel_round_waits_for_every_tool_end():
    events = [row for row in client_events(parallel=True) if row["ts_ms"] != 480.]
    rows, _, _ = tool_reentries(events)
    assert rows[0]["status"] == "tool_round_not_complete_in_prefix"
    assert "client_ready_to_submit_ms" not in rows[0]


def test_tool_run_ids_disambiguate_repeated_call_ids_in_different_model_rounds():
    events = client_events()
    events.extend((
        event("llm_result", 520., epoch=2, attributes={
            "request_id": "next", "tool_call_count": 1,
            "structured_action_kinds": [StructuredActionKind.FUNCTION_CALL.value],
        }),
        event("tool_start", 530., attributes={
            "tool_call_id": "call-1", "tool_run_id": "run-2",
        }),
        event("tool_end", 540., attributes={
            "tool_call_id": "call-1", "tool_run_id": "run-2",
        }),
        event("llm_submit", 560., epoch=3, attributes={"request_id": "third"}),
    ))
    rows, _, _ = tool_reentries(events)
    assert [row["request_id"] for row in rows] == ["next", "third"]
    assert [row["tool_round_duration_ms"] for row in rows] == [200., 10.]


def test_tool_identity_is_scoped_to_the_actual_invocation_and_workflow():
    events = client_events()
    for label in ("child", "nested-child"):
        others = deepcopy(client_events())
        for row in others:
            row["invocation_id"] = label
            if row["kind"] in ("llm_submit", "llm_result"):
                row["attributes"]["request_id"] += f"-{label}"
        events.extend(others)
    rows, _, _ = tool_reentries(events)
    assert len(rows) == 3
    assert {row["invocation_id"] for row in rows} == {"agent", "child", "nested-child"}
    assert all(row["status"] == "submitted_no_native_service_in_prefix" for row in rows)


@pytest.mark.parametrize("internal", (False, True))
def test_unbound_starts_and_internal_rounds_remain_in_diagnostics(internal):
    events = client_events()
    if internal:
        events[1]["attributes"]["runtime_internal"] = True
    else:
        events = [row for row in events if row["kind"] not in ("llm_submit", "llm_result")]
    rows, _, counts = tool_reentries(events)
    assert not rows
    assert counts["internal_tool_rounds" if internal else "tool_starts_without_origin_submit"] == 1


def test_tool_cohort_uses_completion_and_keeps_unfinished_rounds():
    rows, _, _ = tool_reentries(client_events(), start_seconds=.33, end_seconds=.4)
    assert len(rows) == 1
    rows, _, _ = tool_reentries(client_events(), start_seconds=0., end_seconds=.33)
    assert not rows
    events = [row for row in client_events() if row["kind"] != "tool_end"]
    rows, _, _ = tool_reentries(events, start_seconds=0., end_seconds=.2)
    assert rows[0]["status"] == "tool_round_not_complete_in_prefix"


@pytest.mark.parametrize("prompt,bucket", (
    (0, "lt_8k"), (8191, "lt_8k"), (8192, "8k_to_32k"), (32767, "8k_to_32k"),
    (32768, "32k_to_64k"), (65535, "32k_to_64k"),
    (65536, "64k_to_128k"), (131071, "64k_to_128k"),
    (131072, "ge_128k"), (262144, "ge_128k"), (None, "unknown"), (True, "unknown"),
))
def test_context_strata_preserve_unserved_rounds_and_never_invent_prompt_lengths(prompt, bucket):
    rows, _, _ = tool_reentries(client_events())
    native = native_events()
    native[0]["attributes"]["prompt_tokens"] = prompt
    attach_service(rows, native, services())
    unserved, _, _ = tool_reentries(client_events()[:-1])
    report = summarize([*rows, *unserved])
    groups = report["by_prompt_tokens"]
    assert sum(group["tool_request_rounds"] for group in groups.values()) == 2
    assert sum(group["first_service_observed_requests"] for group in groups.values()) == 1
    assert groups[bucket]["native_queue_to_service_ms"]["count"] == 1
    assert groups["unknown"]["tool_request_rounds"] == (2 if bucket == "unknown" else 1)


def test_tool_callbacks_with_no_context_fields_are_bound_to_the_model_round():
    events = client_events()
    for row in events:
        if row["kind"] in ("tool_start", "tool_end"):
            row["context_id"] = row["context_epoch"] = None
    rows, _, _ = tool_reentries(events)
    attach_service(rows, native_events(), services())
    assert rows[0]["status"] == "first_service_observed"
    assert rows[0]["origin_context_id"] == "ctx"


@pytest.mark.parametrize("start,end", (
    (-1., None), (0., 0.), (0., float("nan")), (float("inf"), None),
))
def test_invalid_tool_cohort_is_rejected_before_reading_events(start, end):
    def forbidden():
        raise AssertionError("invalid bounds must not read the prefix")
        yield
    with pytest.raises(ValueError, match="cohort bounds"):
        tool_reentries(forbidden(), start_seconds=start, end_seconds=end)


def write_arm(tmp_path):
    client = tmp_path / "client_1/workflows/wf/runtime_events.deepagents.jsonl"
    server = tmp_path / "server"
    client.parent.mkdir(parents=True)
    server.mkdir()
    for path, rows in (
        (client, client_events()), (server / "runtime_events.sglang.jsonl", native_events()),
        (server / "runtime_audit.jsonl", services()),
    ):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return tmp_path


def test_arm_audit_needs_no_controller_or_prefetch_files(tmp_path):
    arm = write_arm(tmp_path)
    report = audit_arm(arm)
    assert report["summary"]["first_service_observed_requests"] == 1
    assert len(report["input_jsonl_prefixes"]) == 3
    assert not (arm / "opportunities").exists()


def test_tool_cli_records_sealed_prefixes_and_preserves_previous_output(tmp_path, monkeypatch):
    arm = write_arm(tmp_path / "arm")
    output = tmp_path / "tool.json"
    monkeypatch.setattr("sys.argv", [
        "audit", "--arm", f"native={arm}", "--output", str(output),
    ])
    main()
    report = json.loads(output.read_text())
    assert report["arms"]["native"]["summary"]["first_service_observed_requests"] == 1
    assert len(report["auditor_source_sha256"]) == 5
    assert report["gpu_performance_accepted"] is False
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main()
    assert output.read_bytes() == before


@pytest.mark.parametrize("mode", ("full", "join_priority_only", "native_matched"))
def test_campaign_postprocessing_audits_both_reentries_before_cleanup(tmp_path, monkeypatch, mode):
    run = tmp_path / "run"
    run.mkdir()
    execute = Mock()
    monkeypatch.setattr(run_internal_comparison.subprocess, "run", execute)
    run_internal_comparison.postprocess({"execution_checkout": str(tmp_path / "checkout")}, mode, run)
    commands = [call.args[0] for call in execute.call_args_list]
    names = [Path(command[1]).name for command in commands]
    for kind in ("join", "tool"):
        name = f"audit_{kind}_reentry_service.py"
        command = commands[names.index(name)]
        assert command[command.index("--arm") + 1] == f"{mode}={run / mode}"
        assert names.index(name) < names.index("summarize_semantic_h2d_ab.py")
