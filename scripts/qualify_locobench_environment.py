#!/usr/bin/env python3
"""CPU-only sandbox qualification, retaining original project test failures."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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


def pin_environment(manifest, report, report_path):
    if report.get("dependency_preflight_passed") is not True:
        raise ValueError("cannot pin an environment whose preflight failed")
    expected = {row["source_repo"] for row in manifest["workloads"]}
    if expected != report["projects"].keys():
        raise ValueError("environment evidence does not cover the complete workload")
    pinned = deepcopy(manifest)
    for row in pinned["workloads"]:
        evidence = report["projects"][row["source_repo"]]
        image = evidence["docker_image_id"]
        if (
            evidence["source_commit"] != row["base_commit"]
            or type(image) is not str or len(image) != 71 or not image.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in image[7:])
        ):
            raise ValueError("environment evidence needs the original commit and immutable image ID")
        row["docker_image"] = image
    pinned["environment_qualification"] = {
        "path": str(report_path.resolve()),
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "base_manifest": report["manifest"],
        "base_manifest_sha256": report["manifest_sha256"],
        "task_correctness": "ungraded",
    }
    return pinned


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
    parser.add_argument("--image")
    parser.add_argument("--image-override", action="append", default=[], metavar="PROJECT=IMAGE")
    parser.add_argument("--pin-manifest", type=Path)
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
        prefix = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{source}:/workspace:ro", "-w", "/workspace",
            "-e", "PATH=/opt/miniconda3/envs/testbed/bin:/usr/bin:/bin",
            "-e", "PIP_NO_INDEX=1",
            "-e", "PYTHONPATH=/workspace/src:/workspace",
            "-e", "HOME=/tmp",
            image_id,
        ]
        preflight = capture(prefix + ["bash", "-c", row["preflight_command"]])
        command = PROJECT_COMMANDS.get(Path(source).name, DEFAULT_TEST_COMMAND)
        baseline = capture(prefix + ["bash", "-c", command])
        return source, {
            "source_commit": row["base_commit"], "docker_image": image,
            "docker_image_id": image_id,
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
        "default_image_override": args.image, "project_image_overrides": overrides,
        "cpu_only": True, "projects": results,
        "dependency_preflight_passed": all(
            row["dependency_preflight"]["returncode"] == 0 for row in results.values()
        ),
        "task_correctness": "ungraded", "gpu_workload_acceptance": "pending",
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if args.pin_manifest is not None and report["dependency_preflight_passed"]:
        pinned = pin_environment(manifest, report, args.output)
        args.pin_manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.pin_manifest.open("x") as stream:
            json.dump(pinned, stream, indent=2, allow_nan=False)
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
