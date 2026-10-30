"""Declare original mixed-scope tasks without labelling their future work size."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from beliefkv.experiments.workload_mix import summary
from experiments.reports.workflow_work_training_20261029.prepare import training_configuration
from scripts.prepare_locobench_agent_manifest import scenario_prompt
from scripts.train_workflow_work import write


BASE = ROOT / "configs/workloads/locobench_tools18_20261026.json"
BASE_CONFIG = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
MANIFEST = ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json"
CONFIG = ROOT / "configs/migration/workflow_mix_scope18_native_20261029.json"
HERE = ROOT / "experiments/reports/workflow_mix_20261029"


def mixed_scope_manifest(base, archive, *, seed=21):
    if (
        type(seed) is not int or seed < 0 or len(base["workloads"]) != 18
        or len({row["repo"] for row in base["workloads"]}) != 18
    ):
        raise ValueError("mixed-scope collection requires eighteen distinct qualified projects")
    candidates = {row["project_id"]: row for row in base["candidates"]}
    ordered = sorted(
        base["workloads"], key=lambda row: (row["frozen_repository_context"]["tokens"], row["repo"]),
    )
    assignments, references = [], {}
    generator = random.Random(seed)
    # Each adjacent context band contributes one task to each scope. Scope
    # hypotheses are preparation metadata, never task-size hints to the model.
    for band in range(6):
        scopes = ["investigation", "implementation", "multi_stage"]
        generator.shuffle(scopes)
        for old, scope in zip(ordered[band * 3:(band + 1) * 3], scopes):
            category = {
                "investigation": "bug_investigation",
                "implementation": "feature_implementation" if band % 2 == 0 else "cross_file_refactoring",
                "multi_stage": "multi_session_development",
            }[scope]
            project = old["repo"].split("/", 1)[1]
            options = [
                name for name in candidates[project]["scenario_paths"]
                if f"_{category}_" in name
            ]
            if len(options) != 1:
                raise ValueError("project lacks one unmodified original scenario for its scope")
            raw = archive.read(options[0])
            scenario = json.loads(raw)
            if scenario["task_category"] != category:
                raise ValueError("original scenario category mismatch")
            task = deepcopy(old)
            task.update(
                instance_id="locobench__" + scenario["id"],
                difficulty=scenario["difficulty"], problem_statement=scenario_prompt(scenario),
            )
            reference = ROOT / "workloads/workflow_mix_scope18_20261029/references" / (
                scenario["id"] + ".reference.json"
            )
            task["dataset_provenance"].update(
                original_scenario=options[0], task_category=category,
                scenario_sha256=hashlib.sha256(raw).hexdigest(),
                reference_path=str(reference), reference_fields_exposed=False,
            )
            references[reference] = raw
            assignments.append({
                "task": task, "scope": scope, "context_band": band,
                "context_tokens": task["frozen_repository_context"]["tokens"],
            })
    # Use a separate seeded draw for registration order; neither context order
    # nor expected task scope determines who receives initial service.
    ordered_assignments = sorted(assignments, key=lambda row: row["task"]["instance_id"])
    random.Random(seed + 1).shuffle(ordered_assignments)
    result = deepcopy(base)
    result.update(
        workloads=[row["task"] for row in ordered_assignments],
        selected_projects=[row["task"]["repo"].split("/", 1)[1] for row in ordered_assignments],
        selected_scenarios=[
            row["task"]["dataset_provenance"]["original_scenario"] for row in ordered_assignments
        ],
        task_categories=sorted({row["task"]["dataset_provenance"]["task_category"] for row in assignments}),
        selection_seed=seed, requested_workflows=18,
        selection_policy=(
            "eighteen existing qualified projects; one investigation/implementation/multi-stage "
            "task per adjacent context band; separate seeded registration permutation; "
            "no observed duration or scheduler outcome selection"
        ),
        purpose="natural_mixed_scope_candidate_collection_not_yet_a_qualified_size_mix",
    )
    declaration = {
        "schema_version": 1, "kind": "original_scope_diversity_candidate_collection",
        "scope_counts": dict(Counter(row["scope"] for row in assignments)),
        "scope_assignment_seed": seed, "registration_permutation_seed": seed + 1,
        "assignments": [
            {
                "instance_id": row["task"]["instance_id"], "project": row["task"]["repo"],
                "scope_hypothesis": row["scope"], "context_band": row["context_band"],
                "context_tokens": row["context_tokens"],
            }
            for row in ordered_assignments
        ],
        "context_tokens_by_scope": {
            scope: summary([row["context_tokens"] for row in assignments if row["scope"] == scope])
            for scope in ("investigation", "implementation", "multi_stage")
        },
        "observed_size_tiers": None, "observed_p90_p10": None,
        "scope_is_not_a_measured_size_label": True,
        "shared_execution_protocol": {
            "profile": "native_in_graph_short_workstreams",
            "child_warning_target_calls": [4, 8], "root_phase_warning_target_calls": [8, 16],
            "same_budgets_for_all_scopes": True, "fixed_DAG_or_turn_count": False,
            "root_minimum_completed_parallel_JOIN_members": 2,
            "natural_multi_round_and_nested_delegation_encouraged": True,
            "serial_phases_retained_within_parallel_workflows": True,
        },
        "post_capture_acceptance": {
            "complete_natural_returns": True, "different_projects": 18,
            "size_tier_counts": [6, 6, 6],
            "p80_p20_diagnostic_minimum": 3, "p80_p20_stronger_diagnostic": 4,
            "p90_p10_is_reference_only": True,
            "crossed_contexts_required": True,
            "record_real_JOIN_fanout_rounds_and_nesting": True,
            "no_normal_tail_removal_or_forced_length": True,
            "if_insufficient": "retain_results_and_expand_original_candidates; do_not_relax_silently",
        },
        "online_size_hints_injected": False,
        "project_sources_tests_environments_changed": False,
        "source_capture_arrivals": "simultaneous_seeded_permutation",
        "new_matched_performance_controls_required": True,
        "new_main_arrival_rate": None, "old_mean120s_reused": False,
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    return result, declaration, references


def main():
    base = json.loads(BASE.read_bytes())
    if sha256_file(Path(base["archive"])) != base["archive_sha256"]:
        raise ValueError("original public dataset archive changed")
    with zipfile.ZipFile(base["archive"]) as archive:
        manifest, declaration, references = mixed_scope_manifest(base, archive)
    for path, content in references.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError("preserve an existing distinct original reference")
        else:
            with path.open("xb") as stream:
                stream.write(content)
    configuration = training_configuration(
        json.loads(BASE_CONFIG.read_bytes()), count=18, workload=MANIFEST,
    )
    configuration.update(
        purpose="natural_mixed_scope_candidate_collection_not_a_size_ranking_result",
        source_collection_contract_sha256=fingerprint(declaration),
    )
    configuration["training_protocol"].update(
        purpose="collect_new_whole_workload_candidates_and_reuse_source_as_native",
        preserve_existing_18_workflow_evaluation_and_all_previous_traces=True,
        no_further_native_replay_of_this_source_required=True,
    )
    write(MANIFEST, manifest)
    write(CONFIG, configuration)
    write(HERE / "scope18_declaration.json", declaration)
    write(HERE / "scope18_preparation.json", {
        "workload": {"path": str(MANIFEST), "sha256": sha256_file(MANIFEST)},
        "configuration": {"path": str(CONFIG), "sha256": sha256_file(CONFIG)},
        "declaration_sha256": sha256_file(HERE / "scope18_declaration.json"),
        "inputs_sha256": {str(path): sha256_file(path) for path in (BASE, BASE_CONFIG, Path(__file__))},
        "original_source_tests_context_environment_preserved": True,
        "project_count": len({row["repo"] for row in manifest["workloads"]}),
        "GPU_experiment_started": False,
    })
    print(json.dumps({
        "workload": str(MANIFEST), "configuration": str(CONFIG),
        "scope_counts": declaration["scope_counts"], "qualified_measured_size_mix": False,
    }))


if __name__ == "__main__":
    main()
