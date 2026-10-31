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
    permuted_poisson_arrival_plan, validate_arrival_plan,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from scripts.train_workflow_work import write


def followup_protocol(
    arrivals, cohort, *, mean_interval_seconds, initial_workflows=None,
):
    identities = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    validate_arrival_plan(arrivals, identities)
    validate_cohort(cohort, arrivals)
    if (
        arrivals["mode"] != "permuted_warm_start_poisson"
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
    following = permuted_poisson_arrival_plan(
        identities, rate_per_second=1. / mean_interval_seconds, seed=arrivals["seed"],
        permutation_seed=arrivals["permutation_seed"],
        initial_workflows=initial,
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


def followup_configuration(previous, arrival_path):
    if previous["order"] != ["full"]:
        raise ValueError("retain the unsorted Full calibration before oracle comparisons")
    result = deepcopy(previous)
    result["workflow_arrivals"] = {
        "path": str(arrival_path.resolve()), "sha256": sha256_file(arrival_path),
    }
    if {key: value for key, value in result.items() if key != "workflow_arrivals"} != {
        key: value for key, value in previous.items() if key != "workflow_arrivals"
    }:
        raise ValueError("arrival calibration changed a nonarrival setting")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "previous-registration", "previous-configuration", "previous-cohort",
        "idle-audit", "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
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
    audit = json.loads(args.idle_audit.read_bytes())
    if (
        audit["registration"] != str(args.previous_registration.resolve())
        or audit["inputs_sha256"][str(args.previous_registration.resolve())]
        != sha256_file(args.previous_registration)
        or not audit["complete_common_window_observed"]
        or audit["common_window"]["start_s"] != cohort["throughput_window_offset_seconds"][0]
        or audit["common_window"]["end_s"] != cohort["throughput_window_offset_seconds"][1]
    ):
        raise ValueError("calibration rationale does not belong to the frozen control window")
    following, analysis = followup_protocol(
        arrivals, cohort, mean_interval_seconds=args.mean_interval_seconds,
        initial_workflows=args.initial_workflows,
    )
    args.output_dir.mkdir(parents=True)
    new_arrivals, new_cohort = args.output_dir / "arrivals.json", args.output_dir / "analysis_cohort.json"
    write(new_arrivals, following)
    write(new_cohort, analysis)
    config = followup_configuration(previous, new_arrivals)
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
        "arrival_seed": following["seed"], "permutation_seed": following["permutation_seed"],
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
        "all_nonarrival_configuration_fields_identical": True,
        "all_original_workflows_tools_tokens_and_dependencies_retained": True,
        "complete_measured_JCT_and_explicit_background_censoring": True,
        "cohort_not_delivered_to_scheduler": True,
        "previous_common_window_supply": audit["common_window"],
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.previous_registration, args.previous_configuration, args.previous_cohort,
            args.idle_audit, arrival_path, Path(__file__),
        )},
        "outputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            new_arrivals, new_cohort, args.output_dir / "full.json",
        )},
        "fresh_source_bound_CPU_and_GPU_qualification_required": True,
        "pressure_supply_and_background_coverage_require_actual_calibration": True,
        "GPU_action": False, "oracle_registered": False, "performance_accepted": False,
    })
    print(json.dumps({
        "workflows": len(offsets), "mean_interval_seconds": args.mean_interval_seconds,
        "measurement_window_seconds": analysis["throughput_window_offset_seconds"],
        "last_arrival_s": offsets[-1], "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
