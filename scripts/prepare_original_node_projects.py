"""Prepare untouched original Node projects without qualifying their applications."""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.prepare_locobench_agent_manifest import load_project
from scripts.prepare_original_task_mix import (
    POOL, TOKENIZER_SOURCE, materialize_project, original_task,
    source_integrity, validate_population,
)
from scripts.train_workflow_work import write


def candidates(inventory):
    if (
        inventory.get("source_and_tests_modified") is not False
        or inventory.get("original_task_requirements_modified") is not False
        or inventory.get("selection_uses_native_or_policy_outcomes") is not False
        or inventory.get("short_workflows_not_proven_before_natural_capture") is not True
    ):
        raise ValueError("require original-task inventory independent of policy outcomes")
    selected = inventory["selected_short_context_candidates"]
    families = inventory["already_captured_project_families"]
    if not selected or len({row["project"] for row in selected}) != len(selected):
        raise ValueError("require different complete original projects")
    for row in selected:
        if (
            not row["project"].startswith("javascript_")
            or row["project"].rsplit("_", 1)[-1] in families
            or row["original_difficulty"] not in ("easy", "medium")
            or not 8000 <= row["complete_context_tokens"] <= 96000
            or row.get("incidental_python_syntax_diagnostics_not_repaired")
        ):
            raise ValueError("candidate violates the declared original-project selection")
    return selected


def node_preflight(packages):
    ranges = [row.get("engines", {}) for row in packages]
    payload = (
        "const s=require('/usr/local/lib/node_modules/npm/node_modules/semver');"
        "const versions={node:process.version,"
        "npm:require('/usr/local/lib/node_modules/npm/package.json').version};"
        "const engines=" + json.dumps(ranges, separators=(",", ":")) + ";"
        "for(const row of engines)for(const key of ['node','npm'])"
        "if(row[key]&&!s.satisfies(versions[key],row[key]))"
        "throw Error(key+' '+versions[key]+' fails '+row[key]);"
        "console.log(JSON.stringify(versions));"
    )
    return "node -e " + shlex.quote(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve original-project preparation evidence")
    inventory, pool, model_metadata = (
        json.loads(path.read_bytes()) for path in (args.inventory, POOL, TOKENIZER_SOURCE)
    )
    selected = candidates(inventory)
    if (
        inventory["archive_sha256"] != pool["archive_sha256"]
        or sha256_file(Path(pool["archive"])) != pool["archive_sha256"]
        or inventory["tokenizer_sha256"] != model_metadata["tokenizer_sha256"]
        or sha256_file(Path(model_metadata["tokenizer"]) / "tokenizer.json")
        != model_metadata["tokenizer_sha256"]
        or sha256_file(Path(model_metadata["tokenizer"]) / "tokenizer_config.json")
        != model_metadata["tokenizer_config_sha256"]
    ):
        raise ValueError("original archive or actual serving tokenizer changed")
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True,
    ).strip()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_metadata["tokenizer"], local_files_only=True)
    here = args.output_dir.resolve()
    here.mkdir(parents=True, exist_ok=False)
    tasks, assignments = [], []
    with zipfile.ZipFile(pool["archive"]) as archive:
        for row in selected:
            metadata = archive.read(row["metadata_path"])
            if hashlib.sha256(metadata).hexdigest() != row["metadata_sha256"]:
                raise ValueError("original project metadata changed")
            template = materialize_project(
                archive, row["project"], row, here / "contexts",
                ROOT / "workloads/sources/locobench_original_node_20261030", pool, tokenizer,
            )
            context = template["frozen_repository_context"]
            if context["tokens"] != row["complete_context_tokens"]:
                raise ValueError("complete current-model context differs from the inventory")
            reference = here / "references" / (row["original_id"] + ".reference.json")
            task, raw = original_task(template, row["original_scenario"], archive, reference)
            if (
                hashlib.sha256(raw).hexdigest() != row["original_scenario_sha256"]
                or hashlib.sha256(task["problem_statement"].encode()).hexdigest()
                != row["original_problem_sha256"]
            ):
                raise ValueError("complete public task changed")
            _metadata, files, _provenance, _errors = load_project(
                archive, row["metadata_path"], python_minor_version=10,
            )
            packages = [
                {"path": path, **json.loads(text)}
                for path, text in files.items() if Path(path).name == "package.json"
            ]
            task["docker_image"] = image
            task["preflight_command"] += " && " + node_preflight(packages)
            reference.parent.mkdir(parents=True, exist_ok=True)
            with reference.open("xb") as stream:
                stream.write(raw)
            tasks.append(task)
            assignments.append({
                "project": task["repo"], "instance_id": task["instance_id"],
                "original_scenario": row["original_scenario"], "original_difficulty": task["difficulty"],
                "original_title": row["original_title"], "context_tokens": context["tokens"],
                "source_integrity": source_integrity(task, archive),
                "task_requirements_unchanged": True,
                "package_requirements": [
                    {name: package[name] for name in (
                        "path", "engines", "dependencies", "devDependencies", "scripts",
                    ) if name in package}
                    for package in packages
                ],
            })
        validate_population(tasks, archive)
    manifest = deepcopy(pool)
    for name in ("environment_qualification", "tool_environment_qualification"):
        manifest.pop(name, None)
    manifest.update(
        workloads=tasks, candidates=selected, requested_workflows=len(tasks),
        max_tasks_per_project=1,
        selected_projects=[row["project"] for row in selected],
        selected_scenarios=[row["original_scenario"] for row in selected],
        tokenizer=model_metadata["tokenizer"],
        tokenizer_sha256=model_metadata["tokenizer_sha256"],
        tokenizer_config_sha256=model_metadata["tokenizer_config_sha256"],
        purpose="additional_complete_original_Node_projects",
        selection_policy="previously declared original easy/medium short-context candidates",
    )
    write(here / "manifest.json", manifest)
    write(here / "preparation.json", {
        "kind": manifest["purpose"], "assignments": assignments,
        "projects": len(tasks), "docker_image_id": image,
        "difficulty_counts": dict(Counter(row["difficulty"] for row in tasks)),
        "original_source_tests_and_tasks_preserved": True,
        "already_captured_project_families": inventory["already_captured_project_families"],
        "project_dependency_installation_and_live_tools_pending": True,
        "short_workflow_and_JOIN_shape_require_natural_capture": True,
        "capture_ready": False, "GPU_action": False, "existing_running_control_unchanged": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.inventory, POOL, TOKENIZER_SOURCE, Path(__file__),
        )},
    })
    print(json.dumps({"projects": len(tasks), "manifest": str(here / "manifest.json"),
                      "capture_ready": False, "GPU_action": False}))


if __name__ == "__main__":
    main()
