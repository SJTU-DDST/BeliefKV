"""Prepare an explicit calibrated-cost/set-selection oracle on unchanged traffic."""

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.runtime.workflow_work_oracle import COST_KIND, KIND, WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as causal
from scripts.review_matched_work_ranking import verify_ranking_only_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def calibrated_artifact(original, calibration, *, calibration_path, calibration_sha256):
    WorkflowWorkOracleArtifact(original)
    if (
        original["kind"] != KIND
        or original.get("remaining_work_ranking_enabled") is not True
        or original.get("admission_order") != "global_ready_remaining_path"
        or original.get("free_dispatch_coalescing_window_ms") != 2000
        or calibration.get("configured_oracle") != {
            "prefill_token_weight": .01,
            "model_seconds_per_work_token": original["model_seconds_per_work_token"],
        }
        or calibration.get("scope", {}).get("same_completed_control_not_partial_oracle") is not True
    ):
        raise ValueError("calibration requires the matching completed two-second control")
    costs = calibration["completed_control_cost_proxy"]
    CombinationCosts(**costs)
    decode = calibration["completed_control_weighted_decode_step_ms"]
    if type(decode) not in (int, float) or not math.isfinite(decode) or decode <= 0:
        raise ValueError("calibrated decode step must be finite and positive")
    artifact = deepcopy(original)
    artifact.update(
        kind=COST_KIND, model_seconds_per_work_token=decode / 1000.,
        model_prefill_seconds_per_token=costs["prefill_ms_per_token"] / 1000.,
        model_prefill_progress="first_observed_output",
        admission_cost_profile={
            "kind": "r5_frozen_batch_cost_proxy", "costs": deepcopy(costs),
            "identity": hashlib.sha256(json.dumps(costs, sort_keys=True).encode()).hexdigest(),
            "oracle_reference_path_s": 600.,
        },
        calibrated_model_time={
            "report": str(calibration_path), "report_sha256": calibration_sha256,
            "source": "completed_matching_control_worker_intervals",
            "pure_GPU_time_claimed": False,
            "intrinsic_work_population_thresholds_and_source_unchanged": True,
        },
    )
    WorkflowWorkOracleArtifact(artifact)
    return artifact


def configuration(base, *, artifact_path, artifact_sha256, control_path, calibration_path):
    candidate = deepcopy(base)
    common = candidate["common_environment"]
    for name, value in {
        "WORKFLOW_WORK_MODEL": str(artifact_path),
        "WORKFLOW_WORK_MODEL_SHA256": artifact_sha256,
        "WORKFLOW_COMBINATION_ADMISSION": "1",
    }.items():
        common[name] = common["BELIEFKV_" + name] = value
    candidate["status"] = "cpu_calibrated_global_combination_oracle"
    candidate["candidate_provenance"].update({
        "calibrated_combination_control": str(control_path),
        "calibrated_combination_control_sha256": sha256_file(control_path),
        "calibration_report": str(calibration_path),
        "calibration_report_sha256": sha256_file(calibration_path),
        "same_tasks_tokens_tool_times_resources_and_measured_members": True,
        "ordinary_coalescing_window_ms": 2000,
        "event_tool_replacement_retained_as_part_of_predicted_offload": True,
        "oracle_bookkeeping_retained_in_both_arms": False,
        "same_observed_request_progress_but_changed_path_cost_semantics": True,
    })
    candidate["comparison_contract"].update({
        "matched_pair_changes_only_remaining_work_ranking": False,
        "oracle_cost_and_set_selection_are_joint_change": True,
        "workflow_work_ranking": True,
        "oracle_candidate_kind": "calibrated_global_join_combination",
        "tool_replacement_uses_actual_single_donor_pairs": True,
        "tool_replacement_filtered_by_pre_release_set_budget": False,
        "candidate_window": 6, "sibling_candidate_window": 8,
        "combination_search_soft_budget_ns": 500_000,
        "formal_workload_accepted": False,
        "workflow_resume_key": [
            "protected_ready_JOIN", "bounded_joint_utility_per_incremental_cost",
            "stable_reference_on_tie_or_unsupported_summary",
        ],
    })
    candidate["gpu_execution"]["registered"] = False
    candidate["gpu_execution"]["requires_new_paired_control"] = False
    candidate["gpu_execution"]["existing_complete_control"] = str(control_path)
    candidate["workflow_work_qualification"] = {
        "kind": COST_KIND, "artifact": str(artifact_path), "artifact_sha256": artifact_sha256,
        "future_information_used": True, "deployable": False,
        "learned_calibration_requirement_applies": False,
        "cost_and_set_selection_joint_change": True,
    }
    candidate["performance_accepted"] = False
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "control", "calibration", "cohort", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve earlier preparations")
    base, control = causal.load(args.base), causal.load(args.control)
    causal.comparison.verify_sources(control)
    verify_ranking_only_pair(causal.load(Path(control["experiment_configuration"])), base)
    completion = verify_execution(args.control, args.cohort)
    calibration = causal.load(args.calibration)
    if calibration["registration"] != str(args.control.resolve()):
        raise ValueError("cost observations belong to another control")
    for name, digest in calibration["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("calibration input changed: " + name)
    for name, prefix in calibration["input_jsonl_prefixes"].items():
        path = Path(name)
        if (
            path.stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed control cost observations changed")
    common = base["common_environment"]
    original = WorkflowWorkOracleArtifact.load(
        Path(common["WORKFLOW_WORK_MODEL"]), common["WORKFLOW_WORK_MODEL_SHA256"],
    )
    artifact = calibrated_artifact(
        original.raw, calibration, calibration_path=args.calibration.resolve(),
        calibration_sha256=sha256_file(args.calibration),
    )
    destination = args.output_dir.resolve()
    artifact_path, config_path = destination / "oracle.json", destination / "full.json"
    write(artifact_path, artifact)
    candidate = configuration(
        base, artifact_path=artifact_path, artifact_sha256=sha256_file(artifact_path),
        control_path=args.control.resolve(), calibration_path=args.calibration.resolve(),
    )
    from scripts.review_matched_work_ranking import verify_calibrated_combination_pair

    verify_calibrated_combination_pair(
        causal.load(Path(control["experiment_configuration"])), candidate,
    )
    settings = arm_environment(candidate["common_environment"], "full")
    for name in causal.SERVER_FIELDS:
        if name in settings:
            settings["BELIEFKV_" + name] = settings[name]
    entry = causal.preflight_runtime(settings, common["MODEL_PATH"])
    if not entry["verified_configuration"]["workflow_combination_admission"]:
        raise ValueError("actual constructor did not enable combination selection")
    write(config_path, candidate)
    write(destination / "entry.json", {
        "preflight": entry, "complete_control_measured_requests": completion["measured_requests"],
        "complete_control_measured_output_tokens": completion["measured_output_tokens"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.base, args.control, args.calibration, args.cohort, Path(__file__),
        )},
        "artifact_sha256": sha256_file(artifact_path),
        "configuration_sha256": sha256_file(config_path),
        "GPU_experiment_started": False, "formal_workload_accepted": False,
        "performance_accepted": False,
    })
    print(destination)


if __name__ == "__main__":
    main()
