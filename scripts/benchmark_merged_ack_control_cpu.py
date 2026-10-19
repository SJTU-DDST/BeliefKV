#!/usr/bin/env python3
"""Compare merged-ACK validation and complete runtime callbacks on a CPU."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import cProfile
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pstats
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_prefetch_control_cpu import distribution
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


def observed_shapes(path):
    digest, rows, missing, split = hashlib.sha256(), 0, 0, Counter()
    shapes = Counter()
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            row = json.loads(line)
            if row.get("status") != "completed":
                continue
            rows += 1
            receipts = row.get("tagged_child_commits")
            if receipts is None:
                missing += 1
                continue
            if type(receipts) is not list:
                raise ValueError("unexpected native tagged receipt schema")
            direction = row["direction"]
            shapes[direction, len(receipts), len(row["node_ids"])] += 1
            split[direction] += sum(
                item["published_node_ids"] != [item["anchor_node_id"]]
                for item in receipts
            )
    return {
        "path": str(path.resolve()), "sha256": digest.hexdigest(),
        "completed_rows": rows, "rows_without_tagged_child_field": missing,
        "split_publications_by_direction": dict(split),
        "ack_shape_histogram": [
            {"direction": direction, "tagged_receipts": children,
             "merged_nodes": nodes, "ack_rows": count}
            for (direction, children, nodes), count in sorted(shapes.items())
        ],
        "scope": "Completed ACK row populations; not pending-command counts or callback timing.",
    }


def ledger_state(ledger):
    return {
        "pending": [
            (command, asdict(expected), started, sorted(received))
            for command, (expected, started, received) in ledger._pending.items()
        ],
        "pending_nodes": sorted(ledger._pending_nodes),
        "pending_bytes": dict(ledger._pending_action_bytes),
        "pending_counts": dict(ledger._pending_action_counts),
        "pending_h2d_contexts": sorted(ledger._pending_h2d_contexts.items()),
        "invalidated": sorted(ledger._context_invalidated_commands),
        "history": list(ledger._history), "seen": sorted(ledger._seen),
        "published": ledger._published_by_command,
    }


def core_fixture(cls, *, direction, commands, width=1, extra=0, parts=1, split=False):
    obligations, stages, cache_nodes = [], [[] for _ in range(parts)], {}
    action = "PREFETCH_GPU" if direction == "h2d" else "PREPARE_HOST"
    geometry = (("kv", 10), ("mamba", 5)) if split else (("kv", 10),)
    for command in range(commands):
        children = []
        for part in range(parts):
            base = 1 + 100 * command + 10 * part
            published = tuple(range(base, base + width))
            anchor = base
            if split:
                published, anchor = (base, base + 1), base + 1
                parent = NS(
                    id=base, creation_time=float(base), parent=None,
                    component_data={0: NS(host_value=(base,)), 2: NS(host_value=None)},
                )
                cache_nodes[base] = parent
                cache_nodes[anchor] = NS(
                    id=anchor, creation_time=float(anchor), parent=parent,
                    component_data={0: NS(host_value=(anchor,)), 2: NS(host_value=anchor)},
                )
                child = PhysicalChildExpectation(
                    anchor, (anchor,), (("kv", 20), ("mamba", 5)), 25,
                    anchor_creation_time=float(anchor),
                    host_destination_indices=(("kv", published), ("mamba", (anchor,))),
                )
            else:
                child = PhysicalChildExpectation(
                    anchor, published, (("kv", width * 10),), width * 10,
                )
            children.append(child)
            stages[part].append(NS(
                command_id=f"command-{command}", anchor_node_id=anchor,
                published_node_ids=published,
                num_tokens_by_pool=tuple(
                    (pool, amount // dict(geometry)[pool]) for pool, amount in child.pool_bytes
                ), num_bytes=child.num_bytes,
            ))
        obligations.append(PhysicalActionExpectation(
            f"command-{command}", action, "ctx", 3, tuple(children), geometry,
        ))
    for index in range(extra):
        node_id = 1_000_000 + index
        obligations.append(PhysicalActionExpectation(
            f"other-{index}", action, "ctx", 3,
            (PhysicalChildExpectation(node_id, (node_id,), (("kv", 10),), 10),),
            (("kv", 10),),
        ))
    events = []
    for receipts in stages:
        counts = Counter()
        for receipt in receipts:
            counts.update(dict(receipt.num_tokens_by_pool))
        events.append(NS(
            status="completed", direction=direction, child_commits=tuple(receipts),
            node_ids=tuple(node for item in receipts for node in item.published_node_ids),
            num_tokens_by_pool=tuple(sorted(counts.items())),
            actual_bytes=sum(item.num_bytes for item in receipts),
        ))
    cache = NS(tree_core=NS(node_by_id=cache_nodes.get)) if split else None
    state = {}

    def reset():
        ledger = cls()
        for obligation in obligations:
            ledger.register(obligation)
        state["ledger"] = ledger

    def run():
        state["completed"] = [
            state["ledger"].observe(
                event, live_context_epochs={"ctx": 3}, native_cache=cache,
            )
            for event in events
        ]

    def signature():
        return {
            "completed": [[asdict(item) for item in batch] for batch in state["completed"]],
            **ledger_state(state["ledger"]),
        }

    return NS(reset=reset, run=run, signature=signature, close=lambda: None)


def runtime_fixture(cls, *, commands, diagnostics, source):
    runtime, key, cache, reads, _, rows, reset_dispatch, dispatch, _ = dispatch_fixture(
        cls, source=source, depth=commands, diagnostics=diagnostics, native_budget=True,
    )
    runtime._physical_command_namespace = "merged-ack-cpu"
    locks, acquired, released = {}, [], []

    def acquire(node_id):
        if node_id in locks:
            raise AssertionError("duplicate native lock")
        receipt = NS(node_id=node_id)
        locks[node_id] = receipt
        acquired.append(node_id)
        return NS(to_dec_params=lambda: receipt)

    def release(node_id, receipt):
        if locks.pop(node_id) is not receipt:
            raise AssertionError("native lock receipt changed")
        released.append(node_id)

    cache.tree_core.inc_lock_ref = acquire
    cache.tree_core.dec_lock_ref = release
    state = {}

    def reset():
        for command in tuple(runtime._prefetch_service_leases):
            runtime._release_prefetch_service_lease(command, "cpu_reset")
        if locks:
            raise AssertionError("previous native protection was not released")
        reset_dispatch()
        runtime.completed_physical_actions.clear()
        # Command identities are intentionally identical between the isolated ledgers.
        runtime._physical_command_sequence = iter(range(10000))
        acquired.clear()
        released.clear()
        for node_id in range(commands + 1):
            node = cache.tree_core.node_by_id(node_id)
            node.component_data[0].value = None
            node.component_data[2].value = None
        dispatch()
        if runtime.physical_ledger.pending_count != commands:
            raise AssertionError(("fixture did not submit every target", commands, dict(runtime.counts)))
        receipts, nodes, counts, total = [], [], Counter(), 0
        for expected, _, _ in runtime.physical_ledger._pending.values():
            for child in expected.children:
                sizes = dict(expected.pool_bytes_per_token)
                units = tuple((pool, size // sizes[pool]) for pool, size in child.pool_bytes)
                receipts.append(NS(
                    command_id=expected.command_id, anchor_node_id=child.anchor_node_id,
                    published_node_ids=child.published_node_ids,
                    num_tokens_by_pool=units, num_bytes=child.num_bytes,
                ))
                nodes.extend(child.published_node_ids)
                counts.update(dict(units))
                total += child.num_bytes
                node = cache.tree_core.node_by_id(child.anchor_node_id)
                for pool, amount in child.pool_bytes:
                    if amount:
                        data = node.component_data[0 if pool == "kv" else 2]
                        data.value = data.host_value
        state["event"] = NS(
            status="completed", direction="h2d", child_commits=tuple(receipts),
            node_ids=tuple(nodes), num_tokens_by_pool=tuple(sorted(counts.items())),
            actual_bytes=total, submit_to_ack_ms=1., enqueue_to_submit_ms=1.,
            submit_ts_ms=1_000_000.,
        )
        reads.clear()
        rows.clear()
        runtime.counts.clear()

    def run():
        state["completed"] = runtime.on_native_transfer_commit(state["event"])

    def signature():
        leases = {}
        for command, lease in runtime._prefetch_service_leases.items():
            value = asdict(lease)
            if value["lock_params"] is not None:
                value["lock_params"] = vars(value["lock_params"])
            leases[command] = value
        if runtime.physical_disabled or len(leases) != commands:
            raise AssertionError(("invalid runtime ACK/protection", dict(runtime.counts)))
        return {
            "completed": [asdict(item) for item in state["completed"]],
            "ledger": ledger_state(runtime.physical_ledger),
            "leases": leases, "counts": dict(runtime.counts), "records": list(rows),
            "native_reads": dict(reads), "lock_nodes": sorted(locks),
            "lock_acquires": list(acquired), "lock_releases": list(released),
            "h2d_samples": list(runtime._h2d_samples),
            "service_samples": [asdict(item) for item in runtime._native_service_samples],
        }

    return NS(reset=reset, run=run, signature=signature, close=runtime.close)


def measure(factory, classes, case, samples):
    variants = {name: factory(cls, **case) for name, cls in classes.items()}
    costs = {name: {"thread_cpu_us": [], "wall_us": []} for name in variants}
    try:
        for sample in range(samples + 10):
            outputs = {}
            order = list(variants)
            if sample % 2:
                order.reverse()
            for name in order:
                fixture = variants[name]
                fixture.reset()
                cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
                fixture.run()
                elapsed_wall = (time.perf_counter_ns() - wall) / 1000.
                elapsed_cpu = (time.thread_time_ns() - cpu) / 1000.
                if sample >= 10:
                    costs[name]["thread_cpu_us"].append(elapsed_cpu)
                    costs[name]["wall_us"].append(elapsed_wall)
                outputs[name] = fixture.signature()
            if outputs["baseline"] != outputs["candidate"]:
                raise AssertionError((case, sample, outputs))
        return {
            **case, "complete_output_and_state_equal": True,
            "costs": {
                name: {clock: {**distribution(values), "samples_us": values}
                       for clock, values in clocks.items()}
                for name, clocks in costs.items()
            },
        }
    finally:
        for fixture in variants.values():
            fixture.close()


def profile_ledger(classes):
    result = {}
    for name, cls in classes.items():
        fixture = core_fixture(cls, direction="h2d", commands=64)
        fixture.reset()
        profiler = cProfile.Profile()
        profiler.runcall(fixture.run)
        stats = pstats.Stats(profiler)
        result[name] = {
            "total_calls": stats.total_calls, "primitive_calls": stats.prim_calls,
            "top_internal_time": [
                {"file": file, "line": line, "function": function,
                 "primitive_calls": values[0], "calls": values[1],
                 "self_seconds": values[2], "cumulative_seconds": values[3]}
                for (file, line, function), values in sorted(
                    stats.stats.items(), key=lambda item: item[1][2], reverse=True,
                )[:20]
            ],
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="0c6cbc6")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--observed-transfers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("positive samples and a fresh report are required")
    paths = (
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "scripts/benchmark_merged_ack_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_wait_prefetch_dispatch_cpu.py",
        "scripts/benchmark_prefetch_control_cpu.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    old_ledger = baseline.on_native_transfer_commit.__globals__["PhysicalTransactionLedger"]
    ledgers = {"baseline": old_ledger, "candidate": PhysicalTransactionLedger}
    runtimes = {"baseline": baseline, "candidate": NativeAdmissionRuntime}
    observed = observed_shapes(args.observed_transfers)
    with ExitStack() as patches:
        patches.enter_context(patch("time.monotonic", lambda: 1000.))
        patches.enter_context(patch("time.time", lambda: 1000.))
        for cls in ledgers.values():
            patches.enter_context(patch.dict(cls.register.__globals__, monotonic=lambda: 1000.))
        core_cases = [
            measure(core_fixture, ledgers, {
                "direction": direction, "commands": commands, "width": width, "extra": extra,
            }, args.samples)
            for direction in ("h2d", "d2h") for commands in (1, 8, 42, 64)
            for width in (1, 4) for extra in sorted({0, min(8, 64 - commands)})
        ]
        core_cases += [
            measure(core_fixture, ledgers, {
                "direction": direction, "commands": commands, "parts": 2,
            }, args.samples)
            for direction in ("h2d", "d2h") for commands in (1, 8, 32)
        ]
        core_cases += [
            measure(core_fixture, ledgers, {
                "direction": "d2h", "commands": commands, "extra": extra, "split": True,
            }, args.samples)
            for commands in (1, 8) for extra in (0, 8)
        ]
        runtime_cases = [
            measure(runtime_fixture, runtimes, {
                "commands": commands, "diagnostics": diagnostics, "source": source,
            }, args.samples)
            for commands in (1, 8, 42, 64)
            for diagnostics in (False, True) for source in ("join_ticket", "tool_wait")
        ]
        profiles = profile_ledger(ledgers)
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != wanted
           for path, wanted in sources.items()):
        raise RuntimeError("CPU benchmark source changed")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "observed_at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "candidate_source_sha256": sources,
        "observed_native_ack_shapes": observed,
        "core_cases": core_cases, "runtime_cases": runtime_cases,
        "untimed_profiles": profiles,
        "samples_per_case": args.samples, "warmup_samples": 10,
        "scope": [
            "Full old ledger/runtime/observer/event channel are pinned and rebound to the baseline.",
            "Ledger includes live registration, partial receipts, pending ownership and retirement.",
            "Complete runtime callbacks include service evidence, lease registration and modeled lock receipts.",
            "Setup, native publication stand-in, state reset, equality checks and profiles are outside timing.",
            "Writer appends to memory; native locks/controller/allocator are stand-ins. No real DMA or GPU.",
            "Observed ACK populations are not measurements of callback cost or a basis for inferred JCT gains.",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "ledger_cases": len(core_cases), "runtime_cases": len(runtime_cases),
        "observed_ack_rows": observed["completed_rows"],
        "runtime_representatives": [
            {"commands": case["commands"], "source": case["source"],
             **{name: clocks["thread_cpu_us"]["p50_us"] for name, clocks in case["costs"].items()}}
            for case in runtime_cases if not case["diagnostics"] and case["commands"] in (8, 42)
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
