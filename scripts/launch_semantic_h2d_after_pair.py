#!/usr/bin/env python3
"""Deploy a committed follow-up after a complete pair and start a fresh pair."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state
from scripts.resume_predictive_after_native import (
    PATCH_PATH,
    engine_delta,
    git,
    process_identity,
    write_json,
)
from scripts.resume_semantic_h2d_ab import resume_environment
from scripts.summarize_semantic_h2d_ab import verify_frozen_execution_source


def fresh_pair_environment(
    plan: dict, run_root: Path, *, python: str, port: int,
) -> dict[str, str]:
    environment = resume_environment(plan, run_root, python=python, port=port)
    environment.update(RESUME_PENDING="0", ARM_ORDER="predictive_h2d native")
    return environment


def validate_completed_pair(run_root: Path, plan: dict) -> None:
    if plan["order"] != ["predictive_h2d", "native"]:
        raise ValueError("the previous run must preserve its predictive/native pair")
    for arm in plan["order"]:
        summary = run_root / arm / f"client_{plan['root_count']}" / "summary.json"
        if json.loads(summary.read_text())["workflow_count"] != plan["root_count"]:
            raise ValueError(f"{arm} did not collect the complete arrival table")
        for artifact in (
            run_root / f"{arm}.timeline_export.json",
            run_root / f"{arm}.workspace_cleanup.json",
            run_root / "timelines" / f"{arm}.html",
        ):
            if not artifact.is_file():
                raise ValueError(f"previous pair postprocessing is incomplete: {artifact}")
    if not (run_root / "native_policy_comparison.json").is_file():
        raise ValueError("the previous pair has no final native-policy comparison")


def wait_for_processes(identities: dict[int, tuple | None]) -> None:
    while True:
        remaining = False
        for pid, identity in identities.items():
            current = process_identity(pid)
            if (
                identity is not None and current is not None
                and current[2] == identity[2] and current[0] != "Z"
            ):
                remaining = True
        if not remaining:
            return
        time.sleep(5)


def _require_clean(root: Path) -> None:
    if git(root, "status", "--porcelain", "--untracked-files=normal").strip():
        raise ValueError(f"checkout changed or contains uncommitted files: {root}")


def _require_idle_port(port: int) -> None:
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=1)
    except ConnectionRefusedError:
        return
    with connection:
        raise ValueError(f"port {port} is still serving another process")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path, required=True)
    parser.add_argument("--target-checkout", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--wait-pid", type=int, action="append", required=True)
    parser.add_argument("--port", type=int, default=18454)
    parser.add_argument(
        "--python",
        default="/home/longhao/miniconda3/envs/beliefkv-next/bin/python",
    )
    args = parser.parse_args()
    repo, previous, target, run_root, state_path = (
        path.resolve() for path in (
            args.repo, args.previous_run, args.target_checkout, args.run_root, args.state,
        )
    )
    if state_path.exists() or run_root.exists():
        raise ValueError("preserve the existing launch state or experiment directory")
    plan_path = previous / "ab_plan.json"
    original = json.loads(plan_path.read_text())
    revision = git(target, "rev-parse", "HEAD").decode().strip()
    previous_source = verify_frozen_execution_source(repo, original["code_commit"])
    _require_clean(repo)
    _require_clean(target)
    git(repo, "merge-base", "--is-ancestor", original["code_commit"], revision)
    patch = target / PATCH_PATH
    patch_sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    identities = {pid: process_identity(pid) for pid in args.wait_pid}
    environment = fresh_pair_environment(
        original, run_root, python=args.python, port=args.port,
    )
    state = {
        "status": "waiting_for_previous_pair",
        "previous_run": str(previous),
        "previous_code_commit": original["code_commit"],
        "previous_patch_sha256": original["sglang_patch_sha256"],
        "previous_execution_verification": previous_source,
        "target_checkout": str(target),
        "target_revision": revision,
        "target_patch_sha256": patch_sha,
        "new_run": str(run_root),
        "wait_process_identities": identities,
        "environment": environment,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path, state)
    print(json.dumps(state), flush=True)
    try:
        wait_for_processes(identities)
        validate_completed_pair(previous, original)
        if json.loads(plan_path.read_text()) != original:
            raise ValueError("the previous frozen plan changed during collection")
        if git(target, "rev-parse", "HEAD").decode().strip() != revision:
            raise ValueError("the scheduled target revision changed")
        _require_clean(repo)
        _require_clean(target)
        state["previous_execution_verification"] = verify_frozen_execution_source(
            repo, original["code_commit"],
        )
        if run_root.exists():
            raise ValueError("the new experiment directory was created by another process")
        if hashlib.sha256(patch.read_bytes()).hexdigest() != patch_sha:
            raise ValueError("the scheduled canonical patch changed")
        subprocess.run([
            args.python, str(repo / "scripts/summarize_semantic_h2d_ab.py"),
            "--run-root", str(previous), "--verify-frozen-plan",
        ], cwd=repo, check=True)
        _require_idle_port(args.port)
        engine = repo / "third_party/sglang-v0.5.20"
        old_patch = repo / PATCH_PATH
        git_source_state(engine, canonical_patch=old_patch)
        delta = engine_delta(engine, old_patch, patch)
        delta_path = state_path.with_suffix(".engine_delta.patch")
        delta_path.write_bytes(delta)
        if delta:
            git(engine, "apply", "--check", str(delta_path))
        state["status"] = "deploying_after_completed_pair"
        write_json(state_path, state)
        git(repo, "merge", "--ff-only", revision)
        if delta:
            git(engine, "apply", str(delta_path))
        state["deployed_engine"] = git_source_state(
            engine, canonical_patch=repo / PATCH_PATH,
        )["canonical_patch"]
        state["status"] = "running_new_pair"
        state["started_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, state)
        print(json.dumps(state), flush=True)
        result = subprocess.run(
            ["bash", str(repo / "scripts/run_qwen35_semantic_h2d_ab.sh")],
            cwd=repo, env={**os.environ, **environment},
        )
        state["exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"new experiment driver exited {result.returncode}")
        validate_completed_pair(
            run_root, json.loads((run_root / "ab_plan.json").read_text()),
        )
        state["status"] = "completed"
        state["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, state)
        print(json.dumps(state), flush=True)
    except Exception as error:
        state["status"] = "launch_or_experiment_failed"
        state["error"] = f"{type(error).__name__}: {error}"
        write_json(state_path, state)
        raise


if __name__ == "__main__":
    main()
