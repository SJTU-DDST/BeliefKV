"""Archive idle September/October worktrees, including frozen engine patches."""

import argparse
from collections import Counter
from datetime import datetime
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
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXPERIMENT = ROOT.parent
ARCHIVES = ROOT / "experiments/archive/20261121_historical_worktrees"
PLAN = HERE / "inventory.json"
RECEIPT = HERE / "receipt.json"
CUTOFF = datetime.fromisoformat("2026-11-01T00:00:00+08:00").timestamp()
RECENT = datetime.fromisoformat("2026-11-14T00:00:00+08:00").timestamp()
REPOS = (ROOT, ROOT / "third_party/sglang-v0.5.20")
NAME = re.compile(rb"(?:beliefkv|BeliefKV|sglang|bkv)-[A-Za-z0-9_.-]+")
LOCAL_PATH = re.compile(rb"/home/longhao/[A-Za-z0-9_./+-]+")
SCRIPT_TYPES = ("*.py", "*.sh", "*.json", "*.yaml", "*.yml", "*.toml")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous = load("verified_cleanup", HERE.parent / "storage_cleanup_20261121/cleanup.py")
base = previous.base
helper = base.helper
original_process_paths = helper.process_paths
digest, save, now, git = base.digest, base.save, base.now, base.git
FILE_CACHE = {}


def process_paths():
    result = original_process_paths()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        for name in ("cmdline", "environ", "maps"):
            try:
                data = (proc / name).read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            result.update(Path(os.fsdecode(value)) for value in LOCAL_PATH.findall(data))
    return result


helper.process_paths = process_paths


def registry():
    rows = []
    for repo in REPOS:
        text = git(repo, "worktree", "list", "--porcelain").stdout.decode()
        for block in text.split("\n\n"):
            row = dict(line.partition(" ")[::2] for line in block.splitlines())
            if "worktree" in row:
                rows.append({**row, "repository": str(repo)})
    return rows


def old_candidate(row):
    path = Path(row["worktree"])
    dot = path / ".git"
    return (
        path not in REPOS and path.is_relative_to(EXPERIMENT)
        and path.name.startswith(("beliefkv-", "BeliefKV-", "sglang-", "bkv-"))
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and dot.is_file() and not dot.is_symlink() and dot.stat().st_mtime < CUTOFF
    )


def recent_files(directory, patterns):
    command = ["find", str(directory), "-type", "f", "-newermt", "@" + str(int(RECENT)), "("]
    for index, pattern in enumerate(patterns):
        command.extend((["-o"] if index else []) + ["-name", pattern])
    command.extend([")", "-print0"])
    return [Path(os.fsdecode(value))
            for value in subprocess.check_output(command).split(b"\0") if value]


def maintenance(path):
    return any(re.search(r"cleanup|retirement|retired|unused_archive|storage_review", part)
               for part in path.parts)


def file_references(path):
    info = path.stat()
    signature = (info.st_ino, info.st_size, info.st_mtime_ns)
    cached = FILE_CACHE.get(path)
    if cached is None or cached[0] != signature:
        data = path.read_bytes()
        cached = (signature, {os.fsdecode(value).rstrip(".") for value in NAME.findall(data)},
                  {Path(os.fsdecode(value)) for value in LOCAL_PATH.findall(data)})
        FILE_CACHE[path] = cached
    return cached[1], cached[2]


def protection(records):
    protected = {}
    roots = {Path(row["worktree"]) for row in records}

    def protect(path, reason):
        root = next((item for item in (path, *path.parents) if item in roots), None)
        if root:
            protected.setdefault(str(root), set()).add(reason)

    live = process_paths()
    for path in live:
        protect(path, "live_process")
    for root in helper.helper.mounted_sources():
        for row in records:
            path = Path(row["worktree"])
            if path.is_relative_to(root) or root.is_relative_to(path):
                protect(path, "live_container_bind")
    command = ["rg", "--files", "beliefkv", "scripts", "tests", "configs"]
    for pattern in SCRIPT_TYPES:
        command.extend(["-g", pattern])
    files = {ROOT / name for name in subprocess.check_output(
        command, cwd=ROOT, text=True,
    ).splitlines()}
    files.update(recent_files(EXPERIMENT / "ops", ("*.json", "*.py", "*.sh")))
    files.update(
        path for path in recent_files(ROOT / "experiments/reports", ("*.json",))
        if not maintenance(path) and re.search(
            r"source|input|manifest|evidence|entry|freeze|registration|plan|state", path.name,
        )
    )
    for pattern in (
        "*/lib/python*/site-packages/*.pth",
        "*/lib/python*/site-packages/*.egg-link",
        "*/lib/python*/site-packages/*/direct_url.json",
        "*/lib/python*/site-packages/__editable__*finder.py",
    ):
        files.update(Path("/home/longhao/miniconda3/envs").glob(pattern))
    # Follow old plans that a current watcher uses, even if their mtime is old.
    pending = list(path for path in live if path.suffix == ".json")
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file() or path.is_symlink():
            continue
        seen.add(path)
        if path.stat().st_size > 32 * 1024**2:
            protect(path, "large_live_input")
            continue
        files.add(path)
        _, dependencies = file_references(path)
        pending.extend(item for item in dependencies if item.suffix == ".json")
    by_name = {Path(row["worktree"]).name: row["worktree"] for row in records}
    for path in sorted(files):
        if maintenance(path) or not path.is_file() or path.is_symlink():
            continue
        names, dependencies = file_references(path)
        for name in names & by_name.keys():
            protected.setdefault(by_name[name], set()).add(str(path))
        for dependency in dependencies:
            protect(dependency, str(path))
    # Retained application checkouts must keep their linked engine code available.
    for row in records:
        link = Path(row["worktree"]) / "third_party/sglang-v0.5.20"
        if link.is_symlink():
            protect(link.resolve(), "engine_symlink:" + str(link))
    return {key: sorted(value) for key, value in protected.items()}, len(files)


def tree_signature(path):
    entries = []
    file_count = 0
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(directory) / name
            info = item.lstat()
            if info.st_uid != os.getuid() or not (
                stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
            ):
                raise ValueError("preserve unowned or special filesystem entries")
            entries.append((
                str(item.relative_to(path)), info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ino, info.st_dev, info.st_nlink,
                os.readlink(item) if stat.S_ISLNK(info.st_mode) else None,
            ))
            file_count += not stat.S_ISDIR(info.st_mode)
    encoded = json.dumps(sorted(entries), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), file_count


def state(row):
    path, repo = Path(row["worktree"]), Path(row["repository"])
    admin = Path(git(path, "rev-parse", "--absolute-git-dir").stdout.decode().strip())
    if (admin / "config.worktree").exists() or (admin / "locked").exists():
        raise ValueError("preserve custom or locked worktree")
    if git(path, "config", "--bool", "core.sparseCheckout", check=False).stdout.strip() == b"true":
        raise ValueError("preserve sparse checkout")
    flags = git(path, "ls-files", "-v", "-z").stdout.split(b"\0")
    if any(value and value[:1] != b"H" for value in flags):
        raise ValueError("preserve special index flags or unmerged entries")
    if git(path, "rev-parse", "HEAD").stdout.decode().strip() != row["HEAD"]:
        raise ValueError("HEAD changed")
    status = git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if repo == ROOT and status:
        raise ValueError("preserve application worktree with local changes")
    signature, count = tree_signature(path)
    return {
        "git_admin": str(admin), "filesystem_stat_sha256": signature,
        "files_and_links": count, "status_porcelain_hex": status.hex(),
        "has_frozen_engine_changes": bool(status),
        "tree": git(path, "rev-parse", "HEAD^{tree}").stdout.decode().strip(),
        "index_sha256": digest(admin / "index"),
    }


def inventory():
    if PLAN.exists():
        raise FileExistsError("preserve previous inventory")
    records = registry()
    protected, scanned = protection(records)
    selected, skipped = [], []
    for row in records:
        if not old_candidate(row):
            continue
        path = Path(row["worktree"])
        if row["worktree"] in protected:
            skipped.append({**row, "reason": "current_dependency", "references": protected[str(path)]})
            continue
        if any(Path(other["worktree"]).is_relative_to(path) and other["worktree"] != str(path)
               for other in records):
            skipped.append({**row, "reason": "contains_registered_worktree"})
            continue
        try:
            details = state(row)
        except ValueError as error:
            skipped.append({**row, "reason": str(error)})
            continue
        ident = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        reflog = git(path, "reflog", "show", "HEAD", "--format=%H").stdout.decode().splitlines()
        selected.append({
            **row, **details, "id": ident, "status": "selected",
            "archive_ref": f"refs/archive/20261121-historical/{ident}/head",
            "reflog_commits": sorted(set(reflog) | {row["HEAD"]}),
            "allocated_bytes": base.allocated(path) + base.allocated(Path(details["git_admin"])),
        })
    plan = {
        "created_at": now(), "operator_sha256": digest(Path(__file__)),
        "worktrees": selected, "skipped": skipped, "reference_files_scanned": scanned,
        "registered_worktrees": len(records), "free_bytes_before": shutil.disk_usage(ROOT).free,
        "source_allocated_bytes": sum(row["allocated_bytes"] for row in selected),
        "policy": [
            "Only pre-November owned, unlocked, inactive worktrees without current dependencies.",
            "Historical narrative mentions alone do not require keeping a physical checkout.",
            "Preserve application worktrees with local changes and every registered nested checkout.",
            "Full source archives preserve frozen engine edits, untracked files, ignored data and links.",
            "Pin HEAD and reflog commits; archive original index and administration; retain branches.",
            "Compare full archives and rehearse exact restoration before deleting archived source copies.",
            "Unregister only the selected missing paths; never force Git removal or prune other entries.",
        ],
    }
    save(PLAN, plan)
    print(json.dumps({
        "selected": len(selected), "skipped": len(skipped),
        "source_allocated_bytes": plan["source_allocated_bytes"],
        "by_repository": dict(Counter(row["repository"] for row in selected)),
        "skip_reasons": dict(Counter(row["reason"] for row in skipped)),
    }), flush=True)


def guard(row):
    records = registry()
    current = next((value for value in records if value["worktree"] == row["worktree"]), None)
    if current is None or not old_candidate(current) or current["HEAD"] != row["HEAD"]:
        raise ValueError("registration, age, HEAD or lock changed")
    protected, _ = protection(records)
    if row["worktree"] in protected:
        raise ValueError("selected worktree became a current dependency")
    for key, value in state(current).items():
        if row[key] != value:
            raise ValueError("worktree content or index changed: " + key)


def archive(target, source):
    subprocess.run(
        ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
         "--file", str(target), "--directory", str(source), "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["tar", "--compare", "--use-compress-program=zstd -T1",
         "--file", str(target), "--directory", str(source)],
        check=True, capture_output=True,
    )
    return {"path": str(target), "sha256": digest(target),
            "bytes": target.stat().st_size, "full_tar_comparison_passed": True}


def restoration(row, directory):
    repo = Path(row["repository"])
    probe = directory / "restore_probe"
    git(repo, "worktree", "add", "--quiet", "--no-checkout", "--detach", str(probe), row["archive_ref"])
    subprocess.run(
        ["tar", "--extract", "--use-compress-program=zstd -T1",
         "--exclude=./.git", "--file", row["source_archive"]["path"], "--directory", str(probe)],
        check=True, capture_output=True,
    )
    admin = Path(git(probe, "rev-parse", "--absolute-git-dir").stdout.decode().strip())
    index = subprocess.check_output([
        "tar", "--extract", "--to-stdout", "--use-compress-program=zstd -T1",
        "--file", row["admin_archive"]["path"], "./index",
    ])
    (admin / "index").write_bytes(index)
    subprocess.run(
        ["tar", "--compare", "--use-compress-program=zstd -T1",
         "--exclude=./.git", "--file", row["source_archive"]["path"], "--directory", str(probe)],
        check=True, capture_output=True,
    )
    if (
        git(probe, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.hex()
        != row["status_porcelain_hex"]
        or digest(admin / "index") != row["index_sha256"]
        or git(probe, "rev-parse", "HEAD^{tree}").stdout.decode().strip() != row["tree"]
    ):
        raise ValueError("exact restoration failed; preserve probe")
    # This newly created probe also checks ordinary removal of missing paths.
    shutil.rmtree(probe)
    git(repo, "worktree", "remove", str(probe))
    return {"passed": True, "full_tar_compare": True, "exact_index_and_status": True,
            "ordinary_unregister_of_missing_path": True}


def execute(plan):
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True)
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("require nice19 and idle I/O")
    if RECEIPT.exists() or ARCHIVES.exists() or not plan["worktrees"]:
        raise ValueError("require fresh nonempty plan")
    for repo in REPOS:
        commits = sorted({commit for row in plan["worktrees"] if row["repository"] == str(repo)
                          for commit in row["reflog_commits"]})
        if commits:
            git(repo, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", *commits)
    ARCHIVES.mkdir()
    record = {**plan, "status": "archiving", "started_at": now()}
    save(RECEIPT, record)
    rehearsed = set()
    try:
        for row in record["worktrees"]:
            started = time.monotonic()
            path, repo = Path(row["worktree"]), Path(row["repository"])
            guard(row)
            refs = {row["archive_ref"]: row["HEAD"]}
            refs.update({
                row["archive_ref"].removesuffix("/head") + "/reflog/" + commit: commit
                for commit in row["reflog_commits"] if commit != row["HEAD"]
            })
            for name, commit in refs.items():
                git(repo, "update-ref", name, commit, "0" * 40)
            row["pinned_refs"] = refs
            directory = ARCHIVES / row["id"]
            directory.mkdir()
            row["source_archive"] = archive(directory / "source.tar.zst", path)
            row["admin_archive"] = archive(directory / "git_admin.tar.zst", Path(row["git_admin"]))
            key = (row["repository"], row["has_frozen_engine_changes"])
            if key not in rehearsed:
                row["restore_rehearsal"] = restoration(row, directory)
                rehearsed.add(key)
            row.update(status="fully_archived_and_verified", archived_at=now())
            row["restore_commands"] = [
                shlex.join(["mkdir", "-p", str(path), row["git_admin"]]),
                shlex.join(["tar", "--extract", "--use-compress-program=zstd -T1",
                            "--file", row["source_archive"]["path"], "--directory", str(path)]),
                shlex.join(["tar", "--extract", "--use-compress-program=zstd -T1",
                            "--file", row["admin_archive"]["path"],
                            "--directory", row["git_admin"]]),
                shlex.join(["git", "-C", str(repo), "worktree", "repair", str(path)]),
            ]
            save(RECEIPT, record)
            guard(row)
            # Both original sources and index are recoverable byte for byte.
            # Remove only this archived copy, then unregister exactly its missing path.
            shutil.rmtree(path)
            git(repo, "worktree", "remove", str(path))
            if path.exists() or Path(row["git_admin"]).exists():
                raise ValueError("selected copy or registration still exists")
            if "branch" in row and (
                git(repo, "rev-parse", row["branch"]).stdout.decode().strip() != row["HEAD"]
            ):
                raise ValueError("original branch changed")
            row.update(status="archived_and_retired", finished_at=now(),
                       elapsed_seconds=time.monotonic() - started)
            save(RECEIPT, record)
            print(json.dumps({
                "path": str(path), "status": row["status"],
                "source_bytes": row["allocated_bytes"],
                "archive_bytes": row["source_archive"]["bytes"] + row["admin_archive"]["bytes"],
                "seconds": row["elapsed_seconds"],
            }), flush=True)
        allocated = base.allocated(ARCHIVES)
        record.update(
            status="archived_and_retired", finished_at=now(),
            archive_allocated_bytes=allocated,
            net_reclaimed_bytes=record["source_allocated_bytes"] - allocated,
            free_bytes_after=shutil.disk_usage(ROOT).free,
        )
        save(RECEIPT, record)
    except BaseException as error:
        record.update(status="failed_preserve_remaining", error=repr(error), failed_at=now())
        save(RECEIPT, record)
        raise


def review():
    record = json.loads(RECEIPT.read_bytes())
    if record["status"] != "archived_and_retired":
        raise ValueError("finish retirement first")
    registered = {row["worktree"] for row in registry()}
    for row in record["worktrees"]:
        repo = Path(row["repository"])
        if row["worktree"] in registered or Path(row["worktree"]).exists():
            raise ValueError("retired worktree still present")
        for name, commit in row["pinned_refs"].items():
            if git(repo, "rev-parse", name).stdout.decode().strip() != commit:
                raise ValueError("pinned history differs")
        if "branch" in row and git(repo, "rev-parse", row["branch"]).stdout.decode().strip() != row["HEAD"]:
            raise ValueError("original branch differs")
        for key in ("source_archive", "admin_archive"):
            item = row[key]
            if digest(Path(item["path"])) != item["sha256"]:
                raise ValueError("archive SHA256 differs")
            subprocess.run(["zstd", "--test", "--quiet", item["path"]], check=True)
    lines = [
        "# Historical Worktree Recovery", "",
        "Full archives include frozen patches, untracked and ignored files, symlinks and",
        "the original Git index/administration. Commits and branches remain pinned.", "",
        "Run a listed recipe only if BOTH the original directory and its Git admin",
        "directory are absent. If either exists, restore elsewhere and adapt gitdir",
        "pointers; do not extract over an existing checkout.", "",
    ]
    for row in record["worktrees"]:
        lines.extend(["## " + row["worktree"], "", "```bash", *row["restore_commands"], "```", ""])
    (ARCHIVES / "README.md").write_text("\n".join(lines))
    report = {
        "status": "independently_verified", "finished_at": now(),
        "retired_worktrees": len(record["worktrees"]),
        "by_repository": dict(Counter(row["repository"] for row in record["worktrees"])),
        "frozen_engine_copies_with_changes": sum(row["has_frozen_engine_changes"]
                                               for row in record["worktrees"]),
        "source_allocated_bytes": record["source_allocated_bytes"],
        "archive_allocated_bytes": base.allocated(ARCHIVES),
        "net_reclaimed_bytes": record["source_allocated_bytes"] - base.allocated(ARCHIVES),
        "receipt_sha256": digest(RECEIPT), "inventory_sha256": digest(PLAN),
        "all_archive_sha256_and_zstd_integrity_verified": True,
        "all_original_heads_reflogs_and_branches_preserved": True,
        "rehearsals": [{"path": row["worktree"], **row["restore_rehearsal"]}
                      for row in record["worktrees"] if "restore_rehearsal" in row],
    }
    save(HERE / "review.json", report)
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        inventory()
        return
    plan = json.loads(PLAN.read_bytes())
    if plan["operator_sha256"] != digest(Path(__file__)):
        raise ValueError("operator changed after inventory")
    if args.action == "execute":
        execute(plan)
    else:
        review()


if __name__ == "__main__":
    main()
