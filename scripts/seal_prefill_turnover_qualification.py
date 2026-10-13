#!/usr/bin/env python3
"""Bind turnover-observation checks to the exact cold candidate and engine."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.experiments.source_provenance import git_source_state


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior qualification")
    suite, = ET.parse(args.junit).getroot().findall("testsuite")
    checks = {key: int(suite.get(key)) for key in ("tests", "failures", "errors", "skipped")}
    if checks["tests"] < 144 or any(checks[key] for key in ("failures", "errors", "skipped")):
        raise ValueError(f"incomplete qualification: {checks}")
    classes = {case.get("classname") for case in suite.findall("testcase")}
    for expected in (
        "test_prefill_turnover_probe", "test_v0520_native_telemetry",
        "test_restore_wait_probe", "test_analyze_prefill_turnover",
        "test_wait_prefetch_batch", "TestBeliefKVHook",
    ):
        if not any(expected in name for name in classes):
            raise ValueError(f"missing qualification scope: {expected}")
    benchmark = json.loads(args.benchmark.read_bytes())
    probe = ROOT / "beliefkv/runtime/prefill_turnover_probe.py"
    if benchmark["probe_sha256"] != digest(probe):
        raise ValueError("probe differs from measured source")
    candidate = benchmark["engine_sources"]["candidate"]
    scheduler = args.engine / "python/sglang/srt/managers/scheduler.py"
    if (
        candidate["engine"] != str(args.engine.resolve())
        or candidate["scheduler_sha256"] != digest(scheduler)
        or benchmark["benchmark_sha256"] != digest(ROOT / "scripts/benchmark_prefill_turnover_probe.py")
    ):
        raise ValueError("benchmark source or engine changed")
    cases = benchmark["cases"]
    if {case["prefill_every"] for case in cases} != {1, 5, 16} or any(
        not case["dispatch_counts_equal"] for case in cases
    ):
        raise ValueError("missing unchanged-dispatch CPU evidence")
    engine = git_source_state(
        args.engine, canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if not engine["matches_worktree"]:
        raise ValueError("canonical patch differs from tested engine")
    paths = (
        "beliefkv/runtime/prefill_turnover_probe.py",
        "beliefkv/runtime/v0520_native_telemetry.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_prefill_turnover_probe.py",
        "scripts/seal_prefill_turnover_qualification.py",
        "scripts/analyze_prefill_turnover.py",
        "scripts/seal_reservation_cutoff.py",
        "tests/test_prefill_turnover_probe.py",
        "tests/test_v0520_native_telemetry.py",
        "tests/test_restore_wait_probe.py",
        "tests/test_analyze_prefill_turnover.py",
        "tests/test_wait_prefetch_batch.py",
    )
    result = {
        "qualified_at_local": datetime.now().astimezone().isoformat(),
        "status": "qualified_for_cold_observation",
        "base_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "source_sha256": {path: digest(ROOT / path) for path in paths},
        "engine": engine, "candidate_engine": str(args.engine.resolve()),
        "engine_hook_tests_sha256": digest(
            args.engine / "test/srt/test_beliefkv_scheduler_hook.py",
        ),
        "checks": {**checks, "path": str(args.junit.resolve()), "sha256": digest(args.junit)},
        "benchmark": {
            "path": str(args.benchmark.resolve()), "sha256": digest(args.benchmark),
            "incremental_cpu_ns_per_iteration": {
                str(case["prefill_every"]): case["incremental_cpu_ns_per_iteration"]
                for case in cases
            },
        },
        "constraints": [
            "Existing workload, budgets, prefill batching and delay settings remain frozen.",
            "Sample one in 16 prefills with a bounded backlog; retain no request/tensor ownership.",
            "Both native and predictive telemetry use the same loop instrumentation.",
            "CUDA event queries are nonblocking; unsupported, pending and shutdown evidence stays unknown.",
            "Normal-loop GPU samples use the actual current stream; overlap uses the forward stream.",
            "GPU spans include dependency waits and CPU dispatch gaps, not exclusive kernels.",
            "CPU fixtures prove dispatch counts and incremental observation cost, not real GPU overhead.",
            "Prior probe microbenchmarks exclude the new nested launch-telemetry span and first-service prefix conversion.",
            "List/tuple prefix conversion preserves exact numeric fallback; performance remains unmeasured.",
            "launch_telemetry is part of run_batch; never add both as exclusive CPU cost.",
            "Failed burst checks emit bounded existing-plan evidence, preserving the pre-drain JOIN identity.",
            "Rejection observations add no physical queries, allocations or changes to existing validation.",
            "The old probe CPU fixture excludes the new failure-only observation cost; real perturbation remains unmeasured.",
            "Real GPU events, their perturbation, native counterparts and full JCT gains remain unverified.",
            "V54 has a recorded administrative reservation cutoff; preserve its unfinished outcome.",
            "Cold deployment is allowed only after 06:00 Asia/Shanghai and GPU availability checks.",
        ],
        "helper_sha256": digest(Path(__file__)),
    }
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "checks", "benchmark")}, indent=2))


if __name__ == "__main__":
    main()
