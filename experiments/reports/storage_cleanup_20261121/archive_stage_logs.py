"""Archive old stage-layout logs after hashing their existing stage metadata."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
DETAIL = HERE / "stage_logs"
spec = importlib.util.spec_from_file_location("current_storage_operator", HERE / "cleanup.py")
current = importlib.util.module_from_spec(spec)
spec.loader.exec_module(current)
logs = current.configure_logs()
logs.HERE = DETAIL
logs.ARCHIVES = current.ROOT / "experiments/archive/20261121_historical_stage_logs"
logs.PLAN, logs.RECEIPT = DETAIL / "log_inventory.json", DETAIL / "log_receipt.json"
logs.MARKER = "OLD_STAGE_LOGS_ARCHIVED_20261121.md"
initial = json.loads((HERE / "log_inventory.json").read_bytes())
allowed = {
    row["run_root"] for row in initial["skipped"]
    if row["reason"] == "no original top-level result/registration metadata"
}
original_eligible = logs.eligible
logs.eligible = logs.old.eligible_name = lambda run: str(run) in allowed and original_eligible(run)


def metadata(run):
    names = subprocess.check_output([
        "find", str(run), "-type", "d", "(",
        "-name", "workspaces", "-o", "-name", "workloads", "-o", "-name", "workflows",
        "-o", "-name", ".git", "-o", "-name", "plugin", ")", "-prune", "-o",
        "-type", "f", "-name", "*.json", "-size", "-16M", "-print0",
    ]).split(b"\0")
    files = sorted(Path(os.fsdecode(name)) for name in names if name)
    if (
        not files or not any(path.name == "server_info.json" for path in files)
        or not any(path.name == "launch_configuration.json" for path in files)
        or any(path.is_symlink() or not path.is_file() for path in files)
    ):
        raise ValueError("preserve run lacking original server identity and launch metadata")
    return {str(path.relative_to(logs.RAW)): logs.old.digest(path) for path in files}


logs.old.preserved_metadata = metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        DETAIL.mkdir(exist_ok=True)
        logs.inventory()
        plan = json.loads(logs.PLAN.read_bytes())
        plan.update(
            extension_sha256=current.base.digest(Path(__file__)),
            current_operator_sha256=current.base.digest(HERE / "cleanup.py"),
            metadata_policy="Hash existing small stage JSON files; require server and launch identity.",
        )
        current.base.save(logs.PLAN, plan)
        return
    plan = json.loads(logs.PLAN.read_bytes())
    if (
        plan["extension_sha256"] != current.base.digest(Path(__file__))
        or plan["current_operator_sha256"] != current.base.digest(HERE / "cleanup.py")
    ):
        raise ValueError("stage archival operators changed after selection")
    if args.action == "execute":
        logs.execute(plan)
    else:
        logs.review(plan)
        index = logs.ARCHIVES / "README.md"
        index.write_text(index.read_text().replace("2026-11-17", "2026-11-21"))


if __name__ == "__main__":
    main()
