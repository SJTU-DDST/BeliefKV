import hashlib
import json
import subprocess
import sys

import pytest

from scripts.summarize_semantic_h2d_ab import (
    capacity_pressure_watch, cleanup_workspaces, main, paired_trajectory_report, summarize,
    runtime_clock_offset, verify_frozen_execution_source, workflow_trajectory_audit, workload_balance,
)


@pytest.mark.parametrize("event", ("safe_point_census", "admission_runtime_state"))
def test_runtime_clock_alignment_does_not_require_opportunity_sampling(event):
    assert runtime_clock_offset({
        "event": event, "ts_ms": 1500., "monotonic_ms": 1000.,
    }) == 500.


@pytest.mark.parametrize("row", (
    {"event": "admission_runtime_state", "ts_ms": 1500.},
    {"event": "admission_runtime_state", "ts_ms": True, "monotonic_ms": 1000.},
    {"event": "admission_runtime_state", "ts_ms": 1500., "monotonic_ms": float("nan")},
    {"event": "admission_runtime_state", "ts_ms": float("inf"), "monotonic_ms": 1000.},
    {"event": "tool_wait_forecast", "ts_ms": 1500., "monotonic_ms": 1000.},
))
def test_runtime_clock_alignment_keeps_unpaired_or_invalid_observations_unknown(row):
    assert runtime_clock_offset(row) is None


def test_capacity_watch_keeps_loss_populations_and_missing_evidence_separate():
    missing = capacity_pressure_watch({})
    assert missing["same_session_common_input"]["missing_fraction"] is None
    assert missing["evicted_host_full_reaccess"]["recomputed_units"] is None
    native = {
        "context_prefix_reuse_evidence": {"counts": {
            "with_prior_served_prompt": 4,
            "previously_served_common_prefix_tokens": 100,
            "previously_served_prefix_recompute_proxy_tokens": 25,
            "requests_with_prefix_recompute_proxy": 1,
        }},
        "host_block_eviction_attribution": {
            "available": True, "reused_full_units": 40, "recomputed_full_units": 10,
            "pending_blocks_by_pool": {"full": 3}, "probe_prefix_length_overflow": 2,
        },
    }
    watch = capacity_pressure_watch(native)
    assert watch["same_session_common_input"]["missing_fraction"] == .25
    assert watch["evicted_host_full_reaccess"]["recomputed_fraction_of_matched_units"] == .2
    assert watch["evicted_host_full_reaccess"]["pending_blocks_by_pool"] == {"full": 3}
    assert watch["evicted_host_full_reaccess"]["probe_prefix_length_overflow"] == 2
    native["context_prefix_reuse_evidence"]["counts"].update(
        previously_served_common_prefix_tokens=0,
        previously_served_prefix_recompute_proxy_tokens=0,
    )
    assert capacity_pressure_watch(native)["same_session_common_input"]["missing_fraction"] is None


@pytest.mark.parametrize("committed", (False, True))
@pytest.mark.parametrize("path,allowed", (
    ("docs/report.md", True),
    ("tests/report_fixture.py", True),
    ("scripts/summarize_semantic_h2d_ab.py", True),
    ("scripts/launch_semantic_h2d_after_pair.py", True),
    ("experiments/reports/analysis.json", True),
    ("experiments/reports/analysis.json.gz", True),
    ("experiments/reports/profile.raw.gz", True),
    ("experiments/reports/analysis.md", True),
    ("experiments/reports/nested/analysis.json", True),
    ("experiments/reports/helper.py", False),
    ("experiments/models/calibrated.json", False),
    ("patches/sglang-v0.5.20-beliefkv-staging.patch", False),
    ("beliefkv/runtime/runtime.py", False),
    ("configs/workload.json", False),
    ("scripts/run_deepagents_swebench.py", False),
))
def test_frozen_execution_keeps_collection_revision_and_rejects_runtime_changes(
    tmp_path, committed, path, allowed,
):
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, text=True, stderr=subprocess.STDOUT,
        ).strip()

    git("init", "-q")
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("original\n")
    git("add", ".")
    commit_options = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid")
    git(*commit_options, "commit", "-qm", "collection")
    frozen = git("rev-parse", "HEAD")
    target.write_text("changed\n")
    if committed:
        git(*commit_options, "commit", "-qam", "follow-up")
    if not allowed:
        with pytest.raises(ValueError, match="execution source changed"):
            verify_frozen_execution_source(tmp_path, frozen)
        return
    result = verify_frozen_execution_source(tmp_path, frozen)
    assert result["collection_code_commit"] == frozen
    assert result["verification_code_commit"] == git("rev-parse", "HEAD")
    assert result["execution_source_unchanged"] is True
    assert result["changed_committed_paths"] == ([path] if committed else [])


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


def test_queued_handoff_bytes_are_distinct_from_boundary_prediction(tmp_path):
    arm = tmp_path / "predictive_h2d"
    _, server = fixture(arm)
    (server / "physical_action_ack.jsonl").write_text(json.dumps({
        "command_id": "cmd", "action": "PREFETCH_GPU", "source": "execution_handoff",
        "node_ids": [1], "num_bytes": 200, "pool_bytes": {"kv": 100, "mamba": 100},
    }) + "\n")
    (server / "physical_action_use.jsonl").write_text(json.dumps({
        "event": "beliefkv_prefetch_first_service", "command_id": "cmd",
        "full_node_reused": True, "reused_full_node_ids": [1],
        "ack_ts_ms": 10., "first_service_ts_ms": 20.,
    }) + "\n")
    result = summarize(arm)["controlled_h2d_by_source"]
    assert result["execution_handoff"]["acks"] == 1
    assert result["execution_handoff"]["bytes"] == 200
    assert result["execution_handoff"]["verified_full_reused_bytes"] == 100
    assert result["join_ticket"]["acks"] == result["tool_wait"]["acks"] == 0


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


def test_stopped_cleanup_uses_archived_results_without_fabricating_summary(tmp_path, monkeypatch):
    arm = tmp_path / "predictive_h2d"
    client, server = fixture(arm)
    (client / "summary.json").unlink()
    (client / "manifest.json").write_text("{}")
    (server / "native_telemetry_ready.json").write_text('{"scheduler_pid": 123}')
    for task in ("archived", "missing_patch"):
        base = client / "workflows" / task
        (base / "workspace").mkdir(parents=True)
        (base / "workspace/file").write_text("data")
        (base / "result.json").write_text(json.dumps({
            "instance_id": task, "outcome": "error",
            "sandbox_cleanup_status": "completed", "artifact_collection": {"errors": []},
        }))
    (client / "workflows/archived/model.patch").write_text("saved diff")

    def exited(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr("scripts.summarize_semantic_h2d_ab.os.kill", exited)
    result = cleanup_workspaces(arm, stopped_collection=True)
    assert result["collection_scope"] == "stopped_archived_results"
    assert result["removed_workspaces"] == [str(client / "workflows/archived/workspace")]
    assert (client / "workflows/missing_patch/workspace/file").exists()
    assert (client / "workflows/archived/model.patch").read_text() == "saved diff"
    assert not (client / "summary.json").exists()


def test_stopped_cleanup_refuses_a_live_scheduler(tmp_path, monkeypatch):
    arm = tmp_path / "predictive_h2d"
    _, server = fixture(arm)
    (server / "native_telemetry_ready.json").write_text('{"scheduler_pid": 123}')
    monkeypatch.setattr("scripts.summarize_semantic_h2d_ab.os.kill", lambda pid, sig: None)
    with pytest.raises(ValueError, match="scheduler to have exited"):
        cleanup_workspaces(arm, stopped_collection=True)


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
    monkeypatch.setenv("HOST_NUMA_MEMORY_POLICY", "preferred")
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
        "--native-reference-run", str(tmp_path / "native-reference"),
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert plan["activation_wall_clock_seconds"] == 21600
    assert plan["host_numa_memory_policy"] == "preferred"
    assert plan["workload_instance_ids_in_manifest_order"] == ["z", "a"]
    assert plan["workflow_arrival_batch_size"] == 0
    assert plan["order"] == ["predictive_h2d"]
    assert "historical native references" in plan["scope"]
    assert plan["native_reference_run"] == str(tmp_path / "native-reference")
    assert plan["formal_repetition_order"] == ["predictive_h2d native"]


def test_long_project_sources_and_small_workload_cutoff_are_frozen(tmp_path, monkeypatch):
    context = tmp_path / "project.txt.gz"
    context.write_bytes(b"frozen project source")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({
        "context_delivery": "complete project in every agent",
        "workloads": [
            {
                "instance_id": str(index), "task_kind": "locobench_agent",
                "frozen_repository_context": {
                    "path": str(context),
                    "sha256": hashlib.sha256(context.read_bytes()).hexdigest(),
                },
            }
            for index in range(4)
        ],
    }))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "4", "--workload-manifest", str(manifest),
        "--tail-active-workflows", "2", "--warmup-seconds", "0",
    ])
    main()
    configuration = json.loads((tmp_path / "ab_plan.json").read_text())
    assert configuration["primary_comparison_policy"]["tail_active_workflows"] == 2
    assert configuration["primary_comparison_policy"]["warmup_seconds"] == 0
    assert configuration["workload_task_kinds"] == ["locobench_agent"]
    assert len(configuration["workload_auxiliary_artifacts"]) == 1
    monkeypatch.setattr(
        "scripts.summarize_semantic_h2d_ab.verify_frozen_execution_source",
        lambda *_: {"execution_source_unchanged": True},
    )
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    main()
    context.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="frozen artifact changed"):
        main()


def test_single_reactive_arm_keeps_prepare_without_predictive_inputs(tmp_path, monkeypatch):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [
        {"instance_id": "z"}, {"instance_id": "a"},
    ]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "2", "--arm-order", "reactive",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--arrival-rate-per-second", str(1 / 15), "--arrival-seed", "21",
        "--prepare-host", "1", "--enable-tool-timing", "1",
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert plan["order"] == ["reactive"]
    assert plan["workload_instance_ids_in_manifest_order"] == ["z", "a"]
    assert plan["workflow_arrival_mode"] == "poisson"
    assert "single-arm development" in plan["scope"]
    assert plan["tool_timing_enabled"]
    policy = plan["policy_configuration"]["reactive"]
    assert policy["prepare_host"] and policy["resident_first"]
    assert not policy["predictive_h2d"] and not policy["execution_handoff"]
    assert not policy["h2d_opportunity_sampling"] and not policy["semantic_text_stream"]


@pytest.mark.parametrize("changed_field", ("native_reference_timeline", "native_reference_cache"))
def test_initialize_freezes_selected_reference_artifacts_and_verifies_their_hashes(
    tmp_path, monkeypatch, changed_field,
):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [{"instance_id": "task"}]}))
    references = {
        "native_reference_timeline": tmp_path / "native.clock_v2.json.gz",
        "native_reference_cache": tmp_path / "native.clock_v2.json",
    }
    for path in references.values():
        path.write_bytes(b"sealed reference fixture")
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "1", "--arm-order", "predictive_h2d",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--native-reference-run", str(tmp_path / "native-reference"),
        "--native-reference-timeline", str(references["native_reference_timeline"]),
        "--native-reference-cache", str(references["native_reference_cache"]),
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    for field, path in references.items():
        assert plan[field] == str(path.resolve())
        assert plan[f"{field}_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "scripts.summarize_semantic_h2d_ab.verify_frozen_execution_source",
        lambda *_: {"execution_source_unchanged": True},
    )
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    main()
    verification = json.loads((tmp_path / "frozen_execution_verification.json").read_text())
    assert verification["frozen_artifact_digests_verified"]
    references[changed_field].write_bytes(b"changed reference")
    with pytest.raises(ValueError, match="frozen artifact changed"):
        main()


def test_resume_rejects_changed_common_frontend_cache(tmp_path, monkeypatch):
    (tmp_path / "ab_plan.json").write_text(json.dumps({"chat_token_block_cache": True}))
    monkeypatch.setenv("BELIEFKV_CHAT_TOKEN_BLOCK_CACHE", "0")
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    with pytest.raises(ValueError, match="frozen chat token cache configuration changed"):
        main()


@pytest.mark.parametrize("native_cache", ["0", "1"])
def test_new_dynamic_plan_freezes_each_frontend_without_prescribing_root_actions(
    tmp_path, monkeypatch, native_cache,
):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [{"instance_id": "task"}]}))
    monkeypatch.setenv("FANOUT_PROFILE", "native_in_graph_dynamic_nested")
    monkeypatch.setenv("NATIVE_CHAT_TOKEN_BLOCK_CACHE", native_cache)
    monkeypatch.setenv("PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE", "1")
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "1", "--arm-order", "predictive_h2d native",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert plan["chat_token_block_cache_by_arm"] == {
        "predictive_h2d": True, "native": native_cache == "1",
    }
    assert plan["policy_configuration"]["predictive_h2d"]["chat_token_block_cache"]
    assert plan["policy_configuration"]["native"]["chat_token_block_cache"] == (
        native_cache == "1"
    )
    assert plan["in_graph_initial_tool_choice"] == "auto"
    assert plan["in_graph_initial_fanout_generation_bounds"] is None
    assert "no prescribed first action" in plan["in_graph_named_task_constraint"]
    monkeypatch.setenv("PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE", "0")
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    with pytest.raises(ValueError, match="frozen per-arm chat token cache configuration changed"):
        main()


def test_comparison_requires_the_actual_server_frontend_to_match_each_arm(tmp_path, monkeypatch):
    arm = tmp_path / "predictive_h2d"
    fixture(arm)
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["predictive_h2d"],
        "chat_token_block_cache_by_arm": {"predictive_h2d": True},
    }))
    monkeypatch.setattr(sys, "argv", ["summarize", "--run-root", str(tmp_path)])
    for value in (None, False, True):
        if value is not None:
            (arm / "server/launch_configuration.json").write_text(json.dumps({
                "chat_token_block_cache": value,
            }))
        if value is not True:
            with pytest.raises(ValueError, match="mismatched server launch chat token cache"):
                main()
        else:
            main()
    comparison = json.loads((tmp_path / "comparison.json").read_text())
    assert comparison["planned_frontend_cache_by_arm"] == {"predictive_h2d": True}
    assert comparison["arms"]["predictive_h2d"]["server_launch_chat_token_block_cache"]


@pytest.mark.parametrize("option", ("--native-reference-timeline", "--native-reference-cache"))
def test_explicit_reference_artifacts_require_a_reference_run(tmp_path, monkeypatch, option):
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize", option, "/reference",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not (tmp_path / "ab_plan.json").exists()


def test_native_pair_freezes_disjoint_108_plus_48_fixed_arrivals(tmp_path, monkeypatch):
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [
        {"instance_id": f"task-{index}"} for index in range(156)
    ]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "156", "--arm-order", "native predictive_h2d",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--arrival-batch-size", "108", "--arrival-batch-interval-ms", "3600000",
        "--prepare-host", "1", "--host-split", "80:20",
        "--h2d-opportunity-sampling", "1",
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert [row["offset_seconds"] for row in plan["arrival_schedule"]] == [0.] * 108 + [3600.] * 48
    assert len({row["instance_id"] for row in plan["arrival_schedule"]}) == 156
    assert not any(plan["policy_configuration"]["native"].values())
    predictive = plan["policy_configuration"]["predictive_h2d"]
    assert predictive["control"] and predictive["predictive_h2d"]
    assert predictive["prepare_host"] and predictive["execution_handoff"]
    assert predictive["semantic_text_stream"] is False
    assert plan["join_prefetch_trigger"] == "observed_eos"
    assert plan["semantic_artifact"] is None
    assert not plan["prepare_host_in_both_arms"]
    assert plan["host_split"] == "80:20"
    assert plan["order"] == ["native", "predictive_h2d"]
    assert plan["formal_repetition_order"] == ["predictive_h2d native"]

@pytest.mark.parametrize("prefix_diagnostics", (0, 1))
def test_optimized_prefetch_configuration_survives_plan_environment_roundtrip(
    tmp_path, monkeypatch, prefix_diagnostics,
):
    from scripts.resume_semantic_h2d_ab import resume_environment

    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [{"instance_id": "task"}]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize", "--root-count", "1",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--join-prefetch-min-advance-ms", "1000", "--tool-prefetch-min-advance-ms", "500",
        "--parallel-wait-prefetch", "1", "--wait-prefetch-contexts", "2",
        "--prediction-maintenance-interval-ms", "100",
        "--h2d-opportunity-sampling", "0", "--terminal-cache-diagnostics", "0",
        "--prefetch-prefix-diagnostics", str(prefix_diagnostics),
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert not plan["policy_configuration"]["predictive_h2d"]["h2d_opportunity_sampling"]
    for field in ("h2d_seed_artifact", "tool_timing_artifact", "transfer_service_seed"):
        plan.setdefault(field, "")
    env = resume_environment(plan, tmp_path, python="/python", port=18454)
    assert env["JOIN_PREFETCH_MIN_ADVANCE_MS"] == "1000.0"
    assert env["TOOL_PREFETCH_MIN_ADVANCE_MS"] == "500.0"
    assert env["PARALLEL_WAIT_PREFETCH"] == "1"
    assert env["WAIT_PREFETCH_CONTEXTS"] == "2"
    assert env["PREDICTION_MAINTENANCE_INTERVAL_MS"] == "100.0"
    assert env["H2D_OPPORTUNITY_SAMPLING"] == env["TERMINAL_CACHE_DIAGNOSTICS"] == "0"
    assert plan["profile_shared_path"] is False
    assert env["PROFILE_SHARED_PATH"] == "0"
    assert plan["prefetch_prefix_diagnostics"] is bool(prefix_diagnostics)
    assert env["PREFETCH_PREFIX_DIAGNOSTICS"] == str(prefix_diagnostics)
    del plan["prefetch_prefix_diagnostics"]
    assert resume_environment(plan, tmp_path, python="/python", port=18454)[
        "PREFETCH_PREFIX_DIAGNOSTICS"
    ] == "1"


def test_frozen_prefix_diagnostics_rejects_a_changed_setting(tmp_path, monkeypatch):
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "prefetch_prefix_diagnostics": True,
    }))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
        "--prefetch-prefix-diagnostics", "0",
    ])
    with pytest.raises(ValueError, match="frozen prefetch prefix diagnostics"):
        main()


@pytest.mark.parametrize("required", (False, True))
def test_frozen_delegation_check_cannot_be_changed_between_arms(
    tmp_path, monkeypatch, required,
):
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "delegation_shape_required_before_native": required,
    }))
    monkeypatch.setenv("REQUIRE_DELEGATION_SHAPE", str(int(not required)))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    with pytest.raises(ValueError, match="frozen delegation shape qualification"):
        main()


@pytest.mark.parametrize("actual", (None, False, True))
def test_summary_requires_the_frozen_prefix_diagnostics_in_server_launch(
    tmp_path, monkeypatch, actual,
):
    arm = tmp_path / "predictive_h2d"
    _, server = fixture(arm)
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["predictive_h2d"], "prefetch_prefix_diagnostics": False,
    }))
    if actual is not None:
        (server / "launch_configuration.json").write_text(json.dumps({
            "prefetch_prefix_diagnostics": actual,
        }))
    monkeypatch.setattr(sys, "argv", ["summarize", "--run-root", str(tmp_path)])
    if actual is False:
        main()
    else:
        with pytest.raises(ValueError, match="mismatched server launch prefetch prefix"):
            main()

def test_actual_runtime_must_match_the_registered_parallel_restore_setting(tmp_path, monkeypatch):
    arm = tmp_path / "predictive_h2d"
    fixture(arm)
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["predictive_h2d"], "parallel_wait_prefetch": True,
    }))
    monkeypatch.setattr(sys, "argv", ["summarize", "--run-root", str(tmp_path)])
    with pytest.raises(ValueError, match="mismatched parallel_wait_prefetch"):
        main()
    path = arm / "opportunities/admission_opportunities.jsonl"
    actual = json.loads(path.read_text())
    actual["parallel_wait_prefetch"] = True
    path.write_text(json.dumps(actual) + "\n")
    main()
    assert (tmp_path / "comparison.json").is_file()

def test_nested_workload_contract_must_match_the_actual_client(tmp_path, monkeypatch):
    from beliefkv.experiments.delegation_policy import (
        NESTED_IN_GRAPH_PROFILE, delegation_contract,
    )

    arm = tmp_path / "predictive_h2d"
    client, _ = fixture(arm)
    shape = delegation_contract(NESTED_IN_GRAPH_PROFILE)
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["predictive_h2d"], "fanout_profile": NESTED_IN_GRAPH_PROFILE,
        "delegation_contract": shape,
    }))
    monkeypatch.setattr(sys, "argv", ["summarize", "--run-root", str(tmp_path)])
    with pytest.raises(ValueError, match="mismatched nested delegation workload"):
        main()
    path = client / "summary.json"
    actual = json.loads(path.read_text())
    actual.update(subagent_fanout_profile=NESTED_IN_GRAPH_PROFILE, delegation_contract=shape)
    path.write_text(json.dumps(actual))
    manifest_path = client / "manifest.json"
    manifest_path.write_text(json.dumps({
        "config": {"subagent_fanout_profile": "native_in_graph_2to4"},
        "delegation_contract": delegation_contract("native_in_graph_2to4"),
    }))
    with pytest.raises(ValueError, match="mismatched nested delegation workload"):
        main()
    manifest_path.write_text(json.dumps({
        "config": {"subagent_fanout_profile": NESTED_IN_GRAPH_PROFILE},
        "delegation_contract": shape,
    }))
    main()
    assert (tmp_path / "comparison.json").is_file()


def test_poisson_pair_freezes_one_exact_schedule_without_reordering_tasks(tmp_path, monkeypatch):
    from beliefkv.experiments.arrival_schedule import (
        arrival_schedule_digest, build_poisson_workflow_arrivals,
    )
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    manifest = tmp_path / "workload.json"
    ids = ["z-task", "a-task", "last-task"]
    manifest.write_text(json.dumps({"workloads": [
        {"instance_id": instance} for instance in ids
    ]}))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize",
        "--root-count", "3", "--arm-order", "predictive_h2d native",
        "--semantic-artifact", str(artifact), "--workload-manifest", str(manifest),
        "--arrival-rate-per-second", str(1 / 15), "--arrival-seed", "21",
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    assert plan["workflow_arrival_mode"] == "poisson"
    assert plan["workflow_arrival_batch_size"] == 0
    assert plan["workflow_arrival_batch_interval_ms"] == 0
    assert plan["workload_instance_ids_in_manifest_order"] == ids
    assert plan["arrival_schedule"] == [
        {"instance_id": instance, "offset_seconds": arrival.scheduled_offset_seconds}
        for instance, arrival in zip(
            ids, build_poisson_workflow_arrivals(3, rate_per_second=1 / 15, seed=21),
        )
    ]
    assert plan["arrival_schedule_sha256"] == arrival_schedule_digest(plan["arrival_schedule"])
    monkeypatch.setattr(
        "scripts.summarize_semantic_h2d_ab.verify_frozen_execution_source",
        lambda *_: {"execution_source_unchanged": True},
    )
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--verify-frozen-plan",
    ])
    main()
    verification = json.loads((tmp_path / "frozen_execution_verification.json").read_text())
    assert verification["frozen_arrival_schedule_verified"]
    plan["arrival_schedule"][1]["offset_seconds"] += .1
    (tmp_path / "ab_plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="digest changed"):
        main()


def test_native_baseline_labels_are_not_reported_as_reactive():
    demand = {
        "llm_request_count": 1, "tool_call_count": 1,
        "submitted_input_tokens": 1, "completed_request_output_tokens": 1,
    }
    assert "relative_changes_predictive_vs_native" in workload_balance(
        demand, demand, baseline="native"
    )
    rows = {"task": {"request_sequence": [], "missing_prompt_fingerprints": 0}}
    report = paired_trajectory_report(rows, rows, baseline="native")
    assert "native" in report["workflows"][0]
    assert "reactive" not in report["workflows"][0]


@pytest.mark.parametrize("cross_revision", (False, True))
def test_completed_native_predictive_pair_reports_correct_baseline(
    tmp_path, monkeypatch, cross_revision,
):
    fixture(tmp_path / "native")
    (tmp_path / "native/opportunities/admission_opportunities.jsonl").unlink()
    fixture(tmp_path / "predictive_h2d")
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["native", "predictive_h2d"],
        "policy_configuration": {
            "native": {"prepare_host": False},
            "predictive_h2d": {"prepare_host": False},
        },
        "arm_revisions": {
            "native": {"code_commit": "old", "sglang_patch_sha256": "old-patch"},
            "predictive_h2d": {
                "code_commit": "new" if cross_revision else "old",
                "sglang_patch_sha256": "new-patch" if cross_revision else "old-patch",
            },
        },
    }))
    monkeypatch.setattr(sys, "argv", ["summarize", "--run-root", str(tmp_path)])
    main()
    report = json.loads((tmp_path / "comparison.json").read_text())
    assert report["comparison_eligible"]
    assert report["baseline"] == "native"
    assert report["paired_mean_jct_seconds"] == {"native": 5., "predictive_h2d": 5.}
    assert report["workload_balance"]["baseline"] == "native"
    assert report["same_code_revision"] is not cross_revision
    assert report["same_engine_patch"] is not cross_revision
    assert not report["isolated_policy_effect_verified"]


@pytest.mark.parametrize("trigger", (
    "observed_eos", "report_service_or_eos", "selective_report_or_eos",
))
def test_observed_join_summary_accepts_online_timing_without_semantic_worker(
    tmp_path, monkeypatch, trigger,
):
    client, _ = fixture(tmp_path / "predictive_h2d")
    (client / "manifest.json").write_text(json.dumps({
        "config": {"max_completion_tokens": 16384},
    }))
    state_path = tmp_path / "predictive_h2d/opportunities/admission_opportunities.jsonl"
    state = json.loads(state_path.read_text())
    state.update(
        join_prefetch_trigger=trigger, online_tool_timing=True,
        borrowable_parent_headroom=True, semantic_worker_configured=False, counts={},
    )
    policy = {"min_full_bytes": 536870912, "interval_ms": 200., "max_candidates": 4}
    if trigger == "selective_report_or_eos":
        state["selective_join_prefetch"] = {**policy, "enabled": True}
    state_path.write_text(json.dumps(state) + "\n")
    (tmp_path / "ab_plan.json").write_text(json.dumps({
        "order": ["predictive_h2d", "native"], "join_prefetch_trigger": trigger,
        "online_tool_timing": True, "borrowable_parent_headroom": True,
        "max_completion_tokens": 16384,
        **({"selective_join_prefetch": policy} if trigger == "selective_report_or_eos" else {}),
    }))
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--allow-incomplete",
    ])
    main()
    report = json.loads((tmp_path / "comparison.json").read_text())
    assert report["status"] == "partial"
    assert not report["comparison_eligible"]
    assert not report["arms"]["predictive_h2d"]["runtime_state"]["semantic_worker_configured"]

    for setting, error in (
        ("final_stage_prefetch", "wrong predictive/model configuration"),
        ("semantic_worker_configured", "wrong predictive/model configuration"),
        ("online_tool_timing", "mismatched online_tool_timing"),
        ("borrowable_parent_headroom", "mismatched borrowable_parent_headroom"),
    ):
        changed = {**state, setting: not state[setting]}
        state_path.write_text(json.dumps(changed) + "\n")
        with pytest.raises(ValueError, match=error):
            main()
    state_path.write_text(json.dumps(state) + "\n")


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
