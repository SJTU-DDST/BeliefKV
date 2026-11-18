"""Bind unchanged public SWE-rebench issues to their original repository trees."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from beliefkv.experiments.source_provenance import sha256_file


KIND = "original_swe_rebench_public_inventory_v1"
SNAPSHOT_KIND = "original_swe_rebench_git_tree_v1"
PUBLIC_FIELDS = frozenset((
    "repo", "instance_id", "base_commit", "problem_statement", "version", "created_at",
    "environment_setup_commit", "install_config", "docker_image", "image_name",
))


def _load_pinned(path, digest):
    path = Path(path)
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest) or sha256_file(path) != digest:
        raise ValueError("original public task or repository evidence changed")
    return path, json.loads(path.read_bytes())


def source_dataset(manifest):
    value = manifest.get("source_dataset")
    if (
        not isinstance(value, dict) or set(value) != {"kind", "path", "sha256"}
        or value["kind"] != KIND
    ):
        raise ValueError("SWE-rebench capture requires its explicit original public inventory")
    _path, inventory = _load_pinned(value["path"], value["sha256"])
    if (
        not isinstance(inventory.get("candidates"), list)
        or not set(inventory.get("read_fields", ())) <= PUBLIC_FIELDS
        or inventory.get("oracle_values_read_or_exported") is not False
    ):
        raise ValueError("original task inventory must exclude solutions and gold tests")
    return value


def original_task(task, dataset=None):
    provenance = task.get("dataset_provenance") or {}
    expected = {
        "kind": KIND, "path": provenance.get("public_inventory"),
        "sha256": provenance.get("public_inventory_sha256"),
    }
    if (
        task.get("task_kind") != "swebench" or provenance.get("dataset") != "SWE-rebench"
        or provenance.get("original_instance_id") != task.get("instance_id")
        or provenance.get("solution_or_gold_tests_exported") is not False
        or dataset is not None and dataset != expected
        or task.get("frozen_repository_context") is not None
    ):
        raise ValueError("require an explicitly bound original SWE-rebench task")
    _path, inventory = _load_pinned(expected["path"], expected["sha256"])
    if (
        not set(inventory.get("read_fields", ())) <= PUBLIC_FIELDS
        or inventory.get("oracle_values_read_or_exported") is not False
    ):
        raise ValueError("original task inventory exposes undeclared fields")
    matches = [row for row in inventory["candidates"] if row["instance_id"] == task["instance_id"]]
    if len(matches) != 1:
        raise ValueError("original task has no unique public inventory entry")
    original = matches[0]
    if (
        any(task.get(name) != original.get(name) for name in ("instance_id", "repo", "base_commit", "problem_statement"))
        or not re.fullmatch("[0-9a-f]{40}", task["base_commit"])
        or hashlib.sha256(task["problem_statement"].encode()).hexdigest()
        != provenance.get("original_problem_sha256")
    ):
        raise ValueError("original repository task requirements or base changed")
    return original


def repository_tree(task):
    source = Path(task["source_repo"])

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(source), *args], text=True, stderr=subprocess.STDOUT, timeout=30,
        ).strip()

    if git("rev-parse", "HEAD") != task["base_commit"] or git("status", "--porcelain"):
        raise ValueError("original repository is not a clean checkout of its public base")
    return git("rev-parse", task["base_commit"] + "^{tree}")


def bind_task(task, path, dataset):
    original_task(task, dataset)
    provenance = task["dataset_provenance"]
    snapshot = {
        "kind": SNAPSHOT_KIND, "instance_id": task["instance_id"], "repo": task["repo"],
        "base_commit": task["base_commit"], "git_tree": repository_tree(task),
        "problem_sha256": provenance["original_problem_sha256"],
        "source_repo": str(Path(task["source_repo"]).resolve()), "public_inventory": deepcopy(dataset),
        "source_files_or_tests_modified": False, "solution_or_gold_tests_exported": False,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(snapshot, stream, allow_nan=False, sort_keys=True)
        stream.write("\n")
    bound = deepcopy(task)
    bound["dataset_provenance"].update(
        source_provenance_path=str(path.resolve()), source_provenance_sha256=sha256_file(path),
    )
    return bound


def task_inputs(task, dataset):
    original_task(task, dataset)
    provenance = task["dataset_provenance"]
    path, proof = _load_pinned(
        provenance["source_provenance_path"], provenance["source_provenance_sha256"],
    )
    expected = {
        "kind": SNAPSHOT_KIND, "instance_id": task["instance_id"], "repo": task["repo"],
        "base_commit": task["base_commit"], "git_tree": repository_tree(task),
        "problem_sha256": provenance["original_problem_sha256"],
        "source_repo": str(Path(task["source_repo"]).resolve()), "public_inventory": dataset,
        "source_files_or_tests_modified": False, "solution_or_gold_tests_exported": False,
    }
    if proof != expected:
        raise ValueError("original repository tree or provenance differs from the captured task")
    return [Path(dataset["path"]), path]
