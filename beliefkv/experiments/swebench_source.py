"""Validate local SWE-bench source objects before per-workflow checkouts."""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def verify_workload_source_objects(
    workloads: Iterable[tuple[Path, str]],
) -> None:
    by_repo: dict[Path, set[str]] = defaultdict(set)
    for source_repo, base_commit in workloads:
        by_repo[source_repo.resolve()].add(base_commit)
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    for source_repo, commits in by_repo.items():
        blobs = set()
        for commit in sorted(commits):
            result = subprocess.run(
                [
                    "git", "ls-tree", "-r", "--full-tree",
                    "--format=%(objecttype) %(objectname)", commit,
                ],
                cwd=source_repo, env=env, capture_output=True, text=True,
                check=False, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"source tree unavailable locally for {commit} in "
                    f"{source_repo}: {result.stderr.strip()}"
                )
            blobs.update(
                line.split(" ", 1)[1] for line in result.stdout.splitlines()
                if line.startswith("blob ")
            )
        if not blobs:
            continue
        result = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            cwd=source_repo, env=env, capture_output=True, text=True,
            input="\n".join(sorted(blobs)) + "\n",
            check=False, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to check local source blobs in {source_repo}: "
                f"{result.stderr.strip()}"
            )
        missing = [
            line for line in result.stdout.splitlines()
            if not line.endswith(" blob")
        ]
        if missing:
            raise RuntimeError(
                f"{source_repo} lacks {len(missing)} checkout blobs for the "
                f"selected base commits (first: {missing[0]}). Prefetch "
                "complete source objects before launching GPU work, e.g. "
                "git fetch --refetch --no-filter origin <base-commits>."
            )
