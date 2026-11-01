"""Retire unreferenced clean detached worktrees with pinned commits and recovery."""

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT.parent
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_retired_worktrees"
PLAN = HERE / "inventory.json"
RECEIPT = HERE / "receipt.json"
CUTOFF = datetime.fromisoformat("2026-10-29T00:00:00+08:00").timestamp()
HELPER = HERE.parent / "home_storage_cleanup_20261101/cleanup_storage.py"
spec = importlib.util.spec_from_file_location("storage_guards", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
digest, save, now = helper.digest, helper.save, helper.now
snapshot = helper.helper.snapshot
TOKEN = re.compile(rb"beliefkv-[A-Za-z0-9_.-]+")
_reference_cache = {}


def git(path, *args, input=None, check=True):
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(path), *args],
        input=input, check=check, capture_output=True,
    )


def worktrees():
    rows = []
    for block in git(ROOT, "worktree", "list", "--porcelain").stdout.decode().split("\n\n"):
        row = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            row[key] = value
        if "worktree" in row:
            rows.append(row)
    return rows


def reference_files():
    files = {
        ROOT / line for line in subprocess.check_output(
            ["rg", "--files", "beliefkv", "scripts", "tests", "docs", "configs",
             "-g", "*.py", "-g", "*.md", "-g", "*.json", "-g", "*.yaml", "-g", "*.toml"],
            cwd=ROOT, text=True,
        ).splitlines()
    }
    files.add(ROOT / "AGENTS.md")
    files.update(
        path for path in (BASE / "ops").rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix in {".json", ".py", ".md"}
        and path.stat().st_mtime >= CUTOFF
    )
    for path in (ROOT / "experiments/reports").rglob("*.json"):
        if path.is_relative_to(HERE) or path.is_symlink():
            continue
        if (
            path.stat().st_mtime >= CUTOFF
            and re.search(r"source|input|manifest|evidence|entry|freeze|registration|plan|state",
                          path.name)
        ):
            files.add(path)
    return sorted(path for path in files if not path.is_relative_to(HERE))


def references(names):
    protected = {}
    scanned = []
    for path in reference_files():
        info = path.stat()
        signature = (info.st_ino, info.st_size, info.st_mtime_ns)
        cached = _reference_cache.get(path)
        if cached is None or cached[0] != signature:
            with path.open("rb") as stream:
                tokens = {
                    os.fsdecode(token).rstrip(".") for line in stream
                    for token in TOKEN.findall(line)
                }
            cached = (signature, tokens)
            _reference_cache[path] = cached
        for name in cached[1] & names:
            protected.setdefault(name, []).append(str(path))
        scanned.append([str(path), *signature])
    return protected, {
        "files_scanned": len(scanned),
        "reference_stat_manifest_sha256": hashlib.sha256(
            json.dumps(scanned, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def eligible(row):
    path = Path(row["worktree"])
    return (
        path != ROOT and path.is_relative_to(BASE / ".worktrees")
        and path.name.startswith("beliefkv-") and "detached" in row
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and (path / ".git").is_file() and not (path / ".git").is_symlink()
        and (path / ".git").stat().st_mtime < CUTOFF
    )


def clean(path):
    status = git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if status:
        raise ValueError("tracked changes or untracked user files must remain")
    if git(path, "config", "--bool", "core.sparseCheckout", check=False).stdout.strip() == b"true":
        raise ValueError("preserve sparse worktree configuration")
    admin = Path(git(path, "rev-parse", "--absolute-git-dir").stdout.decode().strip())
    if (admin / "config.worktree").exists():
        raise ValueError("preserve custom per-worktree configuration")
    return admin


def ignored_files(path):
    names = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
    paths = [path / os.fsdecode(name) for name in names.split(b"\0") if name]
    for item in paths:
        if (
            not item.is_relative_to(path) or item.stat().st_uid != os.getuid()
            or not item.is_file() or item.is_symlink() or item.stat().st_nlink != 1
        ):
            raise ValueError("preserve non-private or nonregular ignored data")
    return sorted(paths)


def allocated(path):
    return int(subprocess.check_output(
        ["du", "-x", "-s", "-B1", str(path)], text=True,
    ).split("\t", 1)[0])


def guard(path):
    if helper.busy_roots([path]):
        raise ValueError("worktree is active or belongs to a running bind mount")
    protected, checks = references({path.name})
    if protected:
        raise ValueError("worktree acquired a code/document/recent-input reference")
    return checks


def inventory():
    registry = worktrees()
    candidates = [row for row in registry if eligible(row)]
    protected, proof = references({Path(row["worktree"]).name for row in candidates})
    busy = helper.busy_roots({Path(row["worktree"]) for row in candidates})
    rows, skipped = [], []
    for record in candidates:
        path = Path(record["worktree"])
        if path.name in protected or path in busy:
            skipped.append({
                "path": str(path),
                "reason": "referenced" if path.name in protected else "active_or_mounted",
                "references": protected.get(path.name, []),
            })
            continue
        if any(Path(item["worktree"]).is_relative_to(path) and item["worktree"] != str(path)
               for item in registry):
            skipped.append({"path": str(path), "reason": "contains another registered worktree"})
            continue
        try:
            admin = clean(path)
            commit = git(path, "rev-parse", "HEAD").stdout.decode().strip()
            if commit != record["HEAD"]:
                raise ValueError("worktree commit changed during inventory")
            extras = ignored_files(path)
            ident = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            reflog = git(path, "reflog", "show", "HEAD", "--format=%H").stdout.decode().splitlines()
            rows.append({
                "path": str(path), "commit": commit, "status": "selected", "id": ident,
                "tree": git(path, "rev-parse", "HEAD^{tree}").stdout.decode().strip(),
                "reflog_commits": sorted(set(reflog) | {commit}),
                "archive_ref": f"refs/archive/20261101-worktrees/{ident}/head",
                "git_admin": str(admin), "allocated_bytes": allocated(path),
                "ignored_files": {
                    str(item.relative_to(path)): snapshot(item) for item in extras
                },
                "tracked_files": len(git(path, "ls-files", "-z").stdout.split(b"\0")) - 1,
            })
        except ValueError as error:
            skipped.append({"path": str(path), "reason": str(error)})
    return {
        "created_at": now(), "operator_sha256": digest(Path(__file__)),
        "guard_helper_sha256": digest(HELPER), "registered_worktrees": len(registry),
        "candidates_before_reference_checks": len(candidates), "reference_proof": proof,
        "worktrees": rows, "skipped": skipped,
        "source_allocated_bytes": sum(row["allocated_bytes"] for row in rows),
        "policy": [
            "Only old, unreferenced, clean, detached, unlocked and inactive main-repository worktrees.",
            "Preserve main, branches, user changes, custom/sparse trees, engines and every current input.",
            "Pin original HEAD and HEAD reflog commits before removal; never force worktree removal.",
            "Verify and archive ignored files and original Git administration metadata before removal.",
            "Restore through git worktree add at the original path plus the archived ignored files.",
        ],
    }


def archive_files(archive, base, names):
    subprocess.run(
        ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
         "--file", str(archive), "--directory", str(base), "--null",
         "--verbatim-files-from", "--files-from=-"],
        input=b"".join(os.fsencode(name) + b"\0" for name in names),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["tar", "--compare", "--use-compress-program=zstd -T1",
         "--file", str(archive), "--directory", str(base)],
        check=True, capture_output=True,
    )
    return {"path": str(archive), "sha256": digest(archive), "bytes": archive.stat().st_size,
            "full_tar_comparison_passed": True}


def execute(plan):
    if RECEIPT.exists() or ARCHIVES.exists() or not plan["worktrees"]:
        raise ValueError("require a fresh nonempty retirement plan")
    if digest(Path(__file__)) != plan["operator_sha256"] or digest(HELPER) != plan["guard_helper_sha256"]:
        raise ValueError("operator or safety helper changed")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True)
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("nice19 and idle I/O required")
    commits = sorted({value for row in plan["worktrees"] for value in row["reflog_commits"]})
    git(ROOT, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", *commits)
    ARCHIVES.mkdir()
    record = {
        **plan, "started_at": now(), "status": "retiring",
        "all_selected_commits_connectivity_checked": True,
        "free_bytes_before": shutil.disk_usage(ROOT).free,
    }
    save(RECEIPT, record)
    try:
        # Rebuild one complete representative checkout before removing any original.
        example = plan["worktrees"][0]
        probe = ARCHIVES / "restore_probe"
        git(ROOT, "worktree", "add", "--detach", str(probe), example["commit"])
        if (
            git(probe, "rev-parse", "HEAD^{tree}").stdout.decode().strip() != example["tree"]
            or git(probe, "status", "--porcelain=v1", "--untracked-files=all").stdout
        ):
            raise ValueError("complete restoration rehearsal failed")
        git(ROOT, "worktree", "remove", str(probe))
        record["full_checkout_restore_rehearsal"] = {
            "commit": example["commit"], "tree": example["tree"], "passed": True,
        }
        save(RECEIPT, record)
        for row in record["worktrees"]:
            path = Path(row["path"])
            row["pre_retire_reference_check"] = guard(path)
            admin = clean(path)
            if git(path, "rev-parse", "HEAD").stdout.decode().strip() != row["commit"]:
                raise ValueError("worktree HEAD changed")
            extras = ignored_files(path)
            if row["ignored_files"] != {
                str(item.relative_to(path)): snapshot(item) for item in extras
            }:
                raise ValueError("ignored content changed")
            row.update(status="pinning_commits", started_at=now())
            save(RECEIPT, record)
            refs = {row["archive_ref"]: row["commit"]}
            refs.update({
                row["archive_ref"].removesuffix("/head") + "/reflog/" + commit: commit
                for commit in row["reflog_commits"] if commit != row["commit"]
            })
            for name, commit in refs.items():
                git(ROOT, "update-ref", name, commit, "0" * 40)
            row["pinned_refs"] = refs
            directory = ARCHIVES / row["id"]
            directory.mkdir()
            if extras:
                row["ignored_archive"] = archive_files(
                    directory / "ignored_files.tar.zst", path, row["ignored_files"],
                )
            admin_files = sorted(
                str(item.relative_to(admin)) for item in admin.rglob("*") if item.is_file()
            )
            row["admin_archive"] = archive_files(
                directory / "original_git_admin.tar.zst", admin, admin_files,
            )
            row["restore_commands"] = [
                shlex.join(["git", "-C", str(ROOT), "worktree", "add", "--detach",
                            str(path), row["archive_ref"]]),
            ]
            if extras:
                row["restore_commands"].append(shlex.join([
                    "tar", "--extract", "--use-compress-program=zstd -T1",
                    "--file", row["ignored_archive"]["path"], "--directory", str(path),
                ]))
            row["status"] = "archived_and_ready_for_git_removal"
            save(RECEIPT, record)
            guard(path)
            clean(path)
            if row["ignored_files"] != {
                str(item.relative_to(path)): snapshot(item) for item in ignored_files(path)
            }:
                raise ValueError("ignored files changed immediately before removal")
            git(ROOT, "worktree", "remove", str(path))
            if path.exists():
                raise ValueError("Git did not remove the selected worktree")
            row.update(status="pinned_archived_and_retired", finished_at=now())
            save(RECEIPT, record)
            print(json.dumps({
                "worktree": path.name, "status": row["status"],
                "source_allocated_bytes": row["allocated_bytes"],
            }), flush=True)
        record.update(
            status="pinned_archived_and_retired", finished_at=now(),
            free_bytes_after=shutil.disk_usage(ROOT).free,
            archive_allocated_bytes=sum(
                path.stat().st_blocks * 512 for path in ARCHIVES.rglob("*") if path.is_file()
            ),
        )
        record["net_reclaimed_bytes"] = record["source_allocated_bytes"] - record["archive_allocated_bytes"]
        save(RECEIPT, record)
        print(json.dumps({key: record[key] for key in (
            "status", "source_allocated_bytes", "archive_allocated_bytes", "net_reclaimed_bytes",
        )}), flush=True)
    except BaseException as error:
        record.update(status="failed_preserve_remaining_worktrees", error=repr(error), failed_at=now())
        save(RECEIPT, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        execute(json.loads(PLAN.read_bytes()))
    else:
        if PLAN.exists():
            raise FileExistsError("preserve existing inventory")
        plan = inventory()
        save(PLAN, plan)
        print(json.dumps({
            "registered_worktrees": plan["registered_worktrees"],
            "eligible_before_reference_checks": plan["candidates_before_reference_checks"],
            "selected_worktrees": len(plan["worktrees"]), "skipped": len(plan["skipped"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
        }, indent=2))


if __name__ == "__main__":
    main()
