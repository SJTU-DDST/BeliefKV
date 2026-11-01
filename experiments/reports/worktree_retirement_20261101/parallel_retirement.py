"""Run disjoint retirement batches with unchanged per-worktree safety checks."""

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retirement_continuation", HERE / "continue_retirement.py")
continuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuation)
base = continuation.base
BATCHES = 4


def setup(linked):
    prefix = "linked_" if linked else ""
    base.PLAN = HERE / (prefix + "inventory.json")
    base.RECEIPT = HERE / (prefix + "receipt.json")
    if linked:
        base.ARCHIVES = base.ROOT / "experiments/archive/20261101_retired_linked_worktrees"
        base.snapshot = continuation.linked_snapshot
        base.ignored_files = continuation.linked_ignored_files
    base.reference_files = continuation.fast_reference_files
    return prefix, HERE / (prefix + "parallel_preparation.json")


def prepare(linked, prefix, preparation_path):
    if preparation_path.exists():
        raise FileExistsError("preserve the original parallel preparation")
    old = set(continuation.original_reference_files())
    fast = set(continuation.fast_reference_files())
    if old != fast:
        raise ValueError("reference enumeration differs from the original policy")
    proof = {
        "checked_at": base.now(), "same_file_set": True, "files": len(old),
        "sorted_paths_sha256": hashlib.sha256(
            "\n".join(map(str, sorted(old))).encode()
        ).hexdigest(),
    }
    if linked:
        plan = json.loads(base.PLAN.read_bytes())
        primary = json.loads((HERE / "receipt.json").read_bytes())
        if (
            base.RECEIPT.exists() or base.ARCHIVES.exists() or not plan["worktrees"]
            or plan["extension_sha256"] != base.digest(HERE / "continue_retirement.py")
            or primary["status"] != "pinned_archived_and_retired"
        ):
            raise ValueError("require a fresh linked plan and a completed primary batch")
        commits = sorted({value for row in plan["worktrees"] for value in row["reflog_commits"]})
        base.git(base.ROOT, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", *commits)
        base.ARCHIVES.mkdir()
        record = {
            **plan, "started_at": base.now(), "all_selected_commits_connectivity_checked": True,
            "full_checkout_restore_rehearsal": primary["full_checkout_restore_rehearsal"],
            "restore_rehearsal_source": str(HERE / "receipt.json"),
            "free_bytes_before": shutil.disk_usage(base.ROOT).free,
            "continuation_sha256": base.digest(HERE / "continue_retirement.py"),
        }
    else:
        record = json.loads(base.RECEIPT.read_bytes())
        if (
            record["status"] != "failed_preserve_remaining_worktrees"
            or record.get("error") != "KeyboardInterrupt()"
            or not record["all_selected_commits_connectivity_checked"]
            or not record["full_checkout_restore_rehearsal"]["passed"]
        ):
            raise ValueError("require the preserved administrative checkpoint")
        checkpoint = HERE / "sequential_execution_interrupted.json"
        if checkpoint.exists():
            raise FileExistsError("preserve original sequential checkpoint")
        shutil.copy2(base.RECEIPT, checkpoint)
        record["sequential_checkpoint"] = {
            "path": str(checkpoint), "sha256": base.digest(checkpoint),
            "reason": "Run disjoint old worktrees concurrently, keeping all per-path guards.",
        }
        record.pop("error")
        record.pop("failed_at")
    pending = [
        row["path"] for row in record["worktrees"] if row["status"] != "pinned_archived_and_retired"
    ]
    if not pending:
        raise ValueError("no pending worktrees")
    record.update(
        status="parallel_retiring", parallel_finisher_sha256=base.digest(Path(__file__)),
        enumeration_equivalence=proof,
    )
    (HERE / (prefix + "parallel_workers")).mkdir()
    preparation = {
        "created_at": base.now(), "operator_sha256": base.digest(Path(__file__)),
        "record": record, "enumeration_proof": proof,
        "batches": [pending[index::BATCHES] for index in range(BATCHES)],
    }
    base.save(preparation_path, preparation)
    base.save(base.RECEIPT, record)
    print(json.dumps({
        "pending_worktrees": len(pending),
        "batch_sizes": list(map(len, preparation["batches"])),
    }), flush=True)


def worker(index, prefix, preparation):
    if not 0 <= index < BATCHES:
        raise ValueError("invalid batch")
    original_receipt = base.RECEIPT
    base.RECEIPT = HERE / (prefix + "parallel_workers") / f"batch_{index}.json"
    if base.RECEIPT.exists():
        raise FileExistsError("preserve the original worker progress record")
    selected = set(preparation["batches"][index])
    record = copy.deepcopy(preparation["record"])
    record["worktrees"] = [row for row in record["worktrees"] if row["path"] in selected]
    record["source_allocated_bytes"] = sum(row["allocated_bytes"] for row in record["worktrees"])
    record.update(
        worker_progress_only=True, batch=index, merged_receipt=str(original_receipt),
        shared_archive_allocation_is_not_per_worker_reclamation=True,
    )
    continuation.continue_execution(record, preparation["enumeration_proof"])


def merge(prefix, preparation):
    record = preparation["record"]
    by_path = {row["path"]: row for row in record["worktrees"]}
    workers = []
    for index, expected in enumerate(preparation["batches"]):
        path = HERE / (prefix + "parallel_workers") / f"batch_{index}.json"
        part = json.loads(path.read_bytes())
        if (
            part["status"] != "pinned_archived_and_retired"
            or {row["path"] for row in part["worktrees"]} != set(expected)
            or part["parallel_finisher_sha256"] != base.digest(Path(__file__))
            or any(row["status"] != "pinned_archived_and_retired" for row in part["worktrees"])
        ):
            raise ValueError("an independent batch is incomplete or differs")
        workers.append({"path": str(path), "sha256": base.digest(path), "count": len(expected)})
        by_path.update({row["path"]: row for row in part["worktrees"]})
    record["worktrees"] = [by_path[row["path"]] for row in record["worktrees"]]
    if any(Path(row["path"]).exists() for row in record["worktrees"]):
        raise ValueError("a retired path still exists")
    allocated = sum(
        path.stat().st_blocks * 512 for path in base.ARCHIVES.rglob("*") if path.is_file()
    )
    record.update(
        status="pinned_archived_and_retired", finished_at=base.now(), parallel_workers=workers,
        archive_allocated_bytes=allocated,
        net_reclaimed_bytes=record["source_allocated_bytes"] - allocated,
        free_bytes_after=shutil.disk_usage(base.ROOT).free,
    )
    base.save(base.RECEIPT, record)
    print(json.dumps({key: record[key] for key in (
        "status", "source_allocated_bytes", "archive_allocated_bytes", "net_reclaimed_bytes",
    )}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "worker", "merge"))
    parser.add_argument("--linked", action="store_true")
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    prefix, path = setup(args.linked)
    if args.action == "prepare":
        prepare(args.linked, prefix, path)
        return
    preparation = json.loads(path.read_bytes())
    if preparation["operator_sha256"] != base.digest(Path(__file__)):
        raise ValueError("parallel operator changed after preparation")
    if args.action == "worker":
        worker(args.batch, prefix, preparation)
    else:
        merge(prefix, preparation)


if __name__ == "__main__":
    main()
