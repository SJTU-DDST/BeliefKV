"""Verify every archived report's bytes and write its restoration index."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REPORTS = ROOT / "experiments/reports"
ARCHIVES = ROOT / "experiments/archive/20261101_old_reports"


def digest_stream(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def digest(path):
    with path.open("rb") as stream:
        return digest_stream(stream)


def main():
    output, index = HERE / "final_review.json", ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve existing independent report review")
    plan = json.loads((HERE / "inventory.json").read_bytes())
    receipt = json.loads((HERE / "receipt.json").read_bytes())
    if (
        receipt["status"] != "verified_and_archived"
        or len(plan["groups"]) != len(receipt["groups"])
        or plan["operator_sha256"] != digest(HERE / "archive_reports.py")
    ):
        raise ValueError("archive completion or operator identity mismatch")
    groups = []
    for expected, row in zip(plan["groups"], receipt["groups"]):
        archive = Path(row["archive"])
        if (
            row["status"] != "verified_and_archived" or row["day"] != expected["day"]
            or row["paths"] != expected["paths"] or not row["full_tar_comparison_passed"]
            or not archive.is_file() or archive.is_symlink()
            or archive.stat().st_size != row["archive_bytes"]
            or digest(archive) != row["archive_sha256"]
            or any((REPORTS / name).exists() for name in row["paths"])
        ):
            raise ValueError("archive identity or selected report inventory mismatch")
        members = []
        process = subprocess.Popen(
            ["zstd", "-T1", "--decompress", "--stdout", str(archive)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as stream:
                for member in stream:
                    if not member.isfile() or member.name not in row["paths"]:
                        raise ValueError("unexpected archive member")
                    source = row["paths"][member.name]
                    if member.size != source["size_bytes"]:
                        raise ValueError("report payload length differs")
                    with stream.extractfile(member) as payload:
                        if digest_stream(payload) != source["sha256"]:
                            raise ValueError("archived report payload SHA256 differs")
                    members.append(member.name)
            # Drain padding beyond the tar end before checking the decompressor.
            while process.stdout.read(8 * 1024 * 1024):
                pass
            error = process.stderr.read()
            if process.wait() != 0:
                raise ValueError("decompression failed: " + repr(error))
        finally:
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        if len(members) != len(row["paths"]) or set(members) != set(row["paths"]):
            raise ValueError("archive has missing or duplicate reports")
        groups.append({
            "day": row["day"], "files": len(members), "archive": str(archive),
            "archive_sha256": row["archive_sha256"],
            "archive_bytes": archive.stat().st_size,
            "archive_allocated_bytes": archive.stat().st_blocks * 512,
            "source_allocated_bytes": row["source_allocated_bytes"],
            "restore_command": row["restore_command"],
            "every_archived_report_payload_sha256_matches": True,
        })
    for row in plan["retained_referenced_reports"]:
        path = REPORTS / row["path"]
        if not path.is_file() or path.stat().st_size != row["size_bytes"]:
            raise ValueError("referenced historical report is missing or changed in size")
    marker = REPORTS / "OLD_REPORTS_ARCHIVED_20261101.md"
    marker_text = marker.read_text()
    if str(HERE / "inventory.json") not in marker_text or str(index) not in marker_text:
        raise ValueError("report directory recovery marker is missing or incorrect")
    review = {
        "status": "independently_verified",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "groups": groups, "archived_files": sum(row["files"] for row in groups),
        "archive_bundles": len(groups),
        "retained_referenced_reports": len(plan["retained_referenced_reports"]),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in groups),
        "archive_bytes": sum(row["archive_bytes"] for row in groups),
        "net_reclaimed_bytes": sum(
            row["source_allocated_bytes"] - row["archive_allocated_bytes"] for row in groups
        ),
        "receipt_net_reclaimed_bytes": receipt["net_reclaimed_bytes"],
        "free_bytes_observed": shutil.disk_usage(ROOT).free,
        "all_archive_sha256_match": True, "all_report_payload_sha256_match": True,
        "all_member_lists_match": True, "all_retained_referenced_reports_present": True,
        "all_retained_referenced_report_sizes_match": True,
        "recovery_marker_sha256": digest(marker),
        "all_sources_removed_only_after_complete_tar_comparison": True,
        "preserved": plan["preserved"],
        "inputs_sha256": {
            str(path): digest(path) for path in (
                HERE / "inventory.json", HERE / "receipt.json",
                HERE / "archive_reports.py", Path(__file__),
            )
        },
        "scope": "Report archival only; no experimental performance claim.",
    }
    review["allocation_snapshot_difference_bytes"] = (
        review["net_reclaimed_bytes"] - receipt["net_reclaimed_bytes"]
    )
    lines = [
        "# Historical Derived Reports, 2026-11-01", "",
        f"Archived {review['archived_files']} old report files in {len(groups)} dated bundles.",
        f"Retained {review['retained_referenced_reports']} referenced candidate reports in place.",
        "All original files passed complete tar comparison before removal.",
        "Every report payload SHA256 was independently checked from its compressed archive.", "",
        f"Source allocation: {review['source_allocated_bytes'] / 1e9:.3f} GB.",
        f"Archive payload: {review['archive_bytes'] / 1e9:.3f} GB.",
        f"Net reclaimed: {review['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Exact filenames and original SHA256: `{HERE / 'inventory.json'}`",
        f"Receipt: `{HERE / 'receipt.json'}`",
        f"Independent review: `{output}`", "",
        "## Bundles", "",
        "| Original date | Reports | Source GB | Archive GB |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row['day']} | {row['files']} | {row['source_allocated_bytes'] / 1e9:.6f} "
        f"| {row['archive_bytes'] / 1e9:.6f} |" for row in groups
    )
    lines += ["", "## Restore", "",
              "Restore the matching date before rerunning a historical report analysis.", ""]
    for row in groups:
        lines += [f"### {row['day']}", "", f"SHA256: `{row['archive_sha256']}`", "",
                  "```bash", row["restore_command"], "```", ""]
    lines += ["## Exact Report Mapping", "",
              "| Report | Dated bundle |", "| --- | --- |"]
    for row in receipt["groups"]:
        lines.extend(f"| `{name}` | `{row['day']}_reports.tar.zst` |" for name in row["paths"])
    lines.append("")
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    with output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: review[key] for key in (
        "status", "archived_files", "archive_bundles", "retained_referenced_reports",
        "source_allocated_bytes", "archive_bytes", "net_reclaimed_bytes", "free_bytes_observed",
    )}, indent=2))


if __name__ == "__main__":
    main()
