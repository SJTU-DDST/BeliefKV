"""Collect missing original projects for a full, reusable workload population."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.delegation_policy import (
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract,
)
from beliefkv.experiments.long_repository_tasks import parse_repository_context
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_original_task_mix import (
    POOL, TOKENIZER_SOURCE, materialize_project, original_task,
    qualified_capture_configuration, scenario_catalog, source_integrity, validate_population,
)
from scripts.prepare_reused_workflow_mix import original_source_proof
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/original_project_supplement_20261030/preparation_r1"
MANIFEST = ROOT / "configs/workloads/locobench_original_supplement26_coder_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_original_supplement26_native_20261030.json"
BASE_CONFIG = ROOT / "configs/migration/workflow_original_task_mix6_coder_native_20261030.json"
INVENTORIES = (
    ROOT / "experiments/reports/workflow_mix_20261029/scope18_finish_r1/inventory.json",
    ROOT / "experiments/reports/original_task_mix6_20261030/finish_r1/inventory.json",
)

# Complete analysis tasks complement earlier implementation-heavy captures.
# The largest contexts also include analysis: work and KV size must cross.
CATEGORIES = {
    "python_web_cms_easy_038": "architectural_understanding",
    "python_system_security_medium_028": "feature_implementation",
    "python_system_networking_medium_063": "feature_implementation",
    "python_system_automation_medium_098": "feature_implementation",
    "python_ml_training_medium_087": "feature_implementation",
    "python_system_monitoring_medium_061": "feature_implementation",
    "python_web_blog_hard_076": "feature_implementation",
    "python_mobile_game_hard_060": "feature_implementation",
    "python_data_warehouse_hard_048": "architectural_understanding",
}


def missing_projects(candidates, inventories):
    covered = {row["project"].split("/", 1)[1] for inv in inventories for row in inv["workflows"]}
    if any(not row["eligible"] for inv in inventories for row in inv["workflows"]):
        raise ValueError("reuse inventory must retain complete naturally eligible workflows")
    selected = sorted(
        (row for row in candidates if not row.get("syntax_errors")
         and row["context_tokens"] <= 196608 and row["project_id"] not in covered),
        key=lambda row: (row["context_tokens"], row["project_id"]),
    )
    if not selected:
        raise ValueError("no missing original projects remain")
    return covered, selected


def bind_tools(here):
    qualified_path = here / "qualified_manifest.json"
    original, qualified = json.loads(MANIFEST.read_bytes()), json.loads(qualified_path.read_bytes())
    proof = qualified["tool_environment_qualification"]
    report_path = Path(proof["path"])
    report = json.loads(report_path.read_bytes())
    if (
        proof["sha256"] != sha256_file(report_path)
        or Path(proof["base_manifest"]).resolve() != MANIFEST
        or proof["base_manifest_sha256"] != sha256_file(MANIFEST)
        or report["tool_execution_passed"] is not True
        or report["qualification_scope"] != "live_root_child_tools"
        or report["task_tests_required"] is not False
    ):
        raise ValueError("fresh root/child execution evidence is required")
    with zipfile.ZipFile(original["archive"]) as archive:
        validate_population(qualified["workloads"], archive)
    configuration = qualified_capture_configuration(
        original, qualified, json.loads(CONFIG.read_bytes()), qualified_path,
    )
    output = here / "native_qualified.json"
    write(output, configuration)
    write(here / "tool_binding.json", {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (MANIFEST, CONFIG, qualified_path, report_path, Path(__file__))
        },
        "configuration": {"path": str(output), "sha256": sha256_file(output)},
        "original_tasks_context_source_preserved": True,
        "live_root_child_tools_passed": True, "task_correctness": "agent_responsibility",
        "GPU_experiment_started": False,
    })
    print({"qualified_projects": len(qualified["workloads"]), "configuration": str(output)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--bind-qualified-tools", action="store_true")
    args = parser.parse_args()
    here = args.output_dir.resolve()
    if args.bind_qualified_tools:
        bind_tools(here)
        return
    if here.exists() or MANIFEST.exists() or CONFIG.exists():
        raise FileExistsError("preserve previous original-project preparations")
    pool = json.loads(POOL.read_bytes())
    inventories = [json.loads(path.read_bytes()) for path in INVENTORIES]
    source_proofs = [original_source_proof(inv) for inv in inventories]
    if sha256_file(Path(pool["archive"])) != pool["archive_sha256"]:
        raise ValueError("pinned original archive changed")
    covered, missing = missing_projects(pool["candidates"], inventories)
    metadata = json.loads(TOKENIZER_SOURCE.read_bytes())
    configuration = json.loads(BASE_CONFIG.read_bytes())
    model = configuration["common_environment"]["CONTEXT_TOKENIZER_PATH"]
    if (
        model != metadata["tokenizer"]
        or sha256_file(Path(model) / "tokenizer.json") != metadata["tokenizer_sha256"]
        or sha256_file(Path(model) / "tokenizer_config.json") != metadata["tokenizer_config_sha256"]
    ):
        raise ValueError("current model tokenizer differs from pinned inputs")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    existing = {row["repo"].split("/", 1)[1]: row for row in pool["workloads"]}
    here.mkdir(parents=True, exist_ok=False)
    tasks, assignments = [], []
    with zipfile.ZipFile(pool["archive"]) as archive:
        catalog = scenario_catalog(archive, pool["candidates"])
        for candidate in missing:
            project = candidate["project_id"]
            category = CATEGORIES.get(project, "code_comprehension")
            options = [row for row in catalog if row["project"] == project and row["category"] == category]
            if len(options) != 1:
                raise ValueError("one full original task is required per missing project")
            option = options[0]
            template = existing.get(project)
            if template is None:
                template = materialize_project(
                    archive, project, candidate,
                    ROOT / "workloads/original_project_supplement_20261030",
                    ROOT / "workloads/sources/locobench_original_supplement_20261030",
                    pool, tokenizer,
                )
            reference = here / "references" / (option["original_id"] + ".reference.json")
            task, raw = original_task(template, option["scenario"], archive, reference)
            count = len(tokenizer.encode(
                parse_repository_context(task["frozen_repository_context"]).text,
                add_special_tokens=False,
            ))
            if count > 196608:
                raise ValueError("current tokenizer context exceeds the declared selection bound")
            task["frozen_repository_context"]["tokens"] = count
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            assignments.append({
                "instance_id": task["instance_id"], "project": task["repo"],
                "original_scenario": option["scenario"], "original_category": category,
                "title": option["title"], "original_difficulty": task["difficulty"],
                "context_tokens": count, "scenario_sha256": option["scenario_sha256"],
                "problem_statement_sha256": option["rendered_problem_sha256"],
                "task_requirements_unchanged": True, "source_integrity": source_integrity(task, archive),
            })
            tasks.append(task)
        validate_population(tasks, archive)
    random.Random(31).shuffle(tasks)
    order = {row["instance_id"]: index for index, row in enumerate(tasks)}
    assignments.sort(key=lambda row: order[row["instance_id"]])
    manifest = deepcopy(pool)
    for key in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(key, None)
    manifest.update(
        workloads=tasks, requested_workflows=len(tasks), max_tasks_per_project=1,
        selected_projects=[task["repo"].split("/", 1)[1] for task in tasks],
        selected_scenarios=[task["dataset_provenance"]["original_scenario"] for task in tasks],
        selection_seed=31, tokenizer=model, tokenizer_sha256=metadata["tokenizer_sha256"],
        tokenizer_config_sha256=metadata["tokenizer_config_sha256"],
        context_token_range=[min(row["context_tokens"] for row in assignments),
                             max(row["context_tokens"] for row in assignments)],
        purpose="missing_original_project_trace_supplement",
        selection_policy="all missing syntax-qualified projects; complete analysis and implementation originals",
    )
    declaration = {
        "schema_version": 1, "kind": "missing_original_project_trace_supplement",
        "assignments": assignments, "covered_original_projects": sorted(covered),
        "existing_source_proofs": source_proofs, "supplementary_projects": len(tasks),
        "combined_unique_project_candidates": len(covered) + len(tasks),
        "registration_permutation_seed": 31, "client_root_concurrency": 12,
        "category_counts": dict(Counter(row["original_category"] for row in assignments)),
        "complete_original_public_prompts_verified": True, "original_task_objectives_changed": False,
        "delegation_contract": delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        "shared_execution_protocol": {
            "child_warning_target_calls": [4, 8], "root_phase_warning_target_calls": [8, 16],
            "model_call_limit_enforced": False, "fixed_delegation_DAG_added": False,
        },
        "context_tokenizer": model, "context_counts_recomputed_with_current_model_tokenizer": True,
        "real_tool_times_retained": True, "source_tests_changed": False,
        "shape_review": {
            "model_work_p80_p20_diagnostic_minimum": 3,
            "model_work_p80_p20_stronger_diagnostic": 4,
            "model_work_p90_p10_is_reference_only": True,
            "multi_member_JOIN_fraction_diagnostic_minimum": .6,
            "child_calls_p50_p90_diagnostic_maxima": [12, 20],
            "root_parallel_rounds_median_diagnostic_minimum": 2,
            "source_capture_is_not_formal_arrival_pattern": True, "no_normal_tails_removed": True,
        },
        "source_shape_qualified": False, "serving_pressure_qualified": False,
        "formal_comparison_ready": False, "GPU_experiment_started": False, "performance_accepted": False,
    }
    configuration.update(
        purpose=declaration["kind"], source_collection_contract_sha256=fingerprint(declaration),
    )
    configuration["common_environment"].update(
        ROOT_COUNT=str(len(tasks)), CLIENT_ROOT_CONCURRENCY="12",
        WORKLOAD_MANIFEST=str(MANIFEST.relative_to(ROOT)), WORKFLOW_ARRIVAL_SCHEDULE="",
    )
    configuration["training_protocol"].update(
        purpose=declaration["kind"], extra_tasks=len(tasks),
        development_validation="complete_original_tasks_from_previously_uncovered_projects",
        complete_original_tasks_only=True, client_root_concurrency=12,
    )
    write(MANIFEST, manifest)
    write(CONFIG, configuration)
    write(here / "declaration.json", declaration)
    write(here / "preparation.json", {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (POOL, TOKENIZER_SOURCE, BASE_CONFIG, *INVENTORIES, Path(__file__))
        },
        "outputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (MANIFEST, CONFIG, here / "declaration.json")
        },
        "original_task_integrity_passed": True, "fresh_live_tool_check_required": True,
        "GPU_experiment_started": False,
    })
    print({
        "covered_projects": len(covered), "supplementary_projects": len(tasks),
        "combined_candidates": len(covered) + len(tasks),
        "context_range": manifest["context_token_range"], "GPU_action": False,
    })


if __name__ == "__main__":
    main()
