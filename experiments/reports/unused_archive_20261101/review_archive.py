"""Independently verify archived payloads, manifests and restoration markers."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RAW = ROOT / "experiments/raw"
ARCHIVES = ROOT / "experiments/archive/20261101_unused_logs"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    output = HERE / "final_review.json"
    index = ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve previous independent review")
    plan = json.loads((HERE / "inventory.json").read_bytes())
    receipt = json.loads((HERE / "receipt.json").read_bytes())
    if (
        receipt["status"] != "verified_and_archived"
        or len(plan["runs"]) != len(receipt["runs"])
        or plan["operator_sha256"] != digest(HERE / "archive_old_logs.py")
        or plan["helper_sha256"] != digest(
            HERE.parent / "unused_archive_20261029/archive_old_logs.py"
        )
    ):
        raise ValueError("incomplete archive or source identity mismatch")
    rows = []
    for expected, row in zip(plan["runs"], receipt["runs"]):
        archive = Path(row["archive"])
        run = Path(row["run_root"])
        if (
            row["status"] != "verified_and_archived"
            or row["run_root"] != expected["run_root"] or row["paths"] != expected["paths"]
            or row["source_allocated_bytes"] != expected["source_allocated_bytes"]
            or not row["full_tar_comparison_passed"]
            or not archive.is_file() or archive.is_symlink()
            or archive.stat().st_size != row["archive_bytes"]
            or digest(archive) != row["archive_sha256"]
            or any((RAW / name).exists() for name in row["paths"])
            or row["remaining_archived_raw_files"] != 0
            or digest(run / "ab_plan.json") != row["plan_sha256"]
        ):
            raise ValueError("archive bytes, original plan or selected inventory mismatch")
        members = subprocess.check_output(
            ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(archive)],
            text=True,
        ).splitlines()
        if len(members) != len(row["paths"]) or set(members) != set(row["paths"]):
            raise ValueError("archive member list differs from frozen selection")
        marker = run / "OLD_LOGS_ARCHIVED_20261101.md"
        text = marker.read_text()
        if row["restore_command"] not in text or row["archive_sha256"] not in text:
            raise ValueError("restoration marker mismatch")
        rows.append({
            "run": run.name, "archived_files": len(row["paths"]),
            "archive": str(archive), "archive_sha256": row["archive_sha256"],
            "source_allocated_bytes": row["source_allocated_bytes"],
            "archive_bytes": archive.stat().st_size,
            "archive_allocated_bytes": archive.stat().st_blocks * 512,
            "restore_command": row["restore_command"], "marker_sha256": digest(marker),
            "exact_archive_member_list_verified": True,
        })
    review = {
        "status": "independently_verified",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "runs": rows, "archived_runs": len(rows),
        "archived_files": sum(row["archived_files"] for row in rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "archive_bytes": sum(row["archive_bytes"] for row in rows),
        "net_reclaimed_bytes": sum(
            row["source_allocated_bytes"] - row["archive_allocated_bytes"] for row in rows
        ),
        "receipt_net_reclaimed_bytes": receipt["net_reclaimed_bytes"],
        "free_bytes_observed": shutil.disk_usage(ROOT).free,
        "all_original_plans_match": True, "all_archive_sha256_match": True,
        "all_archive_member_lists_match": True, "all_restore_markers_verified": True,
        "all_sources_absent_only_after_full_byte_comparison": True,
        "preserved": receipt["preserved"],
        "inputs_sha256": {
            str(path): digest(path) for path in (
                HERE / "inventory.json", HERE / "receipt.json",
                HERE / "archive_old_logs.py", Path(__file__),
            )
        },
        "scope": "Lossless CPU/IO archival, not GPU or performance qualification.",
    }
    review["allocation_snapshot_difference_bytes"] = (
        review["net_reclaimed_bytes"] - receipt["net_reclaimed_bytes"]
    )
    lines = [
        "# Superseded Experiment Logs, 2026-11-01", "",
        f"Verified {review['archived_files']} files from {len(rows)} old Qwen3.5 runs.",
        "Every raw file passed complete tar comparison before removal.",
        "Every archive SHA256 and exact member inventory passed independent review.",
        "Plans, outcomes, source/request tokens, workflow events and workspaces remain.", "",
        f"Source allocation: {review['source_allocated_bytes'] / 1e9:.3f} GB.",
        f"Compressed payload: {review['archive_bytes'] / 1e9:.3f} GB.",
        f"Observed net reclaimed: {review['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Receipt: `{HERE / 'receipt.json'}`",
        f"Independent review: `{output}`", "",
        "## Archived Runs", "",
        "| Run | Source GB | Archive GB | SHA256 |",
        "| --- | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {row['run']} | {row['source_allocated_bytes'] / 1e9:.6f} "
        f"| {row['archive_bytes'] / 1e9:.6f} | `{row['archive_sha256']}` |"
        for row in rows
    )
    lines += ["", "## Restore", "",
              "Extract the needed run before rerunning an old raw-log analysis.", ""]
    for row in rows:
        lines += [f"### {row['run']}", "", "```bash", row["restore_command"], "```", ""]
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    with output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: review[key] for key in (
        "status", "archived_runs", "archived_files", "source_allocated_bytes",
        "archive_bytes", "net_reclaimed_bytes", "free_bytes_observed",
    )}, indent=2))


if __name__ == "__main__":
    main()
