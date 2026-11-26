"""Bind LoCoBench tool setup to the exact original task and source files."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_locobench_agent_manifest import (
    ARCHIVE_SHA256, UPSTREAM_REVISION, load_project, scenario_prompt, sha256_file, write_json,
)
from beliefkv.experiments.long_repository_tasks import parse_repository_context, render_repository_context

SETUP_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock", "environment.yml",
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", ".nvmrc", ".python-version",
}
SETUP_INSTRUCTIONS = re.compile(
    r"\b(?:pip3? install|npm (?:ci|install)|yarn install|pnpm install|"
    r"poetry install|uv sync|conda (?:env|create)|"
    r"python (?:-m pytest|manage\.py)|(?:npm|pnpm|yarn) (?:test|run)|"
    r"Python\s*[>=]*\s*3\.\d+|Node(?:\.js)?\s*[>=]*\s*\d+|"
    r"docker(?:-|\s+)compose|(?:RUN|FROM)\s+(?:pip|python|npm|node))\b",
    re.IGNORECASE,
)


def merge_tool_environments(original, prepared_manifests):
    permitted = {"docker_image", "preflight_command", "sandbox_environment"}
    original_rows = {row["instance_id"]: row for row in original["workloads"]}
    if len(original_rows) != len(original["workloads"]):
        raise ValueError("original task identifiers must be unique")
    environments = {}
    for manifest in prepared_manifests:
        rows = {row["instance_id"]: row for row in manifest["workloads"]}
        if len(rows) != len(manifest["workloads"]) or rows.keys() != original_rows.keys():
            raise ValueError("tool environment overlay changed the task population")
        for key, row in rows.items():
            before = original_rows[key]
            if ({field: value for field, value in before.items() if field not in permitted}
                    != {field: value for field, value in row.items() if field not in permitted}):
                raise ValueError(f"tool environment overlay changed original task {key}")
            if row != before:
                environments[key] = row
    return {
        **original,
        "workloads": [environments.get(key, row) for key, row in original_rows.items()],
    }


def frozen_workloads(composite):
    selected = json.loads(composite.read_bytes())["selected_workflows"]
    snapshots = {}
    for ref in selected:
        if not ref["project"].startswith("locobench/"):
            continue
        capture = Path(ref["source_capture"])
        if capture not in snapshots:
            clients = sorted(capture.glob("client_*/manifest.json"))
            if len(clients) != 1:
                raise ValueError(f"expected one client capture manifest in {capture}")
            client = json.loads(clients[0].read_bytes())
            manifest = Path(client["config"]["workload_manifest"])
            snapshots[capture] = manifest, {
                row["instance_id"]: row
                for row in json.loads(manifest.read_bytes())["workloads"]
            }
        manifest, rows = snapshots[capture]
        row = rows[ref["task_id"]]
        if row["repo"] != ref["project"]:
            raise ValueError("composite and frozen capture identify different projects")
        yield row, manifest


def original_project(archive, row):
    project = row["repo"].removeprefix("locobench/")
    prefix = f"data/generated/{project}/"
    metadata_path = prefix + "project_metadata.json"
    metadata, files, provenance, syntax_errors = load_project(
        archive, metadata_path, python_minor_version=sys.version_info.minor,
    )
    snapshot = subprocess.check_output(
        ["git", "-C", row["source_repo"], "archive", row["base_commit"]], timeout=30,
    )
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as tree:
        materialized = {entry.name: tree.extractfile(entry).read() for entry in tree if entry.isfile()}
    expected = {path: content.encode("utf-8") for path, content in files.items()}
    if expected != materialized:
        changes = sorted(path for path in expected.keys() | materialized.keys()
                         if expected.get(path) != materialized.get(path))
        raise ValueError(f"{project}: files differ from the dataset archive: {changes}")
    context = parse_repository_context(row["frozen_repository_context"])
    if context.source_file_count != len(files) or context.text != render_repository_context(project, files):
        raise ValueError(f"{project}: frozen context differs from the original project")
    scenario_path = row["dataset_provenance"]["original_scenario"]
    if scenario_prompt(json.loads(archive.read(scenario_path))) != row["problem_statement"]:
        raise ValueError(f"{project}: task prompt differs from the original scenario")
    setup_files, instructions = {}, {}
    for path, content in files.items():
        name = PurePosixPath(path).name
        if (
            name in SETUP_NAMES or name.startswith(("Dockerfile", "docker-compose", "compose."))
            or name.startswith("requirements") and name.endswith((".txt", ".in"))
            or "requirements" in PurePosixPath(path).parts[:-1] and name.endswith(".txt")
        ):
            setup_files[path] = content
        if name.lower().startswith("readme") or name.startswith("Dockerfile"):
            matched = [
                {"line": number, "text": line.strip()} for number, line in
                enumerate(content.splitlines(), 1) if SETUP_INSTRUCTIONS.search(line)
            ]
            if matched:
                instructions[path] = matched
    return {
        "archive_metadata_path": metadata_path, "scenario_path": scenario_path,
        "source_files": len(expected), "source_files_match": True, "scenario_matches": True,
        "context_matches": True,
        "unlisted_archive_files": sorted(
            entry.removeprefix(prefix)
            for entry in archive.namelist()
            if entry.startswith(prefix)
            and not entry.endswith("/") and entry != metadata_path
            and entry.removeprefix(prefix) not in expected
        ),
        "specification_dependencies": metadata["specification"].get("dependencies", []),
        "setup_file_sha256": {
            item["path"]: item["materialized_sha256"]
            for item in provenance if item["path"] in setup_files
        },
        "setup_instructions": instructions, "original_python_syntax_errors": syntax_errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--composite", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    digest = sha256_file(args.archive)
    if digest != ARCHIVE_SHA256:
        raise ValueError("unexpected LoCoBench archive")
    rows, audit, sources = [], {}, {}
    with zipfile.ZipFile(args.archive) as archive:
        for row, manifest in frozen_workloads(args.composite):
            project = row["repo"]
            if project in audit:
                raise ValueError(f"duplicate project in composite: {project}")
            rows.append(row)
            audit[project] = original_project(archive, row)
            sources[str(manifest)] = sha256_file(manifest)
    if not rows:
        raise ValueError("composite contains no LoCoBench tasks")
    write_json(args.output_dir / "original_manifest.json", {
        "dataset": "SalesforceAIResearch/LoCoBench-Agent",
        "archive": str(args.archive.resolve()), "archive_sha256": digest,
        "upstream_revision": UPSTREAM_REVISION,
        "requested_workflows": len(rows), "max_tasks_per_project": 1, "workloads": rows,
    })
    write_json(args.output_dir / "audit.json", {
        "composite": str(args.composite.resolve()), "composite_sha256": sha256_file(args.composite),
        "original_manifest_sha256": sha256_file(args.output_dir / "original_manifest.json"),
        "capture_manifest_sha256": sources,
        "projects": audit, "source_files": sum(v["source_files"] for v in audit.values()),
    })
    print(json.dumps({"projects": len(rows), "report": str(args.output_dir / "audit.json")}))


if __name__ == "__main__":
    main()
