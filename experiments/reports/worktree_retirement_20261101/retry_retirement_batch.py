"""Retry a preserved batch after a racing Docker removal, without bypassing guards."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retirement_parallel", HERE / "parallel_retirement.py")
parallel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parallel)
base = parallel.base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--linked", action="store_true")
    args = parser.parse_args()
    prefix, prepared = parallel.setup(args.linked)
    receipt = HERE / (prefix + "parallel_workers") / f"batch_{args.batch}.json"
    record = json.loads(receipt.read_bytes())
    if (
        record["status"] != "failed_preserve_remaining_worktrees"
        or "docker" not in record.get("error", "") or "inspect" not in record["error"]
    ):
        raise ValueError("require a preserved Docker-inspection failure")
    checkpoint = receipt.with_name(receipt.stem + "_original_failure.json")
    if checkpoint.exists():
        raise FileExistsError("preserve original failed-batch evidence")
    shutil.copy2(receipt, checkpoint)
    state = {
        "operator": str(Path(__file__)), "sha256": base.digest(Path(__file__)),
        "failure_checkpoint": str(checkpoint), "failure_sha256": base.digest(checkpoint),
        "retries": 0, "policy": "Retry a fresh complete Docker mount snapshot, at most three times.",
    }
    original_mounts = base.helper.helper.mounted_sources

    def fresh_mounts():
        for attempt in range(3):
            try:
                return original_mounts()
            except subprocess.CalledProcessError as error:
                if error.cmd[:2] != ["docker", "inspect"] or attempt == 2:
                    raise
                state["retries"] += 1
        raise AssertionError("unreachable")

    base.helper.helper.mounted_sources = fresh_mounts
    base.RECEIPT = receipt
    record["guard_retry"] = state
    record.pop("error")
    record.pop("failed_at")
    preparation = json.loads(prepared.read_bytes())
    if preparation["operator_sha256"] != base.digest(HERE / "parallel_retirement.py"):
        raise ValueError("parallel operator changed")
    parallel.continuation.continue_execution(record, preparation["enumeration_proof"])


if __name__ == "__main__":
    main()
