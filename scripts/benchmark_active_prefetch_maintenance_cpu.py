#!/usr/bin/env python3
"""Compare active restore-receipt maintenance using pinned CPU runtime code."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pickle
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import synthetic_runtime
from scripts.benchmark_prefetch_control_cpu import distribution


def observed_bursts(path):
    live, kinds = {}, Counter()
    totals, same_context = Counter(), Counter()
    missing = maximum = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            row = json.loads(line)
            if row.get("event") == "prefetch_residency_registered":
                kinds[row["source"]] += 1
                live[row["command_id"]] = row["context_id"]
                counts = Counter(live.values())
                totals[len(live)] += 1
                same_context[counts[row["context_id"]]] += 1
                maximum = max(maximum, max(counts.values(), default=0))
            elif row.get("event") == "prefetch_residency_released":
                missing += live.pop(row["command_id"], None) is None
    return {
        "path": str(path.resolve()), "sha256": digest.hexdigest(),
        "registrations_by_source": dict(kinds),
        "live_receipts_after_registration_histogram": dict(sorted(totals.items())),
        "same_context_receipts_after_registration_histogram": dict(sorted(same_context.items())),
        "maximum_same_context_receipts": maximum,
        "unmatched_releases": missing, "unreleased_receipts": len(live),
        "scope": "registration-event counts, not time or maintenance-frequency weighting",
    }


def fixture(cls, *, contexts, receipts, phase, diagnostics, profiling):
    runtime, queue = synthetic_runtime(cls, CausalFrontierScheduler, 24, 4)
    runtime._tool_timing_only = True
    runtime.enable_tool_prefetch = runtime.enable_final_stage_prefetch = True
    runtime.enable_prepare_host = True
    runtime._tool_refresh_next_ms = 1e100
    runtime._join_prepare_next_ms = runtime._tool_prepare_next_ms = 1e100
    runtime._prediction_maintenance_interval_ms = 100.
    runtime._hotpath_timing.enabled = profiling
    nodes, generations, initial_leases, initial_locks, consumers = {}, {}, {}, {}, []
    records, releases = [], []
    lease_cls = cls._refresh_prefetch_service_leases.__globals__["_PrefetchServiceLease"]
    for group in range(contexts):
        wid, iid = f"wf-{group}", f"wf-{group}-root"
        cid, session = f"ctx-{iid}", f"session-root-{group}"
        key = PrefillCandidateKey(f"old-{group}", wid, iid, cid, 0, 0, session, 1)
        generations[session] = 1
        runtime.context_sessions[cid] = key
        parent = runtime.graph.invocations[iid]
        parent.state = InvocationState.WAIT_JOIN if group == 0 else InvocationState.WAIT_TOOL
        if phase in ("ready_scan", "epoch_handoff"):
            parent.state = InvocationState.READY
        if phase in ("consumer_scan", "first_service"):
            parent.state = InvocationState.RUNNING_LLM
            runtime.graph.contexts[cid].epoch = 1
            consumer = replace(key, request_id=f"consumer-{group}", context_epoch=1)
            runtime.context_sessions[cid] = runtime.visible[consumer.request_id] = consumer
            consumers.append(NS(
                rid=consumer.request_id, session_id=session, session_generation=1,
                cache_request_handle=NS(attempt_id=0),
                beliefkv_metadata={
                    "root_workflow_id": wid, "invocation_id": iid, "context_id": cid,
                    "context_epoch": 1, "parent_invocation_id": None,
                }, origin_input_ids=(1,), output_ids=(2,), finished=lambda: False,
            ))
        if phase == "epoch_handoff":
            runtime.graph.contexts[cid].epoch = 1
            runtime.context_sessions.pop(cid)
        for offset in range(receipts):
            node_id = 1 + group * receipts + offset
            command = f"restore-{node_id}"
            deepest = offset == receipts - 1
            nodes[node_id] = NS(
                id=node_id, creation_time=float(node_id),
                component_data={0: NS(value=object()), 2: NS(value=object() if deepest else None)},
            )
            receipt = NS(node_id=node_id) if deepest else None
            if receipt is not None:
                initial_locks[node_id] = 1
            initial_leases[command] = lease_cls(
                key, command, node_id, float(node_id),
                "join_ticket" if group == 0 else "tool_wait", None,
                999., 1001., (("kv", 2560), ("mamba", 30)) if deepest else (("kv", 2560),),
                lock_params=receipt, protected_bytes=receipts * 2560 + 30 if deepest else 0,
            )
    lock_refs = dict(initial_locks)

    def release(node_id, receipt):
        if receipt.node_id != node_id or lock_refs[node_id] != 1:
            raise AssertionError("native lock receipt mismatch or repeated release")
        lock_refs[node_id] = 0
        releases.append((node_id, receipt.node_id))

    runtime.attach_native_cache(NS(
        tree_core=NS(node_by_id=nodes.__getitem__, dec_lock_ref=release),
        session_refs=NS(_session_generations=generations, _closed_session_ids=set()),
    ))
    runtime._context_sessions_revision += contexts
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None) if diagnostics else None
    initial_visible = dict(runtime.visible)
    selected = NS(reqs=consumers if phase == "first_service" else queue[:8])
    if phase == "served_decode":
        lease_contexts = {lease.key.context_id for lease in initial_leases.values()}
        selected = NS(
            reqs=[
                req for req in queue
                if runtime.visible[req.rid].context_id not in lease_contexts
            ][:8],
            forward_mode=NS(is_decode=lambda: True, is_extend=lambda: False),
        )
        if len(selected.reqs) != 8:
            raise AssertionError("served-decode fixture needs eight unrelated running requests")

    def reset():
        runtime._prefetch_service_leases = dict(initial_leases)
        runtime._lease_refresh_signature = None
        runtime._lease_refresh_next_ms = 0.
        runtime.visible = dict(initial_visible)
        runtime.counts.clear()
        records.clear()
        releases.clear()
        lock_refs.clear()
        lock_refs.update(initial_locks)
        if phase == "deferred_scan":
            runtime._refresh_prefetch_service_leases()
            runtime.counts.clear()
            records.clear()
        elif phase == "served_decode":
            runtime.on_batch_completed(NS(
                reqs=selected.reqs,
                forward_mode=NS(is_decode=lambda: False, is_extend=lambda: True),
            ))
            runtime._refresh_prefetch_service_leases()
            runtime.counts.clear()
            records.clear()

    def run():
        if phase in ("first_service", "served_decode"):
            runtime.on_batch_completed(selected)
        elif phase == "scheduler_cycle":
            runtime.scheduler_step(defer_observations=True)
            runtime.dispatch_after_forward(running_batch=selected)
            runtime.on_batch_completed(selected)
        else:
            runtime._refresh_prefetch_service_leases()

    def state():
        leases = {}
        for command, lease in runtime._prefetch_service_leases.items():
            data = asdict(lease)
            data["lock_params"] = (
                vars(data["lock_params"]) if data["lock_params"] is not None else None
            )
            leases[command] = data
        return {
            "leases": leases, "counts": dict(runtime.counts), "records": records,
            "native_releases": releases, "lock_refs": dict(lock_refs),
            "physical_disabled": runtime.physical_disabled,
            "pending_commands": runtime.physical_ledger.pending_count,
            "visible": {rid: asdict(key) for rid, key in runtime.visible.items()},
            "semantic_revision": runtime.semantic_revision,
            "refresh_signature": runtime._lease_refresh_signature,
            "next_refresh_ms": runtime._lease_refresh_next_ms,
        }

    return runtime, reset, run, state


def measure(baseline, *, samples, contexts, receipts, phase, diagnostics, profiling):
    fixtures = {
        name: fixture(cls, contexts=contexts, receipts=receipts, phase=phase,
                      diagnostics=diagnostics, profiling=profiling)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    costs = {name: {"thread_cpu_us": [], "wall_us": []} for name in fixtures}
    storage = {}
    for name, (runtime, _, _, _) in fixtures.items():
        lease = next(iter(runtime._prefetch_service_leases.values()), None)
        if lease is None:
            # The fixture installs its immutable receipts in reset().
            fixtures[name][1]()
            lease = next(iter(runtime._prefetch_service_leases.values()), None)
        if lease is None:
            storage[name] = {"receipts": 0}
            continue
        instance_dict = getattr(lease, "__dict__", None)
        storage[name] = {
            "instance_bytes": sys.getsizeof(lease),
            "instance_dict_bytes": (
                sys.getsizeof(instance_dict) if instance_dict is not None else 0
            ),
            "excludes_referenced_field_objects": True,
        }
        for convert in (
            copy.copy, copy.deepcopy, lambda item: pickle.loads(pickle.dumps(item)),
            lambda item: replace(item),
        ):
            restored = convert(lease)
            if type(restored) is not type(lease) or asdict(restored) != asdict(lease):
                raise AssertionError("restore receipt copy/serialization changed")
    calls = 100 if phase in ("deferred_scan", "served_decode") else 1
    canonical = None
    try:
        for sample in range(samples + 20):
            outputs = {}
            order = list(fixtures)
            if sample % 2:
                order.reverse()
            for name in order:
                _, reset, run, state = fixtures[name]
                reset()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                for _ in range(calls):
                    run()
                elapsed_wall = (time.perf_counter_ns() - wall) / calls / 1000.
                elapsed_cpu = (time.thread_time_ns() - cpu) / calls / 1000.
                if sample >= 20:
                    costs[name]["thread_cpu_us"].append(elapsed_cpu)
                    costs[name]["wall_us"].append(elapsed_wall)
                outputs[name] = state()
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError((contexts, receipts, phase, outputs))
            expected = 0 if phase == "first_service" else contexts * receipts
            if len(outputs["candidate"]["leases"]) != expected:
                raise AssertionError("CPU fixture lost expected restore receipts")
            if outputs["candidate"]["physical_disabled"] or outputs["candidate"]["pending_commands"]:
                raise AssertionError("CPU maintenance failed or issued a transfer")
            canonical = json.dumps(outputs["candidate"], sort_keys=True)
        return {
            "contexts": contexts, "receipts_per_context": receipts,
            "total_receipts": contexts * receipts, "phase": phase,
            "diagnostics_writer_fixture": diagnostics, "profiling": profiling,
            "calls_per_sample": calls, "state_records_receipts_and_locks_equal": True,
            "canonical_output_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "record_storage_by_arm": storage,
            "four_copy_and_serialization_roundtrips_equal": True,
            "costs": {
                name: {clock: distribution(values) for clock, values in clocks.items()}
                for name, clocks in costs.items()
            },
            "raw_samples": costs,
        }
    finally:
        for runtime, *_ in fixtures.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="4d389de")
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--served-decode-only", action="store_true")
    parser.add_argument("--observed-opportunities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a fresh report are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py", "beliefkv/runtime/event_channel.py",
        "beliefkv/runtime/sglang_v0520_observer.py", "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/hotpath_timing.py", "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_native_shared_path.py", "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_active_prefetch_maintenance_cpu.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, digests = baseline_runtime(args.baseline)
    observed = observed_bursts(args.observed_opportunities)
    phases = (
        "waiting_scan", "ready_scan", "epoch_handoff", "consumer_scan",
        "first_service", "deferred_scan", "scheduler_cycle",
    )
    with patch("time.monotonic", lambda: 1000.), patch("time.time", lambda: 1000.1):
        if args.served_decode_only:
            cases = [
                measure(
                    baseline, samples=args.samples, contexts=contexts, receipts=receipts,
                    phase="served_decode", diagnostics=diagnostics, profiling=False,
                )
                for contexts, receipts in (
                    (1, 0), (1, 1), (1, 8), (1, 42),
                    (2, 42), (4, 42), (4, 128),
                )
                for diagnostics in (False, True)
            ]
        else:
            cases = [
                measure(baseline, samples=args.samples, contexts=contexts, receipts=receipts,
                        phase=phase, diagnostics=diagnostics, profiling=profiling)
                for contexts in (1, 2) for receipts in (1, 8, 42) for phase in phases
                for diagnostics in (False, True)
                for profiling in ((False, True) if phase == "scheduler_cycle" else (False,))
            ]
    for case in cases:
        case["p50_thread_cpu_reduction_fraction"] = 1. - (
            case["costs"]["candidate"]["thread_cpu_us"]["p50_us"]
            / case["costs"]["baseline"]["thread_cpu_us"]["p50_us"]
        )
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("measured source changed")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "scope": "CPU active receipt maintenance and runtime cycles; no native allocator, DMA or JCT",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": digests,
        "candidate_source_sha256": sources, "samples": args.samples, "warmup_samples": 20,
        "observed_bursts": observed, "cases": cases,
        "limits": [
            "Real trace counts describe registrations, not frequency-weighted maintenance populations.",
            "Synthetic receipt populations are reported separately from observed registration counts; no time weighting is inferred.",
            "Native fields and single deepest-lock ownership use CPU doubles, not the real allocator.",
            "Reset, output comparisons, construction and trace reading are outside timing.",
            "Active scans are deliberately due; deferred calls and first service are separate scenes.",
            "A writer fixture appends records; file serialization and background I/O are absent.",
            "Fixed monotonic/wall clocks use plain functions, not counted mocks.",
            "The runtime cycle excludes the rest of native scheduler selection and GPU execution.",
            "Served decode cases prime a completed prefill for eight unrelated requests and defer receipt maintenance before timing.",
            "No synthetic population or measured microseconds are extrapolated to throughput/JCT.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "cases": len(cases),
        "lower_p50_cases": sum(case["p50_thread_cpu_reduction_fraction"] > 0 for case in cases),
        "p50_reduction_range": (
            min(case["p50_thread_cpu_reduction_fraction"] for case in cases),
            max(case["p50_thread_cpu_reduction_fraction"] for case in cases),
        ),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
