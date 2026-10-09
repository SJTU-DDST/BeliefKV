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

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_shared_path import distribution, synthetic_runtime
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from tests.test_sglang_v0520_observer import (
    ComponentData, UnifiedTreeCore, UnifiedTreeNode, _static_cache,
)


ROOT = Path(__file__).resolve().parents[1]


def baseline_runtime(revision):
    modules, digests = [], {}
    for name in ("physical", "runtime"):
        path = f"beliefkv/runtime/sglang_v0520_{name}.py"
        source = subprocess.check_output(
            ["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True,
        )
        digests[path] = hashlib.sha256(source.encode()).hexdigest()
        module = ModuleType(f"beliefkv.runtime._prepare_baseline_{name}")
        sys.modules[module.__name__] = module
        exec(compile(source, f"{revision}:{path}", "exec"), vars(module))
        modules.append(module)
    physical, runtime = modules
    for name, value in vars(physical).items():
        if name in vars(runtime):
            setattr(runtime, name, value)
    return runtime.NativeAdmissionRuntime, digests


def fixture(runtime_class, *, workflows, depth, backed, host_full, leases):
    runtime, queue = synthetic_runtime(
        runtime_class, CausalFrontierScheduler, workflows, 16,
    )
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
        return ((0, ((leaf.id, leaf.creation_time),)),
                (2, ((leaf.id, leaf.creation_time),)))

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
            full_value = range(128) if offset else None
            full_host = full_value if backed or offset < depth // 2 else None
            state_value = (0,) if offset == depth - 1 else None

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
                    2: component(state_value, state_value if backed else None),
                },
                write_through_pending_id=None, load_back_pending_id=None,
            )
            nodes[node_id] = node
            if backed and offset:
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


def measure_case(baseline, *, workflows, depth, iterations, backed, host_full, leases):
    variants = {
        name: fixture(cls, workflows=workflows, depth=depth, backed=backed,
                      host_full=host_full, leases=leases)
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
                    (row["context_id"], row.get("prepare_node_id"),
                     row.get("prepare_required_full_tokens"),
                     row.get("prepare_required_mamba_slots"))
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
            "live_restore_leases": leases, "iterations": iterations,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-revision", default="e985d8c")
    parser.add_argument("--workflows", type=int, default=156)
    parser.add_argument("--depth", type=int, default=24)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.depth <= 64 or not 4 <= args.workflows <= 512 or args.iterations < 1:
        raise ValueError("depth 1..64, workflows 4..512 and positive iterations required")
    baseline, digests = baseline_runtime(args.baseline_revision)
    cases = [
        measure_case(baseline, workflows=args.workflows, depth=args.depth,
                     iterations=args.iterations, **case)
        for case in (
            {"backed": True, "host_full": 1_000_000, "leases": 0},
            {"backed": True, "host_full": 1_000_000, "leases": 4},
            {"backed": False, "host_full": 1_000_000, "leases": 0},
            {"backed": False, "host_full": 0, "leases": 0},
        )
    ]
    report = {
        "scope": "same-input CPU fixtures; native enqueue is stubbed, no GPU or throughput claim",
        "baseline_revision": args.baseline_revision,
        "baseline_source_sha256": digests,
        "fixture": "validated static pools and real observer ancestry; sixteen retained workflow rounds",
        "handoff_identity": handoff_identity_case(baseline),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps([{
        key: case[key] for key in (
            "depth", "all_current_input_backed", "host_full_free_tokens",
            "live_restore_leases", "mean_reduction", "selection_and_publication_equal",
        )
    } for case in cases], indent=2))


if __name__ == "__main__":
    main()
