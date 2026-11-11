"""Reuse the verified log-archival operator for inactive pre-Oct-20 runs."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "unused_archive_20261101/archive_old_logs.py"
spec = importlib.util.spec_from_file_location("previous_log_archival", SOURCE)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
archive.HERE = HERE
archive.ARCHIVES = ROOT / "experiments/archive/20261111_unused_logs"
archive.PLAN = HERE / "inventory.json"
archive.REPORT = HERE / "receipt.json"
archive.MARKER = "OLD_LOGS_ARCHIVED_20261111.md"
archive.RUNS = (
    "qwen35_native_predictive_replenished_108plus48_20261012_v29",
    "qwen35_native_predictive_replenished_108plus48_20261014_v58",
    "qwen35_joint_wait_h2d_predictive_108root_2to4_v8d",
    "qwen35_native_policy_108root_2to4_20261008_v9",
    "qwen35_joint_wait_h2d_ab_108root_2to4_v8c",
    "qwen35_joint_wait_h2d_ab_84root_v7",
    "qwen35_joint_wait_h2d_ab_84root_20261005_v5",
    "qwen35_joint_wait_h2d_ab_84root_20261006_v6",
    "qwen35_joint_wait_h2d_ab_64root_v4",
    "qwen35_join_prepare_h2d_ab_64root_20261004_v1",
    "qwen35_join_prepare_h2d_ab_64root_20261004_v3",
    "qwen35_native_predictive_replenished_108plus48_20261009_v12",
    "qwen35_native_predictive_replenished_108plus48_20261010_v19b",
    "qwen35_native_predictive_replenished_108plus48_20261010_v16b",
)


def review():
    plan = json.loads(archive.PLAN.read_bytes())
    receipt = json.loads(archive.REPORT.read_bytes())
    output = HERE / "verification.json"
    index = archive.ARCHIVES / "README.md"
    if output.exists() or index.exists():
        raise FileExistsError("preserve existing verification")
    if (
        receipt["status"] != "verified_and_archived"
        or receipt["wrapper_sha256"] != archive.digest(Path(__file__))
        or len(plan["runs"]) != len(receipt["runs"])
    ):
        raise ValueError("incomplete archival or changed operator")
    rows = []
    for expected, row in zip(plan["runs"], receipt["runs"]):
        path = Path(row["archive"])
        run = Path(row["run_root"])
        if (
            row["status"] != "verified_and_archived"
            or row["paths"] != expected["paths"]
            or row["run_root"] != expected["run_root"]
            or not row["full_tar_comparison_passed"]
            or archive.digest(path) != row["archive_sha256"]
            or path.stat().st_size != row["archive_bytes"]
            or archive.digest(run / "ab_plan.json") != row["plan_sha256"]
            or any((archive.RAW / name).exists() for name in row["paths"])
        ):
            raise ValueError("archive, plan or cleanup verification failed")
        members = subprocess.check_output([
            "tar", "--list", "--use-compress-program=zstd -T1",
            "--file", str(path),
        ], text=True).splitlines()
        if len(members) != len(row["paths"]) or set(members) != set(row["paths"]):
            raise ValueError("archive member inventory differs")
        marker = (run / archive.MARKER).read_text()
        if row["archive_sha256"] not in marker or row["restore_command"] not in marker:
            raise ValueError("missing recovery marker")
        rows.append({
            "run": run.name, "files": len(members), "archive": str(path),
            "archive_sha256": row["archive_sha256"],
            "source_allocated_bytes": row["source_allocated_bytes"],
            "archive_allocated_bytes": path.stat().st_blocks * 512,
            "restore_command": row["restore_command"],
        })
    result = {
        "status": "verified", "observed_at": archive.now(), "runs": rows,
        "files": sum(row["files"] for row in rows),
        "net_reclaimed_bytes": sum(
            row["source_allocated_bytes"] - row["archive_allocated_bytes"]
            for row in rows
        ),
        "all_complete_tar_comparisons_passed_before_removal": True,
        "all_archive_hashes_and_member_lists_verified": True,
        "all_original_plans_and_restore_markers_verified": True,
    }
    archive.save(output, result)
    lines = [
        "# Historical Qwen3.5 Logs, 2026-11-11", "",
        "Every selected log passed complete tar comparison before raw removal.",
        "Archive hashes, member inventories, original plans and recovery markers",
        "were verified after cleanup. Current inputs and raw workflow events remain.",
        "", f"Verified files: {result['files']}.",
        f"Net reclaimed: {result['net_reclaimed_bytes'] / 1e9:.3f} GB.", "",
        f"Receipt: `{archive.REPORT}`", f"Verification: `{output}`", "",
    ]
    for row in rows:
        lines.extend([
            f"## {row['run']}", "", f"SHA256: `{row['archive_sha256']}`",
            "", "```bash", row["restore_command"], "```", "",
        ])
    with index.open("x") as stream:
        stream.write("\n".join(lines))
    print(json.dumps({name: result[name] for name in (
        "status", "files", "net_reclaimed_bytes",
    )}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "execute", "review"))
    args = parser.parse_args()
    if args.action == "inventory":
        if archive.PLAN.exists():
            raise FileExistsError("preserve existing inventory")
        plan = archive.inventory()
        plan["wrapper_sha256"] = archive.digest(Path(__file__))
        plan["preserved"] = [
            "Recent/live registered inputs, all current 83-project experiments.",
            "All source/request tokens, client workflow events, results and plans.",
            "All task workspaces, frozen source trees, models and figures.",
            "Historical native and predictive logs remain losslessly recoverable.",
        ]
        archive.save(archive.PLAN, plan)
        print(json.dumps({
            "runs": len(plan["runs"]),
            "files": sum(len(row["paths"]) for row in plan["runs"]),
            "source_allocated_bytes": plan["source_allocated_bytes"],
            "skipped": plan["skipped"],
        }, indent=2), flush=True)
    elif args.action == "execute":
        plan = json.loads(archive.PLAN.read_bytes())
        if plan["wrapper_sha256"] != archive.digest(Path(__file__)):
            raise ValueError("wrapper changed after inventory")
        archive.execute(plan)
    else:
        review()


if __name__ == "__main__":
    main()
