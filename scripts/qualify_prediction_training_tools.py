"""Qualify root/child tools while retaining each training project's pinned image."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_locobench_tools import qualify
from scripts.train_workflow_work import write


def project_images(tasks, inspect):
    images = {}
    if not tasks or len({row["repo"] for row in tasks}) != len(tasks):
        raise ValueError("tool checks require distinct original projects")
    for row in tasks:
        image = row["docker_image"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("training tools require each original project's immutable image")
        if image not in images:
            actual = inspect(image).strip()
            if actual != image:
                raise ValueError("the project's declared tool image identity changed")
            images[image] = actual
    return {row["repo"]: images[row["docker_image"]] for row in tasks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "report", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.report.exists() or args.output.exists() or not 1 <= args.workers <= 4:
        raise ValueError("preserve tool observations and use one to four CPU workers")
    paths = (
        args.manifest.resolve(), Path(__file__).resolve(), Path(qualify.__code__.co_filename).resolve(),
        ROOT / "beliefkv/experiments/deepagents_swebench.py",
    )
    pins = {str(path): sha256_file(path) for path in paths}
    manifest = json.loads(args.manifest.read_bytes())
    images = project_images(manifest["workloads"], lambda image: subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True,
    ))

    def check(row):
        try:
            result = qualify(row, images[row["repo"]])
        except Exception as error:
            result = {
                "tool_execution_passed": False, "docker_image_id": images[row["repo"]],
                "error": repr(error),
            }
        print(json.dumps({
            "project": row["repo"], "image": images[row["repo"]],
            "tools": result["tool_execution_passed"],
        }), flush=True)
        return row["source_repo"], result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = dict(executor.map(check, manifest["workloads"]))
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("training tasks or qualification source changed during their tool checks")
    report = {
        "schema_version": 1, "cpu_only": True, "qualification_scope": "live_root_child_tools",
        "manifest": str(args.manifest.resolve()), "manifest_sha256": sha256_file(args.manifest),
        "projects": results, "per_project_images": images,
        "original_per_project_images_preserved": True,
        "tool_execution_passed": all(row["tool_execution_passed"] for row in results.values()),
        "task_tests_required": False, "task_correctness": "agent_responsibility",
        "training_only": True, "GPU_action": False,
        "inputs_sha256": pins,
    }
    write(args.report, report)
    if not report["tool_execution_passed"]:
        raise SystemExit("root/child tool execution failed; preserve the per-project report")
    pinned = deepcopy(manifest)
    for row in pinned["workloads"]:
        result = results[row["source_repo"]]
        if result["docker_image_id"] != row["docker_image"]:
            raise ValueError("tool qualification must not replace a project's existing image")
        row["sandbox_environment"] = result["sandbox_environment"]
        row.pop("test_command", None)
        row.pop("test_entrypoint_scope", None)
    pinned["tool_environment_qualification"] = {
        "path": str(args.report.resolve()), "sha256": sha256_file(args.report),
        "base_manifest": str(args.manifest.resolve()), "base_manifest_sha256": sha256_file(args.manifest),
        "qualification_scope": "live_root_child_tools",
    }
    write(args.output, pinned)
    print(json.dumps({
        "qualified_projects": len(results), "original_images_preserved": True,
        "tests_not_repaired_or_required_to_pass": True, "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
