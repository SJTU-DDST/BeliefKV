"""Compare a repaired control and oracle that share progress bookkeeping."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.review_predictive_thunder_cohort import matching_serving_traffic
from scripts.review_sustained_oracle import compare_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def verify_ranking_only_pair(control, oracle, *, allow_legacy_control_declaration=False):
    fields = {
        prefix + name
        for prefix in ("", "BELIEFKV_")
        for name in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256")
    }
    old, new = (row["common_environment"] for row in (control, oracle))
    if (
        control["order"] != ["full"] or oracle["order"] != ["full"]
        or any(control.get(name) != oracle.get(name) for name in (
            "source_registration", "workflow_arrivals", "tool_timing_overlay",
            "stream_response_metrics", "workload_acceptance", "private_analysis_cohort",
        ))
        or old.keys() != new.keys()
        or any(old[name] != new[name] for name in old.keys() - fields)
    ):
        raise ValueError("matched ranking changed tasks, tools, arrivals or serving conditions")
    artifacts, paths = [], []
    for configuration, ranking in ((control, False), (oracle, True)):
        common, contract = (
            configuration["common_environment"], configuration["comparison_contract"],
        )
        declared = contract.get("matched_pair_changes_only_remaining_work_ranking") is True or (
            allow_legacy_control_declaration and not ranking
            and contract.get("matched_pair_changes_only_remaining_work_ranking") is False
        )
        if (
            common.get("WORKFLOW_SIZE_RESUME") != "1"
            or common.get("WORKFLOW_WORK_SOURCE") != "oracle"
            or common.get("WORKFLOW_COMBINATION_ADMISSION") != "0"
            or common.get("AGENT_PRESSURE_PAUSE_POLICY") != "workflow_tool_aware"
            or common.get("AGENT_CONTROL_INTERVAL_MS") != "2000"
            or common.get("AGENT_FREE_DISPATCH_INTERVAL_MS") != "500"
            or not declared
            or contract.get("workflow_work_ranking") is not ranking
            or contract.get("coalescing_and_oracle_ranking_are_joint_change") is not False
            or any(common.get(name) != common.get("BELIEFKV_" + name) for name in (
                "WORKFLOW_SIZE_RESUME", "WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL",
                "WORKFLOW_WORK_MODEL_SHA256", "WORKFLOW_COMBINATION_ADMISSION",
                "AGENT_PRESSURE_PAUSE_POLICY",
            ))
        ):
            raise ValueError("require explicit matched tool-aware control and oracle declarations")
        path = Path(common["WORKFLOW_WORK_MODEL"])
        if sha256_file(path) != common["WORKFLOW_WORK_MODEL_SHA256"]:
            raise ValueError("matched work artifact changed")
        artifact = causal.load(path)
        if (
            artifact.get("remaining_work_ranking_enabled") is not ranking
            or artifact.get("free_dispatch_coalescing_window_ms") != 2000
        ):
            raise ValueError("matched artifact changed the ranking toggle or two-second window")
        artifacts.append({
            name: value for name, value in artifact.items()
            if name != "remaining_work_ranking_enabled"
        })
        paths.append(path)
    if artifacts[0] != artifacts[1]:
        raise ValueError("matched artifacts differ beyond the remaining-work ranking toggle")
    if any(
        control["comparison_contract"].get(name) != oracle["comparison_contract"].get(name)
        for name in (
            "minimum_promising_measured_mean_JCT_reduction",
            "common_window_output_throughput_noninferiority_fraction",
        )
    ):
        raise ValueError("matched comparison changed its performance thresholds")
    return paths


def evaluate_pair_cohorts(control, oracle, contract, *, minimum_jct_gain=None):
    configured = contract["minimum_promising_measured_mean_JCT_reduction"]
    effective = configured if minimum_jct_gain is None else minimum_jct_gain
    throughput = contract["common_window_output_throughput_noninferiority_fraction"]
    result = compare_cohorts(
        control, oracle, minimum_jct_gain=effective, minimum_throughput_fraction=throughput,
    )
    result["performance_goal"] = {
        "minimum_measured_mean_JCT_gain": effective,
        "configuration_minimum_measured_mean_JCT_gain": configured,
        "explicit_goal_override": minimum_jct_gain is not None,
        "minimum_common_window_output_throughput_fraction": throughput,
        "registered_configurations_modified": False,
    }
    return result


def verify_calibrated_combination_pair(control, oracle):
    from beliefkv.runtime.workflow_work_oracle import COST_KIND, KIND, WorkflowWorkOracleArtifact

    allowed = {
        prefix + name
        for prefix in ("", "BELIEFKV_")
        for name in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256",
                     "WORKFLOW_COMBINATION_ADMISSION")
    }
    old, new = control["common_environment"], oracle["common_environment"]
    if (
        control["order"] != ["full"] or oracle["order"] != ["full"]
        or any(control.get(name) != oracle.get(name) for name in (
            "source_registration", "workflow_arrivals", "tool_timing_overlay",
            "stream_response_metrics", "workload_acceptance", "private_analysis_cohort",
        ))
        or old.keys() != new.keys()
        or any(old[name] != new[name] for name in old.keys() - allowed)
        or old.get("WORKFLOW_COMBINATION_ADMISSION") != "0"
        or new.get("WORKFLOW_COMBINATION_ADMISSION") != "1"
        or new.get("AGENT_CONTROL_INTERVAL_MS") != "2000"
        or new.get("AGENT_FREE_DISPATCH_INTERVAL_MS") != "500"
        or new.get("AGENT_PRESSURE_PAUSE_POLICY") != "workflow_tool_aware"
        or control["comparison_contract"].get("workflow_work_ranking") is not False
        or oracle["comparison_contract"].get("workflow_work_ranking") is not True
        or oracle["comparison_contract"].get("matched_pair_changes_only_remaining_work_ranking") is not False
        or oracle["comparison_contract"].get("oracle_cost_and_set_selection_are_joint_change") is not True
    ):
        raise ValueError("calibrated combination changed traffic or undeclared policy conditions")
    artifacts, paths = [], []
    for spec, ranking, kind in ((control, False, KIND), (oracle, True, COST_KIND)):
        common = spec["common_environment"]
        if any(common.get(name) != common.get("BELIEFKV_" + name) for name in (
            "WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256", "WORKFLOW_COMBINATION_ADMISSION",
        )):
            raise ValueError("calibrated combination aliases disagree")
        path = Path(common["WORKFLOW_WORK_MODEL"])
        artifact = WorkflowWorkOracleArtifact.load(path, common["WORKFLOW_WORK_MODEL_SHA256"]).raw
        if (
            artifact["kind"] != kind or artifact.get("remaining_work_ranking_enabled") is not ranking
            or artifact.get("admission_order") != "global_ready_remaining_path"
            or artifact.get("free_dispatch_coalescing_window_ms") != 2000
        ):
            raise ValueError("calibrated combination requires explicit artifact kinds and the same window")
        artifacts.append(artifact)
        paths.append(path)
    changed_fields = {
        "kind", "remaining_work_ranking_enabled", "model_seconds_per_work_token",
        "model_prefill_seconds_per_token", "model_prefill_progress",
        "admission_cost_profile", "calibrated_model_time",
    }
    if (
        {k: v for k, v in artifacts[0].items() if k not in changed_fields}
        != {k: v for k, v in artifacts[1].items() if k not in changed_fields}
        or any(control["comparison_contract"].get(name) != oracle["comparison_contract"].get(name)
               for name in (
                   "minimum_promising_measured_mean_JCT_reduction",
                   "common_window_output_throughput_noninferiority_fraction",
               ))
    ):
        raise ValueError("calibrated combination changed intrinsic work, source graph or acceptance thresholds")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "control", "oracle", "control-review-dir", "oracle-review-dir",
        "arrivals", "cohort", "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--pair-kind", choices=("ranking", "calibrated_combination"), default="ranking")
    parser.add_argument("--allow-legacy-control-declaration", action="store_true")
    parser.add_argument("--minimum-jct-gain", type=float)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve earlier comparison evidence")
    plans = [causal.load(path) for path in (args.control, args.oracle)]
    matching_serving_traffic(*plans)
    for plan in plans:
        causal.comparison.verify_sources(plan)
        if plan["workflow_arrivals_path"] != str(args.arrivals.resolve()):
            raise ValueError("comparison uses another frozen arrival table")
    configurations = [causal.load(Path(row["experiment_configuration"])) for row in plans]
    ranking_only = args.pair_kind == "ranking"
    if args.allow_legacy_control_declaration and not ranking_only:
        raise ValueError("legacy control declarations apply only to ranking-only comparisons")
    artifacts = (
        verify_ranking_only_pair(
            *configurations, allow_legacy_control_declaration=args.allow_legacy_control_declaration,
        ) if ranking_only else verify_calibrated_combination_pair(*configurations)
    )
    completion = [verify_execution(path, args.cohort) for path in (args.control, args.oracle)]
    if completion[0]["work"] != completion[1]["work"]:
        raise ValueError("matched executions changed complete measured native work")
    paths = [args.control, args.oracle, args.arrivals, args.cohort, *artifacts]
    cohorts, decisions = [], []
    for registration, directory, evidence in (
        (args.control, args.control_review_dir, completion[0]),
        (args.oracle, args.oracle_review_dir, completion[1]),
    ):
        review_path, cohort_path, decision_path = (
            directory / name for name in ("review.json", "cohort.json", "workload_decision.json")
        )
        review, cohort = causal.load(review_path), causal.load(cohort_path)
        review_matches_execution(review, evidence)
        if (
            cohort["cohort"] != causal.load(args.cohort)
            or any(cohort["inputs_sha256"].get(str(path.resolve())) != sha256_file(path)
                   for path in (args.arrivals, args.cohort))
        ):
            raise ValueError("review changed fixed cohort membership or throughput window")
        for report in (review, cohort):
            for path, prefix in report.get("input_jsonl_prefixes", {}).items():
                if (
                    Path(path).stat().st_size != prefix["complete_prefix_bytes"]
                    or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
                ):
                    raise ValueError("completed review evidence changed")
        decision = causal.load(decision_path)
        if any(
            sha256_file(Path(path)) != digest
            for path, digest in decision["inputs_sha256"].items()
        ):
            raise ValueError("workload decision inputs changed")
        decisions.append(decision)
        cohorts.append(cohort)
        paths.extend((review_path, cohort_path, decision_path))
    contract = configurations[1]["comparison_contract"]
    result = evaluate_pair_cohorts(
        *cohorts, contract, minimum_jct_gain=args.minimum_jct_gain,
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        matched_repaired_control_with_same_progress_bookkeeping=ranking_only,
        only_remaining_work_ranking_changed=ranking_only,
        matched_actual_traffic_and_serving_conditions_verified=True,
        calibrated_oracle_cost_and_set_selection_joint_change=not ranking_only,
        pair_kind=args.pair_kind,
        legacy_unpaired_control_declaration_explicitly_allowed=args.allow_legacy_control_declaration,
        complete_measured_work_fresh_drain_and_actual_exit=True,
        workload_decisions=decisions,
        original_workload_rejections_preserved=True,
        formal_workload_accepted=False,
        oracle_is_not_an_optimal_JCT_bound=True,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            *paths, *(Path(plan["experiment_configuration"]) for plan in plans),
            Path(__file__), ROOT / "scripts/review_sustained_oracle.py",
            ROOT / "scripts/review_predictive_thunder_cohort.py",
            ROOT / "scripts/verify_measured_execution.py",
        )},
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "measured_mean_JCT_reduction_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "common_window_output_throughput": result["common_window_output_throughput"],
        "checks": result["checks"],
    })


if __name__ == "__main__":
    main()
