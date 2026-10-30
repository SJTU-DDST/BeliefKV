"""Supplement complete original projects using current, rather than old, token counts."""

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

from beliefkv.experiments.composite_workload import load_composed_source
from beliefkv.experiments.delegation_policy import PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE, delegation_contract
from beliefkv.experiments.long_repository_tasks import parse_repository_context, render_repository_context
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_locobench_agent_manifest import load_project
from scripts.prepare_original_task_mix import (
    POOL, TOKENIZER_SOURCE, materialize_project, original_task,
    qualified_capture_configuration, scenario_catalog, source_integrity, validate_population,
)
from scripts.train_workflow_work import write


HERE = ROOT / "experiments/reports/remaining_original_projects_20261030/preparation_r1"
MANIFEST = ROOT / "configs/workloads/locobench_remaining_original5_coder_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_remaining_original5_native_20261030.json"
BASE = ROOT / "configs/migration/workflow_original_supplement26_native_20261030.json"
COMPLETED = ROOT / "experiments/reports/original_project_supplement_20261030/composite48_r1"
MAX_CONTEXT_TOKENS = 235000


def select_missing(candidates, covered, count_context, *, maximum):
    """Reject complete over-limit contexts; never shorten their source or task."""
    selected, observations = [], []
    for candidate in sorted(candidates, key=lambda row: row["project_id"]):
        project = candidate["project_id"]
        if project in covered or candidate.get("syntax_errors"):
            continue
        count = count_context(candidate)
        if type(count) is not int or count <= 0:
            raise ValueError("require actual positive current-model context lengths")
        accepted = count <= maximum
        observations.append({
            "project": project, "old_context_tokens": candidate["context_tokens"],
            "current_context_tokens": count, "selected": accepted,
            "reason": "complete_context_fits" if accepted else "insufficient_protocol_headroom",
        })
        if accepted:
            selected.append({**candidate, "current_context_tokens": count})
    return selected, observations


def bind_tools():
    original = json.loads(MANIFEST.read_bytes())
    qualified_path = HERE / "qualified_manifest.json"
    qualified = json.loads(qualified_path.read_bytes())
    proof = qualified["tool_environment_qualification"]
    report_path = Path(proof["path"])
    report = json.loads(report_path.read_bytes())
    if (
        proof["sha256"] != sha256_file(report_path)
        or Path(proof["base_manifest"]).resolve() != MANIFEST
        or proof["base_manifest_sha256"] != sha256_file(MANIFEST)
        or report.get("tool_execution_passed") is not True
        or report.get("qualification_scope") != "live_root_child_tools"
        or report.get("task_tests_required") is not False
    ):
        raise ValueError("require live root/child tools without changing project tests")
    with zipfile.ZipFile(original["archive"]) as archive:
        validate_population(qualified["workloads"], archive)
        for task in qualified["workloads"]:
            source_integrity(task, archive)
    configuration = qualified_capture_configuration(
        original, qualified, json.loads(CONFIG.read_bytes()), qualified_path,
    )
    output = HERE / "native_qualified.json"
    write(output, configuration)
    write(HERE / "tool_binding.json", {
        "configuration": str(output.resolve()), "configuration_sha256": sha256_file(output),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            MANIFEST, CONFIG, qualified_path, report_path, Path(__file__),
        )},
        "live_root_child_tools_passed": True, "original_source_tests_and_tasks_preserved": True,
        "GPU_experiment_started": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-qualified-tools", action="store_true")
    args = parser.parse_args()
    if args.bind_qualified_tools:
        bind_tools()
        return
    if HERE.exists() or MANIFEST.exists() or CONFIG.exists():
        raise FileExistsError("preserve earlier original-project preparations")
    pool, tokenizer_source, configuration = (
        json.loads(path.read_bytes()) for path in (POOL, TOKENIZER_SOURCE, BASE)
    )
    if sha256_file(Path(pool["archive"])) != pool["archive_sha256"]:
        raise ValueError("original archive changed")
    _plan, _expected, evidence = load_composed_source(COMPLETED)
    covered = {row["project"].split("/", 1)[1] for row in evidence["selection"]}
    model = configuration["common_environment"]["CONTEXT_TOKENIZER_PATH"]
    if (
        model != tokenizer_source["tokenizer"]
        or sha256_file(Path(model) / "tokenizer.json") != tokenizer_source["tokenizer_sha256"]
        or sha256_file(Path(model) / "tokenizer_config.json") != tokenizer_source["tokenizer_config_sha256"]
    ):
        raise ValueError("source selection must use the actual serving tokenizer")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    HERE.mkdir(parents=True, exist_ok=False)
    tasks, assignments = [], []
    with zipfile.ZipFile(pool["archive"]) as archive:
        metadata = {
            path.rsplit("/", 2)[1]: path for path in archive.namelist()
            if path.startswith("data/generated/python_") and path.endswith("/project_metadata.json")
        }

        def count_context(candidate):
            project = candidate["project_id"]
            _meta, files, _proof, errors = load_project(
                archive, metadata[project], python_minor_version=10,
            )
            if errors:
                raise ValueError("current source differs from the syntax-qualified inventory")
            return len(tokenizer.encode(render_repository_context(project, files), add_special_tokens=False))

        selected, observations = select_missing(
            pool["candidates"], covered, count_context, maximum=MAX_CONTEXT_TOKENS,
        )
        if not selected:
            raise ValueError("no complete new original projects fit")
        catalog = scenario_catalog(archive, pool["candidates"])
        for candidate in selected:
            project = candidate["project_id"]
            options = [
                row for row in catalog
                if row["project"] == project and row["category"] == "code_comprehension"
            ]
            if len(options) != 1:
                raise ValueError("require the unchanged original comprehension scenario")
            option = options[0]
            template = materialize_project(
                archive, project, candidate, ROOT / "workloads/remaining_original_projects_20261030",
                ROOT / "workloads/sources/locobench_remaining_original_20261030", pool, tokenizer,
            )
            reference = HERE / "references" / (option["original_id"] + ".reference.json")
            task, raw = original_task(template, option["scenario"], archive, reference)
            task["frozen_repository_context"]["tokens"] = candidate["current_context_tokens"]
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            tasks.append(task)
            assignments.append({
                "project": task["repo"], "instance_id": task["instance_id"],
                "original_category": option["category"], "original_scenario": option["scenario"],
                "original_difficulty": option["difficulty"], "original_title": option["title"],
                "context_tokens": candidate["current_context_tokens"],
                "scenario_sha256": option["scenario_sha256"],
                "problem_statement_sha256": option["rendered_problem_sha256"],
                "source_integrity": source_integrity(task, archive),
                "task_requirements_unchanged": True,
            })
        validate_population(tasks, archive)
    random.Random(37).shuffle(tasks)
    ordering = {row["instance_id"]: index for index, row in enumerate(tasks)}
    assignments.sort(key=lambda row: ordering[row["instance_id"]])
    manifest = deepcopy(pool)
    for name in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(name, None)
    manifest.update(
        workloads=tasks, requested_workflows=len(tasks), max_tasks_per_project=1,
        selected_projects=[row["repo"].split("/", 1)[1] for row in tasks],
        selected_scenarios=[row["dataset_provenance"]["original_scenario"] for row in tasks],
        selection_seed=37, tokenizer=model, tokenizer_sha256=tokenizer_source["tokenizer_sha256"],
        tokenizer_config_sha256=tokenizer_source["tokenizer_config_sha256"],
        purpose="remaining_complete_original_project_trace_supplement",
        selection_policy="all_missing_syntax_qualified_projects_fitting_current_tokenizer_with_protocol_headroom",
    )
    declaration = {
        "schema_version": 1, "kind": manifest["purpose"], "assignments": assignments,
        "already_captured_projects": sorted(covered), "current_tokenizer_observations": observations,
        "source_context_selection_maximum_tokens": MAX_CONTEXT_TOKENS,
        "context_window_tokens": 262144, "maximum_completion_tokens": 16384,
        "minimum_reserved_protocol_and_growth_tokens": 262144 - MAX_CONTEXT_TOKENS - 16384,
        "complete_original_task_prompts_verified": True, "source_tests_changed": False,
        "all_original_files_preserved": True, "new_project_count": len(tasks),
        "combined_original_project_candidates": len(covered) + len(tasks),
        "difficulty_counts": dict(Counter(row["original_difficulty"] for row in assignments)),
        "delegation_contract": delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        "shared_execution_protocol": {
            "child_warning_target_calls": [4, 8], "root_phase_warning_target_calls": [8, 16],
            "model_call_limit_enforced": False, "fixed_delegation_DAG_added": False,
        },
        "source_collection_client_root_concurrency": len(tasks), "native_running_decode_limit": 24,
        "actual_work_and_shape_require_complete_natural_capture": True,
        "future_serving": {
            "retain_existing_complete_sources": True, "fixed_arrival_index_measured_workflows": 18,
            "larger_initial_population_and_common_background_require_calibration": True,
            "model_work_p80_p20_minimum": 3, "model_work_p80_p20_stronger_diagnostic": 4,
            "full_JCT_and_background_coverage_required": True,
        },
        "formal_workload_qualified": False, "GPU_experiment_started": False,
    }
    configuration.update(
        purpose=declaration["kind"], source_collection_contract_sha256=fingerprint(declaration),
    )
    configuration["common_environment"].update(
        ROOT_COUNT=str(len(tasks)), CLIENT_ROOT_CONCURRENCY=str(len(tasks)),
        WORKLOAD_MANIFEST=str(MANIFEST.relative_to(ROOT)), WORKFLOW_ARRIVAL_SCHEDULE="",
    )
    configuration["training_protocol"].update(
        purpose=declaration["kind"], extra_tasks=len(tasks), complete_original_tasks_only=True,
        client_root_concurrency=len(tasks),
        development_validation="current_tokenizer_previously_uncovered_original_projects",
    )
    write(MANIFEST, manifest)
    write(CONFIG, configuration)
    write(HERE / "declaration.json", declaration)
    write(HERE / "preparation.json", {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            POOL, TOKENIZER_SOURCE, BASE, COMPLETED / "composite_source.json", Path(__file__),
        )},
        "outputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            MANIFEST, CONFIG, HERE / "declaration.json",
        )},
        "fresh_live_tool_check_required": True, "source_shape_or_pressure_qualified": False,
        "GPU_action": False,
    })
    print(json.dumps({
        "new_projects": len(tasks), "total_project_candidates": len(covered) + len(tasks),
        "contexts": [row["context_tokens"] for row in assignments],
        "difficulty_counts": declaration["difficulty_counts"], "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
