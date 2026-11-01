"""Independently verify retained object bytes, native Git reads and log archives."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_home_storage"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    output, index = HERE / "final_review.json", ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve previous independent review")
    plan = json.loads((HERE / "inventory.json").read_bytes())
    receipt = json.loads((HERE / "receipt.json").read_bytes())
    if (
        receipt["status"] != "verified_and_cleaned"
        or plan["operator_sha256"] != digest(HERE / "cleanup_storage.py")
    ):
        raise ValueError("cleanup did not finish from the selected source")
    groups = []
    for expected, row in zip(plan["pack_groups"], receipt["pack_groups"], strict=True):
        if (
            row["status"] != "verified_and_deduplicated" or row["copies"] != expected["copies"]
            or not row["git_verify_pack_passed"]
            or row["completed_copies"] != len(row["copies"])
        ):
            raise ValueError("deduplication inventory mismatch")
        for kind, name in row["canonical"].items():
            canonical = Path(name)
            if digest(canonical) != row["sha256"][kind]:
                raise ValueError("canonical object SHA256 mismatch")
            original = row["copies"][0][kind + "_before"]
            if canonical.stat().st_size != original["size_bytes"]:
                raise ValueError("canonical object payload length mismatch")
            for copy in row["copies"]:
                path = Path(copy[kind])
                info = path.stat()
                if (
                    not path.is_file() or path.is_symlink()
                    or (info.st_dev, info.st_ino) !=
                       (canonical.stat().st_dev, canonical.stat().st_ino)
                    or stat.S_IMODE(info.st_mode) != 0o444
                ):
                    raise ValueError("retained object path/inode/read-only mode mismatch")
        subprocess.run(
            ["git", "verify-pack", row["canonical"]["index"]],
            check=True, capture_output=True,
        )
        for copy in row["copies"]:
            subprocess.run(
                ["git", "--no-optional-locks", "-C", copy["workspace"],
                 "rev-parse", "--verify", "HEAD^{commit}"],
                check=True, capture_output=True,
            )
        groups.append({
            "pack_name": row["pack_name"], "copies": len(row["copies"]),
            "canonical": row["canonical"], "sha256": row["sha256"],
            "reclaimed_bytes": row["potential_reclaimed_bytes"],
            "native_git_verify_pack_passed": True, "every_retained_git_head_readable": True,
        })
    logs = []
    for expected, row in zip(plan["log_groups"], receipt["log_groups"], strict=True):
        archive = Path(row["archive"])
        if (
            row["status"] != "verified_and_archived" or row["paths"] != expected["paths"]
            or not row["full_tar_comparison_passed"]
            or digest(archive) != row["archive_sha256"]
            or any((ROOT / name).exists() for name in row["paths"])
            or any(digest(ROOT / name) != value for name, value in
                   row["preserved_metadata_sha256"].items())
        ):
            raise ValueError("old-log archive or preserved metadata mismatch")
        names = subprocess.check_output(
            ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(archive)],
            text=True,
        ).splitlines()
        if len(names) != len(row["paths"]) or set(names) != set(row["paths"]):
            raise ValueError("archive member inventory differs")
        marker = Path(row["root"]) / "OLD_SERVICE_LOGS_ARCHIVED_20261101.md"
        if row["restore_command"] not in marker.read_text():
            raise ValueError("old-log recovery marker missing")
        logs.append({
            "root": row["root"], "files": len(names), "archive": str(archive),
            "archive_bytes": archive.stat().st_size, "archive_sha256": row["archive_sha256"],
            "source_allocated_bytes": row["source_allocated_bytes"],
            "net_reclaimed_bytes": row["source_allocated_bytes"] - archive.stat().st_blocks * 512,
            "restore_command": row["restore_command"],
        })
    review = {
        "status": "independently_verified",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "pack_groups": groups, "log_groups": logs,
        "deduplicated_pack_copies": sum(row["copies"] - 1 for row in groups),
        "verified_pack_reclaimed_bytes": sum(row["reclaimed_bytes"] for row in groups),
        "archived_log_files": sum(row["files"] for row in logs),
        "log_net_reclaimed_bytes": sum(row["net_reclaimed_bytes"] for row in logs),
        "all_canonical_object_sha256_match": True, "all_retained_paths_are_read_only_hardlinks": True,
        "all_native_git_pack_checks_pass": True, "all_original_git_heads_readable": True,
        "all_log_archive_sha256_and_members_match": True, "all_small_old_metadata_hashes_match": True,
        "free_bytes_observed": shutil.disk_usage(ROOT).free,
        "inputs_sha256": {
            str(path): digest(path) for path in (
                HERE / "inventory.json", HERE / "receipt.json",
                HERE / "cleanup_storage.py", Path(__file__),
                HERE / "storage_and_cache_observations.json",
            )
        },
        "scope": "CPU/IO storage maintenance; no GPU run, source checkout removal or result claim.",
    }
    if review["verified_pack_reclaimed_bytes"] != receipt["verified_pack_reclaimed_bytes"]:
        raise ValueError("reclaimed private-inode allocation does not reconcile")
    review["verified_reclaimed_bytes_excluding_download_cache"] = (
        review["verified_pack_reclaimed_bytes"] + review["log_net_reclaimed_bytes"]
    )
    lines = [
        "# Home Storage Cleanup, 2026-11-01", "",
        "Inactive task workspaces retain every original Git pack/index path and payload.",
        "Verified duplicate immutable objects share read-only inodes with canonical archive objects.",
        "Original per-copy metadata and file modes are recorded in the frozen inventory.",
        "Native git verify-pack and every retained workspace HEAD read passed independent review.", "",
        f"Deduplicated redundant pack/index pairs: {review['deduplicated_pack_copies']}.",
        f"Verified object allocation reclaimed: {review['verified_pack_reclaimed_bytes'] / 1e9:.3f} GB.",
        f"Archived old service logs: {review['archived_log_files']}.",
        f"Log allocation reclaimed: {review['log_net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        "## Evidence", "",
        f"Selection and original metadata: `{HERE / 'inventory.json'}`",
        f"Execution receipt: `{HERE / 'receipt.json'}`",
        f"Independent verification: `{output}`", "",
        "## Shared Git Objects", "",
        "| Git object pack | Preserved copies | Reclaimed GB |",
        "| --- | ---: | ---: |",
    ]
    lines.extend(
        f"| `{row['pack_name']}` | {row['copies']} | {row['reclaimed_bytes'] / 1e9:.3f} |"
        for row in groups
    )
    lines += ["", "## Restore Archived Logs", ""]
    for row in logs:
        lines += [
            f"### {Path(row['root']).name}", "", f"Archive SHA256: `{row['archive_sha256']}`",
            "", "```bash", row["restore_command"], "```", "",
        ]
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    with output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: review[key] for key in (
        "status", "deduplicated_pack_copies", "verified_pack_reclaimed_bytes",
        "archived_log_files", "log_net_reclaimed_bytes", "free_bytes_observed",
    )}, indent=2))


if __name__ == "__main__":
    main()
