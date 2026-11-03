"""Bind qualified original training tasks to the unchanged natural native protocol."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_original_task_mix import qualified_capture_configuration
from scripts.run_causal_source_capture import declared_workload_inputs
from scripts.train_workflow_work import write


def configuration(base, original, qualified, population, qualified_path):
    selected = population["selected"]
    count = len(selected)
    tasks = qualified["workloads"]
    held_out = set(population["held_out_projects_excluded"])
    expected = {row["project"]: "locobench__" + row["original_id"] for row in selected}
    common = base["common_environment"]
    if (
        base["source_profile"] != "nested_oneshot" or base["order"] != ["native_matched"]
        or population.get("training_only") is not True
        or population.get("new_independent_project_count") != 0
        or len(expected) != count or len(tasks) != count
        or {row["repo"]: row["instance_id"] for row in tasks} != expected
        or held_out & set(expected)
        or any(common.get(name) != value for name, value in {
            "MODEL_CACHE_LAYOUT": "full_only", "MAX_RUNNING_REQUESTS": "24",
            "CHUNKED_PREFILL_SIZE": "2048", "ENABLE_MIXED_CHUNK": "0",
            "FANOUT_PROFILE": "native_in_graph_parallel_workstreams",
            "CHILD_WORK_BUDGET_WARNING_CALLS": "4", "CHILD_WORK_BUDGET_TARGET_CALLS": "8",
            "ROOT_WORK_BUDGET_WARNING_CALLS": "8", "ROOT_WORK_BUDGET_TARGET_CALLS": "16",
        }.items())
    ):
        raise ValueError("training capture must preserve whole-task holdouts and the existing native protocol")
    if any(before["docker_image"] != after["docker_image"] for before, after in zip(
        original["workloads"], tasks, strict=True,
    )):
        raise ValueError("training capture cannot replace an original project's tool image")
    result = qualified_capture_configuration(original, qualified, base, qualified_path)
    result["common_environment"].update(ROOT_COUNT=str(count), CLIENT_ROOT_CONCURRENCY=str(count))
    result["purpose"] = "original_task_diversity_training_only_native_capture"
    result["training_protocol"].update(
        extra_tasks=count, client_root_concurrency=count, new_independent_projects=0,
        training_only=True, preserve_existing_18_workflow_evaluation_and_all_previous_traces=True,
        preserve_formal_original73_workload=True,
        development_validation="different_original_tasks_within_training_projects_only",
    )
    result["source_collection_contract_sha256"] = fingerprint({
        "population": population, "qualified_manifest": qualified,
        "training_only": True, "native_sampling_and_normal_tools": True,
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-config", "original-manifest", "qualified-manifest", "population", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve original training-capture bindings")
    paths = (args.base_config, args.original_manifest, args.qualified_manifest, args.population)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    pins.update({str(path): sha256_file(path) for path in (
        Path(__file__).resolve(), ROOT / "scripts/prepare_original_task_mix.py",
        ROOT / "scripts/run_causal_source_capture.py",
    )})
    base, original, qualified, population = (json.loads(path.read_bytes()) for path in paths)
    proof = qualified["tool_environment_qualification"]
    if (
        Path(proof["base_manifest"]).resolve() != args.original_manifest.resolve()
        or proof["base_manifest_sha256"] != pins[str(args.original_manifest.resolve())]
    ):
        raise ValueError("tool checks belong to a different original task population")
    bound = declared_workload_inputs(args.qualified_manifest.resolve(), require_tool_execution=True)
    pins.update({str(path.resolve()): sha256_file(path) for path in bound})
    result = configuration(base, original, qualified, population, args.qualified_manifest.resolve())
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a training-capture input changed during binding")
    output = args.output_dir / "native_qualified.json"
    write(output, result)
    write(args.output_dir / "binding.json", {
        "configuration": str(output.resolve()), "configuration_sha256": sha256_file(output),
        "inputs_sha256": pins, "bound_original_projects": len(qualified["workloads"]),
        "training_only": True, "new_independent_projects": 0,
        "original_tasks_context_source_tests_and_images_preserved": True,
        "actual_root_child_tools_passed": True, "task_test_success_required": False,
        "formal_original73_arrivals_and_fixed18_unchanged": True,
        "clean_native_operator_and_current_sampling_startup_binding_pending": True,
        "GPU_capture_registered": False, "natural_training_traces_added": 0,
        "GPU_action": False,
    })
    print(json.dumps({
        "original_training_tasks": len(qualified["workloads"]), "tools_and_images_bound": True,
        "native_configuration_prepared": True, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
