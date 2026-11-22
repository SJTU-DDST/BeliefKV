"""Archive inactive historical telemetry using the existing verified operators."""

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = HERE.parent / "storage_cleanup_20261121/cleanup.py"
CUTOFF = "2026-11-20T00:00:00-08:00"
BATCHES = 2

spec = importlib.util.spec_from_file_location("november21_cleanup", PREVIOUS)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
previous.HERE = HERE
previous.base.CUTOFF = datetime.fromisoformat(CUTOFF).timestamp()
logs = previous.configure_logs()
logs.HERE = HERE
logs.ARCHIVES = ROOT / "experiments/archive/20261122_historical_logs"
logs.PLAN = HERE / "log_inventory.json"
logs.RECEIPT = HERE / "log_receipt.json"
logs.MARKER = "OLD_LOGS_ARCHIVED_20261122.md"
logs.old.RECENT = previous.base.CUTOFF
original_metadata = logs.old.preserved_metadata


def eligible(run):
    dates = re.findall(r"20\d{6}", run.name)
    return (
        run.is_dir() and not run.is_symlink() and "source" not in run.name.lower()
        and bool(dates) and max(dates) < "20261120"
    )


def existing_metadata(run):
    try:
        return original_metadata(run)
    except ValueError as error:
        if str(error) != "no original top-level result/registration metadata":
            raise
    names = subprocess.check_output([
        "find", str(run), "-type", "d", "(",
        "-name", "workspaces", "-o", "-name", "workloads", "-o", "-name", "workflows",
        "-o", "-name", ".git", "-o", "-name", "plugin", ")", "-prune", "-o",
        "-type", "f", "-name", "*.json", "-size", "-16M", "-print0",
    ]).split(b"\0")
    paths = sorted(Path(os.fsdecode(name)) for name in names if name)
    if (
        not any(path.name == "server_info.json" for path in paths)
        or not any(path.name == "launch_configuration.json" for path in paths)
        or any(path.is_symlink() or not path.is_file() for path in paths)
    ):
        raise ValueError("preserve stage without original server and launch identity")
    return {str(path.relative_to(logs.RAW)): logs.old.digest(path) for path in paths}


logs.eligible = logs.old.eligible_name = eligible
logs.old.preserved_metadata = existing_metadata


def inventory():
    if logs.PLAN.exists():
        raise FileExistsError("preserve the immutable original inventory")
    names = subprocess.check_output([
        "find", str(logs.RAW), "-type", "d", "(",
        "-name", "workspaces", "-o", "-name", "workloads", "-o", "-name", "workflows",
        "-o", "-name", ".git", "-o", "-name", "plugin", ")", "-prune", "-o",
        "-type", "f", "-size", "+64M", "(",
        "-name", "*.log", "-o", "-name", "*.jsonl", ")", "-print0",
    ]).split(b"\0")
    by_run = {}
    for name in names:
        if not name:
            continue
        path = Path(os.fsdecode(name))
        if path.parent.name in {"server", "opportunities"} and "request_tokens" not in path.name:
            run = logs.RAW / path.relative_to(logs.RAW).parts[0]
            by_run.setdefault(run, []).append(path)
    logs.old.selected = lambda run: sorted(by_run.get(run, []))
    logs.protection = logs.old.protection
    logs.inventory()
    plan = json.loads(logs.PLAN.read_bytes())
    plan.update(
        extension_sha256=logs.old.digest(Path(__file__)),
        previous_extension_sha256=logs.old.digest(PREVIOUS),
        selection_cutoff=CUTOFF,
        batches=BATCHES,
        original_df=subprocess.check_output(["df", "-B1", str(ROOT)], text=True),
    )
    logs.old.save(logs.PLAN, plan)


def bind_batch(index, plan):
    if not 0 <= index < BATCHES:
        raise ValueError("invalid archival batch")
    logs.HERE = HERE / f"batch_{index}"
    logs.RECEIPT = logs.HERE / "log_receipt.json"
    logs.ARCHIVES = ROOT / "experiments/archive/20261122_historical_logs" / f"batch_{index}"
    selected = {**plan, "runs": plan["runs"][index::BATCHES], "batch": index}
    selected["source_allocated_bytes"] = sum(
        row["source_allocated_bytes"] for row in selected["runs"]
    )
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    if args.action == "inventory":
        inventory()
        return
    plan = json.loads(logs.PLAN.read_bytes())
    if (
        plan["extension_sha256"] != logs.old.digest(Path(__file__))
        or plan["previous_extension_sha256"] != logs.old.digest(PREVIOUS)
        or plan["operator_sha256"] != logs.old.digest(Path(logs.__file__))
    ):
        raise ValueError("an archival operator changed after selection")
    if args.batch is None:
        raise ValueError("select an explicit disjoint batch")
    selected = bind_batch(args.batch, plan)
    if args.action == "execute":
        logs.HERE.mkdir()
        logs.ARCHIVES.parent.mkdir(exist_ok=True)
        logs.execute(selected)
    else:
        logs.review(selected)
        index = logs.ARCHIVES / "README.md"
        index.write_text(index.read_text().replace("2026-11-17", "2026-11-22"))


if __name__ == "__main__":
    main()
