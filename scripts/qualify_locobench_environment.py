#!/usr/bin/env python3
"""CPU-only sandbox qualification, retaining original project test failures."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess


PROJECT_COMMANDS = {
    "python_api_gateway_hard_009": (
        "cd edubridge-gateway && python -m pytest --collect-only -q tests/unit"
    ),
    "python_web_cms_hard_074": (
        "cd mediverse_cms && python -m pytest --collect-only -q apps/wellness/tests"
    ),
    "python_game_simulation_medium_033": (
        "cd physage_academy && python -m pytest --collect-only -q tests"
    ),
    "python_system_networking_hard_027": (
        "cd stream_carnival/services/orchestrator-service && "
        "python -m pytest -q app/tests/test_carnival_logic.py --maxfail=2"
    ),
    "python_system_automation_hard_062": (
        "python -m pytest --collect-only -q tests/test_utils.py"
    ),
    "python_mobile_game_hard_060": (
        "python -m pytest -q tests/test_utils.py --maxfail=2"
    ),
}

DEFAULT_TEST_COMMAND = "python -m pytest --collect-only -q"


def capture(args, **kwargs):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-24000:], "stderr": result.stderr[-12000:],
        }
    except subprocess.TimeoutExpired as error:
        return {"timeout": True, "returncode": None, "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing environment evidence")
    manifest = json.loads(args.manifest.read_text())
    projects = {row["source_repo"]: row for row in manifest["workloads"]}

    def qualify(item):
        source, row = item
        image = row["docker_image"]
        prefix = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{source}:/workspace:ro", "-w", "/workspace",
            "-e", "PATH=/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin",
            "-e", "PIP_NO_INDEX=1",
            "-e", "PYTHONPATH=/workspace/src:/workspace",
            "-e", "HOME=/tmp",
            image,
        ]
        preflight = capture(prefix + ["bash", "-c", row["preflight_command"]])
        command = PROJECT_COMMANDS.get(Path(source).name, DEFAULT_TEST_COMMAND)
        baseline = capture(prefix + ["bash", "-c", command])
        return source, {
            "source_commit": row["base_commit"], "docker_image": image,
            "docker_image_id": subprocess.check_output(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                text=True,
            ).strip(),
            "dependency_preflight": preflight,
            "original_test_command": command, "original_test_result": baseline,
            "test_command_selection": (
                "declared_project_probe" if Path(source).name in PROJECT_COMMANDS
                else "unmodified_project_pytest_collection"
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
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "cpu_only": True, "projects": results,
        "dependency_preflight_passed": all(
            row["dependency_preflight"]["returncode"] == 0 for row in results.values()
        ),
        "task_correctness": "ungraded", "gpu_workload_acceptance": "pending",
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "report": str(args.output),
        "dependency_preflight_passed": report["dependency_preflight_passed"],
        "baseline_test_exit_codes": {
            Path(source).name: row["original_test_result"]["returncode"]
            for source, row in results.items()
        },
    }, indent=2))
    if not report["dependency_preflight_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
