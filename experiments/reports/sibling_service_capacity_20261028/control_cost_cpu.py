#!/usr/bin/env python3
"""Measure native-fixture prefill planning; no GPU work or performance claim."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous CPU measurements")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("this fixture must hide all GPUs")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/join_sibling_service.py",
        "beliefkv/runtime/native_transfer_policy.py",
        "tests/test_reference_sibling_service.py",
        "tests/test_full_only_native_lifecycle.py",
        str(Path(__file__).resolve().relative_to(ROOT)),
    )
    sources = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths
    }
    import pytest
    from tests.test_full_only_native_lifecycle import full_native
    from tests.test_reference_sibling_service import sibling_native

    cases = {}
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory() as directory:
        actual = full_native.__wrapped__(patch, NS(param=512))
        fixture = sibling_native.__wrapped__(actual, patch, Path(directory))
        live, _cache, _allocator, requests, adder = next(fixture)
        siblings = live._agent_admission._siblings
        try:
            for mode in ("disabled", "joint_fit", "input_limited"):
                live._agent_admission._siblings = None if mode == "disabled" else siblings
                adder.rem_input_tokens = adder.rem_chunk_tokens = 1 if mode == "input_limited" else 16
                samples = []
                output_order = None
                for iteration in range(550):
                    started = time.thread_time_ns()
                    plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=adder)
                    elapsed = time.thread_time_ns() - started
                    output_order = [key.request_id for key in plan.prioritized]
                    if iteration >= 50:
                        samples.append(elapsed / 1000.)
                samples.sort()
                cases[mode] = {
                    "samples": len(samples), "warmup": 50,
                    "thread_cpu_p50_us": statistics.median(samples),
                    "thread_cpu_p99_us": samples[494],
                    "thread_cpu_max_us": max(samples),
                    "result_request_order": output_order,
                }
        finally:
            live._agent_admission._siblings = siblings
            try:
                next(fixture)
            except StopIteration:
                pass
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
           for name, digest in sources.items()):
        raise ValueError("CPU fixture sources changed during measurement")
    result = {
        "scope": "prefill_planning_thread_cpu_with_real_native_cpu_tree_and_copy_standins",
        "fixture": {"programs": 3, "siblings": 2, "device_token_slots": 512},
        "candidate_sources": sources,
        "cases": cases,
        "gpu_execution": False,
        "limits": [
            "Small repeatable CPU fixture, not the maximum 64-candidate or 24-member path.",
            "No GPU inference, DMA, writer-thread cost, whole-controller or JCT attribution.",
            "The 50ms advisory observation cache participates as it does in repeated native plans.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()
