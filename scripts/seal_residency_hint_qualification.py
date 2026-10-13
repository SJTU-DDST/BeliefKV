#!/usr/bin/env python3
"""Seal residency-ordering qualification while preserving the running source."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve qualification")
    suite, = ET.parse(args.junit).getroot().findall("testsuite")
    checks = {key: int(suite.get(key)) for key in ("tests", "failures", "errors", "skipped")}
    if checks != {"tests": 409, "failures": 0, "errors": 0, "skipped": 0}:
        raise ValueError(f"unexpected qualification: {checks}")
    benchmark = json.loads(args.benchmark.read_bytes())
    source = ROOT / "beliefkv/runtime/sglang_v0520_runtime.py"
    if benchmark["qualified_runtime_sha256"] != digest(source):
        raise ValueError("benchmark runtime changed")
    cases = benchmark["cases"]
    if len(cases) != 36 or any(
        not row["matching_input_checkpoint_order_equal"] or not row["tensor_indices"]
        for row in cases
    ):
        raise ValueError("missing native matching-input CPU evidence")
    reductions = [row["mean_thread_cpu_reduction_fraction"] for row in cases]
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "scripts/benchmark_prefill_probe_budget_cpu.py",
        "scripts/analyze_prefill_work_bins.py",
        "scripts/seal_residency_hint_qualification.py",
        "tests/test_session_residency_hint.py",
        "tests/test_sglang_v0520_runtime.py",
    )
    result = {
        "qualified_at_local": datetime.now().astimezone().isoformat(),
        "status": "qualified_for_cold_development",
        "base_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "candidate_root": str(ROOT),
        "source_sha256": {path: digest(ROOT / path) for path in paths},
        "canonical_patch_sha256": digest(
            ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
        ),
        "checks": {**checks, "path": str(args.junit.resolve()), "sha256": digest(args.junit)},
        "benchmark": {
            "path": str(args.benchmark.resolve()), "sha256": digest(args.benchmark),
            "case_count": len(cases), "minimum_cpu_reduction": min(reductions),
            "maximum_cpu_reduction": max(reductions),
        },
        "constraints": [
            "Session residency hints affect ordering only; input token contents are explicitly unverified.",
            "Hints are kept separate from authoritative reentry observations and expire on the existing 50ms cadence.",
            "Unknown or unsupported hints retain the complete native input-checkpoint inspection.",
            "Changed-input positive hints cannot create restore anchors or prove readiness for a reduced aged-head quota.",
            "Native prefix matching, complete data dependencies and resource admission remain authoritative.",
            "Aggregate native-prefix observations measure hint disagreement without new per-request I/O or policy gates.",
            "Matching-input CPU fixtures are not online hint accuracy, GPU speedup, useful prefetch or JCT proof.",
            "No new prefill batching, delay, mixed chunk, canary or completion guard is enabled.",
            "Running V53 stays frozen; deploy only after its process and postprocessing completion checks.",
        ],
        "helper_sha256": digest(Path(__file__)),
        "helper_utf8_source": Path(__file__).read_text(),
    }
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "checks", "benchmark")}, indent=2))


if __name__ == "__main__":
    main()
