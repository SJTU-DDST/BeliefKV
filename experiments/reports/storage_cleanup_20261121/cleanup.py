"""Reuse verified archival guards for a fresh, low-priority maintenance batch."""

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = HERE.parent / "storage_cleanup_20261117"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_search():
    if shutil.which("rg"):
        return
    choices = sorted(Path("/home/longhao/.vscode-server/cli/servers").glob(
        "*/server/node_modules/@vscode/ripgrep*/bin/*/rg"
    ))
    if not choices:
        raise FileNotFoundError("no ripgrep binary available for the existing reference guards")
    os.environ["PATH"] = str(choices[-1].parent) + os.pathsep + os.environ.get("PATH", "")


configure_search()
trees = load("november17_retirement", PREVIOUS / "retire_worktrees.py")
trees.HERE, trees.BATCHES = HERE, 1
base = trees.base
base.HERE = HERE
base.PLAN = HERE / "worktree_inventory.json"
base.RECEIPT = HERE / "worktree_receipt.json"
base.ARCHIVES = ROOT / "experiments/archive/20261121_retired_worktrees"
base.CUTOFF = datetime.fromisoformat("2026-11-18T00:00:00+08:00").timestamp()
original_references = base.reference_files
base.reference_files = lambda: [
    path for path in original_references() if not path.is_relative_to(HERE)
]


def tree_inventory():
    if base.PLAN.exists():
        raise FileExistsError("preserve the original selection")
    plan = base.inventory()
    for row in plan["worktrees"]:
        row["archive_ref"] = row["archive_ref"].replace("20261101-worktrees", "20261121-worktrees")
    plan.update(
        wrapper_sha256=base.digest(PREVIOUS / "retire_worktrees.py"),
        extension_sha256=base.digest(Path(__file__)),
        selection_cutoff="2026-11-18T00:00:00+08:00",
        free_bytes_before=shutil.disk_usage(ROOT).free,
    )
    base.save(base.PLAN, plan)
    print(json.dumps({
        "selected": len(plan["worktrees"]), "skipped": len(plan["skipped"]),
        "source_allocated_bytes": plan["source_allocated_bytes"],
    }), flush=True)


def configure_logs():
    logs = load("november17_log_archiver", PREVIOUS / "archive_logs.py")
    logs.HERE = HERE
    logs.ARCHIVES = ROOT / "experiments/archive/20261121_historical_logs"
    logs.PLAN, logs.RECEIPT = HERE / "log_inventory.json", HERE / "log_receipt.json"
    logs.MARKER = "OLD_LOGS_ARCHIVED_20261121.md"
    logs.old.RECENT = datetime.fromisoformat("2026-11-14T00:00:00+08:00").timestamp()
    original_protection = logs.old.protection
    input_cache = {}

    def eligible(run):
        if not run.is_dir() or run.is_symlink() or "source" in run.name.lower():
            return False
        dates = re.findall(r"20\d{6}", run.name)
        return bool(dates) and max(dates) < "20261114"

    def selected(run):
        names = subprocess.check_output(
            ["find", str(run), "-type", "f", "-size", "+64M", "(", "-name", "*.jsonl",
             "-o", "-name", "*.log", ")", "-print0"],
        ).split(b"\0")
        return sorted(
            Path(os.fsdecode(name)) for name in names if name
            and Path(os.fsdecode(name)).parent.name in {"server", "opportunities"}
            and not any(
                item in {"workspaces", "workloads", ".git"}
                for item in Path(os.fsdecode(name)).relative_to(run).parts
            )
            and "request_tokens" not in Path(os.fsdecode(name)).name
        )

    def protection():
        paths, proof = original_protection()
        recent_files = trees.previous.recent_files(ROOT / "experiments/reports", ["*.json"])
        for path in recent_files:
            if (
                path.is_relative_to(HERE) or path.stat().st_mtime < base.CUTOFF
                or not re.search(r"source|input|manifest|evidence|entry|freeze|registration|plan|state",
                                 path.name)
            ):
                continue
            info = path.stat()
            signature = (info.st_ino, info.st_size, info.st_mtime_ns)
            cached = input_cache.get(path)
            if cached is None or cached[0] != signature:
                try:
                    payload = json.loads(path.read_bytes())
                except (UnicodeError, json.JSONDecodeError):
                    continue
                cached = (
                    signature, {item for item in logs.old.raw_paths(payload)
                                if item.is_file() or item.is_dir()},
                )
                input_cache[path] = cached
            paths.update(cached[1])
        return paths, proof

    logs.eligible = logs.old.eligible_name = eligible
    logs.old.selected = selected
    logs.old.protection = protection
    return logs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "tree-inventory", "tree-prepare", "tree-execute", "tree-review",
        "restore-check", "log-inventory", "log-execute", "log-review",
    ))
    args = parser.parse_args()
    if args.action.startswith("log-"):
        logs = configure_logs()
        if args.action == "log-inventory":
            logs.inventory()
            plan = json.loads(logs.PLAN.read_bytes())
            plan["extension_sha256"] = base.digest(Path(__file__))
            base.save(logs.PLAN, plan)
            return
        plan = json.loads(logs.PLAN.read_bytes())
        if plan["extension_sha256"] != base.digest(Path(__file__)):
            raise ValueError("archival extension changed after selection")
        if args.action == "log-execute":
            logs.execute(plan)
        else:
            logs.review(plan)
            index = logs.ARCHIVES / "README.md"
            index.write_text(index.read_text().replace("2026-11-17", "2026-11-21"))
        return
    if args.action == "tree-inventory":
        tree_inventory()
        return
    plan = json.loads(base.PLAN.read_bytes())
    if (
        plan["extension_sha256"] != base.digest(Path(__file__))
        or plan["wrapper_sha256"] != base.digest(PREVIOUS / "retire_worktrees.py")
    ):
        raise ValueError("retirement operators changed after selection")
    if args.action == "tree-prepare":
        trees.prepare(plan)
    elif args.action == "tree-execute":
        trees.worker(0)
    elif args.action == "tree-review":
        trees.review()
        index = base.ARCHIVES / "README.md"
        index.write_text(index.read_text().replace("2026-11-17", "2026-11-21"))
    else:
        restore = load("november17_restoration", PREVIOUS / "verify_restoration.py")
        restore.HERE, restore.base = HERE, base
        restore.main()


if __name__ == "__main__":
    main()
