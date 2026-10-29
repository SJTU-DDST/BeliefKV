"""Losslessly archive explicitly superseded Qwen3.5 logs, retaining small evidence."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "experiments/raw"
OPS = ROOT.parent / "ops"
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261029_unused_logs"
PLAN = HERE / "inventory.json"
REPORT = HERE / "receipt.json"
RUNS = (
    "qwen35_native_predictive_replenished_108plus48_20261014_v56",
    "qwen35_native_predictive_replenished_108plus48_20261014_v57",
    "qwen35_native_predictive_replenished_108plus48_20261014_v59",
    "qwen35_native_predictive_replenished_108plus48_20261015_v61",
    "qwen35_native_predictive_replenished_108plus48_20261015_v62",
    "qwen35_native_predictive_replenished_108plus48_20261015_v63_retry1",
    "qwen35_native_predictive_replenished_108plus48_20261015_v68_partial_restore",
    "qwen35_native_predictive_replenished_108plus48_20261015_v70_terminal_root",
    "qwen35_native_predictive_replenished_108plus48_20261015_v71_aged_head",
    "qwen35_native_predictive_online_poisson_156root_20261016_v73",
    "qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback",
    "qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch",
    "qwen35_native_close_gc_online_poisson_156root_20261016_v77",
    "qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order",
)


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def snapshot(path):
    info = path.stat()
    return {
        "size_bytes": info.st_size, "allocated_bytes": info.st_blocks * 512,
        "inode": info.st_ino, "device": info.st_dev,
        "mtime_ns": info.st_mtime_ns, "uid": info.st_uid, "nlink": info.st_nlink,
    }


def selected(run):
    paths = set()
    for pattern in ("*/server/*.jsonl", "*/server/*.log", "*/opportunities/*.jsonl",
                    "*/client_*/server/*.jsonl", "*/client_*/server/*.log"):
        for path in run.glob(pattern):
            if path.stat().st_size >= 1024 ** 2 and "request_tokens" not in path.name:
                paths.add(path)
    return sorted(paths)


def path_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from path_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from path_values(item)
    elif isinstance(value, str) and value.startswith(str(RAW) + "/"):
        yield Path(value).resolve()


def protected_inputs():
    files = [
        path for path in OPS.glob("*.registration.json")
        if any(f"202610{day}" in path.name for day in range(24, 30))
    ]
    result = set()
    for path in files:
        result.update(path_values(json.loads(path.read_bytes())))
    return result


def mounted_sources():
    ids = subprocess.check_output(["docker", "ps", "-q"], text=True, timeout=15).splitlines()
    sources = set()
    for begin in range(0, len(ids), 128):
        rows = subprocess.check_output(
            ["docker", "inspect", "--format", "{{json .Mounts}}", *ids[begin:begin + 128]],
            text=True, timeout=30,
        )
        for line in rows.splitlines():
            for mount in json.loads(line):
                if mount.get("Type") == "bind":
                    sources.add(Path(mount["Source"]).resolve())
    return sources


def guard(run, paths):
    if run.is_symlink() or not run.is_dir() or not paths:
        raise ValueError("invalid or empty old-run selection")
    for path in paths:
        if (
            path.is_symlink() or not path.is_file() or path.resolve() != path.absolute()
            or not path.is_relative_to(run) or path.stat().st_uid != os.getuid()
            or path.stat().st_nlink != 1
        ):
            raise ValueError("unexpected source file: " + str(path))
    protected = protected_inputs()
    mounts = mounted_sources()
    for path in paths:
        if any(path.is_relative_to(item) or item.is_relative_to(path) for item in protected):
            raise ValueError("recent registered input must remain readable: " + str(path))
        if any(path.is_relative_to(item) for item in mounts):
            raise ValueError("selected file belongs to a live bind mount: " + str(path))
    names = set(map(str, paths))
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            arguments = (proc / "cmdline").read_bytes().split(b"\0")
            if any(str(run).encode() in item for item in arguments):
                raise ValueError("old run still referenced by a live process: " + proc.name)
            for item in (proc / "environ").read_bytes().split(b"\0"):
                if item.startswith(b"RUN_ROOT=") and str(run).encode() in item:
                    raise ValueError("old run still owns a live process: " + proc.name)
            cwd = (proc / "cwd").resolve()
            if cwd.is_relative_to(run):
                raise ValueError("live working directory uses old run: " + proc.name)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
        try:
            for fd in (proc / "fd").iterdir():
                try:
                    if os.readlink(fd) in names:
                        raise ValueError("selected log is open by process: " + proc.name)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    pass
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass


def inventory():
    rows, skipped = [], []
    for name in RUNS:
        run = RAW / name
        paths = selected(run)
        try:
            guard(run, paths)
            plan = run / "ab_plan.json"
            files = {str(path.relative_to(RAW)): snapshot(path) for path in paths}
            rows.append({
                "run_root": str(run), "paths": files,
                "plan_sha256": digest(plan), "status": "selected",
                "source_bytes": sum(row["size_bytes"] for row in files.values()),
                "source_allocated_bytes": sum(row["allocated_bytes"] for row in files.values()),
            })
        except ValueError as error:
            skipped.append({"run_root": str(run), "reason": str(error)})
    return {
        "created_at": now(), "runs": rows, "skipped": skipped,
        "source_bytes": sum(row["source_bytes"] for row in rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "operator_sha256": digest(Path(__file__)),
        "preserved": [
            "All workspaces, bind mounts, processes and frozen worktrees.",
            "All plans, summaries, client workflow events, request token bundles and failure/stop receipts.",
            "All Qwen3-Coder runs, current Full/baselines, V230/V311 source, and recent registered inputs.",
            "All reports, figures, trained models and prepared workloads.",
        ],
    }


def execute(plan):
    if REPORT.exists() or ARCHIVES.exists() or not plan["runs"]:
        raise FileExistsError("preserve prior receipts and archives; require a nonempty inventory")
    if digest(Path(__file__)) != plan["operator_sha256"]:
        raise ValueError("archive operator changed after selection")
    if shutil.disk_usage(ROOT).free < 16 * 1024 ** 3:
        raise ValueError("insufficient temporary archive space")
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) < 19:
        raise ValueError("archival requires nice19; launch with idle I/O scheduling")
    ARCHIVES.mkdir(parents=True)
    record = {
        **plan, "started_at": now(), "status": "archiving",
        "compression": "single-thread zstd -T1 -1; nice19; idle I/O",
        "free_bytes_before": shutil.disk_usage(ROOT).free,
        "concurrent_experiment_scope": "CPU/IO archival may overlap qualifications; timestamps retained.",
    }
    save(REPORT, record)
    try:
        for row in record["runs"]:
            run = Path(row["run_root"])
            paths = [RAW / name for name in row["paths"]]
            guard(run, paths)
            before = {str(path.relative_to(RAW)): snapshot(path) for path in paths}
            if before != row["paths"] or digest(run / "ab_plan.json") != row["plan_sha256"]:
                raise ValueError("old source changed after inventory")
            archive = ARCHIVES / (run.name + ".tar.zst")
            temporary = archive.with_suffix(archive.suffix + ".partial")
            row.update(status="compressing", started_at=now(), archive=str(archive))
            save(REPORT, record)
            print(json.dumps({"run": run.name, "status": "compressing",
                              "source_bytes": row["source_bytes"]}), flush=True)
            subprocess.run(
                ["tar", "--create", "--sort=name",
                 "--use-compress-program=zstd -T1 -1", "--file", str(temporary),
                 "--directory", str(RAW), "--null", "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in before),
                check=True, capture_output=True,
            )
            row["status"] = "comparing_all_source_bytes"
            save(REPORT, record)
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(temporary), "--directory", str(RAW)],
                check=True, capture_output=True,
            )
            guard(run, paths)
            if (
                before != {str(path.relative_to(RAW)): snapshot(path) for path in paths}
                or digest(run / "ab_plan.json") != row["plan_sha256"]
            ):
                raise ValueError("source changed before verified cleanup")
            row.update(
                status="verified", archive_bytes=temporary.stat().st_size,
                archive_sha256=digest(temporary), full_tar_comparison_passed=True,
            )
            temporary.replace(archive)
            save(REPORT, record)
            restore = shlex.join([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(RAW),
            ])
            row["restore_command"] = restore
            marker = run / "OLD_LOGS_ARCHIVED_20261029.md"
            with marker.open("x") as stream:
                stream.write(
                    "# Archived Superseded Logs\n\n"
                    "Selected large server/opportunity logs passed complete byte comparison "
                    "before raw copies were removed. Plans, summaries, workflow events, "
                    "request tokens, failures and workspaces remain.\n\n"
                    f"Archive: `{archive}`\n\nSHA256: `{row['archive_sha256']}`\n\n"
                    f"Receipt: `{REPORT}`\n\n```bash\n{restore}\n```\n"
                )
            for path in paths:
                if snapshot(path) != before[str(path.relative_to(RAW))]:
                    raise ValueError("source changed immediately before cleanup")
                path.unlink()
            row.update(
                status="verified_and_archived", finished_at=now(),
                remaining_archived_raw_files=sum(path.exists() for path in paths),
            )
            save(REPORT, record)
            print(json.dumps({"run": run.name, "status": row["status"],
                              "archive_bytes": row["archive_bytes"]}), flush=True)
        record.update(
            status="verified_and_archived", finished_at=now(),
            archive_bytes=sum(row["archive_bytes"] for row in record["runs"]),
            net_reclaimed_bytes=sum(
                row["source_allocated_bytes"] - (Path(row["archive"]).stat().st_blocks * 512)
                for row in record["runs"]
            ),
            free_bytes_after=shutil.disk_usage(ROOT).free,
        )
        save(REPORT, record)
        print(json.dumps({key: record[key] for key in (
            "status", "source_allocated_bytes", "archive_bytes", "net_reclaimed_bytes",
        )}), flush=True)
    except BaseException as error:
        record.update(status="failed_preserve_unverified_sources", error=repr(error), failed_at=now())
        save(REPORT, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        execute(json.loads(PLAN.read_bytes()))
    else:
        if PLAN.exists():
            raise FileExistsError("preserve the frozen archive inventory")
        plan = inventory()
        save(PLAN, plan)
        print(json.dumps({
            "selected_runs": len(plan["runs"]), "skipped": plan["skipped"],
            "source_bytes": plan["source_bytes"],
            "source_allocated_bytes": plan["source_allocated_bytes"],
            "files": sum(len(row["paths"]) for row in plan["runs"]),
        }, indent=2))


if __name__ == "__main__":
    main()
