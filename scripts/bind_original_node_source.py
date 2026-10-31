"""Bind dependency-qualified original projects to an incremental native capture."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_original_task_mix import (
    qualified_capture_configuration, source_integrity, validate_population,
)
from scripts.run_causal_source_capture import declared_workload_inputs
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependency-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve every source declaration")
    dependencies, here = args.dependency_dir.resolve(), args.output_dir.resolve()
    base_path, qualified_path = (
        dependencies / "pending_image_manifest.json", dependencies / "qualified_manifest.json"
    )
    original, qualified = (json.loads(path.read_bytes()) for path in (base_path, qualified_path))
    proof_path = dependencies / "provisioning.json"
    proof = json.loads(proof_path.read_bytes())
    count = len(qualified["workloads"])
    projects = {row["repo"].split("/", 1)[1] for row in qualified["workloads"]}
    if (
        not count or len(projects) != count
        or proof["dependency_installed_projects"] != count
        or proof["dependency_only"] is not True
        or proof["root_project_lifecycle_deferred_to_agent"] is not True
        or any(proof["projects"][name]["installed"] is not True for name in projects)
    ):
        raise ValueError("require real dependencies without repairing task code")
    inputs = declared_workload_inputs(qualified_path, require_tool_execution=True)
    with zipfile.ZipFile(qualified["archive"]) as archive:
        validate_population(qualified["workloads"], archive)
        integrity = {
            task["repo"]: source_integrity(task, archive) for task in qualified["workloads"]
        }
    base_config = ROOT / (
        "experiments/reports/remaining_original_projects_20261030/preparation_r1/native_qualified.json"
    )
    configuration = deepcopy(json.loads(base_config.read_bytes()))
    configuration["common_environment"].update(
        ROOT_COUNT=str(count), CLIENT_ROOT_CONCURRENCY=str(count), WORKFLOW_ARRIVAL_SCHEDULE="",
    )
    configuration["training_protocol"].update(
        extra_tasks=count, client_root_concurrency=count,
        same_model_and_prompt_budgets_as_current_workload=True,
        complete_original_tasks_only=True,
        development_validation="incremental_complete_original_Node_projects",
    )
    configuration = qualified_capture_configuration(
        original, qualified, configuration, qualified_path,
    )
    declaration = {
        "kind": "incremental_complete_original_Node_source",
        "projects": sorted(projects), "workflows": count,
        "source_integrity": integrity,
        "child_warning_target_calls": [4, 8], "root_warning_target_calls": [8, 16],
        "complete_original_task_source_tests_and_context_preserved": True,
        "no_fixed_DAG_or_hard_call_cap": True, "natural_tails_required": True,
        "normal_tool_times_retained": True,
        "project_install_hooks_and_test_results_remain_agent_responsibility": True,
        "existing_complete_original_project_sources_retained": True,
        "formal_comparison_population": False, "short_workflow_shape_not_yet_proven": True,
        "capture_GPU_qualification_pending": True,
    }
    configuration.update(
        purpose=declaration["kind"], source_collection_contract_sha256=fingerprint(declaration),
    )
    write(here / "declaration.json", declaration)
    write(here / "native_qualified.json", configuration)
    write(here / "binding.json", {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (*inputs, base_path, proof_path, base_config, Path(__file__))
        },
        "configuration": str(here / "native_qualified.json"),
        "configuration_sha256": sha256_file(here / "native_qualified.json"),
        "original_source_integrity": integrity,
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"projects": count, "configuration": str(here / "native_qualified.json"),
                      "GPU_action": False}))


if __name__ == "__main__":
    main()
