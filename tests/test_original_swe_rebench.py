from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from beliefkv.experiments.original_swe_rebench import (
    KIND, PUBLIC_FIELDS, bind_task, original_task, repository_tree, source_dataset, task_inputs,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import original_project
from beliefkv.experiments.swebench_prompt import build_swebench_task_prompt
from scripts import run_causal_source_capture as capture
from scripts.prepare_reused_workflow_mix import original_source_proof


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def original(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("value = 1\n")
    for arguments in (
        ("init", "-q"), ("add", "code.py"),
        ("-c", "user.name=fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "original"),
    ):
        subprocess.run(["git", "-C", str(repo), *arguments], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    issue = "Preserve the original requirement.\nProject: this line is issue data, not identity."
    public = {
        "instance_id": "example__project-1", "repo": "example/project",
        "base_commit": commit, "problem_statement": issue,
    }
    inventory = write(tmp_path / "public.json", {
        "candidates": [public], "read_fields": sorted(PUBLIC_FIELDS),
        "oracle_values_read_or_exported": False,
    })
    dataset = {"kind": KIND, "path": str(inventory), "sha256": sha256_file(inventory)}
    task = {
        **public, "task_kind": "swebench", "source_repo": str(repo),
        "docker_image": "sha256:" + "b" * 64,
        "dataset_provenance": {
            "dataset": "SWE-rebench", "original_instance_id": public["instance_id"],
            "public_inventory": str(inventory), "public_inventory_sha256": sha256_file(inventory),
            "original_problem_sha256": hashlib.sha256(issue.encode()).hexdigest(),
            "solution_or_gold_tests_exported": False,
        },
    }
    bound = bind_task(task, tmp_path / "source.json", dataset)
    manifest = {
        "requested_workflows": 1, "max_tasks_per_project": 1,
        "source_dataset": dataset, "workloads": [bound],
        "sandbox_test_env_path": "/opt/conda/envs/testbed",
    }
    return manifest, write(tmp_path / "configs/tasks.json", manifest), task


def test_capture_binds_real_original_commit_tree_public_issue_and_manifest_without_relabeling(original):
    manifest, path, unbound = original
    task = manifest["workloads"][0]
    assert task == {
        **unbound, "dataset_provenance": {
            **unbound["dataset_provenance"],
            "source_provenance_path": task["dataset_provenance"]["source_provenance_path"],
            "source_provenance_sha256": task["dataset_provenance"]["source_provenance_sha256"],
        },
    }
    assert capture.validated_workload(path) == manifest
    inputs = capture.declared_workload_inputs(path)
    assert inputs == [path, Path(manifest["source_dataset"]["path"]), path.parent.parent / "source.json"]
    assert repository_tree(task) == json.loads(inputs[-1].read_bytes())["git_tree"]
    assert original_task(task, source_dataset(manifest))["problem_statement"] == unbound["problem_statement"]


@pytest.mark.parametrize("change", ("problem", "repo", "base", "kind", "original_id", "context", "missing_declaration"))
def test_capture_rejects_changed_or_relabelled_original_task(original, change):
    manifest, path, _ = original
    task = manifest["workloads"][0]
    if change == "problem":
        task["problem_statement"] = "A shorter derived task."
        task["dataset_provenance"]["original_problem_sha256"] = hashlib.sha256(task["problem_statement"].encode()).hexdigest()
    elif change == "repo":
        task["repo"] = "another/project"
    elif change == "base":
        task["base_commit"] = "a" * 40
    elif change == "kind":
        task["task_kind"] = "locobench_agent"
    elif change == "original_id":
        task["dataset_provenance"]["original_instance_id"] = "another-id"
    elif change == "context":
        task["frozen_repository_context"] = {"unbound": True}
    else:
        manifest.pop("source_dataset")
    write(path, manifest)
    with pytest.raises(ValueError):
        capture.validated_workload(path)


@pytest.mark.parametrize("change", ("public", "gold", "modified_source", "forged_snapshot"))
def test_capture_rejects_changed_inventory_gold_exposure_worktree_or_tree_proof(original, change):
    manifest, path, _ = original
    task = manifest["workloads"][0]
    if change == "public":
        Path(manifest["source_dataset"]["path"]).write_text("{}")
    elif change == "gold":
        inventory_path = Path(manifest["source_dataset"]["path"])
        value = json.loads(inventory_path.read_bytes())
        value["read_fields"].append("patch")
        write(inventory_path, value)
        manifest["source_dataset"]["sha256"] = sha256_file(inventory_path)
        task["dataset_provenance"]["public_inventory_sha256"] = sha256_file(inventory_path)
        write(path, manifest)
    elif change == "modified_source":
        (Path(task["source_repo"]) / "code.py").write_text("value = 2\n")
    else:
        proof = Path(task["dataset_provenance"]["source_provenance_path"])
        value = json.loads(proof.read_bytes())
        value["git_tree"] = "a" * 40
        write(proof, value)
        task["dataset_provenance"]["source_provenance_sha256"] = sha256_file(proof)
        write(path, manifest)
    with pytest.raises(ValueError):
        capture.declared_workload_inputs(path)


def test_structured_inventory_resolves_swe_identity_and_keeps_issue_text_out_of_project_name(original):
    manifest, path, _ = original
    task = manifest["workloads"][0]
    client = {"config": {"workload_manifest": str(path)}, "workload_manifest_sha256": sha256_file(path)}
    prompt = build_swebench_task_prompt(**{name: task[name] for name in (
        "instance_id", "repo", "base_commit", "problem_statement",
    )})
    assert original_project(client, task["instance_id"], prompt) == "example/project"
    for invalid in (prompt.replace("Repository: example/project", "Repository: another/project"), "Repository: example/project"):
        with pytest.raises(ValueError):
            original_project(client, task["instance_id"], invalid)


def test_complete_original_source_reuse_includes_public_and_repository_provenance_inputs(original, tmp_path):
    manifest, path, _ = original
    task = manifest["workloads"][0]
    arm = tmp_path / "capture"
    write(arm / "client_1/manifest.json", {
        "config": {"workload_manifest": str(path)}, "workload_manifest_sha256": sha256_file(path),
    })
    result = original_source_proof({
        "capture": str(arm), "workflows": [{"task_id": task["instance_id"], "project": task["repo"]}],
    })
    assert result["complete_original_task_prompts_verified"]
    assert result["source_dataset"] == manifest["source_dataset"]
    assert set(result["source_inputs_sha256"]) == {
        str(value.resolve()) for value in task_inputs(task, manifest["source_dataset"])
    }


def test_capture_rejects_a_python_environment_different_from_the_qualified_manifest(original, tmp_path, monkeypatch):
    from tests.test_causal_source_capture import reference

    _manifest, source, _task = original
    plan, checkout = reference(tmp_path / "environment")
    target = checkout / "configs/tasks.json"
    target.write_bytes(source.read_bytes())
    monkeypatch.setattr(capture, "ROOT", source.parent.parent)
    args = (plan, checkout, tmp_path / "engine", tmp_path / "run", 18546)
    with pytest.raises(ValueError, match="Python environment"):
        capture.environment(*args, source_profile="reusable_nested", source_workload=source)
    plan["arms"]["native_matched"]["SANDBOX_TEST_ENV"] = "/opt/conda/envs/testbed"
    environment = capture.environment(*args, source_profile="reusable_nested", source_workload=source)
    assert environment["SANDBOX_TEST_ENV"] == "/opt/conda/envs/testbed"
    assert environment["NATIVE_POLICY_BASELINE"] == "1"
    assert environment["ROOT_COUNT"] == "1"


@pytest.mark.parametrize("change", (None, "group_environment", "project_environment", "preflight", "access"))
def test_original_capture_binds_exact_tool_qualified_task_and_project_environment(original, tmp_path, monkeypatch, change):
    manifest, path, _ = original
    task = manifest["workloads"][0]
    base = write(tmp_path / "tool-base.json", manifest)
    proof = {
        "tool_execution_passed": True, "qualification_scope": "live_root_child_tools",
        "manifest_sha256": sha256_file(base),
        "sandbox_test_env_path": "/wrong" if change == "group_environment" else manifest["sandbox_test_env_path"],
        "projects": {task["source_repo"]: {
            "tool_execution_passed": True, "source_commit": task["base_commit"],
            "docker_image_id": task["docker_image"], "sandbox_environment": {},
            "sandbox_test_env_path": "/wrong" if change == "project_environment" else manifest["sandbox_test_env_path"],
        }},
    }
    report = write(tmp_path / "tools.json", proof)
    manifest["tool_environment_qualification"] = {
        "path": str(report), "sha256": sha256_file(report),
        "base_manifest": str(base), "base_manifest_sha256": sha256_file(base),
    }
    if change == "preflight":
        task["preflight_command"] = "changed command"
    elif change == "access":
        task["child_task_access"] = "read_only"
    write(path, manifest)
    monkeypatch.setattr(capture.qualified, "command", lambda args: task["docker_image"])
    if change is None:
        inputs = capture.declared_workload_inputs(path, require_tool_execution=True)
        assert base in inputs and report in inputs
    else:
        with pytest.raises(ValueError, match="declared source"):
            capture.declared_workload_inputs(path, require_tool_execution=True)


def test_capture_configuration_retains_model_sampling_delegation_and_native_resources(original):
    from scripts.prepare_swe_rebench_capture import configuration

    manifest, path, _ = original
    base = {
        "source_profile": "nested_oneshot", "order": ["native_matched"],
        "common_environment": {
            "MODEL_CACHE_LAYOUT": "full_only", "MODEL_PATH": "/same/model",
            "ROOT_COUNT": "5", "CLIENT_ROOT_CONCURRENCY": "5", "MAX_RUNNING_REQUESTS": "24",
            "WORKLOAD_MANIFEST": "/previous/manifest", "WORKFLOW_ARRIVAL_SCHEDULE": "",
            "FANOUT_PROFILE": "native_in_graph_parallel_workstreams",
            "CHILD_WORK_BUDGET_WARNING_CALLS": "4", "CHILD_WORK_BUDGET_TARGET_CALLS": "8",
            "HICACHE_SIZE_GB": "200", "SAMPLING_PROFILE": "qwen3_coder",
        },
    }
    before = deepcopy(base)
    result = configuration(base, path, manifest)
    changed = {"ROOT_COUNT", "CLIENT_ROOT_CONCURRENCY", "WORKLOAD_MANIFEST", "SANDBOX_TEST_ENV"}
    assert {name: value for name, value in result["common_environment"].items() if name not in changed} == {
        name: value for name, value in base["common_environment"].items() if name not in changed
    }
    assert result["common_environment"]["ROOT_COUNT"] == "1"
    assert result["common_environment"]["SANDBOX_TEST_ENV"] == "/opt/conda/envs/testbed"
    assert base == before


def test_mixed_original_dataset_composition_is_explicit_and_preserves_resources_and_source_inputs(original, tmp_path):
    from beliefkv.experiments.composite_workload import SERVING_FIELDS
    from scripts.prepare_composite_workload import serving_artifacts

    manifest, path, _ = original
    task = manifest["workloads"][0]
    sources, proofs, selected = {}, [], []
    info = {**dict.fromkeys(SERVING_FIELDS), "model_path": "/same/model", "enable_beliefkv_admission": False}
    for name in ("legacy", "swe"):
        arm = tmp_path / name / "native_capture"
        write(arm / "server/server_info.json", info)
        write(arm / "server/native_capacity_census.json", {"capacity": {"device_full_tokens": 1000}})
        write(arm.parent / "capture_plan.json", {"engine_tree": "same-engine", "hold_file": "/same/hold"})
        if name == "legacy":
            source_task = {"instance_id": "legacy-task", "dataset_provenance": {"original_scenario": "original.json"}}
            workload = write(tmp_path / "legacy.json", {"archive_sha256": "a" * 64, "workloads": [source_task]})
            client = write(arm / "client_1/manifest.json", {})
            proof = {"workload": str(workload), "client_manifest": str(client)}
            project = "locobench/legacy"
        else:
            source_task, workload, project = task, path, task["repo"]
            write(arm / "client_1/manifest.json", {
                "config": {"workload_manifest": str(path)}, "workload_manifest_sha256": sha256_file(path),
            })
            proof = original_source_proof({
                "capture": str(arm), "workflows": [{"task_id": task["instance_id"], "project": project}],
            })
        row = {
            "source_capture": str(arm), "project": project, "task_id": source_task["instance_id"],
            "protocol_sha256": "same-soft-budgets",
        }
        sources[str(arm)] = [row]
        proofs.append(proof)
        selected.append(row)
    with pytest.raises(ValueError, match="same pinned dataset"):
        serving_artifacts(sources, proofs, selected)
    artifacts, inputs = serving_artifacts(sources, proofs, selected, allow_mixed_original_datasets=True)
    combined = artifacts["workload_manifest.json"]
    assert combined["heterogeneous_original_datasets_disclosed"] is True
    assert len(combined["source_datasets"]) == 2
    assert "archive_sha256" not in combined and "source_dataset" not in combined
    assert combined["workloads"][1] == task
    assert combined["selected_scenarios"] == ["original.json", task["instance_id"]]
    assert set(proofs[1]["source_inputs_sha256"]) <= inputs.keys()
    info["chunked_prefill_size"] = 1024
    write(tmp_path / "swe/native_capture/server/server_info.json", info)
    with pytest.raises(ValueError, match="same model"):
        serving_artifacts(sources, proofs, selected, allow_mixed_original_datasets=True)
