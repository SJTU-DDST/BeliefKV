"""Install original Node dependencies in private copies, leaving task files intact."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.deepagents_swebench import SweBenchWorkload, prepare_workspace
from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


HEAVY_DEPENDENCIES = frozenset((
    "@tensorflow/tfjs-node", "onnxruntime-node", "electron", "electron-builder", "electron-forge",
))


def dependency_link(source, destination):
    payload = (
        "const fs=require('node:fs');"
        "const [src,dst]=" + json.dumps([source, destination]) + ";"
        "if(!fs.statSync(src).isDirectory())throw Error('dependency directory missing');"
        "let old;try{old=fs.lstatSync(dst)}catch(e){if(e.code!=='ENOENT')throw e}"
        "if(old){if(!old.isSymbolicLink()||fs.readlinkSync(dst)!==src)"
        "throw Error('unexpected existing dependency path')}else fs.symlinkSync(src,dst,'dir');"
    )
    return "node -e " + shlex.quote(payload)


def packages_for(task):
    source = Path(task["source_repo"])
    packages = []
    for path in sorted(source.rglob("package.json")):
        if ".git" in path.relative_to(source).parts:
            continue
        package = json.loads(path.read_bytes())
        specs = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
            **package.get("optionalDependencies", {}),
        }
        if HEAVY_DEPENDENCIES.intersection(specs):
            return [], "native_ML_or_GUI_environment_outside_this_bounded_supplement"
        if package.get("workspaces") or any(
            str(version).startswith(("workspace:", "file:", "link:")) for version in specs.values()
        ):
            return [], "local_workspace_dependency_needs_a_separate_environment_binding"
        packages.append(path.relative_to(source).parent)
    return (packages, None) if packages else ([], "original_has_no_dependency_manifest")


def install(task, directories, here, *, timeout_s):
    project = task["repo"].split("/", 1)[1]
    workspace = here / "workspaces" / project
    workload = SweBenchWorkload(
        task["instance_id"], task["repo"], task["base_commit"],
        task["problem_statement"], task["difficulty"],
    )
    prepare_workspace(Path(task["source_repo"]), workload, workspace)
    logs, installed = [], []
    for index, directory in enumerate(directories):
        name = "beliefkv-node-prepare-" + uuid.uuid4().hex
        log = here / "logs" / (project + f"-{index}.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "docker", "run", "--rm", "--name", name,
            "--label", "beliefkv.cpu-preparation=original-node-20261030",
            "--network", "bridge", "--cpus", "1", "--memory", "4g",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--volume", str(workspace) + ":/workspace",
            "--volume", str(here / "npm-cache") + ":/npm-cache",
            "--workdir", "/workspace/" + directory.as_posix(),
            "--env", "HOME=/tmp", "--env", "HUSKY=0",
            "--env", "NPM_CONFIG_CACHE=/npm-cache",
            "--env", "PYTHON=/opt/miniconda3/envs/testbed/bin/python",
            "--env", "OMP_NUM_THREADS=1", "--env", "OPENBLAS_NUM_THREADS=1",
            "--entrypoint", "/bin/sh", task["docker_image"], "-c",
            "npm install --legacy-peer-deps --no-audit --no-fund",
        ]
        code, timeout = None, False
        try:
            with log.open("xb") as stream:
                code = subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout_s,
                ).returncode
        except subprocess.TimeoutExpired:
            timeout = True
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=30,
            )
        logs.append({
            "directory": directory.as_posix(), "command": command,
            "returncode": code, "timed_out": timeout,
            "log": str(log), "log_sha256": sha256_file(log),
        })
        if code != 0 or timeout:
            return {"installed": False, "attempts": logs}
        modules = workspace / directory / "node_modules"
        if not modules.is_dir():
            raise ValueError("successful dependency installation did not create node_modules")
        target = here / "image" / "dependencies" / project / directory / "node_modules"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(modules, target, symlinks=True)
        lock = workspace / directory / "package-lock.json"
        installed.append({
            "directory": directory.as_posix(), "runtime_directory": (
                Path("/opt/beliefkv-node") / project / directory / "node_modules"
            ).as_posix(),
            "package_lock_sha256": sha256_file(lock) if lock.is_file() else None,
        })
    return {"installed": True, "attempts": logs, "packages": installed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=300.)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve dependency preparation attempts")
    here = args.output_dir.resolve()
    here.mkdir(parents=True, exist_ok=False)
    (here / "npm-cache").mkdir()
    manifest = json.loads(args.manifest.read_bytes())
    results, selected = {}, []
    for task in manifest["workloads"]:
        project = task["repo"].split("/", 1)[1]
        directories, reason = packages_for(task)
        if reason:
            results[project] = {"installed": False, "not_attempted_reason": reason}
        else:
            result = install(task, directories, here, timeout_s=args.timeout_s)
            results[project] = result
            if result["installed"]:
                selected.append(task)
        write(here / (project + ".json"), results[project])
        print(json.dumps({"project": project, **{
            key: value for key, value in results[project].items() if key != "attempts"
        }}), flush=True)
    if selected:
        shutil.copy2(
            ROOT / "configs/docker/locobench_node_dependencies_20261030.Dockerfile",
            here / "image/Dockerfile",
        )
    output = deepcopy(manifest)
    output["workloads"] = selected
    output["requested_workflows"] = len(selected)
    output["selected_projects"] = [task["repo"].split("/", 1)[1] for task in selected]
    output["selected_scenarios"] = [
        task["dataset_provenance"]["original_scenario"] for task in selected
    ]
    output.pop("tool_environment_qualification", None)
    for task in output["workloads"]:
        project = task["repo"].split("/", 1)[1]
        for package in results[project]["packages"]:
            destination = "/workspace/" + package["directory"] + "/node_modules"
            source = package["runtime_directory"]
            task["preflight_command"] += " && " + dependency_link(source, destination)
    write(here / "pending_image_manifest.json", output)
    write(here / "provisioning.json", {
        "manifest": str(args.manifest.resolve()), "manifest_sha256": sha256_file(args.manifest),
        "projects": results, "dependency_installed_projects": len(selected),
        "task_source_and_tests_modified": False, "private_installation_copies_only": True,
        "original_task_requirements_modified": False, "task_tests_required_to_pass": False,
        "future_image_and_live_root_child_checks_required": True, "GPU_action": False,
        "capture_ready": False, "analysis_sha256": sha256_file(Path(__file__)),
    })
    print(json.dumps({"installed_projects": len(selected), "GPU_action": False}), flush=True)


if __name__ == "__main__":
    main()
