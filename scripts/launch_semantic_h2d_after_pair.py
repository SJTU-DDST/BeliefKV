#!/usr/bin/env python3
"""Deploy a follow-up after completed or intentionally interrupted collection."""

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
    prefetch_lead_ms: int | None = None,
    execution_handoff: bool | None = None,
    host_numa_memory_policy: str | None = None,
    arm_order: str = "predictive_h2d",
    native_reference_run: Path | None = None,
    native_reference_timeline: Path | None = None,
    native_reference_cache: Path | None = None,
) -> dict[str, str]:
    if (
        native_reference_timeline is not None or native_reference_cache is not None
    ) and native_reference_run is None:
        raise ValueError("native reference artifacts require a native reference run")
    if native_reference_run is not None and arm_order != "predictive_h2d":
        raise ValueError("historical native references apply to predictive-only runs")
    environment = resume_environment(plan, run_root, python=python, port=port)
    environment.update(RESUME_PENDING="0", ARM_ORDER=arm_order)
    environment["HOST_NUMA_MEMORY_POLICY"] = (
        host_numa_memory_policy or plan.get("host_numa_memory_policy", "preferred")
    )
    # Empty values also clear reference paths inherited by the launching process.
    for field in (
        "NATIVE_REFERENCE_RUN", "NATIVE_REFERENCE_TIMELINE", "NATIVE_REFERENCE_CACHE",
    ):
        environment[field] = ""
    if native_reference_run is not None:
        environment["NATIVE_REFERENCE_RUN"] = str(native_reference_run)
        same_reference = bool(plan.get("native_reference_run")) and (
            Path(plan["native_reference_run"]).resolve() == native_reference_run.resolve()
        )
        for field, override in (
            ("native_reference_timeline", native_reference_timeline),
            ("native_reference_cache", native_reference_cache),
        ):
            if override is not None:
                environment[field.upper()] = str(override)
            elif same_reference:
                environment[field.upper()] = str(plan.get(field) or "")
    if prefetch_lead_ms is not None:
        environment["PREFETCH_LEAD_MS"] = str(prefetch_lead_ms)
    if execution_handoff is not None:
        environment["EXECUTION_HANDOFF"] = str(int(execution_handoff))
    return environment


def validate_completed_pair(run_root: Path, plan: dict) -> None:
    if plan["order"] not in (["predictive_h2d", "native"], ["predictive_h2d"]):
        raise ValueError("unsupported previous collection order")
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
    if len(plan["order"]) > 1 and not (run_root / "native_policy_comparison.json").is_file():
        raise ValueError("the previous pair has no final native-policy comparison")
    if plan.get("native_reference_run") and not (
        run_root / "native_reference_comparison.json"
    ).is_file():
        raise ValueError("the previous collection has no historical native comparison")


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
    tracked = git(
        root, "status", "--porcelain", "--untracked-files=no", "--",
        ".", ":(exclude)docs/",
    ).strip()
    untracked = git(
        root, "ls-files", "--others", "--exclude-standard", "--",
        ".", ":(exclude)docs/",
    ).strip()
    if tracked or untracked:
        raise ValueError(f"checkout changed or contains uncommitted files: {root}")


def verify_previous_execution(
    repo: Path, frozen_revision: str, *, previous_checkout: Path | None = None,
    development_restart: bool = False,
) -> tuple[Path, dict]:
    if previous_checkout is not None and not development_restart:
        raise ValueError("--previous-checkout requires --development-restart")
    source = repo if previous_checkout is None else previous_checkout.resolve()
    if previous_checkout is not None:
        _require_clean(source)
    verification = verify_frozen_execution_source(source, frozen_revision)
    verification["execution_root"] = str(source)
    return source, verification


def _require_idle_port(port: int) -> None:
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=1)
    except ConnectionRefusedError:
        return
    with connection:
        raise ValueError(f"port {port} is still serving another process")


def _remove_stale_control_socket(path: Path) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        try:
            connection.connect(str(path))
        except FileNotFoundError:
            return False
        except ConnectionRefusedError:
            if not path.is_socket():
                raise ValueError(f"control path is not a socket: {path}")
            path.unlink()
            return True
    raise ValueError(f"control socket is still serving another process: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path, required=True)
    parser.add_argument(
        "--previous-checkout", type=Path,
        help="Verify interrupted development against its retained frozen checkout when the deployment repo has advanced.",
    )
    parser.add_argument("--target-checkout", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--wait-pid", type=int, action="append", required=True)
    parser.add_argument(
        "--development-restart", action="store_true",
        help="Start a fresh development pair after old processes exit, retaining partial evidence.",
    )
    parser.add_argument("--port", type=int, default=18454)
    parser.add_argument(
        "--arm-order", choices=("predictive_h2d", "predictive_h2d native"),
        default="predictive_h2d",
        help="Development defaults to predictive only; request a fresh native arm explicitly.",
    )
    parser.add_argument(
        "--native-reference-run", type=Path,
        help="Completed run containing the fixed native reference; defaults to the previous reference.",
    )
    parser.add_argument(
        "--native-reference-timeline", type=Path,
        help="Select a corrected native timeline without replacing the sealed original.",
    )
    parser.add_argument(
        "--native-reference-cache", type=Path,
        help="Select a corrected comparison cache; its path and digest enter the new frozen plan.",
    )
    parser.add_argument(
        "--prefetch-lead-ms", type=int,
        help="Explicitly override the next pair's residency lead while preserving the previous plan.",
    )
    parser.add_argument(
        "--execution-handoff", type=int, choices=(0, 1),
        help="Use 0 to let native prefill restore submitted demand; JOIN/tool anticipation remains enabled.",
    )
    parser.add_argument(
        "--host-numa-memory-policy", choices=("bind", "preferred"),
        help="Freeze the next cold run's NUMA policy; preferred permits fallback.",
    )
    parser.add_argument(
        "--python",
        default="/home/longhao/miniconda3/envs/beliefkv-next/bin/python",
    )
    args = parser.parse_args()
    if args.arm_order != "predictive_h2d" and any(
        value is not None for value in (
            args.native_reference_run, args.native_reference_timeline,
            args.native_reference_cache,
        )
    ):
        parser.error("historical native references apply to predictive-only runs")
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
    previous_root, previous_source = verify_previous_execution(
        repo, original["code_commit"], previous_checkout=args.previous_checkout,
        development_restart=args.development_restart,
    )
    _require_clean(repo)
    _require_clean(target)
    git(repo, "merge-base", "--is-ancestor", original["code_commit"], revision)
    patch = target / PATCH_PATH
    patch_sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    identities = {pid: process_identity(pid) for pid in args.wait_pid}
    native_reference = None
    if args.arm_order == "predictive_h2d":
        native_reference = (
            args.native_reference_run
            or Path(original.get("native_reference_run") or previous)
        ).resolve()
    environment = fresh_pair_environment(
        original, run_root, python=args.python, port=args.port,
        prefetch_lead_ms=args.prefetch_lead_ms,
        execution_handoff=(
            bool(args.execution_handoff) if args.execution_handoff is not None else None
        ),
        host_numa_memory_policy=args.host_numa_memory_policy,
        arm_order=args.arm_order, native_reference_run=native_reference,
        native_reference_timeline=(
            args.native_reference_timeline.resolve()
            if args.native_reference_timeline is not None else None
        ),
        native_reference_cache=(
            args.native_reference_cache.resolve()
            if args.native_reference_cache is not None else None
        ),
    )
    state = {
        "status": (
            "waiting_for_previous_processes" if args.development_restart
            else "waiting_for_previous_pair"
        ),
        "previous_collection_scope": (
            "interrupted_development" if args.development_restart else "completed_pair"
        ),
        "previous_run": str(previous),
        "previous_code_commit": original["code_commit"],
        "previous_patch_sha256": original["sglang_patch_sha256"],
        "previous_execution_root": str(previous_root),
        "previous_execution_verification": previous_source,
        "target_checkout": str(target),
        "target_revision": revision,
        "target_patch_sha256": patch_sha,
        "new_run": str(run_root),
        "wait_process_identities": identities,
        "environment": environment,
        "native_reference_run": str(native_reference) if native_reference else None,
        "native_reference_timeline": environment["NATIVE_REFERENCE_TIMELINE"] or None,
        "native_reference_cache": environment["NATIVE_REFERENCE_CACHE"] or None,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path, state)
    print(json.dumps(state), flush=True)
    try:
        wait_for_processes(identities)
        if not args.development_restart:
            validate_completed_pair(previous, original)
        if json.loads(plan_path.read_text()) != original:
            raise ValueError("the previous frozen plan changed during collection")
        if git(target, "rev-parse", "HEAD").decode().strip() != revision:
            raise ValueError("the scheduled target revision changed")
        _require_clean(repo)
        _require_clean(target)
        _, state["previous_execution_verification"] = verify_previous_execution(
            repo, original["code_commit"], previous_checkout=args.previous_checkout,
            development_restart=args.development_restart,
        )
        if run_root.exists():
            raise ValueError("the new experiment directory was created by another process")
        if hashlib.sha256(patch.read_bytes()).hexdigest() != patch_sha:
            raise ValueError("the scheduled canonical patch changed")
        subprocess.run([
            args.python, str(previous_root / "scripts/summarize_semantic_h2d_ab.py"),
            "--run-root", str(previous), "--verify-frozen-plan",
        ], cwd=previous_root, check=True)
        _require_idle_port(args.port)
        if args.development_restart:
            state["stale_control_socket_removed"] = _remove_stale_control_socket(
                Path(f"/tmp/bkv-regime-{args.port}.sock"),
            )
        engine = repo / "third_party/sglang-v0.5.20"
        old_patch = repo / PATCH_PATH
        git_source_state(engine, canonical_patch=old_patch)
        delta = engine_delta(engine, old_patch, patch)
        delta_path = state_path.with_suffix(".engine_delta.patch")
        delta_path.write_bytes(delta)
        if delta:
            git(engine, "apply", "--check", str(delta_path))
        state["status"] = (
            "deploying_after_interrupted_development" if args.development_restart
            else "deploying_after_completed_pair"
        )
        write_json(state_path, state)
        git(repo, "merge", "--ff-only", revision)
        if delta:
            git(engine, "apply", str(delta_path))
        state["deployed_engine"] = git_source_state(
            engine, canonical_patch=repo / PATCH_PATH,
        )["canonical_patch"]
        state["status"] = (
            "running_new_predictive" if args.arm_order == "predictive_h2d"
            else "running_new_pair"
        )
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
