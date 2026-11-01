"""Losslessly archive inactive Qwen3-Coder logs without moving source captures."""

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
RAW = ROOT / "experiments/raw"
OPS = ROOT.parent / "ops"
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_qwen3_unused_logs"
PLAN = HERE / "inventory.json"
REPORT = HERE / "receipt.json"
MARKER = "OLD_QWEN3_LOGS_ARCHIVED_20261101.md"
HELPER = HERE.parent / "unused_archive_20261029/archive_old_logs.py"
HELPER_SHA256 = "ad46bd6e86e3eb9356bc17dbb747234e7d0f1f91738bfea82158b1a4cba8606f"
RECENT = datetime.fromisoformat("2026-10-29T00:00:00+08:00").timestamp()
MODEL = "Qwen3-Coder-30B-A3B-Instruct"
MIN_RUN_BYTES = 64 * 1024 ** 2
NON_CONFIG_PROFILES = {
    OPS / "v552_v58_active_stack_20261014.speedscope.json":
        "b9fa5360f47067cbeff0866dc50c49c40e00f533c1cf7bdee702a5f5bfce390d",
}

spec = importlib.util.spec_from_file_location("archive_20261029_helpers", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
digest, save, snapshot, now = helper.digest, helper.save, helper.snapshot, helper.now
if digest(HELPER) != HELPER_SHA256:
    raise ValueError("immutable archive helper differs")

_metadata_cache = {}


def raw_paths(value):
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


def live_metadata():
    found = set()
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
                found.add(Path(text))
    return found


def protection():
    live = live_metadata()
    files = set(OPS.glob("*.json")) | live
    protected, signatures = set(), []
    for path in sorted(files):
        info = path.stat()
        signature = (info.st_ino, info.st_size, info.st_mtime_ns)
        if path in NON_CONFIG_PROFILES:
            if path in live or digest(path) != NON_CONFIG_PROFILES[path]:
                raise ValueError("historical non-config profile changed or became live")
            signatures.append([str(path), *signature])
            continue
        cached = _metadata_cache.get(path)
        if cached is None or cached[0] != signature:
            cached = (signature, set(raw_paths(json.loads(path.read_bytes()))))
            _metadata_cache[path] = cached
        recent_or_live = info.st_mtime >= RECENT or path in live
        # Historical output directories do not make their old logs live inputs.
        # Every explicit file input remains protected, regardless of its age.
        protected.update(
            item for item in cached[1] if recent_or_live or not item.is_dir()
        )
        signatures.append([str(path), *signature])
    stamp = hashlib.sha256(
        json.dumps(signatures, separators=(",", ":")).encode()
    ).hexdigest()
    return protected, {
        "metadata_files": len(files), "live_metadata_files": sorted(map(str, live)),
        "metadata_stat_manifest_sha256": stamp, "protected_paths": len(protected),
        "preserved_non_config_profiles": {str(key): value for key, value in
                                          NON_CONFIG_PROFILES.items()},
    }


def eligible_name(run):
    return (
        run.is_dir() and not run.is_symlink()
        and re.search(r"2026102[4-8](?:_|$)", run.name) is not None
        and not run.name.startswith("qwen35_")
        and "source" not in run.name
    )


def selected(run):
    paths = set(helper.selected(run))
    paths.update(
        path for path in run.glob("*/server.log") if path.stat().st_size >= 1024 ** 2
    )
    return sorted(paths)


def preserved_metadata(run):
    paths = set(run.glob("*.json"))
    paths.update(run.glob("*/server/server_info.json"))
    paths.update(run.glob("*/server/launch_configuration.json"))
    if not any(path.parent == run for path in paths):
        raise ValueError("no original top-level result/registration metadata")
    if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("unexpected metadata path")
    return {str(path.relative_to(RAW)): digest(path) for path in sorted(paths)}


def guard(run, paths):
    if not eligible_name(run) or not paths:
        raise ValueError("invalid or empty historical Qwen3 selection")
    protected, checks = protection()
    mounts = helper.mounted_sources()
    for path in paths:
        info = path.stat()
        if (
            path.is_symlink() or not path.is_file() or path.resolve() != path.absolute()
            or not path.is_relative_to(run) or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise ValueError("unexpected selected source: " + str(path))
        if any(path.is_relative_to(item) or item.is_relative_to(path) for item in protected):
            raise ValueError("registered file or recent/live input: " + str(path))
        if any(path.is_relative_to(item) for item in mounts):
            raise ValueError("selected file is in a running container mount: " + str(path))
    names = set(map(str, paths))
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if any(str(run).encode() in item for item in
                   (proc / "cmdline").read_bytes().split(b"\0")):
                raise ValueError("run is referenced by a live process: " + proc.name)
            if any(item.startswith(b"RUN_ROOT=") and str(run).encode() in item
                   for item in (proc / "environ").read_bytes().split(b"\0")):
                raise ValueError("run owns a live process: " + proc.name)
            if (proc / "cwd").resolve().is_relative_to(run):
                raise ValueError("run has a live working directory: " + proc.name)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
        try:
            for fd in (proc / "fd").iterdir():
                try:
                    if os.readlink(fd) in names:
                        raise ValueError("selected file is open: " + proc.name)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    pass
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return checks


def inventory():
    rows, skipped = [], []
    for run in sorted(RAW.iterdir()):
        if not eligible_name(run):
            continue
        paths = selected(run)
        if sum(path.stat().st_size for path in paths) < MIN_RUN_BYTES:
            continue
        try:
            model_files = list(run.glob("*/server/server_info.json"))
            models = {
                json.loads(path.read_bytes()).get("model_path", "").split("/")[-1]
                for path in model_files
            }
            if models != {MODEL}:
                raise ValueError("missing or different model identity: " + repr(models))
            protected, _ = protection()
            retained = [
                path for path in paths if any(
                    path.is_relative_to(item) or item.is_relative_to(path)
                    for item in protected
                )
            ]
            paths = [path for path in paths if path not in retained]
            if sum(path.stat().st_size for path in paths) < MIN_RUN_BYTES:
                raise ValueError("unreferenced logs below 64 MiB after retaining registered inputs")
            checks = guard(run, paths)
            if (run / MARKER).exists():
                raise ValueError("existing archive marker")
            metadata = preserved_metadata(run)
            metadata.update({str(path.relative_to(RAW)): digest(path) for path in retained})
            files = {str(path.relative_to(RAW)): snapshot(path) for path in paths}
            rows.append({
                "run_root": str(run), "status": "selected", "paths": files,
                "preserved_metadata_sha256": metadata, "protection": checks,
                "retained_registered_logs": [
                    str(path.relative_to(RAW)) for path in retained
                ],
                "model": MODEL,
                "source_bytes": sum(item["size_bytes"] for item in files.values()),
                "source_allocated_bytes": sum(
                    item["allocated_bytes"] for item in files.values()
                ),
            })
        except ValueError as error:
            skipped.append({"run_root": str(run), "reason": str(error)})
    return {
        "created_at": now(), "runs": rows, "skipped": skipped,
        "source_bytes": sum(row["source_bytes"] for row in rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "operator_sha256": digest(Path(__file__)), "helper_sha256": digest(HELPER),
        "protection_policy": {
            "recent_metadata_since": "2026-10-29T00:00:00+08:00",
            "all_explicit_file_inputs_protected_regardless_of_age": True,
            "unreferenced_logs_selected_individually_within_historical_runs": True,
            "historical_output_directory_is_not_a_live_input": True,
            "candidate_dates": "2026-10-24 through 2026-10-28",
            "minimum_selected_log_bytes_per_run": MIN_RUN_BYTES,
            "preserved_non_config_profiles": {str(key): value for key, value in
                                              NON_CONFIG_PROFILES.items()},
        },
        "preserved": [
            "Every naturally captured source run, token bundle and workflow event stream.",
            "Every Oct 29 or newer run and every recent/live registered input.",
            "All top-level plans/results/registrations/failures and server configuration.",
            "All workspaces, bind mounts, processes, frozen trees, models and reports.",
            "Failed and negative results retain their status and recoverable log bytes.",
        ],
    }


def check_unchanged(row):
    if any(digest(RAW / name) != value for name, value in
           row["preserved_metadata_sha256"].items()):
        raise ValueError("preserved original metadata changed")
    if row["paths"] != {name: snapshot(RAW / name) for name in row["paths"]}:
        raise ValueError("selected source changed")


def execute(plan):
    if REPORT.exists() or ARCHIVES.exists() or not plan["runs"]:
        raise FileExistsError("require a new nonempty immutable archive selection")
    if digest(Path(__file__)) != plan["operator_sha256"] or digest(HELPER) != HELPER_SHA256:
        raise ValueError("operator identity changed")
    if shutil.disk_usage(ROOT).free < 16 * 1024 ** 3:
        raise ValueError("insufficient archive scratch capacity")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True).strip()
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("launch with nice19 and idle I/O")
    ARCHIVES.mkdir()
    record = {
        **plan, "started_at": now(), "status": "archiving",
        "compression": "zstd -T1 -1", "cpu_nice": 19, "io_priority": io,
        "free_bytes_before": shutil.disk_usage(ROOT).free,
        "scope": "CPU/IO archival only; no GPU launch or experiment configuration change.",
    }
    save(REPORT, record)
    try:
        for row in record["runs"]:
            run = Path(row["run_root"])
            paths = [RAW / name for name in row["paths"]]
            row["pre_archive_protection"] = guard(run, paths)
            check_unchanged(row)
            archive = ARCHIVES / (run.name + ".tar.zst")
            partial = archive.with_suffix(".zst.partial")
            row.update(status="compressing", started_at=now(), archive=str(archive))
            save(REPORT, record)
            print(json.dumps({"run": run.name, "status": row["status"]}), flush=True)
            subprocess.run(
                ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
                 "--file", str(partial), "--directory", str(RAW), "--null",
                 "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in row["paths"]),
                check=True, capture_output=True,
            )
            row["status"] = "comparing_all_source_bytes"
            save(REPORT, record)
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(partial), "--directory", str(RAW)],
                check=True, capture_output=True,
            )
            row["pre_remove_protection"] = guard(run, paths)
            check_unchanged(row)
            row.update(
                status="verified", archive_bytes=partial.stat().st_size,
                archive_sha256=digest(partial), full_tar_comparison_passed=True,
            )
            partial.replace(archive)
            row["restore_command"] = shlex.join([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(RAW),
            ])
            save(REPORT, record)
            with (run / MARKER).open("x") as stream:
                stream.write(
                    "# Archived Historical Qwen3-Coder Logs\n\n"
                    "Complete tar comparison passed before removal of raw log copies. "
                    "Source traces, workflow events, tokens, metadata and workspaces remain.\n\n"
                    f"Archive: `{archive}`\n\nSHA256: `{row['archive_sha256']}`\n\n"
                    f"Receipt: `{REPORT}`\n\n```bash\n{row['restore_command']}\n```\n"
                )
            for path in paths:
                if snapshot(path) != row["paths"][str(path.relative_to(RAW))]:
                    raise ValueError("source changed immediately before cleanup")
                path.unlink()
            row.update(status="verified_and_archived", finished_at=now())
            save(REPORT, record)
            print(json.dumps({"run": run.name, "status": row["status"]}), flush=True)
        record.update(
            status="verified_and_archived", finished_at=now(),
            archive_bytes=sum(row["archive_bytes"] for row in record["runs"]),
            net_reclaimed_bytes=sum(
                row["source_allocated_bytes"] - Path(row["archive"]).stat().st_blocks * 512
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
            raise FileExistsError("preserve existing archive inventory")
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
