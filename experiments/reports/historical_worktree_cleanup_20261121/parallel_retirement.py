"""Finish disjoint portions of the original selection in three low-priority batches."""

import argparse
import importlib.util
import inspect
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cached_retirement", HERE / "continue_verified.py")
continuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuation)
operator = continuation.operator
PLAN = HERE / "parallel_plan.json"
RECEIPTS = HERE / "parallel_receipts"
BATCHES = 3
CHECKPOINTS = (HERE / "receipt.json", HERE / "continuation_receipt.json")


def bindings():
    paths = [
        HERE / name for name in (
            "retire.py", "preserve_index.py", "continue_verified.py",
            "parallel_retirement.py", "cache_equivalence.json", "inventory.json",
        )
    ] + list(CHECKPOINTS)
    return {str(path): operator.digest(path) for path in paths}


def checkpoint_rows(original):
    completed = {}
    for path in CHECKPOINTS:
        record = json.loads(path.read_bytes())
        if record["status"] != "failed_preserve_remaining" or record.get("error") != "KeyboardInterrupt()":
            raise ValueError("require both explicit administrative checkpoints")
        for row in record["worktrees"]:
            if row["status"] == "archived_and_retired":
                if row["worktree"] in completed or Path(row["worktree"]).exists():
                    raise ValueError("duplicate completion or recreated path")
                completed[row["worktree"]] = row
    if not set(completed) <= {row["worktree"] for row in original["worktrees"]}:
        raise ValueError("checkpoint contains an unselected path")
    return completed


def prepare():
    if PLAN.exists() or RECEIPTS.exists() or (operator.ARCHIVES / "parallel").exists():
        raise FileExistsError("preserve original parallel selection")
    original = json.loads(operator.PLAN.read_bytes())
    if original["operator_sha256"] != operator.digest(HERE / "retire.py"):
        raise ValueError("original operator changed")
    completed = checkpoint_rows(original)
    remaining = [row for row in original["worktrees"] if row["worktree"] not in completed]
    if any(not Path(row["worktree"]).is_dir() for row in remaining):
        raise ValueError("preserve a partial removal for review")
    proof = json.loads((HERE / "cache_equivalence.json").read_bytes())
    if (
        proof["status"] != "same_protection_set_and_file_count"
        or proof["continuation_sha256"] != operator.digest(HERE / "continue_verified.py")
        or proof["original_operator_sha256"] != original["operator_sha256"]
    ):
        raise ValueError("cached resolver is not qualified by the original comparison")
    operator.save(PLAN, {
        "created_at": operator.now(), "bindings": bindings(), "batches": BATCHES,
        "completed_before_parallel": len(completed), "remaining": remaining,
        "all_partitions_disjoint": True,
    })
    RECEIPTS.mkdir()
    (operator.ARCHIVES / "parallel").mkdir()
    print(json.dumps({"already_retired": len(completed), "remaining": len(remaining),
                      "batches": BATCHES}), flush=True)


def install_cached_resolver():
    source = inspect.getsource(operator.protection)
    needle = "next((item for item in (path, *path.parents) if item in roots), None)"
    if source.count(needle) != 1:
        raise ValueError("original resolver differs")
    namespace = {**operator.__dict__, "_cached_root": continuation.cached_root}
    exec(compile(source.replace(needle, "_cached_root(path, roots)"),
                 str(HERE / "equivalent_cached_protection"), "exec"), namespace)
    operator.protection = namespace["protection"]
    operator.git, operator.guard = continuation.idempotent_git, continuation.guard


def worker(plan, index):
    if index is None or not 0 <= index < BATCHES:
        raise ValueError("invalid partition")
    original = json.loads(operator.PLAN.read_bytes())
    rows = plan["remaining"][index::BATCHES]
    operator.RECEIPT = RECEIPTS / f"batch_{index}.json"
    operator.ARCHIVES = operator.ARCHIVES / "parallel" / f"batch_{index}"
    install_cached_resolver()
    operator.execute({
        **original, "worktrees": rows, "batch": index,
        "parallel_plan_sha256": operator.digest(PLAN),
        "source_allocated_bytes": sum(row["allocated_bytes"] for row in rows),
    })
    print(json.dumps({"batch_finished": index, "retired": len(rows)}), flush=True)


def review(plan):
    original = json.loads(operator.PLAN.read_bytes())
    completed = checkpoint_rows(original)
    for index in range(BATCHES):
        record = json.loads((RECEIPTS / f"batch_{index}.json").read_bytes())
        expected = plan["remaining"][index::BATCHES]
        if (
            record["status"] != "archived_and_retired"
            or [row["worktree"] for row in record["worktrees"]]
            != [row["worktree"] for row in expected]
        ):
            raise ValueError("incomplete or changed partition")
        for row in record["worktrees"]:
            if row["worktree"] in completed or row["status"] != "archived_and_retired":
                raise ValueError("duplicate or incomplete retirement")
            completed[row["worktree"]] = row
    if set(completed) != {row["worktree"] for row in original["worktrees"]}:
        raise ValueError("completed population differs from immutable selection")
    complete = HERE / "complete_receipt.json"
    operator.save(complete, {
        **original, "status": "archived_and_retired",
        "worktrees": [completed[row["worktree"]] for row in original["worktrees"]],
        "parallel_plan_sha256": operator.digest(PLAN),
        "batch_receipts_sha256": {
            str(path): operator.digest(path) for path in sorted(RECEIPTS.glob("batch_*.json"))
        },
        "finished_at": operator.now(),
    })
    operator.RECEIPT = complete
    operator.review()
    spec = importlib.util.spec_from_file_location("original_index_review", HERE / "preserve_index.py")
    indexes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indexes)
    indexes.review()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "worker", "review"))
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
        return
    plan = json.loads(PLAN.read_bytes())
    if plan["bindings"] != bindings() or plan["batches"] != BATCHES:
        raise ValueError("operators, selection or checkpoints changed")
    if args.action == "worker":
        worker(plan, args.batch)
    else:
        review(plan)


if __name__ == "__main__":
    main()
