#!/usr/bin/env python3
"""Deploy a committed revision between an active native arm and predictive."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


PATCH_PATH = "patches/sglang-v0.5.20-beliefkv-staging.patch"


def git(root: Path, *args: str, env: dict | None = None) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], env=env)


def process_identity(pid: int) -> tuple[str, int, str] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return None
    return fields[0], int(fields[1]), fields[19]


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def revised_plan(plan: dict, revision: str, patch_sha256: str) -> dict:
    if plan["order"] != ["native", "predictive_h2d"]:
        raise ValueError("transition requires native followed by predictive")
    updated = deepcopy(plan)
    updated["arm_revisions"] = {
        "native": {
            "code_commit": plan["code_commit"],
            "sglang_patch_sha256": plan["sglang_patch_sha256"],
        },
        "predictive_h2d": {
            "code_commit": revision,
            "sglang_patch_sha256": patch_sha256,
        },
    }
    updated["code_commit"] = revision
    updated["sglang_patch_sha256"] = patch_sha256
    updated["scope"] = (
        "user-authorized cross-revision live development comparison; "
        "same workload and model artifacts; report revision and trajectory differences"
    )
    updated["revision_transition"] = {
        "after_arm": "native",
        "before_arm": "predictive_h2d",
        "reason": "user requested latest validated optimizations after native finishes",
        "native_frozen_plan": "ab_plan.native_frozen.json",
        "isolated_policy_effect_verified": False,
    }
    return updated


def engine_delta(engine: Path, old_patch: Path, new_patch: Path) -> bytes:
    # Compare two patched upstream trees without changing the live engine or index.
    with tempfile.TemporaryDirectory(prefix="bkv-engine-transition-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        trees = []
        for patch in (old_patch, new_patch):
            git(engine, "read-tree", "HEAD", env=env)
            git(engine, "apply", "--cached", str(patch), env=env)
            trees.append(git(engine, "write-tree", env=env).decode().strip())
        return git(engine, "diff", "--binary", *trees)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--target-checkout", type=Path, required=True)
    parser.add_argument("--driver-pid", type=int, required=True)
    parser.add_argument("--native-pid", type=int, required=True)
    args = parser.parse_args()
    repo, run_root, target = (
        path.resolve() for path in (args.repo, args.run_root, args.target_checkout)
    )
    engine = (repo / "third_party/sglang-v0.5.20").resolve()
    state_path = run_root / "revision_transition.json"
    if state_path.exists():
        raise ValueError("a revision transition is already registered")
    driver = process_identity(args.driver_pid)
    native = process_identity(args.native_pid)
    if driver is None or driver[0] not in ("T", "t"):
        raise ValueError("stop only the pair driver before registering the transition")
    if native is None or native[1] != args.driver_pid:
        raise ValueError("native wrapper is not the pair driver's active child")
    plan_path = run_root / "ab_plan.json"
    original = json.loads(plan_path.read_text())
    revision = git(target, "rev-parse", "HEAD").decode().strip()
    git(target, "diff", "--exit-code", "HEAD", "--")
    patch = target / PATCH_PATH
    patch_sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    updated = revised_plan(original, revision, patch_sha)
    state = {
        "status": "waiting_for_native",
        "driver_pid": args.driver_pid,
        "native_pid": args.native_pid,
        "native_revision": original["code_commit"],
        "predictive_revision": revision,
        "predictive_patch_sha256": patch_sha,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(state_path, state)
    print(json.dumps(state), flush=True)
    try:
        while (current := process_identity(args.native_pid)) is not None:
            if current[2] != native[2] or current[0] == "Z":
                break
            time.sleep(5)
        current_driver = process_identity(args.driver_pid)
        if (
            current_driver is None or current_driver[2] != driver[2]
            or current_driver[0] not in ("T", "t")
        ):
            raise ValueError("pair driver identity or stopped state changed")
        summary = run_root / "native" / f"client_{original['root_count']}" / "summary.json"
        collected = json.loads(summary.read_text())
        if collected["workflow_count"] != original["root_count"]:
            raise ValueError("native collection did not finish the planned workload")
        if (run_root / "predictive_h2d").exists():
            raise ValueError("predictive has already started")
        if json.loads(plan_path.read_text()) != original:
            raise ValueError("the native plan changed during collection")
        python = os.environ.get(
            "PYTHON", "/home/longhao/miniconda3/envs/beliefkv-next/bin/python",
        )
        subprocess.run([
            python, str(repo / "scripts/summarize_semantic_h2d_ab.py"),
            "--run-root", str(run_root), "--verify-frozen-plan",
        ], cwd=repo, check=True)
        if git(target, "rev-parse", "HEAD").decode().strip() != revision:
            raise ValueError("the scheduled target revision changed")
        git(target, "diff", "--exit-code", "HEAD", "--")
        if hashlib.sha256(patch.read_bytes()).hexdigest() != patch_sha:
            raise ValueError("the scheduled engine patch changed")
        archive = run_root / "ab_plan.native_frozen.json"
        write_json(archive, original)
        old_patch = run_root / "native_staging.patch"
        old_patch.write_bytes(git(repo, "show", f"{original['code_commit']}:{PATCH_PATH}"))
        git(engine, "apply", "--reverse", "--check", str(old_patch))
        delta_path = run_root / "predictive_revision_delta.patch"
        delta_path.write_bytes(engine_delta(engine, old_patch, patch))
        git(engine, "apply", "--check", str(delta_path))
        state["status"] = "deploying_after_native"
        write_json(state_path, state)
        git(repo, "merge", "--ff-only", revision)
        git(engine, "apply", str(delta_path))
        git(engine, "apply", "--reverse", "--check", str(repo / PATCH_PATH))
        write_json(plan_path, updated)
        subprocess.run([
            python, str(repo / "scripts/summarize_semantic_h2d_ab.py"),
            "--run-root", str(run_root), "--verify-frozen-plan",
        ], cwd=repo, check=True)
        os.kill(args.driver_pid, signal.SIGCONT)
        state["status"] = "driver_resumed_with_latest_predictive"
        state["resumed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, state)
        print(json.dumps(state), flush=True)
    except Exception as error:
        state["status"] = "transition_failed_driver_kept_stopped"
        state["error"] = f"{type(error).__name__}: {error}"
        write_json(state_path, state)
        raise


if __name__ == "__main__":
    main()
