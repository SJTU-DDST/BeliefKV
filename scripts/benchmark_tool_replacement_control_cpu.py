"""Compare replacement checks on real native CPU trees without GPU work."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_tool_replacement import ReferenceToolReplacement
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write


def baseline_type(checkout):
    path = checkout / "beliefkv/runtime/reference_tool_replacement.py"
    source = path.read_bytes()
    namespace = {"__name__": "baseline_tool_replacement_cpu"}
    exec(compile(source, str(path), "exec"), namespace)
    return namespace["ReferenceToolReplacement"], {str(path.resolve()): hashlib.sha256(source).hexdigest()}


def measure(engine, baseline, *, case, blocks, calls):
    import pytest

    from tests.test_full_only_native_lifecycle import full_native
    from tests.test_reference_tool_replacement import pair
    from tests.test_tool_policy_adaptation import demand_runtime

    with ExitStack() as stack:
        monkeypatch = stack.enter_context(pytest.MonkeyPatch.context())
        monkeypatch.setenv("SGLANG_SOURCE_CHECKOUT", str(engine))
        native = full_native.__wrapped__(monkeypatch, NS(param=64))
        fixture = demand_runtime.__wrapped__(native)
        parts = next(fixture)
        stack.callback(fixture.close)
        runtime, admission, controller, adder = pair(parts)
        runtime._opportunity_writer = None
        if case == "impossible_logical_budget":
            request = parts[5]
            request.origin_input_ids.extend(range(1000, 1083))
            admission.policy.programs["consumer"].tokens = len(request.origin_input_ids)
        elif case == "logical_fit_prediction_unsupported":
            monkeypatch.setattr(runtime, "_tool_offload_remaining", lambda *_args, **_kwargs: None)
        else:
            raise ValueError("unknown bounded replacement CPU fixture")
        variants = {
            "baseline": baseline(controller),
            "candidate": ReferenceToolReplacement(controller),
        }
        requests, running = list(runtime._capacity_waiting_queue), NS(reqs=[])

        def invoke(replacement):
            controller.replacement = replacement
            replacement.next_ms = 0.
            return replacement.run(requests, running_batch=running, adder=adder)

        def verify_unchanged():
            if (
                parts[3].available_size() != 4 or parts[4].component_data[0].value is None
                or admission.policy.programs["consumer"].active
                or not admission.policy.programs["hot"].active
                or not admission.policy.programs["tool"].active
                or runtime.physical_ledger.pending_count
                or native[2] or native[3]
            ):
                raise ValueError("a failed replacement benchmark changed physical or execution state")

        observations = {}
        for name, replacement in variants.items():
            counter = Mock(wraps=runtime._read_request_reentry)
            native_counter = Mock(wraps=parts[2].inspect_beliefkv_reentry)
            with (
                patch.object(runtime, "_read_request_reentry", counter),
                patch.object(parts[2], "inspect_beliefkv_reentry", native_counter),
            ):
                for _ in range(10):
                    if invoke(replacement):
                        raise ValueError("a failed replacement fixture unexpectedly granted an agent")
            observations[name] = {
                "reentry_helper_calls_in_10_calls": counter.call_count,
                "native_prefix_inspections_in_10_calls": native_counter.call_count,
            }
            verify_unchanged()
        timings = {name: {"thread_cpu_us": [], "wall_us": []} for name in variants}
        for index in range(blocks):
            names = list(variants)
            if index % 2:
                names.reverse()
            for name in names:
                replacement = variants[name]
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                for _ in range(calls):
                    if invoke(replacement):
                        raise ValueError("a failed replacement fixture unexpectedly granted an agent")
                timings[name]["thread_cpu_us"].append((time.thread_time_ns() - cpu) / calls / 1000.)
                timings[name]["wall_us"].append((time.perf_counter_ns() - wall) / calls / 1000.)
                verify_unchanged()
        summaries = {
            name: {clock: distribution(values) for clock, values in clocks.items()}
            for name, clocks in timings.items()
        }
        return {
            "case": case, "alternating_blocks": blocks, "calls_per_block": calls,
            "costs": summaries, "separate_call_count_observations": observations,
            "thread_cpu_median_reduction_percent": 100. * (
                1. - summaries["candidate"]["thread_cpu_us"]["p50"]
                / summaries["baseline"]["thread_cpu_us"]["p50"]
            ),
            "both_variants_preserve_no_grant_no_transfer_and_existing_agents": True,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=9)
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or not 2 <= args.blocks <= 100 or not 1 <= args.calls <= 1000
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("use fresh output, bounded calls, nice19 and hidden CUDA")
    baseline, pins = baseline_type(args.baseline)
    paths = [
        Path(__file__), ROOT / "beliefkv/runtime/reference_tool_replacement.py",
        ROOT / "beliefkv/runtime/predicted_tool_offload.py",
        ROOT / "tests/test_full_only_native_lifecycle.py",
        ROOT / "tests/test_tool_policy_adaptation.py",
        ROOT / "tests/test_reference_tool_replacement.py",
        ROOT / "tests/test_reference_residency_pause.py",
        args.engine / "python/sglang/srt/mem_cache/unified_radix_cache.py",
        args.engine / "python/sglang/srt/mem_cache/radix_cache.py",
    ]
    pins.update({str(path.resolve()): sha256_file(path) for path in paths})
    cases = [
        measure(args.engine, baseline, case=case, blocks=args.blocks, calls=args.calls)
        for case in ("impossible_logical_budget", "logical_fit_prediction_unsupported")
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a replacement CPU benchmark source changed")
    write(args.output, {
        "cases": cases, "inputs_sha256": pins,
        "semantics": [
            "Real native CPU radix/allocator fixtures; transfer queues and GPU operations are stand-ins.",
            "The baseline is the frozen replacement class; shared runtime and demand safety paths are current.",
            "Both cases intentionally end without replacement; successful actions are covered by regression tests.",
            "The unsupported-prediction case explicitly supplies an unavailable tool forecast.",
            "Prefix-call counting is separate from timing; setup, assertions and source loading are not timed.",
            "Only local check cost is measured, not whole-controller, GPU, full-run or JCT performance.",
            "The frozen queued GPU candidate is not changed or requalified by this benchmark.",
        ],
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps(cases), flush=True)


if __name__ == "__main__":
    main()
