from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from beliefkv.experiments.deepagents_swebench import load_workload_bundle
from beliefkv.experiments.long_repository_tasks import parse_repository_context
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.prepare_locobench_agent_manifest import scenario_prompt
from scripts.prepare_original_task_mix import (
    CONFIG, HERE, MANIFEST, POOL, ROOT, SELECTIONS,
    original_task, qualified_capture_configuration, source_integrity,
    validate_original_task, validate_population,
)


@pytest.fixture(scope="module")
def prepared():
    pool = json.loads(POOL.read_bytes())
    with zipfile.ZipFile(pool["archive"]) as archive:
        yield json.loads(MANIFEST.read_bytes()), json.loads((HERE / "declaration.json").read_bytes()), archive


def test_selected_original_tasks_retain_full_requirements_across_context_lengths(prepared):
    manifest, declaration, archive = prepared
    tasks = manifest["workloads"]
    validate_population(tasks, archive)
    assert len(tasks) == len({task["repo"] for task in tasks}) == 6
    assert {
        (task["repo"].split("/", 1)[1], task["dataset_provenance"]["task_category"])
        for task in tasks
    } == {(project, category) for project, category, _ in SELECTIONS}
    assert min(task["frozen_repository_context"]["tokens"] for task in tasks) < 40000
    assert max(task["frozen_repository_context"]["tokens"] for task in tasks) > 110000
    assert any(
        task["dataset_provenance"]["task_category"] == "architectural_understanding"
        and task["frozen_repository_context"]["tokens"] > 90000
        for task in tasks
    )
    assert any(
        task["dataset_provenance"]["task_category"] == "multi_session_development"
        and task["frozen_repository_context"]["tokens"] < 40000
        for task in tasks
    )
    assert declaration["complete_original_public_prompts_verified"]
    assert not declaration["original_task_objectives_changed"]
    assert declaration["context_counts_recomputed_with_current_model_tokenizer"]
    assert manifest["tokenizer"] == declaration["context_tokenizer"]
    assert "Qwen3-Coder" in manifest["tokenizer"]
    assert not declaration["formal_comparison_ready"]
    for task in tasks:
        provenance = task["dataset_provenance"]
        raw = archive.read(provenance["original_scenario"])
        original = json.loads(raw)
        assert task["problem_statement"] == scenario_prompt(original)
        assert provenance["scenario_sha256"] == hashlib.sha256(raw).hexdigest()
        assert Path(provenance["reference_path"]).read_bytes() == raw
        assert "derived_task" not in provenance
        assert source_integrity(task, archive)["all_original_file_bytes_match"]


def test_context_counts_use_the_running_models_tokenizer_not_the_import_pool(prepared):
    from transformers import AutoTokenizer
    manifest, declaration, _ = prepared
    configuration = json.loads(CONFIG.read_bytes())
    path = configuration["common_environment"]["CONTEXT_TOKENIZER_PATH"]
    assert path == manifest["tokenizer"] == declaration["context_tokenizer"]
    assert "Qwen3-Coder" in path
    assert hashlib.sha256((Path(path) / "tokenizer.json").read_bytes()).hexdigest() == manifest["tokenizer_sha256"]
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    for task in manifest["workloads"]:
        context = parse_repository_context(task["frozen_repository_context"])
        assert context.tokens == len(tokenizer.encode(context.text, add_special_tokens=False))


def test_original_multisession_tasks_do_not_lose_later_sessions(prepared):
    manifest, _, archive = prepared
    for task in manifest["workloads"]:
        if task["dataset_provenance"]["task_category"] != "multi_session_development":
            continue
        changed = deepcopy(task)
        changed["problem_statement"] = changed["problem_statement"].split("Session 2:")[0]
        if changed["problem_statement"] == task["problem_statement"]:
            changed["problem_statement"] = changed["problem_statement"].split("SESSION 2:")[0]
        assert changed["problem_statement"] != task["problem_statement"]
        with pytest.raises(ValueError, match="complete original"):
            validate_original_task(changed, archive)


@pytest.mark.parametrize("mutation", (
    "derived_id", "wrong_project", "wrong_difficulty", "wrong_category",
    "wrong_scenario_hash", "reference_exposed", "derived_provenance",
    "narrowed_deliverable",
))
def test_task_integrity_rejects_rewritten_or_mislabelled_originals(prepared, mutation):
    manifest, _, archive = prepared
    task = deepcopy(manifest["workloads"][0])
    if mutation == "derived_id":
        task["instance_id"] += "__narrowed"
    elif mutation == "wrong_project":
        task["repo"] = "locobench/unrelated_project"
    elif mutation == "wrong_difficulty":
        task["difficulty"] = "invented"
    elif mutation == "wrong_category":
        task["dataset_provenance"]["task_category"] = "invented"
    elif mutation == "wrong_scenario_hash":
        task["dataset_provenance"]["scenario_sha256"] = "0" * 64
    elif mutation == "reference_exposed":
        task["dataset_provenance"]["reference_fields_exposed"] = True
    elif mutation == "derived_provenance":
        task["dataset_provenance"]["derived_task"] = {"original_goal_removed": True}
    else:
        task["problem_statement"] = "Only inspect one function and stop."
    with pytest.raises(ValueError, match="complete original"):
        validate_original_task(task, archive)


def test_original_task_builder_does_not_mutate_inputs_or_recycle_derived_provenance(prepared):
    manifest, _, archive = prepared
    task = deepcopy(manifest["workloads"][0])
    original = deepcopy(task)
    result, _ = original_task(
        task, task["dataset_provenance"]["original_scenario"], archive,
        Path(task["dataset_provenance"]["reference_path"]),
    )
    assert task == original
    assert result == original
    task["dataset_provenance"]["derived_task"] = {}
    with pytest.raises(ValueError, match="pristine"):
        original_task(task, task["dataset_provenance"]["original_scenario"], archive, Path("unused"))


def test_same_original_project_cannot_supply_multiple_workflows(prepared):
    manifest, _, archive = prepared
    tasks = deepcopy(manifest["workloads"])
    tasks.append(deepcopy(tasks[0]))
    with pytest.raises(ValueError, match="different original project"):
        validate_population(tasks, archive)


def test_original_tasks_keep_edit_permissions_and_matching_native_capture_contract(prepared):
    manifest, declaration, _ = prepared
    bundle = load_workload_bundle(MANIFEST)
    assert all(
        task.task_access == "read_write" and not task.children_read_only
        and task.repository_tool_profile == "native"
        for task in bundle.workloads
    )
    config = json.loads(CONFIG.read_bytes())
    assert config["source_collection_contract_sha256"] == fingerprint(declaration)
    environment = config["common_environment"]
    assert environment["ROOT_COUNT"] == str(len(manifest["workloads"]))
    assert environment["MAX_RUNNING_REQUESTS"] == "24"
    assert environment["CHILD_WORK_BUDGET_WARNING_CALLS"] == "4"
    assert environment["CHILD_WORK_BUDGET_TARGET_CALLS"] == "8"
    assert environment["ROOT_WORK_BUDGET_WARNING_CALLS"] == "8"
    assert environment["ROOT_WORK_BUDGET_TARGET_CALLS"] == "16"
    assert environment["TOOL_PREFETCH"] == environment["LAYER_HANDOFF"] == "0"
    assert not environment["WORKFLOW_ARRIVAL_SCHEDULE"]
    assert not declaration["GPU_experiment_started"]


def test_tool_binding_keeps_original_work_instead_of_repairing_project_tests(prepared):
    manifest, _, _ = prepared
    configuration = json.loads(CONFIG.read_bytes())
    before = deepcopy((manifest, configuration))
    qualified = deepcopy(manifest)
    for task in qualified["workloads"]:
        task["docker_image"] = "sha256:" + "1" * 64
        task["sandbox_environment"] = {"PYTHONPATH": "/workspace/src"}
    path = HERE / "qualified_manifest.json"
    actual = qualified_capture_configuration(manifest, qualified, configuration, path)
    assert (manifest, configuration) == before
    assert actual["common_environment"]["WORKLOAD_MANIFEST"] == str(path.relative_to(ROOT))
    assert actual["source_collection_contract_sha256"] == configuration["source_collection_contract_sha256"]
    qualified["workloads"][0]["problem_statement"] += "\nOnly solve the first part."
    with pytest.raises(ValueError, match="changed original"):
        qualified_capture_configuration(manifest, qualified, configuration, path)
