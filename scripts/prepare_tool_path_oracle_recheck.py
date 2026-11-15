"""Prepare a tool-path Oracle against a completed fixed-cohort Full, CPU only."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


KIND = "workflow_model_tool_critical_path_oracle_v1"
LABEL_FIELDS = ("requests", "workflows", "workflow_graphs", "source_binding")
MODEL_FIELDS = {
    prefix + name for prefix in ("", "BELIEFKV_")
    for name in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256")
}


def load(path):
    return json.loads(Path(path).read_bytes())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def label_fingerprints(raw):
    return {
        name: hashlib.sha256(json.dumps(
            raw[name], sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        for name in LABEL_FIELDS
    }


def ranked_artifact(control, reference):
    if (
        control.get("kind") != KIND
        or control.get("admission_order") != "global_ready_remaining_path"
        or control.get("remaining_work_ranking_enabled") is not False
        or control.get("free_dispatch_coalescing_window_ms") != 2000
        or control.get("future_information_used") is not True
        or control.get("deployable") is not False
        or any(control.get(name) != reference.get(name) for name in LABEL_FIELDS)
        or control.get("model_seconds_per_work_token")
        != reference.get("model_seconds_per_work_token")
    ):
        raise ValueError("require the same complete labels and disabled tool-path control")
    result = deepcopy(control)
    result["remaining_work_ranking_enabled"] = True
    return result


def ranked_configuration(control, path, digest):
    common = control["common_environment"]
    if (
        control.get("order") != ["full"]
        or common.get("WORKFLOW_COMBINATION_ADMISSION") != "0"
        or common.get("BELIEFKV_WORKFLOW_COMBINATION_ADMISSION") != "0"
        or control["comparison_contract"].get("workflow_work_ranking") is not False
    ):
        raise ValueError("require the completed nonranking global Full configuration")
    result = deepcopy(control)
    result.update(
        purpose="Current-load recheck of global remaining model/tool critical-path ranking",
        status="CPU_prepared_not_GPU_registered", performance_accepted=False,
    )
    for prefix in ("", "BELIEFKV_"):
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL"] = str(path.resolve())
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = digest
    result["comparison_contract"].update(
        workflow_work_ranking=True,
        matched_pair_changes_only_remaining_work_ranking=True,
        configuration_pair_changes_only_remaining_work_ranking=True,
        ranking_gain_isolated=False,
        serving_source_changes_require_separate_disclosure=True,
        oracle_future_tool_durations_used=True,
        oracle_is_not_an_optimal_JCT_bound=True,
        oracle_candidate_kind=KIND,
        workflow_work_prediction="exact_remaining_model_tool_critical_path_seconds",
        workflow_combination_admission=False,
        selection_strategy="existing_global_ready_remaining_path",
        workload_change="none",
        tool_prefetch_disabled_by_user=True,
        ready_capacity_accumulation_enabled=False,
        additional_tool_boundary_preemption_enabled=False,
    )
    result["workflow_work_qualification"] = {
        "kind": "explicit_future_model_tool_path_diagnostic_not_deployable",
        "decision_eligible": True, "deployable": False, "model_sha256": digest,
        "same_complete_labels_and_calibration_as_completed_Full": True,
    }
    result["gpu_execution"] = {
        "registered": False, "fresh_source_bound_position_check_required": True,
        "automatic_GPU_followup": False,
    }
    return result


def verify_completion(proof, registration_digest, cohort, cohort_digest):
    if (
        proof.get("registration_sha256") != registration_digest
        or proof.get("cohort_sha256") != cohort_digest or proof.get("cohort") != cohort
        or proof.get("protocol") != "all_fixed_measured_workflows_completed"
        or proof.get("actual_owned_exit_verified") is not True
        or len(set(cohort["measured_workflow_ids"])) != 18
        or not proof.get("measured_work")
        or proof["measured_work"] != proof.get("work")
        or proof.get("measured_requests") != len(proof["work"])
    ):
        raise ValueError("require the original complete fixed18 execution proof")


def distribution(values):
    values = sorted(values)
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("require finite CPU observations")
    return {
        "count": len(values), "median": values[(len(values) - 1) // 2],
        "p90": values[math.ceil(len(values) * .9) - 1], "max": values[-1],
        "sum": sum(values),
    }


def benchmark_paths(path, digest, oracle_type, literal):
    oracle = oracle_type(str(path), digest)
    cold, warm, initial = [], [], {}
    try:
        for workflow in sorted(oracle.artifact.workflows):
            started = time.thread_time_ns()
            prediction = oracle.prediction(workflow)
            cold.append((time.thread_time_ns() - started) / 1000.)
            expected = literal(
                oracle.artifact.graphs[workflow], oracle.artifact.requests, {}, {},
                oracle.artifact.model_seconds_per_work_token, prediction.updated_at,
            )
            actual = (
                prediction.remaining_path_s, prediction.model_only_path_s,
                prediction.tool_and_fixed_delay_path_s,
            )
            if any(not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-7)
                   for a, b in zip(actual, expected)):
                raise ValueError("cached Oracle path differs from the literal DAG")
            initial[workflow] = {
                "model_work": prediction.remaining_work,
                "model_tool_path_s": prediction.remaining_path_s,
                "model_only_path_s": prediction.model_only_path_s,
                "external_only_path_s": prediction.tool_and_fixed_delay_path_s,
            }
        for _repeat in range(5):
            for workflow in sorted(oracle.artifact.workflows):
                started = time.thread_time_ns()
                oracle.prediction(workflow)
                warm.append((time.thread_time_ns() - started) / 1000.)
        pairs = inversions = 0
        values = list(initial.values())
        for index, first in enumerate(values):
            for second in values[index + 1:]:
                model = first["model_work"] - second["model_work"]
                path_delta = first["model_tool_path_s"] - second["model_tool_path_s"]
                pairs += bool(model and path_delta)
                inversions += model * path_delta < 0
        return {
            "cold_path_thread_cpu_us": distribution(cold),
            "cached_path_thread_cpu_us": distribution(warm),
            "counts": dict(oracle.counts), "initial_workflow_scores": initial,
            "initial_population_pairs_without_ties": pairs,
            "initial_population_rank_inversions": inversions,
            "scope": {
                "all_workflows_at_initial_unserved_state": True,
                "literal_path_equality_checked": True,
                "warm_queries_have_no_observed_progress_change": True,
                "actual_online_control_cost_or_actual_competition": False,
                "JCT_simulation_or_performance_acceptance": False,
            },
        }
    finally:
        oracle.close()


def prepare(args):
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("CPU preparation requires hidden CUDA")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if args.output_dir.exists():
        raise FileExistsError("preserve previous preparation")
    operator = args.operator.resolve()
    qualification = load(args.bound_cpu_evidence)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=operator, text=True,
    ).strip()
    if (
        qualification.get("candidate") != str(operator)
        or qualification.get("candidate_commit") != commit
        or qualification.get("failed_error_skipped_cases") != 0
        or qualification.get("sources_unchanged_during_execution") is not True
        or subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=operator, text=True,
        ).strip()
    ):
        raise ValueError("require the clean, unchanged source-bound CPU operator")
    manifest = qualification["source_manifest"]
    if sha256(manifest["path"]) != manifest["sha256"]:
        raise ValueError("bound CPU source manifest changed")
    for name, digest in load(manifest["path"]).items():
        if sha256(operator / name) != digest:
            raise ValueError("qualified serving source changed: " + name)
    for name in ("report",):
        item = qualification[name]
        if sha256(item["path"]) != item["sha256"]:
            raise ValueError("bound CPU qualification changed")
    registration = load(args.control_registration)
    control_path = Path(registration["experiment_configuration"])
    control = load(control_path)
    cohort_path = Path(control["private_analysis_cohort"]["path"])
    cohort = load(cohort_path)
    proof = load(args.control_proof)
    verify_completion(proof, sha256(args.control_registration), cohort, sha256(cohort_path))
    if sha256(proof["certificate"]) != proof["certificate_sha256"]:
        raise ValueError("completed Full certificate changed")
    for field in ("workflow_arrivals", "tool_timing_overlay", "private_analysis_cohort"):
        item = control[field]
        if sha256(item["path"]) != item["sha256"]:
            raise ValueError("fixed workload input changed: " + field)
    common = control["common_environment"]
    control_work = Path(common["WORKFLOW_WORK_MODEL"])
    if sha256(control_work) != common["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("completed Full artifact changed")
    raw = ranked_artifact(load(control_work), load(args.label_reference))

    sys.path.insert(0, str(operator))
    os.chdir(operator)
    from beliefkv.runtime.workflow_work_oracle import (
        WorkflowWorkOracle, WorkflowWorkOracleArtifact, scan_remaining_paths,
    )
    from scripts.review_matched_work_ranking import verify_ranking_only_pair
    from scripts.run_causal_comparison import environment, preflight_runtime

    imported = sys.modules[WorkflowWorkOracle.__module__].__file__
    if Path(imported).resolve() != operator / "beliefkv/runtime/workflow_work_oracle.py":
        raise ValueError("another serving tree supplied the Oracle")
    WorkflowWorkOracleArtifact(raw)
    args.output_dir.mkdir(parents=True)
    work_path = args.output_dir / "oracle_work.json"
    write(work_path, raw)
    candidate = ranked_configuration(control, work_path, sha256(work_path))
    pair = verify_ranking_only_pair(control, candidate)
    settings, _flags, _socket = environment(
        {"arms": {"full": candidate["common_environment"]}}, "full", operator,
        Path(qualification["environment"]["SGLANG_SOURCE_CHECKOUT"]),
        args.output_dir / "constructor", 19078, operator,
        sampling_mode=registration["sampling_mode"],
        max_running_requests=registration["max_running_requests"],
    )
    write(args.output_dir / "oracle.json", candidate)
    try:
        constructor = preflight_runtime(settings, settings["MODEL_PATH"])
    except subprocess.CalledProcessError as error:
        write(args.output_dir / "constructor_error.json", {
            "returncode": error.returncode, "stdout": error.stdout, "stderr": error.stderr,
            "GPU_action": False, "performance_accepted": False,
        })
        raise
    costs = benchmark_paths(
        work_path, sha256(work_path), WorkflowWorkOracle, scan_remaining_paths,
    )
    write(args.output_dir / "path_cpu.json", costs)
    changed = subprocess.check_output([
        "git", "diff", "--name-only", registration["code_commit"], commit,
        "--", "beliefkv/runtime", "beliefkv/policy",
    ], cwd=operator, text=True).splitlines()
    inputs = (
        args.control_registration, args.control_proof, control_path, control_work,
        args.label_reference, args.bound_cpu_evidence, Path(manifest["path"]),
        Path(qualification["report"]["path"]), cohort_path, Path(proof["certificate"]),
        Path(control["workflow_arrivals"]["path"]),
        Path(control["tool_timing_overlay"]["path"]), Path(__file__),
    )
    result = {
        "kind": "tool_path_oracle_current_load_CPU_preparation_v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "operator": str(operator), "operator_commit": commit,
        "control_registration": str(args.control_registration.resolve()),
        "control_commit": registration["code_commit"],
        "serving_source_deltas_from_completed_Full": changed,
        "ranking_only_causal_gain_isolated": not changed,
        "configuration_pair_only_ranking_toggle": True,
        "pair_validation_paths": [str(path) for path in pair],
        "exact_original_label_fingerprints": label_fingerprints(raw),
        "workflows": len(raw["workflows"]), "requests": len(raw["requests"]),
        "DAG_nodes": sum(map(len, raw["workflow_graphs"].values())),
        "fixed_measured_workflows": len(cohort["measured_workflow_ids"]),
        "measured_requests": proof["measured_requests"],
        "measured_output_tokens": proof["measured_output_tokens"],
        "constructor": constructor,
        "qualification_reused_literal_cases": qualification["unique_test_identities"],
        "initial_path_CPU": {name: value for name, value in costs.items()
                             if name != "initial_workflow_scores"},
        "required_actual_result_collector": "audit_ranked_oracle_service.py",
        "combination_choice_collector_is_applicable": False,
        "Full_GPU_rerun_required": False,
        "GPU_registration_created": False, "live_queue_changed": False,
        "GPU_action": False, "performance_accepted": False, "goal_complete": False,
        "inputs_sha256": {str(path.resolve()): sha256(path) for path in inputs},
        "outputs_sha256": {
            str(path.resolve()): sha256(path) for path in (
                work_path, args.output_dir / "oracle.json", args.output_dir / "path_cpu.json",
            )
        },
    }
    write(args.output_dir / "preparation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "operator", "control-registration", "control-proof", "label-reference",
        "bound-cpu-evidence", "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    result = prepare(parser.parse_args())
    print(json.dumps({name: result[name] for name in (
        "workflows", "requests", "DAG_nodes", "measured_requests",
        "measured_output_tokens", "configuration_pair_only_ranking_toggle",
        "ranking_only_causal_gain_isolated", "GPU_action",
    )}))


if __name__ == "__main__":
    main()
