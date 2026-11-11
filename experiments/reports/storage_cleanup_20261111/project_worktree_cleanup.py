"""Recover inactive checkouts referenced only by historical documentation."""

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name("retire_worktrees.py")
spec = importlib.util.spec_from_file_location("verified_retirement_wrapper", SOURCE)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
base = previous.base
HERE = Path(__file__).resolve().parent / "project_worktrees"
previous.HERE = HERE
base.HERE = HERE
base.PLAN = HERE / "inventory.json"
base.RECEIPT = HERE / "receipt.json"
base.ARCHIVES = ROOT / "experiments/archive/20261111_project_worktrees"
original_reference_files = base.reference_files


def reference_files():
    return [
        path for path in original_reference_files()
        if not path.is_relative_to(ROOT / "docs") and path != ROOT / "AGENTS.md"
    ]


def eligible(row):
    path = Path(row["worktree"])
    in_scope = path.is_relative_to(base.BASE / ".worktrees") or path.parent == base.BASE
    return (
        path != ROOT and in_scope and path.name.startswith("beliefkv-")
        and "detached" in row
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and (path / ".git").is_file() and not (path / ".git").is_symlink()
        and (path / ".git").stat().st_mtime < previous.AGE_CUTOFF
    )


base.reference_files = reference_files
base.eligible = eligible


def check_operator(plan):
    if (
        plan["followup_operator_sha256"] != base.digest(Path(__file__))
        or plan["wrapper_sha256"] != base.digest(SOURCE)
    ):
        raise ValueError("retirement operators changed after selection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        if base.PLAN.exists():
            raise FileExistsError("preserve original selection")
        HERE.mkdir()
        plan = base.inventory()
        plan.update(
            wrapper_sha256=base.digest(SOURCE),
            followup_operator_sha256=base.digest(Path(__file__)),
            created_before="2026-11-08T00:00:00+08:00",
            reference_policy={
                "code_tests_configs_recent_operational_and_input_references_protected": True,
                "original_reference_horizon_preserved": True,
                "historical_document_mentions_allow_recoverable_retirement": True,
                "main_and_branch_attached_dirty_recent_live_and_locked_trees_protected": True,
                "scope": "Detached checkouts under .worktrees or directly under experiment.",
            },
        )
        plan["policy"][0] = (
            "Only old clean detached inactive checkouts with no code, test, "
            "configuration, recent operational or input reference."
        )
        for row in plan["worktrees"]:
            row["archive_ref"] = f"refs/archive/20261111-project-worktrees/{row['id']}/head"
        base.save(base.PLAN, plan)
        print(json.dumps({
            "registered_worktrees": plan["registered_worktrees"],
            "selected": len(plan["worktrees"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
            "skipped_reasons": dict(Counter(row["reason"] for row in plan["skipped"])),
        }, indent=2), flush=True)
    else:
        plan = json.loads(base.PLAN.read_bytes())
        check_operator(plan)
        if args.action == "execute":
            base.execute(plan)
        else:
            previous.verify()


if __name__ == "__main__":
    main()
