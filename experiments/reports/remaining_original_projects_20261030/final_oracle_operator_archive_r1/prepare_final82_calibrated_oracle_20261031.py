"""Freeze the last v6 Oracle against the completed matching Full control."""

import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    root = args.checkout.resolve()
    sys.path.insert(0, str(root))
    from beliefkv.experiments.internal_comparison import arm_environment
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
    from scripts import run_causal_comparison as causal
    from scripts.audit_native_system_costs import distribution
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts.prepare_calibrated_combination_oracle import (
        calibrated_artifact, configuration, context_prefill_costs,
    )
    from scripts.prepare_sustained_oracle_admission import calibrate_costs
    from scripts.review_matched_work_ranking import verify_calibrated_combination_pair
    from scripts.run_causal_gpu_replay import source_plan
    from scripts.train_workflow_work import write
    from scripts.verify_measured_execution import review_matches_execution, verify_execution

    main_root = Path("/home/longhao/experiment/BeliefKV")
    reports = main_root / "experiments/reports/remaining_original_projects_20261030"
    base_dir = reports / "final_oracle_original82_traffic_r1"
    here = reports / "final82_completed_control_oracle_r1"
    control = main_root.parent / "ops/workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json"
    review_path = reports / "measured_cohort_workflow_final82_initial12_mean70_predictive_control_r1/review.json"
    cohort_path = base_dir / "analysis_cohort.json"
    if here.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19 or git_source_state(root)["dirty"]
    ):
        raise ValueError("preserve preparation and require clean nice19 CPU-only execution")
    complete = verify_execution(control, cohort_path)
    review = causal.load(review_path)
    review_matches_execution(review, complete)
    plan, base = causal.load(control), causal.load(base_dir / "full.json")
    original = causal.load(base_dir / "control_oracle.json")
    if plan["order"] != ["full"] or original["remaining_work_ranking_enabled"] is not False:
        raise ValueError("matching nonranking control is required")
    arm = Path(plan["run_root"]) / "full"
    batches, transfers = arm / "server/runtime_audit.jsonl", arm / "server/transfer_telemetry.jsonl"
    reader, groups = SealedRecords(), defaultdict(list)
    bounds = [8192, 32768, 65536, 131072]
    names = ["8192", "32768", "65536", "131072", "at_least_131072"]
    totals = defaultdict(lambda: [0., 0])
    weighted, steps = 0., 0

    def observed():
        nonlocal weighted, steps
        for row in reader(batches):
            if row.get("event") == "gpu_service_sample":
                if row["phase"] == "decode":
                    weighted += row["batch_size"] * row["service_elapsed_ms"]
                    steps += row["batch_size"]
                elif row["phase"] == "prefill" and len(row["request_samples"]) == 1:
                    member = row["request_samples"][0]
                    tokens, prefix = member["token_delta"], member["sequence_tokens_before"]
                    if type(tokens) is not int or tokens < 0 or type(prefix) is not int or prefix < 0:
                        raise ValueError("invalid native prefill geometry")
                    if tokens >= 256:
                        name = names[bisect_left(bounds, prefix + tokens)]
                        groups[name].append(row["service_elapsed_ms"] / tokens)
                        totals[name][0] += row["service_elapsed_ms"]
                        totals[name][1] += tokens
            yield row

    costs, support = calibrate_costs(observed(), reader(transfers))
    if not steps or not math.isclose(
        weighted / steps / 1000.,
        review["model_cost_calibration"]["model_seconds_per_work_token"],
        abs_tol=1e-12, rel_tol=0.,
    ) or any(len(groups[name]) < 30 for name in names):
        raise ValueError("complete independent cost calibration lacks its declared support")
    inputs = (
        control, review_path, cohort_path, Path(complete["certificate"]),
        base_dir / "full.json", base_dir / "control_oracle.json", Path(__file__),
        root / "scripts/prepare_sustained_oracle_admission.py",
        root / "scripts/prepare_calibrated_combination_oracle.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in inputs}
    calibration = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "registration": str(control),
        "configured_oracle": {
            "prefill_token_weight": .01,
            "model_seconds_per_work_token": original["model_seconds_per_work_token"],
        },
        "completed_control_cost_proxy": costs, "calibration_evidence": support,
        "completed_control_weighted_decode_step_ms": weighted / steps,
        "input_jsonl_prefixes": reader.prefixes, "inputs_sha256": pins,
        "scope": {
            "same_completed_control_not_partial_oracle": True,
            "fixed_measured_complete_other_workflows_right_censored": True,
            "worker_intervals_may_include_stalls_and_CPU_submission": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    context = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_registration": str(control),
        "frozen_current_prefill_ms_per_token": costs["prefill_ms_per_token"],
        "single_request_prefill_batches_at_least_256_tokens_by_context_after_upper_bound": {
            name: {"batch_count": len(groups[name]), "ms_per_input_token": distribution(groups[name]),
                   "total_worker_ms": totals[name][0], "total_input_tokens": totals[name][1],
                   "weighted_ms_per_input_token": totals[name][0] / totals[name][1]}
            for name in names
        },
        "input_jsonl_prefixes": {str(batches): reader.prefixes[str(batches)]},
        "inputs_sha256": pins, "worker_not_pure_GPU_cost": True, "GPU_action": False,
        "scope": {
            "same_completed_control": True,
            "sequence_tokens_before_is_native_prefix_not_total_input": True,
            "GPU_action": False, "worker_not_pure_GPU_cost": True,
        },
    }
    context_prefill_costs(context, calibration)
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed control telemetry changed")
    calibration_path, context_path = here / "calibration.json", here / "context_prefill.json"
    write(calibration_path, calibration)
    write(context_path, context)
    original["remaining_work_ranking_enabled"] = True
    artifact = calibrated_artifact(
        original, calibration, calibration_path=calibration_path,
        calibration_sha256=sha256_file(calibration_path), context_calibration=context,
        context_calibration_path=context_path, context_calibration_sha256=sha256_file(context_path),
        include_unreferenced_capacity=True, allow_deep_prefix_aggregate=True,
        allow_feasible_resource_alternatives=True, full_batch_cost=True, plan_device_capacity=True,
    )
    source = Path(base["common_environment"]["WORKLOAD_MANIFEST"]).parent
    graph, _expected, evidence = source_plan(source)
    WorkflowWorkOracleArtifact(artifact).validate_source(source, graph, evidence)
    artifact_path = here / "oracle.json"
    write(artifact_path, artifact)
    candidate = configuration(
        base, artifact_path=artifact_path, artifact_sha256=sha256_file(artifact_path),
        control_path=control, calibration_path=calibration_path, decode_cost_basis="full_batch",
    )
    candidate["status"] = "final_new_workflow_capacity_v6_oracle"
    candidate["candidate_provenance"].update(
        final_attempt_gate_mean_JCT_reduction=.05,
        final_attempt_gate_common_window_throughput_fraction=.98,
        no_more_workload_or_policy_grid_search=True,
        matching_new_workflow_control_costs=True,
    )
    verify_calibrated_combination_pair(base, candidate)
    settings = arm_environment(candidate["common_environment"], "full")
    for name in causal.SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    preflight = causal.preflight_runtime(settings, settings["MODEL_PATH"])
    env = causal.qualified.driver_environment({"environment": settings}, os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "99999"
    program = (
        "import json,sys; from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime; "
        "r=NativeAdmissionRuntime(event_socket_path=sys.argv[1],model_path=sys.argv[2]); "
        "c=r._agent_admission._combination; "
        "print(json.dumps({'plan_device_capacity':c.plan_device_capacity,"
        "'decode_cost_basis':c.costs.decode_cost_basis,'cost_identity':c.cost_identity})); r.close()"
    )
    with tempfile.TemporaryDirectory(prefix="final82-oracle-cpu-") as temporary:
        result = subprocess.run(
            [sys.executable, "-c", program, str(Path(temporary) / "events.sock"), settings["MODEL_PATH"]],
            cwd=root, env=env, capture_output=True, text=True, timeout=60,
        )
    actual = json.loads(result.stdout) if result.returncode == 0 else None
    if actual != {
        "plan_device_capacity": True, "decode_cost_basis": "full_batch",
        "cost_identity": artifact["admission_cost_profile"]["identity"],
    }:
        write(here / "constructor_failure.json", {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        })
        raise ValueError("actual final Oracle constructor does not match its declared profile")
    config_path = here / "full.json"
    write(config_path, candidate)
    write(here / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": git_source_state(root),
        "actual_runtime_preflight": preflight, "actual_v6_controller": actual,
        "measured_requests": complete["measured_requests"], "measured_outputs": complete["measured_output_tokens"],
        "new_workflow_count": graph.summary()["workflows"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            *inputs, calibration_path, context_path, artifact_path, config_path,
        )},
        "GPU_action": False, "performance_accepted": False,
        "formal_workload_accepted": False, "no_more_source_or_policy_search": True,
    })
    print(json.dumps({"configuration": str(config_path), "actual_v6_controller": actual,
                      "context_support": {name: len(groups[name]) for name in names}}), flush=True)


if __name__ == "__main__":
    main()
