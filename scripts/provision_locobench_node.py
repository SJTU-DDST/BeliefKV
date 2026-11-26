"""Prepare the selected LoCoBench Node dependencies from their original manifests."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_locobench_agent_manifest import sha256_file, write_json
from scripts.provision_locobench_python import image_id
from scripts.provision_original_node_projects import dependency_link, install
from scripts.qualify_locobench_tools import qualify


def node_packages(source, audit):
    packages, blocked = [], {}
    for relative, digest in audit["setup_file_sha256"].items():
        path = source / relative
        if path.name != "package.json":
            continue
        if sha256_file(path) != digest:
            raise ValueError(f"original package declaration changed: {path}")
        try:
            package = json.loads(path.read_bytes())
        except json.JSONDecodeError as error:
            blocked[relative] = f"invalid original JSON: {error}"
            continue
        dependencies = {
            **package.get("dependencies", {}), **package.get("devDependencies", {}),
            **package.get("optionalDependencies", {}),
        }
        if package.get("workspaces") or any(
            str(version).startswith(("workspace:", "file:", "link:"))
            for version in dependencies.values()
        ):
            blocked[relative] = "original local workspace dependencies need workspace resolution"
            continue
        packages.append(path.parent.relative_to(source))
    return packages, blocked


def package_checks(directories):
    return [
        "cd " + shlex.quote(f"/workspace/{directory.as_posix()}")
        + " && npm ls --depth=0 --json"
        for directory in directories
    ]


def prepare(row, audit, output, cache, timeout_s):
    project = row["repo"].split("/", 1)[1]
    source = Path(row["source_repo"])
    directories, blocked = node_packages(source, audit)
    result = {"project": row["repo"], "blocked_declarations": blocked}
    image = image_id(row["docker_image"])
    if image != row["docker_image"]:
        raise ValueError("expected an immutable original Node image")
    if "/opt/beliefkv-node/" in row["preflight_command"]:
        result["existing"] = qualify(row, image, project_commands=package_checks(directories))
        if result["existing"].get("project_commands_passed"):
            return result, row
    if not directories:
        return result, row
    here = output / project
    here.mkdir()
    result["provisioning"] = install(
        row, directories, here, timeout_s=timeout_s, dependency_only=True, cache=cache,
        use_original_lockfile=True, legacy_peer_dependencies=False,
    )
    packages = result["provisioning"]["packages"]
    if not packages:
        return result, row
    base = f"beliefkv/locobench-tools-base:{image.removeprefix('sha256:')[:16]}"
    subprocess.run(["docker", "tag", image, base], check=True, timeout=20, capture_output=True)
    tag = f"beliefkv/locobench-env-{project}:{output.name}"
    with (here / "build.log").open("xb") as log:
        subprocess.run([
            "docker", "build", "--network", "none",
            "--file", str(ROOT / "configs/docker/locobench_node_dependencies_20261030.Dockerfile"),
            "--build-arg", f"BASE_IMAGE={base}", "--tag", tag, str(here / "image"),
        ], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    updated = {**row, "docker_image": image_id(tag)}
    links = [
        dependency_link(package["runtime_directory"], "/workspace/" + package["directory"]
                        + "/node_modules") for package in packages
    ]
    updated["preflight_command"] = " && ".join([row["preflight_command"], *links])
    result["qualification"] = qualify(updated, updated["docker_image"], project_commands=package_checks(
        [Path(package["directory"]) for package in packages],
    ))
    if not (
        result["qualification"]["tool_execution_passed"]
        and result["qualification"]["project_commands_passed"]
    ):
        return result, row
    updated["sandbox_environment"] = result["qualification"]["sandbox_environment"]
    return result, updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--npm-cache", type=Path)
    parser.add_argument("--project", action="append", default=[])
    args = parser.parse_args()
    audit = json.loads((args.inventory / "audit.json").read_bytes())
    original_path = args.inventory / "original_manifest.json"
    if sha256_file(original_path) != audit["original_manifest_sha256"]:
        raise ValueError("original task manifest changed")
    original = json.loads(original_path.read_bytes())
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = args.npm_cache.resolve() if args.npm_cache else output / "npm-cache"
    cache.mkdir(parents=True, exist_ok=True)

    def run(row):
        project = row["repo"].split("/", 1)[1]
        try:
            result, updated = prepare(row, audit["projects"][row["repo"]], output, cache, args.timeout_s)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result, updated = {"project": row["repo"], "error": repr(error)}, row
        write_json(output / project / "result.json", result)
        print(json.dumps({
            "project": row["repo"], "updated_image": updated["docker_image"] if updated is not row else None,
            "legacy_verified": result.get("existing", {}).get("project_commands_passed"),
            "resolver_passed": result.get("provisioning", {}).get("installed"),
            "blocked_declarations": len(result.get("blocked_declarations", {})),
            "error": result.get("error"),
        }), flush=True)
        return result, updated

    rows = [row for row in original["workloads"]
            if row["repo"].startswith(("locobench/javascript_", "locobench/typescript_"))]
    if args.project:
        unknown = set(args.project) - {row["repo"].split("/", 1)[1] for row in rows}
        if unknown:
            raise ValueError(f"projects absent from the inventory: {sorted(unknown)}")
        rows = [row for row in rows if row["repo"].split("/", 1)[1] in args.project]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(run, rows))
    updates = {row["instance_id"]: row for _, row in results}
    prepared = deepcopy(original)
    prepared["workloads"] = [updates.get(row["instance_id"], row) for row in original["workloads"]]
    write_json(output / "prepared_manifest.json", prepared)
    write_json(output / "report.json", {
        "original_manifest_sha256": sha256_file(original_path),
        "audit_sha256": sha256_file(args.inventory / "audit.json"),
        "projects": [result for result, _ in results],
    })


if __name__ == "__main__":
    main()
