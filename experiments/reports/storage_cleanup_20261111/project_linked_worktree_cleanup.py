"""Retire the separately inventoried old checkouts containing ignored symlinks."""

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name("project_worktree_cleanup.py")
spec = importlib.util.spec_from_file_location("project_retirement", SOURCE)
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)
base = project.base
primary_plan = project.HERE / "inventory.json"
allowed = {
    row["path"] for row in json.loads(primary_plan.read_bytes())["skipped"]
    if row["reason"] == "preserve non-private or nonregular ignored data"
}
HELPER = ROOT / "experiments/reports/worktree_retirement_20261101/continue_retirement.py"
spec = importlib.util.spec_from_file_location("verified_link_retirement", HELPER)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)
original_eligible = base.eligible
base.eligible = lambda row: row["worktree"] in allowed and original_eligible(row)
base.ignored_files = links.linked_ignored_files
base.snapshot = links.linked_snapshot
HERE = Path(__file__).resolve().parent / "project_linked_worktrees"
project.previous.HERE = HERE
base.HERE = HERE
base.PLAN = HERE / "inventory.json"
base.RECEIPT = HERE / "receipt.json"
base.ARCHIVES = ROOT / "experiments/archive/20261111_project_linked_worktrees"


def check_operator(plan):
    if (
        plan["linked_followup_operator_sha256"] != base.digest(Path(__file__))
        or plan["project_operator_sha256"] != base.digest(SOURCE)
        or plan["linked_helper_sha256"] != base.digest(HELPER)
        or plan["wrapper_sha256"] != base.digest(project.SOURCE)
        or plan["primary_plan_sha256"] != base.digest(primary_plan)
    ):
        raise ValueError("linked retirement operators or original selection changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        if base.PLAN.exists():
            raise FileExistsError("preserve original linked selection")
        HERE.mkdir()
        plan = base.inventory()
        plan.update(
            wrapper_sha256=base.digest(project.SOURCE),
            linked_followup_operator_sha256=base.digest(Path(__file__)),
            project_operator_sha256=base.digest(SOURCE),
            linked_helper_sha256=base.digest(HELPER),
            primary_plan_sha256=base.digest(primary_plan),
            created_before="2026-11-08T00:00:00+08:00",
            symlink_policy="Archive the link itself; preserve every external target.",
            reference_policy="Same code, configuration and runtime-input guards as primary follow-up.",
        )
        for row in plan["worktrees"]:
            row["archive_ref"] = f"refs/archive/20261111-project-linked/{row['id']}/head"
        base.save(base.PLAN, plan)
        print(json.dumps({
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
            project.previous.verify()


if __name__ == "__main__":
    main()
