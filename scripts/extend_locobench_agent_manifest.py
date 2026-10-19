#!/usr/bin/env python3
"""Add distinct original tasks while reusing qualified source snapshots."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_locobench_agent_manifest import (  # noqa: E402
    scenario_prompt, select_scenarios, sha256_file, write_json,
)


def extend_manifest(base, archive, *, root_count, max_tasks_per_project, references):
    selected = select_scenarios(base["candidates"], root_count, max_tasks_per_project)
    if len(selected) != root_count:
        raise ValueError("not enough distinct qualified original tasks")
    by_project = {
        row["repo"].split("/", 1)[1]: row for row in base["workloads"]
    }
    if any(row["project_id"] not in by_project for row, _ in selected):
        raise ValueError("extension would introduce an unqualified project environment")
    if references.exists():
        raise FileExistsError("reference directory must be fresh")
    report = deepcopy(base)
    report.update(
        requested_workflows=root_count, max_tasks_per_project=max_tasks_per_project,
        selected_scenarios=[path for _, path in selected],
        selected_projects=sorted({row["project_id"] for row, _ in selected}),
        source_snapshots_reused=True, workloads=[],
    )
    for row, scenario_path in selected:
        scenario_bytes = archive.read(scenario_path)
        scenario = json.loads(scenario_bytes)
        task = deepcopy(by_project[row["project_id"]])
        reference = references / f"{scenario['id']}.reference.json"
        reference.parent.mkdir(parents=True, exist_ok=True)
        with reference.open("xb") as stream:
            stream.write(scenario_bytes)
        task.update(
            instance_id="locobench__" + scenario["id"],
            difficulty=scenario["difficulty"],
            problem_statement=scenario_prompt(scenario),
        )
        task["dataset_provenance"].update(
            original_scenario=scenario_path,
            scenario_sha256=hashlib.sha256(scenario_bytes).hexdigest(),
            reference_path=str(reference.resolve()),
            task_category=scenario["task_category"],
            reference_fields_exposed=False,
        )
        report["workloads"].append(task)
    if len({row["instance_id"] for row in report["workloads"]}) != root_count:
        raise ValueError("duplicate original task IDs")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--root-count", type=int, default=24)
    parser.add_argument("--max-tasks-per-project", type=int, default=4)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be fresh")
    if args.root_count <= 0 or args.max_tasks_per_project <= 0:
        parser.error("counts must be positive")
    base = json.loads(args.base_manifest.read_text())
    if sha256_file(Path(base["archive"])) != base["archive_sha256"]:
        raise ValueError("archive no longer matches qualified input")
    with zipfile.ZipFile(base["archive"]) as archive:
        report = extend_manifest(
            base, archive, root_count=args.root_count,
            max_tasks_per_project=args.max_tasks_per_project, references=args.references,
        )
    report["expanded_from_manifest"] = {
        "path": str(args.base_manifest.resolve()),
        "sha256": sha256_file(args.base_manifest),
    }
    write_json(args.output, report)
    print(json.dumps({
        "manifest": str(args.output), "distinct_workflows": len(report["workloads"]),
        "projects": report["selected_projects"], "source_snapshots_reused": True,
        "gpu_experiment_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
