"""Losslessly archive unreferenced historical derived reports in dated bundles."""

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
REPORTS = ROOT / "experiments/reports"
OPS = ROOT.parent / "ops"
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_old_reports"
PLAN = HERE / "inventory.json"
RECEIPT = HERE / "receipt.json"
MARKER = REPORTS / "OLD_REPORTS_ARCHIVED_20261101.md"
HELPER = REPORTS / "unused_archive_20261029/archive_old_logs.py"
HELPER_SHA256 = "ad46bd6e86e3eb9356bc17dbb747234e7d0f1f91738bfea82158b1a4cba8606f"
CUTOFF = datetime.fromisoformat("2026-10-24T00:00:00+08:00").timestamp()
MIN_BYTES = 256 * 1024
NAME = re.compile(r"v\d+[\w.-]*?(202610(?:0[1-9]|1\d|2[0-3]))[\w.-]*\.(json|json.gz|html|raw)$")
REFERENCE = re.compile(
    rb"(?<![A-Za-z0-9_])v[0-9]+[A-Za-z0-9_.-]*?\."
    rb"(?:json(?:\.gz)?|html|raw)(?![A-Za-z0-9_-])"
)
ESSENTIAL = re.compile(
    r"archive|inventory|registration|manifest|provenance|receipt|qualification|qualified"
    r"|(?:^|_)cpu(?:_|[.])|source_sha256|(?:^|_)frozen(?:_|[.])"
)
METADATA = re.compile(r"source|input|manifest|evidence|entry|freeze|registration|plan|state|review")
spec = importlib.util.spec_from_file_location("archive_20261029_helpers", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
digest, save, snapshot, now = helper.digest, helper.save, helper.snapshot, helper.now
if digest(HELPER) != HELPER_SHA256:
    raise ValueError("immutable archival helper changed")

_references_cache = {}


def tracked_paths():
    return {
        ROOT / os.fsdecode(path) for path in subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT,
        ).split(b"\0") if path
    }


def candidate_files():
    result = []
    tracked = tracked_paths()
    for path in sorted(REPORTS.iterdir()):
        if (
            not path.is_file() or path.is_symlink() or path in tracked
            or not NAME.fullmatch(path.name) or ESSENTIAL.search(path.name)
        ):
            continue
        info = path.stat()
        if (
            info.st_size >= MIN_BYTES and info.st_mtime < CUTOFF
            and info.st_uid == os.getuid() and info.st_nlink == 1
        ):
            result.append(path)
    return result


def scan_files():
    files = {
        ROOT / line for line in subprocess.check_output(
            ["rg", "--files", "--hidden", "beliefkv", "scripts", "tests", "docs", "configs",
             "-g", "*.py", "-g", "*.md", "-g", "*.json", "-g", "*.yaml", "-g", "*.yml",
             "-g", "*.toml", "-g", "!**/__pycache__/**"],
            cwd=ROOT, text=True,
        ).splitlines()
    }
    files.update(ROOT / name for name in ("AGENTS.md", "README.md") if (ROOT / name).is_file())
    files.update(
        path for path in OPS.rglob("*") if path.is_file() and not path.is_symlink()
        and path.suffix in {".json", ".py", ".md"}
        and path.stat().st_mtime >= CUTOFF
    )
    for path in REPORTS.rglob("*.json"):
        if path.is_relative_to(HERE) or path.is_symlink():
            continue
        if path.stat().st_mtime >= CUTOFF and METADATA.search(path.name):
            files.add(path)
    # Also pin live input arguments even when their metadata has an older mtime.
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cwd = (proc / "cwd").resolve()
            for argument in (proc / "cmdline").read_bytes().split(b"\0"):
                text = os.fsdecode(argument)
                if not text.endswith((".json", ".py", ".md")):
                    continue
                path = Path(text)
                if not path.is_absolute():
                    path = cwd / path
                if path.is_relative_to(ROOT.parent) and path.is_file():
                    files.add(path)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return sorted(path for path in files if not path.is_relative_to(HERE))


def references(names):
    protected = {}
    metadata = []
    for path in scan_files():
        info = path.stat()
        signature = (info.st_ino, info.st_size, info.st_mtime_ns)
        cached = _references_cache.get(path)
        if cached is None or cached[0] != signature:
            # Byte matching tolerates historical profiling exports with invalid UTF-8.
            matches = set()
            with path.open("rb") as stream:
                for line in stream:
                    matches.update(os.fsdecode(item) for item in REFERENCE.findall(line))
            cached = (signature, matches)
            _references_cache[path] = cached
        for name in cached[1] & names:
            protected.setdefault(name, []).append(str(path))
        metadata.append([str(path), *signature])
    return protected, {
        "reference_files_scanned": len(metadata),
        "reference_stat_manifest_sha256": hashlib.sha256(
            json.dumps(metadata, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def live_guard(paths):
    names = {path.name for path in paths}
    absolute = set(map(str, paths))
    mounts = helper.mounted_sources()
    for path in paths:
        if any(path.is_relative_to(source) for source in mounts):
            raise ValueError("selected report belongs to a running bind mount: " + str(path))
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            arguments = (proc / "cmdline").read_bytes().split(b"\0")
            for argument in arguments:
                text = os.fsdecode(argument)
                if text in absolute or Path(text).name in names:
                    raise ValueError("selected report is a live argument: " + proc.name)
            if (proc / "cwd").resolve() == REPORTS:
                raise ValueError("report directory has a live working process: " + proc.name)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
        try:
            for fd in (proc / "fd").iterdir():
                try:
                    if os.readlink(fd) in absolute:
                        raise ValueError("selected report is open: " + proc.name)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    pass
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass


def guard(paths):
    tracked = tracked_paths()
    for path in paths:
        info = path.stat()
        if (
            path.parent != REPORTS or path.is_symlink() or not path.is_file()
            or path.resolve() != path.absolute() or path in tracked
            or info.st_uid != os.getuid() or info.st_nlink != 1
            or info.st_mtime >= CUTOFF or not NAME.fullmatch(path.name)
            or ESSENTIAL.search(path.name)
        ):
            raise ValueError("report no longer matches archival scope: " + str(path))
    protected, checks = references({path.name for path in paths})
    if protected:
        raise ValueError("report gained a current reference: " + repr(protected))
    live_guard(paths)
    return checks


def inventory():
    candidates = candidate_files()
    protected, checks = references({path.name for path in candidates})
    paths = [path for path in candidates if path.name not in protected]
    live_guard(paths)
    groups = {}
    for path in paths:
        day = NAME.fullmatch(path.name).group(1)
        groups.setdefault(day, {})[path.name] = {
            **snapshot(path), "sha256": digest(path),
        }
    kept = []
    for path in candidates:
        if path.name in protected:
            kept.append({
                "path": path.name, "size_bytes": path.stat().st_size,
                "references": protected[path.name],
            })
    return {
        "created_at": now(), "operator_sha256": digest(Path(__file__)),
        "helper_sha256": digest(HELPER), "protection": checks,
        "candidate_files": len(candidates), "retained_referenced_reports": kept,
        "groups": [{
            "day": day, "status": "selected", "paths": files,
            "source_bytes": sum(row["size_bytes"] for row in files.values()),
            "source_allocated_bytes": sum(row["allocated_bytes"] for row in files.values()),
        } for day, files in sorted(groups.items())],
        "selected_files": len(paths),
        "source_bytes": sum(path.stat().st_size for path in paths),
        "source_allocated_bytes": sum(path.stat().st_blocks * 512 for path in paths),
        "policy": {
            "scope": "Untracked flat versioned derived reports, Oct 1-23, at least 256 KiB.",
            "reference_guard": "Code/docs/tests/configs, recent ops/metadata and live arguments.",
            "tracked_files_changed_or_removed": False,
            "recent_report_directories_changed": False,
        },
        "preserved": [
            "Current and recent reports, models, datasets and source/token packages.",
            "All Git-tracked reports and all currently referenced historical reports.",
            "All qualification/CPU proofs, inventories, receipts, provenance and archive evidence.",
            "All paper PDFs, presentations, workspaces and running processes.",
            "Archived negative/failed analyses retain their exact original bytes.",
        ],
    }


def check_sources(row):
    for name, expected in row["paths"].items():
        path = REPORTS / name
        if snapshot(path) != {key: value for key, value in expected.items() if key != "sha256"}:
            raise ValueError("report changed after selection: " + name)


def execute(plan):
    if RECEIPT.exists() or ARCHIVES.exists() or MARKER.exists() or not plan["groups"]:
        raise FileExistsError("require a fresh nonempty report archive inventory")
    if digest(Path(__file__)) != plan["operator_sha256"]:
        raise ValueError("archive operator changed")
    if shutil.disk_usage(ROOT).free < 8 * 1024 ** 3:
        raise ValueError("insufficient scratch capacity")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True).strip()
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("launch with nice19 and idle I/O scheduling")
    ARCHIVES.mkdir()
    record = {
        **plan, "status": "archiving", "started_at": now(),
        "compression": "zstd -T1 -1", "cpu_nice": 19, "io_priority": io,
        "free_bytes_before": shutil.disk_usage(ROOT).free,
        "scope": "CPU/IO report archival only; no GPU launch, process stop or policy change.",
    }
    save(RECEIPT, record)
    with MARKER.open("x") as stream:
        stream.write(
            "# Archived Historical Reports\n\n"
            f"Inventory: `{PLAN}`\n\nReceipt: `{RECEIPT}`\n\n"
            f"Archive and recovery index: `{ARCHIVES / 'README.md'}`\n\n"
            "Only verified original copies are removed. Restore the required dated bundle "
            "before rerunning an old analysis. Source files, recent reports and tracked "
            "reports remain directly available.\n"
        )
    try:
        for row in record["groups"]:
            paths = [REPORTS / name for name in row["paths"]]
            row["pre_archive_protection"] = guard(paths)
            check_sources(row)
            archive = ARCHIVES / (row["day"] + "_reports.tar.zst")
            partial = archive.with_suffix(".zst.partial")
            row.update(status="compressing", archive=str(archive), started_at=now())
            save(RECEIPT, record)
            print(json.dumps({"day": row["day"], "status": row["status"],
                              "files": len(paths), "source_bytes": row["source_bytes"]}), flush=True)
            subprocess.run(
                ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
                 "--file", str(partial), "--directory", str(REPORTS), "--null",
                 "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in row["paths"]),
                check=True, capture_output=True,
            )
            row["status"] = "comparing_all_source_bytes"
            save(RECEIPT, record)
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(partial), "--directory", str(REPORTS)],
                check=True, capture_output=True,
            )
            row["pre_remove_protection"] = guard(paths)
            check_sources(row)
            if any(digest(REPORTS / name) != expected["sha256"]
                   for name, expected in row["paths"].items()):
                raise ValueError("original report SHA256 changed")
            row.update(
                status="verified", archive_bytes=partial.stat().st_size,
                archive_sha256=digest(partial), full_tar_comparison_passed=True,
            )
            partial.replace(archive)
            row["restore_command"] = shlex.join([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(REPORTS),
            ])
            save(RECEIPT, record)
            for path in paths:
                expected = row["paths"][path.name]
                if snapshot(path) != {key: value for key, value in expected.items() if key != "sha256"}:
                    raise ValueError("report changed immediately before cleanup")
                path.unlink()
            row.update(status="verified_and_archived", finished_at=now())
            save(RECEIPT, record)
            print(json.dumps({"day": row["day"], "status": row["status"],
                              "archive_bytes": row["archive_bytes"]}), flush=True)
        record.update(
            status="verified_and_archived", finished_at=now(),
            archive_bytes=sum(row["archive_bytes"] for row in record["groups"]),
            net_reclaimed_bytes=sum(
                row["source_allocated_bytes"] - Path(row["archive"]).stat().st_blocks * 512
                for row in record["groups"]
            ),
            free_bytes_after=shutil.disk_usage(ROOT).free,
        )
        save(RECEIPT, record)
        print(json.dumps({key: record[key] for key in (
            "status", "selected_files", "source_allocated_bytes",
            "archive_bytes", "net_reclaimed_bytes",
        )}), flush=True)
    except BaseException as error:
        record.update(status="failed_preserve_unverified_sources", error=repr(error), failed_at=now())
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
            raise FileExistsError("preserve the existing report selection")
        plan = inventory()
        save(PLAN, plan)
        print(json.dumps({
            "selected_files": plan["selected_files"], "bundles": len(plan["groups"]),
            "retained_referenced_reports": len(plan["retained_referenced_reports"]),
            "source_bytes": plan["source_bytes"],
            "source_allocated_bytes": plan["source_allocated_bytes"],
        }, indent=2))


if __name__ == "__main__":
    main()
