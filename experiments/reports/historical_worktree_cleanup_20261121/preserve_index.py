"""Pin original staged/undo objects before the immutable full-archive operator."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("historical_retirement", HERE / "retire.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
PINS = HERE / "index_object_pins.json"


def pin_indexes(plan):
    if PINS.exists() or operator.RECEIPT.exists():
        raise FileExistsError("preserve prior archival evidence")
    record = {
        "status": "pinning", "operator_sha256": operator.digest(Path(__file__)),
        "retirement_operator_sha256": operator.digest(HERE / "retire.py"),
        "inventory_sha256": operator.digest(operator.PLAN), "worktrees": [],
    }
    known_objects = {}
    operator.save(PINS, record)
    for row in plan["worktrees"]:
        path, repo = Path(row["worktree"]), Path(row["repository"])
        original_index = Path(row["git_admin"]) / "index"
        if (
            operator.digest(original_index) != row["index_sha256"]
            or operator.git(path, "rev-parse", "HEAD").stdout.decode().strip() != row["HEAD"]
        ):
            raise ValueError("source HEAD or index changed; preserve the worktree")
        with tempfile.TemporaryDirectory(prefix="index-pin-", dir=HERE) as temporary:
            index = Path(temporary) / "index"
            index.write_bytes(original_index.read_bytes())
            tree = subprocess.check_output(
                ["git", "--no-optional-locks", "-C", str(path), "write-tree"],
                env={**os.environ, "GIT_INDEX_FILE": str(index)}, text=True,
            ).strip()
        prefix = row["archive_ref"].removesuffix("/head")
        refs = {prefix + "/index-tree": tree}
        key = (row["repository"], row["HEAD"])
        if key not in known_objects:
            known_objects[key] = {
                entry.split(b"\t", 1)[0].split()[2].decode()
                for entry in operator.git(path, "ls-tree", "-r", "-z", row["HEAD"]).stdout.split(b"\0")
                if entry
            }
        entries = operator.git(path, "ls-files", "--stage", "--resolve-undo", "-z").stdout
        objects = {
            entry.split(b"\t", 1)[0].split()[1].decode()
            for entry in entries.split(b"\0") if entry
        }
        for value in objects - known_objects[key]:
            refs[prefix + "/index-objects/" + value] = value
        private = operator.git(
            path, "for-each-ref", "--format=%(objectname) %(refname)",
            "refs/worktree", "refs/bisect", "refs/rewritten",
        ).stdout.decode().splitlines()
        for value in private:
            oid, name = value.split(" ", 1)
            refs[prefix + "/private/" + name] = oid
        for name, value in refs.items():
            operator.git(repo, "update-ref", name, value, "0" * 40)
        if operator.digest(original_index) != row["index_sha256"]:
            raise ValueError("source index changed while preserving it")
        record["worktrees"].append({
            "path": row["worktree"], "repository": str(repo), "refs": refs,
            "original_index_sha256": row["index_sha256"],
        })
        operator.save(PINS, record)
    record.update(status="all_original_index_objects_pinned", finished_at=operator.now())
    operator.save(PINS, record)
    print(json.dumps({"index_snapshots_pinned": len(record["worktrees"])}), flush=True)


def review():
    record = json.loads(PINS.read_bytes())
    if (
        record["status"] != "all_original_index_objects_pinned"
        or record["operator_sha256"] != operator.digest(Path(__file__))
        or record["retirement_operator_sha256"] != operator.digest(HERE / "retire.py")
        or record["inventory_sha256"] != operator.digest(operator.PLAN)
    ):
        raise ValueError("archival operators or immutable selection differ")
    for row in record["worktrees"]:
        for name, value in row["refs"].items():
            if operator.git(Path(row["repository"]), "rev-parse", name).stdout.decode().strip() != value:
                raise ValueError("original staged or undo object is no longer pinned")
    result = {
        "status": "all_original_index_objects_independently_verified",
        "worktrees": len(record["worktrees"]),
        "pinned_index_and_undo_refs": sum(len(row["refs"]) for row in record["worktrees"]),
        "pins_sha256": operator.digest(PINS), "finished_at": operator.now(),
    }
    operator.save(HERE / "index_review.json", result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "review"))
    args = parser.parse_args()
    plan = json.loads(operator.PLAN.read_bytes())
    if plan["operator_sha256"] != operator.digest(HERE / "retire.py"):
        raise ValueError("original retirement operator changed")
    if args.action == "execute":
        pin_indexes(plan)
        operator.execute(plan)
    else:
        operator.review()
        review()


if __name__ == "__main__":
    main()
