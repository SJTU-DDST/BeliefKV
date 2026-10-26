#!/usr/bin/env python3
"""CPU-only sandbox qualification, retaining original project test failures."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beliefkv.experiments.sandbox_environment import (
    merge_project_environment, project_environment,
)


PROJECT_COMMANDS = {
    "python_api_gateway_hard_009": (
        "cd edubridge-gateway && python -m pytest -q tests/unit"
    ),
    "python_web_cms_hard_074": (
        "cd mediverse_cms && python -m pytest -q apps/wellness/tests"
    ),
    "python_game_simulation_medium_033": (
        "cd physage_academy && python -m pytest -q tests"
    ),
    "python_system_networking_hard_027": (
        "cd stream_carnival/services/orchestrator-service && "
        "python -m pytest -q app/tests/test_carnival_logic.py --maxfail=2"
    ),
    "python_system_automation_hard_062": (
        "python -m pytest -q tests/test_utils.py"
    ),
    "python_mobile_game_hard_060": (
        "python -m pytest -q tests/test_utils.py --maxfail=2"
    ),
}

DEFAULT_TEST_COMMAND = "python -m pytest -q"
SUPPORT_DIR = Path(__file__).resolve().parents[1] / "beliefkv/experiments/sandbox_support"


def test_execution_evidence(result):
    """Accept assertion failures, but never collection, fixture, or empty runs."""
    reasons = []
    xml = result.get("junit_xml")
    if not xml:
        return {"qualified": False, "reasons": ["missing_junit_execution_evidence"]}
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError):
        return {"qualified": False, "reasons": ["invalid_junit_execution_evidence"]}
    cases = list(root.iter("testcase"))
    errors = sum(len(case.findall("error")) for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    executed = [
        case for case in cases
        if case.find("error") is None and case.find("skipped") is None
    ]
    failed = sum(case.find("failure") is not None for case in executed)
    infrastructure_failures = sum(
        bool(re.match(
            r"^(?:ModuleNotFoundError|ImportError|"
            r"(?:django\.core\.exceptions\.)?ImproperlyConfigured)\b",
            failure.get("message", ""),
        ))
        for case in executed for failure in case.findall("failure")
    )
    resource_failures = sum(
        bool(re.match(
            r"^(?:(?:[\w]+\.)*(?:FileNotFoundError|PermissionError|ConnectionError|"
            r"ConnectionRefusedError|URLError|ConnectTimeout|ReadTimeout|"
            r"ProxyError|SSLError)\b|"
            r"ValueError: found the following matches with the input file in "
            r"xarray's IO backends:)",
            failure.get("message", ""),
        ))
        for case in executed for failure in case.findall("failure")
    )
    if root.tag not in ("testsuites", "testsuite"):
        reasons.append("unexpected_junit_root")
    if list(root.iter("error")):
        reasons.append("collection_setup_or_teardown_error")
    if not executed:
        reasons.append("no_executed_test_calls")
    if infrastructure_failures:
        reasons.append("import_or_configuration_failure_inside_test_call")
    if resource_failures:
        reasons.append("unreviewed_external_resource_or_optional_backend_failure")
    if (
        type(result.get("returncode")) is not int
        or result["returncode"] not in (0, 1) or result.get("timeout")
    ):
        reasons.append("test_runner_did_not_finish")
    return {
        "qualified": not reasons, "reasons": reasons,
        "testcases": len(cases), "executed_test_calls_lower_bound": len(executed),
        "passed": len(executed) - failed, "assertion_or_call_failures": failed,
        "collection_setup_or_teardown_errors": errors, "skipped": skipped,
        "import_or_configuration_call_failures": infrastructure_failures,
        "unreviewed_resource_or_optional_backend_call_failures": resource_failures,
    }


def production_execution_evidence(result):
    report = result.get("production_calls")
    reasons = []
    if not isinstance(report, dict):
        return {"qualified": False, "reasons": ["missing_production_call_evidence"]}
    if report.get("observation") != "first_party_python_calls_during_pytest_call_phase":
        reasons.append("unsupported_production_observation")
    if (
        type(report.get("call_events")) is not int or report["call_events"] <= 0
        or type(report.get("test_cases_reaching_production_count")) is not int
        or report["test_cases_reaching_production_count"] <= 0
        or not report.get("production_files")
    ):
        reasons.append("no_test_calls_reached_project_implementation")
    if report.get("main_and_future_thread_profile_restored") is not True:
        reasons.append("production_probe_incomplete")
    if not test_execution_evidence(result)["qualified"]:
        reasons.append("instrumented_test_environment_not_qualified")
    return {
        "qualified": not reasons, "reasons": reasons,
        "test_cases_reaching_production": report.get("test_cases_reaching_production_count", 0),
        "production_files": report.get("production_files", []),
        "call_events": report.get("call_events", 0),
        "instrumented_duration_used_as_tool_time": False,
    }


def pin_environment(manifest, report, report_path):
    if report.get("dependency_preflight_passed") is not True:
        raise ValueError("cannot pin an environment whose preflight failed")
    if report.get("test_execution_passed") is not True:
        raise ValueError("cannot pin an environment without actual test execution")
    production_required = report.get("production_execution_required", False)
    if production_required and report.get("production_execution_passed") is not True:
        raise ValueError("cannot pin an environment without actual project execution")
    expected = {row["source_repo"] for row in manifest["workloads"]}
    if expected != report["projects"].keys():
        raise ValueError("environment evidence does not cover the complete workload")
    pinned = deepcopy(manifest)
    for row in pinned["workloads"]:
        evidence = report["projects"][row["source_repo"]]
        image = evidence["docker_image_id"]
        if (
            evidence["source_commit"] != row["base_commit"]
            or evidence.get("test_execution", {}).get("qualified") is not True
            or test_execution_evidence(evidence.get("original_test_result", {}))[
                "qualified"
            ] is not True
            or evidence.get("sandbox_environment", {}) != project_environment(
                row.get("sandbox_environment"),
            )
            or production_required and (
                evidence.get("production_execution", {}).get("qualified") is not True
                or not production_execution_evidence(
                    evidence.get("production_probe_result", {}),
                )["qualified"]
            )
            or type(image) is not str or len(image) != 71 or not image.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in image[7:])
        ):
            raise ValueError("environment evidence needs the original commit and immutable image ID")
        row["docker_image"] = image
        row["test_command"] = evidence["original_test_command"]
    pinned["environment_qualification"] = {
        "path": str(report_path.resolve()),
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "base_manifest": report["manifest"],
        "base_manifest_sha256": report["manifest_sha256"],
        "task_correctness": "ungraded",
        "test_execution_required": True,
        "production_execution_required": production_required,
    }
    return pinned


def capture(args, **kwargs):
    started = time.monotonic()
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-24000:], "stderr": result.stderr[-12000:],
            "duration_ms": (time.monotonic() - started) * 1000.,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "timeout": True, "returncode": None, "error": str(error),
            "duration_ms": (time.monotonic() - started) * 1000.,
        }


def run_probe(
    source, image, command, *, junit=False, sandbox_environment=None,
    production_calls=False,
):
    """Use a private writable checkout, retaining the live pytest environment."""
    name = "beliefkv-envtest-" + uuid4().hex
    with tempfile.TemporaryDirectory(prefix="beliefkv-envtest-") as directory:
        workspace = Path(directory) / "workspace"
        shutil.copytree(source, workspace, symlinks=True)
        prefix = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--cpus", "2", "--memory", "6g",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{workspace}:/workspace", "-w", "/workspace",
            "-v", f"{SUPPORT_DIR}:/beliefkv-support:ro",
            "-e", "PATH=/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin",
            "-e", "PIP_NO_INDEX=1",
            "-e", "PYTHONPATH=/beliefkv-support:/workspace/src:/workspace",
            "-e", "HOME=/tmp", "-e", "PYTHONNOUSERSITE=1",
            "-e", "PYTHONHASHSEED=21", "-e", "BELIEFKV_SANDBOX_RANDOM_SEED=21",
            "-e", "DJANGO_TEST_PROCESSES=2",
            "-e", "HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis",
        ]
        if junit or production_calls:
            prefix.extend([
                "-v", f"{directory}:/beliefkv-results",
            ])
        settings = project_environment(sandbox_environment)
        if "PYTHONPATH" in settings:
            settings["PYTHONPATH"] = merge_project_environment(
                {"PYTHONPATH": "/beliefkv-support:/workspace/src:/workspace"}, settings,
            )["PYTHONPATH"]
        options = ["-p no:cacheprovider", "--junitxml=/beliefkv-results/pytest.xml"]
        if production_calls:
            options.extend((
                "-p beliefkv_production_probe",
                "--beliefkv-production-report=/beliefkv-results/production.json",
            ))
        for key, value in settings.items():
            prefix.extend(("-e", f"{key}={value}"))
        probe_command = command
        if junit or production_calls:
            probe_command = (
                'env PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }'
                + " ".join(options) + '" bash -c ' + shlex.quote(command)
            )
        result = None
        try:
            result = capture(prefix + [image, "bash", "-c", probe_command])
            result["workspace_isolation"] = "private_writable_copy_original_unchanged"
            result["pytest_image_and_project_options_preserved"] = True
            path = Path(directory) / "pytest.xml"
            if (junit or production_calls) and path.is_file():
                result["junit_xml"] = path.read_text()
            profile = Path(directory) / "production.json"
            if production_calls and profile.is_file():
                result["production_calls"] = json.loads(profile.read_bytes())
            return result
        finally:
            try:
                cleanup = subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=30,
                    check=False,
                )
                if result is not None and getattr(cleanup, "returncode", 0):
                    result["cleanup_error"] = cleanup.stderr[-2000:]
            except subprocess.TimeoutExpired:
                if result is not None:
                    result["cleanup_error"] = "owned container removal timed out"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image")
    parser.add_argument("--image-override", action="append", default=[], metavar="PROJECT=IMAGE")
    parser.add_argument("--pin-manifest", type=Path)
    parser.add_argument("--verify-production-execution", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing environment evidence")
    if args.pin_manifest is not None and (
        args.pin_manifest.exists() or args.pin_manifest.resolve() == args.output.resolve()
    ):
        parser.error("the pinned workload manifest must use a separate fresh path")
    manifest = json.loads(args.manifest.read_text())
    projects = {row["source_repo"]: row for row in manifest["workloads"]}
    overrides = {}
    known = {Path(source).name for source in projects}
    for entry in args.image_override:
        project, separator, image = entry.partition("=")
        if not separator or project not in known or not image or project in overrides:
            parser.error("image overrides require a unique known project and nonempty image")
        overrides[project] = image

    def qualify(item):
        source, row = item
        image = overrides.get(Path(source).name, args.image or row["docker_image"])
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            text=True,
        ).strip()
        settings = project_environment(row.get("sandbox_environment"))
        preflight = run_probe(
            source, image_id, row["preflight_command"], sandbox_environment=settings,
        )
        command = row.get("test_command") or PROJECT_COMMANDS.get(
            Path(source).name, DEFAULT_TEST_COMMAND,
        )
        baseline = run_probe(
            source, image_id, command, junit=True, sandbox_environment=settings,
        )
        production = (
            run_probe(
                source, image_id, command, junit=True,
                sandbox_environment=settings, production_calls=True,
            ) if args.verify_production_execution else None
        )
        return source, {
            "source_commit": row["base_commit"], "docker_image": image,
            "docker_image_id": image_id,
            "dependency_preflight": preflight,
            "original_test_command": command, "original_test_result": baseline,
            "test_execution": test_execution_evidence(baseline),
            "sandbox_environment": settings,
            **({
                "production_probe_result": production,
                "production_execution": production_execution_evidence(production),
            } if production is not None else {}),
            "test_command_selection": (
                "manifest_test_entry_point" if row.get("test_command") else
                "declared_project_probe" if Path(source).name in PROJECT_COMMANDS
                else "unmodified_project_pytest_execution"
            ),
            "original_source_modified": False,
            "interpretation": (
                "Original source/test failures are retained. Stub-based tests "
                "are not evidence that task production code or correctness passes."
            ),
        }

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = dict(executor.map(qualify, projects.items()))
    report = {
        "schema_version": 2, "manifest": str(args.manifest.resolve()),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "default_image_override": args.image, "project_image_overrides": overrides,
        "cpu_only": True, "projects": results,
        "dependency_preflight_passed": all(
            row["dependency_preflight"]["returncode"] == 0 for row in results.values()
        ),
        "test_execution_passed": all(
            row["test_execution"]["qualified"] for row in results.values()
        ),
        "production_execution_required": args.verify_production_execution,
        "production_execution_passed": (
            all(row["production_execution"]["qualified"] for row in results.values())
            if args.verify_production_execution else None
        ),
        "task_correctness": "ungraded", "gpu_workload_acceptance": "pending",
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        args.pin_manifest is not None and report["dependency_preflight_passed"]
        and report["test_execution_passed"]
        and (
            not args.verify_production_execution or report["production_execution_passed"]
        )
    ):
        pinned = pin_environment(manifest, report, args.output)
        args.pin_manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.pin_manifest.open("x") as stream:
            json.dump(pinned, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "report": str(args.output),
        "dependency_preflight_passed": report["dependency_preflight_passed"],
        "test_execution_passed": report["test_execution_passed"],
        "production_execution_passed": report["production_execution_passed"],
        "test_execution_failures": {
            Path(source).name: row["test_execution"]
            for source, row in results.items() if not row["test_execution"]["qualified"]
        },
        "baseline_test_exit_codes": {
            Path(source).name: row["original_test_result"]["returncode"]
            for source, row in results.items()
        },
    }, indent=2))
    if (
        not report["dependency_preflight_passed"] or not report["test_execution_passed"]
        or args.verify_production_execution and not report["production_execution_passed"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
