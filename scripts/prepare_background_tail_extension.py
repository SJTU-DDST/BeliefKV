"""Prepare distinct original background workflows without changing measured members."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import extend_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import declare_cohort, validate_cohort
from beliefkv.experiments.workload_mix import summary
from scripts.train_workflow_work import write


def extend_population(base, available, arrivals, cohort, *, extra_count, minimum_ratio):
    validate_cohort(cohort, arrivals)
    before = base["selected_workflows"]
    all_rows = available["workflows"]
    base_projects = {row["project"] for row in before}
    candidates = {row["project"]: row for row in all_rows}
    if (
        type(extra_count) is not int or extra_count < 1
        or len(base_projects) != len(before) or len(candidates) != len(all_rows)
        or {row["workflow_id"] for row in before}
        != {row["workflow_id"] for row in arrivals["arrival_schedule"]}
        or any(candidates.get(row["project"]) != row for row in before)
    ):
        raise ValueError("extension requires unchanged distinct original projects and arrivals")
    extra = sorted(
        (row for row in all_rows if row["project"] not in base_projects),
        key=lambda row: (row["model_work_units"], row["project"]),
    )[:extra_count]
    if len(extra) != extra_count or any(
        row.get("eligible") is not True or row.get("rejection_reasons") for row in extra
    ):
        raise ValueError("not enough eligible original background workflows")
    selected = deepcopy([*before, *extra])
    if len({row["workflow_id"] for row in selected}) != len(selected):
        raise ValueError("extended original projects share a workflow identity")
    work = summary([row["model_work_units"] for row in selected])
    if work["p80_p20"] < minimum_ratio:
        raise ValueError("background extension falls below the accepted work spread")
    extended = extend_poisson_arrival_plan(arrivals, [row["workflow_id"] for row in extra])
    updated = declare_cohort(
        extended, warmup=len(cohort["warmup_workflow_ids"]),
        measured=len(cohort["measured_workflow_ids"]),
        throughput_start_s=cohort["throughput_window_offset_seconds"][0],
        throughput_end_s=cohort["throughput_window_offset_seconds"][1],
        stop_after_measured=cohort.get("schema_version") == 2,
    )
    for key in (
        "warmup_workflow_ids", "measured_workflow_ids", "throughput_window_offset_seconds",
        "complete_JCT_including_normal_tails", "background_submission_stops_when_cohort_finishes",
    ):
        if updated[key] != cohort[key]:
            raise ValueError("background extension changed measured work or timing")
    return {
        "kind": "original_background_extension_plan_not_executable_source",
        "selected_workflows": selected, "additional_workflows": deepcopy(extra),
        "projects": len(selected), "model_work_units": work,
        "model_work_p80_p20_ge_3": work["p80_p20"] >= 3,
        "model_work_p80_p20_ge_4": work["p80_p20"] >= 4,
        "minimum_model_work_p80_p20": minimum_ratio,
        "selection_rule": "smallest_intrinsic_model_work_among_previously_unused_complete_projects",
        "JCT_or_method_winner_used_for_selection": False,
        "original_arrival_prefix_preserved": True,
        "measured_members_and_common_wall_window_preserved": True,
        "tool_overlay_or_predictor_changed": False,
        "GPU_registration": None, "formal_workload_accepted": False, "performance_accepted": False,
    }, extended, updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source-population", "available-population", "accepted-population", "arrivals", "cohort",
        "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--extra-workflows", type=int, default=4)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve previous preparations and use nice19 CPU-only execution")
    paths = (
        args.source_population, args.available_population, args.accepted_population,
        args.arrivals, args.cohort, Path(__file__),
        ROOT / "beliefkv/experiments/replay_arrivals.py",
        ROOT / "beliefkv/experiments/workflow_cohort.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    base, available, accepted, arrivals, cohort = (
        json.loads(path.read_bytes()) for path in paths[:5]
    )
    minimum = accepted["population"]["model_work_units"]["p80_p20"]
    population, extended, updated = extend_population(
        base, available, arrivals, cohort, extra_count=args.extra_workflows, minimum_ratio=minimum,
    )
    if any(sha256_file(Path(name)) != value for name, value in pins.items()):
        raise ValueError("background preparation inputs changed")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "population_plan.json", population)
    write(args.output_dir / "arrivals.json", extended)
    write(args.output_dir / "analysis_cohort.json", updated)
    write(args.output_dir / "preparation.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "original_projects": len(base["selected_workflows"]), "projects": population["projects"],
        "additional_projects": [row["project"] for row in population["additional_workflows"]],
        "original_last_arrival_seconds": arrivals["arrival_schedule"][-1]["offset_seconds"],
        "extended_last_arrival_seconds": extended["arrival_schedule"][-1]["offset_seconds"],
        "rate_per_second": extended["rate_per_second"],
        "model_work_p80_p20": population["model_work_units"]["p80_p20"],
        "minimum_ratio_basis": "no_lower_than_the_user_accepted_complete_original56_population",
        "minimum_ratio": minimum, "measured_workflows": len(updated["measured_workflow_ids"]),
        "same_original_project_rows_and_arrival_prefix": True,
        "original_tools_tokens_and_dependency_graphs_must_still_be_preserved": True,
        "future_scheduled_arrival_is_not_proof_of_actual_ready_work_or_pressure": True,
        "must_measure_actual_ready_supply_pressure_Host_loss_and_complete_JCT": True,
        "executable_composition_registration_and_fresh_GPU_qualification_pending": True,
        "tool_overlay_and_prior_binding_for_new_population_pending": True,
        "source_engine_and_policy_registration_pending": True,
        "GPU_action": False, "GPU_followup_registered": False,
        "automatic_successor_to_running_pair": False, "formal_workload_accepted": False,
        "inputs_sha256": pins,
        "artifacts_sha256": {
            name: sha256_file(args.output_dir / name)
            for name in ("population_plan.json", "arrivals.json", "analysis_cohort.json")
        },
    })
    print(json.dumps({
        "projects": population["projects"], "measured": len(updated["measured_workflow_ids"]),
        "P80_P20": population["model_work_units"]["p80_p20"],
        "last_arrival_seconds": extended["arrival_schedule"][-1]["offset_seconds"],
        "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
