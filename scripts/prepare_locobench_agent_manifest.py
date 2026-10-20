#!/usr/bin/env python3
"""Import complete long LoCoBench-Agent projects without padding or gold inputs."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.long_repository_tasks import (  # noqa: E402
    render_repository_context, repository_path, unwrap_generated_file,
)

ARCHIVE_SHA256 = "53299531d0e6fe51166b5d2e12d9d9aad462ce4eb46ae00a3808bfa0b95e63a1"
UPSTREAM_REVISION = "2ab9218e6d46e50ebdbb139442503e5573bc4a5d"
DEFAULT_CATEGORIES = (
    "bug_investigation", "feature_implementation", "cross_file_refactoring",
    "integration_testing", "multi_session_development",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_project(archive, metadata_path, *, python_minor_version=9):
    metadata = json.loads(archive.read(metadata_path))
    project_id = metadata["specification"]["unique_id"]
    prefix = metadata_path.rsplit("/", 1)[0] + "/"
    files, provenance, errors = {}, [], []
    for item in metadata["files"]:
        path = repository_path(item["path"])
        if path in files:
            continue
        entry = archive.getinfo(prefix + path)
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise ValueError(f"project source is a symlink: {path}")
        raw = archive.read(entry)
        text, unwrapped = unwrap_generated_file(raw.decode("utf-8"))
        files[path] = text
        provenance.append({
            "path": path, "archive_path": entry.filename,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "materialized_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "outer_markdown_fence_removed": unwrapped,
        })
        if path.endswith(".py"):
            try:
                ast.parse(text, filename=path, feature_version=(3, python_minor_version))
            except SyntaxError as error:
                errors.append({"path": path, "line": error.lineno, "message": error.msg})
    return metadata, files, provenance, errors


def scenario_prompt(scenario):
    # Expected approaches, answers and evaluation criteria are never prompt inputs.
    return "\n\n".join(
        str(scenario[key]).strip()
        for key in ("title", "description", "task_prompt")
        if scenario.get(key)
    )


def select_scenarios(candidates, root_count, max_tasks_per_project):
    qualified = [row for row in candidates if not row["rejection_reasons"]]
    selected, used = [], set()
    for _ in range(max_tasks_per_project):
        for row in qualified:
            if len(selected) >= root_count:
                break
            available = [path for path in row["scenario_paths"] if path not in used]
            category = DEFAULT_CATEGORIES[len(selected) % len(DEFAULT_CATEGORIES)]
            preferred = [path for path in available if f"_{category}_" in path]
            if not available:
                continue
            scenario_path = (preferred or available)[0]
            selected.append((row, scenario_path))
            used.add(scenario_path)
    return selected


def git(repo, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo, text=True, capture_output=True, check=True, timeout=120,
        env={
            **os.environ, "GIT_AUTHOR_DATE": "2025-11-19T18:00:06+00:00",
            "GIT_COMMITTER_DATE": "2025-11-19T18:00:06+00:00",
        },
    )
    return result.stdout.strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", default=ARCHIVE_SHA256)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path)
    parser.add_argument("--sources-dir", type=Path)
    parser.add_argument("--root-count", type=int, default=12)
    parser.add_argument("--max-tasks-per-project", type=int, default=2)
    parser.add_argument("--min-context-tokens", type=int, default=110000)
    parser.add_argument("--max-context-tokens", type=int, default=170000)
    parser.add_argument("--selection-seed", type=int, default=21)
    parser.add_argument("--sandbox-python-minor-version", type=int, default=9)
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--docker-image", default="beliefkv/locobench-agent:long-py310-v1")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be a new path")
    if not 0 < args.min_context_tokens <= args.max_context_tokens <= 196608:
        parser.error("context bounds must leave room within the 256K model")
    if args.root_count <= 0 or args.max_tasks_per_project <= 0 or args.selection_seed < 0:
        parser.error("counts must be positive and seed nonnegative")
    if not 7 <= args.sandbox_python_minor_version <= sys.version_info.minor:
        parser.error("sandbox Python feature version must be supported by this importer")
    if not args.profile_only and (args.assets_dir is None or args.sources_dir is None):
        parser.error("imports require fresh assets and source directories")
    digest = sha256_file(args.archive)
    if digest != args.archive_sha256:
        raise ValueError("archive does not match the pinned dataset")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    archive = zipfile.ZipFile(args.archive)
    metadata_paths = sorted(
        name for name in archive.namelist()
        if name.startswith("data/generated/python_") and name.endswith("/project_metadata.json")
    )
    scenario_paths = sorted(
        name for name in archive.namelist()
        if name.startswith("data/output/scenarios/python_") and name.endswith(".json")
    )
    candidates, loaded = [], {}
    for metadata_path in metadata_paths:
        try:
            metadata, files, file_provenance, syntax_errors = load_project(
                archive, metadata_path,
                python_minor_version=args.sandbox_python_minor_version,
            )
        except (KeyError, ValueError, UnicodeDecodeError) as error:
            candidates.append({
                "project_id": metadata_path.split("/")[-2],
                "context_tokens": None,
                "rejection_reasons": ["invalid_project_source"],
                "source_error": str(error),
            })
            continue
        project_id = metadata["specification"]["unique_id"]
        context = render_repository_context(project_id, files)
        count = len(tokenizer.encode(context, add_special_tokens=False))
        matching = [
            path for path in scenario_paths
            if path.split("/")[-1].startswith(project_id + "_")
            and any(f"_{category}_" in path for category in DEFAULT_CATEGORIES)
        ]
        reasons = []
        if not args.min_context_tokens <= count <= args.max_context_tokens:
            reasons.append("outside_context_range")
        if syntax_errors:
            reasons.append("python_syntax_error")
        if not matching:
            reasons.append("no_software_change_scenario")
        row = {
            "project_id": project_id, "source_files": len(files),
            "context_tokens": count, "syntax_errors": syntax_errors,
            "unwrapped_files": sum(item["outer_markdown_fence_removed"] for item in file_provenance),
            "duplicate_metadata_paths_removed": len(metadata["files"]) - len(files),
            "scenario_paths": matching, "rejection_reasons": reasons,
        }
        candidates.append(row)
        if not reasons:
            loaded[project_id] = metadata, files, file_provenance, context
        print(json.dumps({
            "project": project_id, "tokens": count, "rejected": reasons,
        }), flush=True)
    candidates.sort(key=lambda row: hashlib.sha256(
        f"{args.selection_seed}:{row['project_id']}".encode(),
    ).hexdigest())
    selected = select_scenarios(candidates, args.root_count, args.max_tasks_per_project)
    report = {
        "dataset": "SalesforceAIResearch/LoCoBench-Agent",
        "upstream_revision": UPSTREAM_REVISION,
        "archive": str(args.archive.resolve()), "archive_sha256": digest,
        "archive_download": (
            "https://drive.usercontent.google.com/download?"
            "id=1HwPztd0bipUUi8zs7Pxo3StZCOnJBwVR&export=download&confirm=t"
        ),
        "tokenizer": str(args.tokenizer.resolve()),
        "tokenizer_sha256": sha256_file(args.tokenizer / "tokenizer.json"),
        "tokenizer_config_sha256": sha256_file(args.tokenizer / "tokenizer_config.json"),
        "selection_seed": args.selection_seed,
        "selection_policy": "seeded project order; round-robin distinct original tasks; no runtime/outcome selection",
        "max_tasks_per_project": args.max_tasks_per_project,
        "context_token_range": [args.min_context_tokens, args.max_context_tokens],
        "gold_fields_exposed": False,
        "dataset_nature": "generated long software projects; adapted multi-agent systems workload",
        "context_delivery": (
            "complete frozen source in root only; children receive model-selected "
            "task descriptions and read relevant files from the shared workspace"
        ),
        "code_transformation": "one whole-file Markdown fence removed; no semantic fixes or padding",
        "python_ast_check": "syntax only; imports, tests and task correctness are not established",
        "sandbox_python_feature_version": [3, args.sandbox_python_minor_version],
        "candidates": candidates, "qualified_projects": len(loaded),
        "requested_workflows": args.root_count,
        "selected_projects": sorted({row["project_id"] for row, _ in selected}),
        "selected_scenarios": [path for _, path in selected],
    }
    if args.profile_only:
        write_json(args.output, report)
        print(json.dumps({
            "output": str(args.output), "qualified_projects": len(loaded),
            "rejections": dict(Counter(
                reason for row in candidates for reason in row["rejection_reasons"]
            )),
        }, indent=2))
        return
    if len(selected) != args.root_count:
        raise ValueError(f"only {len(selected)} qualified distinct tasks for {args.root_count} requested")
    if args.assets_dir.exists() or args.sources_dir.exists():
        raise FileExistsError("asset/source import directories must be fresh")
    args.assets_dir.mkdir(parents=True)
    args.sources_dir.mkdir(parents=True)
    workloads, imported = [], {}
    for row, scenario_path in selected:
        project_id = row["project_id"]
        _, files, provenance, context = loaded[project_id]
        scenario_bytes = archive.read(scenario_path)
        scenario = json.loads(scenario_bytes)
        repo = (args.sources_dir / project_id).resolve()
        pack = args.assets_dir / f"{project_id}.txt.gz"
        provenance_path = args.assets_dir / f"{project_id}.source_provenance.json"
        if project_id not in imported:
            repo.mkdir()
            for relative, text in files.items():
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            git(repo, "init", "--quiet")
            git(repo, "add", "--force", "--", ".")
            git(repo, "-c", "user.name=BeliefKV Dataset Import",
                "-c", "user.email=dataset-import@localhost", "commit", "--quiet",
                "-m", f"Import frozen LoCoBench-Agent source {project_id}")
            imported[project_id] = git(repo, "rev-parse", "HEAD")
            pack.write_bytes(gzip.compress(context.encode(), mtime=0))
            write_json(provenance_path, provenance)
        commit = imported[project_id]
        scenario_copy = args.assets_dir / f"{scenario['id']}.reference.json"
        scenario_copy.write_bytes(scenario_bytes)
        workloads.append({
            "instance_id": "locobench__" + scenario["id"],
            "repo": "locobench/" + project_id, "base_commit": commit,
            "task_kind": "locobench_agent",
            "difficulty": scenario["difficulty"],
            "problem_statement": scenario_prompt(scenario),
            "source_repo": str(repo),
            "docker_image": args.docker_image,
            "preflight_command": "python -c 'import ast, json, pytest, hypothesis, yaml, numpy, pydantic, fastapi, sqlalchemy'",
            "frozen_repository_context": {
                "path": str(pack.resolve()), "sha256": sha256_file(pack),
                "tokens": row["context_tokens"], "source_file_count": len(files),
            },
            "dataset_provenance": {
                "original_scenario": scenario_path,
                "scenario_sha256": hashlib.sha256(scenario_bytes).hexdigest(),
                "reference_path": str(scenario_copy.resolve()),
                "source_provenance_path": str(provenance_path.resolve()),
                "source_provenance_sha256": sha256_file(provenance_path),
                "task_category": scenario["task_category"],
                "reference_fields_exposed": False,
            },
        })
    report.update({"schema_version": 1, "dataset_revision": digest, "workloads": workloads})
    write_json(args.output, report)
    print(json.dumps({
        "output": str(args.output), "workloads": len(workloads),
        "source_context_tokens": [row["frozen_repository_context"]["tokens"] for row in workloads],
        "sha256": sha256_file(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
