"""Prepare eight original tasks with unchanged, tool-qualified project sources."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_locobench_agent_manifest import scenario_prompt
from scripts.train_workflow_work import write

BASE_WORKLOAD = ROOT / "configs/workloads/locobench_tools18_20261026.json"
BASE_CONFIG = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
WORKLOAD = ROOT / "configs/workloads/locobench_workflow_work_train8_20261029.json"
CONFIG = ROOT / "configs/migration/workflow_work_train8_native_20261029.json"
HERE = Path(__file__).resolve().parent


def training_manifest(base, archive, references, *, count=8):
    if count < 1 or count > len(base["workloads"]):
        raise ValueError("training capture population is outside the qualified pool")
    # Preserve the existing seeded project order. Rotate the original task
    # category once; neither observed duration nor outcome selects these tasks.
    categories = {
        "feature_implementation": "cross_file_refactoring",
        "cross_file_refactoring": "integration_testing",
        "integration_testing": "feature_implementation",
    }
    candidates = {row["project_id"]: row for row in base["candidates"]}
    selected = []
    for old in base["workloads"][:count]:
        project = old["repo"].split("/", 1)[1]
        category = categories[old["dataset_provenance"]["task_category"]]
        paths = sorted(
            path for path in candidates[project]["scenario_paths"]
            if f"_{category}_" in path
        )
        if len(paths) != 1:
            raise ValueError("qualified project lacks one original alternative task")
        content = archive.read(paths[0])
        scenario = json.loads(content)
        if scenario["task_category"] != category:
            raise ValueError("archive task category changed")
        task = deepcopy(old)
        task.update(
            instance_id="locobench__" + scenario["id"],
            difficulty=scenario["difficulty"], problem_statement=scenario_prompt(scenario),
        )
        if task["instance_id"] == old["instance_id"]:
            raise ValueError("training task must differ from its prior evaluation task")
        reference = references / (scenario["id"] + ".reference.json")
        reference.parent.mkdir(parents=True, exist_ok=True)
        with reference.open("xb") as handle:
            handle.write(content)
        task["dataset_provenance"].update(
            original_scenario=paths[0], scenario_sha256=hashlib.sha256(content).hexdigest(),
            reference_path=str(reference.resolve()), task_category=category,
            reference_fields_exposed=False,
        )
        selected.append(task)
    result = deepcopy(base)
    result.update(
        requested_workflows=count, max_tasks_per_project=1, workloads=selected,
        selected_projects=[row["repo"].split("/", 1)[1] for row in selected],
        selected_scenarios=[row["dataset_provenance"]["original_scenario"] for row in selected],
        candidates=[candidates[row["repo"].split("/", 1)[1]] for row in selected],
        selection_policy="first eight existing seeded projects; rotate original task category; no timing/outcome selection",
        purpose="remaining_work_predictor_training_only_not_a_performance_baseline",
    )
    if len({row["repo"] for row in selected}) != count:
        raise ValueError("training capture must use different projects")
    return result


def training_configuration(base):
    env = deepcopy(base["common_environment"])
    env.update({
        "ROOT_COUNT": "8", "MAX_RUNNING_REQUESTS": "24",
        "WORKLOAD_MANIFEST": str(WORKLOAD.relative_to(ROOT)),
        "SGLANG_PATCH_FLAVOR": "staging",
        "WORKFLOW_SIZE_RESUME": "0", "BELIEFKV_WORKFLOW_SIZE_RESUME": "0",
        "WORKFLOW_WORK_MODEL": "", "BELIEFKV_WORKFLOW_WORK_MODEL": "",
        "WORKFLOW_WORK_MODEL_SHA256": "", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "",
        "ARRIVAL_BATCH_SIZE": "0", "ARRIVAL_BATCH_INTERVAL_MS": "0",
        "WORKFLOW_ARRIVAL_SCHEDULE": "",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21", "PYTHONHASHSEED": "21",
    })
    native = arm_environment(env, "native_matched")
    if any(native.get(name) != value for name, value in {
        "NATIVE_POLICY_BASELINE": "1", "BELIEFKV_WORKFLOW_SIZE_RESUME": "0",
        "BELIEFKV_AGENT_ADMISSION": "off", "BELIEFKV_ENABLE_PREPARE_HOST": "0",
        "BELIEFKV_ENABLE_TOOL_PREFETCH": "0", "BELIEFKV_LAYER_HANDOFF": "0",
    }.items()):
        raise ValueError("training capture must remain plain SGLang native")
    return {
        "schema_version": 1, "source_profile": "nested_oneshot",
        "purpose": "remaining_work_predictor_training_only_not_a_performance_baseline",
        "common_environment": env, "order": ["native_matched"],
        "performance_accepted": False,
        "training_protocol": {
            "extra_tasks": 8, "distinct_projects_per_capture": True,
            "same_model_and_prompt_budgets_as_current_workload": True,
            "child_warning_target": [4, 8], "root_warning_target": [8, 16],
            "normal_tool_times_retained": True, "tool_timing_overlay": None,
            "preserve_existing_18_workflow_evaluation_and_all_previous_traces": True,
            "development_validation": "independent_complete_captures_shared_projects_allowed",
        },
    }


def main():
    base = json.loads(BASE_WORKLOAD.read_bytes())
    if sha256_file(Path(base["archive"])) != base["archive_sha256"]:
        raise ValueError("original LoCoBench archive changed")
    with zipfile.ZipFile(base["archive"]) as archive:
        manifest = training_manifest(
            base, archive, ROOT / "workloads/workflow_work_training_20261029/references",
        )
    write(WORKLOAD, manifest)
    write(CONFIG, training_configuration(json.loads(BASE_CONFIG.read_bytes())))
    write(HERE / "preparation.json", {
        "workload": str(WORKLOAD), "workload_sha256": sha256_file(WORKLOAD),
        "configuration": str(CONFIG), "configuration_sha256": sha256_file(CONFIG),
        "base_workload_sha256": sha256_file(BASE_WORKLOAD),
        "base_configuration_sha256": sha256_file(BASE_CONFIG),
        "projects": [row["repo"] for row in manifest["workloads"]],
        "tasks": [row["instance_id"] for row in manifest["workloads"]],
        "source_and_test_files_changed": False, "GPU_experiment_started": False,
    })
    print(json.dumps({"workload": str(WORKLOAD), "configuration": str(CONFIG)}, indent=2))


if __name__ == "__main__":
    main()
