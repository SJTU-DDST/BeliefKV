"""Bind original cached SWE-rebench backgrounds to the native capture protocol."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.deepagents_swebench import load_workload_bundle
from beliefkv.experiments.original_swe_rebench import KIND, bind_task, source_dataset
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.run_causal_source_capture import declared_workload_inputs
from scripts.train_workflow_work import write


def configuration(original, manifest_path, manifest):
    count = manifest.get("requested_workflows")
    source_dataset(manifest)
    environment = manifest.get("sandbox_test_env_path")
    if (
        original.get("source_profile") != "nested_oneshot"
        or original.get("order") != ["native_matched"]
        or original["common_environment"].get("MODEL_CACHE_LAYOUT") != "full_only"
        or type(count) is not int or not 1 <= count <= 156
        or not isinstance(environment, str) or not environment.startswith("/")
    ):
        raise ValueError("original backgrounds require the existing FULL-only natural native protocol")
    result = deepcopy(original)
    result["common_environment"].update(
        ROOT_COUNT=str(count), CLIENT_ROOT_CONCURRENCY=str(count),
        WORKFLOW_ARRIVAL_SCHEDULE="", WORKLOAD_MANIFEST=str(manifest_path.resolve()),
        SANDBOX_TEST_ENV=environment,
    )
    result.update(
        purpose="additional_complete_original_SWE_rebench_background_source",
        source_collection_contract_sha256=fingerprint({
            "kind": KIND, "manifest_sha256": sha256_file(manifest_path),
            "original_workflows": count, "source_dataset": manifest["source_dataset"],
            "native_model_and_soft_delegation_protocol_retained": True,
            "natural_returns_and_original_tools_required": True,
        }),
        performance_accepted=False,
    )
    if "training_protocol" in result:
        result["training_protocol"].update(
            purpose=result["purpose"], extra_tasks=count, client_root_concurrency=count,
            complete_original_tasks_only=True, predictor_training_labels_added=False,
            existing_fixed18_measured_members_unchanged=True,
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-manifest", type=Path, required=True)
    parser.add_argument("--base-configuration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve earlier source declarations and hide CUDA")
    original = json.loads(args.qualified_manifest.read_bytes())
    tasks = original["workloads"]
    if (
        not tasks or len({row["repo"] for row in tasks}) != len(tasks)
        or original.get("max_tasks_per_project") != 1
        or original.get("fresh_tool_qualification_pending") is not False
    ):
        raise ValueError("require complete original projects with actual root/child tool evidence")
    inventories = {
        (row["dataset_provenance"]["public_inventory"], row["dataset_provenance"]["public_inventory_sha256"])
        for row in tasks
    }
    if len(inventories) != 1:
        raise ValueError("one pinned public inventory must declare the new original population")
    inventory, digest = inventories.pop()
    dataset = {"kind": KIND, "path": inventory, "sha256": digest}
    manifest = deepcopy(original)
    manifest["source_dataset"] = dataset
    source_dataset(manifest)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest["workloads"] = [
        bind_task(row, args.output_dir / "originals" / (row["instance_id"] + ".json"), dataset)
        for row in tasks
    ]
    manifest["natural_source_capture_pending"] = True
    manifest_path = args.output_dir / "qualified_manifest.json"
    write(manifest_path, manifest)
    inputs = declared_workload_inputs(manifest_path, require_tool_execution=True)
    load_workload_bundle(manifest_path)
    spec = configuration(json.loads(args.base_configuration.read_bytes()), manifest_path, manifest)
    configuration_path = args.output_dir / "native_qualified.json"
    write(configuration_path, spec)
    write(args.output_dir / "binding.json", {
        "kind": "explicit_original_SWE_rebench_source_capture_binding",
        "projects": len(tasks), "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "configuration": str(configuration_path.resolve()),
        "configuration_sha256": sha256_file(configuration_path),
        "source_dataset": dataset, "all_original_public_issues_and_clean_git_trees_verified": True,
        "actual_root_child_tools_bound": True,
        "original_tasks_sources_images_and_tools_not_rewritten": True,
        "native_model_sampling_and_soft_delegation_budgets_retained": True,
        "predictor_refit_or_future_work_labels_added": False,
        "current_fixed18_measurement_unchanged": True,
        "natural_capture_and_GPU_qualification_pending": True,
        "GPU_action": False, "GPU_registered": False, "performance_accepted": False,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.qualified_manifest, args.base_configuration, *inputs, Path(__file__),
            ROOT / "beliefkv/experiments/original_swe_rebench.py",
            ROOT / "scripts/run_causal_source_capture.py",
            ROOT / "scripts/run_qwen35_native_regime_probe.sh",
        )},
    })
    print(json.dumps({"original_projects": len(tasks), "configuration": str(configuration_path), "GPU_action": False}))


if __name__ == "__main__":
    main()
