"""Continue retirement with equivalent find-based enumeration; preserve linked extras."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retirement_base", HERE / "retire_worktrees.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
original_reference_files = base.reference_files
original_snapshot = base.snapshot
original_eligible = base.eligible


def recent_files(directory, patterns):
    command = [
        "find", str(directory), "-type", "f",
        "-newermt", "@" + str(int(base.CUTOFF) - 1), "(",
    ]
    for index, pattern in enumerate(patterns):
        if index:
            command.append("-o")
        command.extend(["-name", pattern])
    command.extend([")", "-print0"])
    return [
        Path(os.fsdecode(name))
        for name in subprocess.check_output(command).split(b"\0") if name
    ]


def fast_reference_files():
    files = {
        base.ROOT / name for name in subprocess.check_output(
            ["rg", "--files", "beliefkv", "scripts", "tests", "docs", "configs",
             "-g", "*.py", "-g", "*.md", "-g", "*.json", "-g", "*.yaml", "-g", "*.toml"],
            cwd=base.ROOT, text=True,
        ).splitlines()
    }
    files.add(base.ROOT / "AGENTS.md")
    files.update(
        path for path in recent_files(base.BASE / "ops", ["*.json", "*.py", "*.md"])
        if path.stat().st_mtime >= base.CUTOFF
    )
    files.update(
        path for path in recent_files(base.ROOT / "experiments/reports", ["*.json"])
        if not path.is_relative_to(HERE) and path.stat().st_mtime >= base.CUTOFF
        and re.search(r"source|input|manifest|evidence|entry|freeze|registration|plan|state",
                      path.name)
    )
    return sorted(path for path in files if not path.is_relative_to(HERE))


def linked_snapshot(path):
    info = path.lstat()
    if not stat.S_ISLNK(info.st_mode):
        return original_snapshot(path)
    return {
        "kind": "symlink", "target": os.readlink(path),
        "size_bytes": info.st_size, "allocated_bytes": info.st_blocks * 512,
        "inode": info.st_ino, "device": info.st_dev, "mtime_ns": info.st_mtime_ns,
        "uid": info.st_uid, "nlink": info.st_nlink,
    }


def linked_ignored_files(path):
    names = base.git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
    files = [path / os.fsdecode(name) for name in names.split(b"\0") if name]
    for item in files:
        info = item.lstat()
        if (
            not item.is_relative_to(path) or info.st_uid != os.getuid() or info.st_nlink != 1
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
        ):
            raise ValueError("preserve non-private or special ignored data")
    if not any(item.is_symlink() for item in files):
        raise ValueError("linked follow-up requires at least one preserved symbolic link")
    return sorted(files)


def fresh_guard(path, row):
    current = next((item for item in base.worktrees() if item["worktree"] == str(path)), None)
    if current is None or not original_eligible(current) or current["HEAD"] != row["commit"]:
        raise ValueError("worktree registration, age, lock, branch or HEAD changed")
    checks = base.guard(path)
    admin = base.clean(path)
    if base.git(path, "rev-parse", "HEAD").stdout.decode().strip() != row["commit"]:
        raise ValueError("worktree HEAD changed")
    extras = base.ignored_files(path)
    if {str(item.relative_to(path)): base.snapshot(item) for item in extras} != row["ignored_files"]:
        raise ValueError("ignored files or symbolic links changed")
    return checks, admin, extras


def continue_execution(record, enumeration_proof):
    if base.digest(HERE / "retire_worktrees.py") != record["operator_sha256"]:
        raise ValueError("original immutable retirement operator changed")
    if base.digest(base.HELPER) != record["guard_helper_sha256"]:
        raise ValueError("original immutable process and mount guards changed")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True)
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("nice19 and idle I/O required")
    record.update(
        status="retiring", continuation_sha256=base.digest(Path(__file__)),
        enumeration_equivalence=enumeration_proof,
    )
    base.save(base.RECEIPT, record)
    try:
        for row in record["worktrees"]:
            path = Path(row["path"])
            if row["status"] == "pinned_archived_and_retired":
                if path.exists():
                    raise ValueError("previously retired path was recreated")
                continue
            checks, admin, extras = fresh_guard(path, row)
            row.update(pre_retire_reference_check=checks, continuation_started_at=base.now())
            refs = {row["archive_ref"]: row["commit"]}
            refs.update({
                row["archive_ref"].removesuffix("/head") + "/reflog/" + commit: commit
                for commit in row["reflog_commits"] if commit != row["commit"]
            })
            for name, commit in refs.items():
                current = base.git(base.ROOT, "rev-parse", "--verify", name, check=False)
                if current.returncode:
                    base.git(base.ROOT, "update-ref", name, commit, "0" * 40)
                elif current.stdout.decode().strip() != commit:
                    raise ValueError("existing archive reference differs")
            row["pinned_refs"] = refs
            directory = base.ARCHIVES / row["id"]
            directory.mkdir(exist_ok=True)
            archives = []
            if extras:
                if "ignored_archive" not in row:
                    target = directory / "ignored_files.tar.zst"
                    if target.exists():
                        raise ValueError("preserve an unexpected preexisting ignored archive")
                    row["ignored_archive"] = base.archive_files(
                        target, path, row["ignored_files"],
                    )
                archives.append((row["ignored_archive"], path))
            if "admin_archive" not in row:
                target = directory / "original_git_admin.tar.zst"
                if target.exists():
                    raise ValueError("preserve an unexpected preexisting administration archive")
                names = sorted(
                    str(item.relative_to(admin)) for item in admin.rglob("*") if item.is_file()
                )
                row["admin_archive"] = base.archive_files(target, admin, names)
            archives.append((row["admin_archive"], admin))
            for archive, source in archives:
                target = Path(archive["path"])
                if base.digest(target) != archive["sha256"]:
                    raise ValueError("existing archive digest differs")
                subprocess.run(
                    ["tar", "--compare", "--use-compress-program=zstd -T1",
                     "--file", str(target), "--directory", str(source)],
                    check=True, capture_output=True,
                )
            row["restore_commands"] = [
                shlex.join(["git", "-C", str(base.ROOT), "worktree", "add", "--detach",
                            str(path), row["archive_ref"]]),
            ]
            if extras:
                row["restore_commands"].append(shlex.join([
                    "tar", "--extract", "--use-compress-program=zstd -T1",
                    "--file", row["ignored_archive"]["path"], "--directory", str(path),
                ]))
            row["status"] = "archived_and_ready_for_git_removal"
            base.save(base.RECEIPT, record)
            fresh_guard(path, row)
            base.git(base.ROOT, "worktree", "remove", str(path))
            if path.exists():
                raise ValueError("Git did not remove the selected worktree")
            row.update(status="pinned_archived_and_retired", finished_at=base.now())
            base.save(base.RECEIPT, record)
            print(json.dumps({
                "worktree": path.name, "status": row["status"],
                "source_allocated_bytes": row["allocated_bytes"],
            }), flush=True)
        record.update(
            status="pinned_archived_and_retired", finished_at=base.now(),
            free_bytes_after=shutil.disk_usage(base.ROOT).free,
            archive_allocated_bytes=sum(
                path.stat().st_blocks * 512
                for path in base.ARCHIVES.rglob("*") if path.is_file()
            ),
        )
        record["net_reclaimed_bytes"] = (
            record["source_allocated_bytes"] - record["archive_allocated_bytes"]
        )
        base.save(base.RECEIPT, record)
        print(json.dumps({key: record[key] for key in (
            "status", "source_allocated_bytes", "archive_allocated_bytes", "net_reclaimed_bytes",
        )}), flush=True)
    except BaseException as error:
        record.update(
            status="failed_preserve_remaining_worktrees", error=repr(error), failed_at=base.now(),
        )
        base.save(base.RECEIPT, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("resume", "linked-inventory", "linked-execute"))
    args = parser.parse_args()
    old = set(original_reference_files())
    optimized = set(fast_reference_files())
    if old != optimized:
        raise ValueError("optimized reference enumeration differs from the original guard")
    proof = {
        "checked_at": base.now(), "same_file_set": True, "files": len(old),
        "sorted_paths_sha256": hashlib.sha256(
            "\n".join(map(str, sorted(old))).encode()
        ).hexdigest(),
    }
    base.reference_files = fast_reference_files
    if args.mode == "resume":
        record = json.loads(base.RECEIPT.read_bytes())
        checkpoint = HERE / "initial_execution_interrupted.json"
        if checkpoint.exists() or record["status"] != "failed_preserve_remaining_worktrees":
            raise ValueError("require the original interrupted receipt and a fresh continuation")
        if (
            not record["all_selected_commits_connectivity_checked"]
            or not record["full_checkout_restore_rehearsal"]["passed"]
            or record["error"] != "KeyboardInterrupt()"
        ):
            raise ValueError("original safety and administrative interruption proofs differ")
        shutil.copy2(base.RECEIPT, checkpoint)
        record["administrative_checkpoint"] = {
            "path": str(checkpoint), "sha256": base.digest(checkpoint),
            "reason": "Replace repeated pathlib traversal with equivalent GNU find enumeration.",
        }
        record.pop("error")
        record.pop("failed_at")
        continue_execution(record, proof)
        return
    initial = json.loads((HERE / "inventory.json").read_bytes())
    allowed = {
        row["path"] for row in initial["skipped"]
        if row["reason"] == "preserve non-private or nonregular ignored data"
    }
    base.eligible = lambda row: row["worktree"] in allowed and original_eligible(row)
    base.ignored_files, base.snapshot = linked_ignored_files, linked_snapshot
    base.PLAN, base.RECEIPT = HERE / "linked_inventory.json", HERE / "linked_receipt.json"
    base.ARCHIVES = base.ROOT / "experiments/archive/20261101_retired_linked_worktrees"
    if args.mode == "linked-inventory":
        if base.PLAN.exists():
            raise FileExistsError("preserve the original linked-worktree inventory")
        plan = base.inventory()
        plan.update(extension_sha256=base.digest(Path(__file__)), enumeration_equivalence=proof)
        plan["policy"].append(
            "Preserve owned symbolic links as links in tar; do not follow or remove their targets."
        )
        base.save(base.PLAN, plan)
        print(json.dumps({
            "selected_worktrees": len(plan["worktrees"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
            "skipped": len(plan["skipped"]),
        }), flush=True)
        return
    plan = json.loads(base.PLAN.read_bytes())
    if (
        base.RECEIPT.exists() or base.ARCHIVES.exists() or not plan["worktrees"]
        or plan["extension_sha256"] != base.digest(Path(__file__))
    ):
        raise ValueError("require the original extension and a fresh nonempty linked plan")
    commits = sorted({value for row in plan["worktrees"] for value in row["reflog_commits"]})
    base.git(base.ROOT, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", *commits)
    primary = json.loads((HERE / "receipt.json").read_bytes())
    if primary["status"] != "pinned_archived_and_retired":
        raise ValueError("finish the primary batch before retiring linked worktrees")
    base.ARCHIVES.mkdir()
    record = {
        **plan, "started_at": base.now(), "all_selected_commits_connectivity_checked": True,
        "full_checkout_restore_rehearsal": primary["full_checkout_restore_rehearsal"],
        "restore_rehearsal_source": str(HERE / "receipt.json"),
        "free_bytes_before": shutil.disk_usage(base.ROOT).free,
    }
    continue_execution(record, proof)


if __name__ == "__main__":
    main()
