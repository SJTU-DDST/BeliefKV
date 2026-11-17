"""Restore one retired checkout with its ignored data, then remove the verified probe."""

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retirement_operator", HERE / "retire_worktrees.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
base = operator.base


def main():
    output = HERE / "restoration_review.json"
    if output.exists():
        raise FileExistsError("preserve the original restoration proof")
    record = json.loads(base.RECEIPT.read_bytes())
    if record["status"] != "pinned_archived_and_retired":
        raise ValueError("retirement must finish first")
    row = next(
        (item for item in record["worktrees"]
         if any(info.get("kind") == "symlink" for info in item["ignored_files"].values())),
        record["worktrees"][0],
    )
    probe = base.ARCHIVES / "verified_restore_probe"
    if probe.exists():
        raise FileExistsError("preserve the existing restoration probe")
    base.git(base.ROOT, "worktree", "add", "--quiet", "--detach", str(probe), row["archive_ref"])
    archive = row.get("ignored_archive")
    if archive:
        for action in ("--extract", "--compare"):
            subprocess.run(
                ["tar", action, "--use-compress-program=zstd -T1",
                 "--file", archive["path"], "--directory", str(probe)],
                check=True, capture_output=True,
            )
    for name, info in row["ignored_files"].items():
        path = probe / name
        if info.get("kind") == "symlink" and (
            not path.is_symlink() or str(path.readlink()) != info["target"]
        ):
            raise ValueError("restored symbolic link differs; preserve the probe")
    if (
        base.git(probe, "rev-parse", "HEAD^{tree}").stdout.decode().strip() != row["tree"]
        or base.git(probe, "status", "--porcelain=v1", "--untracked-files=all").stdout
    ):
        raise ValueError("restored tracked files differ; preserve the probe")
    base.git(base.ROOT, "worktree", "remove", str(probe))
    proof = {
        "status": "verified_restoration",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "original_path": row["path"], "commit": row["commit"], "git_tree": row["tree"],
        "ignored_files_restored_and_compared": len(row["ignored_files"]),
        "symbolic_links_restored": sum(
            info.get("kind") == "symlink" for info in row["ignored_files"].values()
        ),
        "complete_checkout_clean": True, "probe_removed_without_force": not probe.exists(),
    }
    base.save(output, proof)
    print(json.dumps(proof, indent=2), flush=True)


if __name__ == "__main__":
    main()
