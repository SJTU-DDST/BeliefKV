"""Archive superseded logs only after full comparison and live-input checks."""

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "experiments/raw"
OPS = ROOT.parent / "ops"
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_unused_logs"
PLAN = HERE / "inventory.json"
REPORT = HERE / "receipt.json"
MARKER = "OLD_LOGS_ARCHIVED_20261101.md"
HELPER = HERE.parent / "unused_archive_20261029/archive_old_logs.py"
HELPER_SHA256 = "ad46bd6e86e3eb9356bc17dbb747234e7d0f1f91738bfea82158b1a4cba8606f"
RUNS = (
    "qwen35_native_predictive_replenished_108plus48_20261013_v53",
    "qwen35_native_predictive_replenished_108plus48_20261016_v72_workflow_work",
    "qwen35_nested_prefetch_two_wave_156root_20261018_v82",
    "qwen35_nested_prefetch_two_wave_156root_20261018_v82b",
    "qwen35_nested_eos_prefetch_two_wave_156root_20261019_v83c",
    "qwen35_locobench_long_12root_running8_20261019_v85d",
    "qwen35_locobench_long_24root_running8_20261020_v89",
    "qwen35_locobench_long_24root_running8_20261020_v91b",
    "qwen35_locobench_long_24root_running8_20261020_v92",
    "qwen35_locobench_long_24root_running8_20261020_v93",
    "qwen35_locobench_long_24root_running8_20261020_v94",
    "qwen35_locobench_long_24root_running8_20261020_v95",
    "qwen35_locobench_long_36root_running8_20261020_v96",
    "qwen35_locobench_long_36root_running8_20261020_v97",
    "qwen35_locobench_long_36root_running8_20261020_v98",
    "qwen35_locobench_long_36root_running8_20261020_v99",
    "qwen35_locobench_long_36root_running8_20261020_v101",
    "qwen35_locobench_long_36root_running8_20261021_v103",
    "qwen35_locobench_long_36root_running8_20261021_v104",
    "qwen35_locobench_long_36root_running8_20261021_v105",
    "qwen35_locobench_long_36root_running8_20261021_v106",
    "qwen35_locobench_long_36root_running8_20261021_v107",
    "qwen35_locobench_long_36root_running12_20261021_v109",
    "qwen35_locobench_long_36root_running12_internal_20261021_v111",
)


spec = importlib.util.spec_from_file_location("archive_20261029_helpers", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
if helper.digest(HELPER) != HELPER_SHA256:
    raise ValueError("immutable archive helper differs")

digest = helper.digest
save = helper.save
snapshot = helper.snapshot
now = helper.now
selected = helper.selected


def raw_paths(value):
    """Read path keys as well as values in registered hash manifests."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from raw_paths(key)
            yield from raw_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from raw_paths(item)
    elif isinstance(value, str):
        if value.startswith(str(RAW) + "/"):
            yield Path(value).resolve()
        elif value.startswith("experiments/raw/"):
            yield (ROOT / value).resolve()


def live_registrations():
    paths = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            arguments = (proc / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for argument in arguments:
            text = os.fsdecode(argument)
            if text.startswith(str(OPS) + "/") and text.endswith(".json"):
                path = Path(text)
                if path.is_file():
                    paths.add(path)
    return paths


def protection():
    # No upper date bound: future registrations and Nov 1 recovery plans count.
    files = live_registrations()
    for path in OPS.glob("*.json"):
        if path.stat().st_mtime >= datetime.fromisoformat(
            "2026-10-24T00:00:00+08:00"
        ).timestamp():
            files.add(path)
    protected = set()
    manifests = []
    for path in sorted(files):
        payload = json.loads(path.read_bytes())
        protected.update(raw_paths(payload))
        manifests.append(str(path))
    return protected, manifests


def guard(run, paths):
    if run.name not in RUNS or run.is_symlink() or not run.is_dir() or not paths:
        raise ValueError("invalid or empty old-run selection")
    for path in paths:
        if (
            path.is_symlink() or not path.is_file()
            or path.resolve() != path.absolute() or not path.is_relative_to(run)
            or path.stat().st_uid != os.getuid() or path.stat().st_nlink != 1
        ):
            raise ValueError("unexpected selected source: " + str(path))
    protected, manifests = protection()
    mounts = helper.mounted_sources()
    for path in paths:
        if any(path.is_relative_to(item) or item.is_relative_to(path) for item in protected):
            raise ValueError("registered input must remain directly readable: " + str(path))
        if any(path.is_relative_to(item) for item in mounts):
            raise ValueError("selected source is in a running container mount: " + str(path))
    # Reuse the stopped-owner/open-file checks with the broader protection set.
    helper.protected_inputs = lambda: protected
    helper.mounted_sources = lambda: mounts
    helper.guard(run, paths)
    return {"protected_paths": len(protected), "checked_metadata_files": manifests}


MOUNTED_SOURCES = helper.mounted_sources


def checked_guard(run, paths):
    # helper.guard reads this module-level helper; reset before each fresh scan.
    helper.mounted_sources = MOUNTED_SOURCES
    return guard(run, paths)


def inventory():
    rows, skipped = [], []
    for name in RUNS:
        run = RAW / name
        paths = selected(run)
        try:
            checks = checked_guard(run, paths)
            plan = run / "ab_plan.json"
            if not plan.is_file() or (run / MARKER).exists():
                raise ValueError("missing original plan or existing archive marker")
            files = {str(path.relative_to(RAW)): snapshot(path) for path in paths}
            rows.append({
                "run_root": str(run), "paths": files,
                "plan_sha256": digest(plan), "status": "selected",
                "source_bytes": sum(item["size_bytes"] for item in files.values()),
                "source_allocated_bytes": sum(
                    item["allocated_bytes"] for item in files.values()
                ),
                "protection": checks,
            })
        except ValueError as error:
            skipped.append({"run_root": str(run), "reason": str(error)})
    return {
        "created_at": now(), "runs": rows, "skipped": skipped,
        "source_bytes": sum(row["source_bytes"] for row in rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "operator_sha256": digest(Path(__file__)), "helper_sha256": digest(HELPER),
        "preserved": [
            "All workspaces, running containers/processes and frozen source worktrees.",
            "All Qwen3-Coder and Oct 22 or newer runs, v29/v58 native references and v108.",
            "All recent/live registered raw inputs, current native/Full and baseline runs.",
            "All plans, outcomes, summaries, workflow events, request tokens and source traces.",
            "All negative results, failed attempts, normal tails, models, figures and reports.",
        ],
    }


def execute(plan):
    if REPORT.exists() or ARCHIVES.exists() or not plan["runs"]:
        raise FileExistsError("require a new nonempty immutable archive selection")
    if digest(Path(__file__)) != plan["operator_sha256"] or digest(HELPER) != HELPER_SHA256:
        raise ValueError("operator changed after inventory")
    if shutil.disk_usage(ROOT).free < 16 * 1024 ** 3:
        raise ValueError("insufficient archive scratch capacity")
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) < 19:
        raise ValueError("launch with nice19 and idle I/O priority")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True).strip()
    if "idle" not in io:
        raise ValueError("idle I/O scheduling is required")
    ARCHIVES.mkdir()
    receipt = {
        **plan, "started_at": now(), "status": "archiving",
        "compression": "zstd -T1 -1", "cpu_nice": 19, "io_priority": io,
        "free_bytes_before": shutil.disk_usage(ROOT).free,
        "scope": "CPU/IO archival; no GPU launch, process stop or experiment configuration change.",
    }
    save(REPORT, receipt)
    try:
        for row in receipt["runs"]:
            run = Path(row["run_root"])
            paths = [RAW / name for name in row["paths"]]
            row["pre_archive_protection"] = checked_guard(run, paths)
            before = {str(path.relative_to(RAW)): snapshot(path) for path in paths}
            if before != row["paths"] or digest(run / "ab_plan.json") != row["plan_sha256"]:
                raise ValueError("source or plan changed after selection")
            archive = ARCHIVES / (run.name + ".tar.zst")
            partial = archive.with_suffix(".zst.partial")
            row.update(status="compressing", started_at=now(), archive=str(archive))
            save(REPORT, receipt)
            print(json.dumps({
                "run": run.name, "status": row["status"], "source_bytes": row["source_bytes"]
            }), flush=True)
            subprocess.run(
                ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
                 "--file", str(partial), "--directory", str(RAW), "--null",
                 "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in before),
                check=True, capture_output=True,
            )
            row["status"] = "comparing_all_source_bytes"
            save(REPORT, receipt)
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(partial), "--directory", str(RAW)],
                check=True, capture_output=True,
            )
            row["pre_remove_protection"] = checked_guard(run, paths)
            if (
                before != {str(path.relative_to(RAW)): snapshot(path) for path in paths}
                or digest(run / "ab_plan.json") != row["plan_sha256"]
            ):
                raise ValueError("source changed before cleanup")
            row.update(
                status="verified", archive_bytes=partial.stat().st_size,
                archive_sha256=digest(partial), full_tar_comparison_passed=True,
            )
            partial.replace(archive)
            row["restore_command"] = shlex.join([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(RAW),
            ])
            save(REPORT, receipt)
            with (run / MARKER).open("x") as stream:
                stream.write(
                    "# Archived Superseded Logs\n\n"
                    "Complete tar content comparison passed before removal of raw copies. "
                    "Plans, outcomes, workflow events, source/request tokens and workspaces remain.\n\n"
                    f"Archive: `{archive}`\n\nSHA256: `{row['archive_sha256']}`\n\n"
                    f"Receipt: `{REPORT}`\n\n```bash\n{row['restore_command']}\n```\n"
                )
            for path in paths:
                if snapshot(path) != before[str(path.relative_to(RAW))]:
                    raise ValueError("source changed immediately before cleanup")
                path.unlink()
            row.update(
                status="verified_and_archived", finished_at=now(),
                remaining_archived_raw_files=sum(path.exists() for path in paths),
            )
            save(REPORT, receipt)
            print(json.dumps({
                "run": run.name, "status": row["status"], "archive_bytes": row["archive_bytes"]
            }), flush=True)
        receipt.update(
            status="verified_and_archived", finished_at=now(),
            archive_bytes=sum(row["archive_bytes"] for row in receipt["runs"]),
            net_reclaimed_bytes=sum(
                row["source_allocated_bytes"] - Path(row["archive"]).stat().st_blocks * 512
                for row in receipt["runs"]
            ),
            free_bytes_after=shutil.disk_usage(ROOT).free,
        )
        save(REPORT, receipt)
        print(json.dumps({key: receipt[key] for key in (
            "status", "source_allocated_bytes", "archive_bytes", "net_reclaimed_bytes",
        )}), flush=True)
    except BaseException as error:
        receipt.update(status="failed_preserve_unverified_sources", error=repr(error), failed_at=now())
        save(REPORT, receipt)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        execute(json.loads(PLAN.read_bytes()))
    else:
        if PLAN.exists():
            raise FileExistsError("preserve existing selection")
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
