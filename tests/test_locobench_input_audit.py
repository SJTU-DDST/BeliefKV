import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from beliefkv.experiments import deepagents_swebench as runner
from beliefkv.experiments.delegation_policy import REUSABLE_NESTED_IN_GRAPH_PROFILE
from beliefkv.runtime.context_lifecycle import ContextLifecyclePolicy
from scripts.audit_locobench_root_inputs import prefix_union, root_request
from scripts import qualify_locobench_environment as environment


def test_prefix_union_counts_shared_content_once_without_adding_isolation_tokens():
    assert prefix_union([[1, 2, 3], [1, 2, 4], [5]]) == (5, [2, 0])
    assert prefix_union([[1, 2], [1, 2]]) == (2, [2])
    assert prefix_union([]) == (0, [])


def test_real_reusable_root_probe_includes_source_and_both_task_api_tools(tmp_path, monkeypatch):
    text = "FULL_SOURCE_SENTINEL = 1\n"
    context = tmp_path / "context.gz"
    context.write_bytes(gzip.compress(text.encode(), mtime=0))
    workload = runner.SweBenchWorkload(
        "task", "locobench/project", "deadbeef", "Diagnose the stale state.", "hard",
        source_repo=tmp_path, docker_image="cpu-fixture", task_kind="locobench_agent",
        frozen_repository_context=runner.parse_repository_context({
            "path": str(context), "sha256": hashlib.sha256(context.read_bytes()).hexdigest(),
            "tokens": 10, "source_file_count": 1,
        }),
    )
    config = runner.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://127.0.0.1:1/v1", model="cpu-probe",
        output_dir=tmp_path, workload_manifest=tmp_path / "manifest.json",
        docker_image="cpu-fixture", completion_gate_enabled=False,
        native_radix_sessions=True, native_policy_baseline=True,
        subagent_fanout_profile=REUSABLE_NESTED_IN_GRAPH_PROFILE,
        context_lifecycle=ContextLifecyclePolicy(window_tokens=262144),
    )

    def no_tool_execution(*args, **kwargs):
        pytest.fail("initial input probe executed a workspace command")

    monkeypatch.setattr(runner.DockerWorkspaceBackend, "execute", no_tool_execution)
    messages, tools = root_request(config, workload, tmp_path)
    assert any("FULL_SOURCE_SENTINEL" in str(message.content) for message in messages)
    assert any("Diagnose the stale state." in str(message.content) for message in messages)
    assert {"task", "close_agent"} <= {row["function"]["name"] for row in tools}
    assert not (tmp_path / "context.gz").read_bytes() == b""


@pytest.mark.parametrize("project", ("new-project", "python_mobile_game_hard_060"))
def test_environment_probe_preserves_original_failures_and_accepts_new_projects(
    tmp_path, monkeypatch, project,
):
    manifest, output = tmp_path / "manifest.json", tmp_path / "out.json"
    source = tmp_path / project
    manifest.write_text(json.dumps({
        "workloads": [{
            "source_repo": str(source), "docker_image": "pinned",
            "base_commit": "original", "preflight_command": "python -c 'import pytest'",
        }],
    }))
    calls = []

    def command(arguments):
        calls.append(arguments)
        code = 2 if "pytest" in arguments[-1] and "import pytest" not in arguments[-1] else 0
        return {"returncode": code, "stdout": "original failure" if code else "", "stderr": ""}

    monkeypatch.setattr(environment, "capture", command)
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *a, **k: "sha256:pinned")
    monkeypatch.setattr("sys.argv", [
        "qualify", "--manifest", str(manifest), "--output", str(output),
    ])
    environment.main()
    result = json.loads(output.read_bytes())
    evidence = result["projects"][str(source)]
    assert result["dependency_preflight_passed"] is True
    assert evidence["original_source_modified"] is False
    assert evidence["original_test_result"]["returncode"] == 2
    assert evidence["original_test_result"]["stdout"] == "original failure"
    expected = environment.PROJECT_COMMANDS.get(project, environment.DEFAULT_TEST_COMMAND)
    assert calls[-1][-1] == expected


def test_project_image_override_is_explicit_without_rewriting_original_inputs(tmp_path, monkeypatch):
    manifest, output = tmp_path / "manifest.json", tmp_path / "out.json"
    source = str(tmp_path / "project")
    original = json.dumps({
        "workloads": [{
            "source_repo": source, "docker_image": "original",
            "base_commit": "commit", "preflight_command": "preflight",
        }],
    })
    manifest.write_text(original)
    calls = []
    monkeypatch.setattr(environment, "capture", lambda args: (
        calls.append(args) or {"returncode": 0, "stdout": "", "stderr": ""}
    ))
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *a, **k: "sha256:actual")
    monkeypatch.setattr("sys.argv", [
        "qualify", "--manifest", str(manifest), "--output", str(output),
        "--image", "shared", "--image-override", "project=special",
    ])
    environment.main()
    result = json.loads(output.read_bytes())
    assert result["default_image_override"] == "shared"
    assert result["project_image_overrides"] == {"project": "special"}
    assert result["projects"][source]["docker_image"] == "special"
    assert result["projects"][source]["docker_image_id"] == "sha256:actual"
    assert all(args[-4] == "sha256:actual" for args in calls)
    assert manifest.read_text() == original


@pytest.mark.parametrize("overrides", (
    ["unknown=image"], ["project="], ["project"], ["project=a", "project=b"],
))
def test_unknown_empty_or_duplicate_image_overrides_never_launch_a_container(
    tmp_path, monkeypatch, overrides,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "workloads": [{"source_repo": str(tmp_path / "project")}],
    }))
    args = ["qualify", "--manifest", str(manifest), "--output", str(tmp_path / "out.json")]
    for entry in overrides:
        args.extend(("--image-override", entry))
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(environment, "capture", lambda *_: pytest.fail("launched unknown image"))
    with pytest.raises(SystemExit):
        environment.main()


@pytest.mark.parametrize("change", (None, "failed_preflight", "missing_project", "commit", "tag"))
def test_environment_pin_changes_only_images_and_binds_the_actual_report(tmp_path, change):
    original = {"workloads": [{
        "source_repo": "/source/project", "base_commit": "original",
        "docker_image": "old", "problem_statement": "Original task.",
        "frozen_repository_context": {"sha256": "unchanged"},
    }]}
    manifest = deepcopy(original)
    report = {
        "dependency_preflight_passed": True, "manifest": "/manifest.json",
        "manifest_sha256": "b" * 64,
        "projects": {"/source/project": {
            "source_commit": "original", "docker_image_id": "sha256:" + "a" * 64,
        }},
    }
    if change == "failed_preflight":
        report["dependency_preflight_passed"] = False
    elif change == "missing_project":
        report["projects"].clear()
    elif change == "commit":
        report["projects"]["/source/project"]["source_commit"] = "another"
    elif change == "tag":
        report["projects"]["/source/project"]["docker_image_id"] = "mutable:tag"
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report))
    if change is not None:
        with pytest.raises(ValueError):
            environment.pin_environment(manifest, report, path)
    else:
        pinned = environment.pin_environment(manifest, report, path)
        assert pinned["workloads"][0]["docker_image"] == "sha256:" + "a" * 64
        assert pinned["environment_qualification"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        pinned["workloads"][0]["docker_image"] = "old"
        del pinned["environment_qualification"]
        assert pinned == original
    assert manifest == original
