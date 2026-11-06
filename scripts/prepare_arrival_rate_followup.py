"""Change seeded arrival intensity or initial population, retaining whole tasks."""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import (
    permuted_poisson_arrival_plan, substitute_background_arrivals, validate_arrival_plan,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.train_workflow_work import write


def permuted_source(arrivals):
    source = (
        arrivals["original_plan"]
        if arrivals["mode"] == "background_substituted_poisson" else arrivals
    )
    if source["mode"] != "permuted_warm_start_poisson":
        raise ValueError("arrival calibration requires an original seeded permutation")
    return source


def followup_protocol(
    arrivals, cohort, *, mean_interval_seconds, initial_workflows=None,
):
    identities = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    validate_arrival_plan(arrivals, identities)
    validate_cohort(cohort, arrivals)
    if (
        arrivals["mode"] not in (
            "permuted_warm_start_poisson", "background_substituted_poisson",
        )
        or cohort.get("schema_version") != 2
        or type(mean_interval_seconds) not in (int, float)
        or not math.isfinite(mean_interval_seconds) or mean_interval_seconds <= 0
    ):
        raise ValueError("require seeded permuted arrivals, measured completion and a finite positive mean")
    initial = arrivals["initial_workflows"] if initial_workflows is None else initial_workflows
    if type(initial) is not int or initial <= 0 or (
        initial != arrivals["initial_workflows"]
        and initial > len(cohort["warmup_workflow_ids"])
    ):
        raise ValueError("initial workflows must be a positive integer within the fixed warmup group")
    source = permuted_source(arrivals)
    following = permuted_poisson_arrival_plan(
        [row["workflow_id"] for row in source["arrival_schedule"]],
        rate_per_second=1. / mean_interval_seconds, seed=source["seed"],
        permutation_seed=source["permutation_seed"],
        initial_workflows=initial,
    )
    if arrivals["mode"] == "background_substituted_poisson":
        following = substitute_background_arrivals(
            following, arrivals["replacements"],
            preserved_prefix_workflows=arrivals["preserved_prefix_workflows"],
        )
    scale = mean_interval_seconds * arrivals["rate_per_second"]
    if any(
        left["workflow_id"] != right["workflow_id"]
        or initial == arrivals["initial_workflows"] and not math.isclose(
            left["offset_seconds"] * scale, right["offset_seconds"],
            abs_tol=1e-8, rel_tol=1e-12,
        )
        for left, right in zip(arrivals["arrival_schedule"], following["arrival_schedule"])
    ):
        raise ValueError("arrival calibration changed workflow order or unchanged-population seeded slots")
    warmup, measured = len(cohort["warmup_workflow_ids"]), len(cohort["measured_workflow_ids"])
    offsets = [row["offset_seconds"] for row in following["arrival_schedule"]]
    analysis = declare_cohort(
        following, warmup=warmup, measured=measured, throughput_start_s=offsets[warmup],
        throughput_end_s=offsets[warmup + measured - 1], stop_after_measured=True,
    )
    if any(analysis[name] != cohort[name] for name in (
        "warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids",
    )):
        raise ValueError("arrival followup changed the fixed cohort")
    return following, analysis


def followup_configuration(previous, arrival_path, cohort_path=None):
    if previous["order"] != ["full"]:
        raise ValueError("retain the unsorted Full calibration before oracle comparisons")
    result = deepcopy(previous)
    result["workflow_arrivals"] = {
        "path": str(arrival_path.resolve()), "sha256": sha256_file(arrival_path),
    }
    changed = {"workflow_arrivals"}
    if "private_analysis_cohort" in previous:
        reference = previous["private_analysis_cohort"]
        if cohort_path is None or reference.get("delivered_to_scheduler") is not False:
            raise ValueError("retiming requires a new private analysis cohort reference")
        original_path = Path(reference["path"])
        if sha256_file(original_path) != reference["sha256"]:
            raise ValueError("previous private cohort declaration changed")
        original = json.loads(original_path.read_bytes())
        analysis = json.loads(cohort_path.read_bytes())
        validate_cohort(analysis, json.loads(arrival_path.read_bytes()))
        if any(original[name] != analysis[name] for name in (
            "warmup_workflow_ids", "measured_workflow_ids", "background_workflow_ids",
        )):
            raise ValueError("private cohort update changed fixed members")
        result["private_analysis_cohort"] = {
            **reference, "path": str(cohort_path.resolve()), "sha256": sha256_file(cohort_path),
        }
        changed.add("private_analysis_cohort")
    if {key: value for key, value in result.items() if key not in changed} != {
        key: value for key, value in previous.items() if key not in changed
    }:
        raise ValueError("arrival calibration changed a nonarrival setting")
    return result


def supply_window(audit, registration_path, cohort):
    if (
        audit.get("registration") != str(registration_path.resolve())
        or audit.get("inputs_sha256", {}).get(str(registration_path.resolve()))
        != sha256_file(registration_path)
    ):
        raise ValueError("supply evidence belongs to another serving registration")
    start, end = cohort["throughput_window_offset_seconds"]
    if "complete_common_window_observed" in audit:
        window = audit.get("common_window", {})
        if (
            audit["complete_common_window_observed"] is not True
            or window.get("start_s") != start or window.get("end_s") != end
        ):
            raise ValueError("calibration rationale does not cover the frozen control window")
        return window
    bins = audit.get("bins", [])
    observed = audit.get("observed_through_offset_s")
    if (
        audit.get("scope", {}).get("sampled_state_not_exact_control_decisions") is not True
        or type(observed) not in (int, float) or not math.isfinite(observed) or observed < end
        or not bins or bins[0].get("start_s") != start or bins[-1].get("end_s") != end
        or any(
            not start <= row["start_s"] < row["end_s"] <= end
            or type(row.get("samples")) is not int or row["samples"] < 0
            for row in bins
        )
        or any(left["end_s"] != right["start_s"] for left, right in zip(bins, bins[1:]))
        or sum(row["samples"] for row in bins) != audit.get("sample_counts", {}).get("common_window")
    ):
        raise ValueError("sampled supply evidence does not cover the frozen control window")
    return {
        "kind": "sampled_admission_supply_without_GPU_utilization",
        "start_s": start, "end_s": end, "sample_counts": audit["sample_counts"], "bins": bins,
        "time_weighted": False, "physical_counterfactual_verified": False,
    }


def completion_coverage_screen(arrivals, cohort, report, following):
    elapsed = report.get("measured_workflow_JCT_ms", {})
    if (
        report.get("cohort") != cohort or report.get("all_measured_completed") is not True
        or set(elapsed) != set(cohort["measured_workflow_ids"])
        or any(
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in elapsed.values()
        )
    ):
        raise ValueError("coverage screen requires complete JCTs for the original fixed cohort")
    original_end = max(
        row["offset_seconds"] + elapsed[row["workflow_id"]] / 1000.
        for row in arrivals["arrival_schedule"] if row["workflow_id"] in elapsed
    )
    observed_end = report.get("measured_cohort_completion_offset_s")
    if type(observed_end) not in (int, float) or not math.isfinite(observed_end) or not math.isclose(
        original_end, observed_end, abs_tol=1e-5, rel_tol=1e-12,
    ):
        raise ValueError("complete JCTs disagree with the recorded cohort completion")
    candidate_end = max(
        row["offset_seconds"] + elapsed[row["workflow_id"]] / 1000.
        for row in following["arrival_schedule"] if row["workflow_id"] in elapsed
    )
    last = following["arrival_schedule"][-1]["offset_seconds"]
    return {
        "kind": "fixed_observed_JCT_arrival_coverage_screen",
        "previous_latest_measured_completion_offset_s": original_end,
        "candidate_latest_completion_if_JCT_fixed_s": candidate_end,
        "candidate_last_scheduled_arrival_s": last,
        "candidate_background_margin_s": last - candidate_end,
        "candidate_schedule_covers_static_tail": last >= candidate_end,
        "previous_observed_JCTs_held_fixed": True,
        "new_serving_performance_predicted": False,
        "ready_supply_or_steady_state_proven": False,
        "formal_workload_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "previous-registration", "previous-configuration", "previous-cohort",
        "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--idle-audit", "--supply-audit", dest="supply_audit", type=Path, required=True)
    parser.add_argument("--previous-cohort-report", type=Path)
    parser.add_argument("--mean-interval-seconds", type=float, required=True)
    parser.add_argument("--initial-workflows", type=int)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier arrival and measurement declarations")
    registration = json.loads(args.previous_registration.read_bytes())
    previous = json.loads(args.previous_configuration.read_bytes())
    arrival_path = Path(previous["workflow_arrivals"]["path"])
    if sha256_file(arrival_path) != previous["workflow_arrivals"]["sha256"]:
        raise ValueError("previous arrival declaration changed")
    arrivals = json.loads(arrival_path.read_bytes())
    if registration["workflow_arrivals"] != arrivals:
        raise ValueError("previous serving registration used different arrivals")
    cohort = json.loads(args.previous_cohort.read_bytes())
    audit = json.loads(args.supply_audit.read_bytes())
    if any(
        audit.get("inputs_sha256", {}).get(str(path.resolve())) != sha256_file(path)
        for path in (arrival_path, args.previous_cohort)
    ):
        raise ValueError("supply evidence used another arrival or cohort declaration")
    window = supply_window(audit, args.previous_registration, cohort)
    following, analysis = followup_protocol(
        arrivals, cohort, mean_interval_seconds=args.mean_interval_seconds,
        initial_workflows=args.initial_workflows,
    )
    coverage = None
    if args.previous_cohort_report is not None:
        coverage = completion_coverage_screen(
            arrivals, cohort, json.loads(args.previous_cohort_report.read_bytes()), following,
        )
    args.output_dir.mkdir(parents=True)
    new_arrivals, new_cohort = args.output_dir / "arrivals.json", args.output_dir / "analysis_cohort.json"
    write(new_arrivals, following)
    write(new_cohort, analysis)
    config = followup_configuration(previous, new_arrivals, new_cohort)
    write(args.output_dir / "full.json", config)
    offsets = [row["offset_seconds"] for row in following["arrival_schedule"]]
    same_initial = following["initial_workflows"] == arrivals["initial_workflows"]
    write(args.output_dir / "preparation.json", {
        "kind": (
            "same_workload_arrival_intensity_calibration" if same_initial
            else "same_workload_initial_population_and_arrival_intensity_calibration"
        ),
        "previous_registration": str(args.previous_registration.resolve()),
        "workflow_count": len(offsets), "initial_workflows": following["initial_workflows"],
        "previous_initial_workflows": arrivals["initial_workflows"],
        "initial_population_changed": not same_initial,
        "mean_interval_seconds": args.mean_interval_seconds,
        "previous_mean_interval_seconds": 1. / arrivals["rate_per_second"],
        "arrival_seed": following["seed"],
        "permutation_seed": permuted_source(following)["permutation_seed"],
        "whole_background_identity_substitutions_retained": (
            following.get("replacements") == arrivals.get("replacements")
        ),
        "warmup": len(analysis["warmup_workflow_ids"]),
        "measured": len(analysis["measured_workflow_ids"]),
        "background": len(analysis["background_workflow_ids"]),
        "first_measured_arrival_s": analysis["throughput_window_offset_seconds"][0],
        "last_measured_arrival_s": analysis["throughput_window_offset_seconds"][1],
        "last_scheduled_arrival_s": offsets[-1],
        "maximum_arrival_gap_s": max(b - a for a, b in zip(offsets, offsets[1:])),
        "fixed_members_and_seeded_slot_draws_retained": same_initial,
        "fixed_cohort_members_retained": True,
        "common_seeded_interarrival_draw_prefix_retained": True,
        "workflow_slot_offsets_are_rate_scaled": same_initial,
        "all_nonarrival_configuration_fields_identical": "private_analysis_cohort" not in previous,
        "all_serving_settings_other_than_arrivals_identical": True,
        "private_analysis_cohort_reference_updated": "private_analysis_cohort" in previous,
        "all_original_workflows_tools_tokens_and_dependencies_retained": True,
        "complete_measured_JCT_and_explicit_background_censoring": True,
        "cohort_not_delivered_to_scheduler": True,
        "previous_common_window_supply": window,
        "previous_measured_JCT_static_coverage_screen": coverage,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.previous_registration, args.previous_configuration, args.previous_cohort,
            args.supply_audit, arrival_path, Path(__file__),
            *([args.previous_cohort_report] if args.previous_cohort_report is not None else []),
        )},
        "outputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            new_arrivals, new_cohort, args.output_dir / "full.json",
        )},
        "fresh_source_bound_CPU_and_GPU_qualification_required": True,
        "pressure_supply_and_background_coverage_require_actual_calibration": True,
        "GPU_action": False, "oracle_registered": False, "performance_accepted": False,
        "formal_workload_accepted": False,
    })
    print(json.dumps({
        "workflows": len(offsets), "mean_interval_seconds": args.mean_interval_seconds,
        "measurement_window_seconds": analysis["throughput_window_offset_seconds"],
        "last_arrival_s": offsets[-1], "GPU_action": False,
        "static_tail_coverage": coverage,
    }))


if __name__ == "__main__":
    main()
