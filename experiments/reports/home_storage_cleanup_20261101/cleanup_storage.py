"""Deduplicate inactive Git pack copies and archive explicitly old service logs."""

import argparse
from collections import defaultdict
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
EXPERIMENTS = ROOT / "experiments"
ARCHIVES = EXPERIMENTS / "archive/20261101_home_storage"
PLAN = HERE / "inventory.json"
RECEIPT = HERE / "receipt.json"
HELPER = EXPERIMENTS / "reports/unused_archive_20261029/archive_old_logs.py"
HELPER_SHA256 = "ad46bd6e86e3eb9356bc17dbb747234e7d0f1f91738bfea82158b1a4cba8606f"
LOG_ROOTS = (
    EXPERIMENTS / "ab/p6_h200_high_pressure_v3/20260920_v58_pair",
    EXPERIMENTS / "ab/p6_h200_high_pressure_v3/20260921_v59_predictive",
    EXPERIMENTS / "ab/p6_h200_high_pressure_v3/20260921_v60_predictive_retry1",
    EXPERIMENTS / "shadow/p6_predictive_wait_shadow_v58",
)
spec = importlib.util.spec_from_file_location("archive_helpers", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
digest, save, now = helper.digest, helper.save, helper.now
if digest(HELPER) != HELPER_SHA256:
    raise ValueError("immutable archival helper changed")


def snapshot(path):
    info = path.stat()
    return {
        **helper.snapshot(path), "mode": stat.S_IMODE(info.st_mode),
        "atime_ns": info.st_atime_ns,
    }


def stable(path, expected):
    actual = snapshot(path)
    return all(actual[key] == value for key, value in expected.items() if key != "atime_ns")


def process_paths():
    found = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = (proc / "cwd").resolve()
            found.add(cwd)
            for argument in (proc / "cmdline").read_bytes().split(b"\0"):
                text = os.fsdecode(argument)
                if text.startswith("/home/longhao/"):
                    found.add(Path(text))
            for item in (proc / "environ").read_bytes().split(b"\0"):
                if item.startswith((b"PYTHONPATH=", b"RUN_ROOT=")):
                    for value in os.fsdecode(item).split("=", 1)[1].split(":"):
                        if value.startswith("/home/longhao/"):
                            found.add(Path(value))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
        try:
            for fd in (proc / "fd").iterdir():
                try:
                    text = os.readlink(fd)
                    if text.startswith("/home/longhao/"):
                        found.add(Path(text))
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    pass
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return found


def busy_roots(roots):
    mounts = helper.mounted_sources()
    live = process_paths()
    return {
        root for root in roots if any(
            root.is_relative_to(source) or source.is_relative_to(root) for source in mounts
        ) or any(path.is_relative_to(root) for path in live)
    }


def workspace(path):
    return path.parents[3]


def roots():
    return (
        sorted(path for path in (EXPERIMENTS / "raw").glob("qwen35_*") if path.is_dir())
        + list(LOG_ROOTS)
    )


def inventory():
    packs = []
    for base in roots():
        packs.extend(path for path in base.rglob("*.pack") if re.fullmatch(
            r"pack-[0-9a-f]{40}\.pack", path.name
        ) and path.parent.name == "pack" and path.parent.parent.name == "objects"
            and path.parents[2].name == ".git" and path.stat().st_size >= 16 * 1024 ** 2)
    workspaces = {workspace(path) for path in packs}
    busy = busy_roots(workspaces)
    groups = defaultdict(list)
    skipped = []
    for path in sorted(packs):
        index = path.with_suffix(".idx")
        if workspace(path) in busy:
            continue
        if any(
            item.is_symlink() or not item.is_file() or item.resolve() != item.absolute()
            or item.stat().st_uid != os.getuid() or item.stat().st_nlink != 1
            for item in (path, index)
        ):
            skipped.append({"path": str(path), "reason": "not a private regular owned pack/index"})
            continue
        groups[(path.name, path.stat().st_size, index.stat().st_size)].append(path)
    result = []
    for key, paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        result.append({
            "pack_name": key[0], "status": "selected",
            "copies": [{
                "workspace": str(workspace(path)),
                "pack": str(path), "index": str(path.with_suffix(".idx")),
                "pack_before": snapshot(path), "index_before": snapshot(path.with_suffix(".idx")),
            } for path in paths],
            "potential_reclaimed_bytes": sum(
                path.stat().st_blocks * 512 + path.with_suffix(".idx").stat().st_blocks * 512
                for path in paths[1:]
            ),
        })
    logs = []
    log_busy = busy_roots(LOG_ROOTS)
    for base in LOG_ROOTS:
        if base in log_busy:
            continue
        paths = [
            path for path in base.rglob("*") if path.is_file() and not path.is_symlink()
            and path.parent.name == "server" and path.suffix in {".jsonl", ".log"}
            and "request_tokens" not in path.name and path.stat().st_size >= 1024 ** 2
            and path.stat().st_uid == os.getuid() and path.stat().st_nlink == 1
        ]
        if paths:
            logs.append({
                "root": str(base), "status": "selected",
                "paths": {str(path.relative_to(ROOT)): snapshot(path) for path in sorted(paths)},
                "preserved_metadata_sha256": {
                    str(path.relative_to(ROOT)): digest(path) for path in base.rglob("*.json")
                    if path.stat().st_size < 1024 ** 2
                },
                "source_allocated_bytes": sum(path.stat().st_blocks * 512 for path in paths),
            })
    return {
        "created_at": now(), "operator_sha256": digest(Path(__file__)),
        "helper_sha256": digest(HELPER), "pack_groups": result, "log_groups": logs,
        "candidate_pack_files": len(packs), "workspaces_with_packs": len(workspaces),
        "busy_workspaces_skipped": sorted(map(str, busy)),
        "busy_log_roots_skipped": sorted(map(str, log_busy)), "other_skipped": skipped,
        "potential_pack_reclaimed_bytes": sum(row["potential_reclaimed_bytes"] for row in result),
        "log_source_allocated_bytes": sum(row["source_allocated_bytes"] for row in logs),
        "policy": [
            "Only inactive, unmounted, privately owned Git pack/index copies may share read-only inodes.",
            "Every original copy must match both canonical SHA256 before any deduplication.",
            "Canonical pack/index must pass native git verify-pack.",
            "Preserve every working-tree source file, Git reference, task change and path.",
            "Archive only old server logs; preserve request tokens, outcomes and all small metadata.",
            "No frozen checkout, environment, model, current experiment or process is removed.",
        ],
    }


def copy_guard(copies):
    paths = {Path(row["workspace"]) for row in copies}
    if busy_roots(paths):
        raise ValueError("a selected workspace became active or mounted")
    for row in copies:
        for kind in ("pack", "index"):
            if not stable(Path(row[kind]), row[kind + "_before"]):
                raise ValueError("selected object copy changed")


def execute(plan):
    if RECEIPT.exists() or ARCHIVES.exists() or digest(Path(__file__)) != plan["operator_sha256"]:
        raise ValueError("require an unused immutable cleanup plan")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True)
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("nice19 and idle I/O required")
    ARCHIVES.mkdir()
    record = {
        **plan, "started_at": now(), "status": "cleaning", "free_bytes_before": shutil.disk_usage(ROOT).free,
        "verified_pack_reclaimed_bytes": 0, "log_net_reclaimed_bytes": 0,
    }
    save(RECEIPT, record)
    try:
        for group in record["pack_groups"]:
            copies = group["copies"]
            copy_guard(copies)
            first = copies[0]
            hashes = {kind: digest(Path(first[kind])) for kind in ("pack", "index")}
            group.update(status="verifying_every_copy", sha256=hashes)
            save(RECEIPT, record)
            print(json.dumps({"pack": group["pack_name"], "copies": len(copies),
                              "status": group["status"]}), flush=True)
            for row in copies:
                for kind in hashes:
                    if digest(Path(row[kind])) != hashes[kind]:
                        raise ValueError("same-name Git objects have different contents")
            copy_guard(copies)
            directory = ARCHIVES / "git_objects" / group["pack_name"].removesuffix(".pack")
            directory.mkdir(parents=True)
            canonical = {kind: directory / Path(first[kind]).name for kind in hashes}
            for kind, path in canonical.items():
                os.link(first[kind], path)
            subprocess.run(
                ["git", "verify-pack", str(canonical["index"])],
                check=True, capture_output=True,
            )
            for path in canonical.values():
                path.chmod(0o444)
            group.update(
                status="sharing_verified_read_only_objects",
                canonical={key: str(value) for key, value in canonical.items()},
                git_verify_pack_passed=True, completed_copies=1,
            )
            save(RECEIPT, record)
            for row in copies[1:]:
                for kind, source in canonical.items():
                    target = Path(row[kind])
                    if not stable(target, row[kind + "_before"]):
                        raise ValueError("object changed immediately before atomic replacement")
                    temporary = target.with_name(target.name + ".dedup-20261101-tmp")
                    if temporary.exists():
                        raise FileExistsError("preserve unexpected existing dedup temporary")
                    os.link(source, temporary)
                    os.replace(temporary, target)
                    if target.stat().st_ino != source.stat().st_ino:
                        raise ValueError("atomic hardlink publication failed")
                    record["verified_pack_reclaimed_bytes"] += row[kind + "_before"]["allocated_bytes"]
                group["completed_copies"] += 1
                save(RECEIPT, record)
            group.update(status="verified_and_deduplicated", finished_at=now())
            save(RECEIPT, record)
            print(json.dumps({"pack": group["pack_name"], "status": group["status"],
                              "reclaimed_bytes": group["potential_reclaimed_bytes"]}), flush=True)
        for index, group in enumerate(record["log_groups"]):
            base = Path(group["root"])
            if busy_roots([base]):
                raise ValueError("selected old log root became active")
            paths = [ROOT / name for name in group["paths"]]
            if not all(stable(path, group["paths"][str(path.relative_to(ROOT))]) for path in paths):
                raise ValueError("old log source changed")
            archive = ARCHIVES / f"old_service_logs_{index + 1}.tar.zst"
            temporary = archive.with_suffix(".zst.partial")
            group.update(status="compressing", archive=str(archive))
            save(RECEIPT, record)
            subprocess.run(
                ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
                 "--file", str(temporary), "--directory", str(ROOT), "--null",
                 "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in group["paths"]),
                check=True, capture_output=True,
            )
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(temporary), "--directory", str(ROOT)],
                check=True, capture_output=True,
            )
            if busy_roots([base]) or not all(
                stable(path, group["paths"][str(path.relative_to(ROOT))]) for path in paths
            ):
                raise ValueError("old log root changed before cleanup")
            group.update(
                archive_sha256=digest(temporary), archive_bytes=temporary.stat().st_size,
                full_tar_comparison_passed=True,
            )
            temporary.replace(archive)
            group["restore_command"] = shlex.join([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(ROOT),
            ])
            save(RECEIPT, record)
            with (base / "OLD_SERVICE_LOGS_ARCHIVED_20261101.md").open("x") as stream:
                stream.write(
                    "# Archived Old Service Logs\n\n"
                    f"Archive: `{archive}`\n\nSHA256: `{group['archive_sha256']}`\n\n"
                    f"Receipt: `{RECEIPT}`\n\n```bash\n{group['restore_command']}\n```\n"
                )
            for path in paths:
                if not stable(path, group["paths"][str(path.relative_to(ROOT))]):
                    raise ValueError("source changed immediately before verified removal")
                path.unlink()
            group.update(status="verified_and_archived", finished_at=now())
            record["log_net_reclaimed_bytes"] += (
                group["source_allocated_bytes"] - archive.stat().st_blocks * 512
            )
            save(RECEIPT, record)
        record.update(
            status="verified_and_cleaned", finished_at=now(),
            free_bytes_after=shutil.disk_usage(ROOT).free,
        )
        save(RECEIPT, record)
        print(json.dumps({key: record[key] for key in (
            "status", "verified_pack_reclaimed_bytes", "log_net_reclaimed_bytes", "free_bytes_after",
        )}), flush=True)
    except BaseException as error:
        record.update(status="failed_preserve_remaining_sources", error=repr(error), failed_at=now())
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
            raise FileExistsError("preserve the frozen inventory")
        plan = inventory()
        save(PLAN, plan)
        print(json.dumps({
            "pack_groups": len(plan["pack_groups"]),
            "pack_copies": sum(len(row["copies"]) for row in plan["pack_groups"]),
            "potential_pack_reclaimed_bytes": plan["potential_pack_reclaimed_bytes"],
            "busy_workspaces_skipped": len(plan["busy_workspaces_skipped"]),
            "log_groups": len(plan["log_groups"]),
            "log_files": sum(len(row["paths"]) for row in plan["log_groups"]),
            "log_source_allocated_bytes": plan["log_source_allocated_bytes"],
        }, indent=2))


if __name__ == "__main__":
    main()
