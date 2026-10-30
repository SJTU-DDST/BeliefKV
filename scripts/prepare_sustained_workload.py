"""Expand the original project population before calibrating sustained arrivals."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_work_training_20261029.prepare import training_configuration
from scripts.prepare_locobench_agent_manifest import scenario_prompt
from scripts.train_workflow_work import write


def expand(base, pool, archive, references, *, count=36, seed=32):
    existing = {row["repo"]: row for row in base["workloads"]}
    available = {row["repo"]: row for row in pool["workloads"]}
    if (
        len(existing) != len(base["workloads"])
        or len(available) != len(pool["workloads"])
        or not len(existing) < count <= len(available)
        or not existing.keys() <= available.keys()
        or base["archive_sha256"] != pool["archive_sha256"]
    ):
        raise ValueError("expansion needs a larger distinct-project pool from the same original archive")
    tasks = deepcopy(list(existing.values()))
    candidates = {row["project_id"]: row for row in pool["candidates"]}
    added = sorted(
        (row for repo, row in available.items() if repo not in existing),
        key=lambda row: (row["frozen_repository_context"]["tokens"], row["repo"]),
    )[:count - len(tasks)]
    scopes = ("bug_investigation", "feature_implementation", "multi_session_development")
    references_out, assignments = {}, []
    for index, old in enumerate(added):
        category = scopes[index % len(scopes)]
        project = old["repo"].split("/", 1)[1]
        paths = [
            path for path in candidates[project]["scenario_paths"]
            if f"_{category}_" in path
        ]
        if len(paths) != 1:
            raise ValueError("each additional project needs one original task in its declared category")
        content = archive.read(paths[0])
        scenario = json.loads(content)
        task = deepcopy(old)
        task.update(
            instance_id="locobench__" + scenario["id"],
            problem_statement=scenario_prompt(scenario), difficulty=scenario["difficulty"],
        )
        reference = references / (scenario["id"] + ".reference.json")
        task["dataset_provenance"].update(
            original_scenario=paths[0], task_category=category,
            scenario_sha256=hashlib.sha256(content).hexdigest(),
            reference_path=str(reference.resolve()), reference_fields_exposed=False,
        )
        for field in ("test_command", "test_entrypoint_scope", "sandbox_environment"):
            task.pop(field, None)
        references_out[reference] = content
        assignments.append({
            "project": task["repo"], "instance_id": task["instance_id"],
            "original_task_category": category,
        })
        tasks.append(task)
    random.Random(seed).shuffle(tasks)
    result = deepcopy(pool)
    for field in ("environment_qualification", "tool_environment_qualification"):
        result.pop(field, None)
    result.update(
        workloads=tasks, requested_workflows=count, max_tasks_per_project=1,
        selected_projects=[row["repo"].split("/", 1)[1] for row in tasks],
        selected_scenarios=[row["dataset_provenance"]["original_scenario"] for row in tasks],
        selection_seed=seed,
        selection_policy=(
            "retain all eighteen original tasks; add distinct original projects by context order "
            "with rotating original categories; independent seeded registration permutation"
        ),
        purpose="expanded_natural_source_for_sustained_arrival_comparisons",
    )
    return result, assignments, references_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json")
    parser.add_argument("--pool", type=Path, default=ROOT / "configs/workloads/locobench_agent_36project_env_20261024.json")
    parser.add_argument("--base-config", type=Path, default=ROOT / "configs/migration/workflow_mix_scope18_native_20261029.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.exists():
        raise FileExistsError("preserve every workload preparation")
    base, pool = (json.loads(path.read_bytes()) for path in (args.base, args.pool))
    if sha256_file(Path(pool["archive"])) != pool["archive_sha256"]:
        raise ValueError("original dataset archive changed")
    with zipfile.ZipFile(pool["archive"]) as archive:
        manifest, added, references = expand(
            base, pool, archive, args.output_dir / "references",
        )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for path, content in references.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    unqualified = args.output_dir / "unqualified_manifest.json"
    qualified = args.output_dir / "qualified_manifest.json"
    write(unqualified, manifest)
    configuration = training_configuration(
        json.loads(args.base_config.read_bytes()), count=36, workload=qualified,
    )
    configuration.update(
        purpose="expanded36_natural_capture_then_sustained_arrival_comparison",
        pending_tool_qualification=True,
    )
    configuration["training_protocol"].update(
        purpose="collect_whole_project_disjoint_workflows_not_predictor_training_only",
        extra_tasks=36, preserve_existing_18_workflow_evaluation_and_all_previous_traces=True,
    )
    write(args.output_dir / "native.json", configuration)
    write(args.output_dir / "declaration.json", {
        "schema_version": 1, "workflow_count": 36, "distinct_projects": 36,
        "existing_tasks_retained": 18, "additional_original_tasks": added,
        "registration_seed": 32,
        "source_collection": {
            "mode": "natural_native_capture", "fresh_whole_capture_required": True,
            "model_requests_and_outputs_not_duplicated": True,
            "execution_protocol_preserved": True, "root_budgets": [8, 16],
            "child_budgets": [4, 8], "running_decode_limit": 24,
        },
        "performance_arrivals": {
            "initial_workflows": 8, "subsequent_workflows": 28,
            "permutation_seed": 21, "arrival_seed": 32,
            "rate_per_second": None,
            "calibrate_from_complete_new_population_control": True,
            "old_rate_not_claimed_as_new_capacity": True,
            "target_submission_window_share_of_control": [.7, .9],
            "retain_all_raw_draws_no_policy_outcome_selection": True,
        },
        "tool_environment_check_required": True, "task_tests_are_agent_responsibility": True,
        "original_project_sources_tests_unchanged": True,
        "GPU_action": False, "performance_accepted": False,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (args.base, args.pool, args.base_config, Path(__file__))
        },
    })
    print({
        "workflows": 36, "new_projects": len(added), "initial_workflows": 8,
        "manifest": str(unqualified), "GPU_action": False,
    })


if __name__ == "__main__":
    main()
