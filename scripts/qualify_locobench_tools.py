#!/usr/bin/env python3
"""Check live root/child tools without repairing or qualifying project tests."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.deepagents_swebench import (
    DockerWorkspaceBackend, JsonlAudit, SweBenchWorkload, prepare_workspace,
)
from beliefkv.experiments.source_provenance import sha256_file


def package_paths(source):
    roots = [source, source / "src"]
    for path in sorted(source.rglob("__init__.py")):
        if not path.is_file() or any(part in (
            ".git", "tests", "test", "__pycache__",
        ) for part in path.relative_to(source).parts):
            continue
        package = path.parent
        while package != source and (package / "__init__.py").is_file():
            package = package.parent
        roots.append(package)
    return ":".join(dict.fromkeys(
        "/workspace" + ("/" + path.relative_to(source).as_posix() if path != source else "")
        for path in roots if path.is_dir()
    ))


def tool_command(role):
    payload = (
        "import importlib.metadata as m, json, os; from pathlib import Path; "
        "assert Path.cwd() == Path('/workspace'); "
        "p=Path('.beliefkv-tool-check'); p.mkdir(exist_ok=True); "
        + (
            "(p/'shared.txt').write_text('root'); "
            if role == "root" else
            "assert (p/'shared.txt').read_text() == 'root'; "
            "(p/'shared.txt').write_text('child'); "
        )
        + "print(json.dumps({'cwd':str(Path.cwd()),'pytest':m.version('pytest'),"
        "'pythonpath':os.environ.get('PYTHONPATH'),"
        "'hashseed':os.environ.get('PYTHONHASHSEED')}))"
    )
    return (
        "command -v bash git rg patch >/dev/null && "
        "python -m pytest --version && python -c " + shlex.quote(payload)
    )


def qualify(row, image):
    source = Path(row["source_repo"])
    settings = {
        "PYTHONPATH": package_paths(source),
        "QT_QPA_PLATFORM": "offscreen", "QT_OPENGL": "software", "MPLBACKEND": "Agg",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    evidence = {
        "source_commit": row["base_commit"], "docker_image_id": image,
        "sandbox_environment": settings, "roles": {}, "tool_execution_passed": False,
        "project_source_and_tests_modified": False, "test_results_required": False,
    }
    with tempfile.TemporaryDirectory(prefix="beliefkv-tool-check-") as directory:
        workspace = Path(directory) / "workspace"
        workload = SweBenchWorkload(
            row["instance_id"], row["repo"], row["base_commit"],
            row["problem_statement"], row["difficulty"],
        )
        prepare_workspace(source, workload, workspace)
        backends = []
        try:
            for role in ("root", "child"):
                backend = DockerWorkspaceBackend(
                    workspace, image=image, audit=JsonlAudit(Path(directory) / f"{role}.jsonl"),
                    preflight_command=row["preflight_command"], random_seed=21,
                    sandbox_environment=settings,
                )
                backends.append(backend)
                backend.start()
                result = backend.execute(tool_command(role))
                failed_command = backend.execute("exit 7")
                evidence["roles"][role] = {
                    "exit_code": result.exit_code, "output": result.output,
                    "nonzero_exit_returned": failed_command.exit_code == 7,
                    "docker_environment_args": backend._docker_environment_args(),
                }
            handoff = backends[0].execute("test \"$(cat .beliefkv-tool-check/shared.txt)\" = child")
            evidence["shared_workspace_handoff_passed"] = handoff.exit_code == 0
        finally:
            for backend in reversed(backends):
                backend.close()
            evidence["cleanup"] = [backend.cleanup_status for backend in backends]
    roles = evidence["roles"]
    evidence["tool_execution_passed"] = (
        set(roles) == {"root", "child"}
        and all(item["exit_code"] == 0 and item["nonzero_exit_returned"]
                for item in roles.values())
        and roles["root"]["docker_environment_args"] == roles["child"]["docker_environment_args"]
        and evidence.get("shared_workspace_handoff_passed") is True
        and evidence["cleanup"] == ["completed", "completed"]
    )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists() or args.output.exists():
        parser.error("preserve existing reports and workload manifests")
    manifest = json.loads(args.manifest.read_bytes())
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True,
    ).strip()

    def check(row):
        result = qualify(row, image)
        print(json.dumps({"project": row["repo"], "tools": result["tool_execution_passed"]}),
              flush=True)
        return row["source_repo"], result

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = dict(executor.map(check, manifest["workloads"]))
    report = {
        "schema_version": 1, "cpu_only": True, "qualification_scope": "live_root_child_tools",
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256_file(args.manifest), "projects": results,
        "tool_execution_passed": all(row["tool_execution_passed"] for row in results.values()),
        "task_tests_required": False, "task_correctness": "agent_responsibility",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    if not report["tool_execution_passed"]:
        raise SystemExit("tool infrastructure check failed; see report")
    pinned = deepcopy(manifest)
    pinned.pop("environment_qualification", None)
    for row in pinned["workloads"]:
        row["docker_image"] = image
        row["sandbox_environment"] = results[row["source_repo"]]["sandbox_environment"]
        row.pop("test_command", None)
        row.pop("test_entrypoint_scope", None)
    pinned["tool_environment_qualification"] = {
        "path": str(args.report.resolve()), "sha256": sha256_file(args.report),
        "base_manifest": str(args.manifest.resolve()),
        "base_manifest_sha256": sha256_file(args.manifest),
        "qualification_scope": "live_root_child_tools", "task_tests_required": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(pinned, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"qualified_projects": len(results), "manifest": str(args.output)}))


if __name__ == "__main__":
    main()
