"""Keep the structured six-project tasks and declare the concurrent group API."""

from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import (
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/parallel_checkpoint_pilot6_20261030"
SOURCE = ROOT / "configs/workloads/locobench_structured_checkpoint_pilot6_20261030.json"
DECLARATION_SOURCE = ROOT / "experiments/reports/structured_checkpoint_pilot6_20261030/declaration.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_structured_checkpoint_pilot6_native_20261030.json"
MANIFEST = ROOT / "configs/workloads/locobench_parallel_checkpoint_pilot6_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_parallel_checkpoint_pilot6_native_20261030.json"


def parallel_manifest(source, declaration):
    rows = {row["instance_id"]: row for row in declaration["assignments"]}
    if (
        len(source["workloads"]) != 6 or len(rows) != 6
        or len({task["repo"] for task in source["workloads"]}) != 6
        or {task["instance_id"] for task in source["workloads"]} != rows.keys()
    ):
        raise ValueError("preserve the same six structured checkpoint projects and objectives")
    manifest, contract = deepcopy(source), deepcopy(declaration)
    assignments = []
    for task in manifest["workloads"]:
        previous = task["instance_id"]
        row = rows[previous]
        if row["project"] != task["repo"] or row["task_access"] != task["task_access"]:
            raise ValueError("parallel API must not change a project's task access")
        task["instance_id"] += "__parallel_api_v1"
        task["dataset_provenance"]["derived_task"].update(
            kind="parallel_api_source_grounded_checkpoint_v1",
            previous_derived_instance_id=previous, task_objective_changed=False,
        )
        assignments.append({
            **row, "instance_id": task["instance_id"], "previous_instance_id": previous,
            "problem_statement_sha256": fingerprint(task["problem_statement"]),
        })
    manifest.update(
        purpose="parallel_checkpoint_api_shape_pilot_not_performance_comparison",
        selection_policy=(
            "same six structured objectives, access and project/context slots; "
            "explicit concurrent group API; no policy-outcome selection"
        ),
    )
    contract.update(
        kind="parallel_checkpoint_api_shape_pilot",
        assignments=assignments,
        delegation_contract=delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        original_task_objectives_changed=False,
        root_child_soft_budgets_changed=False,
        parallel_group_execution="native_member_tasks_concurrently_then_real_all_join",
        model_tokens_or_tool_calls_rewritten=False,
        fixed_delegation_DAG_added=False,
        source_shape_qualified=False, serving_pressure_qualified=False,
        formal_comparison_ready=False,
    )
    return manifest, contract


def main():
    outputs = MANIFEST, CONFIG, HERE / "declaration.json", HERE / "preparation.json"
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve all declared parallel checkpoint candidates")
    manifest, contract = parallel_manifest(
        json.loads(SOURCE.read_bytes()), json.loads(DECLARATION_SOURCE.read_bytes()),
    )
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose="parallel_checkpoint_api_shape_pilot_not_performance_comparison",
        source_collection_contract_sha256=fingerprint(contract),
    )
    configuration["common_environment"]["WORKLOAD_MANIFEST"] = str(MANIFEST.relative_to(ROOT))
    configuration["training_protocol"]["purpose"] = "natural_parallel_checkpoint_api_shape_pilot"
    for path, value in zip(outputs[:3], (manifest, configuration, contract), strict=True):
        write(path, value)
    write(outputs[3], {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                SOURCE, DECLARATION_SOURCE, CONFIG_SOURCE, Path(__file__),
                ROOT / "beliefkv/runtime/checkpoint_subagents.py",
                ROOT / "beliefkv/runtime/deepagents_adapter.py",
                ROOT / "beliefkv/experiments/deepagents_swebench.py",
                ROOT / "beliefkv/experiments/root_delegation_requirement.py",
                ROOT / "beliefkv/experiments/delegation_policy.py",
            )
        },
        "outputs_sha256": {str(path): sha256_file(path) for path in outputs[:3]},
        "task_objectives_access_context_unchanged": True,
        "original_sources_tests_environments_preserved": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print({"projects": 6, "parallel_api_version": 1, "GPU_action": False})


if __name__ == "__main__":
    main()
