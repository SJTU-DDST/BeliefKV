"""Compose the last declared Oracle diagnostic from the complete 100-project bank."""

from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "final_oracle_original82_source_r1"
BANK = REPORTS / "background_extension_source25_timing_requalification_r1/original81_population_r1.json"
NEW = REPORTS / "remaining_node_original19_finish_r2/inventory.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture, population_summary
from scripts import run_causal_comparison as causal
from scripts.prepare_composite_workload import prepare, project_representatives, select_diverse_projects
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve final attempts and use nice19 CPU-only preparation")
    bank, new = causal.load(BANK), causal.load(NEW)
    rows = [*bank["workflows"], *new["workflows"]]
    if len(rows) != 100 or len({row["project"] for row in rows}) != 100:
        raise ValueError("require all one hundred distinct complete original projects")
    sources = OrderedDict.fromkeys(row["source_capture"] for row in rows)
    if len(sources) != 7:
        raise ValueError("the complete seven-source bank changed")
    declaration = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "last_new_workload_oracle_diagnostic_before_retiring_size_scheduling",
        "complete_original_project_bank": 100, "new_original_projects": 19,
        "source_capture_order": list(sources),
        "selection": "first project representative; remove nearest intrinsic model-work median",
        "minimum_model_work_p80_p20": 3., "minimum_primary_projects": 72,
        "ratio_four_reported_as_separate_diagnostic_only": True,
        "new_source_collection_or_task_rewriting_allowed": False,
        "tool_durations_unchanged": True,
        "ordinary_initial_workflows": 12, "arrival_mean_seconds": 70.,
        "arrival_seed": 35, "warmup_workflows": 12, "fixed_measured_workflows": 18,
        "static_permutation_seed_search": [61, 256],
        "measurement_must_include_at_least_four_new_source_workflows": True,
        "measurement_model_work_p80_p20_minimum": 4.,
        "oracle_candidate": "capacity_v6_full_batch_cost_with_matching_completed_control_costs",
        "mean_JCT_reduction_required": .05,
        "common_window_output_throughput_noninferiority_fraction": .98,
        "if_not_promising": "retire_workflow_size_ranking_and_use_completed_original56_initial12_mean70",
        "no_more_workload_or_policy_grid_search_after_this_pair": True,
        "oracle_is_not_an_optimal_JCT_upper_bound": True,
        "GPU_action": False, "performance_accepted": False,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            BANK, NEW, REPORTS / "remaining_node_original19_finish_r2/complete.json", Path(__file__),
        )},
    }
    write(HERE / "declaration.json", declaration)
    paths, inventories = [], []
    coefficient = .019149832536849994
    for index, source in enumerate(sources, 1):
        inventory = inventory_capture(Path(source), seconds_per_model_unit=coefficient)
        path = HERE / "inventories" / f"source_{index}.json"
        write(path, inventory)
        inventories.append(inventory)
        paths.append(path)
        print(json.dumps({"inventory": index, "projects": len(inventory["workflows"])}), flush=True)
    representatives, retained = project_representatives(inventories)
    known = {row["project"]: row for row in rows}
    if len(representatives) != 100 or any(
        tuple(row[name] for name in ("task_id", "source_capture", "requests", "generated_tokens", "model_work_units"))
        != tuple(known[row["project"]][name] for name in (
            "task_id", "source_capture", "requests", "generated_tokens", "model_work_units",
        )) for row in representatives
    ):
        raise ValueError("refresh changed original project representatives or intrinsic work")
    alternate, excluded = select_diverse_projects(
        representatives, minimum_ratio=4., minimum_projects=36,
    )
    write(HERE / "ratio_four_alternative.json", {
        "population": population_summary(alternate), "selected": alternate,
        "excluded_whole_workflows_retained": excluded, "GPU_action": False,
        "primary_workload": False, "performance_selected": False,
    })
    result = prepare(
        inventories, HERE / "composite", inventory_paths=paths, audit_shape=True,
        minimum_work_ratio=3., minimum_projects=72,
        allow_reviewed_native_engine_variants=True,
    )
    population = causal.load(HERE / "composite/composite_source.json")
    write(HERE / "preparation.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "result": result,
        "primary_population": population["population"],
        "new_source_selected": sum(
            row["source_capture"] == new["capture"] for row in population["selected_workflows"]
        ),
        "whole_duplicate_project_alternatives_retained": len(retained),
        "ratio_four_alternative": len(alternate), "GPU_action": False,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            HERE / "declaration.json", *paths, HERE / "ratio_four_alternative.json",
            HERE / "composite/composite_source.json", Path(__file__),
        )},
        "formal_workload_accepted": False, "performance_accepted": False,
    })
    print(json.dumps({"primary_projects": result["projects"],
                      "ratio": population["population"]["model_work_units"]["p80_p20"],
                      "ratio_four_projects": len(alternate), "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
