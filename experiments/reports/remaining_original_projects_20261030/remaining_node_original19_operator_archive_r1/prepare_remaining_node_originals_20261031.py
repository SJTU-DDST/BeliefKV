"""Materialize complete original tasks for the nineteen remaining Node families."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-device-plan-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
INVENTORY = REPORTS / "remaining_node_families_inventory_r1/inventory.json"
HERE = REPORTS / "remaining_node_original19_preparation_r1"
SOURCES = MAIN / "workloads/sources/locobench_remaining_node_20261031"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_original_task_mix import (
    POOL, TOKENIZER_SOURCE, materialize_project, original_task, source_integrity, validate_population,
)
from scripts.prepare_original_node_projects import node_preflight
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve preparation and use nice19 CPU-only execution")
    inventory, pool, tokenizer_info = (
        json.loads(path.read_bytes()) for path in (INVENTORY, POOL, TOKENIZER_SOURCE)
    )
    selected = inventory["selected_candidates"]
    if any(sha256_file(Path(name)) != digest for name, digest in inventory["inputs_sha256"].items()):
        raise ValueError("inventory inputs changed")
    if len(selected) != 19 or len({row["family"] for row in selected}) != 19 or any(
        row["family"] in inventory["already_captured_project_families"]
        or row["project"] in inventory["already_captured_projects"]
        or row["incidental_python_syntax_diagnostics_not_repaired"]
        or not row["fits_protocol_headroom"]
        for row in selected
    ):
        raise ValueError("the explicit original family/context population changed")
    if sha256_file(Path(pool["archive"])) != inventory["archive_sha256"]:
        raise ValueError("original archive changed")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_info["tokenizer"], local_files_only=True)
    HERE.mkdir(parents=True, exist_ok=False)
    tasks, assignments = [], []
    with zipfile.ZipFile(pool["archive"]) as archive:
        for row in selected:
            if hashlib.sha256(archive.read(row["metadata_path"])).hexdigest() != row["metadata_sha256"]:
                raise ValueError("original metadata changed")
            template = materialize_project(
                archive, row["project"], row, HERE / "contexts", SOURCES, pool, tokenizer,
            )
            reference = HERE / "references" / (row["original_id"] + ".reference.json")
            task, raw = original_task(template, row["original_scenario"], archive, reference)
            if (
                hashlib.sha256(raw).hexdigest() != row["original_scenario_sha256"]
                or hashlib.sha256(task["problem_statement"].encode()).hexdigest() != row["original_problem_sha256"]
                or task["frozen_repository_context"]["tokens"] != row["complete_context_tokens"]
            ):
                raise ValueError("complete public task or current-tokenizer context changed")
            task["docker_image"] = "beliefkv-typescript-tools:20261031"
            task["preflight_command"] += (
                " && " + node_preflight(row["packages"])
                + " && tsc --version && ts-node --version"
            )
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            integrity = source_integrity(task, archive)
            tasks.append(task)
            assignments.append({
                "project": task["repo"], "instance_id": task["instance_id"],
                "language": row["language"], "family": row["family"],
                "original_scenario": row["original_scenario"],
                "original_difficulty": row["original_difficulty"],
                "original_title": row["original_title"],
                "context_tokens": row["complete_context_tokens"],
                "source_integrity": integrity,
                "original_task_requirements_unchanged": True,
            })
        validate_population(tasks, archive)
    manifest = deepcopy(pool)
    for name in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(name, None)
    manifest.update(
        workloads=tasks, requested_workflows=len(tasks), max_tasks_per_project=1,
        selected_projects=[row["project"] for row in selected],
        selected_scenarios=[row["original_scenario"] for row in selected],
        tokenizer=tokenizer_info["tokenizer"], tokenizer_sha256=tokenizer_info["tokenizer_sha256"],
        tokenizer_config_sha256=tokenizer_info["tokenizer_config_sha256"],
        purpose="complete_original_tasks_from_remaining_Node_families",
        selection_policy=inventory["selection"],
        runtime_image_pin_and_live_root_child_tools_pending=True,
    )
    write(HERE / "manifest.json", manifest)
    write(HERE / "preparation.json", {
        "kind": manifest["purpose"], "assignments": assignments,
        "projects": len(tasks),
        "language_counts": dict(Counter(row["language"] for row in selected)),
        "difficulty_counts": dict(Counter(row["original_difficulty"] for row in selected)),
        "existing_original_projects": inventory["already_captured_projects"],
        "existing_numbered_families": inventory["already_captured_project_families"],
        "original_source_tests_and_tasks_preserved": True,
        "short_workflow_and_JOIN_shape_require_natural_capture": True,
        "runtime_image_pin_and_live_root_child_tools_pending": True,
        "application_dependencies_and_project_test_success_not_claimed": True,
        "capture_ready": False, "GPU_action": False, "GPU_followup_registered": False,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            INVENTORY, POOL, TOKENIZER_SOURCE, Path(__file__),
            ROOT / "scripts/prepare_original_task_mix.py",
            ROOT / "scripts/prepare_original_node_projects.py",
        )},
    })
    print(json.dumps({
        "projects": len(tasks), "manifest": str(HERE / "manifest.json"),
        "original_source_files": sum(row["source_integrity"]["files"] for row in assignments),
        "capture_ready": False, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
