"""Record workload acceptance before an oracle or learned scheduling comparison."""

import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def validate_minimum_work_ratio(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 1.:
        raise ValueError("declare a finite model-work heterogeneity minimum of at least one")
    return float(value)


def inspect(
    control, cohort, supply, periodic, demand, population, *,
    minimum_model_work_p80_p20=3.,
):
    minimum = validate_minimum_work_ratio(minimum_model_work_p80_p20)
    counts = supply.get("sample_counts", {})
    samples, ready = counts.get("common_window", 0), counts.get("with_ready_paused", 0)
    reuse = supply.get("latest_native_counters_not_atomic_with_prefixes", {}).get(
        "context_prefix_reuse_evidence",
    ) or {}
    loss = reuse.get("counts", {}).get("previously_served_prefix_recompute_proxy_tokens")
    window = cohort["cohort"]["throughput_window_offset_seconds"]
    regular = [
        row for row in demand["observations"]
        if row.get("observation_kind") == "periodic"
        and window[0] <= row["arrival_offset_s"] < window[1]
    ]
    maximum = max((
        row["next_request_reusable_prior_union_fraction_of_combined_ceiling"]
        for row in regular
    ), default=None)
    model_ratio = population["population"]["model_work_units"]["p80_p20"]
    checks = {
        "complete_control_and_owned_exit": control.get("complete_work_and_actual_exit_verified") is True,
        "complete_workflow_population": (
            cohort.get("all_workflows_completed") is True
            and supply.get("all_workflows_completed") is True
            and supply.get("all_measured_completed") is True
        ),
        "fixed_complete_measured_JCT": (
            cohort["cohort"]["selection_rule"] == "fixed_arrival_indices_before_execution"
            and cohort.get("normal_tails_removed") is False
            and cohort.get("measured_complete_workflow_JCT_ms", {}).get("count")
            == len(cohort["cohort"]["measured_workflow_ids"])
            and len(cohort["cohort"]["measured_workflow_ids"]) >= 18
        ),
        "exact_output_receipt_conservation": cohort.get("service_output_matches_complete_receipts") is True,
        "background_arrivals_cover_measured_tails": (
            cohort.get("background_submission_coverage", {}).get("covers_all_measured_completions") is True
        ),
        "model_work_p80_p20_meets_declared_minimum": model_ratio >= minimum,
        "repeated_actual_periodic_competition": (
            periodic.get("counts", {}).get("cross_workflow_logical_set_competition_controls", 0) >= 10
        ),
        "ready_paused_supply_in_at_least_quarter_of_window_samples": samples > 0 and ready / samples >= .25,
        "zero_previously_served_same_context_prefix_loss_proxy": type(loss) is int and loss == 0,
        "regular_live_state_coverage": (
            0 < demand.get("regular_sample_interval_s", float("inf")) <= 60 and len(regular) >= 10
        ),
        "sampled_next_reuse_union_fits_combined_pool": maximum is not None and maximum <= 1.,
    }
    return {
        "accepted_for_oracle_diagnostic": all(checks.values()),
        "checks": checks, "failed_checks": [name for name, passed in checks.items() if not passed],
        "minimum_model_work_p80_p20": minimum,
        "model_work_p80_p20": model_ratio,
        "model_work_p80_p20_ge_3": model_ratio >= 3.,
        "model_work_p80_p20_ge_4": model_ratio >= 4.,
        "same_context_prefix_recompute_proxy_tokens": loss,
        "common_window_ready_paused_sample_fraction": ready / samples if samples else None,
        "common_window_regular_demand_samples": len(regular),
        "maximum_sampled_reusable_prior_union_fraction": maximum,
        "full_shape_status": population.get("JOIN_shape_status", "review_separate_shape_assessment"),
        "scope": {
            "diagnostic_minima_not_a_formal_steady_state_or_statistical_proof": True,
            "zero_prior_prefix_loss_required_for_this_low_recompute_calibration": True,
            "other_Host_block_recomputation_is_reported_separately_not_added": True,
            "logical_competition_still_requires_oracle_selection_to_actual_service_audit": True,
            "sampled_reuse_union_is_not_native_page_or_copy_feasibility": True,
            "JOIN_child_shape_limitations_not_waived_by_this_diagnostic": True,
            "no_policy_winners_or_completed_order_used_for_cohort_selection": True,
            "no_GPU_action": True, "no_automatic_GPU_followup": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "cohort", "supply", "periodic", "demand", "population", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--minimum-model-work-p80-p20", type=float, default=3.)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve every complete workload decision")
    names = ("control", "cohort", "supply", "periodic", "demand", "population")
    paths = [getattr(args, name) for name in names]
    result = inspect(
        *(json.loads(path.read_bytes()) for path in paths),
        minimum_model_work_p80_p20=args.minimum_model_work_p80_p20,
    )
    result["inputs_sha256"] = {
        str(path.resolve()): sha256_file(path) for path in (*paths, Path(__file__))
    }
    write(args.output, result)
    print(json.dumps({name: result[name] for name in (
        "accepted_for_oracle_diagnostic", "failed_checks", "maximum_sampled_reusable_prior_union_fraction",
    )}))


if __name__ == "__main__":
    main()
