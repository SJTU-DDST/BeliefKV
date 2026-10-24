import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from beliefkv.experiments.long_repository_tasks import (
    FrozenRepositoryContext, parse_repository_context, render_repository_context,
    repository_path, unwrap_generated_file,
)
from scripts.prepare_locobench_agent_manifest import load_project, scenario_prompt
from scripts.extend_locobench_agent_manifest import extend_manifest


@pytest.mark.parametrize("path", ("../secret", "/tmp/x", "a/.git/config", "a\\..\\x", ""))
def test_project_paths_stay_relative_and_outside_git_metadata(path):
    with pytest.raises(ValueError, match="unsafe project path"):
        repository_path(path)


def test_whole_file_wrapper_is_removed_without_editing_the_source_body():
    source = '"""A literal ``` marker."""\nx = 1\n'
    assert unwrap_generated_file("```python\n" + source + "```\n") == (source, True)
    assert unwrap_generated_file(source) == (source, False)
    assert repository_path("project//src/a.py") == "project/src/a.py"


def test_import_uses_unique_actual_files_and_reports_unfixed_syntax(tmp_path):
    path = tmp_path / "data.zip"
    prefix = "data/generated/python_example/"
    metadata = {
        "specification": {"unique_id": "python_example"},
        "files": [{"path": "src//a.py"}, {"path": "src/a.py"}, {"path": "bad.py"}],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(prefix + "project_metadata.json", json.dumps(metadata))
        archive.writestr(prefix + "src/a.py", "```python\nanswer = 42\n```\n")
        archive.writestr(prefix + "bad.py", "```python\ndef broken(:\n```\n")
    with zipfile.ZipFile(path) as archive:
        _, files, provenance, errors = load_project(archive, prefix + "project_metadata.json")
    assert len(files) == len(provenance) == 2
    assert files["src/a.py"] == "answer = 42\n"
    assert files["bad.py"] == "def broken(:\n"
    assert [row["path"] for row in errors] == ["bad.py"]


def test_import_checks_the_sandbox_python_version(tmp_path):
    path = tmp_path / "data.zip"
    prefix = "data/generated/python_example/"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(prefix + "project_metadata.json", json.dumps({
            "specification": {"unique_id": "python_example"},
            "files": [{"path": "main.py"}],
        }))
        archive.writestr(prefix + "main.py", "match event:\n    case 'done':\n        pass\n")
    with zipfile.ZipFile(path) as archive:
        _, _, _, errors = load_project(archive, prefix + "project_metadata.json")
        assert len(errors) == 1
        _, _, _, errors = load_project(
            archive, prefix + "project_metadata.json", python_minor_version=10,
        )
        assert errors == []


def test_only_public_task_fields_are_exposed_and_context_is_hash_bound(tmp_path):
    public = scenario_prompt({
        "title": "Repair synchronization", "description": "Observed stale state.",
        "task_prompt": "Diagnose and fix the race.",
        "expected_approach": "SECRET_APPROACH", "ground_truth": "SECRET_GOLD",
        "evaluation_criteria": ["SECRET_CRITERION"],
    })
    assert public == "Repair synchronization\n\nObserved stale state.\n\nDiagnose and fix the race."
    text = render_repository_context("project", {"src/a.py": "x = 1\n"})
    path = tmp_path / "context.gz"
    path.write_bytes(gzip.compress(text.encode(), mtime=0))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    context = parse_repository_context({
        "path": str(path), "sha256": digest, "tokens": 100, "source_file_count": 1,
    })
    assert context.text == text
    with pytest.raises(ValueError, match="changed"):
        FrozenRepositoryContext(path, "0" * 64, 100, 1)
    with pytest.raises(TypeError, match="counts integers"):
        parse_repository_context({
            "path": str(path), "sha256": digest, "tokens": True, "source_file_count": 1,
        })


def test_extension_reuses_qualified_sources_and_selects_distinct_original_tasks(tmp_path):
    archive_path = tmp_path / "data.zip"
    scenarios = [
        f"data/python_example_{category}_expert_01.json"
        for category in ("bug_investigation", "feature_implementation", "integration_testing")
    ]
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index, path in enumerate(scenarios):
            archive.writestr(path, json.dumps({
                "id": f"task-{index}", "title": f"Public task {index}",
                "task_prompt": "Check observed evidence.", "difficulty": "expert",
                "task_category": "investigation", "ground_truth": "SECRET_GOLD",
            }))
    base = {
        "candidates": [{
            "project_id": "python_example", "rejection_reasons": [],
            "scenario_paths": scenarios,
        }],
        "workloads": [{
            "repo": "locobench/python_example", "source_repo": "qualified-source",
            "base_commit": "qualified-commit", "frozen_repository_context": {"sha256": "kept"},
            "dataset_provenance": {"source_provenance_sha256": "source-proof"},
        }],
    }
    with zipfile.ZipFile(archive_path) as archive:
        result = extend_manifest(
            base, archive, root_count=3, max_tasks_per_project=3,
            references=tmp_path / "references",
        )
        with pytest.raises(ValueError, match="not enough distinct"):
            extend_manifest(
                base, archive, root_count=4, max_tasks_per_project=4,
                references=tmp_path / "unused",
            )
    rows = result["workloads"]
    assert len({row["instance_id"] for row in rows}) == 3
    assert all(row["source_repo"] == "qualified-source" for row in rows)
    assert all(row["base_commit"] == "qualified-commit" for row in rows)
    assert all(row["frozen_repository_context"] == {"sha256": "kept"} for row in rows)
    assert all("SECRET_GOLD" not in row["problem_statement"] for row in rows)
    assert all("SECRET_GOLD" in Path(
        row["dataset_provenance"]["reference_path"],
    ).read_text() for row in rows)
    assert "requested_workflows" not in base
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("profile", [
    "native_in_graph_nested_2to4", "native_in_graph_dynamic_nested",
])
def test_only_root_receives_frozen_source_while_children_share_workspace(
    tmp_path, monkeypatch, profile,
):
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from beliefkv.experiments import deepagents_swebench as runner
    from beliefkv.runtime.context_lifecycle import ContextLifecyclePolicy

    text = render_repository_context("project", {"src/a.py": "SOURCE_SENTINEL = 1\n"})
    path = tmp_path / "context.gz"
    path.write_bytes(gzip.compress(text.encode(), mtime=0))
    context = FrozenRepositoryContext(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), 120000, 1,
    )
    workload = runner.SweBenchWorkload(
        "locobench__task", "locobench/project", "deadbeef", "Repair the race.", "hard",
        task_kind="locobench_agent", frozen_repository_context=context,
    )
    config = runner.DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://localhost:18000/v1", model="model",
        output_dir=tmp_path / "output", workload_manifest=tmp_path / "manifest.json",
        docker_image="fixture",
        completion_gate_enabled=False,
        max_completion_tokens=16384,
        subagent_fanout_profile=profile,
        context_lifecycle=ContextLifecyclePolicy(window_tokens=262144),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    audit = runner.JsonlAudit(tmp_path / "audit.jsonl")
    backend = runner.DockerWorkspaceBackend(
        workspace, image="fixture", audit=audit, support_dir=None,
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="done")])
    child_specs, roots = [], []

    def subagent_middleware(**kwargs):
        child_specs.extend(kwargs["subagents"])
        return SimpleNamespace(**kwargs)

    def create_agent(**kwargs):
        roots.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(runner, "PrivateStateIsolatingSubAgentMiddleware", subagent_middleware)
    monkeypatch.setattr(runner, "create_agent", create_agent)
    monkeypatch.setattr(runner, "_model", lambda *_: model)
    deadline = SimpleNamespace(register_model=lambda _: None, deadline=None)
    adapter = SimpleNamespace(record_call_censor=lambda _: None)
    try:
        runner._build_autonomous_agent(config, workload, backend, adapter, deadline)
    finally:
        audit.close()
    assert len(roots) == 1
    assert "LoCoBench-Agent" in roots[0]["system_prompt"]
    assert roots[0]["system_prompt"].count("SOURCE_SENTINEL") == 1
    assert len(child_specs) == (25 if profile == "native_in_graph_dynamic_nested" else 9)
    assert all("SOURCE_SENTINEL" not in spec["system_prompt"] for spec in child_specs)
    assert all("shared /workspace checkout" in spec["system_prompt"] for spec in child_specs)
    if profile == "native_in_graph_dynamic_nested":
        from beliefkv.experiments.child_work_budget import ChildWorkBudgetMiddleware

        assert "Every root workflow MUST delegate at least one substantive task" in roots[0]["system_prompt"]
        assert roots[0]["system_prompt"].index("SOURCE_SENTINEL") < roots[0]["system_prompt"].index(
            "DYNAMIC MULTI-ROUND DELEGATION",
        )
        assert all(runner.NATIVE_CHILD_RETURN_INSTRUCTION in spec["system_prompt"]
                   for spec in child_specs)
        assert all(
            any(isinstance(item, ChildWorkBudgetMiddleware) for item in spec["middleware"])
            for spec in child_specs
        )
        assert all("within about 32 model turns" in spec["system_prompt"] for spec in child_specs)
    assert "LoCoBench-Agent scenario" in runner._task_prompt(workload)
    assert "SOURCE_SENTINEL" not in runner._task_prompt(workload)
