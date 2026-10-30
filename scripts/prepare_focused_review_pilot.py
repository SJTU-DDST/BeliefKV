"""Declare narrower source reviews and root-owned edits before new capture."""

import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import (
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.runtime.checkpoint_repository_tools import FOCUSED_TOOL_PROFILE, FOCUSED_READ_LINES
from scripts.prepare_checkpoint_workstream_pilot import source_paths
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/focused_review_pilot6_20261030"
SOURCE = ROOT / "configs/workloads/locobench_parallel_checkpoint_pilot6_20261030.json"
DECLARATION_SOURCE = ROOT / "experiments/reports/parallel_checkpoint_pilot6_20261030/declaration.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_parallel_checkpoint_pilot6_native_20261030.json"
MANIFEST = ROOT / "configs/workloads/locobench_focused_review_pilot6_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_focused_review_pilot6_native_20261030.json"

REFINEMENTS = {
    "locobench/python_game_simulation_medium_033": {
        "path": "physage_academy/src/physage_academy/engine/scene.py",
        "symbols": ["Scene._serialize_snapshot", "Scene.from_snapshot"],
        "deliverable": (
            "Review only the snapshot format shared by Scene._serialize_snapshot "
            "and Scene.from_snapshot in the named source file. Establish the "
            "producer's keys and entity representation and the consumer's "
            "requirements for scene/entity identifiers and values. Root compares "
            "the two contracts and returns a short supported compatibility "
            "finding, or the exact unproven condition. Independent function-level "
            "checks can be delegated together. Do not audit network transport, "
            "object pooling, event wiring or the complete multiplayer symptom. "
            "No patch or running game is requested; do not repair unrelated code."
        ),
    },
    "locobench/python_mobile_social_medium_022": {
        "path": "gatherproof_backend/src/adapters/social_sharing.py",
        "symbols": [
            "SharePayload", "TwitterAdapter._build_status", "SlackAdapter._build_blocks",
        ],
        "deliverable": (
            "Review only how the existing TwitterAdapter._build_status and "
            "SlackAdapter._build_blocks represent location data from SharePayload. "
            "Root owns a small comparison of the accepted payload field and the "
            "two generated representations, supported by source locations or one "
            "focused offline check where feasible. Independent formatter checks "
            "can run together. Return that comparison and any concrete uncertainty. "
            "Do not investigate incident handlers, service wiring, absent platform "
            "adapters or live API behavior. No patch, integration audit or complete "
            "project repair is requested."
        ),
    },
}


def symbol_locations(task, path, symbols):
    blob = subprocess.run(
        ["git", "-C", task["source_repo"], "show", task["base_commit"] + ":" + path],
        check=True, capture_output=True, timeout=10.,
    ).stdout
    tree = ast.parse(blob, filename=path)
    result = []
    for symbol in symbols:
        node = tree
        for component in symbol.split("."):
            matches = [item for item in node.body if getattr(item, "name", None) == component]
            if len(matches) != 1:
                raise ValueError("known symbol is absent or ambiguous in pristine source: " + symbol)
            node = matches[0]
        result.append({
            "path": path, "qualified_name": symbol,
            "first_line": node.lineno, "last_line": node.end_lineno,
            "navigation_only_not_a_behavioral_answer": True,
        })
    return result


def focused_manifest(source, declaration):
    previous = {row["instance_id"]: row for row in declaration["assignments"]}
    if (
        len(source["workloads"]) != 6 or len(previous) != 6
        or len({task["repo"] for task in source["workloads"]}) != 6
        or {task["instance_id"] for task in source["workloads"]} != previous.keys()
        or not REFINEMENTS.keys() <= {task["repo"] for task in source["workloads"]}
    ):
        raise ValueError("keep all six project-disjoint checkpoint slots")
    manifest, contract = deepcopy(source), deepcopy(declaration)
    assignments = []
    for task in manifest["workloads"]:
        old_id = task["instance_id"]
        row = previous[old_id]
        if row["project"] != task["repo"] or row["task_access"] != task["task_access"]:
            raise ValueError("root task access and original project must stay explicit")
        task["instance_id"] = row["original_instance_id"] + "__focused_review_v1"
        task["child_task_access"] = "read_only"
        task["repository_tool_profile"] = FOCUSED_TOOL_PROFILE
        refinement = REFINEMENTS.get(task["repo"])
        symbols = []
        if refinement is not None:
            if row["scope_hypothesis"] != "investigation" or task["task_access"] != "read_only":
                raise ValueError("only the two declared investigations may be narrowed")
            paths = source_paths(task, [refinement["path"]])
            symbols = symbol_locations(task, refinement["path"], refinement["symbols"])
            task["problem_statement"] = (
                "Requested deliverable for this derived task:\n" + refinement["deliverable"]
                + "\n\nKnown source locations, relative to /workspace; navigation only:\n"
                + "\n".join(
                    f"- {item['path']}:{item['first_line']} {item['qualified_name']}"
                    for item in symbols
                )
                + "\nInspect the named live source directly, share relevant evidence with "
                "children, and keep the final synthesis in the root. Choose actual "
                "assignments, fanout and follow-ups; no fixed DAG or round count is given."
            )
            row = {**row, "known_starting_paths": paths, "requested_deliverable": refinement["deliverable"]}
        task["dataset_provenance"]["derived_task"].update(
            kind="focused_read_only_child_checkpoint_v1",
            previous_derived_instance_id=old_id,
            task_objective_changed=refinement is not None,
            requested_deliverable=row["requested_deliverable"],
            known_starting_paths=row["known_starting_paths"],
            known_source_symbols=symbols,
            future_trace_or_reference_solution_used=False,
        )
        assignments.append({
            **row, "instance_id": task["instance_id"], "previous_instance_id": old_id,
            "task_objective_changed": refinement is not None,
            "child_task_access": "read_only", "repository_tool_profile": FOCUSED_TOOL_PROFILE,
            "known_source_symbols": symbols,
            "problem_statement_sha256": fingerprint(task["problem_statement"]),
        })
    manifest.update(
        purpose="focused_review_checkpoint_shape_pilot_not_performance_comparison",
        selection_policy=(
            "same six project/context slots; narrow two broad investigations to "
            "actual function contracts; root owns edits and reports; no scheduler-win selection"
        ),
    )
    contract.update(
        kind="focused_read_only_child_checkpoint_shape_pilot",
        assignments=assignments,
        delegation_contract=delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        objective_change_count=2,
        original_task_objectives_changed=True,
        unchanged_medium_and_full_objectives=4,
        child_task_access="read_only",
        supporting_edit_write_paths_max=0,
        child_read_only_is_tool_contract_not_OS_write_isolation=True,
        repository_tool_profile=FOCUSED_TOOL_PROFILE,
        repository_read_default_lines=FOCUSED_READ_LINES,
        real_tool_times_retained=True,
        root_child_soft_budgets_changed=False,
        fixed_delegation_DAG_added=False,
        source_shape_qualified=False, serving_pressure_qualified=False,
        formal_comparison_ready=False,
    )
    return manifest, contract


def main():
    outputs = MANIFEST, CONFIG, HERE / "declaration.json", HERE / "preparation.json"
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve earlier focused review declarations")
    manifest, contract = focused_manifest(
        json.loads(SOURCE.read_bytes()), json.loads(DECLARATION_SOURCE.read_bytes()),
    )
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose="focused_review_checkpoint_shape_pilot_not_performance_comparison",
        source_collection_contract_sha256=fingerprint(contract),
    )
    configuration["common_environment"]["WORKLOAD_MANIFEST"] = str(MANIFEST.relative_to(ROOT))
    configuration["training_protocol"]["purpose"] = "natural_focused_read_only_child_shape_pilot"
    for path, value in zip(outputs[:3], (manifest, configuration, contract), strict=True):
        write(path, value)
    inputs = (
        SOURCE, DECLARATION_SOURCE, CONFIG_SOURCE, Path(__file__),
        ROOT / "beliefkv/runtime/checkpoint_repository_tools.py",
        ROOT / "beliefkv/runtime/checkpoint_subagents.py",
        ROOT / "beliefkv/runtime/deepagents_adapter.py",
        ROOT / "beliefkv/experiments/deepagents_swebench.py",
        ROOT / "beliefkv/experiments/root_work_budget.py",
    )
    write(outputs[3], {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "outputs_sha256": {str(path): sha256_file(path) for path in outputs[:3]},
        "original_sources_tests_environments_and_context_preserved": True,
        "new_task_scopes_are_not_measured_size_labels": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print({"projects": 6, "changed_objectives": 2, "child_access": "read_only", "GPU_action": False})


if __name__ == "__main__":
    main()
