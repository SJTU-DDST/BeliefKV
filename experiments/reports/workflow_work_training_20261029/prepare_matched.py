"""Collect the first six original tasks again under the current execution protocol."""

from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_work_training_20261029.prepare import (
    BASE_CONFIG, BASE_WORKLOAD, training_configuration,
)
from scripts.train_workflow_work import write

WORKLOAD = ROOT / "configs/workloads/locobench_workflow_work_matched6_20261029.json"
CONFIGURATION = ROOT / "configs/migration/workflow_work_matched6_native_20261029.json"


def matched_manifest(base, *, count=6):
    if type(count) is not int or not 1 <= count <= len(base["workloads"]):
        raise ValueError("matched training population is outside the qualified project pool")
    result = deepcopy(base)
    selected = result["workloads"][:count]
    projects = [row["repo"].split("/", 1)[1] for row in selected]
    if len(set(projects)) != count:
        raise ValueError("matched training collection requires different projects")
    result.update(
        requested_workflows=count, max_tasks_per_project=1, workloads=selected,
        selected_projects=projects,
        selected_scenarios=[row["dataset_provenance"]["original_scenario"] for row in selected],
        candidates=[row for row in result["candidates"] if row["project_id"] in projects],
        selection_policy=f"first {count} existing seeded projects and original tasks; no duration, outcome or prediction selection",
        purpose="remaining_work_predictor_training_only_not_a_performance_baseline",
        training_execution="independent_current_protocol_repeats; unchanged task and project snapshots",
    )
    return result


def main():
    base = json.loads(BASE_WORKLOAD.read_bytes())
    manifest = matched_manifest(base)
    configuration = training_configuration(
        json.loads(BASE_CONFIG.read_bytes()), count=6, workload=WORKLOAD,
    )
    configuration["training_protocol"].update({
        "same_original_tasks_as_current_evaluation": True,
        "independent_execution_required": True,
        "previous_evaluation_trace_used_as_training_labels": False,
        "current_evaluation_workload_changed": False,
    })
    write(WORKLOAD, manifest)
    write(CONFIGURATION, configuration)
    write(Path(__file__).resolve().parent / "matched6_preparation.json", {
        "base_workload_sha256": sha256_file(BASE_WORKLOAD),
        "workload_sha256": sha256_file(WORKLOAD),
        "configuration_sha256": sha256_file(CONFIGURATION),
        "tasks": [row["instance_id"] for row in manifest["workloads"]],
        "projects": manifest["selected_projects"],
        "task_source_tests_environment_changed": False, "GPU_experiment_started": False,
    })
    print({"workload": str(WORKLOAD), "configuration": str(CONFIGURATION)})


if __name__ == "__main__":
    main()
