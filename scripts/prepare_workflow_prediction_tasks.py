"""Select new original tasks confined to existing training projects."""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.prepare_locobench_agent_manifest import ARCHIVE_SHA256, scenario_prompt
from scripts.prepare_original_task_mix import original_task, source_integrity, validate_population
from scripts.train_workflow_work import capture_split, write
from scripts.train_workflow_work_distribution import split_rows


CATEGORIES = (
    "code_comprehension", "architectural_understanding", "bug_investigation",
    "feature_implementation", "cross_file_refactoring", "integration_testing",
    "multi_session_development",
)


def select_tasks(catalog, *, training_projects, held_out_projects, used_task_templates, count=18, seed=21):
    if (
        not training_projects or training_projects & held_out_projects
        or type(count) is not int or count < 1
    ):
        raise ValueError("new prediction tasks require disjoint whole-project training and holdouts")
    candidates = [
        row for row in catalog
        if row["project"] in training_projects and row["project"] not in held_out_projects
        and row["task_template"] not in used_task_templates and row["category"] in CATEGORIES
    ]
    candidates.sort(key=lambda row: hashlib.sha256(
        f"{seed}:{row['project']}:{row['original_id']}".encode(),
    ).digest())
    selected, projects = [], set()
    while len(selected) < count:
        before = len(selected)
        for category in CATEGORIES:
            for row in candidates:
                if row["category"] == category and row["project"] not in projects:
                    selected.append(row)
                    projects.add(row["project"])
                    break
            if len(selected) == count:
                break
        if len(selected) == before:
            raise ValueError("not enough distinct training projects with unused original tasks")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "extra-training-data", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-registration", type=Path, required=True, action="append")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--count", type=int, default=18)
    parser.add_argument("--seed", type=int, default=21)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior original-task training preparations")
    paths = (args.dataset, args.cohort, args.extra_training_data, args.archive, *args.source_registration)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    if pins[str(args.archive.resolve())] != ARCHIVE_SHA256:
        raise ValueError("new tasks must come from the pinned complete original dataset")
    data, cohort, extra = (json.loads(path.read_bytes()) for path in paths[:3])
    train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if extra["training_extension"]["original_dataset_sha256"] != pins[str(args.dataset.resolve())]:
        raise ValueError("the training-only extension changed")
    train = disjoint_training_rows(train, extra["rows"], calibration, test)
    expanded = capture_split(train, calibration, test, require_capture_disjoint=False)
    if expanded != extra["training_extension"]["expanded_split"]:
        raise ValueError("new-task selection changed an existing complete-workflow split")
    training_projects = {row["project"] for row in train}
    held_out_projects = {row["project"] for row in calibration + test}
    templates, manifests = {}, []
    for registration in args.source_registration:
        registered = json.loads(registration.read_bytes())
        path = Path(registered["environment"]["WORKLOAD_MANIFEST"])
        manifest = json.loads(path.read_bytes())
        pins[str(path.resolve())] = sha256_file(path)
        if manifest["archive_sha256"] != ARCHIVE_SHA256:
            raise ValueError("source snapshots refer to a different original archive")
        manifests.append(manifest)
        for task in manifest["workloads"]:
            if task["repo"] in training_projects and task["repo"] not in templates:
                templates[task["repo"]] = task
    used = {row["task_template"] for row in train + calibration + test}
    catalog = []
    with zipfile.ZipFile(args.archive) as archive:
        for path in sorted(archive.namelist()):
            if not path.startswith("data/output/scenarios/") or not path.endswith(".json"):
                continue
            raw = archive.read(path)
            scenario = json.loads(raw)
            category = scenario["task_category"]
            project = "locobench/" + scenario["id"].split("_" + category + "_", 1)[0]
            if project not in templates:
                continue
            text = scenario_prompt(scenario)
            catalog.append({
                "project": project, "original_id": scenario["id"], "category": category,
                "difficulty": scenario["difficulty"], "scenario": path, "title": scenario["title"],
                "scenario_sha256": hashlib.sha256(raw).hexdigest(),
                "problem_statement_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "task_template": hashlib.sha256(text[:4096].encode()).hexdigest(),
                "public_task_characters": len(text),
            })
        selected = select_tasks(
            catalog, training_projects=training_projects, held_out_projects=held_out_projects,
            used_task_templates=used, count=args.count, seed=args.seed,
        )
        tasks, integrity = [], {}
        for candidate in selected:
            reference = args.output_dir / "references" / (candidate["original_id"] + ".reference.json")
            task, raw = original_task(
                templates[candidate["project"]], candidate["scenario"], archive, reference,
            )
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            integrity[candidate["project"]] = source_integrity(task, archive)
            tasks.append(task)
        validate_population(tasks, archive)
    manifest = deepcopy(manifests[0])
    for name in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(name, None)
    manifest["inherited_source_inventory_summary"] = {
        "qualified_projects": manifest.pop("qualified_projects", None),
        "context_token_range": manifest.get("context_token_range"),
        "scope": "historical_source_inventory_not_new_task_population_qualification",
    }
    manifest.update(
        workloads=tasks, requested_workflows=len(tasks), max_tasks_per_project=1,
        selected_projects=[task["repo"].split("/", 1)[1] for task in tasks],
        selected_scenarios=[row["scenario"] for row in selected],
        selection_seed=args.seed, source_snapshots_reused=True,
        purpose="new_original_tasks_for_remaining_work_predictor_training_only",
        selection_policy="original_category_round_robin_then_seeded_order_with_training_project_holdout_exclusion",
        context_token_range=[
            min(task["frozen_repository_context"]["tokens"] for task in tasks),
            max(task["frozen_repository_context"]["tokens"] for task in tasks),
        ],
    )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an original training-task preparation input changed")
    write(args.output_dir / "manifest.json", manifest)
    write(args.output_dir / "population.json", {
        "selected": selected, "category_counts": dict(Counter(row["category"] for row in selected)),
        "original_split": split, "expanded_split": expanded,
        "existing_training_projects": len(training_projects),
        "held_out_projects_excluded": sorted(held_out_projects),
        "new_independent_project_count": 0, "new_original_task_count": len(selected),
        "task_source_and_tests_preserved": True,
        "new_task_work_or_JCT_known": False, "selection_used_runtime_or_prediction_errors": False,
        "formal_73_workload_arrivals_and_fixed18_unchanged": True,
        "source_integrity": integrity, "inputs_sha256": pins,
        "capture_ready": False, "fresh_root_child_tools_and_new_capture_binding_required": True,
        "GPU_capture_registered": False, "natural_training_traces_added": 0,
        "training_only": True,
    })
    print(json.dumps({
        "original_new_tasks": len(selected), "existing_training_projects": len(training_projects),
        "held_out_projects_excluded": len(held_out_projects), "new_independent_projects": 0,
        "categories": dict(Counter(row["category"] for row in selected)),
        "original_source_integrity_verified": True, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
