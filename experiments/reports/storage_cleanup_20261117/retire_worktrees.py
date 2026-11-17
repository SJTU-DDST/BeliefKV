"""Retire inactive historical checkouts using the verified November 1 guards."""

import argparse
import copy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = HERE.parent / "worktree_retirement_20261101"
spec = importlib.util.spec_from_file_location("previous_retirement", PREVIOUS / "continue_retirement.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
base = previous.base
base.HERE = HERE
base.PLAN = HERE / "worktree_inventory.json"
base.RECEIPT = HERE / "worktree_receipt.json"
base.ARCHIVES = ROOT / "experiments/archive/20261117_retired_worktrees"
base.CUTOFF = datetime.fromisoformat("2026-11-10T00:00:00+08:00").timestamp()
base.reference_files = previous.fast_reference_files
base.snapshot = previous.linked_snapshot
BATCHES = 4


def extras(path):
    names = base.git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
    files = [path / os.fsdecode(name) for name in names.split(b"\0") if name]
    for item in files:
        info = item.lstat()
        if (
            not item.is_relative_to(path) or info.st_uid != os.getuid() or info.st_nlink != 1
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
        ):
            raise ValueError("preserve non-private or special ignored files")
    return sorted(files)


base.ignored_files = extras
original_mounts = base.helper.helper.mounted_sources


def mounted_sources():
    for attempt in range(3):
        try:
            return original_mounts()
        except subprocess.CalledProcessError as error:
            if error.cmd[:2] != ["docker", "inspect"] or attempt == 2:
                raise
    raise AssertionError("unreachable")


base.helper.helper.mounted_sources = mounted_sources


def inventory():
    if base.PLAN.exists():
        raise FileExistsError("preserve the original inventory")
    plan = base.inventory()
    for row in plan["worktrees"]:
        row["archive_ref"] = row["archive_ref"].replace("20261101-worktrees", "20261117-worktrees")
    plan.update(
        wrapper_sha256=base.digest(Path(__file__)),
        continuation_sha256=base.digest(PREVIOUS / "continue_retirement.py"),
        free_bytes_observed=shutil.disk_usage(ROOT).free,
        selection_cutoff="2026-11-10T00:00:00+08:00",
    )
    base.save(base.PLAN, plan)
    print(json.dumps({
        "selected": len(plan["worktrees"]), "skipped": len(plan["skipped"]),
        "source_allocated_bytes": plan["source_allocated_bytes"],
    }), flush=True)


def prepare(plan):
    if base.RECEIPT.exists() or base.ARCHIVES.exists() or not plan["worktrees"]:
        raise ValueError("require a fresh nonempty plan")
    commits = sorted({commit for row in plan["worktrees"] for commit in row["reflog_commits"]})
    base.git(ROOT, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", *commits)
    base.ARCHIVES.mkdir()
    row = plan["worktrees"][0]
    probe = base.ARCHIVES / "restore_probe"
    base.git(ROOT, "worktree", "add", "--quiet", "--detach", str(probe), row["commit"])
    if (
        base.git(probe, "rev-parse", "HEAD^{tree}").stdout.decode().strip() != row["tree"]
        or base.git(probe, "status", "--porcelain=v1", "--untracked-files=all").stdout
    ):
        raise ValueError("preserve the failed restoration probe")
    base.git(ROOT, "worktree", "remove", str(probe))
    record = {
        **plan, "status": "prepared", "started_at": base.now(),
        "free_bytes_before": shutil.disk_usage(ROOT).free,
        "all_selected_commits_connectivity_checked": True,
        "full_checkout_restore_rehearsal": {
            "passed": True, "commit": row["commit"], "tree": row["tree"],
        },
    }
    (HERE / "worktree_workers").mkdir()
    base.save(base.RECEIPT, record)
    print(json.dumps({"prepared": len(plan["worktrees"]), "batches": BATCHES}), flush=True)


def worker(index):
    if index is None or not 0 <= index < BATCHES:
        raise ValueError("invalid batch")
    record = json.loads(base.RECEIPT.read_bytes())
    base.RECEIPT = HERE / "worktree_workers" / f"batch_{index}.json"
    if base.RECEIPT.exists():
        raise FileExistsError("preserve original batch evidence")
    record = copy.deepcopy(record)
    record["worktrees"] = record["worktrees"][index::BATCHES]
    record["source_allocated_bytes"] = sum(row["allocated_bytes"] for row in record["worktrees"])
    record.update(batch=index, worker_progress_only=True)
    previous.continue_execution(record, {
        "same_policy": True, "enumeration": "GNU find with original exact mtime and filename filters",
        "files_scanned": record["reference_proof"]["files_scanned"],
    })


def review():
    output = HERE / "worktree_review.json"
    if output.exists():
        raise FileExistsError("preserve original independent review")
    record = json.loads(base.RECEIPT.read_bytes())
    by_path = {}
    worker_proofs = []
    for index in range(BATCHES):
        path = HERE / "worktree_workers" / f"batch_{index}.json"
        part = json.loads(path.read_bytes())
        if (
            part["status"] != "pinned_archived_and_retired"
            or part["wrapper_sha256"] != base.digest(Path(__file__))
            or [row["path"] for row in part["worktrees"]]
            != [row["path"] for row in record["worktrees"][index::BATCHES]]
        ):
            raise ValueError("batch incomplete or differs from inventory")
        by_path.update({row["path"]: row for row in part["worktrees"]})
        worker_proofs.append({"path": str(path), "sha256": base.digest(path)})
    registered = {row["worktree"] for row in base.worktrees()}
    rows = []
    for expected in record["worktrees"]:
        row = by_path[expected["path"]]
        path = Path(row["path"])
        if (
            row["status"] != "pinned_archived_and_retired" or path.exists()
            or str(path) in registered or row["ignored_files"] != expected["ignored_files"]
            or base.git(ROOT, "rev-parse", row["archive_ref"] + "^{tree}").stdout.decode().strip()
            != expected["tree"]
        ):
            raise ValueError("retirement or original Git tree differs")
        for name, commit in row["pinned_refs"].items():
            if base.git(ROOT, "rev-parse", name).stdout.decode().strip() != commit:
                raise ValueError("archived history reference differs")
        archives = [row["admin_archive"]]
        if row["ignored_files"]:
            archives.append(row["ignored_archive"])
        for archive in archives:
            target = Path(archive["path"])
            if base.digest(target) != archive["sha256"] or target.stat().st_size != archive["bytes"]:
                raise ValueError("archive digest or length differs")
            if archive is row.get("ignored_archive"):
                names = subprocess.check_output(
                    ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(target)],
                    text=True,
                ).splitlines()
                if len(names) != len(row["ignored_files"]) or set(names) != set(row["ignored_files"]):
                    raise ValueError("ignored archive member list differs")
        rows.append({
            "path": row["path"], "commit": row["commit"], "archive_ref": row["archive_ref"],
            "source_allocated_bytes": row["allocated_bytes"],
            "restore_commands": row["restore_commands"],
            "archive_sha256": {archive["path"]: archive["sha256"] for archive in archives},
        })
    allocated = sum(
        path.stat().st_blocks * 512 for path in base.ARCHIVES.rglob("*") if path.is_file()
    )
    report = {
        "status": "independently_verified", "finished_at": base.now(),
        "retired_worktrees": len(rows), "source_allocated_bytes": record["source_allocated_bytes"],
        "archive_allocated_bytes": allocated,
        "net_reclaimed_bytes": record["source_allocated_bytes"] - allocated,
        "all_original_commits_trees_and_archive_hashes_verified": True,
        "all_removed_without_force": True,
        "full_checkout_restore_rehearsal": record["full_checkout_restore_rehearsal"],
        "workers": worker_proofs, "worktrees": rows,
    }
    lines = [
        "# Historical Worktree Recovery, 2026-11-17", "",
        "Original commits and reflog history remain pinned. Restore commands recreate",
        "tracked files and then extract ignored data, including original symbolic links.", "",
    ]
    for row in rows:
        lines.extend([f"## {Path(row['path']).name}", "", "```bash", *row["restore_commands"], "```", ""])
    (base.ARCHIVES / "README.md").write_text("\n".join(lines))
    record.update(
        status="pinned_archived_and_retired", finished_at=report["finished_at"],
        worktrees=[by_path[row["path"]] for row in record["worktrees"]],
        net_reclaimed_bytes=report["net_reclaimed_bytes"],
    )
    base.save(base.RECEIPT, record)
    base.save(output, report)
    print(json.dumps({key: report[key] for key in (
        "status", "retired_worktrees", "net_reclaimed_bytes",
    )}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "prepare", "worker", "review"))
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    if args.action == "inventory":
        inventory()
        return
    plan = json.loads(base.PLAN.read_bytes())
    if plan["wrapper_sha256"] != base.digest(Path(__file__)):
        raise ValueError("wrapper changed after selection")
    if args.action == "prepare":
        prepare(plan)
    elif args.action == "worker":
        worker(args.batch)
    else:
        review()


if __name__ == "__main__":
    main()
