"""Provision original Python dependencies into an offline per-project sandbox."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import uuid

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.deepagents_swebench import (
    DEFAULT_SANDBOX_TEST_ENV, SweBenchWorkload, prepare_workspace,
)
from scripts.prepare_locobench_agent_manifest import sha256_file, write_json
from scripts.qualify_locobench_tools import qualify

PYTHON = f"{DEFAULT_SANDBOX_TEST_ENV}/bin/python"
BASIC_PREFLIGHT = "command -v bash git rg patch >/dev/null && python -m pytest --version"


def image_id(image):
    return subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True, timeout=20,
    ).strip()


def python_setup(workspace, audit, override):
    source_path = PurePosixPath(override.get("directory", "."))
    context = [(PurePosixPath(path), workspace / path) for path in audit["setup_file_sha256"]]
    groups = {}
    for relative, path in context:
        if path.name in ("requirements.txt", "pyproject.toml"):
            groups.setdefault(relative.parent, {})[path.name] = path
    if "directory" not in override and len(groups) == 1:
        source_path = next(iter(groups))
    if source_path.is_absolute() or ".." in source_path.parts:
        raise ValueError("invalid container project directory")
    if len(groups) > 1 and "directory" not in override:
        return {"status": "separate_project_components", "directories": sorted(map(str, groups))}
    group = groups.get(source_path, {})
    pyproject = group.get("pyproject.toml")
    metadata = tomllib.loads(pyproject.read_text()) if pyproject else {}
    project = metadata.get("project", {})
    if "requirements" in override:
        names = override["requirements"]
    elif "requirements.txt" in group and override.get("declaration") != "pyproject.toml":
        names = ["requirements.txt"]
        for extra in ("requirements-dev.txt", "requirements-test.txt"):
            if (workspace / source_path / extra).is_file():
                names.append(extra)
    else:
        names = []
    declared = []
    arguments = []
    excluded = set(override.get("unavailable_requirements", {}))
    excluded_in_files = set()
    for name in names:
        relative = (source_path / name).as_posix()
        if relative not in audit["setup_file_sha256"]:
            raise ValueError(f"original requirements file is absent: {relative}")
        declared.append(relative)
        if not excluded and not override.get("parse_registry_requirements"):
            arguments.extend(("-r", name))
            continue
        for line in (workspace / relative).read_text().splitlines():
            value = line.split("#", 1)[0].strip()
            if not value:
                continue
            requirement = Requirement(value)
            if requirement.url is not None:
                raise ValueError("partial provisioning only supports registry requirements")
            if value in excluded:
                excluded_in_files.add(value)
            else:
                arguments.append(value)
    if excluded_in_files != excluded:
        raise ValueError("an excluded requirement did not exactly match an original declaration")
    if pyproject and (not names or override.get("declaration") == "pyproject.toml"):
        if "dependencies" not in project:
            poetry = metadata.get("tool", {}).get("poetry", {})
            if not poetry.get("dependencies"):
                return {"status": "unsupported_dependency_declaration", "directory": str(source_path)}
            from poetry.core.factory import Factory

            dependencies = poetry["dependencies"]
            python_constraint = Factory.create_dependency("python", dependencies.get("python", "*")).constraint
            if not python_constraint.allows_any(
                Factory.create_dependency("python", f"3.{override.get('python_minor', 11)}.*").constraint,
            ):
                raise ValueError(f"declared Python range is {python_constraint}")
            project = {"dependencies": [
                Factory.create_dependency(dependency, constraint).to_pep_508()
                for dependency, constraint in dependencies.items()
                if dependency != "python" and not (
                    isinstance(constraint, dict) and constraint.get("optional")
                )
            ]}
        declared.append((source_path / "pyproject.toml").as_posix())
        arguments.extend(project["dependencies"])
        for extra in override.get("extras", []):
            arguments.extend(project["optional-dependencies"][extra])
    arguments.extend(override.get("additional_dependencies", []))
    if not arguments:
        return {"status": "no_installable_declaration"}
    for dependency in override.get("additional_dependencies", []):
        if Requirement(dependency).url is not None:
            raise ValueError("external requirements must identify registry distributions")
    requires = SpecifierSet(project.get("requires-python", ""))
    minor = override.get("python_minor", 11)
    if str(f"3.{minor}.0") not in requires:
        raise ValueError(f"declared Python range {requires} excludes Python 3.{minor}")
    return {
        "status": "planned", "directory": str(source_path), "declared_files": declared,
        "python_minor": minor, "arguments": arguments, "requires_python": str(requires),
        "unavailable_requirements": override.get("unavailable_requirements", {}),
    }


def install(workspace, base, plan, directory, tag, timeout_s, cache, uv_binary=None, *, cpu_torch=False):
    name = "beliefkv-locobench-python-" + uuid.uuid4().hex
    pip = [PYTHON, "-m", "pip", "--isolated"]
    packages = ["pytest", "pytest-asyncio", *plan["arguments"]]
    pip_install = [
        *pip, "install", "--no-input", "--disable-pip-version-check",
        "--timeout", "20", "--retries", "2", "--cache-dir", "/pip-cache",
        "--report", "/tmp/beliefkv-install.json", *packages,
    ]
    setup = [
        ["rm", "-rf", DEFAULT_SANDBOX_TEST_ENV],
        [f"/usr/local/bin/python3.{plan['python_minor']}", "-m", "venv", DEFAULT_SANDBOX_TEST_ENV],
    ]
    mounts = []
    if uv_binary is not None:
        mounts.extend(("--mount", f"type=bind,source={uv_binary},target=/usr/local/bin/uv,readonly"))
        uv_install = [
            "uv", "pip", "install", "--python", PYTHON, "--cache-dir", "/pip-cache/uv",
            "--link-mode", "copy", "--strict",
        ]
        if cpu_torch:
            uv_install.extend(("--torch-backend", "cpu"))
        setup.append([*uv_install, *packages])
    else:
        setup.append(pip_install)
    setup.extend(([*pip, "check"], [*pip, "freeze", "--all"]))
    script = " && ".join(map(shlex.join, setup))
    if uv_binary is not None:
        script += " && " + shlex.join([*pip, "inspect"]) + " > /tmp/beliefkv-install.json"
    command = [
        "docker", "run", "--name", name, "--network", "bridge", "--cpus", "2", "--memory", "6g",
        "--pids-limit", "256", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE",
        "--security-opt", "no-new-privileges",
        "--label", "beliefkv.cpu-preparation=locobench-python",
        "--mount", f"type=bind,source={workspace},target=/workspace,readonly",
        "--mount", f"type=bind,source={cache},target=/pip-cache",
        *mounts,
        "--workdir", f"/workspace/{plan['directory']}", "--user", "0:0",
        "--entrypoint", "/bin/sh", base, "-c", script,
    ]
    log = directory / "install.log"
    evidence = {
        "base_image": base, "command": command, "log": str(log), "available_image": None,
        "resolver": "pip" if uv_binary is None else "uv",
    }
    try:
        with log.open("xb") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout_s)
        evidence["returncode"] = result.returncode
        if result.returncode == 0:
            subprocess.run(["docker", "cp", f"{name}:/tmp/beliefkv-install.json",
                            str(directory / "pip-install.json")], check=True, timeout=30)
            subprocess.run(["docker", "commit", name, tag], check=True, capture_output=True,
                           timeout=120)
            evidence["available_image"] = image_id(tag)
    except subprocess.TimeoutExpired:
        evidence["timed_out"] = True
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True,
                       timeout=30)
        evidence["log_sha256"] = sha256_file(log)
    return evidence


def provision(row, audit, override, bases, output, timeout_s, cache, uv_binary=None, *, cpu_torch=False):
    project = row["repo"].split("/", 1)[1]
    if not re.fullmatch(r"python_[a-z0-9_]+", project):
        raise ValueError("unexpected Python project identifier")
    directory = output / project
    directory.mkdir()
    with tempfile.TemporaryDirectory(prefix="beliefkv-python-setup-") as staging:
        workspace = Path(staging) / "workspace"
        workload = SweBenchWorkload(
            row["instance_id"], row["repo"], row["base_commit"],
            row["problem_statement"], row["difficulty"],
        )
        prepare_workspace(Path(row["source_repo"]), workload, workspace)
        for path, digest in audit["setup_file_sha256"].items():
            if sha256_file(workspace / path) != digest:
                raise ValueError(f"setup file differs from the original: {project}/{path}")
        plan = python_setup(workspace, audit, override)
        result = {"project": row["repo"], "source_commit": row["base_commit"], "plan": plan}
        if plan["status"] != "planned":
            return result, row
        base = bases[plan["python_minor"]]
        tag = f"beliefkv/locobench-env-{project}:{output.name}"
        result["install"] = install(
            workspace, base, plan, directory, tag, timeout_s, cache, uv_binary, cpu_torch=cpu_torch,
        )
    image = result["install"]["available_image"]
    if not image:
        return result, row
    fixed = {**row, "docker_image": image, "preflight_command": BASIC_PREFLIGHT}
    commands = ["python -m pip check", *override.get("environment_probes", [])]
    try:
        result["qualification"] = qualify(fixed, image, project_commands=commands)
        if override.get("original_entries"):
            result["original_entry"] = qualify(
                fixed, image, project_commands=override["original_entries"],
            )
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        result["qualification_error"] = repr(error)
        return result, row
    if not (result["qualification"]["tool_execution_passed"]
            and result["qualification"]["project_commands_passed"]):
        return result, row
    fixed["sandbox_environment"] = result["qualification"]["sandbox_environment"]
    return result, fixed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--python311-image", required=True)
    parser.add_argument("--python310-image", default="beliefkv/locobench-agent:tools-only-20261026")
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--pip-cache", type=Path)
    parser.add_argument("--uv-binary", type=Path)
    parser.add_argument("--cpu-torch", action="store_true", help="use the PyTorch CPU index with uv")
    args = parser.parse_args()
    if args.cpu_torch and args.uv_binary is None:
        parser.error("--cpu-torch requires --uv-binary")
    audit = json.loads((args.inventory / "audit.json").read_bytes())
    input_path = args.inventory / "original_manifest.json"
    if sha256_file(input_path) != audit["original_manifest_sha256"]:
        raise ValueError("original task manifest changed")
    original = json.loads(input_path.read_bytes())
    rows = [row for row in original["workloads"] if row["repo"].startswith("locobench/python_")]
    if args.project:
        unknown = set(args.project) - {row["repo"].split("/", 1)[1] for row in rows}
        if unknown:
            raise ValueError(f"projects absent from the inventory: {sorted(unknown)}")
        rows = [row for row in rows if row["repo"].split("/", 1)[1] in args.project]
    overrides = json.loads(args.overrides.read_bytes()) if args.overrides else {}
    bases = {10: image_id(args.python310_image), 11: image_id(args.python311_image)}
    output = args.output_dir.resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,99}", output.name):
        raise ValueError("output directory basename must be usable as a Docker tag")
    output.mkdir(parents=True, exist_ok=False)
    cache = args.pip_cache.resolve() if args.pip_cache else output / "pip-cache"
    cache.mkdir(parents=True, exist_ok=True)
    uv_binary = args.uv_binary.resolve(strict=True) if args.uv_binary else None

    def run(row):
        project = row["repo"].split("/", 1)[1]
        try:
            result, fixed = provision(
                row, audit["projects"][row["repo"]], overrides.get(project, {}),
                bases, output, args.timeout_s, cache, uv_binary,
                cpu_torch=args.cpu_torch,
            )
        except (ImportError, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
            result, fixed = {"project": row["repo"], "error": repr(error)}, row
        write_json(output / project / "result.json", result)
        print(json.dumps({
            "project": row["repo"], "plan": result.get("plan", {}).get("status"),
            "install": result.get("install", {}).get("returncode"),
            "qualified_image": fixed["docker_image"] if fixed is not row else None,
            "error": result.get("error"),
        }), flush=True)
        return result, fixed

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(run, rows))
    replacements = {row["instance_id"]: row for _, row in results}
    prepared = deepcopy(original)
    prepared["workloads"] = [
        replacements.get(row["instance_id"], row) for row in original["workloads"]
    ]
    write_json(output / "prepared_manifest.json", prepared)
    write_json(output / "report.json", {
        "original_manifest": str(input_path.resolve()),
        "original_manifest_sha256": sha256_file(input_path),
        "audit_sha256": sha256_file(args.inventory / "audit.json"),
        "overrides_sha256": sha256_file(args.overrides) if args.overrides else None,
        "projects": [result for result, _ in results],
        "updated_projects": sum(row["docker_image"] != replacements.get(
            row["instance_id"], row,
        )["docker_image"] for row in original["workloads"]),
    })


if __name__ == "__main__":
    main()
