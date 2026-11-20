"""Record final home allocation and independently verified maintenance totals."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HOME_ROOT = Path("/home/longhao")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    output = HERE / "final_storage_review.json"
    if output.exists():
        raise FileExistsError("preserve the original final storage observation")
    proofs = [
        HERE / "worktree_review.json",
        HERE / "branch_checkouts/worktree_review.json",
        HERE / "direct_checkouts/worktree_review.json",
        HERE / "log_review.json",
        HERE / "stage_logs/log_review.json",
    ]
    reports = [json.loads(path.read_bytes()) for path in proofs]
    if any(report["status"] != "independently_verified" for report in reports):
        raise ValueError("finish every maintenance batch before the final observation")
    restore = HERE / "restoration_review.json"
    if json.loads(restore.read_bytes())["status"] != "verified_restoration":
        raise ValueError("complete an actual restoration rehearsal")
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    wanted = {
        str(HOME_ROOT), str(ROOT.parent), str(ROOT), str(ROOT.parent / ".worktrees"),
        str(ROOT / "experiments/raw"), str(ROOT / "experiments/reports"),
        str(ROOT / "experiments/archive"),
    }
    result = subprocess.run(
        ["nice", "-n", "19", "ionice", "-c", "3", "du", "-x", "-B1", "--max-depth=4",
         str(HOME_ROOT)], check=True, capture_output=True, text=True,
    )
    directories = {}
    for line in result.stdout.splitlines():
        size, path = line.split("\t", 1)
        if path in wanted:
            directories[path] = int(size)
    report = {
        "status": "verified_and_observed",
        "started_at": started,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "retired_checkouts": sum(item.get("retired_worktrees", 0) for item in reports),
        "archived_log_runs": sum(item.get("archived_runs", 0) for item in reports),
        "archived_log_files": sum(item.get("archived_files", 0) for item in reports),
        "net_reclaimed_bytes": sum(item["net_reclaimed_bytes"] for item in reports),
        "directory_allocated_bytes": directories,
        "df_after_bytes": subprocess.check_output(["df", "-B1", str(ROOT)], text=True),
        "df_after_human": subprocess.check_output(["df", "-h", str(ROOT)], text=True),
        "initial_observations": {
            "df_available_bytes": 111451389952,
            "df_used_bytes": 3474740764672,
            "worktrees_allocated_bytes": 319729000448,
            "beliefkv_allocated_bytes": 547972624384,
            "raw_allocated_bytes": 477153005568,
            "experiment_allocated_bytes": 912465068032,
            "home_allocated_bytes": 952018587648,
        },
        "proof_sha256": {str(path): digest(path) for path in proofs + [restore]},
        "allocation_scope": "Allocated blocks by directory, without following links or crossing mounts.",
        "measurement_note": "Directory and df changes include concurrent experiment and other-user writes.",
        "scope": "Storage maintenance; no GPU launch, job stop, serving policy or other-user cleanup.",
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
