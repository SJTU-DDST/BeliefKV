from __future__ import annotations

import os
import json
import subprocess
import sys

import pytest

from scripts import qualify_locobench_environment as environment


@pytest.mark.parametrize(("source", "options", "qualified", "executed"), [
    ("def test_actual():\n    assert True\n", (), True, 1),
    ("def test_actual():\n    assert False\n", (), True, 1),
    ("raise RuntimeError('settings are not configured')\n", (), False, 0),
    (
        "import pytest\n@pytest.fixture\ndef config():\n"
        "    raise RuntimeError('settings are not configured')\n"
        "def test_actual(config):\n    assert True\n",
        (), False, 0,
    ),
    (
        "import pytest\n@pytest.fixture\ndef config():\n"
        "    yield\n    raise RuntimeError('teardown failed')\n"
        "def test_actual(config):\n    assert True\n",
        (), False, 0,
    ),
    (
        "import pytest\n@pytest.mark.skip(reason='service unavailable')\n"
        "def test_actual():\n    assert True\n", (), False, 0,
    ),
    ("def test_actual():\n    assert True\n", ("--collect-only",), False, 0),
    ("VALUE = 1\n", (), False, 0),
    ("def test_actual():\n    import missing_beliefkv_test_dependency\n", (), False, 1),
    (
        "def test_actual():\n    raise FileNotFoundError('/workspace/tests/data/missing')\n",
        (), False, 1,
    ),
    (
        "def test_actual():\n    raise ConnectionError('external service unavailable')\n",
        (), False, 1,
    ),
])
def test_qualification_requires_real_calls_and_preserves_task_failures(
    tmp_path, source, options, qualified, executed,
):
    test = tmp_path / "test_project.py"
    test.write_text(source)
    report = tmp_path / "result.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", os.devnull,
         "-p", "no:cacheprovider", "--junitxml=" + str(report), *options, str(test)],
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
        capture_output=True, text=True, timeout=20,
    )
    evidence = environment.test_execution_evidence({
        "returncode": result.returncode, "junit_xml": report.read_text(),
    })
    assert evidence["qualified"] is qualified, (evidence, result.stdout)
    assert evidence["executed_test_calls_lower_bound"] == executed


@pytest.mark.parametrize("result", [
    {"returncode": 0},
    {"returncode": 0, "junit_xml": "<broken"},
    {"returncode": None, "timeout": True,
     "junit_xml": '<testsuite><testcase name="one"/></testsuite>'},
])
def test_missing_incomplete_or_timed_out_results_cannot_qualify(result):
    assert environment.test_execution_evidence(result)["qualified"] is False


@pytest.mark.parametrize(("message", "qualified"), [
    (
        "ValueError: found the following matches with the input file in xarray's IO backends: "
        "['netcdf4', 'h5netcdf']. But their dependencies may not be installed",
        False,
    ),
    ("ValueError: invalid application range", True),
    ("AssertionError: expected repaired behavior", True),
    ("Failed: DID NOT RAISE &lt;class 'FileNotFoundError'&gt;", True),
])
def test_optional_io_backend_failure_is_separate_from_real_application_assertions(message, qualified):
    import xml.etree.ElementTree as ET

    suite = ET.Element("testsuite")
    failure = ET.SubElement(ET.SubElement(suite, "testcase", name="test_one"), "failure")
    failure.set("message", message)
    evidence = environment.test_execution_evidence({
        "returncode": 1, "junit_xml": ET.tostring(suite, encoding="unicode"),
    })
    assert evidence["qualified"] is qualified


def test_qualified_test_command_is_visible_to_both_root_and_child_contract():
    from beliefkv.experiments.deepagents_swebench import (
        SweBenchWorkload, repository_sandbox_contract,
    )
    workload = SweBenchWorkload(
        "task", "locobench/project", "commit", "Repair a behavior.", "hard",
        test_command="cd /workspace/service && python -m pytest -q tests",
    )
    contract = repository_sandbox_contract(workload)
    assert workload.test_command in contract
    assert "fixture-setup error" in contract


def test_probe_timeout_removes_only_its_owned_container(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(environment, "capture", lambda args: (
        calls.append(args) or {"returncode": None, "timeout": True}
    ))
    monkeypatch.setattr(environment.subprocess, "run", lambda args, **_: calls.append(args))
    result = environment.run_probe(str(tmp_path), "sha256:" + "a" * 64, "python -m pytest")
    assert result["timeout"]
    name = calls[0][calls[0].index("--name") + 1]
    assert name.startswith("beliefkv-envtest-")
    assert calls[1] == ["docker", "rm", "-f", name]
    assert not any("--gpus" in arg for arg in calls[0])


def test_cleanup_timeout_preserves_original_test_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(environment, "capture", lambda _args: {
        "returncode": 1, "stdout": "1 failed in 0.2s",
    })

    def timeout(args, **_kwargs):
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(environment.subprocess, "run", timeout)
    result = environment.run_probe(str(tmp_path), "fixture", "python -m pytest")
    assert result["returncode"] == 1 and result["stdout"] == "1 failed in 0.2s"
    assert result["cleanup_error"] == "owned container removal timed out"


def test_project_environment_preserves_seed_support_and_matches_live_sandbox(
    monkeypatch, tmp_path,
):
    from beliefkv.experiments.deepagents_swebench import DockerWorkspaceBackend
    from beliefkv.experiments.sandbox_environment import project_environment

    settings = {
        "DJANGO_SETTINGS_MODULE": "project.test_settings",
        "PYTHONPATH": "/workspace/service/src:/workspace/service",
        "PYTEST_ADDOPTS": "--asyncio-mode=auto",
    }
    backend = DockerWorkspaceBackend(
        tmp_path, image="fixture", audit=None, random_seed=21,
        sandbox_environment=settings,
    )
    argv = backend._docker_environment_args()
    live = dict(item.split("=", 1) for item in argv[1::2])
    assert live["DJANGO_SETTINGS_MODULE"] == settings["DJANGO_SETTINGS_MODULE"]
    assert live["PYTHONHASHSEED"] == live["BELIEFKV_SANDBOX_RANDOM_SEED"] == "21"
    assert live["PYTHONPATH"].startswith("/beliefkv-support:")
    calls = []
    monkeypatch.setattr(environment, "capture", lambda args: (
        calls.append(args) or {"returncode": 0}
    ))
    monkeypatch.setattr(environment.subprocess, "run", lambda *_a, **_k: None)
    environment.run_probe(
        str(tmp_path), "fixture", "python -m pytest", junit=True,
        sandbox_environment=settings,
    )
    args = calls[0]
    actual = dict(
        args[index + 1].split("=", 1)
        for index, value in enumerate(args[:-1]) if value == "-e"
    )
    assert actual["PYTHONPATH"] == live["PYTHONPATH"]
    assert actual["DJANGO_SETTINGS_MODULE"] == live["DJANGO_SETTINGS_MODULE"]
    assert "--asyncio-mode=auto" in actual["PYTEST_ADDOPTS"]
    assert actual["PYTEST_ADDOPTS"] == settings["PYTEST_ADDOPTS"]
    assert "--junitxml=/beliefkv-results/pytest.xml" in args[-1]
    assert project_environment(settings) == settings


@pytest.mark.parametrize("key", [
    "PATH", "HOME", "CUDA_VISIBLE_DEVICES", "PYTHONHASHSEED", "PYTEST_PLUGINS",
])
def test_project_settings_cannot_override_runtime_or_randomness_controls(key):
    from beliefkv.experiments.sandbox_environment import project_environment

    with pytest.raises(ValueError, match="unsupported project sandbox environment"):
        project_environment({key: "changed"})


@pytest.mark.parametrize(("output", "exit_code", "healthy"), [
    ("1 passed in 0.4s", 0, True),
    ("1 failed, 2 passed in 0.7s\n[Command failed with exit code 1]", 1, True),
    (
        "ERROR collecting tests/test_one.py\n"
        "E   ModuleNotFoundError: No module named 'application'\n1 error in 0.3s",
        2, False,
    ),
    (
        "E   django.core.exceptions.ImproperlyConfigured: settings are not configured\n"
        "1 failed in 0.3s",
        1, False,
    ),
])
def test_every_sandbox_test_is_audited_and_unhealthy_source_is_rejected_after_completion(
    tmp_path, monkeypatch, output, exit_code, healthy,
):
    from types import SimpleNamespace as NS
    from beliefkv.experiments import deepagents_swebench as harness

    audit_path = tmp_path / "sandbox.jsonl"
    audit = harness.JsonlAudit(audit_path)
    backend = harness.DockerWorkspaceBackend(
        tmp_path, image="fixture", audit=audit, support_dir=None,
    )
    backend._started = True
    monkeypatch.setattr(harness.subprocess, "run", lambda *_a, **_k: NS(
        stdout=output, returncode=exit_code,
    ))
    # The shared backend is also used by children; no root transcript is needed.
    result = backend.execute("python -m pytest tests/test_one.py -q")
    audit.close()
    assert result.exit_code == exit_code
    evidence = json.loads(audit_path.read_text())["test_execution"]
    control = harness.summarize_agent_control(audit_path)
    assert control["test_command_count"] == 1
    assert control["test_environment_failures"] == int(not healthy)
    assert control["executed_test_calls"] == evidence["reported_test_calls"]
    measurement = harness.classify_workflow_measurement(
        outcome="completed", error=None, semantic_completion=None, natural_terminal=True,
        agent_control=control, control_delivery={"degraded": False},
        require_healthy_tests=True,
        trace={
            "workflow_lifecycle_valid": True, "llm_pairing_valid": True,
            "tool_pairing_valid": True, "tool_status_coverage": 1.,
            "workspace_digest_coverage": 1., "dynamic_subagent_count": 0,
        },
    )
    assert measurement["system_jct_eligible"] is healthy
    if not healthy:
        assert "test_environment_failure" in measurement["system_jct_exclusion_reasons"]
