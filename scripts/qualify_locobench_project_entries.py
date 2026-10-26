#!/usr/bin/env python3
"""Find executable original project examples without editing application or tests."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import doctest
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.qualify_locobench_environment import (
    pin_environment, production_execution_evidence, run_probe, test_execution_evidence,
)


def candidates(source):
    """Use original executable examples, preferring self-contained domain modules."""
    result = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        if (
            not path.is_file()
            or any(part in (".git", "tests", "test", "testing", "__pycache__")
                for part in relative.parts)
            or path.name.startswith(("test_", "conftest"))
        ):
            continue
        try:
            text = path.read_text()
            if ">>>" not in text:
                continue
            tree = ast.parse(text)
            compile(tree, str(path), "exec", dont_inherit=True)
            examples = [
                example for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef))
                for example in doctest.DocTestParser().get_examples(
                    ast.get_docstring(node, clean=False) or "",
                )
            ]
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        if not examples:
            continue
        imports = {
            name.split(".")[0] for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            )
        }
        external = len(imports - sys.stdlib_module_names - {"__future__"})
        result.append({
            "path": str(relative), "examples": len(examples),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "ranking": (path.name == "__init__.py", external, len(relative.parts), str(relative)),
        })
    return sorted(result, key=lambda row: row["ranking"])


def import_paths(source, relative):
    path = source / relative
    parent = path.parent
    while parent != source and (parent / "__init__.py").is_file():
        parent = parent.parent
    roots = [parent, source / "src", source]
    for directory in path.parents:
        if directory == source:
            break
        if any((directory / name).is_file() for name in (
            "pyproject.toml", "requirements.txt", "setup.py",
        )):
            roots.extend((directory, directory / "src"))
    return ":".join(dict.fromkeys(
        "/workspace" + ("/" + str(root.relative_to(source)) if root != source else "")
        for root in roots if root.is_dir()
    ))


def qualify(row, *, image, maximum_candidates):
    source = Path(row["source_repo"])
    attempts = []
    preflight = run_probe(
        str(source), image, row["preflight_command"],
        sandbox_environment=row.get("sandbox_environment"),
    )
    if preflight["returncode"] != 0:
        return {"source_repo": str(source), "dependency_preflight": preflight,
                "attempts": attempts, "qualified": False}
    for candidate in candidates(source)[:maximum_candidates]:
        command = (
            "python -m pytest -q --doctest-modules --import-mode=importlib "
            + shlex.quote(candidate["path"])
        )
        settings = {
            **row.get("sandbox_environment", {}),
            "PYTHONPATH": import_paths(source, candidate["path"]),
            "MPLBACKEND": "Agg", "QT_QPA_PLATFORM": "offscreen",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        normal = run_probe(str(source), image, command, junit=True,
                           sandbox_environment=settings)
        attempt = {
            "original_example": {key: value for key, value in candidate.items()
                                 if key != "ranking"},
            "command": command, "sandbox_environment": settings,
            "normal_result": normal, "test_execution": test_execution_evidence(normal),
        }
        attempts.append(attempt)
        # An unchanged example is an environment check, not a task-repair oracle.
        if normal["returncode"] != 0 or not attempt["test_execution"]["qualified"]:
            continue
        probe = run_probe(str(source), image, command, production_calls=True,
                          sandbox_environment=settings)
        attempt["production_probe_result"] = probe
        attempt["production_execution"] = production_execution_evidence(probe)
        if probe["returncode"] == 0 and attempt["production_execution"]["qualified"]:
            return {
                "qualified": True, "source_repo": str(source),
                "source_commit": row["base_commit"], "docker_image": image,
                "docker_image_id": image, "dependency_preflight": preflight,
                "original_test_command": command, "original_test_result": normal,
                "test_execution": attempt["test_execution"],
                "production_probe_result": probe,
                "production_execution": attempt["production_execution"],
                "sandbox_environment": settings, "attempts": attempts,
                "qualification_scope": "original_production_examples_not_entire_test_suite",
                "original_source_modified": False,
                "original_tests_modified": False,
                "business_task_repaired": False,
                "instrumented_duration_used_as_tool_time": False,
            }
    return {
        "qualified": False, "source_repo": str(source),
        "dependency_preflight": preflight, "attempts": attempts,
    }


def qualified_population(manifest, selected, results):
    population = deepcopy(manifest)
    population.pop("environment_qualification", None)
    population.update(
        requested_workflows=len(selected), max_tasks_per_project=1,
        qualified_projects=len(selected), workloads=deepcopy(selected),
        selected_projects=[Path(row["source_repo"]).name for row in selected],
        selected_scenarios=[
            row["dataset_provenance"]["original_scenario"] for row in selected
        ],
        selection_policy="actual original example execution; no policy performance selection",
        test_entrypoint_scope="original_production_examples_not_entire_test_suite",
    )
    for row in population["workloads"]:
        row["sandbox_environment"] = results[row["source_repo"]]["sandbox_environment"]
        row["test_entrypoint_scope"] = population["test_entrypoint_scope"]
    return population


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pin-manifest", type=Path)
    parser.add_argument("--maximum-candidates", type=int, default=4)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--project", action="append")
    args = parser.parse_args()
    if (
        args.output.exists() or args.pin_manifest is not None and args.pin_manifest.exists()
        or not 1 <= args.maximum_candidates <= 16 or not 1 <= args.workers <= 6
    ):
        parser.error("use fresh outputs and bounded candidate/worker counts")
    manifest = json.loads(args.manifest.read_bytes())
    rows = manifest["workloads"]
    if args.project:
        known = {Path(row["source_repo"]).name for row in rows}
        if not set(args.project) <= known:
            parser.error("project filter contains an unknown project")
        rows = [row for row in rows if Path(row["source_repo"]).name in args.project]
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True,
    ).strip()

    def check(row):
        try:
            result = qualify(row, image=image, maximum_candidates=args.maximum_candidates)
        except Exception as error:
            result = {
                "qualified": False, "source_repo": row["source_repo"], "attempts": [],
                "qualification_error": f"{type(error).__name__}: {error}",
            }
        print(json.dumps({
            "project": Path(row["source_repo"]).name,
            "qualified": result["qualified"], "attempts": len(result["attempts"]),
            "entry": result.get("original_test_command"),
        }), flush=True)
        return row["source_repo"], result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = dict(executor.map(check, rows))
    selected = [row for row in rows if results[row["source_repo"]]["qualified"]]
    report = {
        "schema_version": 2, "cpu_only": True,
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "projects": {row["source_repo"]: results[row["source_repo"]] for row in selected},
        "candidate_results": results,
        "qualified_projects": len(selected), "checked_projects": len(rows),
        "dependency_preflight_passed": bool(selected),
        "test_execution_passed": bool(selected),
        "production_execution_required": True, "production_execution_passed": bool(selected),
        "task_correctness": "ungraded",
        "qualification_scope": "original_production_examples_not_entire_test_suite",
        "performance_accepted": False, "original_task_targets_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if selected and args.pin_manifest is not None:
        population = qualified_population(manifest, selected, results)
        pinned = pin_environment(population, report, args.output)
        args.pin_manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.pin_manifest.open("x") as stream:
            json.dump(pinned, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "qualified_projects": len(selected), "checked_projects": len(rows),
        "report": str(args.output),
    }))
    if not selected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
