"""Retire old detached review checkouts stored directly under experiment."""

import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DETAIL = HERE / "direct_checkouts"
spec = importlib.util.spec_from_file_location("current_storage_operator", HERE / "cleanup.py")
current = importlib.util.module_from_spec(spec)
spec.loader.exec_module(current)
trees, base = current.trees, current.base
trees.HERE, base.HERE = DETAIL, DETAIL
base.PLAN, base.RECEIPT = DETAIL / "worktree_inventory.json", DETAIL / "worktree_receipt.json"
base.ARCHIVES = current.ROOT / "experiments/archive/20261121_retired_direct_checkouts"


def eligible(row):
    path = Path(row["worktree"])
    return (
        path != current.ROOT and path.parent == current.ROOT.parent
        and path.name.startswith("beliefkv-") and "detached" in row
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and (path / ".git").is_file() and not (path / ".git").is_symlink()
        and (path / ".git").stat().st_mtime < base.CUTOFF
    )


base.eligible = trees.previous.original_eligible = eligible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "prepare", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        if base.PLAN.exists():
            raise FileExistsError("preserve the original direct-checkout inventory")
        DETAIL.mkdir(exist_ok=True)
        plan = base.inventory()
        for row in plan["worktrees"]:
            row["archive_ref"] = row["archive_ref"].replace("20261101-worktrees", "20261121-direct")
        plan.update(
            wrapper_sha256=base.digest(current.PREVIOUS / "retire_worktrees.py"),
            extension_sha256=base.digest(Path(__file__)),
            current_operator_sha256=base.digest(HERE / "cleanup.py"),
            selection_cutoff="2026-11-18T00:00:00+08:00",
        )
        base.save(base.PLAN, plan)
        print(json.dumps({
            "selected": len(plan["worktrees"]), "skipped": len(plan["skipped"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
        }), flush=True)
        return
    plan = json.loads(base.PLAN.read_bytes())
    if (
        plan["extension_sha256"] != base.digest(Path(__file__))
        or plan["current_operator_sha256"] != base.digest(HERE / "cleanup.py")
    ):
        raise ValueError("direct-checkout operators changed after selection")
    if args.action == "prepare":
        trees.prepare(plan)
    elif args.action == "execute":
        trees.worker(0)
    else:
        trees.review()
        index = base.ARCHIVES / "README.md"
        index.write_text(index.read_text().replace("2026-11-17", "2026-11-21"))


if __name__ == "__main__":
    main()
