import json
import sys

import pytest

from scripts.summarize_semantic_h2d_ab import (
    cleanup_workspaces, main, paired_trajectory_report, summarize,
    workflow_trajectory_audit, workload_balance,
)


def fixture(arm):
    client = arm / "client_36"
    server = arm / "server"
    opportunities = arm / "opportunities"
    for path in (client, server, opportunities):
        path.mkdir(parents=True)
    (client / "summary.json").write_text(json.dumps({
        "workflow_count": 1, "completed_workflows": 1, "duration_seconds": 10.,
        "llm_request_count": 1, "tool_call_count": 1,
        "workflows": [{
            "instance_id": "task", "outcome": "completed", "duration_seconds": 5.,
            "sandbox_cleanup_status": "completed", "artifact_collection": {"errors": []},
        }],
    }))
    (client / "gpu_samples.csv").write_text(
        "gpu_utilization_percent\n50\n"
    )
    (server / "native_capacity_census.json").write_text(json.dumps({"capacity": {
        "device_full_bytes": 1000, "device_mamba_bytes": 900,
    }}))
    (server / "native_telemetry_status.json").write_text(json.dumps({
        "request_cache_evidence": {"all": {"prompt_tokens": 100}},
    }))
    (opportunities / "admission_opportunities.jsonl").write_text(json.dumps({
        "event": "admission_runtime_state", "physical_disabled": False,
        "prepare_host": False, "final_stage_priority": True,
        "final_stage_prefetch": True, "semantic_worker_configured": True,
        "counts": {"semantic_worker_ready": 1},
    }) + "\n")
    (server / "runtime_events.sglang.jsonl").write_text(
        json.dumps({"kind": "llm_submit", "ts_ms": 10,
                    "attributes": {"request_id": "r", "prompt_tokens": 100}}) + "\n"
        + json.dumps({"kind": "llm_result", "ts_ms": 20,
                      "attributes": {"request_id": "r", "output_tokens": 20}}) + "\n"
    )
    return client, server


def test_ack_bytes_are_not_all_counted_as_verified_reuse(tmp_path):
    arm = tmp_path / "predictive_h2d"
    _, server = fixture(arm)
    (server / "physical_action_ack.jsonl").write_text(json.dumps({
        "command_id": "cmd", "action": "PREFETCH_GPU", "node_ids": [1],
        "num_bytes": 200, "pool_bytes": {"kv": 100, "mamba": 100},
    }) + "\n")
    (server / "physical_action_use.jsonl").write_text(json.dumps({
        "event": "beliefkv_prefetch_first_service", "command_id": "cmd",
        "full_node_reused": True, "reused_full_node_ids": [1],
        "ack_ts_ms": 10., "first_service_ts_ms": 20.,
    }) + "\n")
    result = summarize(arm)
    assert result["verified_full_reused_bytes"] == 100
    assert result["mamba_first_service_unverified_bytes"] == 100
    assert result["predictive_h2d_ack_bytes"] == 200
    assert result["ack_to_first_service_byte_seconds_upper_bound"] == 2.
    assert result["submitted_input_tokens"] == 100
    assert result["cache_evidence"]["prompt_tokens"] == 100


def test_cleanup_only_removes_archived_completed_workspace(tmp_path):
    arm = tmp_path / "reactive"
    client, _ = fixture(arm)
    base = client / "workflows/task"
    (base / "workspace").mkdir(parents=True)
    (base / "workspace/file").write_text("data")
    (base / "model.patch").write_text("patch")
    result = cleanup_workspaces(arm)
    assert len(result["removed_workspaces"]) == 1
    assert (base / "model.patch").read_text() == "patch"
    assert not (base / "workspace").exists()


def test_mamba_reuse_requires_matching_forward_proof_and_is_not_double_counted(tmp_path):
    arm = tmp_path / "predictive_h2d"
    _, server = fixture(arm)
    (server / "physical_action_ack.jsonl").write_text(json.dumps({
        "command_id": "cmd", "action": "PREFETCH_GPU", "node_ids": [1],
        "num_bytes": 200, "pool_bytes": {"kv": 100, "mamba": 100},
    }) + "\n")
    first = {
        "event": "beliefkv_prefetch_first_service", "command_id": "cmd",
        "request_id": "r", "context_id": "c", "context_epoch": 0,
        "full_node_reused": False, "reused_full_node_ids": [],
        "ack_ts_ms": 10., "first_service_ts_ms": 20.,
    }
    forward = {
        **first, "event": "beliefkv_prefetch_mamba_forward_completed",
        "node_id": 1, "mamba_reuse": "verified_single_request_cow_forward_completed",
    }
    path = server / "physical_action_use.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in (
        first, {**forward, "request_id": "other"},
    )) + "\n")
    result = summarize(arm)
    assert result["verified_mamba_forward_bytes"] == 0
    path.write_text("\n".join(json.dumps(row) for row in (
        first, forward, forward,
    )) + "\n")
    result = summarize(arm)
    assert result["verified_mamba_forward_count"] == 1
    assert result["verified_mamba_forward_bytes"] == 100
    assert result["mamba_first_service_unverified_bytes"] == 0
    assert result["full_first_service_outcomes"] == {
        "reused": 0, "not_reused": 1, "unknown": 0,
    }


def test_initialize_records_explicit_long_budget_and_manifest_selection(tmp_path, monkeypatch):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [
        {"instance_id": "z"}, {"instance_id": "a"}, {"instance_id": "b"},
    ]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "2", "--arm-order", "predictive_h2d",
        "--semantic-artifact", str(artifact),
        "--activation-wall-clock-seconds", "21600",
        "--workload-manifest", str(manifest),
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert plan["activation_wall_clock_seconds"] == 21600
    assert plan["workload_instance_ids_in_manifest_order"] == ["z", "a"]
    assert plan["workflow_arrival_batch_size"] == 0
    assert plan["order"] == ["predictive_h2d"]
    assert "no throughput comparison" in plan["scope"]


def test_initialize_accepts_exact_84_roots_and_records_live_fairness_scope(tmp_path, monkeypatch):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [
        {"instance_id": f"task-{index}"} for index in range(128)
    ]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "84", "--arm-order", "reactive predictive_h2d",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--sampling-seed", "22", "--repetition-id", "3",
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert len(plan["workload_instance_ids_in_manifest_order"]) == 84
    assert plan["workload_instance_ids_in_manifest_order"][-1] == "task-83"
    assert plan["workflow_arrival_batch_size"] == 0
    assert plan["server_running"] == 48
    assert plan["sampling_seed"] == 22
    assert plan["repetition_id"] == 3
    assert plan["same_seed_is_not_same_trajectory"]
    assert "exploration" in plan["scope"]
    assert plan["formal_paired_repetition_target"] == 4
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "129", "--semantic-artifact", str(artifact),
        "--workload-manifest", str(manifest),
    ])
    with pytest.raises(ValueError, match="manifest"):
        main()


def test_demand_balance_is_diagnostic_not_posthoc_throughput_correction():
    reactive = {
        "llm_request_count": 10, "tool_call_count": 8,
        "submitted_input_tokens": 1000, "completed_request_output_tokens": 100,
    }
    result = workload_balance(reactive, {key: value * 2 for key, value in reactive.items()})
    assert all(value == 1. for value in result["relative_changes_predictive_vs_reactive"].values())
    assert not result["same_logical_trajectory_verified"]
    assert "no isolated KV-policy" in result["performance_claim"]


def test_request_result_audit_uses_rids_not_parallel_completion_order(tmp_path):
    arm = tmp_path / "predictive_h2d"
    client, _ = fixture(arm)
    workflow = client / "workflows/task"
    workflow.mkdir(parents=True)
    rows = [
        {"kind": "llm_submit", "attributes": {"request_id": "a", "prompt_semantic_sha256": "p-a"}},
        {"kind": "llm_submit", "attributes": {"request_id": "b", "prompt_semantic_sha256": "p-b"}},
        {"kind": "llm_result", "attributes": {"request_id": "b", "output_chars": 2}},
        {"kind": "llm_result", "attributes": {"request_id": "a", "output_chars": 1}},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps({**row, "workflow_id": "wf"}) + "\n" for row in rows)
    )
    values = workflow_trajectory_audit(arm)
    sequence = values["task"]["request_sequence"]
    assert [row["model_result"]["output_chars"] for row in sequence] == [1, 2]
    assert paired_trajectory_report(values, values)["observed_request_sequence_equal_count"] == 1
    changed = json.loads(json.dumps(values))
    changed["task"]["request_sequence"][1]["model_result"]["output_chars"] = 3
    report = paired_trajectory_report(values, changed)
    assert report["observed_request_sequence_different_count"] == 1
    assert report["workflows"][0]["first_observed_request_divergence_ordinal"] == 2


def test_disabled_physical_lane_is_exportable_only_as_explicit_diagnostic(tmp_path, monkeypatch):
    arm = tmp_path / "reactive"
    fixture(arm)
    (arm / "opportunities/admission_opportunities.jsonl").write_text(json.dumps({
        "event": "admission_runtime_state", "physical_disabled": True,
        "prepare_host": False, "final_stage_priority": True,
        "final_stage_prefetch": False, "semantic_worker_configured": False,
        "counts": {"physical_receipt_failed": 1},
    }) + "\n")
    argv = ["summarize", "--run-root", str(tmp_path), "--allow-incomplete"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="disabled physical ledger"):
        main()
    monkeypatch.setattr(sys, "argv", argv + ["--allow-degraded-runtime"])
    main()
    report = json.loads((tmp_path / "comparison.json").read_text())
    assert report["status"] == "degraded_diagnostic"
    assert report["comparison_eligible"] is False
    assert report["degraded_runtime_arms"] == ["reactive"]
