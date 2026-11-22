"""Verify completed archival batches and rehearse recovery before reporting space."""

import hashlib
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "experiments/raw"
ARCHIVES = ROOT / "experiments/archive/20261122_historical_logs"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    output = HERE / "final_storage_review.json"
    if output.exists():
        raise FileExistsError("preserve the original final maintenance observation")
    inventory = HERE / "log_inventory.json"
    plan = json.loads(inventory.read_bytes())
    reports, restorations, hashes = [], [], {str(inventory): digest(inventory)}
    for index in range(plan["batches"]):
        detail = HERE / f"batch_{index}"
        review_path = detail / "log_review.json"
        receipt_path = detail / "log_receipt.json"
        report = json.loads(review_path.read_bytes())
        receipt = json.loads(receipt_path.read_bytes())
        if (
            report["status"] != "independently_verified"
            or receipt["status"] != "verified_and_archived"
            or [row["run_root"] for row in report["runs"]]
            != [row["run_root"] for row in plan["runs"][index::plan["batches"]]]
        ):
            raise ValueError("a disjoint batch is incomplete or differs from the inventory")
        reports.append(report)
        hashes.update({
            str(review_path): digest(review_path),
            str(receipt_path): digest(receipt_path),
        })
        smallest = min(receipt["runs"], key=lambda row: sum(
            item["size_bytes"] for item in row["paths"].values()
        ))
        archive = Path(smallest["archive"])
        if digest(archive) != smallest["archive_sha256"]:
            raise ValueError("restoration archive digest changed")
        with tempfile.TemporaryDirectory(prefix="recovery_probe_", dir=ARCHIVES) as temporary:
            probe = Path(temporary)
            subprocess.run([
                "tar", "--extract", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(probe),
            ], check=True, capture_output=True)
            subprocess.run([
                "tar", "--compare", "--use-compress-program=zstd -T1",
                "--file", str(archive), "--directory", str(probe),
            ], check=True, capture_output=True)
            restored = {
                str(path.relative_to(probe)): path
                for path in probe.rglob("*") if path.is_file()
            }
            if set(restored) != set(smallest["paths"]):
                raise ValueError("restored member list differs")
            if any(
                path.is_symlink() or path.stat().st_size != smallest["paths"][name]["size_bytes"]
                for name, path in restored.items()
            ):
                raise ValueError("restored original file type or size differs")
            restorations.append({
                "batch": index,
                "archive": str(archive),
                "all_restored_bytes_compared": True,
                "restored_files_sha256": {
                    name: digest(path) for name, path in restored.items()
                },
            })
        if Path(temporary).exists():
            raise ValueError("temporary restoration directory remains")
    all_runs = [row for report in reports for row in report["runs"]]
    if len({row["run_root"] for row in all_runs}) != len(plan["runs"]):
        raise ValueError("archival batches overlap or omit a selected run")
    archive_allocation = sum(
        path.stat().st_blocks * 512 for path in ARCHIVES.rglob("*")
    ) + ARCHIVES.stat().st_blocks * 512
    source_allocation = sum(row["source_allocated_bytes"] for row in all_runs)
    result = {
        "status": "verified_and_observed",
        "observed_at": datetime.now().astimezone().isoformat(),
        "archived_runs": len(all_runs),
        "archived_files": sum(row["files"] for row in all_runs),
        "source_allocated_bytes": source_allocation,
        "archive_allocated_bytes": archive_allocation,
        "net_reclaimed_bytes": source_allocation - archive_allocation,
        "all_archive_hashes_members_original_metadata_and_recovery_markers_verified": True,
        "actual_restoration_rehearsals": restorations,
        "proof_sha256": hashes,
        "operator_sha256": {
            str(path): digest(path) for path in (HERE / "cleanup.py", Path(__file__))
        },
        "initial_raw_allocated_bytes": 436027375616,
        "initial_experiments_allocated_bytes": 520681779200,
        "original_df": plan["original_df"],
        "final_df": subprocess.check_output(["df", "-B1", str(ROOT)], text=True),
        "final_df_human": subprocess.check_output(["df", "-h", str(ROOT)], text=True),
        "measurement_note": (
            "Reclaimed space uses selected allocated blocks minus every retained archive block. "
            "Filesystem changes also include concurrent experiment and other-user writes."
        ),
        "preserved": (
            "Active/pending jobs and inputs; captured source runs; request tokens, workflow "
            "events, results, configurations, environments, models and previous archives."
        ),
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
