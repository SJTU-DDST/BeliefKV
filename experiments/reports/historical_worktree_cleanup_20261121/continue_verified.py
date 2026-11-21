"""Resume the same immutable selection with equivalent cached path resolution."""

import argparse
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("original_retirement", HERE / "retire.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
ORIGINAL_RECEIPT = operator.RECEIPT
ARCHIVES = operator.ARCHIVES
CONTINUATION_RECEIPT = HERE / "continuation_receipt.json"
COMPLETE = HERE / "complete_receipt.json"
ROOT_CACHE = {}
LAST_ROOTS = set()
original_git = operator.git
original_guard = operator.guard


def cached_root(path, roots):
    global LAST_ROOTS
    if roots is not LAST_ROOTS:
        if roots - LAST_ROOTS:
            ROOT_CACHE.clear()
        LAST_ROOTS = roots
    if path in ROOT_CACHE:
        value = ROOT_CACHE[path]
        if value is None or value in roots:
            return value
    value = next((item for item in (path, *path.parents) if item in roots), None)
    ROOT_CACHE[path] = value
    return value


def configure_protection():
    original = operator.protection
    source = inspect.getsource(original)
    needle = "next((item for item in (path, *path.parents) if item in roots), None)"
    if source.count(needle) != 1:
        raise ValueError("original path resolver differs")
    namespace = {**operator.__dict__, "_cached_root": cached_root}
    exec(compile(source.replace(needle, "_cached_root(path, roots)"),
                 str(HERE / "equivalent_cached_protection"), "exec"), namespace)
    optimized = namespace["protection"]
    records = operator.registry()
    started = time.monotonic()
    expected = original(records)
    original_seconds = time.monotonic() - started
    started = time.monotonic()
    actual = optimized(records)
    if actual != expected:
        raise ValueError("cached resolver changes the protected set")
    first_seconds = time.monotonic() - started
    started = time.monotonic()
    if optimized(records) != expected:
        raise ValueError("warm cached resolver changes the protected set")
    proof = {
        "status": "same_protection_set_and_file_count",
        "protected_worktrees": len(actual[0]), "reference_files": actual[1],
        "original_seconds": original_seconds, "first_cached_seconds": first_seconds,
        "warm_cached_seconds": time.monotonic() - started,
        "change": "memoize only longest existing registered path ancestor",
        "new_registration_invalidates_cache": True,
        "continuation_sha256": operator.digest(Path(__file__)),
        "original_operator_sha256": operator.digest(HERE / "retire.py"),
    }
    operator.save(HERE / "cache_equivalence.json", proof)
    operator.protection = optimized
    print(json.dumps(proof), flush=True)


def idempotent_git(path, *args, **kwargs):
    if len(args) == 4 and args[0] == "update-ref" and args[-1] == "0" * 40:
        existing = original_git(path, "rev-parse", "--verify", args[1], check=False)
        if existing.returncode == 0:
            if existing.stdout.decode().strip() != args[2]:
                raise ValueError("existing archive reference differs")
            return existing
    return original_git(path, *args, **kwargs)


def guard(row):
    path = Path(row["worktree"])
    if any(Path(other["worktree"]).is_relative_to(path) and other["worktree"] != str(path)
           for other in operator.registry()):
        raise ValueError("a registered nested worktree appeared")
    original_guard(row)


def resume(plan):
    if CONTINUATION_RECEIPT.exists() or (ARCHIVES / "continued").exists():
        raise FileExistsError("preserve continuation evidence")
    first = json.loads(ORIGINAL_RECEIPT.read_bytes())
    if first["status"] != "failed_preserve_remaining" or first.get("error") != "KeyboardInterrupt()":
        raise ValueError("require the administrative checkpoint")
    remaining = []
    for expected, actual in zip(plan["worktrees"], first["worktrees"], strict=True):
        if expected["worktree"] != actual["worktree"]:
            raise ValueError("checkpoint selection differs")
        if actual["status"] == "archived_and_retired":
            if Path(actual["worktree"]).exists():
                raise ValueError("completed path was recreated")
        else:
            if not Path(expected["worktree"]).is_dir():
                raise ValueError("preserve a partial removal for manual review")
            remaining.append(copy.deepcopy(expected))
    configure_protection()
    operator.git, operator.guard = idempotent_git, guard
    operator.RECEIPT = CONTINUATION_RECEIPT
    operator.ARCHIVES = ARCHIVES / "continued"
    subset = {
        **plan, "worktrees": remaining,
        "source_allocated_bytes": sum(row["allocated_bytes"] for row in remaining),
        "continuation_sha256": operator.digest(Path(__file__)),
        "previous_receipt_sha256": operator.digest(ORIGINAL_RECEIPT),
    }
    operator.execute(subset)
    print(json.dumps({"continuation_finished": len(remaining)}), flush=True)


def review(plan):
    initial = json.loads(ORIGINAL_RECEIPT.read_bytes())
    continuation = json.loads(CONTINUATION_RECEIPT.read_bytes())
    if continuation["status"] != "archived_and_retired":
        raise ValueError("finish continuation first")
    completed = {row["worktree"]: row for row in initial["worktrees"]
                 if row["status"] == "archived_and_retired"}
    for row in continuation["worktrees"]:
        if row["worktree"] in completed or row["status"] != "archived_and_retired":
            raise ValueError("duplicate or incomplete retirement")
        completed[row["worktree"]] = row
    if set(completed) != {row["worktree"] for row in plan["worktrees"]}:
        raise ValueError("completed population differs from immutable selection")
    record = {
        **plan, "status": "archived_and_retired",
        "worktrees": [completed[row["worktree"]] for row in plan["worktrees"]],
        "checkpoint_sha256": operator.digest(ORIGINAL_RECEIPT),
        "continuation_receipt_sha256": operator.digest(CONTINUATION_RECEIPT),
        "continuation_operator_sha256": operator.digest(Path(__file__)),
        "finished_at": operator.now(),
    }
    operator.save(COMPLETE, record)
    operator.RECEIPT = COMPLETE
    operator.review()
    spec = importlib.util.spec_from_file_location("index_verification", HERE / "preserve_index.py")
    indexes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indexes)
    indexes.review()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("resume", "review"))
    args = parser.parse_args()
    plan = json.loads(operator.PLAN.read_bytes())
    if plan["operator_sha256"] != operator.digest(HERE / "retire.py"):
        raise ValueError("original immutable operator changed")
    if args.action == "resume":
        resume(plan)
    else:
        review(plan)


if __name__ == "__main__":
    main()
