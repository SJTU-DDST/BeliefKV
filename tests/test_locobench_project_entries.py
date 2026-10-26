from copy import deepcopy
from pathlib import Path

import pytest

from beliefkv.experiments.deepagents_swebench import (
    SweBenchWorkload, repository_sandbox_contract,
)
from scripts import qualify_locobench_project_entries as entries


def test_candidate_enumeration_uses_original_production_examples_without_execution(tmp_path):
    (tmp_path / "strange.py").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_fake.py").write_text(
        '"""\\n>>> 1 + 1\\n2\\n"""\n'
    )
    source = (
        '"""\\n>>> price(2)\\n4\\n"""\n'
        "raise RuntimeError('do not execute at enumeration')\n"
        "def price(value):\n    return value * 2\n"
    )
    (tmp_path / "application.py").write_text(source)
    (tmp_path / "invalid.py").write_text(
        '"""\\n>>> 1 + 1\\n2\\n"""\nreturn 1\n'
    )
    found = entries.candidates(tmp_path)
    assert [row["path"] for row in found] == ["application.py"]
    assert found[0]["examples"] == 1
    assert (tmp_path / "application.py").read_text() == source


def test_python_path_uses_real_package_root_and_preserves_workspace(tmp_path):
    package = tmp_path / "service/src/domain"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "events.py").write_text("")
    (tmp_path / "service/pyproject.toml").write_text("")
    paths = entries.import_paths(tmp_path, "service/src/domain/events.py").split(":")
    assert paths[0] == "/workspace/service/src"
    assert "/workspace/service" in paths
    assert "/workspace" in paths
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("actual_project_call", [False, True])
def test_entrypoint_requires_actual_project_call_and_preserves_business_code(
    tmp_path, monkeypatch, actual_project_call,
):
    source = '"""\\n>>> double(2)\\n4\\n"""\ndef double(x):\n    return x * 2\n'
    (tmp_path / "core.py").write_text(source)
    row = {
        "source_repo": str(tmp_path), "base_commit": "unchanged",
        "preflight_command": "python -c 'import pytest'",
    }
    calls = []

    def probe(_source, _image, command, **kwargs):
        calls.append((command, kwargs))
        result = {
            "returncode": 0, "junit_xml": '<testsuite><testcase name="original"/></testsuite>',
        }
        if kwargs.get("production_calls"):
            result["production_calls"] = {
                "observation": "first_party_python_calls_during_pytest_call_phase",
                "call_events": int(actual_project_call),
                "test_cases_reaching_production_count": int(actual_project_call),
                "production_files": ["core.py"] if actual_project_call else [],
                "main_and_future_thread_profile_restored": True,
            }
        return result

    monkeypatch.setattr(entries, "run_probe", probe)
    result = entries.qualify(row, image="immutable", maximum_candidates=4)
    assert result["qualified"] is actual_project_call
    assert (tmp_path / "core.py").read_text() == source
    assert calls[1][1]["sandbox_environment"] == calls[2][1]["sandbox_environment"]
    assert "production_calls" not in calls[1][1]
    if actual_project_call:
        assert not result["business_task_repaired"]
        assert not result["original_tests_modified"]
        assert result["qualification_scope"] == "original_production_examples_not_entire_test_suite"


def test_qualified_subset_keeps_original_task_targets_and_complete_provenance():
    original = {
        "requested_workflows": 2, "qualified_projects": 2,
        "selected_scenarios": ["a", "b"],
        "environment_qualification": {"obsolete": True},
        "workloads": [
            {
                "source_repo": "/source/" + name,
                "problem_statement": f"Original target {name}",
                "dataset_provenance": {"original_scenario": name},
            }
            for name in ("a", "b")
        ],
    }
    before = deepcopy(original)
    result = entries.qualified_population(original, [original["workloads"][1]], {
        "/source/b": {"sandbox_environment": {"PYTHONPATH": "/workspace/src"}},
    })
    assert original == before
    assert result["requested_workflows"] == result["qualified_projects"] == 1
    assert result["selected_scenarios"] == result["selected_projects"] == ["b"]
    assert result["workloads"][0]["problem_statement"] == "Original target b"
    assert "environment_qualification" not in result


def test_original_examples_are_not_described_as_task_completion():
    workload = SweBenchWorkload(
        "task", "locobench/project", "base", "Original repair target", "hard",
        test_command="python -m pytest --doctest-modules source.py",
        test_entrypoint_scope="original_production_examples_not_entire_test_suite",
    )
    instruction = repository_sandbox_contract(workload)
    assert "not validation of the requested change" in instruction
    assert "focused regression tests against project implementation" in instruction
