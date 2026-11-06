"""Compare eviction authorization on the same native CPU tree and demand."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import MethodType
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.predicted_tool_offload import DemandAwareToolOffload
from scripts.audit_native_system_costs import distribution
from scripts.benchmark_tool_offload_refresh_cpu import native_pair
from scripts.train_workflow_work import write
from tests.test_tool_predictive_transfers import event


def baseline_method(revision):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", revision + "^{commit}"], cwd=ROOT, text=True,
    ).strip()
    source = subprocess.check_output([
        "git", "show", commit + ":beliefkv/runtime/predicted_tool_offload.py",
    ], cwd=ROOT)
    owner = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "DemandAwareToolOffload"
    )
    method = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "_before_eviction"
    )
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), commit, "exec"), namespace)
    return namespace["_before_eviction"], {
        "commit": commit, "file_sha256": hashlib.sha256(source).hexdigest(),
        "method_AST_sha256": hashlib.sha256(ast.dump(method).encode()).hexdigest(),
    }


def measure(engine, old, case, *, blocks, calls):
    with native_pair(engine) as (paired, parts, _native, _monkeypatch):
        runtime, admission, controller, adder = paired
        donor, request = parts[4:6]
        key = admission.latest["tool"]
        controller.consumer = request, runtime.visible[request.rid]
        controller.replacement.consumer = controller.consumer
        controller.replacement.donor = key
        controller._admission_adder = adder
        if case == "consumer_removed":
            runtime._capacity_waiting_queue = []
        elif case == "tool_returned":
            runtime.on_events((event(
                10, Kind.TOOL_END, invocation_id="tool",
                attributes={"tool_run_id": "long"},
            ),))
        elif case == "stale_consumer":
            request.cache_request_handle.attempt_id += 1
        expected = case == "live_capacity_stalled_consumer"
        variants = {
            "baseline": MethodType(old, controller),
            "candidate": controller._before_eviction,
        }
        counts, samples = {}, {name: [] for name in variants}
        for name, check in variants.items():
            live = Mock(wraps=controller._live_consumer)
            with patch.object(controller, "_live_consumer", live):
                if check(key, donor) is not expected:
                    raise ValueError("eviction authorization changed: " + case)
            counts[name] = live.call_count
        for block in range(blocks):
            names = list(variants)
            if block % 2:
                names.reverse()
            for name in names:
                started = time.thread_time_ns()
                for _ in range(calls):
                    if variants[name](key, donor) is not expected:
                        raise ValueError("eviction authorization changed during measurement")
                samples[name].append((time.thread_time_ns() - started) / calls / 1000.)
            if (
                parts[3].available_size() != 4
                or donor.component_data[0].value is None
                or donor.component_data[0].host_value is None
                or runtime.physical_ledger.pending_count
            ):
                raise ValueError("authorization-only probe changed physical state")
        costs = {name: distribution(values) for name, values in samples.items()}
        return {
            "case": case, "authorization_allowed": expected, "same_authorization": True,
            "live_consumer_checks_per_call": counts, "thread_CPU_us_per_call": costs,
            "median_CPU_reduction_percent": 100. * (
                1. - costs["candidate"]["p50"] / costs["baseline"]["p50"]
            ),
            "physical_state_conserved": True,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=15)
    parser.add_argument("--calls", type=int, default=100)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19 or args.output.exists()
        or not 3 <= args.blocks <= 30 or not 10 <= args.calls <= 500
    ):
        parser.error("use nice19, hidden CUDA, bounded work and a fresh output")
    old, baseline = baseline_method(args.baseline_revision)
    paths = [
        Path(__file__), ROOT / "beliefkv/runtime/predicted_tool_offload.py",
        ROOT / "beliefkv/runtime/reference_tool_replacement.py",
        ROOT / "scripts/benchmark_tool_offload_refresh_cpu.py",
        ROOT / "tests/test_tool_policy_adaptation.py",
        ROOT / "tests/test_reference_tool_replacement.py",
        ROOT / "tests/test_full_only_native_lifecycle.py",
    ]
    pins = {str(path): sha256_file(path) for path in paths}
    result = {
        "baseline": baseline, "inputs_sha256": pins,
        "engine_checkout": str(args.engine.resolve()),
        "CPU_affinity": sorted(os.sched_getaffinity(0)),
        "blocks": args.blocks, "calls_per_block": args.calls,
        "rows": [
            measure(args.engine, old, case, blocks=args.blocks, calls=args.calls)
            for case in (
                "live_capacity_stalled_consumer", "consumer_removed",
                "tool_returned", "stale_consumer",
            )
        ],
        "scope": (
            "Same native CPU tree, allocator and live demand; copy/event stand-ins. "
            "Only the old authorization method is substituted. No GPU, full-control "
            "cost or workflow JCT claim."
        ),
        "GPU_action": False,
    }
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("benchmark sources changed")
    write(args.output, result)
    print(json.dumps({"rows": result["rows"], "GPU_action": False}))


if __name__ == "__main__":
    main()
