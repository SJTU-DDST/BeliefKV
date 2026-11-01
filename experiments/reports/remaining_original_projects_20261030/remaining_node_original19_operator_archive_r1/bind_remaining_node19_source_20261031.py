"""Freeze the nineteen complete original tasks and their separately scoped tools."""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-original19-source-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
TOOLS = REPORTS / "remaining_node_original19_tools_r2"
HERE = REPORTS / "remaining_node_original19_source_r1"
FROZEN = ROOT / "experiments/reports/remaining_original_projects_20261030/remaining_node_original19_source_r1"
BASE = REPORTS / "background_extension_source25_toolchain_r1/native_qualified.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts import run_causal_comparison as causal
from scripts.prepare_original_task_mix import (
    qualified_capture_configuration, source_integrity, validate_population,
)
from scripts.run_causal_source_capture import declared_workload_inputs
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or FROZEN.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve preparations and use nice19 CPU-only execution")
    input_path, qualified_path = TOOLS / "input_manifest.json", TOOLS / "qualified_manifest.json"
    original, qualified = (causal.load(path) for path in (input_path, qualified_path))
    proof_path = TOOLS / "toolchain_live_root_child.json"
    proof = causal.load(proof_path)
    if proof["tool_execution_passed"] is not True or (
        proof["attempted_projects"] != 19 or proof["qualified_projects"] != 19
        or proof["rejected_projects"] or len(qualified["workloads"]) != 19
        or any(row["dataset_provenance"]["task_category"] != "code_comprehension"
               or not proof["projects"][row["source_repo"]]["original_source_read_verified"]
               or not proof["projects"][row["source_repo"]]["private_TypeScript_compilation_and_execution_verified"]
               for row in qualified["workloads"])
    ):
        raise ValueError("all nineteen original tasks need actual root/child tool evidence")
    for name, digest in proof["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("tool evidence changed: " + name)
    external = declared_workload_inputs(qualified_path, require_tool_execution=True)
    with zipfile.ZipFile(qualified["archive"]) as archive:
        validate_population(qualified["workloads"], archive)
        integrity = {row["repo"]: source_integrity(row, archive) for row in qualified["workloads"]}
    if integrity != proof["original_source_integrity"]:
        raise ValueError("original source or context changed")
    declaration = {
        "kind": "incremental_complete_original_Node19_toolchain_source",
        "workflows": 19, "projects": sorted(integrity), "client_root_concurrency": 12,
        "native_running_limit": 24,
        "complete_original_task_source_tests_and_context_preserved": True,
        "actual_root_child_tools_and_original_source_reads_verified": True,
        "actual_private_TypeScript_compilation_and_execution_verified": True,
        "tool_qualification_executions": proof["qualification_executions"],
        "application_dependencies_prepared": False,
        "application_execution_or_project_test_success_claimed": False,
        "application_defects_and_task_correctness_remain_agent_responsibility": True,
        "must_audit_real_tool_failures_and_task_progress_before_trace_use": True,
        "child_warning_target_calls": [4, 8], "root_warning_target_calls": [8, 16],
        "no_fixed_DAG_or_hard_call_cap": True, "natural_tails_required": True,
        "normal_tool_times_retained": True, "formal_comparison_population": False,
        "short_workflow_and_JOIN_shape_not_yet_proven": True,
        "capture_GPU_qualification_pending": True,
        "population_specific_CPU_binding_pending": True,
        "GPU_action": False, "capture_ready": False,
    }
    FROZEN.mkdir(parents=True, exist_ok=False)
    frozen_manifest = FROZEN / "qualified_manifest.json"
    write(frozen_manifest, qualified)
    config = deepcopy(causal.load(BASE))
    config["common_environment"].update(
        ROOT_COUNT="19", CLIENT_ROOT_CONCURRENCY="12", WORKFLOW_ARRIVAL_SCHEDULE="",
    )
    config["training_protocol"].update(
        extra_tasks=19, client_root_concurrency=12, complete_original_tasks_only=True,
        development_validation="incremental_complete_original_Node19_toolchain",
    )
    config = qualified_capture_configuration(original, qualified, config, frozen_manifest)
    config.update(
        purpose=declaration["kind"], source_collection_contract_sha256=fingerprint(declaration),
    )
    for directory in (HERE, FROZEN):
        write(directory / "declaration.json", declaration)
        write(directory / "native_qualified.json", config)
    write(HERE / "binding.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            *external, input_path, qualified_path, proof_path, BASE, Path(__file__),
            REPORTS / "remaining_node_original19_preparation_r1/preparation.json",
            REPORTS / "remaining_node_families_inventory_r1/inventory.json",
        )},
        "configuration": str(FROZEN / "native_qualified.json"),
        "configuration_sha256": sha256_file(FROZEN / "native_qualified.json"),
        "manifest": str(frozen_manifest), "manifest_sha256": sha256_file(frozen_manifest),
        "original_source_integrity": integrity,
        "GPU_action": False, "performance_accepted": False, "capture_ready": False,
    })
    print({
        "projects": 19, "source_window": 12, "native_running_limit": 24,
        "original_source_files": sum(row["files"] for row in integrity.values()),
        "frozen_manifest": str(frozen_manifest), "GPU_action": False,
    }, flush=True)


if __name__ == "__main__":
    main()
