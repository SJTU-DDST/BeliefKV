"""Compare learned R5 with a matched nonranking Full, preserving complete measured work."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import KIND
from scripts import run_causal_comparison as causal
from scripts.review_matched_work_ranking import evaluate_pair_cohorts
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def verify_learned_combination_pair(control, learned):
    allowed = {
        prefix + name for prefix in ("", "BELIEFKV_") for name in (
            "WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256",
            "WORKFLOW_COMBINATION_ADMISSION",
        )
    }
    old, new = (row["common_environment"] for row in (control, learned))
    if (
        control["order"] != ["full"] or learned["order"] != ["full"]
        or old.keys() != new.keys()
        or any(old[name] != new[name] for name in old.keys() - allowed)
        or any(control.get(name) != learned.get(name) for name in (
            "source_registration", "workflow_arrivals", "tool_timing_overlay",
            "stream_response_metrics", "workload_acceptance",
        ))
    ):
        raise ValueError("learned comparison changed traffic or undeclared serving conditions")
    paths, artifacts = [], []
    for spec, source, combination in ((control, "oracle", "0"), (learned, "learned", "1")):
        env = spec["common_environment"]
        for name in (
            "WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256",
            "WORKFLOW_COMBINATION_ADMISSION",
        ):
            if env.get(name) != env.get("BELIEFKV_" + name):
                raise ValueError("learned comparison aliases disagree")
        if (
            env.get("WORKFLOW_WORK_SOURCE") != source
            or env.get("WORKFLOW_COMBINATION_ADMISSION") != combination
            or env.get("WORKFLOW_SIZE_RESUME") != "1"
            or env.get("AGENT_CONTROL_INTERVAL_MS") != "2000"
            or env.get("AGENT_FREE_DISPATCH_INTERVAL_MS") != "500"
        ):
            raise ValueError("learned comparison changed the declared recovery policy or cadence")
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]:
            raise ValueError("a learned comparison artifact changed")
        artifact = causal.load(path)
        if artifact.get("free_dispatch_coalescing_window_ms") != 2000:
            raise ValueError("learned comparison changed the coalescing window")
        paths.append(path)
        artifacts.append(artifact)
    if (
        artifacts[0].get("kind") != KIND
        or artifacts[0].get("remaining_work_ranking_enabled") is not False
        or control["comparison_contract"].get("workflow_work_ranking") is not False
    ):
        raise ValueError("require the explicitly disabled-ranking Full reference")
    model = WorkflowWorkModel(artifacts[1])
    profile, contract = artifacts[1].get("admission_cost_profile", {}), learned["comparison_contract"]
    if (
        model.schema != R5_SCHEMA or model.metadata.get("decision_eligible") is not True
        or profile.get("kind") != "r5_frozen_batch_cost_proxy_capacity_v6"
        or CombinationCosts(**profile["costs"]).decode_cost_basis != "full_batch"
        or not profile.get("identity")
        or contract.get("future_information_used") is not False
        or contract.get("workflow_work_ranking") is not True
        or contract.get("workflow_combination_admission") is not True
        or contract.get("physical_capacity_credit") is not False
        or contract.get("matched_pair_changes_only_remaining_work_ranking") is not False
    ):
        raise ValueError("require explicit learned distributions and capacity-v6 combination planning")
    for name in (
        "minimum_promising_measured_mean_JCT_reduction",
        "common_window_output_throughput_noninferiority_fraction",
    ):
        if control["comparison_contract"].get(name) != contract.get(name):
            raise ValueError("learned comparison changed performance thresholds")
    return paths


def verify_learned_traffic(control, learned):
    for name in (*TRAFFIC_FIELDS, "expected_replay_capacity", "host_capacity_experiment",
                 "native_execution_configuration"):
        if name not in control or control[name] != learned.get(name):
            raise ValueError("learned comparison changed matched work or resources: " + name)
    if control.get("order") != ["full"] or learned.get("order") != ["full"]:
        raise ValueError("learned comparison requires two Full executions")
    if learned.get("oracle_information") is not None:
        raise ValueError("learned execution must not expose future oracle work")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "learned", "control-review-dir", "learned-review-dir", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--minimum-jct-gain", type=float, default=.01)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier learned comparisons")
    plans = [causal.load(path) for path in (args.control, args.learned)]
    verify_learned_traffic(*plans)
    for plan in plans:
        causal.comparison.verify_sources(plan)
    specs = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    artifacts = verify_learned_combination_pair(*specs)
    executions = [verify_execution(path, args.cohort) for path in (args.control, args.learned)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("learned comparison changed complete measured native work")
    cohorts, decisions, paths = [], [], [args.control, args.learned, args.cohort, *artifacts]
    for directory, execution in zip((args.control_review_dir, args.learned_review_dir), executions):
        report_paths = [directory / name for name in ("review.json", "cohort.json", "workload_decision.json")]
        review, cohort, decision = (causal.load(path) for path in report_paths)
        review_matches_execution(review, execution)
        if cohort["cohort"] != causal.load(args.cohort):
            raise ValueError("learned comparison changed fixed cohort members")
        for report in (review, cohort):
            for name, prefix in report.get("input_jsonl_prefixes", {}).items():
                path = Path(name)
                if path.stat().st_size != prefix["complete_prefix_bytes"] or (
                    sha256_file(path) != prefix["complete_prefix_sha256"]
                ):
                    raise ValueError("a completed review prefix changed")
        if any(sha256_file(Path(name)) != digest for name, digest in decision["inputs_sha256"].items()):
            raise ValueError("a completed workload decision changed")
        cohorts.append(cohort)
        decisions.append(decision)
        paths.extend(report_paths)
    result = evaluate_pair_cohorts(
        *cohorts, specs[1]["comparison_contract"], minimum_jct_gain=args.minimum_jct_gain,
    )
    for group in result["groups"].values():
        group["learned_JCT_ms"] = group.pop("oracle_JCT_ms")
        group["learned_completed_workflows"] = group.pop("oracle_completed_workflows")
        for row in group["workflows"]:
            row["learned_JCT_ms"] = row.pop("oracle_JCT_ms")
    throughput = result["common_window_output_throughput"]
    throughput["learned_tokens_per_second"] = throughput.pop("oracle_tokens_per_second")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        reference="nonranking_full", candidate="learned_R5_combination",
        only_remaining_work_ranking_changed=False,
        conditional_prediction_combination_and_cost_planning_are_joint_change=True,
        complete_measured_work_fresh_drain_and_actual_exit=True,
        original_workload_decisions_preserved=decisions,
        single_run_statistical_significance_claimed=False,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            *paths, *(Path(plan["experiment_configuration"]) for plan in plans),
            Path(__file__), ROOT / "scripts/verify_measured_execution.py",
            ROOT / "scripts/review_matched_work_ranking.py",
        )},
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "measured_mean_JCT_reduction_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "throughput": throughput, "checks": result["checks"],
    })


if __name__ == "__main__":
    main()
