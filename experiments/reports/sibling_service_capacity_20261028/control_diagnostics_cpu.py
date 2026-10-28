"""Measure opt-in timing on a native CPU fixture; no GPU/JCT attribution."""

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
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous cost evidence")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("this fixture must hide all GPUs")
    from beliefkv.runtime.hotpath_timing import HotpathTiming
    import pytest
    from tests.test_full_only_native_lifecycle import full_native
    from tests.test_reference_sibling_service import sibling_native

    paths = (
        "beliefkv/runtime/hotpath_timing.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/join_sibling_service.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "tests/test_full_only_native_lifecycle.py",
        "tests/test_reference_sibling_service.py",
        str(Path(__file__).resolve().relative_to(ROOT)),
    )
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    cases = {}
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory() as directory:
        actual = full_native.__wrapped__(patch, NS(param=512))
        fixture = sibling_native.__wrapped__(actual, patch, Path(directory))
        live, _cache, _allocator, requests, adder = next(fixture)
        try:
            for mode, enabled, interval in (
                ("disabled", False, 64), ("sampled_64", True, 64), ("every_call", True, 1),
            ):
                live._hotpath_timing = HotpathTiming(enabled=enabled, sample_every=interval)
                samples, order = [], None
                for iteration in range(1050):
                    started = time.thread_time_ns()
                    plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=adder)
                    elapsed = time.thread_time_ns() - started
                    current = [key.request_id for key in plan.prioritized]
                    if order is not None and current != order:
                        raise ValueError("timing changed the native planning result")
                    order = current
                    if iteration >= 50:
                        samples.append(elapsed / 1000.)
                samples.sort()
                cases[mode] = {
                    "samples": len(samples), "warmup": 50,
                    "thread_cpu_p50_us": statistics.median(samples),
                    "thread_cpu_p99_us": samples[989],
                    "thread_cpu_max_us": max(samples),
                    "result_request_order": order,
                    "profile": live._hotpath_timing.snapshot(),
                }
        finally:
            try:
                next(fixture)
            except StopIteration:
                pass
    if len({tuple(row["result_request_order"]) for row in cases.values()}) != 1:
        raise ValueError("profiling configurations selected different work")
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
           for name, digest in sources.items()):
        raise ValueError("fixture sources changed during measurement")
    result = {
        "sources_sha256": sources,
        "fixture": {"programs": 3, "siblings": 2, "device_token_slots": 512},
        "cases": cases, "gpu_used": False, "performance_accepted": False,
        "limitations": [
            "CPU native tree/allocator fixture with copy/event stand-ins and an async journal.",
            "Small repeated plan does not measure maximum queues or whole-controller cost.",
            "Clock sampling adds cost; no GPU stall, historical CPU or JCT attribution follows.",
            "All sampled totals are measured samples, without expansion.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        mode: {name: row[name] for name in ("thread_cpu_p50_us", "thread_cpu_p99_us")}
        for mode, row in cases.items()
    }, indent=2))


if __name__ == "__main__":
    main()
