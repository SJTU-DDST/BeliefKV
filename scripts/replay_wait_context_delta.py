#!/usr/bin/env python3
"""Replay recorded workflow events with explicit proxy native-session bindings."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import heapq
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.evaluate_transfer_service_history import read_prefix, summary


def proxy_binding(runtime, event):
    if event.kind is not RuntimeEventKind.LLM_SUBMIT:
        return False
    invocation = runtime.graph.invocations.get(event.invocation_id)
    if invocation is None or invocation.state.terminal:
        return False
    context = runtime.graph.contexts[invocation.context_id]
    rid = event.attributes.get("request_id") or f"proxy:{event.event_id}"
    if rid in runtime.visible:
        return False
    previous = runtime.context_sessions.get(context.context_id)
    if previous is not None:
        runtime.visible.pop(previous.request_id, None)
        runtime._visible_since.pop(previous.request_id, None)
    request = NS(
        rid=rid, session_id=f"proxy:{context.context_id}",
        session_generation=context.epoch + 1,
        cache_request_handle=NS(attempt_id=0),
        beliefkv_metadata={
            "root_workflow_id": event.workflow_id,
            "invocation_id": invocation.invocation_id,
            "context_id": context.context_id, "context_epoch": context.epoch,
        },
    )
    return runtime.register_visible_request(request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", type=Path, required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior replay evidence")
    source_paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "tests/test_native_wait_candidates.py",
        "scripts/replay_wait_context_delta.py",
    )
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths
    }
    sealed_bytes = args.sealed.read_bytes()
    sealed = json.loads(gzip.decompress(sealed_bytes))
    sources = {
        name: metadata for name, metadata in sealed["input_jsonl_prefixes"].items()
        if name.endswith("/runtime_events.deepagents.jsonl")
    }
    if not sources:
        raise ValueError("no sealed workflow events")
    streams, heap = [], []
    for name, metadata in sorted(sources.items()):
        streams.append(iter(read_prefix(Path(name), metadata)))
        row = next(streams[-1], None)
        if row is not None:
            heapq.heappush(heap, (row["ts_ms"], len(streams) - 1, row))
    baseline, baseline_sources = baseline_runtime(args.baseline_revision)
    runtimes = {"baseline": baseline(), "candidate": NativeAdmissionRuntime()}
    states = (InvocationState.WAIT_JOIN, InvocationState.WAIT_TOOL)
    clock = [0.]
    event_cpu_ns, binding_cpu_ns = Counter(), Counter()
    query_cpu = {name: [] for name in runtimes}
    counts, errors = Counter(), Counter()
    maximum_contexts = Counter()
    started = datetime.now().astimezone().isoformat()
    try:
        with patch("time.monotonic", lambda: clock[0]):
            while heap:
                timestamp, stream_index, row = heapq.heappop(heap)
                clock[0] = timestamp / 1000.
                event = RuntimeEvent.from_dict(row)
                order = list(runtimes)
                if counts["events"] % 2:
                    order.reverse()
                outcomes = {}
                for name in order:
                    begin = time.thread_time_ns()
                    try:
                        runtimes[name].on_events((event,))
                        outcomes[name] = None
                    except Exception as error:
                        outcomes[name] = (type(error).__name__, str(error))
                    event_cpu_ns[name] += time.thread_time_ns() - begin
                if outcomes["baseline"] != outcomes["candidate"]:
                    raise AssertionError(f"event acceptance changed: {event.event_id}: {outcomes}")
                if outcomes["baseline"] is not None:
                    errors[f"{outcomes['baseline'][0]}:{event.kind.value}"] += 1
                else:
                    bindings = {}
                    for name in order:
                        runtime = runtimes[name]
                        if (
                            runtime._active_tool_census_version is None
                            and (invocation := runtime.graph.invocations.get(event.invocation_id)) is not None
                        ):
                            runtime._local_frontier_features(
                                invocation, runtime.graph.contexts[invocation.context_id].epoch,
                                now_ms=timestamp,
                            )
                        begin = time.thread_time_ns()
                        bindings[name] = proxy_binding(runtime, event)
                        binding_cpu_ns[name] += time.thread_time_ns() - begin
                    if bindings["baseline"] != bindings["candidate"]:
                        raise AssertionError("proxy binding acceptance changed")
                    counts["proxy_bindings"] += bindings["candidate"]
                for state in states:
                    candidates = {}
                    for name in order:
                        begin = time.thread_time_ns()
                        candidates[name] = runtimes[name]._waiting_context_keys(state)
                        query_cpu[name].append((time.thread_time_ns() - begin) / 1000.)
                    if candidates["baseline"] != candidates["candidate"]:
                        raise AssertionError(
                            f"candidate identity/order changed: {event.event_id}/{state.value}"
                        )
                    expected = tuple(
                        key for key in runtimes["candidate"].context_sessions.values()
                        if (invocation := runtimes["candidate"].graph.invocations.get(
                            key.invocation_id,
                        )) is not None and invocation.state is state
                    )
                    if state is InvocationState.WAIT_JOIN:
                        expected = tuple(sorted(expected, key=lambda key: key.context_id))
                    if candidates["candidate"] != expected:
                        raise AssertionError("cached membership differs from uncached oracle")
                    counts[f"identical_queries:{state.value}"] += 1
                    counts[f"nonempty_queries:{state.value}"] += bool(expected)
                for name, runtime in runtimes.items():
                    maximum_contexts[name] = max(maximum_contexts[name], len(runtime.context_sessions))
                counts["events"] += 1
                counts[f"event:{event.kind.value}"] += 1
                following = next(streams[stream_index], None)
                if following is not None:
                    heapq.heappush(heap, (following["ts_ms"], stream_index, following))
        if any(
            runtime.physical_ledger.pending_count or runtime.completed_physical_actions
            for runtime in runtimes.values()
        ):
            raise AssertionError("logical replay produced physical actions")
        if not counts["proxy_bindings"] or any(
            not counts[f"nonempty_queries:{state.value}"] for state in states
        ):
            raise AssertionError("empty proxy bindings or wait groups cannot establish equivalence")
        if runtimes["baseline"].context_sessions != runtimes["candidate"].context_sessions:
            raise AssertionError("proxy context identities changed")
        if any(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != wanted
            for name, wanted in source_hashes.items()
        ):
            raise ValueError("replay source changed during execution")
        cpu = {
            name: {
                "queries_us": summary(values),
                "query_total_ms": sum(values) / 1000.,
                "event_total_ms": event_cpu_ns[name] / 1_000_000.,
                "proxy_binding_total_ms": binding_cpu_ns[name] / 1_000_000.,
                "combined_total_ms": (
                    sum(values) / 1000.
                    + (event_cpu_ns[name] + binding_cpu_ns[name]) / 1_000_000.
                ),
            } for name, values in query_cpu.items()
        }
        report = {
            "status": "recorded_graph_proxy_binding_equivalence_not_native_action_or_gain",
            "started_at_local": started,
            "finished_at_local": datetime.now().astimezone().isoformat(),
            "baseline_revision": args.baseline_revision,
            "baseline_sources": baseline_sources,
            "sealed_report": str(args.sealed.resolve()),
            "sealed_report_sha256": hashlib.sha256(sealed_bytes).hexdigest(),
            "source_prefixes": sources,
            "counts": dict(counts), "matched_event_errors": dict(errors),
            "maximum_proxy_contexts": dict(maximum_contexts),
            "candidate_counts": {
                key: value for key, value in runtimes["candidate"].counts.items()
                if key.startswith("wait_context_")
            },
            "thread_cpu": cpu,
            "source_sha256": source_hashes,
            "limitations": [
                "Every sealed file retains its sequence; head timestamps interleave files without claiming actual cross-file delivery order.",
                "Original control-message batch boundaries are unknown; each recorded event is applied individually to both runtimes.",
                "Proxy bindings occur only after LLM submit, with deterministic generation and one visible proxy request per context.",
                "Native session generations, queue timings, allocator state and service completion order are not reconstructed.",
                "Both wait groups are queried after every event for equivalence; this is not their measured online call frequency.",
                "Unpinned alternating CPU timings include extra event/cache hooks and proxy-binding work, without a model worker or GPU.",
                "Tool census is initialized outside timing once per mirror; event maintenance exercises the shared affected set.",
                "Event rejection/reset counts remain visible and matched; no task quality, prefetch execution, JCT or throughput gain is inferred.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({
            "files": len(sources), "counts": counts, "matched_errors": errors,
            "candidate_counts": report["candidate_counts"], "thread_cpu": cpu,
            "output": str(args.output),
        }), flush=True)
    finally:
        for stream in streams:
            stream.close()
        for runtime in runtimes.values():
            runtime.close()


if __name__ == "__main__":
    main()
