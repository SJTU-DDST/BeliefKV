"""Bind planned-capacity Oracle to actual original69 control costs, CPU only."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-device-plan-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
BASE = REPORTS / "original69_initial12_mean70_prepare_r1"
COST = REPORTS / "original69_completed_control_costs_r1"
HERE = REPORTS / "original69_planned_capacity_oracle_prepare_r1"
CONTROL = OPS / "workflow_original69_initial12_mean70_predictive_control_20261031_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle, WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as causal
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact, configuration
from scripts.review_matched_work_ranking import verify_calibrated_combination_pair
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve preparation and use nice19 CPU-only execution")
    base = causal.load(BASE / "full.json")
    original = causal.load(BASE / "control_oracle.json")
    if original["remaining_work_ranking_enabled"] is not False:
        raise ValueError("the matching control changed")
    original["remaining_work_ranking_enabled"] = True
    calibration_path, context_path = COST / "calibration.json", COST / "context_prefill.json"
    calibration, context = causal.load(calibration_path), causal.load(context_path)
    if calibration["registration"] != str(CONTROL):
        raise ValueError("the new costs came from a different execution")
    for report in (calibration, context):
        for name, digest in report["inputs_sha256"].items():
            if sha256_file(Path(name)) != digest:
                raise ValueError("cost evidence changed: " + name)
        for name, prefix in report["input_jsonl_prefixes"].items():
            if Path(name).stat().st_size != prefix["complete_prefix_bytes"] or (
                sha256_file(Path(name)) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed control telemetry changed")
    source = Path(base["common_environment"]["WORKLOAD_MANIFEST"]).parent
    graph, expected, evidence = source_plan(source)
    kwargs = dict(
        calibration_path=calibration_path, calibration_sha256=sha256_file(calibration_path),
        context_calibration=context, context_calibration_path=context_path,
        context_calibration_sha256=sha256_file(context_path),
        include_unreferenced_capacity=True, allow_deep_prefix_aggregate=True,
        allow_feasible_resource_alternatives=True, full_batch_cost=True,
    )
    strict, planned = (
        calibrated_artifact(original, calibration, plan_device_capacity=enabled, **kwargs)
        for enabled in (False, True)
    )
    normalized = deepcopy(planned)
    normalized["admission_cost_profile"] = strict["admission_cost_profile"]
    normalized["calibrated_model_time"] = strict["calibrated_model_time"]
    if normalized != strict:
        raise ValueError("capacity planning changed the intrinsic workflow paths")
    for artifact in (strict, planned):
        WorkflowWorkOracleArtifact(artifact).validate_source(source, graph, evidence)
    HERE.mkdir(parents=True, exist_ok=False)
    strict_path, artifact_path = HERE / "strict_capacity_reference.json", HERE / "oracle.json"
    write(strict_path, strict)
    write(artifact_path, planned)
    config = configuration(
        base, artifact_path=artifact_path, artifact_sha256=sha256_file(artifact_path),
        control_path=CONTROL, calibration_path=calibration_path, decode_cost_basis="full_batch",
    )
    config["status"] = "CPU_planned_capacity_with_completed_original69_costs"
    config["candidate_provenance"].update(
        matching_original69_control_completed=True, matching_original69_cost_calibration_claimed=True,
        capacity_planning_uses_observed_pool_geometry=True,
        current_free_summary_does_not_grant_allocation_authority=True,
        workload_ready_supply_and_background_tail_repairs_pending=True,
    )
    config["gpu_execution"].update(
        registered=False, no_automatic_GPU_followup=True, requires_workload_review=True,
        requires_fresh_position_and_sampling=True,
    )
    verify_calibrated_combination_pair(base, config)
    write(HERE / "full.json", config)
    settings = arm_environment(config["common_environment"], "full")
    for name in causal.SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    entry = causal.preflight_runtime(settings, config["common_environment"]["MODEL_PATH"])
    program = (
        "import json,sys; sys.path.insert(0,sys.argv[1]); "
        "from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime; "
        "r=NativeAdmissionRuntime(event_socket_path=sys.argv[2],model_path=sys.argv[3]); "
        "c=r._agent_admission._combination; "
        "print(json.dumps({'plan_device_capacity':c.plan_device_capacity,"
        "'decode_cost_basis':c.costs.decode_cost_basis,'cost_identity':c.cost_identity})); r.close()"
    )
    environment = causal.qualified.driver_environment({"environment": settings}, os.environ)
    environment["CUDA_VISIBLE_DEVICES"] = "99999"
    with tempfile.TemporaryDirectory(prefix="original69-v6-cpu-") as temporary:
        result = subprocess.run(
            [sys.executable, "-c", program, str(ROOT), str(Path(temporary) / "events.sock"),
             config["common_environment"]["MODEL_PATH"]],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
        )
    actual = json.loads(result.stdout) if result.returncode == 0 else None
    expected_controller = {
        "plan_device_capacity": True, "decode_cost_basis": "full_batch",
        "cost_identity": planned["admission_cost_profile"]["identity"],
    }
    if actual != expected_controller:
        write(HERE / "constructor_failure.json", {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        })
        raise ValueError("actual planned-capacity constructor did not match the artifact")
    predictors = [
        WorkflowWorkOracle(path, sha256_file(path)) for path in (strict_path, artifact_path)
    ]
    paths = {}
    try:
        for workflow in original["workflows"]:
            values = [item.prediction(workflow).remaining_path_s for item in predictors]
            if values[0] != values[1]:
                raise ValueError("capacity planning changed remaining work")
            paths[workflow] = values[0]
    finally:
        for predictor in predictors:
            predictor.close()
    write(HERE / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": git_source_state(ROOT),
        "actual_runtime_preflight": entry, "actual_controller": actual,
        "source_workflows": len(original["workflows"]), "source_requests": len(expected),
        "strict_and_planned_initial_remaining_paths_equal": paths,
        "matching_original69_control_and_costs_verified": True,
        "native_allocation_locks_Host_and_DMA_authority_unchanged": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            CONTROL, calibration_path, context_path, BASE / "full.json",
            BASE / "control_oracle.json", BASE / "analysis_cohort.json", BASE / "arrivals.json",
            strict_path, artifact_path, HERE / "full.json", Path(__file__),
        )},
        "GPU_action": False, "GPU_followup_registered": False,
        "formal_workload_accepted": False, "performance_accepted": False,
    })
    print({
        "workflows": len(paths), "requests": len(expected), "actual_controller": actual,
        "matching_original69_costs": True, "GPU_followup_registered": False,
    }, flush=True)


if __name__ == "__main__":
    main()
