#!/usr/bin/env python3
"""Freeze shared task inputs and create one private checkout per harness/task."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beliefkv.experiments.harness_source import HARNESS_NAMES


def git(source, *args):
    return subprocess.run(
        ("git", "-C", str(source), *args), check=True, text=True, capture_output=True,
    ).stdout.strip()


def prepare(manifest_path, output, instances):
    body = manifest_path.read_bytes()
    manifest = json.loads(body)
    revision = manifest.get("dataset_revision") or manifest["upstream_revision"]
    selected = {entry["instance_id"]: entry for entry in manifest["workloads"]}
    instances = instances or tuple(selected)
    if len(set(instances)) != len(instances) or any(
        instance not in selected or re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", instance) is None
        for instance in instances
    ):
        raise ValueError("require distinct declared benchmark task IDs")
    matrix = output / "capture_matrix.json"
    if matrix.exists():
        raise FileExistsError(matrix)

    tasks = []
    for instance in instances:
        entry = selected[instance]
        source = Path(entry.get("source_repo") or manifest["source_repo"]).resolve()
        commit = entry["base_commit"]
        if git(source, "rev-parse", "--verify", f"{commit}^{{commit}}") != commit:
            raise ValueError(f"benchmark base commit is not pinned: {instance}")
        if not entry["problem_statement"].strip():
            raise ValueError(f"benchmark prompt is empty: {instance}")
        tasks.append((instance, source, commit, entry))

    bands = {}
    for harness in HARNESS_NAMES:
        directory = output / harness
        directory.mkdir(parents=True, exist_ok=True)
        workloads = []
        for instance, source, commit, entry in tasks:
            workspace = directory / instance
            if not workspace.exists():
                git(source, "worktree", "add", "--detach", str(workspace), commit)
            if git(workspace, "rev-parse", "HEAD") != commit or git(
                workspace, "status", "--porcelain",
            ):
                raise ValueError(f"benchmark workspace must start at a clean base: {workspace}")
            prompt = directory / f"{instance}.prompt.txt"
            body_text = entry["problem_statement"].encode("utf-8")
            if not prompt.exists():
                with prompt.open("xb") as stream:
                    stream.write(body_text)
            if prompt.read_bytes() != body_text:
                raise ValueError(f"benchmark prompt bytes changed: {prompt}")
            workloads.append({
                **{field: entry[field] for field in (
                    "repo", "docker_image", "sandbox_environment", "preflight_command",
                ) if field in entry},
                "instance_id": instance, "worktree": str(workspace),
                "source_repo": str(source), "base_commit": commit,
                "problem_statement": entry["problem_statement"], "prompt_file": str(prompt),
                "prompt_sha256": sha256(body_text).hexdigest(),
            })
        bands[harness] = workloads

    common = {
        "schema_version": 1, "dataset": manifest["dataset"], "dataset_revision": revision,
        "source_manifest": str(manifest_path), "source_manifest_sha256": sha256(body).hexdigest(),
    }
    host_files = {}
    for harness, workloads in bands.items():
        path = output / harness / "workloads.json"
        content = json.dumps({**common, "workloads": workloads}, indent=2, allow_nan=False) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(f"benchmark manifest changed: {path}")
        else:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
        host_files[harness] = {"path": str(path), "sha256": sha256(content.encode("utf-8")).hexdigest()}
    result = {**common, "instances": list(instances), "harness_manifests": host_files}
    with matrix.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instance", action="append")
    args = parser.parse_args()
    result = prepare(args.manifest.resolve(), args.output.resolve(), args.instance)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
