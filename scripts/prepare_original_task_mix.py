"""Select complete original tasks across context lengths, without editing goals."""

import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
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
from beliefkv.experiments.long_repository_tasks import (
    parse_repository_context, render_repository_context,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_locobench_agent_manifest import (
    git, load_project, scenario_prompt, write_json,
)
from scripts.train_workflow_work import write


POOL = ROOT / "configs/workloads/locobench_agent_36project_env_20261024.json"
TOKENIZER_SOURCE = ROOT / "configs/workloads/locobench_tools18_20261026.json"
CONFIG_SOURCE = ROOT / "configs/migration/workflow_parallel_checkpoint_pilot6_native_20261030.json"
HERE = ROOT / "experiments/reports/original_task_mix6_20261030/preparation_r2"
MANIFEST = ROOT / "configs/workloads/locobench_original_task_mix6_coder_20261030.json"
CONFIG = ROOT / "configs/migration/workflow_original_task_mix6_coder_native_20261030.json"

# Scope labels are hypotheses about original requirements, never measured sizes.
SELECTIONS = (
    ("python_ml_nlp_easy_053", "code_comprehension", "analysis"),
    ("python_mobile_utility_medium_023", "architectural_understanding", "analysis"),
    ("python_web_blog_easy_040", "multi_session_development", "implementation"),
    ("python_api_microservice_medium_044", "feature_implementation", "implementation"),
    ("python_web_social_hard_001", "multi_session_development", "multi_stage"),
    ("python_system_automation_hard_062", "multi_session_development", "multi_stage"),
)


def scenario_catalog(archive, candidates):
    projects = {row["project_id"]: row for row in candidates}
    rows = []
    for path in sorted(archive.namelist()):
        if not path.startswith("data/output/scenarios/python_") or not path.endswith(".json"):
            continue
        raw = archive.read(path)
        scenario = json.loads(raw)
        project = scenario["id"].split("_" + scenario["task_category"] + "_", 1)[0]
        candidate = projects.get(project)
        if candidate is None:
            raise ValueError("original scenario has no project context inventory")
        rows.append({
            "project": project, "scenario": path, "original_id": scenario["id"],
            "category": scenario["task_category"], "difficulty": scenario["difficulty"],
            "title": scenario["title"], "inventory_context_tokens": candidate.get("context_tokens"),
            "source_files": candidate.get("source_files"),
            "source_syntax_errors": candidate.get("syntax_errors", []),
            "scenario_sha256": hashlib.sha256(raw).hexdigest(),
            "rendered_problem_sha256": hashlib.sha256(scenario_prompt(scenario).encode()).hexdigest(),
            "public_prompt_characters": len(scenario_prompt(scenario)),
        })
    return rows


def original_task(task, scenario_path, archive, reference_path):
    raw = archive.read(scenario_path)
    scenario = json.loads(raw)
    project = scenario["id"].split("_" + scenario["task_category"] + "_", 1)[0]
    if task["repo"] != "locobench/" + project:
        raise ValueError("original scenario and project must match")
    result = deepcopy(task)
    result.update(
        instance_id="locobench__" + scenario["id"],
        difficulty=scenario["difficulty"], problem_statement=scenario_prompt(scenario),
        task_access="read_write", child_task_access="inherit", repository_tool_profile="native",
    )
    for field in ("test_command", "test_entrypoint_scope"):
        result.pop(field, None)
    provenance = result["dataset_provenance"]
    if "derived_task" in provenance:
        raise ValueError("select pristine project inputs instead of repurposing a derived candidate")
    provenance.update(
        original_scenario=scenario_path, task_category=scenario["task_category"],
        scenario_sha256=hashlib.sha256(raw).hexdigest(),
        reference_path=str(reference_path.resolve()), reference_fields_exposed=False,
    )
    validate_original_task(result, archive)
    return result, raw


def validate_original_task(task, archive):
    provenance = task["dataset_provenance"]
    raw = archive.read(provenance["original_scenario"])
    scenario = json.loads(raw)
    project = scenario["id"].split("_" + scenario["task_category"] + "_", 1)[0]
    if (
        task["instance_id"] != "locobench__" + scenario["id"]
        or task["repo"] != "locobench/" + project
        or task["difficulty"] != scenario["difficulty"]
        or provenance["task_category"] != scenario["task_category"]
        or provenance["scenario_sha256"] != hashlib.sha256(raw).hexdigest()
        or provenance.get("reference_fields_exposed") is not False
        or "derived_task" in provenance
        or task["problem_statement"] != scenario_prompt(scenario)
    ):
        raise ValueError("task must preserve the complete original public scenario")


def validate_population(tasks, archive):
    if not tasks or len({task["repo"] for task in tasks}) != len(tasks):
        raise ValueError("every workflow needs a different original project")
    for task in tasks:
        validate_original_task(task, archive)


def materialize_project(archive, project, inventory, assets, sources, pool, tokenizer):
    metadata_paths = [
        path for path in archive.namelist()
        if path.startswith("data/generated/")
        and path.endswith("/" + project + "/project_metadata.json")
    ]
    if len(metadata_paths) != 1:
        raise ValueError("original project metadata must be unique")
    _, files, provenance, errors = load_project(
        archive, metadata_paths[0], python_minor_version=10,
    )
    if errors:
        raise ValueError("preserve source syntax problems for agents; choose another candidate")
    context = render_repository_context(project, files)
    count = len(tokenizer.encode(context, add_special_tokens=False))
    source = sources / project
    assets.mkdir(parents=True, exist_ok=True)
    context_path = assets / (project + ".txt.gz")
    provenance_path = assets / (project + ".source_provenance.json")
    if source.exists():
        if (
            any((source / relative).read_bytes() != text.encode() for relative, text in files.items())
            or git(source, "status", "--porcelain")
            or gzip.decompress(context_path.read_bytes()).decode() != context
            or json.loads(provenance_path.read_bytes()) != provenance
        ):
            raise ValueError("existing import is not a pristine identical original project")
    else:
        source.mkdir(parents=True)
        for relative, text in files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(source, "init", "--quiet")
        git(source, "add", "--force", "--", ".")
        git(source, "-c", "user.name=BeliefKV Dataset Import",
            "-c", "user.email=dataset-import@localhost", "commit", "--quiet",
            "-m", f"Import frozen LoCoBench-Agent source {project}")
        with context_path.open("xb") as stream:
            stream.write(gzip.compress(context.encode(), mtime=0))
        write_json(provenance_path, provenance)
    return {
        "repo": "locobench/" + project, "base_commit": git(source, "rev-parse", "HEAD"),
        "source_repo": str(source.resolve()), "task_kind": "locobench_agent",
        "docker_image": pool["workloads"][0]["docker_image"],
        "preflight_command": pool["workloads"][0]["preflight_command"],
        "frozen_repository_context": {
            "path": str(context_path.resolve()), "sha256": sha256_file(context_path),
            "tokens": count, "source_file_count": len(files),
        },
        "dataset_provenance": {
            "source_provenance_path": str(provenance_path.resolve()),
            "source_provenance_sha256": sha256_file(provenance_path),
        },
    }


def source_integrity(task, archive):
    project = task["repo"].split("/", 1)[1]
    path = next(
        path for path in archive.namelist()
        if path.startswith("data/generated/")
        and path.endswith("/" + project + "/project_metadata.json")
    )
    _, files, _, _ = load_project(archive, path, python_minor_version=10)
    source = Path(task["source_repo"])
    for relative, text in files.items():
        if (source / relative).read_bytes() != text.encode():
            raise ValueError("project source/tests differ from the imported original: " + relative)
    if parse_repository_context(task["frozen_repository_context"]).text != render_repository_context(project, files):
        raise ValueError("frozen context must retain the complete original project")
    return {"files": len(files), "all_original_file_bytes_match": True, "complete_context_matches": True}


def qualified_capture_configuration(original, qualified, configuration, qualified_path):
    permitted = {"docker_image", "sandbox_environment", "test_command", "test_entrypoint_scope"}
    if (
        len(original["workloads"]) != len(qualified["workloads"])
        or original["archive_sha256"] != qualified["archive_sha256"]
    ):
        raise ValueError("tool qualification must retain the declared original population")
    for before, after in zip(original["workloads"], qualified["workloads"], strict=True):
        if (
            {key: value for key, value in before.items() if key not in permitted}
            != {key: value for key, value in after.items() if key not in permitted}
        ):
            raise ValueError("tool qualification changed original tasks, context or source")
    result = deepcopy(configuration)
    result["common_environment"]["WORKLOAD_MANIFEST"] = str(qualified_path.relative_to(ROOT))
    return result


def bind_qualified_tools(here):
    qualified_path = here / "qualified_manifest.json"
    qualified = json.loads(qualified_path.read_bytes())
    proof = qualified["tool_environment_qualification"]
    report_path = Path(proof["path"])
    report = json.loads(report_path.read_bytes())
    if (
        proof["sha256"] != sha256_file(report_path)
        or Path(proof["base_manifest"]).resolve() != MANIFEST.resolve()
        or proof["base_manifest_sha256"] != sha256_file(MANIFEST)
        or report["tool_execution_passed"] is not True
        or report["qualification_scope"] != "live_root_child_tools"
        or report["task_tests_required"] is not False
    ):
        raise ValueError("fresh root/child tool qualification is required")
    original = json.loads(MANIFEST.read_bytes())
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
    print({"qualified_projects": len(qualified["workloads"]), "configuration": str(output), "GPU_action": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--bind-qualified-tools", action="store_true")
    args = parser.parse_args()
    here = args.output_dir.resolve()
    if args.bind_qualified_tools:
        bind_qualified_tools(here)
        return
    if here.exists() or MANIFEST.exists() or CONFIG.exists():
        raise FileExistsError("preserve every original-task preparation")
    pool = json.loads(POOL.read_bytes())
    if sha256_file(Path(pool["archive"])) != pool["archive_sha256"]:
        raise ValueError("pinned original dataset archive changed")
    tokenizer_source = json.loads(TOKENIZER_SOURCE.read_bytes())
    model_tokenizer = json.loads(CONFIG_SOURCE.read_bytes())["common_environment"]["CONTEXT_TOKENIZER_PATH"]
    if (
        tokenizer_source["archive_sha256"] != pool["archive_sha256"]
        or tokenizer_source["tokenizer"] != model_tokenizer
        or sha256_file(Path(model_tokenizer) / "tokenizer.json") != tokenizer_source["tokenizer_sha256"]
        or sha256_file(Path(model_tokenizer) / "tokenizer_config.json") != tokenizer_source["tokenizer_config_sha256"]
    ):
        raise ValueError("pinned context tokenizer changed")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_tokenizer, local_files_only=True)
    existing = {row["repo"].split("/", 1)[1]: row for row in pool["workloads"]}
    candidates = {row["project_id"]: row for row in pool["candidates"]}
    here.mkdir(parents=True, exist_ok=False)
    assignments, tasks = [], []
    with zipfile.ZipFile(pool["archive"]) as archive:
        catalog = scenario_catalog(archive, pool["candidates"])
        for project, category, scope in SELECTIONS:
            options = [row for row in catalog if row["project"] == project and row["category"] == category]
            if len(options) != 1:
                raise ValueError("choose exactly one complete original scenario")
            option = options[0]
            old = existing.get(project)
            if old is None:
                old = materialize_project(
                    archive, project, candidates[project],
                    ROOT / "workloads/original_task_mix6_20261030",
                    ROOT / "workloads/sources/locobench_original_task_mix6_20261030",
                    pool, tokenizer,
                )
            reference = here / "references" / (option["original_id"] + ".reference.json")
            task, raw = original_task(old, option["scenario"], archive, reference)
            task["frozen_repository_context"]["tokens"] = len(tokenizer.encode(
                parse_repository_context(task["frozen_repository_context"]).text,
                add_special_tokens=False,
            ))
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            integrity = source_integrity(task, archive)
            tasks.append(task)
            assignments.append({
                "instance_id": task["instance_id"], "original_instance_id": task["instance_id"],
                "project": task["repo"], "original_scenario": option["scenario"],
                "title": option["title"], "original_category": category,
                "original_difficulty": task["difficulty"], "scope_hypothesis": scope,
                "context_tokens": task["frozen_repository_context"]["tokens"],
                "task_access": task["task_access"],
                "scenario_sha256": option["scenario_sha256"],
                "problem_statement_sha256": option["rendered_problem_sha256"],
                "task_requirements_unchanged": True, "source_integrity": integrity,
            })
        validate_population(tasks, archive)
    random.Random(21).shuffle(tasks)
    order = {task["instance_id"]: index for index, task in enumerate(tasks)}
    assignments.sort(key=lambda row: order[row["instance_id"]])
    manifest = deepcopy(pool)
    for field in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(field, None)
    manifest.update(
        workloads=tasks, requested_workflows=len(tasks), max_tasks_per_project=1,
        selected_projects=[task["repo"].split("/", 1)[1] for task in tasks],
        selected_scenarios=[task["dataset_provenance"]["original_scenario"] for task in tasks],
        selection_seed=21, context_token_range=[
            min(row["context_tokens"] for row in assignments),
            max(row["context_tokens"] for row in assignments),
        ],
        purpose="original_complete_task_mixed_context_shape_pilot",
        tokenizer=model_tokenizer, tokenizer_sha256=tokenizer_source["tokenizer_sha256"],
        tokenizer_config_sha256=tokenizer_source["tokenizer_config_sha256"],
        selection_policy=(
            "declared complete original analysis/implementation/multi-stage scenarios; "
            "short and long full contexts; no task rewriting or policy-outcome selection"
        ),
    )
    declaration = {
        "schema_version": 1, "kind": "original_complete_task_mixed_context_shape_pilot",
        "assignments": assignments, "registration_permutation_seed": 21,
        "scope_counts": dict(Counter(row["scope_hypothesis"] for row in assignments)),
        "original_task_objectives_changed": False,
        "complete_original_public_prompts_verified": True, "different_projects": len(tasks),
        "delegation_contract": delegation_contract(PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE),
        "root_child_soft_budgets_changed": False,
        "root_warning_target_calls": [8, 16], "child_warning_target_calls": [4, 8],
        "all_children_keep_normal_edit_permissions": True, "repository_tool_profile": "native",
        "context_tokenizer": model_tokenizer,
        "context_counts_recomputed_with_current_model_tokenizer": True,
        "fixed_delegation_DAG_added": False, "real_tool_times_retained": True,
        "shape_review": {
            "model_work_p80_p20_diagnostic_minimum": 3,
            "model_work_p80_p20_stronger_diagnostic": 4,
            "model_work_p90_p10_is_reference_only": True,
            "multi_member_JOIN_fraction_diagnostic_minimum": .6,
            "child_calls_p50_p90_diagnostic_maxima": [12, 20],
            "root_parallel_rounds_median_diagnostic_minimum": 2,
            "scope_and_difficulty_are_not_measured_work_labels": True,
            "no_normal_tails_removed": True,
        },
        "source_shape_qualified": False, "serving_pressure_qualified": False,
        "formal_comparison_ready": False, "GPU_experiment_started": False,
        "performance_accepted": False,
    }
    configuration = json.loads(CONFIG_SOURCE.read_bytes())
    configuration.update(
        purpose=declaration["kind"], source_collection_contract_sha256=fingerprint(declaration),
    )
    configuration["common_environment"]["WORKLOAD_MANIFEST"] = str(MANIFEST.relative_to(ROOT))
    configuration["training_protocol"].update(
        purpose=declaration["kind"], complete_original_tasks_only=True,
    )
    write(MANIFEST, manifest)
    write(CONFIG, configuration)
    write(here / "declaration.json", declaration)
    write(here / "original_catalog.json", {
        "archive": pool["archive"], "archive_sha256": pool["archive_sha256"],
        "original_python_scenario_count": len(catalog),
        "task_difficulty_counts": dict(Counter(row["difficulty"] for row in catalog)),
        "inventory_context_tokenizer": pool["tokenizer"],
        "selected_task_context_tokenizer": model_tokenizer,
        "scenarios": catalog, "reference_answers_used_for_selection": False,
        "observed_policy_outcomes_used_for_selection": False,
    })
    write(here / "preparation.json", {
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (POOL, TOKENIZER_SOURCE, CONFIG_SOURCE, Path(__file__))
        },
        "outputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (MANIFEST, CONFIG, here / "declaration.json", here / "original_catalog.json")
        },
        "original_task_integrity_passed": True, "fresh_live_tool_check_required": True,
        "source_shape_qualified": False, "GPU_experiment_started": False,
    })
    print({
        "original_scenarios": len(catalog), "projects": len(tasks),
        "context_tokens": [row["context_tokens"] for row in assignments],
        "original_tasks_unchanged": True, "GPU_action": False,
    })


if __name__ == "__main__":
    main()
