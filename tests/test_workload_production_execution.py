import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import qualify_locobench_environment as environment


SUPPORT = Path(environment.SUPPORT_DIR)


@pytest.mark.parametrize(("body", "reaches"), [
    (
        "from application import price\n"
        "def test_real():\n    assert price(4) == 8\n",
        True,
    ),
    (
        "class Domain:\n    def price(self, value):\n        return value * 2\n"
        "def test_fallback():\n    assert Domain().price(4) == 8\n",
        False,
    ),
    (
        "import application\n"
        "def test_import_only():\n    assert True\n",
        False,
    ),
    (
        "import pytest\nfrom application import price\n"
        "@pytest.fixture\ndef expected():\n    return price(4)\n"
        "def test_fixture_only(expected):\n    assert expected == 8\n",
        False,
    ),
    (
        "import pytest\nfrom queue import Queue\nfrom threading import Thread\n"
        "from application import price\n"
        "@pytest.fixture\ndef worker():\n"
        "    incoming, outgoing = Queue(), Queue()\n"
        "    def work():\n"
        "        item = incoming.get()\n"
        "        outgoing.put(price(item))\n"
        "    thread = Thread(target=work)\n"
        "    thread.start()\n"
        "    yield incoming, outgoing\n"
        "    thread.join(timeout=2)\n"
        "def test_thread(worker):\n"
        "    incoming, outgoing = worker\n"
        "    incoming.put(4)\n"
        "    assert outgoing.get(timeout=2) == 8\n",
        True,
    ),
])
def test_application_call_proof_excludes_import_fixture_and_test_local_stubs(
    tmp_path, body, reaches,
):
    (tmp_path / "application.py").write_text("def price(value):\n    return value * 2\n")
    test = tmp_path / "test_application.py"
    test.write_text(body)
    report, junit = tmp_path / "production.json", tmp_path / "junit.xml"
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-c", os.devnull,
        "-p", "no:cacheprovider", "-p", "beliefkv_production_probe",
        "--beliefkv-production-root=" + str(tmp_path),
        "--beliefkv-production-report=" + str(report),
        "--junitxml=" + str(junit), str(test),
    ], capture_output=True, text=True, timeout=20, env={
        **os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "",
        "PYTHONPATH": str(SUPPORT) + ":" + str(tmp_path),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(report.read_bytes())
    evidence = environment.production_execution_evidence({
        "returncode": result.returncode, "junit_xml": junit.read_text(),
        "production_calls": observed,
    })
    assert evidence["qualified"] is reaches
    assert evidence["production_files"] == (["application.py"] if reaches else [])
    assert observed["duration_is_not_normal_tool_timing"] is True


def test_probe_activation_and_output_are_absent_from_normal_tool_commands(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(environment, "capture", lambda args: (
        calls.append(args) or {"returncode": 0}
    ))
    monkeypatch.setattr(environment.subprocess, "run", lambda *_a, **_k: None)
    environment.run_probe(str(tmp_path), "fixture", "python -m pytest", junit=True)
    assert not any("production" in arg for arg in calls[-1])
    environment.run_probe(
        str(tmp_path), "fixture", "python -m pytest", production_calls=True,
    )
    assert any("-p beliefkv_production_probe" in arg for arg in calls[-1])


def test_missing_production_proof_is_not_a_qualified_application():
    assert environment.production_execution_evidence({
        "returncode": 0,
    }) == {"qualified": False, "reasons": ["missing_production_call_evidence"]}


def test_pinning_rechecks_application_evidence_instead_of_trusting_success_flags(tmp_path):
    manifest = {"workloads": [{
        "source_repo": "/source", "base_commit": "original",
        "docker_image": "old", "test_command": "python -m pytest",
    }]}
    tests = {
        "returncode": 0, "junit_xml": '<testsuite><testcase name="test_one"/></testsuite>',
    }
    report = {
        "dependency_preflight_passed": True, "test_execution_passed": True,
        "production_execution_required": True, "production_execution_passed": True,
        "manifest": "/manifest.json", "manifest_sha256": "b" * 64,
        "projects": {"/source": {
            "source_commit": "original", "docker_image_id": "sha256:" + "a" * 64,
            "original_test_command": "python -m pytest",
            "original_test_result": tests, "test_execution": {"qualified": True},
            "production_execution": {"qualified": True},
            "production_probe_result": {
                **tests, "production_calls": {
                    "observation": "first_party_python_calls_during_pytest_call_phase",
                    "call_events": 0, "test_cases_reaching_production_count": 0,
                    "production_files": [],
                    "main_and_future_thread_profile_restored": True,
                },
            },
        }},
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="environment evidence"):
        environment.pin_environment(manifest, report, path)


@pytest.mark.skipif(
    os.environ.get("BELIEFKV_TEST_SANDBOX_DOCKER") != "1",
    reason="requires the explicit owned Docker fixture",
)
def test_real_readonly_docker_probe_distinguishes_application_from_local_fallback(tmp_path):
    image = os.environ["BELIEFKV_TEST_SANDBOX_IMAGE"]
    for kind, body in (
        ("production", "from app import price\ndef test_price():\n    assert price(4) == 8\n"),
        ("fallback", "def test_price():\n    price = lambda value: value * 2\n    assert price(4) == 8\n"),
    ):
        source = tmp_path / kind
        source.mkdir()
        (source / "app.py").write_text("def price(value):\n    return value * 2\n")
        (source / "test_app.py").write_text(body)
        result = environment.run_probe(
            str(source), image, "python -m pytest -q", production_calls=True,
        )
        evidence = environment.production_execution_evidence(result)
        assert evidence["qualified"] is (kind == "production"), (result, evidence)


@pytest.mark.skipif(
    os.environ.get("BELIEFKV_TEST_SANDBOX_DOCKER") != "1",
    reason="requires the explicit owned Docker fixture",
)
def test_real_probe_preserves_async_image_configuration_and_private_runtime_writes(tmp_path):
    image = os.environ["BELIEFKV_TEST_SANDBOX_IMAGE"]
    (tmp_path / "app.py").write_text(
        "from pathlib import Path\n"
        "def persist(value):\n"
        "    cache = Path('/workspace/runtime/cache')\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    path = cache / 'value.txt'\n"
        "    path.write_text(str(value))\n"
        "    return int(path.read_text())\n"
    )
    for expected, returncode in ((2, 0), (3, 1)):
        (tmp_path / "test_project.py").write_text(
            "import pytest\nfrom app import persist\n"
            "@pytest.fixture\nasync def value():\n    return 2\n"
            "@pytest.mark.asyncio\nasync def test_runtime(value):\n"
            f"    assert persist(value) == {expected}\n"
        )
        result = environment.run_probe(
            str(tmp_path), image, "python -m pytest -q", production_calls=True,
        )
        assert result["returncode"] == returncode, result
        assert environment.test_execution_evidence(result)["qualified"]
        assert environment.production_execution_evidence(result)["qualified"]
        assert not (tmp_path / "runtime").exists()
