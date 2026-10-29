"""Independently check completed archives and write their restore index."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261029_unused_logs"


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
        raise FileExistsError("preserve the previous archive review")
    plan = json.loads((HERE / "inventory.json").read_bytes())
    receipt = json.loads((HERE / "receipt.json").read_bytes())
    if (
        receipt["status"] != "verified_and_archived"
        or len(plan["runs"]) != len(receipt["runs"])
        or plan["operator_sha256"] != digest(HERE / "archive_old_logs.py")
    ):
        raise ValueError("archiving has not completed from the selected operator")
    rows = []
    for expected, row in zip(plan["runs"], receipt["runs"]):
        archive = Path(row["archive"])
        if (
            row["status"] != "verified_and_archived"
            or row["run_root"] != expected["run_root"]
            or row["paths"] != expected["paths"]
            or row["source_allocated_bytes"] != expected["source_allocated_bytes"]
            or not row["full_tar_comparison_passed"]
            or not archive.is_file() or archive.is_symlink()
            or archive.stat().st_size != row["archive_bytes"]
            or digest(archive) != row["archive_sha256"]
            or any((ROOT / "experiments/raw" / name).exists() for name in row["paths"])
            or row["remaining_archived_raw_files"] != 0
            or digest(Path(row["run_root"]) / "ab_plan.json") != row["plan_sha256"]
        ):
            raise ValueError("archive, preserved plan or removed-source inventory differs")
        marker = Path(row["run_root"]) / "OLD_LOGS_ARCHIVED_20261029.md"
        text = marker.read_text()
        if row["restore_command"] not in text or row["archive_sha256"] not in text:
            raise ValueError("per-run restoration marker is missing")
        rows.append({
            "run": Path(row["run_root"]).name, "archived_files": len(row["paths"]),
            "archive": str(archive), "archive_sha256": row["archive_sha256"],
            "source_allocated_bytes": row["source_allocated_bytes"],
            "archive_bytes": archive.stat().st_size,
            "archive_allocated_bytes": archive.stat().st_blocks * 512,
            "restore_command": row["restore_command"], "marker_sha256": digest(marker),
        })
    review = {
        "status": "independently_verified", "observed_at": datetime.now(
            ZoneInfo("Asia/Shanghai"),
        ).isoformat(),
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
        "all_sources_absent_only_after_full_byte_comparison": True,
        "all_restore_markers_verified": True,
        "inputs_sha256": {
            str(path): digest(path) for path in (
                HERE / "inventory.json", HERE / "receipt.json",
                HERE / "archive_old_logs.py", Path(__file__),
            )
        },
        "preserved": receipt["preserved"],
        "scope": "Archive verification only; no new GPU experiment or performance conclusion.",
    }
    review["allocation_snapshot_difference_bytes"] = (
        review["net_reclaimed_bytes"] - receipt["net_reclaimed_bytes"]
    )
    review["allocation_accounting_scope"] = (
        "Filesystem allocated-block observations at two times are retained separately. "
        "Payload lengths, full tar content comparisons and all SHA256 checks remain exact."
    )
    lines = [
        "# Superseded Experiment Logs, 2026-10-29", "",
        f"Verified {review['archived_files']} log files from {len(rows)} old Qwen3.5 runs.",
        "Complete tar byte comparisons passed before raw copies were removed.",
        "Plans, summaries, workflow events, request token bundles and workspaces remain.",
        "", f"Receipt: `{HERE / 'receipt.json'}`",
        f"Independent verification: `{output}`", "",
        "| Run | Source GB | Archive GB | SHA256 |",
        "| --- | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {row['run']} | {row['source_allocated_bytes'] / 1e9:.6f} "
        f"| {row['archive_bytes'] / 1e9:.6f} | `{row['archive_sha256']}` |"
        for row in rows
    )
    lines += ["", "## Restore", "",
              "Run the command for the needed experiment before rerunning its old raw-log audit.", ""]
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
