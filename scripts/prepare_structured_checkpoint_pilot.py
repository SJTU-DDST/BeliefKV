"""Keep the six declared task objectives; expose explicit checkpoint parameters."""

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


HERE = ROOT / "experiments/reports/structured_checkpoint_pilot6_20261030"
SOURCE = ROOT / "configs/workloads/locobench_checkpoint_pilot6_20261030.json"
OLD_DECLARATION = ROOT / "experiments/reports/checkpoint_pilot6_20261030/declaration.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_checkpoint_pilot6_native_20261030.json"
MANIFEST = ROOT / "configs/workloads/locobench_structured_checkpoint_pilot6_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_structured_checkpoint_pilot6_native_20261030.json"


def structured_manifest(source, declaration):
    rows = {row["instance_id"]: row for row in declaration["assignments"]}
    tasks = source["workloads"]
    if (
        len(tasks) != 6 or len({task["repo"] for task in tasks}) != 6
        or {task["instance_id"] for task in tasks} != rows.keys()
    ):
        raise ValueError("preserve all six project-disjoint source-grounded tasks")
    result, contract = deepcopy(source), deepcopy(declaration)
    assignments = []
    for task in result["workloads"]:
        before = rows[task["instance_id"]]
        if before["project"] != task["repo"]:
            raise ValueError("structured checkpoint changes its original project")
        previous = task["instance_id"]
        task["instance_id"] += "__structured_v3"
        task["task_access"] = (
            "read_only" if before["scope_hypothesis"] == "investigation" else "read_write"
        )
        task["dataset_provenance"]["derived_task"].update(
            kind="structured_source_grounded_checkpoint_v3",
            previous_derived_instance_id=previous,
            task_objective_changed=False,
        )
        assignments.append({
            **before, "instance_id": task["instance_id"], "previous_instance_id": previous,
            "task_access": task["task_access"],
            "problem_statement_sha256": fingerprint(task["problem_statement"]),
        })
    result.update(
        purpose="structured_checkpoint_shape_pilot_not_performance_comparison",
        selection_policy=(
            "all same six source-grounded objectives and project/context/scope slots; "
            "explicit task access and required task fields; no policy-outcome selection"
        ),
    )
    contract.update(
        kind="structured_source_grounded_checkpoint_shape_pilot",
        assignments=assignments,
        delegation_contract=delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        original_task_objectives_changed=False,
        tool_schema_required_fields=[
            "description", "subagent_type", "known_paths", "existing_evidence",
            "finish_condition", "write_paths",
        ],
        supporting_edit_write_paths_max=2,
        no_model_call_or_output_limit_added=True,
        source_shape_qualified=False, serving_pressure_qualified=False,
        formal_comparison_ready=False,
    )
    return result, contract


def main():
    outputs = MANIFEST, CONFIG, HERE / "declaration.json", HERE / "preparation.json"
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve previous structured checkpoint candidates")
    manifest, contract = structured_manifest(
        json.loads(SOURCE.read_bytes()), json.loads(OLD_DECLARATION.read_bytes()),
    )
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose="structured_checkpoint_shape_pilot_not_performance_comparison",
        source_collection_contract_sha256=fingerprint(contract),
    )
    configuration["common_environment"]["WORKLOAD_MANIFEST"] = str(MANIFEST.relative_to(ROOT))
    configuration["training_protocol"]["purpose"] = "natural_structured_checkpoint_shape_pilot"
    for path, value in zip(outputs[:3], (manifest, configuration, contract), strict=True):
        write(path, value)
    write(outputs[3], {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (
                SOURCE, OLD_DECLARATION, CONFIG_SOURCE, Path(__file__),
                ROOT / "beliefkv/runtime/checkpoint_subagents.py",
                ROOT / "beliefkv/runtime/deepagents_adapter.py",
                ROOT / "beliefkv/experiments/deepagents_swebench.py",
                ROOT / "beliefkv/experiments/delegation_policy.py",
            )
        },
        "outputs_sha256": {str(path): sha256_file(path) for path in outputs[:3]},
        "task_objectives_unchanged": True, "root_child_budgets_unchanged": True,
        "original_sources_tests_environments_preserved": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print({"projects": 6, "protocol_version": 3, "GPU_action": False})


if __name__ == "__main__":
    main()
