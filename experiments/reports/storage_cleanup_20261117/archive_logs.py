"""Archive individually unreferenced October telemetry with complete byte comparison."""

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "experiments/raw"
ARCHIVES = ROOT / "experiments/archive/20261117_historical_logs"
PLAN, RECEIPT = HERE / "log_inventory.json", HERE / "log_receipt.json"
MARKER = "OLD_LOGS_ARCHIVED_20261117.md"
ORIGINAL = HERE.parent / "qwen3_unused_archive_20261101/archive_old_logs.py"
spec = importlib.util.spec_from_file_location("previous_log_guards", ORIGINAL)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
old.RECENT = datetime.fromisoformat("2026-11-10T00:00:00+08:00").timestamp()
original_protection = old.protection
original_mounts = old.helper.mounted_sources
date_pattern = re.compile(r"202610(?:22|23)(?:_|$)")
raw_pattern = re.compile(rb"(?:/home/longhao/experiment/BeliefKV/)?experiments/raw/[A-Za-z0-9_./-]+")
reference_cache = {}


def eligible(run):
    return (
        run.is_dir() and not run.is_symlink() and run.name.startswith("qwen35_")
        and date_pattern.search(run.name) is not None and "source" not in run.name
    )


def protection():
    paths, proof = original_protection()
    names = subprocess.check_output(
        ["rg", "--files", "beliefkv", "scripts", "tests", "docs", "configs",
         "-g", "*.py", "-g", "*.md", "-g", "*.json", "-g", "*.yaml", "-g", "*.toml"],
        cwd=ROOT, text=True,
    ).splitlines() + ["AGENTS.md"]
    references = set()
    for name in names:
        source = ROOT / name
        info = source.stat()
        signature = (info.st_ino, info.st_size, info.st_mtime_ns)
        cached = reference_cache.get(name)
        if cached is None or cached[0] != signature:
            found = set()
            for match in raw_pattern.finditer(source.read_bytes()):
                value = os.fsdecode(match.group()).rstrip(".")
                path = Path(value) if value.startswith("/") else ROOT / value
                if path.is_file():
                    found.add(path)
            cached = (signature, found)
            reference_cache[name] = cached
        references.update(cached[1])
    paths.update(references)
    proof.update(code_and_doc_files=len(names), explicit_document_file_references=len(references))
    return paths, proof


def mounted_sources():
    for attempt in range(3):
        try:
            return original_mounts()
        except subprocess.CalledProcessError as error:
            if error.cmd[:2] != ["docker", "inspect"] or attempt == 2:
                raise
    raise AssertionError("unreachable")


old.eligible_name, old.protection, old.helper.mounted_sources = eligible, protection, mounted_sources


def inventory():
    if PLAN.exists():
        raise FileExistsError("preserve original log selection")
    rows, skipped = [], []
    protected, proof = protection()
    for run in sorted(RAW.iterdir()):
        if not eligible(run):
            continue
        paths = [
            path for path in old.selected(run)
            if path.stat().st_size >= 64 * 1024 ** 2
            and path.stat().st_mtime < old.RECENT
        ]
        retained = [
            path for path in paths
            if any(path.is_relative_to(item) or item.is_relative_to(path) for item in protected)
        ]
        paths = [path for path in paths if path not in retained]
        if not paths:
            continue
        try:
            checks = old.guard(run, paths)
            if (run / MARKER).exists():
                raise ValueError("existing restoration marker")
            metadata = old.preserved_metadata(run)
            rows.append({
                "run_root": str(run), "status": "selected",
                "paths": {str(path.relative_to(RAW)): old.snapshot(path) for path in paths},
                "preserved_metadata_sha256": metadata, "protection": checks,
                "source_allocated_bytes": sum(path.stat().st_blocks * 512 for path in paths),
                "retained_referenced_logs": list(map(str, retained)),
            })
        except ValueError as error:
            skipped.append({"run_root": str(run), "reason": str(error)})
    plan = {
        "created_at": old.now(), "operator_sha256": old.digest(Path(__file__)),
        "previous_guard_sha256": old.digest(ORIGINAL), "protection": proof,
        "runs": rows, "skipped": skipped,
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "preserved": "Tokens, workflow events, source runs, results, metadata, recent and live inputs.",
    }
    old.save(PLAN, plan)
    print(json.dumps({
        "runs": len(rows), "files": sum(len(row["paths"]) for row in rows),
        "source_allocated_bytes": plan["source_allocated_bytes"], "skipped": skipped,
    }), flush=True)


def execute(plan):
    if RECEIPT.exists() or ARCHIVES.exists() or not plan["runs"]:
        raise ValueError("require a fresh nonempty log plan")
    if old.digest(ORIGINAL) != plan["previous_guard_sha256"]:
        raise ValueError("original log guards changed")
    io = subprocess.check_output(["ionice", "-p", str(os.getpid())], text=True)
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 19 or "idle" not in io:
        raise ValueError("nice19 and idle I/O required")
    ARCHIVES.mkdir()
    record = {
        **plan, "status": "archiving", "started_at": old.now(),
        "free_bytes_before": shutil.disk_usage(ROOT).free,
    }
    old.save(RECEIPT, record)
    try:
        for row in record["runs"]:
            run = Path(row["run_root"])
            paths = [RAW / name for name in row["paths"]]
            old.guard(run, paths)
            old.check_unchanged(row)
            archive = ARCHIVES / (run.name + ".tar.zst")
            row.update(status="compressing", archive=str(archive))
            old.save(RECEIPT, record)
            print(json.dumps({"run": run.name, "status": "compressing"}), flush=True)
            subprocess.run(
                ["tar", "--create", "--sort=name", "--use-compress-program=zstd -T1 -1",
                 "--file", str(archive), "--directory", str(RAW), "--null",
                 "--verbatim-files-from", "--files-from=-"],
                input=b"".join(os.fsencode(name) + b"\0" for name in row["paths"]),
                check=True, capture_output=True,
            )
            subprocess.run(
                ["tar", "--compare", "--use-compress-program=zstd -T1",
                 "--file", str(archive), "--directory", str(RAW)],
                check=True, capture_output=True,
            )
            old.guard(run, paths)
            old.check_unchanged(row)
            row.update(
                archive_sha256=old.digest(archive), archive_bytes=archive.stat().st_size,
                archive_allocated_bytes=archive.stat().st_blocks * 512,
                full_byte_comparison_passed=True,
                restore_command=shlex.join([
                    "tar", "--extract", "--use-compress-program=zstd -T1",
                    "--file", str(archive), "--directory", str(RAW),
                ]),
            )
            old.save(RECEIPT, record)
            with (run / MARKER).open("x") as stream:
                stream.write(
                    "# Archived Historical Telemetry\n\n"
                    "Complete tar comparison passed before removing these old log copies.\n\n"
                    f"SHA256: `{row['archive_sha256']}`\n\n"
                    f"Receipt: `{RECEIPT}`\n\n```bash\n{row['restore_command']}\n```\n"
                )
            for path in paths:
                if old.snapshot(path) != row["paths"][str(path.relative_to(RAW))]:
                    raise ValueError("source changed immediately before removal")
                path.unlink()
            row.update(status="verified_and_archived", finished_at=old.now())
            old.save(RECEIPT, record)
            print(json.dumps({"run": run.name, "status": row["status"]}), flush=True)
        record.update(
            status="verified_and_archived", finished_at=old.now(),
            net_reclaimed_bytes=sum(
                row["source_allocated_bytes"] - row["archive_allocated_bytes"] for row in record["runs"]
            ),
        )
        old.save(RECEIPT, record)
    except BaseException as error:
        record.update(status="failed_preserve_remaining", error=repr(error))
        old.save(RECEIPT, record)
        raise


def review(plan):
    output = HERE / "log_review.json"
    if output.exists():
        raise FileExistsError("preserve the original independent log review")
    record = json.loads(RECEIPT.read_bytes())
    if record["status"] != "verified_and_archived" or len(plan["runs"]) != len(record["runs"]):
        raise ValueError("log archive incomplete")
    rows = []
    for expected, row in zip(plan["runs"], record["runs"], strict=True):
        archive = Path(row["archive"])
        if (
            row["paths"] != expected["paths"] or row["status"] != "verified_and_archived"
            or not row["full_byte_comparison_passed"] or old.digest(archive) != row["archive_sha256"]
            or archive.stat().st_size != row["archive_bytes"]
            or any((RAW / name).exists() for name in row["paths"])
            or any(old.digest(RAW / name) != value for name, value in
                   expected["preserved_metadata_sha256"].items())
        ):
            raise ValueError("original metadata, archived payload or retired path differs")
        members = subprocess.check_output(
            ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(archive)], text=True,
        ).splitlines()
        marker = (Path(row["run_root"]) / MARKER).read_text()
        if (
            len(members) != len(row["paths"]) or set(members) != set(row["paths"])
            or row["restore_command"] not in marker or row["archive_sha256"] not in marker
        ):
            raise ValueError("archive member list or restoration marker differs")
        rows.append({
            "run_root": row["run_root"], "files": len(row["paths"]), "archive": row["archive"],
            "archive_sha256": row["archive_sha256"], "restore_command": row["restore_command"],
            "source_allocated_bytes": row["source_allocated_bytes"],
            "archive_allocated_bytes": archive.stat().st_blocks * 512,
        })
    report = {
        "status": "independently_verified", "observed_at": old.now(),
        "runs": rows, "archived_runs": len(rows), "archived_files": sum(row["files"] for row in rows),
        "net_reclaimed_bytes": sum(
            row["source_allocated_bytes"] - row["archive_allocated_bytes"] for row in rows
        ),
        "all_archive_sha256_members_metadata_and_restoration_markers_verified": True,
    }
    old.save(output, report)
    lines = ["# Historical Telemetry Recovery, 2026-11-17", ""]
    for row in rows:
        lines.extend([
            f"## {Path(row['run_root']).name}", "",
            f"SHA256: `{row['archive_sha256']}`", "",
            "```bash", row["restore_command"], "```", "",
        ])
    (ARCHIVES / "README.md").write_text("\n".join(lines))
    print(json.dumps({key: report[key] for key in (
        "status", "archived_runs", "archived_files", "net_reclaimed_bytes",
    )}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        inventory()
        return
    plan = json.loads(PLAN.read_bytes())
    if plan["operator_sha256"] != old.digest(Path(__file__)):
        raise ValueError("log operator changed after inventory")
    if args.action == "execute":
        execute(plan)
    else:
        review(plan)


if __name__ == "__main__":
    main()
