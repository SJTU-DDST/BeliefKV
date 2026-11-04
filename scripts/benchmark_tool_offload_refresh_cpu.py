"""Measure bounded tool-offload controls on native CPU trees without GPU work."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import replace
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
from scripts.benchmark_tool_replacement_control_cpu import baseline_type
from scripts.train_workflow_work import write


@contextmanager
def native_pair(engine):
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
        paired = pair(parts)
        paired[0]._opportunity_writer = None
        yield paired, parts, native, monkeypatch


def attempt(paired, replacement):
    runtime, _admission, controller, adder = paired
    controller.replacement = replacement
    replacement.next_ms = 0.
    return replacement.run(
        runtime._capacity_waiting_queue, running_batch=NS(reqs=[]), adder=adder,
    )


def verify_no_action(paired, parts, native):
    runtime, admission, _controller, _adder = paired
    if (
        parts[3].available_size() != 4 or parts[4].component_data[0].value is None
        or admission.policy.programs["consumer"].active
        or not admission.policy.programs["tool"].active
        or not admission.policy.programs["hot"].active
        or runtime.physical_ledger.pending_count or native[2] or native[3]
    ):
        raise ValueError("a failed control probe changed physical or execution state")


def failed_pair(engine, baseline, case, *, blocks, calls):
    with native_pair(engine) as (paired, parts, native, monkeypatch):
        runtime, admission, controller, _adder = paired
        if case == "impossible_logical_budget":
            admission.policy.programs["consumer"].tokens = 100
        elif case == "unsupported_forecast":
            monkeypatch.setattr(runtime, "_tool_offload_remaining", lambda *a, **k: None)
        elif case == "protected_donor":
            admission.policy.programs["tool"].pending = True
        elif case == "locked_backed_donor":
            parts[4].component_data[0].lock_ref = 1
        elif case == "unbacked_donor":
            parts[4].component_data[0].host_value = None
            parts[2].tree_core._update_duplicate_tracking(parts[4])
            monkeypatch.setattr(runtime, "issue_shadow_backup_steps", Mock(return_value=()))
        else:
            raise ValueError("unknown no-action control case")
        variants = {"baseline": baseline(controller), "candidate": ReferenceToolReplacement(controller)}
        counts, samples = {}, {name: [] for name in variants}
        for name, replacement in variants.items():
            reentry = Mock(wraps=runtime._read_request_reentry)
            capture = Mock(wraps=runtime._refreshed_shadow_backup_selection)
            with (
                patch.object(runtime, "_read_request_reentry", reentry),
                patch.object(runtime, "_refreshed_shadow_backup_selection", capture),
            ):
                for _ in range(10):
                    if attempt(paired, replacement):
                        raise ValueError("a no-action fixture unexpectedly granted a consumer")
            counts[name] = {
                "consumer_reentry_calls_in_10_attempts": reentry.call_count,
                "shadow_refresh_calls_in_10_attempts": capture.call_count,
            }
            verify_no_action(paired, parts, native)
        for block in range(blocks):
            names = list(variants)
            if block % 2:
                names.reverse()
            for name in names:
                started = time.thread_time_ns()
                for _ in range(calls):
                    if attempt(paired, variants[name]):
                        raise ValueError("a failed-pair benchmark granted a consumer")
                samples[name].append((time.thread_time_ns() - started) / calls / 1000.)
                verify_no_action(paired, parts, native)
        costs = {name: distribution(values) for name, values in samples.items()}
        return {
            "case": case, "thread_CPU_us_per_attempt": costs, "counting_outside_timing": counts,
            "median_CPU_reduction_percent": 100. * (1. - costs["candidate"]["p50"] / costs["baseline"]["p50"]),
            "physical_and_agent_state_conserved": True,
        }


def settled_pair(engine, baseline, *, blocks, calls):
    variants = {"baseline": baseline, "candidate": ReferenceToolReplacement}
    samples, counts = {name: [] for name in variants}, {}

    def once(cls, *, count):
        with native_pair(engine) as (paired, parts, native, _monkeypatch):
            runtime, admission, controller, _adder = paired
            capture = Mock(wraps=runtime._refreshed_shadow_backup_selection)
            replacement = cls(controller)
            with patch.object(runtime, "_refreshed_shadow_backup_selection", capture) if count else ExitStack():
                started = time.thread_time_ns()
                granted = attempt(paired, replacement)
                elapsed = time.thread_time_ns() - started
            if (
                not granted or parts[3].available_size() != 36
                or parts[4].component_data[0].value is not None
                or parts[4].component_data[0].host_value is None
                or not admission.policy.programs["consumer"].active
                or admission.policy.programs["tool"].active
                or not admission.policy.programs["hot"].active
                or runtime.physical_ledger.pending_count or native[2] or native[3]
                or controller.node_cursor != 2 * controller.max_nodes
            ):
                raise ValueError("the settled variants did not perform the same safe replacement")
            return elapsed, capture.call_count

    for name, cls in variants.items():
        counts[name] = {"shadow_refresh_calls_in_10_attempts": sum(
            once(cls, count=True)[1] for _ in range(10)
        )}
    for block in range(blocks):
        names = list(variants)
        if block % 2:
            names.reverse()
        for name in names:
            total = sum(once(variants[name], count=False)[0] for _ in range(calls))
            samples[name].append(total / calls / 1000.)
    costs = {name: distribution(values) for name, values in samples.items()}
    return {
        "case": "settled_private_complete_pair", "thread_CPU_us_per_attempt": costs,
        "counting_outside_timing": counts,
        "median_CPU_reduction_percent": 100. * (1. - costs["candidate"]["p50"] / costs["baseline"]["p50"]),
        "same_actual_release_admission_and_cursor": True,
        "fixture_construction_cleanup_and_assertions_outside_timing": True,
    }


def refresh_stream(engine, *, blocks, spacing_ms, refreshes=32):
    samples, choices = {"unconditional_rearm": [], "bounded_refresh": []}, {}
    for block in range(blocks):
        names = list(samples)
        if block % 2:
            names.reverse()
        for name in names:
            with native_pair(engine) as (paired, parts, native, monkeypatch):
                runtime, admission, controller, adder = paired
                previous = next(iter(runtime.tool_wait_hints.values()))
                clock = [previous.issued_monotonic_ms]
                monkeypatch.setattr("time.monotonic", lambda: clock[0] / 1000.)
                monkeypatch.setattr(runtime, "_tool_offload_remaining", lambda *a, **k: None)
                # Both variants use the same new replacement implementation.
                running = NS(reqs=[])
                started = time.thread_time_ns()
                for _ in range(refreshes):
                    clock[0] += spacing_ms
                    hint = replace(
                        previous, issued_monotonic_ms=clock[0],
                        expires_monotonic_ms=clock[0] + 5000.,
                    )
                    if name == "unconditional_rearm":
                        controller.rearm()
                    else:
                        controller.hint_updated(previous, hint, now_ms=clock[0])
                    runtime.tool_wait_hints[hint.key.context_id] = hint
                    if controller.before_agent_admission(
                        runtime._capacity_waiting_queue, running_batch=running, adder=adder,
                    ):
                        raise ValueError("an unsupported forecast granted a consumer")
                    previous = hint
                samples[name].append((time.thread_time_ns() - started) / 1000.)
                count = admission.counts["tool_event_replacement_choices"]
                if name in choices and choices[name] != count:
                    raise ValueError("the deterministic advisory stream changed its control count")
                choices[name] = count
                verify_no_action(paired, parts, native)
    return {
        "case": "same_wait_refresh_stream", "advisory_spacing_ms": spacing_ms,
        "refreshes": refreshes, "thread_CPU_us_per_complete_stream": {
            name: distribution(values) for name, values in samples.items()
        }, "actual_replacement_attempts": choices,
        "scope": "Synthetic control cadence; no predictor execution, real workload frequency, GPU, or JCT claim.",
        "replacement_implementation_shared_between_both_wake_policies": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "baseline", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=9)
    parser.add_argument("--calls", type=int, default=30)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or args.output.exists() or not 2 <= args.blocks <= 30 or not 1 <= args.calls <= 100
    ):
        raise ValueError("use hidden CUDA, nice19, bounded work and a new output")
    baseline, pins = baseline_type(args.baseline)
    paths = [
        Path(__file__), ROOT / "beliefkv/runtime/reference_tool_replacement.py",
        ROOT / "beliefkv/runtime/predicted_tool_offload.py", ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
        ROOT / "scripts/benchmark_tool_replacement_control_cpu.py",
        ROOT / "tests/test_full_only_native_lifecycle.py", ROOT / "tests/test_reference_tool_replacement.py",
        ROOT / "tests/test_reference_residency_pause.py", ROOT / "tests/test_tool_policy_adaptation.py",
        args.engine / "python/sglang/srt/mem_cache/unified_radix_cache.py",
        args.engine / "python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py",
    ]
    pins.update({str(path.resolve()): sha256_file(path) for path in paths})
    cases = [
        failed_pair(args.engine, baseline, case, blocks=args.blocks, calls=args.calls)
        for case in (
            "impossible_logical_budget", "unsupported_forecast", "protected_donor",
            "locked_backed_donor", "unbacked_donor",
        )
    ]
    cases.append(settled_pair(args.engine, baseline, blocks=args.blocks, calls=args.calls))
    streams = [refresh_stream(args.engine, blocks=args.blocks, spacing_ms=spacing) for spacing in (50., 150., 1000.)]
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a benchmark source changed during measurement")
    report = {
        "alternating_blocks": args.blocks, "calls_per_block": args.calls,
        "cases": cases, "refresh_streams": streams, "inputs_sha256": pins,
        "native_CPU_radix_and_allocator_with_GPU_and_copy_standins": True,
        "baseline_replacement_loaded_from_frozen_checkout_shared_other_runtime": True,
        "GPU_action": False, "performance_accepted": False,
    }
    write(args.output, report)
    print(json.dumps({"output": str(args.output), "cases": cases, "refresh_streams": streams}), flush=True)


if __name__ == "__main__":
    main()
