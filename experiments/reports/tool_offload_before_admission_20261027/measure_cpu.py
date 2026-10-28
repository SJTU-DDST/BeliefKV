"""Measure guarded pre-admission checks on the qualified native CPU fixture."""

from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_offload_before_admission import enable_early
from tests.test_tool_policy_adaptation import demand_runtime


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve CPU measurement")
    source_path = output.parent / "qualified_shared_cpu.sources.json"
    qualified = json.loads(source_path.read_bytes())
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/predicted_tool_offload.py",
        "tests/test_tool_offload_before_admission.py",
        "tests/test_tool_policy_adaptation.py",
        "tests/test_full_only_native_lifecycle.py",
    )
    sources = {name: sha256_file(ROOT / name) for name in paths}
    if any(sources[name] != qualified[name] for name in paths):
        raise ValueError("CPU fixture differs from the shared qualification")
    results = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION", "0")
        native = full_native.__wrapped__(patch, NS(param=64))
        fixture = demand_runtime.__wrapped__(native)
        values = next(fixture)
        runtime, controller, _cache, allocator, donor, request, _writes = values
        try:
            enable_early(values)
            key = runtime.visible[request.rid]
            hints = dict(runtime.tool_wait_hints)
            now = time.monotonic()
            patch.setattr(time, "monotonic", lambda: now)
            donor.component_data[0].lock_ref += 1
            batch = NS(reqs=[])
            for name in (
                "disabled", "no_tool_hints", "pending_not_due",
                "due_guarded_attempt", "new_request_attempt", "empty_plan",
            ):
                samples = []
                before = Counter(runtime.counts)
                runtime.tool_offload_before_admission = name != "disabled"
                runtime.tool_wait_hints = {} if name == "no_tool_hints" else dict(hints)
                for index in range(600):
                    controller._admission_seen[key] = None
                    controller.next_ms = now * 1000. + 100_000.
                    if name == "due_guarded_attempt":
                        controller.next_ms = 0.
                    elif name == "new_request_attempt":
                        controller._admission_seen.clear()
                    queue = () if name == "empty_plan" else (request,)
                    started = time.thread_time_ns()
                    runtime.plan_native_prefill(queue, running_batch=batch, adder=None)
                    elapsed = (time.thread_time_ns() - started) / 1000.
                    if index >= 100:
                        samples.append(elapsed)
                if allocator.available_size() != 4 or donor.component_data[0].value is None:
                    raise ValueError("guarded CPU fixture unexpectedly released capacity")
                checks = runtime.counts["tool_offload_before_admission_checks"] - before["tool_offload_before_admission_checks"]
                if checks != (600 if name in ("due_guarded_attempt", "new_request_attempt") else 0):
                    raise ValueError("CPU fixture did not exercise the declared dispatch path")
                results[name] = {
                    "samples": len(samples), "guarded_dispatches_including_warmup": checks,
                    "whole_plan_thread_cpu_us": {
                        "median": statistics.median(samples),
                        "p99": statistics.quantiles(samples, n=100, method="inclusive")[98],
                        "max": max(samples),
                    },
                }
        finally:
            try:
                next(fixture)
            except StopIteration:
                pass
    if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
        raise ValueError("measured sources changed")
    result = {
        "scope": "Full plan callback thread CPU with one pending consumer and real 64-slot native CPU cache.",
        "scenarios": results, "sources_sha256": sources,
        "qualified_source_manifest_sha256": sha256_file(source_path),
        "operator_sha256": sha256_file(Path(__file__)),
        "preserved_previous_measurement": {
            name: sha256_file(output.parent / name) for name in (
                "initial_measure_cpu.py", "initial_measure_cpu.json",
            )
        },
        "gpu_or_jct_performance_verified": False,
        "limitations": [
            "Native tree/allocator are real CPU objects; copy, Host indices and GPU events are stand-ins.",
            "The donor remains locked, so successful backup/release, journal writer and GPU interference are not measured.",
            "The fixture resets maintenance deadlines or first-seen state outside timed calls; this is not an online replay.",
            "Monotonic policy time is frozen while thread CPU time runs.",
            "The eight-consumer/eight-donor bound is not benchmarked by this one-consumer fixture.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
