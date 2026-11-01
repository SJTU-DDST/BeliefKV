"""Independently validate Qwen3 archive bytes, metadata and recovery commands."""

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
ARCHIVES = ROOT / "experiments/archive/20261101_qwen3_unused_logs"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    output, index = HERE / "final_review.json", ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve existing independent review")
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
        raise ValueError("archive completion or operator identity mismatch")
    rows = []
    for expected, row in zip(plan["runs"], receipt["runs"]):
        run, archive = Path(row["run_root"]), Path(row["archive"])
        if (
            row["status"] != "verified_and_archived"
            or row["run_root"] != expected["run_root"] or row["paths"] != expected["paths"]
            or row["preserved_metadata_sha256"] != expected["preserved_metadata_sha256"]
            or row["source_allocated_bytes"] != expected["source_allocated_bytes"]
            or not row["full_tar_comparison_passed"]
            or not archive.is_file() or archive.is_symlink()
            or archive.stat().st_size != row["archive_bytes"]
            or digest(archive) != row["archive_sha256"]
            or any((RAW / name).exists() for name in row["paths"])
            or any(digest(RAW / name) != value for name, value in
                   row["preserved_metadata_sha256"].items())
        ):
            raise ValueError("archive bytes, metadata or source selection mismatch")
        members = subprocess.check_output(
            ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(archive)],
            text=True,
        ).splitlines()
        if len(members) != len(row["paths"]) or set(members) != set(row["paths"]):
            raise ValueError("archive member inventory mismatch")
        marker = run / "OLD_QWEN3_LOGS_ARCHIVED_20261101.md"
        text = marker.read_text()
        if row["restore_command"] not in text or row["archive_sha256"] not in text:
            raise ValueError("restoration marker mismatch")
        rows.append({
            "run": run.name, "archived_files": len(row["paths"]), "archive": str(archive),
            "archive_sha256": row["archive_sha256"],
            "archive_bytes": archive.stat().st_size,
            "archive_allocated_bytes": archive.stat().st_blocks * 512,
            "source_allocated_bytes": row["source_allocated_bytes"],
            "preserved_metadata_files": len(row["preserved_metadata_sha256"]),
            "retained_registered_logs": len(row["retained_registered_logs"]),
            "restore_command": row["restore_command"], "marker_sha256": digest(marker),
        })
    review = {
        "status": "independently_verified",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "runs": rows, "archived_runs": len(rows),
        "archived_files": sum(row["archived_files"] for row in rows),
        "preserved_metadata_files": sum(row["preserved_metadata_files"] for row in rows),
        "retained_registered_logs": sum(row["retained_registered_logs"] for row in rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "archive_bytes": sum(row["archive_bytes"] for row in rows),
        "net_reclaimed_bytes": sum(
            row["source_allocated_bytes"] - row["archive_allocated_bytes"] for row in rows
        ),
        "receipt_net_reclaimed_bytes": receipt["net_reclaimed_bytes"],
        "free_bytes_observed": shutil.disk_usage(ROOT).free,
        "all_original_metadata_sha256_match": True, "all_archive_sha256_match": True,
        "all_archive_member_lists_match": True, "all_restore_markers_verified": True,
        "all_sources_absent_only_after_full_byte_comparison": True,
        "preserved": receipt["preserved"], "skipped": plan["skipped"],
        "inputs_sha256": {
            str(path): digest(path) for path in (
                HERE / "inventory.json", HERE / "receipt.json",
                HERE / "archive_old_logs.py", Path(__file__),
            )
        },
        "scope": "CPU/IO archival only; no GPU or performance qualification.",
    }
    review["allocation_snapshot_difference_bytes"] = (
        review["net_reclaimed_bytes"] - receipt["net_reclaimed_bytes"]
    )
    lines = [
        "# Historical Qwen3-Coder Logs, 2026-11-01", "",
        f"Verified {review['archived_files']} files from {len(rows)} inactive runs.",
        "Complete tar byte comparisons passed before raw log removal.",
        "Independent SHA256, exact member lists, metadata and retained input hashes all pass.",
        "Source captures, tokens, workflow events, outcomes and workspaces remain.", "",
        f"Source allocation: {review['source_allocated_bytes'] / 1e9:.3f} GB.",
        f"Archive payload: {review['archive_bytes'] / 1e9:.3f} GB.",
        f"Net reclaimed: {review['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Receipt: `{HERE / 'receipt.json'}`",
        f"Independent review: `{output}`", "",
        "## Archived Runs", "",
        "| Run | Files | Source GB | Archive GB |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row['run']} | {row['archived_files']} "
        f"| {row['source_allocated_bytes'] / 1e9:.6f} | {row['archive_bytes'] / 1e9:.6f} |"
        for row in rows
    )
    lines += ["", "## Restore", "",
              "Extract the required experiment before rerunning its historical raw-log analysis.", ""]
    for row in rows:
        lines += [
            f"### {row['run']}", "", f"SHA256: `{row['archive_sha256']}`", "",
            "```bash", row["restore_command"], "```", "",
        ]
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    with output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: review[key] for key in (
        "status", "archived_runs", "archived_files", "preserved_metadata_files",
        "retained_registered_logs",
        "source_allocated_bytes", "archive_bytes", "net_reclaimed_bytes", "free_bytes_observed",
    )}, indent=2))


if __name__ == "__main__":
    main()
