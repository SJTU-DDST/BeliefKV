"""Retire old unreferenced clean checkouts using the existing recovery protocol."""

import argparse
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent / "worktrees"
SOURCE = ROOT / "experiments/reports/worktree_retirement_20261101/retire_worktrees.py"
spec = importlib.util.spec_from_file_location("previous_worktree_retirement", SOURCE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.HERE = HERE
base.ARCHIVES = ROOT / "experiments/archive/20261111_retired_worktrees"
base.PLAN = HERE / "inventory.json"
base.RECEIPT = HERE / "receipt.json"
AGE_CUTOFF = datetime.fromisoformat("2026-11-08T00:00:00+08:00").timestamp()


def eligible(row):
    path = Path(row["worktree"])
    return (
        path != ROOT and path.is_relative_to(base.BASE / ".worktrees")
        and path.name.startswith("beliefkv-") and "detached" in row
        and not any(key in row for key in ("locked", "prunable", "bare"))
        and path.is_dir() and not path.is_symlink()
        and (path / ".git").is_file() and not (path / ".git").is_symlink()
        and (path / ".git").stat().st_mtime < AGE_CUTOFF
    )


# Preserve the older reference horizon so ancestral source inputs stay protected.
base.eligible = eligible


def verify():
    output = HERE / "verification.json"
    index = base.ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve existing verification")
    plan = json.loads(base.PLAN.read_bytes())
    receipt = json.loads(base.RECEIPT.read_bytes())
    if (
        receipt["status"] != "pinned_archived_and_retired"
        or receipt["wrapper_sha256"] != base.digest(Path(__file__))
        or not receipt["all_selected_commits_connectivity_checked"]
        or not receipt["full_checkout_restore_rehearsal"]["passed"]
    ):
        raise ValueError("incomplete retirement, source or restoration proof")
    registered = {row["worktree"] for row in base.worktrees()}
    rows = []
    for expected, row in zip(plan["worktrees"], receipt["worktrees"], strict=True):
        if (
            row["status"] != "pinned_archived_and_retired"
            or row["path"] != expected["path"]
            or row["commit"] != expected["commit"]
            or row["ignored_files"] != expected["ignored_files"]
            or Path(row["path"]).exists() or row["path"] in registered
            or base.git(ROOT, "rev-parse", row["archive_ref"] + "^{tree}").stdout.decode().strip()
            != expected["tree"]
        ):
            raise ValueError("worktree removal, original tree or inventory differs")
        for name, commit in row["pinned_refs"].items():
            if base.git(ROOT, "rev-parse", "--verify", name).stdout.decode().strip() != commit:
                raise ValueError("original commit is not pinned")
        archives = [row["admin_archive"]]
        if row["ignored_files"]:
            archives.append(row["ignored_archive"])
        for item in archives:
            path = Path(item["path"])
            if (
                not item["full_tar_comparison_passed"]
                or path.stat().st_size != item["bytes"]
                or base.digest(path) != item["sha256"]
            ):
                raise ValueError("archive integrity or prior byte comparison failed")
            names = subprocess.check_output([
                "tar", "--list", "--use-compress-program=zstd -T1",
                "--file", str(path),
            ], text=True).splitlines()
            if item is row.get("ignored_archive") and (
                len(names) != len(row["ignored_files"]) or set(names) != set(row["ignored_files"])
            ):
                raise ValueError("ignored archive inventory differs")
        rows.append({
            "path": row["path"], "commit": row["commit"],
            "archive_ref": row["archive_ref"], "restore_commands": row["restore_commands"],
        })
    allocated = sum(
        path.stat().st_blocks * 512 for path in base.ARCHIVES.rglob("*") if path.is_file()
    )
    result = {
        "status": "verified", "observed_at": base.now(), "worktrees": rows,
        "retired_worktrees": len(rows), "archive_allocated_bytes": allocated,
        "source_allocated_bytes": receipt["source_allocated_bytes"],
        "net_reclaimed_bytes": receipt["source_allocated_bytes"] - allocated,
        "all_original_commits_and_trees_verified": True,
        "all_archives_hashes_and_prior_byte_comparisons_verified": True,
        "all_retired_paths_and_registrations_absent": True,
        "complete_checkout_restoration_rehearsal": receipt["full_checkout_restore_rehearsal"],
    }
    base.save(output, result)
    lines = [
        "# Retired Unreferenced Worktrees, 2026-11-11", "",
        "Clean, inactive, unreferenced detached checkouts created before Nov 8 only.",
        "Every original HEAD and reflog commit remains pinned. Ignored files and",
        "Git administration passed complete archive comparison before Git removal.",
        "", f"Retired worktrees: {len(rows)}.",
        f"Net reclaimed: {result['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Receipt: `{base.RECEIPT}`", f"Verification: `{output}`", "",
    ]
    for row in rows:
        lines.extend([
            "## " + Path(row["path"]).name, "", "```bash",
            *row["restore_commands"], "```", "",
        ])
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    print(json.dumps({name: result[name] for name in (
        "status", "retired_worktrees", "net_reclaimed_bytes",
    )}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        if base.PLAN.exists():
            raise FileExistsError("preserve existing inventory")
        HERE.mkdir()
        plan = base.inventory()
        plan["wrapper_sha256"] = base.digest(Path(__file__))
        plan["created_before"] = "2026-11-08T00:00:00+08:00"
        for row in plan["worktrees"]:
            row["archive_ref"] = f"refs/archive/20261111-worktrees/{row['id']}/head"
        base.save(base.PLAN, plan)
        print(json.dumps({
            "registered_worktrees": plan["registered_worktrees"],
            "selected": len(plan["worktrees"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
            "skipped_reasons": dict(Counter(row["reason"] for row in plan["skipped"])),
        }, indent=2), flush=True)
    elif args.action == "execute":
        plan = json.loads(base.PLAN.read_bytes())
        if plan["wrapper_sha256"] != base.digest(Path(__file__)):
            raise ValueError("wrapper changed after inventory")
        base.execute(plan)
    else:
        verify()


if __name__ == "__main__":
    main()
