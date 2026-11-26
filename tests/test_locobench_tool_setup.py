from pathlib import Path

import pytest

from scripts.audit_locobench_tool_setup import merge_tool_environments
from scripts.prepare_locobench_agent_manifest import sha256_file
from scripts.provision_locobench_node import node_packages
from scripts.provision_locobench_python import python_setup


def declarations(workspace, files):
    digests = {}
    for name, content in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        digests[name] = sha256_file(path)
    return {"setup_file_sha256": digests}


def test_nested_original_requirements_resolve_in_their_own_directory(tmp_path):
    audit = declarations(tmp_path, {
        "nested/requirements.txt": "pydantic==1.10.15\n",
        "nested/requirements-dev.txt": "pytest==7.4.0\n",
    })
    plan = python_setup(tmp_path, audit, {"python_minor": 10})
    assert plan["directory"] == "nested"
    assert plan["arguments"] == ["-r", "requirements.txt", "-r", "requirements-dev.txt"]
    assert plan["python_minor"] == 10


def test_partial_python_install_preserves_original_pins_and_files(tmp_path):
    original = "aiocache==0.12.2\nmissing-wheel==3.0 # unavailable\n"
    audit = declarations(tmp_path, {"requirements.txt": original})
    excluded = {"missing-wheel==3.0": "package index has no matching release"}
    plan = python_setup(tmp_path, audit, {"unavailable_requirements": excluded})
    assert plan["arguments"] == ["aiocache==0.12.2"]
    assert plan["unavailable_requirements"] == excluded
    assert (tmp_path / "requirements.txt").read_text() == original
    with pytest.raises(ValueError, match="exactly match"):
        python_setup(tmp_path, audit, {"unavailable_requirements": {"missing-wheel==4.0": "bad"}})


def test_python_services_with_different_requirements_are_kept_separate(tmp_path):
    audit = declarations(tmp_path, {
        "a/requirements.txt": "pydantic==1.10.15\n",
        "b/requirements.txt": "pydantic==2.6.1\n",
    })
    assert python_setup(tmp_path, audit, {})["status"] == "separate_project_components"
    assert python_setup(tmp_path, audit, {"directory": "b"})["directory"] == "b"


def test_node_discovery_retains_malformed_and_workspace_declarations(tmp_path):
    audit = declarations(tmp_path, {
        "ok/package.json": '{"dependencies":{"redis":"^4.6.7"}}',
        "malformed/package.json": '{"dependencies":',
        "workspace/package.json": '{"dependencies":{"shared":"workspace:*"}}',
    })
    directories, blocked = node_packages(tmp_path, audit)
    assert directories == [Path("ok")]
    assert set(blocked) == {"malformed/package.json", "workspace/package.json"}
    (tmp_path / "ok/package.json").write_text("{}")
    with pytest.raises(ValueError, match="original package declaration changed"):
        node_packages(tmp_path, audit)


def test_merging_prepared_environments_rejects_changed_tasks():
    original = {
        "workloads": [
            {"instance_id": "a", "docker_image": "original-a", "problem_statement": "one"},
            {"instance_id": "b", "docker_image": "original-b", "problem_statement": "two"},
        ]
    }
    first, second = original["workloads"]
    fixed = {**first, "docker_image": "prepared-a", "preflight_command": "python -m pytest --version"}
    later = {**second, "docker_image": "prepared-b"}
    merged = merge_tool_environments(original, [
        {"workloads": [fixed, second]},
        {"workloads": [first, later]},
    ])
    assert merged["workloads"] == [fixed, later]
    with pytest.raises(ValueError, match="changed original task"):
        merge_tool_environments(original, [
            {"workloads": [{**fixed, "problem_statement": "different"}, second]},
        ])
