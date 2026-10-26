import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as comparison


@pytest.mark.parametrize("root_count", (16, 24, 36))
def test_replay_population_binds_every_declared_workflow_and_rejects_subsets(root_count):
    from beliefkv.core.events import RuntimeEventKind as Kind

    def graph(ids):
        return NS(
            nodes=[NS(event=NS(kind=Kind.WORKFLOW_START, workflow_id=value)) for value in ids],
            summary=lambda: {"workflows": len(ids)},
        )

    ids = [f"w{index}" for index in range(root_count)]
    summary = {
        "workflow_count": root_count, "completed_workflows": root_count,
        "workflows": [{"workflow_id": value} for value in ids],
    }
    assert comparison.complete_source_population(summary, graph(ids)) == root_count
    with pytest.raises(ValueError, match="entire declared"):
        comparison.complete_source_population(summary, graph(ids[:-1]))
    with pytest.raises(ValueError, match="entire declared"):
        comparison.complete_source_population(summary, graph([*ids[:-1], "unrelated"]))
    summary["workflows"][-1]["workflow_id"] = ids[0]
    with pytest.raises(ValueError, match="entire declared"):
        comparison.complete_source_population(summary, graph(ids))


@pytest.mark.parametrize("change", (
    None, "incomplete", "zero_duration", "nan_duration", "missing_jct", "boolean_jct",
    "negative_work", "not_native",
))
def test_captured_native_reference_keeps_all_workflow_tails_and_exact_work(change):
    from beliefkv.core.events import RuntimeEventKind as Kind

    summary = {
        "workflow_count": 2, "completed_workflows": 2, "duration_seconds": 10001.,
        "workflows": [
            {"workflow_id": "short", "duration_seconds": 1.},
            {"workflow_id": "tail", "duration_seconds": 10000.},
        ],
    }
    work = {"workflows": 2, "requests": 4, "prompt_tokens": 120, "output_tokens": 20}
    launch = {"native_policy_baseline": True}
    graph = NS(
        nodes=[
            NS(event=NS(kind=kind, workflow_id=value, ts_ms=when))
            for value, finish in (("short", 900.), ("tail", 9999000.))
            for kind, when in ((Kind.WORKFLOW_START, 0.), (Kind.WORKFLOW_END, finish))
        ],
        summary=lambda: work,
    )
    if change == "incomplete":
        summary["completed_workflows"] = 1
    elif change == "zero_duration":
        summary["duration_seconds"] = 0.
    elif change == "nan_duration":
        summary["duration_seconds"] = float("nan")
    elif change == "missing_jct":
        del summary["workflows"][1]["duration_seconds"]
    elif change == "boolean_jct":
        summary["workflows"][0]["duration_seconds"] = True
    elif change == "negative_work":
        work["output_tokens"] = -1
    elif change == "not_native":
        launch["native_policy_baseline"] = False
    if change is not None:
        with pytest.raises(ValueError, match="captured native baseline|entire declared"):
            comparison.captured_native_baseline(summary, graph, launch)
        return
    baseline = comparison.captured_native_baseline(summary, graph, launch)
    assert baseline["kind"] == "natural_native_source_capture"
    assert baseline["workflow_jct_ms"] == {"short": 900., "tail": 9999000.}
    assert baseline["mean_jct_ms"] == 4999950.
    assert baseline["duration_ms"] == 9999000.
    assert baseline["source_harness_workflow_jct_ms"] == {"short": 1000., "tail": 10000000.}
    assert baseline["source_harness_duration_ms"] == 10001000.
    assert baseline["metric_boundary"] == comparison.METRIC_BOUNDARY
    assert baseline["requests"] == 4 and baseline["output_tokens"] == 20
    assert baseline["whole_run_output_tokens_per_second"] == pytest.approx(20 / 9999.)
    assert baseline["all_normal_tails_retained"] is True
    assert baseline["fresh_full_native_replay_required"] is False


@pytest.fixture
def reference(tmp_path):
    artifact = tmp_path / "timing.json"
    artifact.write_text("{}")
    return {"arms": {"full": {
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "SAMPLING_SEED": "21", "SERVER_RANDOM_SEED": "21", "PYTHONHASHSEED": "21",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
        "MODEL_CONTEXT_TOKENS": "262144", "JOIN_PREFETCH_TRIGGER": "observed_eos",
        "PREFILL_SCHEDULING_POLICY": "workflow", "PREFETCH_RESIDENCY_PROTECTION": "1",
        "TOOL_RESTORE_MODE": "speculative", "RESTORE_TIERS": "complete",
        "IDLE_PREPARE_POLICY": "all", "TOOL_CONTINUITY": "0",
        "EXECUTION_FRONTIER_RESTORE": "0", "ONLINE_TOOL_TIMING": "1",
        "PARALLEL_WAIT_PREFETCH": "1", "WAIT_PREFETCH_CONTEXTS": "2",
        "TOOL_TIMING_ARTIFACT": str(artifact), "H2D_SEED_ARTIFACT": str(artifact),
        "TRANSFER_SERVICE_SEED": str(artifact),
        "BELIEFKV_NATIVE_TELEMETRY_DIR": "old/native",
        "BELIEFKV_ADMISSION_TELEMETRY_DIR": "old/admission",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR": "old/opportunities",
    }}}


@pytest.mark.parametrize("label", comparison.ORDER)
def test_each_control_preserves_common_work_and_only_enables_its_policy(reference, tmp_path, label):
    checkout, engine, arm, plugin = (tmp_path / name for name in ("checkout", "engine", label, "plugin"))
    env, flags, event_socket = comparison.environment(
        reference, label, checkout, engine, arm, 18554, plugin,
    )
    native, custom_kv = label == "native_matched", label in ("full", "no_background_prepare")
    assert env["PYTHONHASHSEED"] == env["SERVER_RANDOM_SEED"] == env["SAMPLING_SEED"] == "21"
    assert env["MAX_RUNNING_REQUESTS"] == "8"
    assert env["HICACHE_SIZE_GB"] == "200" and env["BELIEFKV_FULL_MAMBA_HOST_SPLIT"] == "80:20"
    assert env["CONTEXT_LENGTH"] == "262144" and env["MEM_FRACTION_STATIC"] == "0.94"
    assert env["SGLANG_ENABLE_DELAY_SAMPLE"] == "1"
    assert env["BELIEFKV_CAUSAL_REPLAY_BRIDGE"] == "1"
    assert env["BELIEFKV_REPLAY_TOKEN_CAPTURE"] == "0"
    assert env["BELIEFKV_JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert env["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert env["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_RESIDENT_FIRST"] == str(int(custom_kv))
    assert env["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == str(int(label == "full"))
    assert "--enable-custom-logit-processor" in flags
    assert ("--enable-beliefkv-admission" in flags) is not native
    if native:
        assert event_socket is None and env["BELIEFKV_NATIVE_TELEMETRY_DIR"] == str(arm / "server")
        assert "BELIEFKV_ADMISSION_TELEMETRY_DIR" not in env
    else:
        assert event_socket == Path("/tmp/bkv-causal-18554.sock")
        assert env["BELIEFKV_ADMISSION_TELEMETRY_DIR"] == str(arm / "server")
        assert "BELIEFKV_NATIVE_TELEMETRY_DIR" not in env
    for name, digest in (
        ("BELIEFKV_TOOL_TIMING_ARTIFACT", "BELIEFKV_TOOL_TIMING_SHA256"),
        ("BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256"),
        ("BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256"),
    ):
        assert bool(env[name]) is custom_kv
        assert bool(env[digest]) is custom_kv
    assert reference["arms"]["full"]["BELIEFKV_NATIVE_TELEMETRY_DIR"] == "old/native"


@pytest.mark.parametrize("label", comparison.CONTROLS)
def test_explicit_short_tool_continuity_reaches_the_actual_workflow_arm(reference, tmp_path, label):
    reference["arms"]["full"]["TOOL_CONTINUITY"] = "1"
    env, _, _ = comparison.environment(
        reference, label, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    expected = str(int(label not in ("native_matched", "kv_only")))
    assert env["TOOL_CONTINUITY"] == env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == expected
    assert env["PREFILL_SCHEDULING_POLICY"] == (
        "join_priority" if label == "join_priority_only" else "workflow"
    )


@pytest.mark.parametrize("field,value", (
    ("MAX_RUNNING_REQUESTS", "12"), ("SERVER_RANDOM_SEED", "22"),
    ("PYTHONHASHSEED", "22"), ("HICACHE_SIZE_GB", "160"),
    ("JOIN_PREFETCH_TRIGGER", "semantic"), ("PREFILL_SCHEDULING_POLICY", "join_priority"),
    ("TOOL_RESTORE_MODE", "execution"), ("EXECUTION_FRONTIER_RESTORE", "1"),
))
def test_comparison_does_not_silently_change_the_requested_profile(reference, tmp_path, field, value):
    reference["arms"]["full"][field] = value
    with pytest.raises(ValueError):
        comparison.environment(reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path)


def test_explicit_frontier_candidate_preserves_tool_prefetch_and_native_disables_both(
    reference, tmp_path,
):
    reference["arms"]["full"]["EXECUTION_FRONTIER_RESTORE"] = "1"
    for mode in ("full", "native_matched"):
        environment, _, _ = comparison.environment(
            reference, mode, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
            execution_frontier_restore=True,
        )
        expected = str(int(mode == "full"))
        assert environment["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == expected
        assert environment["BELIEFKV_ENABLE_TOOL_PREFETCH"] == expected
        assert environment["TOOL_RESTORE_MODE"] == "speculative"


@pytest.mark.parametrize("mode", comparison.CONTROLS)
def test_declared_execution_restore_unifies_ready_requests_and_preserves_controls(
    reference, tmp_path, mode,
):
    reference["arms"]["full"].update(
        TOOL_RESTORE_MODE="execution", EXECUTION_FRONTIER_RESTORE="1",
        FRONTIER_COLD_RECLAIM="1", FRONTIER_COLD_RECLAIM_POLICY="waiting",
        TOOL_PREFETCH_PRIORITY_MS="1500",
    )
    settings, _, _ = comparison.environment(
        reference, mode, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
        tool_restore_mode="execution",
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )
    custom = mode in ("full", "kv_only", "no_background_prepare", "no_tool_prefetch")
    assert settings["TOOL_RESTORE_MODE"] == ("execution" if custom else "speculative")
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert settings["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"] == "0"
    assert settings["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == str(int(custom))
    assert settings["BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY"] == "waiting"
    assert settings["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(custom))
    assert settings["BELIEFKV_ONLINE_TOOL_TIMING"] == str(int(custom))


def test_execution_restore_cpu_preflight_matches_the_effective_runtime(
    reference, tmp_path,
):
    reference["arms"]["full"].update(
        TOOL_RESTORE_MODE="execution", EXECUTION_FRONTIER_RESTORE="1",
        FRONTIER_COLD_RECLAIM="1", FRONTIER_COLD_RECLAIM_POLICY="waiting",
        TOOL_PREFETCH_PRIORITY_MS="1500",
        TOOL_TIMING_ARTIFACT="", H2D_SEED_ARTIFACT="", TRANSFER_SERVICE_SEED="",
    )
    settings, _, _ = comparison.environment(
        reference, "full", Path(comparison.ROOT), tmp_path, tmp_path, 18554, tmp_path,
        tool_restore_mode="execution",
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )
    result = comparison.preflight_runtime(settings, "cpu-config-check")
    actual = result["verified_configuration"]
    assert actual["enable_frontier_restore"] is True
    assert actual["enable_tool_prefetch"] is False
    assert actual["tool_restore_mode"] == "execution"
    assert actual["frontier_cold_reclaim_policy"] == "waiting"


def test_unified_execution_profile_preserves_the_captured_workload():
    directory = Path(comparison.ROOT) / "configs/migration"
    source = comparison.load(directory / "qwen3_coder_18project_root_phase16_20261024.json")
    candidate = comparison.load(
        directory / "qwen3_coder_18project_unified_execution_restore_20261024.json",
    )
    original, updated = source["common_environment"], candidate["common_environment"]
    changed = {name for name in original.keys() | updated.keys() if original.get(name) != updated.get(name)}
    assert changed == {
        "TOOL_RESTORE_MODE", "TOOL_PREFETCH", "TOOL_PREFETCH_PRIORITY_MS",
        "FRONTIER_RESTORE_ORDINARY_QUOTA",
    }
    assert updated["FRONTIER_COLD_RECLAIM_POLICY"] == "waiting"
    assert candidate["native_reference"]["repeat_full_native_replay"] is False
    assert candidate["performance_accepted"] is False


@pytest.mark.parametrize("mode", ("full", "kv_only", "native_matched", "join_priority_only"))
def test_cold_frontier_requires_explicit_declaration_and_disables_with_custom_kv(
    reference, tmp_path, mode,
):
    reference["arms"]["full"].update(EXECUTION_FRONTIER_RESTORE="1", FRONTIER_COLD_RECLAIM="1")
    with pytest.raises(ValueError, match="declared frontier cold reclaim"):
        comparison.environment(
            reference, mode, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
            execution_frontier_restore=True,
        )
    environment, _, _ = comparison.environment(
        reference, mode, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )
    assert environment["BELIEFKV_FRONTIER_COLD_RECLAIM"] == str(
        int(mode in ("full", "kv_only"))
    )


@pytest.mark.parametrize("mode", ("full", "kv_only", "native_matched", "join_priority_only"))
def test_selected_queue_policy_is_declared_in_each_actual_control_environment(
    reference, tmp_path, mode,
):
    reference["arms"]["full"].update(
        EXECUTION_FRONTIER_RESTORE="1", FRONTIER_COLD_RECLAIM="1",
        FRONTIER_COLD_RECLAIM_POLICY="selected_queue",
    )
    settings, _, _ = comparison.environment(
        reference, mode, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )
    expected = "selected_queue" if mode in ("full", "kv_only") else "waiting"
    assert settings["FRONTIER_COLD_RECLAIM_POLICY"] == expected
    assert settings["BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY"] == expected


@pytest.mark.parametrize("label", comparison.ORDER)
def test_explicit_24_running_limit_is_the_same_in_every_control(reference, tmp_path, label):
    reference["arms"]["full"]["ROOT_COUNT"] = "24"
    reference["arms"]["full"]["MAX_RUNNING_REQUESTS"] = "24"
    env, _, _ = comparison.environment(
        reference, label, tmp_path, tmp_path, tmp_path, 18564, tmp_path,
        max_running_requests=24,
    )
    assert env["ROOT_COUNT"] == env["MAX_RUNNING_REQUESTS"] == "24"
    assert env["BELIEFKV_SUBAGENT_RETENTION"] == "off"
    assert env["BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM"] == (
        "1" if label in ("full", "no_background_prepare") else "0"
    )
    with pytest.raises(ValueError, match="MAX_RUNNING_REQUESTS"):
        comparison.environment(
            reference, label, tmp_path, tmp_path, tmp_path, 18564, tmp_path,
        )


def test_observed_configuration_checks_the_registered_batch_limit(tmp_path, monkeypatch):
    checks = []
    monkeypatch.setattr(
        comparison.comparison, "observe_arm",
        lambda *args, **kwargs: checks.append((args, kwargs)),
    )
    comparison.observed_configuration(
        {"max_running_requests": 24}, "native_matched", tmp_path,
    )
    assert checks == [
        (("native_matched", tmp_path), {"max_running_requests": 24, "runtime_overrides": None}),
    ]


def test_short_workstream_source_keeps_budgets_seed_and_requires_a_new_population(reference):
    spec = json.loads((comparison.ROOT / (
        "configs/workloads/locobench_short_workstreams_20261026.json"
    )).read_bytes())
    configured = comparison.experiment_reference(reference, spec)["arms"]["full"]
    assert configured["FANOUT_PROFILE"] == "native_in_graph_short_workstreams"
    assert configured["CHILD_WORK_BUDGET_WARNING_CALLS"] == "4"
    assert configured["CHILD_WORK_BUDGET_TARGET_CALLS"] == "8"
    assert configured["SERVER_RANDOM_SEED"] == configured["SAMPLING_SEED"] == "21"
    assert configured["ROOT_COUNT"] == "18"
    assert configured["MAX_RUNNING_REQUESTS"] == "24"
    assert configured["AGENT_ADMISSION"] == "thunderagent_retained"
    assert configured["BELIEFKV_AGENT_ADMISSION"] == "thunderagent_retained"
    assert spec["population"]["manifest"] == configured["WORKLOAD_MANIFEST"]
    assert not spec["qualification"]["actual_project_test_execution_required"]
    assert spec["qualification"]["live_root_child_tool_execution_required"]
    assert not spec["qualification"]["gpu_source_started"]


@pytest.mark.parametrize("label", (
    "full", "join_priority_only", "native_matched", "scheduler_only", "kv_only",
))
def test_saved_oneshot_experiment_pins_capacity_and_policy_for_every_arm(
    reference, tmp_path, label,
):
    from tests.test_causal_source_capture import capacity

    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    configured = comparison.experiment_reference(reference, spec)
    pinned = comparison.source_reference(configured, 24, 24, capacity())
    env, _, _ = comparison.environment(
        pinned, label, tmp_path, tmp_path, tmp_path, 18566, tmp_path,
        max_running_requests=24,
    )
    assert env["ROOT_COUNT"] == env["MAX_RUNNING_REQUESTS"] == "24"
    assert env["DEVICE_FULL_TOKENS"] == "1797968"
    assert env["DEVICE_MAMBA_SLOTS"] == "513"
    assert env["BELIEFKV_SUBAGENT_RETENTION"] == "off"
    assert reference["arms"]["full"]["MAX_RUNNING_REQUESTS"] == "8"
    if label in ("full", "kv_only"):
        assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1"
        assert float(env["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"]) == 1500.
        assert env["BELIEFKV_IDLE_PREPARE_POLICY"] == "cold"
    else:
        assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"


@pytest.mark.parametrize("field,value", (
    ("DEVICE_FULL_TOKENS", "1797967"), ("DEVICE_MAMBA_SLOTS", "512"),
))
def test_explicit_pool_size_cannot_differ_from_the_natural_native_reference(
    reference, field, value,
):
    from tests.test_causal_source_capture import capacity

    reference["arms"]["full"][field] = value
    with pytest.raises(ValueError, match="device capacity differs"):
        comparison.source_reference(reference, 24, 24, capacity())


def test_oneshot_settings_override_stale_prefixed_reference_values_in_the_real_runtime(
    reference, tmp_path, monkeypatch,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    reference["arms"]["full"].update(
        BELIEFKV_IDLE_PREPARE_POLICY="all",
        BELIEFKV_TOOL_RESTORE_MODE="execution",
        BELIEFKV_ENABLE_FRONTIER_RESTORE="1",
        BELIEFKV_RESTORE_TIERS="pipeline",
        BELIEFKV_TOOL_PREFETCH_PRIORITY_MS="0",
    )
    configured = comparison.experiment_reference(reference, spec)
    env, _, _ = comparison.environment(
        configured, "full", tmp_path, tmp_path, tmp_path, 18568, tmp_path,
        max_running_requests=24,
    )
    assert env["BELIEFKV_IDLE_PREPARE_POLICY"] == "cold"
    assert env["BELIEFKV_TOOL_RESTORE_MODE"] == "speculative"
    assert env["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert env["BELIEFKV_RESTORE_TIERS"] == "complete"
    assert float(env["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"]) == 1500.
    assert reference["arms"]["full"]["BELIEFKV_IDLE_PREPARE_POLICY"] == "all"
    for name, value in env.items():
        if name.startswith("BELIEFKV_"):
            monkeypatch.setenv(name, value)
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda *_args, **_kwargs: None)
    runtime = native.NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "config-cpu-events.sock"), model_path="cpu-config-check",
    )
    try:
        assert runtime.idle_prepare_policy == "cold"
        assert runtime.tool_restore_mode == "speculative"
        assert runtime.restore_tiers == "complete"
        assert runtime.tool_prefetch_priority_ms == 1500.
    finally:
        runtime.close()


def test_oneshot_spec_rejects_conflicting_plain_and_prefixed_settings(reference):
    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    spec["common_environment"]["BELIEFKV_IDLE_PREPARE_POLICY"] = "all"
    with pytest.raises(ValueError, match="conflicting"):
        comparison.experiment_reference(reference, spec)


@pytest.mark.parametrize("stale_alias", (False, True))
def test_cpu_preflight_verifies_effective_runtime_settings_before_gpu_start(
    reference, tmp_path, monkeypatch, stale_alias,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    env, _, _ = comparison.environment(
        comparison.experiment_reference(reference, spec),
        "full", tmp_path, tmp_path, tmp_path, 18568, tmp_path, max_running_requests=24,
    )
    if stale_alias:
        env["BELIEFKV_IDLE_PREPARE_POLICY"] = "all"
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
    ):
        env[name] = ""
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda *_a, **_k: None)
    checks = []

    def execute(command, *, env, **_kwargs):
        assert env["CUDA_VISIBLE_DEVICES"] == "99999"
        checks.append(json.loads(command[-1]))
        with monkeypatch.context() as child:
            for name, value in env.items():
                if name.startswith("BELIEFKV_"):
                    child.setenv(name, value)
            child.setattr(comparison.sys, "argv", ["-c", *command[3:]])
            exec(command[2], {})
        return NS(stdout="")

    monkeypatch.setattr(comparison.subprocess, "run", execute)
    if stale_alias:
        with pytest.raises(ValueError, match="CPU runtime configuration differs"):
            comparison.preflight_runtime(env, "cpu-config-check")
    else:
        result = comparison.preflight_runtime(env, "cpu-config-check")
        assert result["runtime_configuration_verified"] is True
        assert result["verified_configuration"]["idle_prepare_policy"] == "cold"
    assert checks[0]["idle_prepare_policy"] == "cold"


@pytest.mark.parametrize("disabled_in_child", (False, True))
def test_cpu_preflight_verifies_effective_tool_residency_grace(
    reference, tmp_path, monkeypatch, disabled_in_child,
):
    spec = comparison.load(comparison.ROOT / (
        "configs/migration/qwen3_coder_18project_root_phase16_tool_grace_20261024.json"
    ))
    env, _, _ = comparison.environment(
        comparison.experiment_reference(reference, spec),
        "full", tmp_path, tmp_path, tmp_path, 18568, tmp_path, max_running_requests=24,
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )

    def execute(command, *, env, **_kwargs):
        assert env["CUDA_VISIBLE_DEVICES"] == "99999"
        with monkeypatch.context() as child:
            for name, value in env.items():
                if name.startswith("BELIEFKV_"):
                    child.setenv(name, value)
            if disabled_in_child:
                child.setenv("BELIEFKV_TOOL_CONTINUITY_GRACE_MS", "0")
            child.setattr(comparison.sys, "argv", ["-c", *command[3:]])
            exec(command[2], {})
        return NS(stdout="")

    monkeypatch.setattr(comparison.subprocess, "run", execute)
    if disabled_in_child:
        with pytest.raises(ValueError, match="CPU runtime configuration differs"):
            comparison.preflight_runtime(env, "cpu-config-check")
    else:
        result = comparison.preflight_runtime(env, "cpu-config-check")
        assert result["verified_configuration"]["tool_continuity_grace_ms"] == 100.


@pytest.mark.parametrize("stale_in_child", (False, True))
def test_cpu_preflight_checks_preload_quota_separately_from_priority(
    reference, tmp_path, monkeypatch, stale_in_child,
):
    spec = comparison.load(comparison.ROOT / (
        "configs/migration/qwen3_coder_18project_root_phase16_20261024.json"
    ))
    spec["common_environment"]["FRONTIER_RESTORE_ORDINARY_QUOTA"] = "0"
    env, _, _ = comparison.environment(
        comparison.experiment_reference(reference, spec),
        "full", tmp_path, tmp_path, tmp_path, 18568, tmp_path, max_running_requests=24,
        execution_frontier_restore=True, frontier_cold_reclaim=True,
    )

    def execute(command, *, env, **_kwargs):
        assert env["CUDA_VISIBLE_DEVICES"] == "99999"
        with monkeypatch.context() as child:
            for name, value in env.items():
                if name.startswith("BELIEFKV_"):
                    child.setenv(name, value)
            if stale_in_child:
                child.setenv("BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA", "4")
            child.setattr(comparison.sys, "argv", ["-c", *command[3:]])
            exec(command[2], {})
        return NS(stdout="")

    monkeypatch.setattr(comparison.subprocess, "run", execute)
    if stale_in_child:
        with pytest.raises(ValueError, match="CPU runtime configuration differs"):
            comparison.preflight_runtime(env, "cpu-config-check")
    else:
        result = comparison.preflight_runtime(env, "cpu-config-check")
        assert result["verified_configuration"]["frontier_restore_ordinary_quota"] == 0


@pytest.mark.parametrize("field,value", (
    ("ROOT_COUNT", None), ("MAX_RUNNING_REQUESTS", "0"),
    ("FANOUT_PROFILE", "native_in_graph_reusable_nested"),
    ("BELIEFKV_SUBAGENT_RETENTION", "selective"),
))
def test_invalid_oneshot_configuration_does_not_silently_enable_another_workload(
    reference, field, value,
):
    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    if value is None:
        del spec["common_environment"][field]
    else:
        spec["common_environment"][field] = value
    with pytest.raises(ValueError, match="one-shot experiment"):
        comparison.experiment_reference(reference, spec)


@pytest.mark.parametrize("change", (
    None, "summary_hash", "frozen_manifest", "qualified_hash", "source_arm",
    "source_batch", "source_binding",
))
def test_oneshot_source_uses_the_gpu_bound_frozen_manifest_without_an_optional_summary_hash(
    tmp_path, monkeypatch, change,
):
    monkeypatch.setattr(comparison, "ROOT", tmp_path)
    manifest = tmp_path / "configs/tasks.json"
    frozen = tmp_path / "frozen/configs/tasks.json"
    tasks = {
        "requested_workflows": 24, "max_tasks_per_project": 1,
        "workloads": [
            {"repo": f"locobench/project-{index}", "instance_id": f"task-{index}",
             "task_kind": "locobench_agent"}
            for index in range(24)
        ],
    }
    for path in (manifest, frozen):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(tasks))
    digest = sha256_file(manifest)
    common = {
        "ROOT_COUNT": "24", "MAX_RUNNING_REQUESTS": "24",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
        "WORKLOAD_MANIFEST": "configs/tasks.json",
    }
    spec = {"source_profile": "nested_oneshot", "common_environment": common}
    arm = tmp_path / "capture/native_capture"
    source_path = tmp_path / "capture.json"
    source = {
        "run_root": str(arm.parent), "execution_checkout": str(tmp_path / "frozen"),
        "environment": {**common, "WORKLOAD_MANIFEST": str(frozen)},
        "source_profile": "nested_oneshot", "source_sha256": {"configs/tasks.json": digest},
    }
    summary = {"subagent_fanout_profile": common["FANOUT_PROFILE"]}
    if change == "summary_hash":
        summary["workload_manifest_sha256"] = "incorrect"
    elif change == "frozen_manifest":
        frozen.write_text('{"different_tasks": [3]}')
    elif change == "qualified_hash":
        source["source_sha256"]["configs/tasks.json"] = "incorrect"
    elif change == "source_arm":
        source["run_root"] = str(tmp_path / "other_capture")
    elif change == "source_batch":
        source["environment"]["MAX_RUNNING_REQUESTS"] = "8"
    source_path.write_text(json.dumps(source))
    smoke = {
        "source_registration": str(source_path),
        "external_inputs": [{"path": str(source_path), "sha256": sha256_file(source_path)}],
    }
    if change == "source_binding":
        source_path.write_text(json.dumps({**source, "changed_after_gpu_check": True}))
    if change is not None:
        with pytest.raises(ValueError, match="source"):
            comparison.experiment_source_evidence(spec, summary, smoke, arm, 24, 24)
    else:
        evidence = comparison.experiment_source_evidence(spec, summary, smoke, arm, 24, 24)
        assert evidence["manifest_sha256"] == digest
        assert evidence["frozen_manifest"] == str(frozen)
        assert evidence["source_registration"] == str(source_path)


def test_oneshot_source_rejects_shared_projects_even_with_valid_source_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "ROOT", tmp_path)
    manifest = tmp_path / "configs/tasks.json"
    frozen = tmp_path / "frozen/configs/tasks.json"
    tasks = {
        "requested_workflows": 2, "max_tasks_per_project": 2,
        "workloads": [
            {"repo": "locobench/shared-project", "instance_id": f"task-{index}",
             "task_kind": "locobench_agent"}
            for index in range(2)
        ],
    }
    for path in (manifest, frozen):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(tasks))
    digest = sha256_file(manifest)
    common = {
        "ROOT_COUNT": "2", "MAX_RUNNING_REQUESTS": "24",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
        "WORKLOAD_MANIFEST": "configs/tasks.json",
    }
    spec = {"source_profile": "nested_oneshot", "common_environment": common}
    arm = tmp_path / "capture/native_capture"
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps({
        "run_root": str(arm.parent), "execution_checkout": str(tmp_path / "frozen"),
        "environment": {**common, "WORKLOAD_MANIFEST": str(frozen)},
        "source_profile": "nested_oneshot", "source_sha256": {"configs/tasks.json": digest},
    }))
    smoke = {
        "source_registration": str(source_path),
        "external_inputs": [{"path": str(source_path), "sha256": sha256_file(source_path)}],
    }
    with pytest.raises(ValueError, match="source workload"):
        comparison.experiment_source_evidence(
            spec, {"subagent_fanout_profile": common["FANOUT_PROFILE"]}, smoke, arm, 2, 24,
        )


def test_generated_tool_model_settings_are_consumed_by_the_real_runtime_constructor(
    reference, tmp_path, monkeypatch,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    env, _, _ = comparison.environment(
        reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    for name, value in env.items():
        if name.startswith("BELIEFKV_"):
            monkeypatch.setenv(name, value)
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    checked = []
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda path, **kwargs: checked.append(
        (path, kwargs["expected_sha256"], kwargs["model_path"]),
    ))
    runtime = native.NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "cpu-events.sock"), model_path="cpu-contract-model",
    )
    try:
        artifact = reference["arms"]["full"]["TOOL_TIMING_ARTIFACT"]
        assert checked == [(artifact, sha256_file(Path(artifact)), "cpu-contract-model")]
        assert runtime._tool_timing_only and runtime.enable_tool_prefetch
    finally:
        runtime.close()


@pytest.mark.parametrize("label", ("scheduler_only", "kv_only"))
def test_orthogonal_replay_controls_reach_the_real_runtime_without_changing_work(
    reference, tmp_path, monkeypatch, label,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    spec = json.loads((comparison.ROOT / (
        "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    )).read_bytes())
    configured = comparison.experiment_reference(reference, spec)
    env, flags, event_socket = comparison.environment(
        configured, label, tmp_path, tmp_path, tmp_path, 18570, tmp_path,
        max_running_requests=24, sampling_mode="position",
    )
    assert label in comparison.ALL_CONTROLS
    assert comparison.DEFAULT_ORDER == ("full", "join_priority_only")
    assert env["ROOT_COUNT"] == env["MAX_RUNNING_REQUESTS"] == "24"
    assert env["SAMPLING_SEED"] == env["SERVER_RANDOM_SEED"] == "21"
    assert env["BELIEFKV_CAUSAL_REPLAY_SAMPLING"] == "position"
    assert env["SGLANG_ENABLE_DELAY_SAMPLE"] == "0"
    assert env["BELIEFKV_SUBAGENT_RETENTION"] == "off"
    assert "--enable-beliefkv-admission" in flags and event_socket is not None
    custom = label == "kv_only"
    for name in ("TOOL_TIMING_ARTIFACT", "H2D_SEED", "TRANSFER_SERVICE_SEED"):
        assert bool(env["BELIEFKV_" + name]) is custom
    for name, value in env.items():
        if name.startswith("BELIEFKV_"):
            monkeypatch.setenv(name, value)
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
        "BELIEFKV_ADMISSION_OPPORTUNITY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda *_a, **_k: None)
    runtime = native.NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "orthogonal.sock"), model_path="cpu-only",
    )
    try:
        assert runtime.internal_comparison_mode == label
        assert runtime._custom_kv_management is custom
        assert runtime.enable_prepare_host is custom
        assert runtime.enable_background_prepare is custom
        assert runtime.enable_terminal_child_reclaim is custom
        assert runtime.enable_final_stage_prefetch is custom
        assert runtime.enable_tool_prefetch is custom
        assert runtime._tool_timing_only is custom
        assert runtime.tool_prefetch_priority_ms == (1500. if custom else 0.)
        assert runtime.prefill_scheduling_policy == ("native" if custom else "workflow")
        assert runtime.enable_resident_first is not custom
        assert runtime.enable_final_stage_priority is not custom
        assert runtime.enable_join_parent_priority is not custom
        assert not runtime._native_cache
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_kv_only_cpu_preflight_checks_the_effective_native_order(
    reference, tmp_path, monkeypatch,
):
    env, _, _ = comparison.environment(
        reference, "kv_only", tmp_path, tmp_path, tmp_path, 18570, tmp_path,
    )
    checked = []
    monkeypatch.setattr(
        comparison.subprocess, "run",
        lambda command, **_kwargs: (
            checked.append(json.loads(command[-1])) or NS(stdout="")
        ),
    )
    result = comparison.preflight_runtime(env, "cpu-only")
    assert checked[0]["prefill_scheduling_policy"] == "native"
    assert result["verified_configuration"] == checked[0]


@pytest.mark.parametrize("stale_alias", (False, True))
def test_cpu_preflight_checks_requested_post_ack_protection(
    reference, tmp_path, monkeypatch, stale_alias,
):
    import beliefkv.runtime.sglang_v0520_runtime as native

    reference["arms"]["full"]["PREFETCH_RESIDENCY_PROTECTION"] = "0"
    env, _, _ = comparison.environment(
        reference, "full", tmp_path, tmp_path, tmp_path, 18588, tmp_path,
    )
    if stale_alias:
        env["BELIEFKV_PREFETCH_RESIDENCY_PROTECTION"] = "1"
    for name in (
        "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
        "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
    ):
        env[name] = ""
    monkeypatch.setattr(native, "validate_tool_timing_artifact", lambda *_a, **_k: None)

    def execute(command, *, env, **_kwargs):
        assert env["CUDA_VISIBLE_DEVICES"] == "99999"
        with monkeypatch.context() as child:
            for name, value in env.items():
                if name.startswith("BELIEFKV_"):
                    child.setenv(name, value)
            child.setattr(comparison.sys, "argv", ["-c", *command[3:]])
            exec(command[2], {})
        return NS(stdout="")

    monkeypatch.setattr(comparison.subprocess, "run", execute)
    if stale_alias:
        with pytest.raises(ValueError, match="CPU runtime configuration differs"):
            comparison.preflight_runtime(env, "cpu-only")
    else:
        result = comparison.preflight_runtime(env, "cpu-only")
        assert result["verified_configuration"]["enable_prefetch_residency_protection"] is False


def test_no_tool_prefetch_changes_only_tool_h2d_and_preserves_offload_settings(reference, tmp_path):
    original, original_flags, original_socket = comparison.environment(
        reference, "full", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    disabled, flags, event_socket = comparison.environment(
        reference, "no_tool_prefetch", tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert {
        name for name in original.keys() | disabled.keys()
        if original.get(name) != disabled.get(name)
    } == {"BELIEFKV_ENABLE_TOOL_PREFETCH"}
    assert disabled["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert disabled["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "full"
    assert disabled["PREPARE_HOST"] == disabled["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "1"
    assert disabled["ENABLE_TOOL_TIMING"] == disabled["BELIEFKV_ONLINE_TOOL_TIMING"] == "1"
    assert disabled["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"
    assert disabled["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "1"
    assert disabled["BELIEFKV_BORROWABLE_PARENT_HEADROOM"] == "1"
    assert flags == original_flags and event_socket == original_socket


def test_no_tool_prefetch_validates_the_observed_runtime_switch(reference, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        comparison.comparison, "observe_arm",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    comparison.observed_configuration({}, "no_tool_prefetch", tmp_path)
    assert calls == [(("full", tmp_path), {
        "max_running_requests": 8, "runtime_overrides": {"tool_prefetch": False},
    })]


def test_position_control_disables_delayed_sampling_for_every_requested_policy(reference, tmp_path):
    for label in comparison.CONTROLS:
        env, _, _ = comparison.environment(
            reference, label, tmp_path, tmp_path, tmp_path, 18558, tmp_path,
            sampling_mode="position",
        )
        assert env["BELIEFKV_CAUSAL_REPLAY_SAMPLING"] == "position"
        assert env["SGLANG_ENABLE_DELAY_SAMPLE"] == "0"


def retention_spec():
    return json.loads((
        Path(__file__).resolve().parents[1]
        / "configs/migration/reusable_subagent_retention_20261022.json"
    ).read_bytes())


@pytest.mark.parametrize("window", (1, 1500., 2000))
def test_tool_priority_option_changes_only_tool_prefetch_and_its_window(reference, window):
    original = reference["arms"]["full"].copy()
    prepared = comparison.tool_priority_reference(reference, window)
    assert reference["arms"]["full"] == original
    assert prepared["arms"]["full"] == {
        **original, "TOOL_PREFETCH": "1",
        "TOOL_PREFETCH_PRIORITY_MS": str(window),
        "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS": str(window),
    }


@pytest.mark.parametrize("window", (None, "1500", True, False, 0, -1, float("nan"), float("inf")))
def test_invalid_tool_priority_option_is_rejected(reference, window):
    with pytest.raises(ValueError, match="priority window"):
        comparison.tool_priority_reference(reference, window)


@pytest.mark.parametrize("label", comparison.CONTROLS)
def test_optional_tool_priority_is_only_enabled_in_custom_tool_prefetch_arms(
    reference, tmp_path, label,
):
    prepared = comparison.tool_priority_reference(reference, 1500.)
    env, _, _ = comparison.environment(
        prepared, label, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    enabled = label in ("full", "kv_only", "no_background_prepare")
    assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(enabled))
    assert float(env["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"]) == (1500. if enabled else 0.)


@pytest.mark.parametrize("label", comparison.RETENTION_CONTROLS)
def test_optional_tool_priority_is_identical_across_retention_controls(reference, tmp_path, label):
    prepared = comparison.retention_reference(
        reference, retention_spec(), list(comparison.RETENTION_CONTROLS),
    )
    prepared = comparison.tool_priority_reference(prepared, 1500.)
    env, _, _ = comparison.environment(
        prepared, label, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
    )
    assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1"
    assert env["BELIEFKV_TOOL_PREFETCH_PRIORITY_MS"] == "1500.0"
    assert env["BELIEFKV_SUBAGENT_RETENTION"] == comparison.RETENTION_CONTROLS[label]


@pytest.mark.parametrize("label,actual,accepted", (
    ("full", 1500., True), ("no_background_prepare", 1500., True),
    ("full", 0., False), ("no_tool_prefetch", 0., True),
    ("no_tool_prefetch", 1500., False), ("join_priority_only", 0., True),
    ("join_priority_only", 1500., False),
    ("scheduler_only", 0., True), ("scheduler_only", 1500., False),
    ("kv_only", 1500., True), ("kv_only", 0., False),
))
def test_tool_priority_observer_validates_the_effective_window(
    tmp_path, monkeypatch, label, actual, accepted,
):
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        comparison.qualified, "last_runtime_state",
        lambda _path: {"tool_prefetch_priority_ms": actual},
    )
    plan = {"tool_prefetch_priority_ms": 1500.}
    if accepted:
        comparison.observed_configuration(plan, label, tmp_path, final=True)
    else:
        with pytest.raises(ValueError, match="priority window"):
            comparison.observed_configuration(plan, label, tmp_path, final=True)


@pytest.mark.parametrize("actual", (None, False, True))
def test_continuity_observer_rejects_an_ignored_enable_switch(tmp_path, monkeypatch, actual):
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        comparison.qualified, "last_runtime_state",
        lambda _path: {"tool_continuity": actual},
    )
    plan = {"reference_environment": {
        "PREFILL_SCHEDULING_POLICY": "workflow", "TOOL_CONTINUITY": "1",
    }}
    if actual is True:
        comparison.observed_configuration(plan, "full", tmp_path, final=True)
    else:
        with pytest.raises(ValueError, match="short-tool continuity"):
            comparison.observed_configuration(plan, "full", tmp_path, final=True)


def test_retention_matrix_keeps_exact_common_settings_and_an_independent_native_control(
    reference, tmp_path,
):
    reference["arms"]["full"].update({
        "BELIEFKV_IDLE_PREPARE_POLICY": "all",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "1",
        "BELIEFKV_RESTORE_TIERS": "dependency",
    })
    labels = ["native_matched", *comparison.RETENTION_CONTROLS]
    prepared = comparison.retention_reference(reference, retention_spec(), labels)
    controls = []
    for label in labels:
        env, flags, socket = comparison.environment(
            prepared, label, tmp_path, tmp_path, tmp_path, 18554, tmp_path,
            sampling_mode="position",
        )
        assert env["FANOUT_PROFILE"] == "native_in_graph_reusable_nested"
        assert env["ROOT_COUNT"] == "36" and env["MAX_RUNNING_REQUESTS"] == "8"
        assert env["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
        assert env["BELIEFKV_RESTORE_TIERS"] == "complete"
        assert env["BELIEFKV_IDLE_PREPARE_POLICY"] == "cold"
        assert env["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
        if label == "native_matched":
            assert env["BELIEFKV_SUBAGENT_RETENTION"] == "off"
            assert socket is None and "--enable-beliefkv-admission" not in flags
        else:
            assert env["BELIEFKV_SUBAGENT_RETENTION"] == comparison.RETENTION_CONTROLS[label]
            assert env["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "1"
            controls.append({name: value for name, value in env.items()
                             if name != "BELIEFKV_SUBAGENT_RETENTION"})
    assert all(control == controls[0] for control in controls)
    assert reference["arms"]["full"]["BELIEFKV_RESTORE_TIERS"] == "dependency"


def test_saved_project_disjoint_configuration_reaches_every_retention_control(reference, tmp_path):
    spec = json.loads((Path(__file__).resolve().parents[1] /
                       "configs/migration/reusable_subagent_retention_16project_20261023.json").read_text())
    labels = ["native_matched", *comparison.RETENTION_CONTROLS]
    prepared = comparison.retention_reference(reference, spec, labels)
    for label in labels:
        env, _, _ = comparison.environment(
            prepared, label, tmp_path, tmp_path, tmp_path, 18562, tmp_path,
        )
        assert env["ROOT_COUNT"] == "16" and env["MAX_RUNNING_REQUESTS"] == "8"
        assert env["WORKLOAD_MANIFEST"] == spec["common_environment"]["WORKLOAD_MANIFEST"]
        assert env["SERVER_RANDOM_SEED"] == env["SAMPLING_SEED"] == env["PYTHONHASHSEED"] == "21"
        if label != "native_matched":
            assert env["BELIEFKV_DORMANT_HOST_BYTES"] == "34359738368"
            assert env["BELIEFKV_DORMANT_HBM_BYTES"] == "2147483648"


@pytest.mark.parametrize("roots", (None, True, 16, "0", "-1", "016", "157", "16.0", "\uff11\uff16"))
def test_retention_root_count_requires_an_explicit_canonical_positive_count(reference, roots):
    spec = retention_spec()
    spec["common_environment"]["ROOT_COUNT"] = roots
    with pytest.raises(ValueError, match="prepared comparison"):
        comparison.retention_reference(reference, spec, ["native_matched", "subagent_selective"])


@pytest.mark.parametrize("change", (None, "count", "hash", "missing", "invalid_hash"))
def test_new_matrix_cannot_reuse_a_different_source_even_with_the_same_root_count(change):
    spec = {
        "common_environment": {"ROOT_COUNT": "16"}, "source_workload_sha256": "a" * 64,
    }
    summary = {"workload_manifest_sha256": "a" * 64}
    count = 16
    if change == "count":
        count = 36
    elif change == "hash":
        summary["workload_manifest_sha256"] = "b" * 64
    elif change == "missing":
        summary.clear()
    elif change == "invalid_hash":
        spec["source_workload_sha256"] = None
    if change is None:
        comparison.retention_source_matches(spec, summary, count)
    else:
        with pytest.raises(ValueError, match="retention source differs"):
            comparison.retention_source_matches(spec, summary, count)


def test_old_retention_matrix_without_a_manifest_override_retains_its_population_check():
    comparison.retention_source_matches(retention_spec(), {}, 36)
    with pytest.raises(ValueError, match="declared root"):
        comparison.retention_source_matches(retention_spec(), {}, 16)


@pytest.mark.parametrize("order", (
    [], ["full", "subagent_selective"], ["join_priority_only", "subagent_lru"],
))
def test_retention_policy_controls_cannot_be_mixed_into_the_legacy_matrix(reference, order):
    with pytest.raises(ValueError, match="matrix"):
        comparison.retention_reference(reference, retention_spec(), order)


@pytest.mark.parametrize("change", (None, "policy", "host_budget", "hbm_budget", "missing"))
def test_retention_observer_checks_the_effective_policy_and_budgets(tmp_path, monkeypatch, change):
    spec = retention_spec()
    common = spec["common_environment"]
    state = {"tool_continuity": False, "subagent_retention": {
        "policy": "selective", "host_budget_bytes": int(common["BELIEFKV_DORMANT_HOST_BYTES"]),
        "device_budget_bytes": int(common["BELIEFKV_DORMANT_HBM_BYTES"]),
    }}
    if change == "policy":
        state["subagent_retention"]["policy"] = "all"
    elif change == "host_budget":
        state["subagent_retention"]["host_budget_bytes"] += 1
    elif change == "hbm_budget":
        state["subagent_retention"]["device_budget_bytes"] += 1
    elif change == "missing":
        state["subagent_retention"] = None
    calls = []
    monkeypatch.setattr(comparison.comparison, "observe_arm",
                        lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda _path: state)
    plan = {"reference_environment": common}
    if change is None:
        comparison.observed_configuration(plan, "subagent_selective", tmp_path, final=True)
        assert calls[0]["runtime_overrides"] == {"tool_prefetch": False}
    else:
        with pytest.raises(ValueError, match="retention"):
            comparison.observed_configuration(plan, "subagent_selective", tmp_path, final=True)


@pytest.mark.parametrize("jct,duration,accepted", (
    (100., 200., True), (104., 208., True), (106., 200., False),
    (100., 211., False), (80., 180., True),
))
def test_native_source_calibration_checks_jct_and_duration_without_trimming_tails(jct, duration, accepted):
    result = comparison.source_speed_check(
        {"mean_jct_ms": 100., "replay_duration_ms": 200.},
        {"mean_jct_ms": jct, "replay_duration_ms": duration}, .05,
    )
    assert result["accepted"] is accepted
    assert result["replay_to_source_ratios"]["mean_jct_ms"] == jct / 100.


@pytest.mark.parametrize("value", (None, 0, -1, float("nan"), float("inf"), True))
def test_source_calibration_rejects_unmeasured_or_invalid_durations(value):
    with pytest.raises(ValueError):
        comparison.source_speed_check(
            {"mean_jct_ms": 100., "replay_duration_ms": 200.},
            {"mean_jct_ms": 100., "replay_duration_ms": value}, .05,
        )


@pytest.fixture
def predecessor(tmp_path):
    registration = tmp_path / "previous.json"
    previous = {
        "run_root": str(tmp_path / "previous"), "order": ["full", "native_matched"],
        "source_arm": "same-source", "source_plan": {"requests": 1},
        "token_source_sha256": "tokens", "source_capacity": {"full": 10},
        "source_server_parameters": {"random_seed": 21}, "workflow_ids": ["w"],
        "quality_exclusions": [], "quality_exclusion_details": [],
        "reference_environment": {"MAX_RUNNING_REQUESTS": "8"}, "engine_tree": "engine",
    }
    registration.write_text(json.dumps(previous))
    state = {
        "run_root": previous["run_root"], "status": "running_native_matched",
        "completed_arms": ["full"], "original_exit_codes": {"full": 0},
        "watcher": {"pid": 10, "start_ticks": "10"},
    }
    state_path = registration.with_suffix(".state.json")
    state_path.write_text(json.dumps(state))
    return {**previous, "after_registration": str(registration)}, previous, state, state_path


@pytest.mark.parametrize("status,live_pid,expected", (
    ("running_native_matched", 10, False),
    ("starting_native_matched", 10, False),
    ("waiting_resources_native_matched", 10, False),
    ("collected_performance_review_pending", 10, False),
    ("collected_performance_review_pending", 12, False),
    ("collected_performance_review_pending", None, True),
))
def test_followup_waits_for_complete_matrix_and_owned_process_release(
    predecessor, monkeypatch, status, live_pid, expected,
):
    plan, previous, state, path = predecessor
    state.update(
        status=status, completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
        owned=[{"pid": 12, "start_ticks": "12"}],
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: row["pid"] == live_pid)
    assert comparison.predecessor_ready(plan) is expected


@pytest.mark.parametrize("change", (
    "source_plan", "token_source_sha256", "source_capacity", "reference_environment",
    "engine_tree", "run_root", "failed", "dead_supervisor", "missing_arm", "failed_arm",
))
def test_followup_rejects_changed_work_failed_predecessor_and_orphaned_runs(
    predecessor, monkeypatch, change,
):
    plan, previous, state, path = predecessor
    state.update(
        status="collected_performance_review_pending",
        completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
    )
    if change in ("source_plan", "token_source_sha256", "source_capacity", "reference_environment", "engine_tree"):
        plan[change] = "changed"
    elif change == "run_root":
        state["run_root"] = "another-run"
    elif change == "failed":
        state["status"] = "stopped_for_investigation"
    elif change == "dead_supervisor":
        state["status"] = "running_native_matched"
    elif change == "missing_arm":
        state["completed_arms"] = ["full"]
    elif change == "failed_arm":
        state["original_exit_codes"]["native_matched"] = 1
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda _row: False)
    with pytest.raises(ValueError):
        comparison.predecessor_ready(plan)


def test_followup_compares_actual_per_request_work_with_every_predecessor_arm(
    predecessor, monkeypatch,
):
    plan, previous, state, path = predecessor
    state.update(
        status="collected_performance_review_pending",
        completed_arms=previous["order"],
        original_exit_codes={label: 0 for label in previous["order"]},
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(comparison.reservation, "alive", lambda _row: False)
    verified, observed, validated = [], [], []
    monkeypatch.setattr(comparison.comparison, "verify_sources", verified.append)
    monkeypatch.setattr(comparison, "observed_configuration", lambda _plan, label, *_args, **_kwargs: observed.append(label))
    for label in previous["order"]:
        directory = Path(previous["run_root"]) / label / "replay"
        directory.mkdir(parents=True)
        (directory / "summary.json").write_text("{}")
    changed = False

    def validate(_plan, _result, arm, *, live_control):
        validated.append((arm.name, live_control))
        return {"r": ("different" if changed and arm.name == "native_matched" else "same")}

    monkeypatch.setattr(comparison, "validate_result", validate)
    assert comparison.predecessor_work(plan) == {"r": "same"}
    assert verified == [previous]
    assert observed == previous["order"]
    assert validated == [("full", True), ("native_matched", False)]
    changed = True
    with pytest.raises(ValueError, match="request work changed"):
        comparison.predecessor_work(plan)


def receipt():
    return {
        "request_id": "r", "prompt_tokens": 12, "output_tokens": 3,
        "prompt_sha256": "prompt", "output_sha256": "output",
        "native_finish_reason": {"type": "stop", "matched": 9},
        "exact_tokens_verified": True,
    }


@pytest.mark.parametrize("change", (
    None, "instance_as_workflow", "duplicate", "missing", "wrong_count",
    "untyped_eligibility", "no_eligible_work",
))
def test_source_quality_uses_explicit_runtime_identity_and_retains_guard_reasons(change):
    summary = {
        "native_agent_jct_eligible_workflows": 1,
        "workflows": [
            {"workflow_id": "runtime:a", "instance_id": "task_a",
             "native_agent_jct_eligible": True},
            {"workflow_id": "runtime:b", "instance_id": "task_b",
             "native_agent_jct_eligible": False,
             "native_agent_jct_exclusion_reasons": ["guard_intervened_completion"]},
        ],
    }
    if change == "instance_as_workflow":
        summary["workflows"][1]["workflow_id"] = "task_b"
    elif change == "duplicate":
        summary["workflows"].append(dict(summary["workflows"][0]))
    elif change == "missing":
        summary["workflows"].pop()
    elif change == "wrong_count":
        summary["native_agent_jct_eligible_workflows"] = 2
    elif change == "untyped_eligibility":
        summary["workflows"][0]["native_agent_jct_eligible"] = 1
    elif change == "no_eligible_work":
        summary["workflows"][0]["native_agent_jct_eligible"] = False
        summary["native_agent_jct_eligible_workflows"] = 0
    if change is None:
        assert comparison.source_quality(summary, ["runtime:a", "runtime:b"]) == [{
            "workflow_id": "runtime:b", "instance_id": "task_b",
            "reasons": ["guard_intervened_completion"],
        }]
    else:
        with pytest.raises(ValueError, match="qualifications"):
            comparison.source_quality(summary, ["runtime:a", "runtime:b"])


def result_fixture(tmp_path):
    arm = tmp_path / "full"
    replay = arm / "replay"
    replay.mkdir(parents=True)
    path = replay / "native_requests.jsonl"
    path.write_text(json.dumps(receipt()) + "\n")
    plan = {
        "source_plan": {"nodes": 2, "requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
    }
    result = {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "original_native_stopping_verified": True,
        "native_session_http_lifecycle_completed": True,
        "live_control_events_delivered": True, "completed_requests": 1,
        "replay_process_python_hash_seed": "21",
        "control_delivery": {"mode": "per_workflow_harness_queued", "workflows": {
            "w": {"accepted_batch_count": 1},
        }},
        "source_plan": dict(plan["source_plan"]),
        "token_source": {"token_source_sha256": "tokens"},
        "workflow_elapsed_ms": {"w": 5000.}, "native_requests_sha256": sha256_file(path),
    }
    return plan, result, arm


@pytest.mark.parametrize("change", (
    None, "incomplete", "stopping", "control", "population", "tokens",
    "workflow", "receipt", "input_total", "output_total", "hash_seed", "control_dropped",
))
def test_complete_comparison_requires_actual_work_and_causal_receipts(tmp_path, change):
    plan, result, arm = result_fixture(tmp_path)
    if change == "incomplete":
        result["causal_execution_completed"] = False
    elif change == "stopping":
        result["original_native_stopping_verified"] = False
    elif change == "control":
        result["live_control_events_delivered"] = False
    elif change == "population":
        result["completed_requests"] = 2
    elif change == "tokens":
        result["token_source"]["token_source_sha256"] = "other"
    elif change == "workflow":
        result["workflow_elapsed_ms"] = {"other": 5000.}
    elif change == "hash_seed":
        result["replay_process_python_hash_seed"] = None
    elif change == "control_dropped":
        result["control_delivery"]["workflows"]["w"]["accepted_batch_count"] = 0
    elif change == "receipt":
        (arm / "replay/native_requests.jsonl").write_text("{}\n")
    elif change in ("input_total", "output_total"):
        key = "prompt_tokens" if change == "input_total" else "output_tokens"
        plan["source_plan"][key] += 1
        result["source_plan"][key] += 1
    if change is None:
        assert comparison.validate_result(plan, result, arm, live_control=True) == {
            "r": (12, 3, "prompt", "output", {"type": "stop", "matched": 9}),
        }
    else:
        with pytest.raises(ValueError):
            comparison.validate_result(plan, result, arm, live_control=True)


@pytest.mark.parametrize("duplicate", (False, True))
def test_receipt_population_rejects_duplicates_and_unverified_tokens(tmp_path, duplicate):
    row = receipt()
    if not duplicate:
        row["exact_tokens_verified"] = False
    path = tmp_path / "requests.jsonl"
    path.write_text((json.dumps(row) + "\n") * (2 if duplicate else 1))
    with pytest.raises(ValueError):
        comparison.receipt_work(path)


@pytest.mark.parametrize("mode", (None, "legacy", "position"))
def test_position_population_requires_per_request_evidence_of_the_actual_sampler(tmp_path, mode):
    row = receipt()
    if mode is not None:
        row["native_receipt"] = {"sampling_path": mode}
    path = tmp_path / "receipts.jsonl"
    path.write_text(json.dumps(row) + "\n")
    if mode == "position":
        assert comparison.receipt_work(path, sampling_mode="position")
    else:
        with pytest.raises(ValueError, match="registered position sampler"):
            comparison.receipt_work(path, sampling_mode="position")


def test_cleanup_never_signals_a_reused_pid_and_keeps_known_orphan_children(tmp_path, monkeypatch):
    parent_pid, child_pid = os.getpid(), os.getppid()
    parent = {"pid": parent_pid, "ppid": 1, "start_ticks": "old"}
    child = {"pid": child_pid, "ppid": 1, "start_ticks": "child"}
    current = {
        parent_pid: {"pid": parent_pid, "ppid": 1, "start_ticks": "reused"},
        child_pid: child,
    }
    monkeypatch.setattr(comparison.reservation, "process", current.get)
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: (
        row["pid"] in current and current[row["pid"]]["start_ticks"] == row["start_ticks"]
    ))
    signaled = []

    def signal_owned(pid, sig):
        signaled.append(pid)
        current.pop(pid)

    monkeypatch.setattr(comparison.os, "kill", signal_owned)
    comparison.cleanup(tmp_path, [parent, child], "completed_test")
    assert signaled == [child_pid]
    assert current[parent_pid]["start_ticks"] == "reused"
    report = json.loads((tmp_path / "owned_process_cleanup.json").read_bytes())
    assert report["reason"] == "completed_test" and report["remaining_processes"] == []


@pytest.mark.parametrize("release_after_kill_seconds", (20., None))
def test_cleanup_waits_for_slow_device_teardown_but_reports_a_persistent_process(
    tmp_path, monkeypatch, release_after_kill_seconds,
):
    identity = {"pid": 11010, "ppid": 1, "start_ticks": "owned", "state": "R"}
    clock, killed_at, signals = [0.], [None], []

    def current(pid):
        assert pid == identity["pid"]
        if (
            killed_at[0] is not None and release_after_kill_seconds is not None
            and clock[0] >= killed_at[0] + release_after_kill_seconds
        ):
            return None
        return {**identity, "state": "D"}

    def send(pid, sig):
        assert pid == identity["pid"]
        signals.append(sig)
        if sig == comparison.signal.SIGKILL:
            killed_at[0] = clock[0]

    monkeypatch.setattr(comparison, "owned_processes", lambda _rows: [identity])
    monkeypatch.setattr(comparison.reservation, "process", current)
    monkeypatch.setattr(comparison.reservation, "alive", lambda row: current(row["pid"]) is not None)
    monkeypatch.setattr(comparison.os, "kill", send)
    monkeypatch.setattr(comparison.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(comparison.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))
    if release_after_kill_seconds is None:
        with pytest.raises(RuntimeError, match="remain after cleanup"):
            comparison.cleanup(tmp_path, [identity], "persistent_test")
        remaining = json.loads((tmp_path / "owned_process_cleanup.json").read_bytes())["remaining_processes"]
        assert len(remaining) == 1 and remaining[0]["state"] == "D"
    else:
        comparison.cleanup(tmp_path, [identity], "slow_teardown_test")
        assert clock[0] >= killed_at[0] + release_after_kill_seconds
        assert not json.loads((tmp_path / "owned_process_cleanup.json").read_bytes())["remaining_processes"]
    assert signals == [comparison.signal.SIGTERM, comparison.signal.SIGKILL]


@pytest.mark.parametrize("failure", (
    None, "driver", "changed_work", "capacity", "no_tool_prefetch",
    "queued_no_tool_prefetch", "failed_predecessor", "source_slowdown", "capture_baseline",
    "orthogonal_controls", "telemetry_drain",
))
def test_supervisor_runs_cold_controls_and_stops_on_failures_before_advancing(
    reference, tmp_path, monkeypatch, failure,
):
    queued = failure == "queued_no_tool_prefetch"
    failed_predecessor = failure == "failed_predecessor"
    captured_reference = failure == "capture_baseline"
    order = (
        ["no_tool_prefetch"] if failure in (
            "no_tool_prefetch", "queued_no_tool_prefetch", "failed_predecessor",
        ) else list(comparison.ORDER)
    )
    if failure == "source_slowdown":
        order = ["native_matched", "no_tool_prefetch"]
    elif failure == "orthogonal_controls":
        order = ["scheduler_only", "kv_only"]
        failure = None
    elif captured_reference:
        order = list(comparison.DEFAULT_ORDER)
        assert "native_matched" not in order
        failure = None
    if failure in ("no_tool_prefetch", "queued_no_tool_prefetch"):
        failure = None
    registration = tmp_path / "registered.json"
    run = tmp_path / "run"
    plan = {
        "run_root": str(run), "order": order, "port": 18554,
        "reference_environment": reference["arms"]["full"],
        "engine_checkout": str(tmp_path / "engine"),
        "source_arm": str(tmp_path / "source"),
        "source_capacity": {"device_full_tokens": 1797968},
        "source_server_parameters": {"random_seed": 21, "max_running_requests": 8},
        "source_plan": {"nodes": 2, "requests": 1, "prompt_tokens": 12, "output_tokens": 3},
        "token_source_sha256": "tokens", "workflow_ids": ["w"],
        "quality_exclusions": [], "scope": "test",
    }
    if failure == "source_slowdown":
        plan.update(
            maximum_replay_slowdown=.05,
            source_metrics={"mean_jct_ms": 4000., "replay_duration_ms": 4000.},
        )
    elif captured_reference:
        plan["native_baseline"] = {
            "kind": "natural_native_source_capture", "mean_jct_ms": 10000.,
            "whole_run_output_tokens_per_second": .3,
            "metric_boundary": comparison.METRIC_BOUNDARY,
        }
    registration.write_text(json.dumps(plan))
    root = {"pid": 100, "ppid": 1, "start_ticks": "root", "state": "S"}
    scheduler = {"pid": 101, "ppid": 100, "start_ticks": "scheduler", "state": "S"}
    driver_identity = {"pid": 102, "ppid": 1, "start_ticks": "driver", "state": "S"}
    identities = {row["pid"]: row for row in (root, scheduler, driver_identity)}
    current = []
    launched, cleaned, configurations, drained = [], [], [], []
    predecessor_checks = []

    def previous_ready(_plan):
        predecessor_checks.append(1)
        if failed_predecessor:
            raise ValueError("predecessor failed")
        return len(predecessor_checks) >= 3

    def resources(_port):
        if queued:
            assert len(predecessor_checks) >= 3
        return {"ready": True}

    class Process:
        def __init__(self, pid, code):
            self.pid, self.code = pid, code

        def poll(self):
            return self.code

        def wait(self):
            return self.code

    def popen(command, **kwargs):
        if command[0] == "bash":
            label = Path(kwargs["env"]["RUN_ROOT"]).name
            arm = run / label
            current[:] = [label]
            launched.append(("server", label, kwargs["env"]))
            (arm / "server/native_telemetry_ready.json").write_text(json.dumps({"scheduler_pid": 101}))
            capacity = {"device_full_tokens": 123} if failure == "capacity" else plan["source_capacity"]
            (arm / "server/native_capacity_census.json").write_text(json.dumps({"capacity": capacity}))
            return Process(100, None)
        label = current[0]
        assert kwargs["env"]["PYTHONHASHSEED"] == "21"
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir()
        row = receipt()
        if failure == "changed_work" and label == "native_matched":
            row["output_sha256"] = "changed"
        path = output / "native_requests.jsonl"
        path.write_text(json.dumps(row) + "\n")
        result = {
            "causal_execution_completed": True, "exact_tokens_verified": True,
            "original_native_stopping_verified": True,
            "native_session_http_lifecycle_completed": True,
            "live_control_events_delivered": "--event-socket" in command,
            "replay_process_python_hash_seed": "21",
            "control_delivery": {"mode": (
                "per_workflow_harness_queued" if "--event-socket" in command else "trace_only"
            ), "workflows": {"w": {"accepted_batch_count": 1}} if "--event-socket" in command else {}},
            "completed_requests": 1, "source_plan": plan["source_plan"],
            "token_source": {"token_source_sha256": "tokens"},
            "workflow_elapsed_ms": {"w": 5000.},
            "native_requests_sha256": sha256_file(path),
            "started_monotonic_ms": 100., "finished_monotonic_ms": 5200.,
        }
        (output / "summary.json").write_text(json.dumps(result))
        (output / "runtime_events.replay.jsonl").write_text("".join(
            json.dumps({"kind": kind, "workflow_id": "w", "ts_ms": when}) + "\n"
            for kind, when in (("workflow_start", 150.), ("workflow_end", 5150.))
        ))
        launched.append(("driver", label, command))
        return Process(102, 1 if failure == "driver" else 0)

    def get(url, **_kwargs):
        if url.endswith("/health"):
            return NS(is_success=True)
        return NS(json=lambda: {
            **plan["source_server_parameters"],
            "enable_beliefkv_admission": current[0] != "native_matched",
            "enable_custom_logit_processor": True, "enable_session_radix_cache": True,
        })

    monkeypatch.setattr(comparison.subprocess, "Popen", popen)
    monkeypatch.setattr(comparison.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comparison.httpx, "get", get)
    monkeypatch.setattr(comparison.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(comparison.fresh, "check_hold", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(comparison.qualified, "resources", resources)
    monkeypatch.setattr(comparison.qualified, "command", lambda _command: "101")
    monkeypatch.setattr(comparison.qualified, "last_runtime_state",
                        lambda _path: {"tool_continuity": False})
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda mode, *_args, **_kwargs: configurations.append(mode))
    monkeypatch.setattr(comparison.reservation, "process", identities.get)
    monkeypatch.setattr(comparison, "owned_processes", lambda rows: (
        [root, scheduler, *(row for row in rows if row["pid"] == 102)]
        if rows else []
    ))

    def drain(arm, *, server, pulse):
        assert server.poll() is None
        assert launched[-1][:2] == ("driver", arm.name)
        assert (arm / "replay/summary.json").is_file()
        if failure == "telemetry_drain":
            raise ValueError("native telemetry did not drain before shutdown")
        drained.append(arm.name)
        pulse()

    def cleanup(arm, rows, reason):
        if reason == "complete_same_work_arm":
            assert arm.name in drained
        cleaned.append((arm.name, [row["pid"] for row in rows], reason))

    monkeypatch.setattr(comparison, "wait_for_telemetry_drain", drain)
    monkeypatch.setattr(comparison, "cleanup", cleanup)
    monkeypatch.setattr(comparison.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(comparison.signal, "signal", lambda *_args: None)
    if queued or failed_predecessor:
        monkeypatch.setattr(comparison, "predecessor_ready", previous_ready)
        monkeypatch.setattr(comparison, "predecessor_work", lambda _plan: {
            "r": (12, 3, "prompt", "output", {"type": "stop", "matched": 9}),
        })
    comparison.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    if failure is None:
        assert state["completed_arms"] == order
        assert state["status"] == "collected_performance_review_pending"
        assert len(cleaned) == len(order)
        assert drained == order
        report = json.loads((run / "causal_comparison.json").read_bytes())
        assert report["all_normal_tails_retained"] and not report["performance_accepted"]
        assert all(row["mean_jct_ms"] == 5000. for row in report["results"].values())
        if captured_reference:
            assert report["native_baseline"] == plan["native_baseline"]
            assert "native_matched" not in report["results"]
            assert report["results"]["full"]["captured_native_mean_jct_change_percent"] == -50.
            assert report["results"]["full"][
                "captured_native_output_throughput_change_percent"
            ] == pytest.approx(100.)
            assert report["results"]["full"]["replay_duration_ms"] == 5100.
            assert report["results"]["full"]["workflow_event_makespan_ms"] == 5000.
            assert report["results"]["full"]["replay_driver_wall_output_tokens_per_second"] == pytest.approx(3. / 5.1)
        commands = [row for row in launched if row[0] == "driver"]
        assert [("--event-socket" in command) for _, _, command in commands] == [
            label != "native_matched" for label in order
        ]
        if "join_priority_only" in order:
            assert "join_priority_only" in configurations
        if order == ["no_tool_prefetch"]:
            environment = next(row[2] for row in launched if row[0] == "server")
            assert environment["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
            assert environment["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "1"
    else:
        assert state["status"] == "stopped_for_investigation"
        assert not state.get("performance_accepted")
        assert all(label not in state["completed_arms"] for label in ("join_priority_only", "no_background_prepare"))
        assert cleaned[-1][2] == "causal_comparison_failure_or_stop"
        if failure == "changed_work":
            assert state["completed_arms"] == ["full"]
            assert "work changed" in state["error"]
        elif failure == "capacity":
            assert not any(row[0] == "driver" for row in launched)
        elif failure == "failed_predecessor":
            assert not launched
            assert "predecessor failed" in state["error"]
        elif failure == "telemetry_drain":
            assert not state["completed_arms"]
            assert not drained
            assert "did not drain" in state["error"]
        elif failure == "source_slowdown":
            assert state["completed_arms"] == ["native_matched"]
            assert not any(row[1] == "no_tool_prefetch" for row in launched)
            check = json.loads((run / "native_matched/source_replay_calibration.json").read_bytes())
            assert check["accepted"] is False
            assert "slowdown tolerance" in state["error"]
