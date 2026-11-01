"""Independently verify retired paths, pinned history and archived extra files."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ARCHIVES = ROOT / "experiments/archive/20261101_retired_worktrees"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def git(*args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(ROOT), *args], text=True,
    ).strip()


def restore_linked_sample(archives_root, row):
    probe = archives_root / "restore_probe"
    if probe.exists():
        raise FileExistsError("preserve the existing restoration probe")
    git("worktree", "add", "--quiet", "--detach", str(probe), row["archive_ref"])
    archive = row["ignored_archive"]["path"]
    subprocess.run(
        ["tar", "--extract", "--use-compress-program=zstd -T1",
         "--file", archive, "--directory", str(probe)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["tar", "--compare", "--use-compress-program=zstd -T1",
         "--file", archive, "--directory", str(probe)],
        check=True, capture_output=True,
    )
    if (
        git("-C", str(probe), "rev-parse", "HEAD") != row["commit"]
        or git("-C", str(probe), "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise ValueError("preserve the restored checkout for investigation")
    links = {name: info for name, info in row["ignored_files"].items() if info.get("kind") == "symlink"}
    for name, info in links.items():
        path = probe / name
        if not path.is_symlink() or str(path.readlink()) != info["target"]:
            raise ValueError("preserve the mismatched restored symbolic link")
    git("worktree", "remove", str(probe))
    return {
        "passed": True, "commit": row["commit"], "ignored_files": len(row["ignored_files"]),
        "symbolic_links": len(links), "complete_tar_comparison_after_extraction": True,
        "temporary_checkout_removed_without_force": not probe.exists(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linked", action="store_true")
    args = parser.parse_args()
    prefix = "linked_" if args.linked else ""
    archives_root = (
        ROOT / "experiments/archive/20261101_retired_linked_worktrees"
        if args.linked else ARCHIVES
    )
    output = HERE / (prefix + "final_review.json")
    index = archives_root / "README.md"
    plan_path, receipt_path = HERE / (prefix + "inventory.json"), HERE / (prefix + "receipt.json")
    if output.exists() or index.exists():
        raise FileExistsError("preserve existing independent retirement review")
    plan = json.loads(plan_path.read_bytes())
    receipt = json.loads(receipt_path.read_bytes())
    if (
        receipt["status"] != "pinned_archived_and_retired"
        or plan["operator_sha256"] != digest(HERE / "retire_worktrees.py")
        or not receipt["all_selected_commits_connectivity_checked"]
        or not receipt["full_checkout_restore_rehearsal"]["passed"]
    ):
        raise ValueError("retirement, commit connectivity or restoration proof is incomplete")
    if receipt.get("continuation_sha256") != digest(HERE / "continue_retirement.py"):
        raise ValueError("continuation operator differs from the execution receipt")
    if not receipt.get("enumeration_equivalence", {}).get("same_file_set"):
        raise ValueError("optimized reference enumeration equivalence is unverified")
    checkpoint = receipt.get("administrative_checkpoint")
    if checkpoint and digest(Path(checkpoint["path"])) != checkpoint["sha256"]:
        raise ValueError("original administrative interruption evidence changed")
    if args.linked and plan["extension_sha256"] != receipt["continuation_sha256"]:
        raise ValueError("symbolic-link handling differs from the selected plan")
    if receipt.get("parallel_finisher_sha256") != digest(HERE / "parallel_retirement.py"):
        raise ValueError("parallel finisher differs from the merged receipt")
    for worker in receipt.get("parallel_workers", []):
        if digest(Path(worker["path"])) != worker["sha256"]:
            raise ValueError("independent worker progress evidence changed")
        retry = json.loads(Path(worker["path"]).read_bytes()).get("guard_retry")
        if retry and (
            digest(Path(retry["operator"])) != retry["sha256"]
            or digest(Path(retry["failure_checkpoint"])) != retry["failure_sha256"]
        ):
            raise ValueError("bounded mount retry or original failed-batch evidence changed")
    sequential_checkpoint = receipt.get("sequential_checkpoint")
    if sequential_checkpoint and (
        digest(Path(sequential_checkpoint["path"])) != sequential_checkpoint["sha256"]
    ):
        raise ValueError("sequential execution checkpoint changed")
    registered = {
        line.removeprefix("worktree ") for line in git(
            "worktree", "list", "--porcelain",
        ).splitlines() if line.startswith("worktree ")
    }
    rows = []
    for expected, row in zip(plan["worktrees"], receipt["worktrees"], strict=True):
        path = Path(row["path"])
        if (
            row["status"] != "pinned_archived_and_retired"
            or row["path"] != expected["path"] or row["commit"] != expected["commit"]
            or row["ignored_files"] != expected["ignored_files"]
            or path.exists() or str(path) in registered
            or git("rev-parse", row["archive_ref"] + "^{tree}") != expected["tree"]
        ):
            raise ValueError("retired worktree path, registry, ignored inventory or Git tree differs")
        for name, commit in row["pinned_refs"].items():
            if git("rev-parse", "--verify", name) != commit:
                raise ValueError("archived HEAD or reflog commit is no longer pinned")
        archives = [row["admin_archive"]]
        if row["ignored_files"]:
            archives.append(row["ignored_archive"])
        for item in archives:
            archive = Path(item["path"])
            if (
                not item["full_tar_comparison_passed"] or not archive.is_file()
                or archive.stat().st_size != item["bytes"]
                or digest(archive) != item["sha256"]
            ):
                raise ValueError("extra-file or administration archive differs")
            names = subprocess.check_output(
                ["tar", "--list", "--use-compress-program=zstd -T1", "--file", str(archive)],
                text=True,
            ).splitlines()
            if item is row.get("ignored_archive") and (
                len(names) != len(row["ignored_files"]) or set(names) != set(row["ignored_files"])
            ):
                raise ValueError("ignored archive member inventory differs")
        rows.append({
            "path": row["path"], "commit": row["commit"], "tree": expected["tree"],
            "archive_ref": row["archive_ref"], "tracked_files": row["tracked_files"],
            "source_allocated_bytes": row["allocated_bytes"],
            "ignored_files": len(row["ignored_files"]),
            "archive_bytes": sum(item["bytes"] for item in archives),
            "restore_commands": row["restore_commands"],
            "pinned_reflog_commits": len(row["pinned_refs"]),
        })
    linked_restore = restore_linked_sample(archives_root, receipt["worktrees"][0]) if args.linked else None
    allocated = sum(path.stat().st_blocks * 512 for path in archives_root.rglob("*") if path.is_file())
    review = {
        "status": "independently_verified",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "worktrees": rows, "retired_worktrees": len(rows),
        "source_allocated_bytes": sum(row["source_allocated_bytes"] for row in rows),
        "archive_payload_bytes": sum(row["archive_bytes"] for row in rows),
        "archive_allocated_bytes_observed": allocated,
        "net_reclaimed_bytes": sum(row["source_allocated_bytes"] for row in rows) - allocated,
        "receipt_net_reclaimed_bytes": receipt["net_reclaimed_bytes"],
        "registered_worktrees_after": len(registered),
        "all_retired_paths_and_registrations_absent": True,
        "all_original_commits_and_reflog_commits_pinned": True,
        "all_original_git_trees_match": True,
        "all_extra_file_archive_sha256_match": True,
        "all_archives_compared_before_removal": True,
        "equivalent_faster_reference_enumeration": receipt["enumeration_equivalence"],
        "original_interruption_evidence_preserved": bool(checkpoint),
        "symbolic_link_targets_untouched": args.linked,
        "complete_checkout_restoration_rehearsal": receipt["full_checkout_restore_rehearsal"],
        "linked_archive_restoration_rehearsal": linked_restore,
        "skipped_worktrees": len(plan["skipped"]),
        "free_bytes_observed": shutil.disk_usage(ROOT).free,
        "inputs_sha256": {
            str(path): digest(path) for path in (
                plan_path, receipt_path, HERE / "retire_worktrees.py",
                HERE / "continue_retirement.py", HERE / "parallel_retirement.py", Path(__file__),
            )
        },
        "scope": "Storage maintenance only; no GPU work or source/experiment policy change.",
    }
    review["allocation_snapshot_difference_bytes"] = (
        review["net_reclaimed_bytes"] - receipt["net_reclaimed_bytes"]
    )
    lines = [
        "# Retired Historical Worktrees, 2026-11-01", "",
        f"Retired {len(rows)} clean detached worktrees after reference and process checks.",
        "Original HEAD and reflog commits remain pinned under refs/archive/20261101-worktrees.",
        "Ignored files and original Git administration passed complete tar comparisons before removal.",
        "A full representative checkout was rebuilt successfully before any original was removed.", "",
        f"Source allocation: {review['source_allocated_bytes'] / 1e9:.3f} GB.",
        f"Archived extra-file payload: {review['archive_payload_bytes'] / 1e9:.3f} GB.",
        f"Net reclaimed: {review['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Inventory and preservation decisions: `{plan_path}`",
        f"Execution receipt: `{receipt_path}`",
        f"Independent review: `{output}`", "",
        "## Original Paths", "",
        "| Worktree | Pinned commit | Source GB |", "| --- | --- | ---: |",
    ]
    lines.extend(
        f"| `{Path(row['path']).name}` | `{row['commit']}` "
        f"| {row['source_allocated_bytes'] / 1e9:.3f} |" for row in rows
    )
    lines += ["", "## Restore", "",
              "Each command recreates the original path and complete Git-tracked source.",
              "Then extract its archived ignored files. Keep original admin archives as evidence;",
              "Git creates fresh valid administration data during restoration.", ""]
    for row in rows:
        lines += [
            f"### {Path(row['path']).name}", "", "```bash",
            *row["restore_commands"], "```", "",
        ]
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    with output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: review[key] for key in (
        "status", "retired_worktrees", "source_allocated_bytes", "archive_payload_bytes",
        "net_reclaimed_bytes", "registered_worktrees_after", "free_bytes_observed",
    )}, indent=2))


if __name__ == "__main__":
    main()
