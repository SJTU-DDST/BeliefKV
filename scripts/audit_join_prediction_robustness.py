"""Measure error tolerance of recorded intrinsic branch-path alternatives, offline."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write


def relative_error_tolerance(other, delayed, competitor, overhead=0.):
    values = (other, delayed, competitor, overhead)
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("branch robustness requires finite nonnegative intrinsic path times")
    denominator = other + delayed + competitor
    margin = other - delayed - competitor - overhead
    return max(0., margin / denominator) if denominator > 0 else 0.


def inspect(oracle, measured):
    counts, rows = Counter(), []
    for group in oracle["group_observations"]:
        for member, candidate in group["candidate_labels"].items():
            for swap in candidate["single_cross_workflow_replacements"]:
                other = candidate["other_branch_path_proxy_s"]
                delayed = candidate["remaining_path_proxy_s"]
                competitor = swap.get("competitor_branch_path_proxy_s")
                if competitor is None:
                    counts["missing_explicit_RETURN_label"] += 1
                    continue
                margin = other - delayed - competitor
                fits = margin >= 0.
                counts["recorded_one_for_one_alternatives"] += 1
                counts["positive_intrinsic_margin"] += fits
                counts["measured_competitor_positive_intrinsic_margin"] += (
                    fits and swap["competitor_workflow"] in measured
                )
                sensitivity = {}
                for overhead in (0., .1, .5, 1., 2., 5.):
                    sensitivity[str(overhead)] = relative_error_tolerance(
                        other, delayed, competitor, overhead,
                    )
                for tolerance in (.1, .25, .5):
                    counts["intrinsic_alternatives_survive_" + str(tolerance)] += (
                        sensitivity["0.0"] >= tolerance
                    )
                rows.append({
                    "selection_sequence": group["selection_sequence"], "JOIN": group["group"],
                    "JOIN_workflow": group["workflow_id"], "delayed_member": member,
                    "competitor": swap["competitor"], "competitor_workflow": swap["competitor_workflow"],
                    "measured_competitor": swap["competitor_workflow"] in measured,
                    "other_branch_path_s": other, "delayed_branch_path_s": delayed,
                    "competitor_branch_path_s": competitor, "intrinsic_margin_s": margin,
                    "symmetric_relative_error_tolerance_by_overhead_s": sensitivity,
                    "current_tool_only_margin_s": (
                        candidate["other_active_tool_remaining_lower_bound_s"] - delayed - competitor
                    ),
                })
    positive = [row for row in rows if row["intrinsic_margin_s"] >= 0]
    counts["distinct_positive_JOINs"] = len({row["JOIN"] for row in positive})
    counts["distinct_positive_branch_pairs"] = len({
        (row["delayed_member"], row["competitor"]) for row in positive
    })
    return {
        "counts": dict(counts), "alternatives": rows,
        "positive_margin_s": distribution([row["intrinsic_margin_s"] for row in positive]),
        "positive_relative_error_tolerance": distribution([
            row["symmetric_relative_error_tolerance_by_overhead_s"]["0.0"] for row in positive
        ]),
        "scope": {
            "inequality": "(1-rho)*A >= (1+rho)*(B+C) + overhead",
            "tolerance": "max(0, (A-B-C-overhead)/(A+B+C))",
            "A_B_C_are_bound_future_intrinsic_RETURN_paths": True,
            "current_tool_return_time_alone_is_separately_checked": True,
            "does_not_predict_real_batch_interference_or_capacity_holding": True,
            "a_wide_intrinsic_margin_is_not_a_safe_online_delay_bound": True,
            "a_branch_RETURN_is_not_necessarily_workflow_completion": True,
            "end_to_end_JCT_counterfactual_or_net_benefit_proven": False,
            "prediction_accuracy_or_probability_coverage_measured": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("oracle", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve prior branch robustness evidence")
    oracle, cohort = (json.loads(path.read_bytes()) for path in (args.oracle, args.cohort))
    for name, digest in oracle["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("the sealed branch oracle input changed: " + name)
    result = inspect(oracle, set(cohort["measured_workflow_ids"]))
    result.update(
        inputs_sha256={str(path.resolve()): sha256_file(path)
                       for path in (args.oracle, args.cohort, Path(__file__))},
        GPU_action=False, online_policy_changed=False,
    )
    write(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "positive_margin_s", "positive_relative_error_tolerance",
    )}), flush=True)


if __name__ == "__main__":
    main()
