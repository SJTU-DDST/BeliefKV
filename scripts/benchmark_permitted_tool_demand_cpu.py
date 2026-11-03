"""Measure one donor's demand check using matched native CPU fixtures."""

import argparse
from contextlib import ExitStack
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
from beliefkv.runtime.predicted_tool_offload import DemandAwareToolOffload
from beliefkv.runtime.reference_tool_replacement import ReferenceToolReplacement
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write


def baseline_types(checkout):
    classes, pins = {}, {}
    for name in ("reference_tool_replacement", "predicted_tool_offload"):
        path = checkout / "beliefkv/runtime" / (name + ".py")
        namespace = {"__name__": "frozen_" + name}
        exec(compile(path.read_bytes(), str(path), "exec"), namespace)
        classes.update({name: namespace})
        pins[str(path)] = sha256_file(path)
    return (
        classes["reference_tool_replacement"]["ReferenceToolReplacement"],
        classes["predicted_tool_offload"]["DemandAwareToolOffload"]._demand, pins,
    )


def measure(args):
    import pytest
    from tests.test_full_only_native_lifecycle import full_native
    from tests.test_reference_tool_replacement import pair
    from tests.test_tool_policy_adaptation import demand_runtime

    baseline, baseline_demand, pins = baseline_types(args.baseline)
    for name in (
        __file__, "beliefkv/runtime/reference_tool_replacement.py",
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "tests/test_full_only_native_lifecycle.py", "tests/test_reference_tool_replacement.py",
        "tests/test_tool_policy_adaptation.py", "tests/test_reference_residency_pause.py",
    ):
        path = ROOT / name
        pins[str(path)] = sha256_file(path)
    for name in ("unified_radix_cache.py", "unified_cache/unified_tree_core.py"):
        path = args.engine / "python/sglang/srt/mem_cache" / name
        pins[str(path)] = sha256_file(path)
    with ExitStack() as stack:
        monkeypatch = stack.enter_context(pytest.MonkeyPatch.context())
        monkeypatch.setenv("SGLANG_SOURCE_CHECKOUT", str(args.engine))
        fixture = demand_runtime.__wrapped__(full_native.__wrapped__(monkeypatch, NS(param=64)))
        parts = next(fixture)
        stack.callback(fixture.close)
        runtime, admission, controller, adder = pair(parts)
        runtime._observe_native_admission_capacity(running_batch=NS(reqs=[]), adder=adder)
        controller._admission_adder = adder
        request, key, donor = parts[5], admission.latest["consumer"], admission.latest["tool"]
        variants = {
            "baseline": (baseline(controller), baseline_demand),
            "candidate": (ReferenceToolReplacement(controller), DemandAwareToolOffload._demand),
        }
        for replacement, _ in variants.values():
            replacement.consumer, replacement.donor = (request, key), donor
        expected = None
        counts, timings = {}, {name: [] for name in variants}
        for name, (replacement, demand) in variants.items():
            controller.replacement = replacement
            counter = Mock(wraps=replacement.demand)
            with patch.object(replacement, "demand", counter):
                values = [demand(controller, request, key, donor) for _ in range(10)]
            if values[0] is None or any(value != values[0] for value in values):
                raise ValueError("the bounded positive-demand fixture must remain unchanged")
            if expected is not None and expected != values[0]:
                raise ValueError("the two implementations disagree about current demand")
            expected = values[0]
            counts[name] = {"demand_observations_in_ten_calls": counter.call_count}
        for block in range(args.blocks):
            names = list(variants)
            if block % 2:
                names.reverse()
            for name in names:
                replacement, demand = variants[name]
                controller.replacement = replacement
                started = time.thread_time_ns()
                for _ in range(args.calls):
                    demand(controller, request, key, donor)
                timings[name].append((time.thread_time_ns() - started) / args.calls / 1000.)
                if (
                    parts[4].component_data[0].value is None
                    or admission.policy.programs["consumer"].active
                    or not admission.policy.programs["tool"].active
                    or runtime.physical_ledger.pending_count
                ):
                    raise ValueError("a read-only demand benchmark changed physical or agent state")
        summaries = {name: distribution(values) for name, values in timings.items()}
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("a bounded demand benchmark source changed")
        return {
            "alternating_blocks": args.blocks, "calls_per_block": args.calls,
            "thread_CPU_us_per_call": summaries, "separate_call_count_observations": counts,
            "median_CPU_reduction_percent": 100. * (
                1. - summaries["candidate"]["p50"] / summaries["baseline"]["p50"]
            ),
            "same_consumer_demand_and_no_physical_or_admission_actions": True,
            "scope": "Native CPU radix/allocator; GPU/copy stand-ins. One demand call, not full control or JCT.",
            "baseline_reference_and_demand_methods_both_loaded_from_frozen_checkout": True,
            "inputs_sha256": pins, "GPU_action": False, "performance_accepted": False,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "baseline", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=9)
    parser.add_argument("--calls", type=int, default=500)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or args.output.exists() or not 2 <= args.blocks <= 100 or not 1 <= args.calls <= 1000
    ):
        raise ValueError("use hidden CUDA, nice19, bounded calls and a new evidence output")
    result = measure(args)
    write(args.output, result)
    print(json.dumps(result), flush=True)
