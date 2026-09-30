import json

from scripts.summarize_semantic_h2d_ab import cleanup_workspaces, summarize


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
