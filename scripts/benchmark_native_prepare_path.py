#!/usr/bin/env python3
"""Compare PREPARE ancestry and sampling costs on identical CPU cache fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace as NS

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [str(ROOT), *(entry for entry in sys.path if entry != str(ROOT))]

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.native_transfer_service import load_native_service_seed
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from tests.test_sglang_v0520_observer import (
    ComponentData, UnifiedTreeCore, UnifiedTreeNode, _static_cache,
)

def baseline_runtime(revision):
    modules, digests = [], {}
    for name in ("observer", "physical", "runtime"):
        path = f"beliefkv/runtime/sglang_v0520_{name}.py"
        source = subprocess.check_output(
            ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
        )
        digests[path] = hashlib.sha256(source.encode()).hexdigest()
        module = ModuleType(f"beliefkv.runtime._prepare_baseline_{name}")
        sys.modules[module.__name__] = module
        exec(compile(source, f"{revision}:{path}", "exec"), vars(module))
        for dependency in modules:
            for symbol, value in vars(dependency).items():
                if not symbol.startswith("__") and symbol in vars(module):
                    setattr(module, symbol, value)
        modules.append(module)
    return modules[-1].NativeAdmissionRuntime, digests


def fixture(
    runtime_class, *, workflows, depth, backed, host_full, leases,
    host_only=False, service_samples=(), mamba_ancestor=False,
):
    runtime, queue = synthetic_runtime(
        runtime_class, CausalFrontierScheduler, workflows, 16,
    )
    if service_samples:
        runtime._native_service_samples.clear()
        runtime._native_service_samples.extend(service_samples)
    cache = _static_cache()
    cache.token_to_kv_pool_allocator.size = 2_000_000
    full = cache.token_to_kv_pool_allocator._kvcache.full_kv_pool
    full.size = 2_000_000
    cache.host_pool_group.entry_map["kv"].host_pool.size = 10_000_000
    cache.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: host_full
    cache.req_to_token_pool.mamba_pool.size = 1024
    cache.req_to_token_pool.mamba_allocator.size = 1024
    cache.host_pool_group.entry_map["mamba"].host_pool.size = 4096
    cache.host_pool_group.entry_map["mamba"].host_pool.available_size = lambda: 4096
    cache.enable_session_radix_cache = True
    cache.cache_controller.write_policy = "write_back"
    cache.ongoing_write_through = {}
    nodes, sessions, keys = {}, {}, []
    reads = Counter()

    def snapshot(session, generation, *, max_leaves):
        reads["session_snapshot"] += 1
        leaf = sessions[session]
        state = leaf.parent if mamba_ancestor else leaf
        return ((0, ((leaf.id, leaf.creation_time),)),
                (2, ((state.id, state.creation_time),)))

    def node_by_id(node_id):
        reads["node_lookup"] += 1
        return nodes[node_id]

    for workflow in range(workflows):
        wid, iid = f"wf-{workflow}", f"wf-{workflow}-root"
        context = f"ctx-{iid}"
        session = f"s-{context}"
        key = PrefillCandidateKey(f"root-{workflow}", wid, iid, context, 0, 0, session, 1)
        runtime.context_sessions[context] = key
        runtime._context_tokens[context] = (0, (depth - 1) * 128 + 1, 0, False)
        runtime.graph.invocations[iid].join_id = f"{wid}-join-15"
        keys.append(key)
        parent = None
        for offset in range(depth):
            node_id = workflow * depth + offset
            full_extent = range(128) if offset else None
            full_value = None if host_only else full_extent
            full_host = full_extent if backed or offset < depth // 2 else None
            state_value = (
                (0,) if offset == depth - (2 if mamba_ancestor else 1) else None
            )

            def component(value, host):
                return ComponentData(
                    value=value, host_value=host, lock_ref=0, host_lock_ref=0,
                    session_ref=1, session_ids={session},
                )

            node = UnifiedTreeNode(
                id=node_id, creation_time=node_id + 1., parent=parent,
                key=range(128) if offset else (),
                component_data={
                    0: component(full_value, full_host),
                    2: component(
                        None if host_only else state_value,
                        state_value if backed else None,
                    ),
                },
                write_through_pending_id=None, load_back_pending_id=None,
            )
            nodes[node_id] = node
            if backed and offset and not host_only:
                runtime._parent_pressure_candidates[node_id] = (key, node.creation_time)
            parent = node
        sessions[session] = parent
    cache.tree_core = UnifiedTreeCore(node_by_id=node_by_id, is_write_back=True)
    cache.session_refs = NS(
        snapshot_session_leaf_anchors=snapshot,
        snapshot_latest_session_leaf_anchors=snapshot,
        _session_generations={session: 1 for session in sessions},
        _closed_session_ids=set(),
    )
    runtime.attach_native_cache(cache)
    if leases:
        from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease
        for index, key in enumerate(keys[:leases]):
            node = sessions[key.session_id]
            now = time.monotonic()
            runtime._prefetch_service_leases[str(index)] = _PrefetchServiceLease(
                key, str(index), node.id, node.creation_time, "join_wait", None,
                now, now + 3600., (("kv", 2560),),
            )
    actions, observations = [], []

    def issue(step, *, source):
        actions.append((step.key.context_id, step.node_id, step.include_mamba, source))
        return f"prepare-{len(actions)}"

    runtime.issue_shadow_backup_step = issue
    runtime._opportunity_writer = NS(record=observations.append, close=lambda: None)
    return runtime, queue, reads, actions, observations


def measure_case(
    baseline, *, workflows, depth, iterations, backed, host_full, leases,
    host_only=False, service_samples=(), force_probes=False, mamba_ancestor=False,
):
    variants = {
        name: fixture(cls, workflows=workflows, depth=depth, backed=backed,
                      host_full=host_full, leases=leases, host_only=host_only,
                      service_samples=service_samples, mamba_ancestor=mamba_ancestor)
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    samples = {name: {"join_prepare": [], "sampling": []} for name in variants}
    totals = {name: Counter() for name in variants}
    try:
        for iteration in range(iterations + 5):
            names = list(variants)
            if iteration % 2:
                names.reverse()
            selections, publications, sampled_steps = [], [], []
            for name in names:
                runtime, queue, reads, actions, observations = variants[name]
                runtime._join_prepare_next_ms = 0.
                runtime._opportunity_next_ms = 0.
                if force_probes:
                    runtime._prepare_probe_after_ms.clear()
                before = reads.copy()
                actions.clear()
                observations.clear()
                start = time.perf_counter_ns()
                runtime.dispatch_join_prepare(queue)
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
                selections.append(tuple(actions))
                publications.append(runtime._native_cache.beliefkv_join_pressure_candidates)
                start = time.perf_counter_ns()
                runtime._sample_h2d_opportunities(queue, now_ms=time.monotonic() * 1000.)
                sample_ms = (time.perf_counter_ns() - start) / 1_000_000
                sampled_steps.append(tuple(
                    {key: value for key, value in row.items()
                     if key not in ("ts_ms", "monotonic_ms")}
                    for row in observations if row["event"] == "session_h2d_opportunity"
                ))
                if iteration >= 5:
                    samples[name]["join_prepare"].append(elapsed)
                    samples[name]["sampling"].append(sample_ms)
                    totals[name].update(reads - before)
            for evidence in (selections, sampled_steps):
                if evidence[0] != evidence[1]:
                    raise AssertionError(f"changed native selection at iteration {iteration}")
            protected = {
                (lease.node_id, lease.creation_time)
                for runtime, *_ in variants.values()
                for lease in runtime._prefetch_service_leases.values()
            }
            if set(publications[0]) - protected != set(publications[1]) - protected:
                raise AssertionError(f"changed unprotected pressure nodes at iteration {iteration}")
        costs = {name: {phase: distribution(values) for phase, values in phases.items()}
                 for name, phases in samples.items()}
        return {
            "workflows": workflows, "depth": depth,
            "all_current_input_backed": backed, "host_full_free_tokens": host_full,
            "host_only": host_only,
            "mamba_anchor_is_full_ancestor": mamba_ancestor,
            "service_history_samples": len(service_samples),
            "live_restore_leases": leases, "iterations": iterations,
            "force_prepare_probes": force_probes,
            "selection_and_publication_equal": True,
            "publication_scope": (
                "unprotected node set; active prefetch leases remain ineligible "
                "through the live native pressure validator"
            ),
            "costs": costs, "observed_reads": {name: dict(values) for name, values in totals.items()},
            "mean_reduction": {
                phase: 1 - costs["optimized"][phase]["mean_ms"]
                / costs["baseline"][phase]["mean_ms"]
                for phase in ("join_prepare", "sampling")
            },
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def handoff_identity_case(baseline):
    evidence = {}
    for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime)):
        runtime, _, _, actions, _ = fixture(
            cls, workflows=4, depth=2, backed=False, host_full=1_000_000, leases=0,
        )
        try:
            key = runtime.context_sessions["ctx-wf-0-root"]
            cache = runtime._native_cache
            node = cache.tree_core.node_by_id(1)
            node.creation_time = np.float64(node.creation_time)
            node.component_data[0].value = None
            node.component_data[0].host_value = range(128)
            node.component_data[2].value = None
            node.component_data[2].host_value = (0,)
            cache.token_to_kv_pool_allocator.available_size = lambda: 1024
            cache.inspect_beliefkv_reentry = lambda _: {
                "component_leaves": (
                    (0, ((node.id, node.creation_time),)),
                    (2, ((node.id, node.creation_time),)),
                ),
                "reusable_input_tokens": 128, "checkpoint_tokens": 128,
                "device_checkpoint_tokens": 0, "missing_full_tokens": 128,
                "missing_mamba_slots": 1,
            }
            request = NS(
                rid=key.request_id, session_id=key.session_id, session_generation=1,
                origin_input_ids=range(129), output_ids=(),
                cache_request_handle=NS(attempt_id=0),
                beliefkv_metadata={
                    "root_workflow_id": key.root_workflow_id,
                    "invocation_id": key.invocation_id,
                    "context_id": key.context_id, "context_epoch": 0,
                },
            )
            runtime.visible[request.rid] = key
            runtime._visible_since[request.rid] = time.monotonic()
            runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
            runtime.enable_execution_handoff = True
            runtime.issue_prefetch_gpu_step = runtime.issue_shadow_backup_step
            runtime.dispatch_execution_handoff([request], running_batch=NS(reqs=()))
            evidence[name] = {
                "selected": runtime.counts["execution_handoff_selected"],
                "issued": runtime.counts["execution_handoff_issued"],
                "no_step": runtime.counts["execution_handoff_closed:resident_or_unavailable"],
                "submitted_step": actions,
            }
        finally:
            runtime.close()
    if evidence["baseline"]["issued"] not in (0, 1) or evidence["optimized"]["issued"] != 1:
        raise AssertionError(f"native timestamp reproduction failed: {evidence}")
    return {
        "creation_time_type": "numpy.float64",
        "scope": "real closure/restore planner; transfer enqueue stubbed, no DMA or ACK credit",
        **evidence,
    }


def terminal_fixture(runtime_class, *, depth, anchor_count):
    runtime, _, reads, _, observations = fixture(
        runtime_class, workflows=4, depth=depth, backed=True,
        host_full=1_000_000, leases=0,
    )
    cache = runtime._native_cache
    original_lookup = cache.tree_core.node_by_id
    watches, added = [], {}
    for workflow in range(4):
        key = runtime.context_sessions[f"ctx-wf-{workflow}-root"]
        leaf = original_lookup(workflow * depth + depth - 1)
        anchors = [(leaf.id, leaf.creation_time)]
        for index in range(1, anchor_count):
            node_id = 4 * depth + workflow * anchor_count + index
            node = UnifiedTreeNode(
                id=node_id, creation_time=node_id + 1., parent=leaf.parent,
                key=range(128) if depth > 1 else (),
                component_data={
                    component: ComponentData(
                        value=range(128) if component == 0 else (index,),
                        host_value=range(128) if component == 0 else (index,),
                        lock_ref=0, host_lock_ref=0, session_ref=1,
                        session_ids={key.session_id},
                    )
                    for component in (0, 2)
                },
                write_through_pending_id=None, load_back_pending_id=None,
            )
            added[node_id] = node
            anchors.append((node.id, node.creation_time))
        watches.append({
            "key": key, "anchors": anchors,
            "terminated_ms": time.monotonic() * 1000., "sample_count": 0,
        })

    def node_by_id(node_id):
        if node_id in added:
            reads["node_lookup"] += 1
            return added[node_id]
        return original_lookup(node_id)

    cache.tree_core.node_by_id = node_by_id
    return runtime, watches, reads, observations


def terminal_case(baseline, *, depth, anchor_count, iterations):
    variants = {
        name: terminal_fixture(cls, depth=depth, anchor_count=anchor_count)
        for name, cls in (("baseline", baseline), ("optimized", NativeAdmissionRuntime))
    }
    samples = {name: {"wall": [], "thread_cpu": []} for name in variants}
    reads_total = {name: Counter() for name in variants}
    volatile = {"ts_ms", "elapsed_since_terminal_ms"}
    canonical = None
    try:
        for iteration in range(iterations + 5):
            names = list(variants)
            if iteration % 2:
                names.reverse()
            outputs = {}
            for name in names:
                runtime, watches, reads, observations = variants[name]
                observations.clear()
                before = reads.copy()
                wall_start = time.perf_counter_ns()
                cpu_start = time.thread_time_ns()
                for watch in watches:
                    runtime._record_terminal_cache(watch)
                cpu_ms = (time.thread_time_ns() - cpu_start) / 1_000_000
                wall_ms = (time.perf_counter_ns() - wall_start) / 1_000_000
                outputs[name] = [
                    {key: value for key, value in row.items() if key not in volatile}
                    for row in observations
                ]
                if len(observations) != len(watches) or any(
                    len(row["nodes"]) != depth - 1 + anchor_count
                    or row["unavailable_anchors"] or row["gone_or_replaced_anchors"]
                    for row in observations
                ):
                    raise AssertionError(f"incomplete terminal fixture: {name}")
                if iteration >= 5:
                    samples[name]["wall"].append(wall_ms)
                    samples[name]["thread_cpu"].append(cpu_ms)
                    reads_total[name].update(reads - before)
            if outputs["baseline"] != outputs["optimized"]:
                raise AssertionError(f"changed terminal evidence at iteration {iteration}")
            canonical = json.dumps(outputs["optimized"], sort_keys=True, separators=(",", ":"))
        costs = {name: {clock: distribution(values) for clock, values in clocks.items()}
                 for name, clocks in samples.items()}
        if reads_total["baseline"] != reads_total["optimized"]:
            raise AssertionError("changed terminal native-observation reads")
        return {
            "watch_count": len(watches), "depth": depth,
            "anchor_count": anchor_count, "iterations": iterations,
            "summaries_per_watch": depth * anchor_count,
            "unique_summaries_per_watch": depth - 1 + anchor_count,
            "output_equal": True,
            "ignored_output_fields": sorted(volatile),
            "canonical_output_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "costs": costs,
            "observed_reads": {name: dict(value) for name, value in reads_total.items()},
            "mean_reduction": {
                clock: 1 - costs["optimized"][clock]["mean_ms"]
                / costs["baseline"][clock]["mean_ms"]
                for clock in ("wall", "thread_cpu")
            },
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="e985d8c")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--depth", type=int, default=24)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--terminal-only", action="store_true",
                        help="Measure read-only terminal samples with overlapping anchors.")
    parser.add_argument("--force-prepare-probes", action="store_true",
                        help="Measure each PREPARE probe instead of its one-second backoff.")
    parser.add_argument(
        "--service-seed", type=Path,
        help="Use the same validated native transfer history for both PREPARE variants.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mamba-ancestor", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.depth <= 64 or not 4 <= args.workflows <= 512 or args.iterations < 1:
        raise ValueError("depth 1..64, workflows 4..512 and positive iterations required")
    if args.mamba_ancestor and args.depth < 3:
        raise ValueError("Mamba ancestor fixture requires depth at least three")
    baseline, digests = baseline_runtime(args.baseline_revision)
    imported_sources = {
        path: str(Path(sys.modules[f"beliefkv.runtime.sglang_v0520_{name}"].__file__).resolve())
        for name, path in (
            ("observer", "beliefkv/runtime/sglang_v0520_observer.py"),
            ("physical", "beliefkv/runtime/sglang_v0520_physical.py"),
            ("runtime", "beliefkv/runtime/sglang_v0520_runtime.py"),
        )
    }
    if any(Path(loaded) != ROOT / path for path, loaded in imported_sources.items()):
        raise RuntimeError(f"benchmark imported a different worktree: {imported_sources}")
    service_seed = None
    service_samples = ()
    if args.service_seed is not None:
        digest = hashlib.sha256(args.service_seed.read_bytes()).hexdigest()
        service_samples = load_native_service_seed(str(args.service_seed), digest)
        service_seed = {
            "path": str(args.service_seed.resolve()), "sha256": digest,
            "sample_count": len(service_samples),
        }
    cases = [] if args.terminal_only else [
        measure_case(baseline, workflows=args.workflows, depth=args.depth,
                     iterations=args.iterations, service_samples=service_samples,
                     force_probes=args.force_prepare_probes,
                     mamba_ancestor=args.mamba_ancestor, **case)
        for case in (
            {"backed": True, "host_full": 1_000_000, "leases": 0},
            {"backed": True, "host_full": 1_000_000, "leases": 4},
            {"backed": False, "host_full": 1_000_000, "leases": 0},
            {"backed": False, "host_full": 0, "leases": 0},
            {"backed": True, "host_full": 0, "leases": 0},
            {"backed": True, "host_full": 1_000_000, "leases": 0, "host_only": True},
        )
    ]
    report = {
        "scope": "same-input CPU fixtures; native enqueue is stubbed, no GPU or throughput claim",
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests,
        "fixture": "validated static pools and real observer ancestry; sixteen retained workflow rounds",
        "optimized_source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in digests
        },
        "imported_sources": imported_sources,
        "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "service_seed": service_seed,
        "handoff_identity": None if args.terminal_only else handoff_identity_case(baseline),
        "cases": cases,
        "terminal_cases": [
            terminal_case(baseline, depth=args.depth, anchor_count=anchors,
                          iterations=args.iterations)
            for anchors in (1, 2, 8)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "cases": [{
            key: case[key] for key in (
                "depth", "all_current_input_backed", "host_full_free_tokens",
                "host_only", "live_restore_leases", "mean_reduction",
                "mamba_anchor_is_full_ancestor",
                "selection_and_publication_equal",
            )
        } for case in cases],
        "terminal_cases": [{
            key: case[key] for key in (
                "depth", "anchor_count", "summaries_per_watch",
                "unique_summaries_per_watch", "output_equal", "mean_reduction",
            )
        } for case in report["terminal_cases"]],
    }, indent=2))


if __name__ == "__main__":
    main()
