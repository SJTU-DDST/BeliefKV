"""Freeze a six-project derived-scope pilot before observing new trajectories."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/workflow_mix_20261029"
SOURCE = ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_mix_scope18_native_20261029.json"
MANIFEST = ROOT / "configs/workloads/locobench_parallel_pilot6_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_parallel_pilot6_native_20261030.json"
CONTEXT_BANDS = (1, 4)

BOUNDED_DELIVERABLES = {
    "locobench/python_web_dashboard_medium_039": (
        "Extract the email notification helpers from src/utils.py into "
        "src/email_service.py, move their required imports, and update the direct "
        "production call sites. Preserve the old public behavior. Validate this "
        "one extraction with a focused real check. Do not reorganize unrelated "
        "utilities or repair the entire test suite. Report any existing unrelated "
        "failures and the remaining work separately."
    ),
    "locobench/python_web_cms_hard_074": (
        "Implement only the first content-versioning milestone: add an ArticleVersion "
        "model, link Article to its latest version, and make the existing article "
        "creation service create and link the initial version atomically. Inspect "
        "mediverse_cms/apps/cms/models.py and the actual cms creation service. Add "
        "the matching schema migration if this project uses migrations and validate "
        "the creation boundary with a focused check. Submission/approval/rejection "
        "endpoints, permissions, notifications and public portal behavior belong "
        "to later work and are outside this milestone."
    ),
}


def pilot_manifest(source, declaration):
    """Select context bands by declaration, without consulting durations or results."""
    tasks = {row["instance_id"]: row for row in source["workloads"]}
    assignments = [
        row for row in declaration["assignments"] if row["context_band"] in CONTEXT_BANDS
    ]
    if (
        len(assignments) != 6
        or len({row["project"] for row in assignments}) != 6
        or Counter(row["scope_hypothesis"] for row in assignments) != {
            "investigation": 2, "implementation": 2, "multi_stage": 2,
        }
    ):
        raise ValueError("pilot requires two context-crossed projects per scope")
    selected, rows = [], []
    for assignment in assignments:
        original = tasks[assignment["instance_id"]]
        if original["repo"] != assignment["project"]:
            raise ValueError("pilot assignment changed its original project")
        task = deepcopy(original)
        scope = assignment["scope_hypothesis"]
        problem = original["problem_statement"]
        if scope == "investigation":
            # The first two public sections contain the title and symptom; the
            # broader original reporting requirements are deliberately out of scope.
            context = "\n\n".join(problem.split("\n\n")[:2])
            objective = (
                "Deliver one narrow, read-only diagnosis of the reported symptom: "
                "locate the relevant production call path and identify one concrete "
                "mismatch that explains the affected-versus-working behavior. Use "
                "complementary path-location and behavior checks in parallel when "
                "independent. Return concise path/symbol evidence, confidence, and "
                "the exact next verification if unresolved. No patch or complete "
                "project repair is requested. Stop after this diagnosis checkpoint."
            )
        elif scope == "implementation":
            context = "\n\n".join(problem.split("\n\n")[:2])
            objective = BOUNDED_DELIVERABLES[original["repo"]]
        else:
            context = problem
            objective = (
                "Complete the original multi-stage development objective below, "
                "including implementation, integration and focused verification of "
                "all its stages. Keep each delegated checkpoint small; integrate "
                "reports before assigning new independent checkpoints. Naturally "
                "finish when the original objective is addressed or honestly report "
                "a specific unresolved blocker. Do not add work to reach a round count."
            )
        task["instance_id"] = original["instance_id"] + "__parallel_checkpoint_v1"
        task["problem_statement"] = (
            "Requested deliverable for this derived task:\n" + objective
            + "\n\nOriginal public task context:\n" + context
        )
        task["dataset_provenance"]["derived_task"] = {
            "kind": "explicit_deliverable_scope_v1",
            "original_instance_id": original["instance_id"],
            "original_problem_sha256": fingerprint(problem),
            "requested_deliverable": objective,
            "original_full_task_success_claimed": scope == "multi_stage",
        }
        selected.append(task)
        rows.append({
            **assignment, "original_instance_id": original["instance_id"],
            "instance_id": task["instance_id"], "requested_deliverable": objective,
            "problem_statement_sha256": fingerprint(task["problem_statement"]),
        })
    manifest = deepcopy(source)
    manifest.update(
        workloads=selected, requested_workflows=6,
        selected_projects=[row["repo"].split("/", 1)[1] for row in selected],
        selected_scenarios=[row["dataset_provenance"]["original_scenario"] for row in selected],
        purpose="derived_deliverable_parallel_shape_pilot_not_performance_comparison",
        selection_policy=(
            "fixed context bands 1 and 4 from the declared eighteen-project population; "
            "two narrow diagnoses, two bounded edits, two full multi-stage tasks; "
            "no measured duration or scheduler outcome selection"
        ),
    )
    contract = {
        "schema_version": 1, "kind": "derived_deliverable_parallel_shape_pilot",
        "context_bands": list(CONTEXT_BANDS), "projects": 6,
        "scope_counts": dict(Counter(row["scope_hypothesis"] for row in rows)),
        "assignments": rows, "scope_is_not_a_measured_size_label": True,
        "shared_execution_protocol": {
            "profile": PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE,
            "child_warning_target_calls": [4, 8],
            "root_phase_warning_target_calls": [8, 16],
            "same_budgets_for_all_scopes": True,
            "hard_model_call_or_output_limit_added": False,
            "child_todo_tool": False, "root_todo_tool": True,
            "first_tool_choice": "auto", "natural_terminal_required": True,
        },
        "shape_review": {
            "multi_child_join_fraction_diagnostic_target": .6,
            "child_model_calls_median_diagnostic_target_max": 12,
            "child_model_calls_p90_diagnostic_target_max": 20,
            "model_work_p90_p10_diagnostic_target_range": [5, 10],
            "targets_are_not_runtime_limits_or_result_filters": True,
            "record_model_request_overlap_and_positive_native_shared_service_separately": True,
            "record_nested_joins_and_root_terminal_deferrals": True,
            "retain_all_complete_and_rejected_workflows": True,
            "automatic_expansion_to_eighteen": False,
        },
        "original_sources_tests_environments_preserved": True,
        "real_tool_times_retained": True, "new_main_rate_requires_new_matched_control": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    return manifest, contract


def main():
    outputs = (MANIFEST, CONFIG, HERE / "parallel_pilot6_declaration.json",
               HERE / "parallel_pilot6_preparation.json")
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve existing pilot declarations")
    manifest, declaration = pilot_manifest(
        json.loads(SOURCE.read_bytes()),
        json.loads((HERE / "scope18_declaration.json").read_bytes()),
    )
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose="derived_deliverable_parallel_shape_pilot_not_performance_comparison",
        source_collection_contract_sha256=fingerprint(declaration),
    )
    configuration["common_environment"].update(
        ROOT_COUNT="6", FANOUT_PROFILE=PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE,
        WORKLOAD_MANIFEST=str(MANIFEST.relative_to(ROOT)),
    )
    configuration["training_protocol"].update(
        extra_tasks=6, purpose="natural_derived_deliverable_shape_pilot",
        same_model_and_prompt_budgets_as_current_workload=False,
        child_warning_target=[4, 8], root_warning_target=[8, 16],
    )
    for path, value in zip(outputs[:3], (manifest, configuration, declaration)):
        write(path, value)
    write(outputs[3], {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (SOURCE, CONFIG_SOURCE, HERE / "scope18_declaration.json", Path(__file__))
        },
        "outputs_sha256": {str(path): sha256_file(path) for path in outputs[:3]},
        "project_count": 6, "GPU_experiment_started": False, "performance_accepted": False,
    })
    print({"projects": 6, "child_warning_target": [4, 8], "GPU_action": False})


if __name__ == "__main__":
    main()
