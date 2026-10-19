#!/usr/bin/env python3
"""Continue an unstarted predictive arm using the collected native plan."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.resume_predictive_after_native import PATCH_PATH, git, write_json


def resume_environment(plan: dict, run_root: Path, *, python: str, port: int) -> dict[str, str]:
    values = {
        "PYTHON": python, "RUN_ROOT": str(run_root),
        "ROOT_COUNT": plan["root_count"],
        "ARRIVAL_BATCH_SIZE": plan["workflow_arrival_batch_size"],
        "ARRIVAL_BATCH_INTERVAL_MS": plan["workflow_arrival_batch_interval_ms"],
        "ARRIVAL_RATE_PER_SECOND": plan.get("workflow_arrival_rate_per_second", 0),
        "ARRIVAL_SEED": plan.get("workflow_arrival_seed", 21),
        "ARM_ORDER": " ".join(plan["order"]), "RESUME_PENDING": 1,
        "HOST_SPLIT": plan["host_split"], "PORT": port,
        "HOST_NUMA_MEMORY_POLICY": plan.get("host_numa_memory_policy", "bind"),
        "SAMPLING_SEED": plan["sampling_seed"], "REPETITION_ID": plan["repetition_id"],
        "ACTIVATION_WALL_CLOCK_SECONDS": plan["activation_wall_clock_seconds"],
        "MODEL_REQUEST_TIMEOUT_SECONDS": plan.get("model_request_timeout_seconds", 600.),
        "MAX_COMPLETION_TOKENS": plan.get("max_completion_tokens", 8192),
        "SEMANTIC_REPORT_ARTIFACT": plan.get("semantic_artifact") or "",
        "PREPARE_HOST": int(plan["policy_configuration"]["predictive_h2d"]["prepare_host"]),
        "EXECUTION_HANDOFF": int(
            plan["policy_configuration"]["predictive_h2d"].get("execution_handoff", True)
        ),
        "H2D_SEED_ARTIFACT": plan["h2d_seed_artifact"],
        "TOOL_TIMING_ARTIFACT": plan["tool_timing_artifact"],
        "TRANSFER_SERVICE_SEED": plan["transfer_service_seed"],
        "ENABLE_TOOL_TIMING": int(plan["tool_timing_enabled"]),
        "PREFETCH_LEAD_MS": plan["prefetch_lead_ms"],
        "JOIN_PREFETCH_MIN_ADVANCE_MS": plan.get("join_prefetch_min_advance_ms", 0),
        "TOOL_PREFETCH_MIN_ADVANCE_MS": plan.get("tool_prefetch_min_advance_ms", 0),
        "PARALLEL_WAIT_PREFETCH": int(plan.get("parallel_wait_prefetch", False)),
        "WAIT_PREFETCH_CONTEXTS": plan.get("wait_prefetch_contexts", 1),
        "PREDICTION_MAINTENANCE_INTERVAL_MS": plan.get(
            "prediction_maintenance_interval_ms", 0,
        ),
        "H2D_OPPORTUNITY_SAMPLING": int(
            plan["policy_configuration"]["predictive_h2d"].get("h2d_opportunity_sampling", True)
        ),
        "TERMINAL_CACHE_DIAGNOSTICS": int(plan.get("terminal_cache_diagnostics", True)),
        "PROFILE_SHARED_PATH": int(plan.get("profile_shared_path", True)),
        "SEMANTIC_WORK_STATISTIC": plan["semantic_work_statistic"],
        "EOS_PROTOCOL_WINDOW_MS": plan["eos_protocol_window_ms"],
        "FANOUT_PROFILE": plan["fanout_profile"],
        "CHILD_FINAL_REPORT_SHADOW": int(plan["child_final_report_shadow"]),
        "WORKLOAD_MANIFEST": plan["workload_manifest"],
    }
    for field in (
        "native_reference_run", "native_reference_timeline", "native_reference_cache",
    ):
        values[field.upper()] = plan.get(field) or ""
    return {name: str(value) for name, value in values.items()}


def update_predictive_revision(plan: dict, revision: str, patch_sha256: str) -> dict:
    updated = deepcopy(plan)
    updated["code_commit"] = revision
    updated["sglang_patch_sha256"] = patch_sha256
    updated["arm_revisions"]["predictive_h2d"] = {
        "code_commit": revision, "sglang_patch_sha256": patch_sha256,
    }
    updated["revision_transition"]["predictive_resume_reason"] = (
        "native export path fixed; completed pending opportunity-aware optimizations"
    )
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18454)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    plan_path = run_root / "ab_plan.json"
    original = json.loads(plan_path.read_text())
    if original["order"] != ["native", "predictive_h2d"]:
        raise ValueError("resume expects collected native followed by unstarted predictive")
    if (run_root / "predictive_h2d").exists():
        raise ValueError("predictive already exists; retain its data and investigate")
    summary = json.loads(
        (run_root / "native" / f"client_{original['root_count']}" / "summary.json").read_text()
    )
    if summary["workflow_count"] != original["root_count"]:
        raise ValueError("native collection did not cover the complete arrival table")
    revision = git(ROOT, "rev-parse", "HEAD").decode().strip()
    git(ROOT, "diff", "--exit-code", "HEAD", "--")
    patch_sha = hashlib.sha256((ROOT / PATCH_PATH).read_bytes()).hexdigest()
    if patch_sha != original["sglang_patch_sha256"]:
        raise ValueError("deploy the planned engine patch before resuming")
    plan = update_predictive_revision(original, revision, patch_sha)
    write_json(run_root / "ab_plan.predictive_pre_resume.json", original)
    write_json(plan_path, plan)
    python = os.environ.get("PYTHON", "/home/longhao/miniconda3/envs/beliefkv-next/bin/python")
    subprocess.run([
        python, str(ROOT / "scripts/summarize_semantic_h2d_ab.py"),
        "--run-root", str(run_root), "--verify-frozen-plan",
    ], cwd=ROOT, check=True)
    environment = resume_environment(plan, run_root, python=python, port=args.port)
    write_json(run_root / "predictive_resume_environment.json", environment)
    state_path = run_root / "revision_transition.json"
    state = json.loads(state_path.read_text())
    state.update(
        status="launching_predictive_after_export_fix",
        predictive_revision=revision, predictive_patch_sha256=patch_sha,
        previous_predictive_revision=original["code_commit"],
    )
    write_json(state_path, state)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run_qwen35_semantic_h2d_ab.sh")],
        cwd=ROOT, env={**os.environ, **environment},
    )
    state["status"] = "predictive_completed" if result.returncode == 0 else "predictive_resume_failed"
    state["resume_exit_code"] = result.returncode
    write_json(state_path, state)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
