"""Compare complete fixed-cohort oracle JCT and a shared throughput window."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_metrics import distribution
from scripts import run_causal_comparison as causal
from scripts.prepare_sustained_oracle_admission import load_diagnostic_request, validate_decision
from scripts.register_sustained_oracle import matching_conditions
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_declared_decision(provenance, control):
    decision_path = Path(provenance["workload_review"])
    if provenance["workload_review_sha256"] != sha256_file(decision_path):
        raise ValueError("original workload decision changed")
    binding = provenance.get("diagnostic_request")
    diagnostic = None
    if binding is not None:
        path = Path(binding["path"])
        if sha256_file(path) != binding["sha256"]:
            raise ValueError("explicit user oracle diagnostic request changed")
        diagnostic = load_diagnostic_request(path, control, decision_path)
    validate_decision(causal.load(decision_path), [], diagnostic=diagnostic)
    return decision_path, diagnostic


def paired_group(members, reference, candidate):
    paired = [key for key in members if key in reference and key in candidate]
    before = distribution(reference[key] for key in paired)
    after = distribution(candidate[key] for key in paired)
    return {
        "control_JCT_ms": before, "oracle_JCT_ms": after,
        "mean_JCT_reduction_percent": (
            100 * (1 - after["mean"] / before["mean"]) if paired else None
        ),
        "improved_workflows": sum(candidate[key] < reference[key] for key in paired),
        "worsened_workflows": sum(candidate[key] > reference[key] for key in paired),
        "paired_completed_workflows": len(paired),
        "fixed_population": len(members),
        "control_completed_workflows": len(reference),
        "oracle_completed_workflows": len(candidate),
        "conditional_on_shared_completion": len(paired) != len(members),
        "workflows": [
            {
                "workflow_id": key, "control_JCT_ms": reference.get(key),
                "oracle_JCT_ms": candidate.get(key),
                "saved_ms": reference[key] - candidate[key] if key in paired else None,
            } for key in members
        ],
    }


def censored_cohort_evidence(report):
    cohort = report["cohort"]
    groups = ("measured", "warmup", "background")
    ids, completed = [], set()
    for name in groups:
        members, values = cohort[name + "_workflow_ids"], report[name + "_workflow_JCT_ms"]
        if (
            not members or len(set(members)) != len(members)
            or (set(values) != set(members) if name == "measured" else not set(values) <= set(members))
            or not all(positive(value) for value in values.values())
        ):
            raise ValueError("censored execution changed fixed members or complete measured JCT")
        ids.extend(members)
        completed.update(values)
    outputs = report["all_service_output_tokens_by_workflow"]
    measured = set(cohort["measured_workflow_ids"])
    expected_censored = set(ids) - completed
    if (
        len(set(ids)) != len(ids) or not set(outputs) <= set(ids)
        or not completed <= outputs.keys()
        or any(type(value) is not int or value < 0 for value in outputs.values())
        or any(outputs[key] <= 0 for key in completed)
        or report.get("all_measured_completed") is not True
        or report.get("normal_tails_removed") is not False
        or report.get("measured_service_output_matches_complete_receipts") is not True
        or type(report.get("measured_exact_receipt_requests")) is not int
        or report["measured_exact_receipt_requests"] <= 0
        or report.get("nonmeasured_workflows_right_censored") != sorted(expected_censored)
        or sum(outputs.values()) != report["all_service_output_tokens"]
        or not positive(report["common_window_output_tokens_per_second"])
        or report["measured_complete_workflow_JCT_ms"] != distribution(
            report["measured_workflow_JCT_ms"].values()
        )
    ):
        raise ValueError("censored execution lacks complete measured receipts or explicit background scope")
    return {key: outputs[key] for key in measured}


def compare_censored_cohorts(control, oracle, *, minimum_jct_gain, minimum_throughput_fraction):
    cohort = control["cohort"]
    if (
        cohort != oracle["cohort"] or cohort.get("schema_version") != 2
        or cohort["selection_rule"] != "fixed_arrival_indices_before_execution"
        or cohort["complete_JCT_including_normal_tails"] is not True
        or cohort["background_submission_stops_when_cohort_finishes"] is not True
        or cohort.get("nonmeasured_workflows_may_be_right_censored") is not True
        or not 0 < minimum_jct_gain < 1 or not 0 < minimum_throughput_fraction <= 1
    ):
        raise ValueError("compare identical fixed measured completion contracts")
    outputs = [censored_cohort_evidence(row) for row in (control, oracle)]
    if outputs[0] != outputs[1] or (
        control["measured_exact_receipt_requests"] != oracle["measured_exact_receipt_requests"]
    ):
        raise ValueError("matched measured executions changed actual complete work")
    groups = {
        name: paired_group(
            cohort[name + "_workflow_ids"],
            control[name + "_workflow_JCT_ms"], oracle[name + "_workflow_JCT_ms"],
        ) for name in ("measured", "warmup", "background")
    }
    ratio = oracle["common_window_output_tokens_per_second"] / control["common_window_output_tokens_per_second"]
    checks = {
        "fixed_measured_mean_JCT_gain": (
            groups["measured"]["mean_JCT_reduction_percent"] >= minimum_jct_gain * 100
        ),
        "common_window_output_throughput_noninferior": ratio >= minimum_throughput_fraction,
        "background_covers_both_measured_tails": all(
            row["background_submission_coverage"]["covers_all_measured_completions"] is True
            for row in (control, oracle)
        ),
    }
    return {
        "groups": groups, "checks": checks,
        "fixed_cohort_performance_target_met": all(checks.values()),
        "common_window_output_throughput": {
            "window_offset_seconds": cohort["throughput_window_offset_seconds"],
            "control_tokens_per_second": control["common_window_output_tokens_per_second"],
            "oracle_tokens_per_second": oracle["common_window_output_tokens_per_second"],
            "gain_percent": 100 * (ratio - 1),
        },
        "thresholds": {
            "minimum_measured_mean_JCT_reduction": minimum_jct_gain,
            "minimum_common_window_output_throughput_fraction": minimum_throughput_fraction,
        },
        "complete_work_scope": "fixed_measured_workflows",
        "background_paired_JCT_is_descriptive_not_a_full_population_estimate": True,
    }


def compare_cohorts(control, oracle, *, minimum_jct_gain=.05, minimum_throughput_fraction=.98):
    if control["cohort"].get("schema_version") == 2:
        return compare_censored_cohorts(
            control, oracle, minimum_jct_gain=minimum_jct_gain,
            minimum_throughput_fraction=minimum_throughput_fraction,
        )
    if (
        control["cohort"] != oracle["cohort"]
        or control["cohort"]["selection_rule"] != "fixed_arrival_indices_before_execution"
        or control["cohort"]["complete_JCT_including_normal_tails"] is not True
        or control["cohort"]["background_submission_stops_when_cohort_finishes"] is not False
        or not 0 < minimum_jct_gain < 1
        or not 0 < minimum_throughput_fraction <= 1
    ):
        raise ValueError("compare the identical fixed complete cohort and declared thresholds")
    names = ("measured", "warmup", "background")
    for label, report in (("control", control), ("oracle", oracle)):
        ids = []
        for name in names:
            members = report["cohort"][name + "_workflow_ids"]
            values = report[name + "_workflow_JCT_ms"]
            if (
                not members or len(set(members)) != len(members)
                or set(values) != set(members) or not all(positive(value) for value in values.values())
            ):
                raise ValueError(label + " changed complete group membership or JCT")
            ids.extend(members)
        outputs = report["all_service_output_tokens_by_workflow"]
        if (
            len(set(ids)) != len(ids) or set(outputs) != set(ids)
            or any(type(value) is not int or value <= 0 for value in outputs.values())
            or report.get("all_workflows_completed") is not True
            or report.get("normal_tails_removed") is not False
            or report.get("service_output_matches_complete_receipts") is not True
            or type(report.get("exact_receipt_requests")) is not int
            or report["exact_receipt_requests"] <= 0
            or sum(outputs.values()) != report["all_service_output_tokens"]
            or not positive(report["common_window_output_tokens_per_second"])
            or report["measured_complete_workflow_JCT_ms"] != distribution(
                report["measured_workflow_JCT_ms"].values()
            )
        ):
            raise ValueError(label + " lacks complete reconciled cohort evidence")
    if any(control[name] != oracle[name] for name in (
        "exact_receipt_requests", "all_service_output_tokens", "all_service_output_tokens_by_workflow",
    )):
        raise ValueError("matched executions changed actual complete work")
    groups = {}
    for name in names:
        reference, candidate = (row[name + "_workflow_JCT_ms"] for row in (control, oracle))
        before, after = distribution(reference.values()), distribution(candidate.values())
        groups[name] = {
            "control_JCT_ms": before, "oracle_JCT_ms": after,
            "mean_JCT_reduction_percent": 100 * (1 - after["mean"] / before["mean"]),
            "improved_workflows": sum(candidate[key] < reference[key] for key in reference),
            "worsened_workflows": sum(candidate[key] > reference[key] for key in reference),
            "workflows": [
                {
                    "workflow_id": key, "control_JCT_ms": reference[key],
                    "oracle_JCT_ms": candidate[key], "saved_ms": reference[key] - candidate[key],
                }
                for key in control["cohort"][name + "_workflow_ids"]
            ],
        }
    ratio = (
        oracle["common_window_output_tokens_per_second"]
        / control["common_window_output_tokens_per_second"]
    )
    checks = {
        "fixed_measured_mean_JCT_gain": (
            groups["measured"]["mean_JCT_reduction_percent"] >= minimum_jct_gain * 100
        ),
        "common_window_output_throughput_noninferior": ratio >= minimum_throughput_fraction,
        "background_covers_both_measured_tails": all(
            row["background_submission_coverage"]["covers_all_measured_completions"] is True
            for row in (control, oracle)
        ),
    }
    return {
        "groups": groups, "checks": checks,
        "fixed_cohort_performance_target_met": all(checks.values()),
        "common_window_output_throughput": {
            "window_offset_seconds": control["cohort"]["throughput_window_offset_seconds"],
            "control_tokens_per_second": control["common_window_output_tokens_per_second"],
            "oracle_tokens_per_second": oracle["common_window_output_tokens_per_second"],
            "gain_percent": 100 * (ratio - 1),
        },
        "thresholds": {
            "minimum_measured_mean_JCT_reduction": minimum_jct_gain,
            "minimum_common_window_output_throughput_fraction": minimum_throughput_fraction,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "control", "oracle", "control-review-dir", "oracle-review-dir", "arrivals", "cohort", "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve every previous complete comparison")
    completion = [verify_execution(path, args.cohort) for path in (args.control, args.oracle)]
    if completion[0]["work"] != completion[1]["work"]:
        raise ValueError("both executions require equal complete work in their declared scope")
    plans = [causal.load(path) for path in (args.control, args.oracle)]
    specifications = [causal.load(Path(row["experiment_configuration"])) for row in plans]
    matching_conditions(*specifications)
    provenance = specifications[1]["candidate_provenance"]
    if provenance["control_registration_sha256"] != sha256_file(args.control):
        raise ValueError("oracle declares another control")
    decision_path, diagnostic = validate_declared_decision(provenance, args.control)
    contract = specifications[1]["comparison_contract"]
    reviews, cohorts, paths = [], [], [args.control, args.oracle, args.arrivals, args.cohort, decision_path]
    for registration, directory, evidence in (
        (args.control, args.control_review_dir, completion[0]),
        (args.oracle, args.oracle_review_dir, completion[1]),
    ):
        review_path, cohort_path = directory / "review.json", directory / "cohort.json"
        review, cohort = causal.load(review_path), causal.load(cohort_path)
        if (
            review["registration_sha256"] != sha256_file(registration)
            or cohort["cohort"] != causal.load(args.cohort)
            or any(cohort["inputs_sha256"].get(str(path.resolve())) != sha256_file(path)
                   for path in (args.arrivals, args.cohort))
        ):
            raise ValueError("complete reviews belong to another execution or measurement definition")
        review_matches_execution(review, evidence)
        for report in (review, cohort):
            for path, prefix in report.get("input_jsonl_prefixes", {}).items():
                if (
                    Path(path).stat().st_size != prefix["complete_prefix_bytes"]
                    or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
                ):
                    raise ValueError("complete review evidence changed")
        reviews.append(review)
        cohorts.append(cohort)
        paths.extend((review_path, cohort_path))
    result = compare_cohorts(
        *cohorts,
        minimum_jct_gain=contract["minimum_promising_measured_mean_JCT_reduction"],
        minimum_throughput_fraction=contract["common_window_output_throughput_noninferiority_fraction"],
    )
    if diagnostic is not None:
        paths.append(Path(provenance["diagnostic_request"]["path"]))
        result["explicit_user_diagnostic"] = {
            "request": diagnostic,
            "original_workload_rejection_preserved": True,
            "formal_workload_accepted": False,
            "numeric_JCT_and_throughput_targets_met": all(
                result["checks"][name] for name in (
                    "fixed_measured_mean_JCT_gain", "common_window_output_throughput_noninferior",
                )
            ),
            "coalescing_and_ranking_are_joint_change": contract[
                "coalescing_and_oracle_ranking_are_joint_change"
            ],
        }
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        complete_equal_work_fresh_drain_and_actual_exit=all(
            row["protocol"] == "all_workflows_completed" for row in completion
        ),
        complete_measured_work_fresh_drain_and_actual_exit=True,
        whole_population_metrics={
            name: row.get("metrics") for name, row in zip(("control", "oracle"), reviews)
        },
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            *paths, *(Path(plan["experiment_configuration"]) for plan in plans), Path(__file__),
        )},
        scope={
            "primary_JCT_is_fixed_arrival_cohort_with_complete_tails": True,
            "common_wall_window_throughput_includes_background": True,
            "whole_population_metrics_are_secondary": True,
            "mechanism_control_cost_and_recomputation_need_separate_review": True,
            "single_run_target_is_not_repeated_statistical_superiority": True,
            "oracle_future_information_used": True, "deployable": False, "optimal_JCT_bound": False,
            "GPU_action": False, "automatic_GPU_followup": False,
        },
        performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps({
        "measured_mean_JCT_reduction_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "common_window_output_throughput": result["common_window_output_throughput"],
        "checks": result["checks"],
    }))


if __name__ == "__main__":
    main()
