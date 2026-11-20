"""Retire unused clean branch checkouts while preserving branch references and history."""

import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DETAIL = HERE / "branch_checkouts"
spec = importlib.util.spec_from_file_location("current_storage_operator", HERE / "cleanup.py")
current = importlib.util.module_from_spec(spec)
spec.loader.exec_module(current)
trees, base = current.trees, current.base
trees.HERE, base.HERE = DETAIL, DETAIL
base.PLAN = DETAIL / "worktree_inventory.json"
base.RECEIPT = DETAIL / "worktree_receipt.json"
base.ARCHIVES = current.ROOT / "experiments/archive/20261121_retired_branch_checkouts"


def eligible(row):
    path = Path(row["worktree"])
    return (
        path != current.ROOT and path.is_relative_to(current.ROOT.parent)
        and path.name.startswith("beliefkv-") and "branch" in row
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and (path / ".git").is_file() and not (path / ".git").is_symlink()
        and (path / ".git").stat().st_mtime < base.CUTOFF
    )


base.eligible = trees.previous.original_eligible = eligible


def inventory():
    if base.PLAN.exists():
        raise FileExistsError("preserve original branch-checkout selection")
    DETAIL.mkdir(exist_ok=True)
    registry = {row["worktree"]: row for row in base.worktrees()}
    plan = base.inventory()
    for row in plan["worktrees"]:
        row["archive_ref"] = row["archive_ref"].replace("20261101-worktrees", "20261121-branches")
        row["original_branch"] = registry[row["path"]]["branch"]
    plan.update(
        wrapper_sha256=base.digest(current.PREVIOUS / "retire_worktrees.py"),
        extension_sha256=base.digest(Path(__file__)),
        current_operator_sha256=base.digest(HERE / "cleanup.py"),
        selection_cutoff="2026-11-18T00:00:00+08:00",
        policy=[
            "Only old, clean, unreferenced, inactive and unlocked branch checkouts.",
            "Leave every original branch reference and commit intact.",
            "Archive original administration and ignored files, including links.",
            "Restore exact original tracked content through the pinned archive reference.",
        ],
    )
    base.save(base.PLAN, plan)
    print(json.dumps({
        "selected": len(plan["worktrees"]), "skipped": len(plan["skipped"]),
        "source_allocated_bytes": plan["source_allocated_bytes"],
    }), flush=True)


def review():
    record = json.loads(base.RECEIPT.read_bytes())
    for row in record["worktrees"]:
        commit = base.git(
            current.ROOT, "rev-parse", "--verify", row["original_branch"],
        ).stdout.decode().strip()
        if commit != row["commit"]:
            raise ValueError("an original branch changed; preserve evidence for review")
    trees.review()
    report_path = DETAIL / "worktree_review.json"
    report = json.loads(report_path.read_bytes())
    report.update(
        all_original_branches_preserved_at_original_commits=True,
        original_branches={row["path"]: row["original_branch"] for row in record["worktrees"]},
    )
    base.save(report_path, report)
    index = base.ARCHIVES / "README.md"
    index.write_text(
        index.read_text().replace("2026-11-17", "2026-11-21")
        + "\nOriginal named branches remain intact; the listed detached restore commands "
        "recreate the exact archived commit even if a branch is advanced later.\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "prepare", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        inventory()
        return
    plan = json.loads(base.PLAN.read_bytes())
    if (
        plan["extension_sha256"] != base.digest(Path(__file__))
        or plan["current_operator_sha256"] != base.digest(HERE / "cleanup.py")
    ):
        raise ValueError("branch-checkout operators changed after selection")
    if args.action == "prepare":
        trees.prepare(plan)
    elif args.action == "execute":
        trees.worker(0)
    else:
        review()


if __name__ == "__main__":
    main()
