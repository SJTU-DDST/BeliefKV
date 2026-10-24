import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_position_replay_calibration as calibration
from tests.test_causal_comparison import reference


@pytest.mark.parametrize("max_tasks_per_project", (1, 2))
def test_calibration_rejects_shared_projects_before_qualification_or_gpu(
    tmp_path, monkeypatch, max_tasks_per_project,
):
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps({
        "requested_workflows": 2, "max_tasks_per_project": max_tasks_per_project,
        "workloads": [
            {"repo": "locobench/shared-project", "instance_id": f"task-{index}",
             "task_kind": "locobench_agent"}
            for index in range(2)
        ],
    }))
    registration = tmp_path / "source.json"
    registration.write_text(json.dumps({
        "source_profile": "balanced_join",
        "environment": {"ROOT_COUNT": "2", "WORKLOAD_MANIFEST": str(manifest)},
    }))
    args = SimpleNamespace(
        registration=tmp_path / "queue.json", run_root=tmp_path / "run",
        comparison_registration=tmp_path / "comparison.json",
        comparison_root=tmp_path / "comparison", source_registration=registration,
        experiment_config=None,
    )

    def unexpected_verification(_plan):
        pytest.fail("shared-project workload reached source or GPU qualification")

    monkeypatch.setattr(calibration.comparison, "verify_sources", unexpected_verification)
    with pytest.raises(ValueError, match="source workload"):
        calibration.register(args)
    assert not args.registration.exists()
    assert not args.run_root.exists()


@pytest.fixture
def source(tmp_path):
    registration = tmp_path / "source.json"
    registration.write_text(json.dumps({"run_root": "/source"}))
    state = {
        "run_root": "/source", "status": "recording_native",
        "watcher": {"pid": 10}, "driver": {"pid": 11},
    }
    path = registration.with_suffix(".state.json")
    path.write_text(json.dumps(state))
    return {"source_registration": str(registration)}, state, path


@pytest.mark.parametrize("status,live_pid,expected", (
    ("recording_native", 10, False),
    ("waiting_gpu_capture", 10, False),
    ("capture_preflight", 10, False),
    ("source_captured_gpu_replay_pending", 10, False),
    ("source_captured_gpu_replay_pending", 11, False),
    ("source_captured_gpu_replay_pending", None, True),
))
def test_calibration_waits_for_source_quality_and_actual_process_release(
    source, monkeypatch, status, live_pid, expected,
):
    plan, state, path = source
    state.update(
        status=status, original_exit_code=0, source_selection={"accepted": True},
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(calibration.reservation, "alive", lambda row: row["pid"] == live_pid)
    assert calibration.source_ready(plan) is expected


@pytest.mark.parametrize("change", ("rejected", "failed", "dead", "nonzero", "other_source", "unselected"))
def test_failed_or_unqualified_source_cannot_start_gpu_replay(source, monkeypatch, change):
    plan, state, path = source
    state.update(
        status="source_captured_gpu_replay_pending", original_exit_code=0,
        source_selection={"accepted": True},
    )
    if change == "rejected":
        state["status"] = "source_captured_selection_rejected"
    elif change == "failed":
        state["status"] = "capture_failed"
    elif change == "dead":
        state["status"] = "recording_native"
    elif change == "nonzero":
        state["original_exit_code"] = 1
    elif change == "other_source":
        state["run_root"] = "/another"
    else:
        state["source_selection"]["accepted"] = False
    path.write_text(json.dumps(state))
    monkeypatch.setattr(calibration.reservation, "alive", lambda _row: False)
    with pytest.raises(ValueError):
        calibration.source_ready(plan)


@pytest.fixture
def complete_source(tmp_path):
    run = tmp_path / "capture"
    (run / "native_capture/client_36").mkdir(parents=True)
    registration = tmp_path / "complete.json"
    registration.write_text(json.dumps({"run_root": str(run)}))
    registration.with_suffix(".state.json").write_text(json.dumps({
        "run_root": str(run), "status": "source_captured_selection_rejected",
        "original_exit_code": 0, "watcher": {"pid": 10}, "driver": {"pid": 11},
    }))
    summary = {
        "workflow_count": 36, "completed_workflows": 36, "delegation_round_count": 75,
        "duration_seconds": 4030.,
        "workflows": [
            {"workflow_id": str(index), "native_agent_jct_eligible": True,
             "duration_seconds": 1000. + index * 85}
            for index in range(36)
        ],
    }
    (run / "native_capture/client_36/summary.json").write_text(json.dumps(summary))
    (run / "token_evidence.json").write_text(json.dumps({"token_evidence_complete": True}))
    (run / "causal_evidence.json").write_text(json.dumps({"causal_graph_compiled": True}))
    return registration, run, summary


@pytest.mark.parametrize("live_pid,expected", ((10, False), (11, False), (None, True)))
def test_reviewed_complete_source_still_requires_actual_process_release(
    complete_source, monkeypatch, live_pid, expected,
):
    registration, _run, _summary = complete_source
    metrics = calibration.complete_source_metrics(registration)
    assert metrics["joins"] == 75 and not metrics["original_selection"]["accepted"]
    plan = {
        "source_registration": str(registration), "source_selection_policy": "complete",
        "reviewed_source_metrics": metrics,
    }
    monkeypatch.setattr(calibration.reservation, "alive", lambda row: row["pid"] == live_pid)
    assert calibration.source_ready(plan) is expected


@pytest.mark.parametrize("change", (
    "guard", "incomplete", "duplicate", "zero_joins", "token_error", "graph_error",
    "nonzero", "running", "changed_summary", "missing_identity", "invalid_duration",
))
def test_complete_source_review_does_not_relax_fidelity_or_completion(
    complete_source, monkeypatch, change,
):
    registration, run, summary = complete_source
    metrics = calibration.complete_source_metrics(registration)
    plan = {
        "source_registration": str(registration), "source_selection_policy": "complete",
        "reviewed_source_metrics": metrics,
    }
    if change == "guard":
        summary["workflows"][0]["native_agent_jct_eligible"] = False
    elif change == "incomplete":
        summary["completed_workflows"] = 35
    elif change == "duplicate":
        summary["workflows"][0]["workflow_id"] = "1"
    elif change == "zero_joins":
        summary["delegation_round_count"] = 0
    elif change == "missing_identity":
        summary["workflows"][0]["workflow_id"] = None
    elif change == "invalid_duration":
        summary["workflows"][0]["duration_seconds"] = -1.
    elif change == "token_error":
        (run / "token_evidence.json").write_text(json.dumps({"token_evidence_complete": False}))
    elif change == "graph_error":
        (run / "causal_evidence.json").write_text(json.dumps({"causal_graph_compiled": False}))
    elif change in ("nonzero", "running"):
        path = registration.with_suffix(".state.json")
        state = json.loads(path.read_bytes())
        state.update(original_exit_code=1) if change == "nonzero" else state.update(status="recording_native")
        path.write_text(json.dumps(state))
    else:
        summary["workflows"][0]["duration_seconds"] += 1
    (run / "native_capture/client_36/summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(calibration.reservation, "alive", lambda _row: False)
    with pytest.raises(ValueError):
        calibration.source_ready(plan)


@pytest.mark.parametrize("roots", (16, 24, 32, 36))
def test_predeclared_oneshot_metrics_use_the_actual_complete_population(
    complete_source, monkeypatch, roots,
):
    registration, run, summary = complete_source
    original = json.loads(registration.read_bytes())
    original.update(
        environment={"ROOT_COUNT": str(roots)}, source_profile="nested_oneshot",
    )
    registration.write_text(json.dumps(original))
    summary["workflow_count"] = summary["completed_workflows"] = roots
    summary["workflows"] = summary["workflows"][:roots]
    summary["delegation_round_count"] = 30
    folder = run / f"native_capture/client_{roots}"
    folder.mkdir(exist_ok=True)
    (folder / "summary.json").write_text(json.dumps(summary))
    path = registration.with_suffix(".state.json")
    state = json.loads(path.read_bytes())
    state.update(
        status="source_captured_gpu_replay_pending",
        source_selection={"profile": "nested_oneshot", "accepted": True},
    )
    path.write_text(json.dumps(state))
    monkeypatch.setattr(calibration.reservation, "alive", lambda _row: False)
    metrics = calibration.complete_source_metrics(registration)
    assert metrics["workflow_count"] == roots and metrics["joins"] == 30
    assert metrics["original_selection"]["accepted"] is True
    assert calibration.source_ready({
        "source_registration": str(registration), "source_selection_policy": "predeclared",
    }) is True


@pytest.mark.parametrize("profile", (
    calibration.capture.NESTED_IN_GRAPH_PROFILE,
    calibration.capture.DYNAMIC_NESTED_IN_GRAPH_PROFILE,
))
@pytest.mark.parametrize("change", (None, "wrong_profile", "guard", "incomplete"))
def test_predeclared_watch_uses_registered_profile_before_token_or_gpu_work(
    tmp_path, monkeypatch, profile, change,
):
    roots = [f"workflow-{index}" for index in range(24)]
    arm = tmp_path / "source"
    (arm / "client_24").mkdir(parents=True)
    summary = {
        "completed_workflows": 24, "subagent_fanout_profile": profile,
        "workflows": [
            {"workflow_id": value, "native_agent_jct_eligible": True} for value in roots
        ],
    }
    if change == "wrong_profile":
        summary["subagent_fanout_profile"] = "unregistered-profile"
    elif change == "guard":
        summary["workflows"][0]["native_agent_jct_eligible"] = False
    elif change == "incomplete":
        summary["completed_workflows"] = 23
    (arm / "client_24/summary.json").write_text(json.dumps(summary))
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "environment": {"ROOT_COUNT": "24", "FANOUT_PROFILE": profile},
    }))
    registration = tmp_path / "calibration.json"
    registration.write_text(json.dumps({
        "source_registration": str(source), "source_arm": str(arm),
        "source_selection_policy": "predeclared", "run_root": str(tmp_path / "calibration"),
    }))
    graph = SimpleNamespace(
        nodes=[
            SimpleNamespace(event=SimpleNamespace(
                kind=calibration.capture.Kind.WORKFLOW_START, workflow_id=value,
            )) for value in roots
        ],
        summary=lambda: {"event_counts": {"join_create": 33}},
    )
    monkeypatch.setattr(calibration.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(calibration.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(calibration, "source_ready", lambda _plan: True)
    monkeypatch.setattr(calibration, "complete_source_metrics", lambda _path: {})
    monkeypatch.setattr(calibration.controls, "source_plan", lambda _arm: (graph, {}, {}))
    reached_tokens = []

    def token_boundary(*_args):
        reached_tokens.append(True)
        raise RuntimeError("CPU stopped before token verification and GPU work")

    monkeypatch.setattr(calibration.controls, "CapturedTokenCorpus", token_boundary)
    monkeypatch.setattr(
        calibration.qualified, "resources",
        lambda _port: pytest.fail("source profile test must never reach GPU checks"),
    )
    calibration.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert state["status"] == "stopped_for_investigation"
    assert reached_tokens == ([True] if change is None else [])
    expected = (
        "CPU stopped before token verification and GPU work"
        if change is None else "predeclared one-shot source is incomplete or guarded"
    )
    assert expected in state["error"]


def test_queued_oneshot_command_reaches_the_real_comparison_parser_without_native_replay(
    tmp_path, monkeypatch,
):
    from scripts import run_causal_comparison

    spec = calibration.ROOT / "configs/migration/qwen35_locobench_oneshot_24root_running24_20261024.json"
    plan = {
        "reference_registration": str(tmp_path / "reference.json"),
        "cpu_report": str(tmp_path / "cpu.xml"), "cpu_sources": str(tmp_path / "sources.json"),
        "engine_cpu_checkout": str(tmp_path / "engine"),
        "engine_evidence": {"report": str(tmp_path / "engine.xml"),
                            "sources": str(tmp_path / "engine-sources.json")},
        "comparison_registration": str(tmp_path / "comparison.json"),
        "comparison_root": str(tmp_path / "comparison"),
        "port": 18566, "max_running_requests": 24,
        "experiment_config": str(spec), "comparison_order": ["full", "join_priority_only"],
    }
    command = calibration.comparison_command(
        plan, tmp_path / "calibration", tmp_path / "source", calibration.ROOT,
    )
    assert "native_matched" not in command
    assert "--maximum-replay-slowdown" not in command
    observed = []
    monkeypatch.setattr(run_causal_comparison, "register", observed.append)
    monkeypatch.setattr(run_causal_comparison.sys, "argv", command[1:])
    run_causal_comparison.main()
    [args] = observed
    assert args.arms == ["full", "join_priority_only"]
    assert args.max_running_requests == 24
    assert args.tool_prefetch_priority_ms == 1500.
    assert args.experiment_config == spec
    assert args.sampling_mode == "position"


@pytest.mark.parametrize("change", (
    None, "wrong_sampler", "missing_request", "different_stop_count", "changed_receipts",
    "unverified_tokens", "missing_server",
))
def test_gpu_evidence_requires_actual_position_receipts_and_all_native_stops(tmp_path, change):
    arm = tmp_path / "gpu"
    (arm / "smoke").mkdir(parents=True)
    requests = arm / "smoke/native_requests.jsonl"
    rows = [{
        "request_id": str(index), "prompt_tokens": 12, "output_tokens": 3,
        "prompt_sha256": "input", "output_sha256": "output",
        "native_finish_reason": {"type": "stop", "matched": 9},
        "exact_tokens_verified": True, "native_receipt": {"sampling_path": "position"},
    } for index in range(24)]
    if change == "wrong_sampler":
        rows[0]["native_receipt"]["sampling_path"] = "legacy"
    elif change == "missing_request":
        rows.pop()
    elif change == "unverified_tokens":
        rows[0]["exact_tokens_verified"] = False
    requests.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {
        "verified_requests": 24, "native_data_plane_sample_verified": True,
        "native_stop_requests": 24, "native_requests_sha256": sha256_file(requests),
    }
    if change == "different_stop_count":
        summary["native_stop_requests"] = 23
    elif change == "changed_receipts":
        summary["native_requests_sha256"] = "changed"
    plan = {
        "source_sha256": {name: "qualified" for name in calibration.SAMPLING_MODULES},
        "source_arm": "/source/native_capture", "source_registration": "/source.json",
    }
    server = None if change == "missing_server" else {"pid": 10}
    if change is None:
        result = calibration.smoke_result(plan, summary, arm, {}, server, {"pid": 11})
        assert result["native_position_sampling_verified"] and result["gpu_process_verified"]
        assert result["requests"] == result["native_stop_requests"] == 24
        assert not result["performance_accepted"]
        client = json.loads(Path(result["client_registration"]).read_bytes())
        assert client["unchanged_server_modules"] == plan["source_sha256"]
    else:
        with pytest.raises(ValueError):
            calibration.smoke_result(plan, summary, arm, {}, server, {"pid": 11})


@pytest.mark.parametrize("frontier", (False, True))
def test_predeclared_calibration_accepts_the_selected_policy_but_runs_native(
    tmp_path, monkeypatch, reference, frontier,
):
    from contextlib import nullcontext

    roots = [f"workflow-{index}" for index in range(18)]
    arm = tmp_path / "source"
    (arm / "client_18").mkdir(parents=True)
    (arm / "server").mkdir()
    (arm / "client_18/summary.json").write_text(json.dumps({
        "completed_workflows": 18,
        "subagent_fanout_profile": calibration.capture.DYNAMIC_NESTED_IN_GRAPH_PROFILE,
        "workflows": [
            {"workflow_id": value, "native_agent_jct_eligible": True} for value in roots
        ],
    }))
    (arm / "server/native_capacity_census.json").write_text(json.dumps({
        "capacity": {
            "pool_layout": "static_full_only",
            "device_full_bytes": 1200, "device_full_tokens": 12,
            "device_mamba_bytes": 0, "device_mamba_slots": 0,
            "device_total_bytes": 1200,
            "host_full_bytes": 2400, "host_full_tokens": 24,
            "host_mamba_bytes": 0, "host_mamba_slots": 0, "host_total_bytes": 2400,
        },
    }))
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "environment": {
            "ROOT_COUNT": "18",
            "FANOUT_PROFILE": calibration.capture.DYNAMIC_NESTED_IN_GRAPH_PROFILE,
        },
    }))
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(reference))
    spec = calibration.ROOT / (
        "configs/migration/qwen3_coder_18project_root_phase"
        + ("_frontier" if frontier else "") + "_20261024.json"
    )
    run = tmp_path / "calibration"
    registration = tmp_path / "calibration.json"
    registration.write_text(json.dumps({
        "source_registration": str(source), "source_arm": str(arm),
        "source_selection_policy": "predeclared", "run_root": str(run),
        "reference_registration": str(reference_path), "experiment_config": str(spec),
        "engine_checkout": str(tmp_path / "engine"),
        "max_running_requests": 24, "port": 18601,
    }))
    graph = SimpleNamespace(
        nodes=[
            SimpleNamespace(event=SimpleNamespace(
                kind=calibration.capture.Kind.WORKFLOW_START, workflow_id=value,
            )) for value in roots
        ],
        summary=lambda: {"event_counts": {"join_create": 30}},
    )
    monkeypatch.setattr(calibration.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(calibration.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(calibration.fresh, "check_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(calibration, "source_ready", lambda _plan: True)
    monkeypatch.setattr(calibration, "complete_source_metrics", lambda _path: {})
    monkeypatch.setattr(calibration.controls, "source_plan", lambda _arm: (graph, {}, {}))
    monkeypatch.setattr(calibration.controls, "CapturedTokenCorpus", lambda *_args: nullcontext())
    monkeypatch.setattr(calibration.qualified, "resources", lambda _port: {"ready": True})
    monkeypatch.setattr(calibration.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        calibration.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("CPU regression must not launch a GPU server"),
    )
    original = calibration.controls.environment
    observed = []

    def native_boundary(*args, **kwargs):
        settings, flags, socket = original(*args, **kwargs)
        observed.append((settings, flags, socket, kwargs))
        raise RuntimeError("CPU stopped after the actual calibration environment")

    monkeypatch.setattr(calibration.controls, "environment", native_boundary)
    calibration.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert "CPU stopped after the actual calibration environment" in state["error"]
    [(settings, flags, socket, kwargs)] = observed
    assert kwargs.get("execution_frontier_restore", False) is frontier
    assert settings["BELIEFKV_INTERNAL_COMPARISON_MODE"] == "native_matched"
    assert settings["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert settings["ROOT_WORK_BUDGET_WARNING_CALLS"] == "16"
    assert settings["ROOT_WORK_BUDGET_TARGET_CALLS"] == "24"
    assert settings["SERVER_RANDOM_SEED"] == settings["SAMPLING_SEED"] == "21"
    assert "--enable-beliefkv-admission" not in flags
    assert socket is None
