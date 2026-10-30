"""Reuse complete original workflows and refresh only their offline metrics."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import (
    inventory_capture, population_summary, stratify, summary,
)
from scripts.prepare_original_task_mix import validate_original_task
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/reused_original_workflows_20261030"
BASE = ROOT / "experiments/reports/workflow_mix_20261029/scope18_finish_r1/inventory.json"
ALTERNATIVES = tuple(
    ROOT / "experiments/reports/workflow_mix_20261029/inventory_r4" / name
    for name in (
        "workflow_work_train8_source_20261029_r1.json",
        "workflow_work_source_20261029_matched6.json",
        "locobench_parallel_retry_source_20261026_v230.json",
    )
)


def choose_reuse(base, inventories, *, target_ratio=5.):
    """Prefer the existing population, then the smallest one-task substitution."""
    selected = list(base["workflows"])
    if (
        not selected or len(selected) % 3
        or len({row["project"] for row in selected}) != len(selected)
        or any(not row["eligible"] for row in selected)
        or len({row["protocol_sha256"] for row in selected}) != 1
        or target_ratio < 1
    ):
        raise ValueError("reuse needs complete project-disjoint baseline workflows")
    by_project = {row["project"]: index for index, row in enumerate(selected)}
    digest = selected[0]["protocol_sha256"]
    seen = {(row["project"], row["task_id"]) for row in selected}
    canonical = list(selected)
    duplicates, incompatible = [], []
    for inventory in inventories:
        for row in inventory["workflows"]:
            key = row["project"], row["task_id"]
            if not row["eligible"] or row["protocol_sha256"] != digest:
                incompatible.append({
                    "project": row["project"], "task_id": row["task_id"],
                    "source_capture": row["source_capture"],
                    "reason": "natural_return_or_execution_protocol_mismatch",
                })
            elif key in seen:
                duplicates.append({
                    "project": row["project"], "task_id": row["task_id"],
                    "source_capture": row["source_capture"],
                    "reason": "retain_predeclared_representative_not_the_shortest_rerun",
                })
            else:
                seen.add(key)
                canonical.append(row)
    before = summary([row["model_work_units"] for row in selected])
    candidates = []
    if before["p90_p10"] < target_ratio:
        for row in canonical:
            index = by_project.get(row["project"])
            if index is None or row["task_id"] == selected[index]["task_id"]:
                continue
            trial = [*selected]
            trial[index] = row
            ratio = summary([item["model_work_units"] for item in trial])["p90_p10"]
            if ratio >= target_ratio:
                candidates.append((
                    abs(row["model_work_units"] - selected[index]["model_work_units"]),
                    row["project"], row["task_id"], row["source_capture"], index, row, ratio,
                ))
    changed = []
    if candidates:
        _, _, _, _, index, winner, _ = min(candidates, key=lambda item: item[:4])
        old = selected[index]
        selected[index] = winner
        changed.append({
            "project": old["project"], "previous_task": old["task_id"],
            "selected_task": winner["task_id"], "previous_source": old["source_capture"],
            "selected_source": winner["source_capture"],
            "absolute_model_work_delta": abs(old["model_work_units"] - winner["model_work_units"]),
        })
    after = summary([row["model_work_units"] for row in selected])
    available = summary([row["model_work_units"] for row in canonical])
    return {
        "selected_workflows": deepcopy(selected), "canonical_candidates": deepcopy(canonical),
        "before_model_work": before, "after_model_work": after,
        "target_ratio": target_ratio, "model_work_diversity_met": after["p90_p10"] >= target_ratio,
        "available_model_work_max_min_envelope": available["max"] / available["min"],
        "available_pool_cannot_reach_target_even_before_project_constraints": (
            available["max"] / available["min"] < target_ratio
        ),
        "substitutions": changed, "retained_duplicate_executions": duplicates,
        "incompatible_candidates": incompatible,
        "unique_projects": len({row["project"] for row in canonical}),
        "selection_policy": (
            "retain base original-task representatives; first declared alternative per original task; "
            "if needed use one different original task with the smallest absolute model-work change "
            "that reaches the declared ratio; never choose a lucky rerun"
        ),
    }


def original_source_proof(inventory):
    capture = Path(inventory["capture"])
    manifests = list(capture.glob("client_*/manifest.json"))
    if len(manifests) != 1:
        raise ValueError("one original capture manifest is required")
    client = json.loads(manifests[0].read_bytes())
    workload_path = Path(client["config"]["workload_manifest"])
    if sha256_file(workload_path) != client["workload_manifest_sha256"]:
        raise ValueError("captured original task manifest changed")
    workload = json.loads(workload_path.read_bytes())
    tasks = {row["instance_id"]: row for row in workload["workloads"]}
    with zipfile.ZipFile(workload["archive"]) as archive:
        for row in inventory["workflows"]:
            task = tasks[row["task_id"]]
            validate_original_task(task, archive)
            if task["repo"] != row["project"]:
                raise ValueError("inventory project differs from its original task")
    return {
        "client_manifest": str(manifests[0]), "client_manifest_sha256": sha256_file(manifests[0]),
        "workload": str(workload_path), "workload_sha256": sha256_file(workload_path),
        "complete_original_task_prompts_verified": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-inventory", type=Path, default=BASE)
    parser.add_argument("--alternative-inventory", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, default=HERE / "review_r2")
    parser.add_argument("--target-ratio", type=float, default=5.)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve every reuse selection and prior assessment")
    inputs = [args.base_inventory, *(args.alternative_inventory or ALTERNATIVES)]
    inventories = [json.loads(path.read_bytes()) for path in inputs]
    originals = {item["capture"]: original_source_proof(item) for item in inventories}
    decision = choose_reuse(inventories[0], inventories[1:], target_ratio=args.target_ratio)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "declared_selection.json", decision)
    coefficient = inventories[0]["model_seconds_per_unit"]
    current = {}
    refreshed = {}
    for capture in sorted({row["source_capture"] for row in decision["selected_workflows"]}):
        print("Refreshing CPU metrics only: " + capture, flush=True)
        value = inventory_capture(Path(capture), seconds_per_model_unit=coefficient)
        write(args.output_dir / (Path(capture).parent.name + ".inventory.json"), value)
        refreshed[capture] = value
        current.update({
            (capture, row["workflow_id"]): row for row in value["workflows"]
        })
    selected = []
    for old in decision["selected_workflows"]:
        row = current[(old["source_capture"], old["workflow_id"])]
        if any(row[key] != old[key] for key in (
            "project", "task_id", "event_sha256", "token_source_sha256", "model_work_units",
        )):
            raise ValueError("refreshed metrics changed the frozen whole-workflow identity")
        selected.append(row)
    tiers = stratify(selected, each=len(selected) // 3, include_tools=True)
    report = {
        "schema_version": 1, "kind": "existing_original_trace_reuse_selection",
        **decision, "selected_workflows": tiers,
        "population": population_summary(tiers),
        "model_observed_tool_path_s": summary([
            row["critical_model_observed_tool_proxy_s"] for row in tiers
        ]),
        "tool_critical_path_s": summary([row["critical_tool_only_s"] for row in tiers]),
        "common_seconds_per_model_work_unit": coefficient,
        "original_source_proofs": originals,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            *inputs, Path(__file__), ROOT / "beliefkv/experiments/workload_mix.py",
        )},
        "trace_reuse_contract": {
            "complete_original_workflows_only": True, "one_original_project_per_workflow": True,
            "preserve_exact_tokens_dependencies_stopping_and_tool_durations": True,
            "capture_concurrency_not_a_compatibility_requirement": True,
            "new_arrival_or_concurrency_requires_fresh_capture": False,
            "new_source_population_has_no_matched_native_performance_result": True,
            "tool_critical_paths_take_parallel_max_not_sum": True,
            "source_queueing_JCT_or_policy_wins_used_for_size": False,
            "all_nonselected_workflows_and_normal_tails_retained": True,
        },
        "pending": [
            "finish_current_six_original_workflows_and_review_complementary_projects",
            "freeze_multisource_replay_bindings_before_any_comparison",
            "qualify_join_shape_background_population_arrivals_and_physical_pressure",
        ],
        "formal_comparison_ready": False, "GPU_action": False,
    }
    write(args.output_dir / "review.json", report)
    print({
        "reused_workflows": len(tiers), "projects": len({row["project"] for row in tiers}),
        "canonical_original_tasks": len(decision["canonical_candidates"]),
        "model_work_p90_p10": report["after_model_work"]["p90_p10"],
        "substitutions": len(report["substitutions"]), "GPU_action": False,
    })


if __name__ == "__main__":
    main()
