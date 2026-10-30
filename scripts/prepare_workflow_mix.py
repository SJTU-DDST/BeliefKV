"""Inventory whole natural traces before declaring a size-diverse workload."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture, population_summary
from scripts.train_workflow_work import write


DEFAULT_CAPTURES = (
    "locobench_parallel_retry_source_20261026_v230",
    "workflow_work_train8_source_20261029_r1",
    "workflow_work_source_20261029_matched6",
    "qwen3_coder_24project_native_source_20261024_v174",
    "qwen3_coder_18project_required_delegation_source_20261024_v181",
    "qwen3_coder_18project_root_phase_source_20261024_v187",
    "qwen3_coder_18project_root_phase16_source_20261024_v195",
)


def review(inventories, *, each=6, target_ratio=5.):
    protocols = defaultdict(list)
    for inventory in inventories:
        for row in inventory["workflows"]:
            if row["eligible"]:
                protocols[row["protocol_sha256"]].append(row)
    groups = []
    for digest, rows in sorted(protocols.items()):
        projects = defaultdict(list)
        for row in rows:
            projects[row["project"]].append(row)
        # This permissive envelope can only rule a pool out. It does not
        # manufacture a project-disjoint population or a feasible percentile.
        full = population_summary(rows)
        min_work = min(row["model_work_units"] for row in rows)
        max_work = max(row["model_work_units"] for row in rows)
        envelope = max_work / min_work if min_work else None
        groups.append({
            "protocol_sha256": digest, "population": full,
            "max_min_model_work_envelope": envelope,
            "can_supply_project_disjoint_count": len(projects) >= each * 3,
            "can_possibly_reach_target_ratio": envelope is not None and envelope >= target_ratio,
            "compatible_captures": sorted({row["source_capture"] for row in rows}),
        })
    qualifies = [
        group for group in groups
        if group["can_supply_project_disjoint_count"] and group["can_possibly_reach_target_ratio"]
    ]
    return {
        "schema_version": 1, "kind": "mixed_workload_preparation_review",
        "requested_workflows": each * 3, "requested_per_tier": each,
        "diagnostic_target_p90_p10": target_ratio, "protocol_groups": groups,
        "eligible_for_selection_requires_further_checks": [group["protocol_sha256"] for group in qualifies],
        "selection_status": "needs_explicit_selection" if qualifies else "needs_new_natural_candidates",
        "GPU_experiment_started": False, "performance_accepted": False,
        "selection_rules": {
            "whole_complete_workflows_only": True, "one_original_project_per_workflow": True,
            "same_execution_protocol": True, "stratification": "mean rank of output, necessary input, request count",
            "tool_durations_separate_from_size_strata": True,
            "context_crossing_required": True,
            "mechanism": "same resume category; ready-paused short/long candidates individually fit but not jointly",
            "performance_advantage_used_to_select": False,
            "normal_tail_removed": False,
            "burst": {"permutation_seed": 21, "initial_workflows": each * 3},
            "main": {
                "permutation_seed": 21, "arrival_seed": 22, "initial_workflows": 5,
                "offered_load": .9, "rate_per_second": None,
                "rate_requires_complete_same_population_control": True,
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--capture", type=Path, action="append")
    parser.add_argument("--model-cost-evidence", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    cost = json.loads(args.model_cost_evidence.read_bytes())["metrics"]["FULL"]["decode"]
    coefficient = cost["request_step_ms"] / cost["request_steps"] / 1000.
    inventories, rejected = [], []
    captures = args.capture or [
        ROOT / "experiments/raw" / name / "native_capture" for name in DEFAULT_CAPTURES
    ]
    for capture in captures:
        print("Profiling " + str(capture), flush=True)
        try:
            value = inventory_capture(capture, seconds_per_model_unit=coefficient)
        except (ValueError, KeyError, FileNotFoundError) as error:
            rejected.append({
                "capture": str(capture.resolve()),
                "reason": type(error).__name__ + ": " + str(error),
            })
            print("Preserved rejection: " + str(error), flush=True)
            continue
        inventories.append(value)
        write(args.output_dir / (capture.parent.name + ".json"), value)
    result = review(inventories)
    result.update(
        source_rejections=rejected,
        model_cost_evidence={
            "path": str(args.model_cost_evidence.resolve()),
            "sha256": sha256_file(args.model_cost_evidence),
            "seconds_per_model_unit": coefficient,
        },
        analysis_sha256={
            str(path.relative_to(ROOT)): sha256_file(path) for path in (
                Path(__file__), ROOT / "beliefkv/experiments/workload_mix.py",
            )
        },
    )
    write(args.output_dir / "review.json", result)
    print(json.dumps({
        "status": result["selection_status"], "groups": len(result["protocol_groups"]),
        "source_rejections": len(rejected),
    }))


if __name__ == "__main__":
    main()
