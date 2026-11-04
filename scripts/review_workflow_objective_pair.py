"""Compare ratio and benefit admission with identical predictors and complete JCT cohorts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from scripts import run_causal_comparison as causal
from scripts.review_learned_work_ranking import (
    control_optimization_environment_changes, control_optimization_scope,
    verify_joint_predictor_reference, verify_learned_traffic,
)
from scripts.review_matched_work_ranking import evaluate_pair_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def verify_objective_pair(reference, candidate):
    optimization = control_optimization_scope(candidate)
    prediction_changed = optimization is not None and optimization["prediction_changed"]
    allowed = {
        prefix + field for prefix in ("", "BELIEFKV_")
        for field in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256")
    }
    allowed |= control_optimization_environment_changes(reference, candidate)
    old, new = (spec["common_environment"] for spec in (reference, candidate))
    if (
        reference["order"] != ["full"] or candidate["order"] != ["full"]
        or old.keys() - allowed != new.keys() - allowed
        or any(old[name] != new[name] for name in old.keys() - allowed)
        or any(reference.get(name) != candidate.get(name) for name in (
            "source_registration", "workflow_arrivals", "tool_timing_overlay",
            "stream_response_metrics", "workload_acceptance",
        ))
    ):
        raise ValueError("objective comparison changed traffic, resources or nonobjective settings")
    models, paths = [], []
    for env in (old, new):
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if any(
            env.get(prefix + "WORKFLOW_WORK_SOURCE") != "learned"
            or env.get(prefix + "WORKFLOW_COMBINATION_ADMISSION") != "1"
            or env.get(prefix + "WORKFLOW_WORK_MODEL") != str(path)
            or env.get(prefix + "WORKFLOW_WORK_MODEL_SHA256") != sha256_file(path)
            for prefix in ("", "BELIEFKV_")
        ):
            raise ValueError("objective comparison requires matching learned model aliases")
        models.append(causal.load(path))
        paths.append(path)
    profiles = [model["admission_cost_profile"] for model in models]
    verify_joint_predictor_reference(models[0], candidate)
    if (
        profiles[0]["kind"] != "r5_frozen_batch_cost_proxy_capacity_v6"
        or profiles[1]["kind"] != "r5_frozen_batch_cost_proxy_capacity_v7"
        or CombinationCosts(**profiles[0]["costs"]).selection_objective != "benefit_per_cost"
        or CombinationCosts(**profiles[1]["costs"]).selection_objective != "benefit"
        or any(CombinationCosts(**p["costs"]).decode_cost_basis != "full_batch" for p in profiles)
        or {k: v for k, v in profiles[1]["costs"].items() if k != "selection_objective"} != profiles[0]["costs"]
        or profiles[0].get("oracle_reference_path_s") != profiles[1].get("oracle_reference_path_s")
        or not prediction_changed and (
            {k: v for k, v in models[0].items() if k != "admission_cost_profile"}
            != {k: v for k, v in models[1].items() if k != "admission_cost_profile"}
        )
    ):
        raise ValueError("objective comparison changed the predictor, cost values or planning scope")
    for profile in profiles:
        identity = hashlib.sha256(json.dumps({
            "costs": profile["costs"], "capacity_summary_kind": profile["kind"],
        }, sort_keys=True).encode()).hexdigest()
        if profile["identity"] != identity:
            raise ValueError("objective profile fingerprint changed")
    contract = candidate["comparison_contract"]
    if (
        contract.get("combination_selection_objective") != "benefit"
        or contract.get("only_combination_selection_objective_changed") is not (optimization is None)
        or contract.get("cost_tie_break_added") is not False
        or contract.get("prediction_parameters_and_support_unchanged") is not (not prediction_changed)
        or any(contract.get(name) != reference["comparison_contract"].get(name) for name in (
            "future_information_used", "physical_capacity_credit",
            "minimum_promising_measured_mean_JCT_reduction",
            "common_window_output_throughput_noninferiority_fraction",
        ))
    ):
        raise ValueError("objective comparison lacks its exact policy and performance contract")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "candidate", "reference-review-dir", "candidate-review-dir", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--minimum-jct-gain", type=float, default=.01)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve previous complete objective comparisons")
    plans = [causal.load(path) for path in (args.reference, args.candidate)]
    verify_learned_traffic(*plans)
    for plan in plans:
        causal.comparison.verify_sources(plan)
    specifications = [Path(plan["experiment_configuration"]) for plan in plans]
    specs = [causal.load(path) for path in specifications]
    artifacts = verify_objective_pair(*specs)
    if specs[1]["comparison_contract"].get("objective_comparison_source_sha256") != sha256_file(specifications[0]):
        raise ValueError("the recorded objective reference configuration differs")
    executions = [verify_execution(path, args.cohort) for path in (args.reference, args.candidate)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("objective comparison changed complete measured native work")
    cohorts, decisions, paths = [], [], [args.reference, args.candidate, args.cohort, *specifications, *artifacts]
    for directory, execution in zip((args.reference_review_dir, args.candidate_review_dir), executions):
        reports = [directory / name for name in ("review.json", "cohort.json", "workload_decision.json")]
        review, cohort, decision = (causal.load(path) for path in reports)
        review_matches_execution(review, execution)
        if cohort["cohort"] != causal.load(args.cohort):
            raise ValueError("objective comparison changed measurement members or common window")
        for value in (review, cohort):
            for name, prefix in value.get("input_jsonl_prefixes", {}).items():
                path = Path(name)
                if path.stat().st_size != prefix["complete_prefix_bytes"] or (
                    sha256_file(path) != prefix["complete_prefix_sha256"]
                ):
                    raise ValueError("a completed objective comparison prefix changed")
        if any(sha256_file(Path(name)) != digest for name, digest in decision.get("inputs_sha256", {}).items()):
            raise ValueError("a completed workload diagnosis changed")
        cohorts.append(cohort)
        decisions.append(decision)
        paths.extend(reports)
    result = evaluate_pair_cohorts(*cohorts, specs[1]["comparison_contract"], minimum_jct_gain=args.minimum_jct_gain)
    for group in result["groups"].values():
        group["reference_JCT_ms"] = group.pop("control_JCT_ms")
        group["candidate_JCT_ms"] = group.pop("oracle_JCT_ms")
        group["reference_completed_workflows"] = group.pop("control_completed_workflows")
        group["candidate_completed_workflows"] = group.pop("oracle_completed_workflows")
        for row in group["workflows"]:
            row["reference_JCT_ms"] = row.pop("control_JCT_ms")
            row["candidate_JCT_ms"] = row.pop("oracle_JCT_ms")
    throughput = result["common_window_output_throughput"]
    throughput["reference_tokens_per_second"] = throughput.pop("control_tokens_per_second")
    throughput["candidate_tokens_per_second"] = throughput.pop("oracle_tokens_per_second")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        reference_objective="benefit_per_cost", candidate_objective="benefit",
        only_combination_selection_objective_changed=control_optimization_scope(specs[1]) is None,
        complete_measured_work_fresh_drain_and_actual_exit=True,
        original_workload_decisions_preserved=decisions,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            *paths, Path(__file__), ROOT / "scripts/verify_measured_execution.py",
            ROOT / "scripts/review_matched_work_ranking.py",
        )},
        GPU_action=False, performance_accepted=False, single_run_statistical_significance_claimed=False,
    )
    optimization = control_optimization_scope(specs[1])
    if optimization is not None:
        result["synchronous_control_optimization"] = optimization
        result["admission_objective_and_control_optimization_are_joint_change"] = True
        result["ranking_gain_isolated"] = False
    write(args.output, result)
    print(json.dumps({
        "mean_JCT_gain_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "throughput": throughput, "checks": result["checks"],
    }), flush=True)


if __name__ == "__main__":
    main()
