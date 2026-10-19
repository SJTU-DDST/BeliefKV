#!/usr/bin/env python3
"""Compare pinned runtime control paths on CPU without launching SGLang."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _ChildFinalStage
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from scripts.benchmark_native_prepare_path import baseline_runtime, fixture
from scripts.benchmark_native_shared_path import synthetic_runtime
from tests.test_wait_prefetch_batch import batch_fixture


def distribution(values):
    ordered = sorted(values)
    return {
        "mean_us": mean(values), "p50_us": median(values),
        "p90_us": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
        "p99_us": ordered[min(len(ordered) - 1, int(.99 * len(ordered)))],
    }


def compare(callbacks, *, samples, calls=50):
    values = {name: [] for name in callbacks}
    for sample in range(samples + 20):
        order = list(callbacks)
        if sample % 2:
            order.reverse()
        for name in order:
            start = time.thread_time_ns()
            for _ in range(calls):
                callbacks[name]()
            elapsed = (time.thread_time_ns() - start) / calls / 1000.
            if sample >= 20:
                values[name].append(elapsed)
    return {name: distribution(observed) for name, observed in values.items()}


def idle_case(baseline, *, samples, profiling):
    variants = {
        name: cls() for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    try:
        for runtime in variants.values():
            runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
            runtime.enable_prepare_host = False
            runtime._prediction_maintenance_interval_ms = 100.
            runtime._hotpath_timing.enabled = profiling
            runtime.event_server = NS(drain=lambda **kwargs: (), close=lambda: None)
        costs = compare({
            name: lambda runtime=runtime: (
                runtime.scheduler_step(defer_observations=True),
                runtime.dispatch_after_forward(),
            ) for name, runtime in variants.items()
        }, samples=samples)
        if any(runtime.physical_disabled or runtime.physical_ledger.pending_count
               for runtime in variants.values()):
            raise AssertionError("empty candidates produced a physical action")
        return {
            "case": "empty_wait_sources_scheduler_and_post_forward",
            "profiling": profiling, "costs": costs,
            "physical_actions": 0, "live_state_equal": True,
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def pending_case(baseline, *, samples, pending):
    variants = {
        name: cls() for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    key = PrefillCandidateKey("r", "wf", "i", "ctx-2", 0, 0, "s-2", 1)
    budget = PrefetchResidencyBudget(2, 10_000)
    canonical = {}
    try:
        for name, runtime in variants.items():
            runtime._parallel_wait_prefetch = True
            runtime._wait_prefetch_contexts = 2
            runtime.attach_native_cache(NS(supports_beliefkv_overlap_prefetch=lambda: True))
            for index in range(pending):
                owner = index % 2
                runtime.physical_ledger.register(PhysicalActionExpectation(
                    f"cmd-{index}", "PREFETCH_GPU", f"ctx-{owner}", index % 3,
                    (PhysicalChildExpectation(index, (index,), (("kv", 20),), 20),),
                    (("kv", 10),), session_id=f"s-{owner}", session_generation=1,
                ))
            canonical[name] = [
                asdict(runtime.physical_ledger.pending_expectation(f"cmd-{index}"))
                for index in range(pending)
            ]

        def query(runtime):
            return (
                runtime._wait_prefetch_transfer_busy(key),
                runtime._prefetch_slot_available(key, budget=budget),
                runtime._prefetch_byte_allowance(budget),
            )

        outputs = {name: query(runtime) for name, runtime in variants.items()}
        if canonical["baseline"] != canonical["candidate"] or outputs["baseline"] != outputs["candidate"]:
            raise AssertionError("physical ownership or budgets changed")
        return {
            "case": "parallel_restore_budget_queries", "pending_commands": pending,
            "physical_state_and_query_results_equal": True,
            "costs": compare({
                name: lambda runtime=runtime: query(runtime)
                for name, runtime in variants.items()
            }, samples=samples),
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def empty_prepare_case(baseline, *, samples, workflows, running):
    variants = {}
    for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
        runtime, queue, reads, actions, _ = fixture(
            cls, workflows=workflows, depth=4, backed=False,
            host_full=1_000_000, leases=0,
        )
        for invocation in runtime.graph.invocations.values():
            invocation.state = InvocationState.READY
        runtime.graph._graph_version += 1
        runtime._opportunity_writer = None
        runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
        batch = NS(reqs=[NS(rid=f"busy-{index}") for index in range(running)])
        variants[name] = runtime, queue, batch, reads, actions
    try:
        def dispatch(runtime, queue, batch):
            runtime._join_prepare_next_ms = runtime._tool_prepare_next_ms = 0.
            runtime.dispatch_after_forward(queue, running_batch=batch)

        capacity_reads = {}
        for name, (runtime, queue, batch, _, _) in variants.items():
            globals_ = type(runtime)._prepare_pressure.__globals__
            observe = globals_["observe_static_full_mamba_headroom"]
            counted = Mock(wraps=observe)
            with patch.dict(globals_, observe_static_full_mamba_headroom=counted):
                dispatch(runtime, queue, batch)
            capacity_reads[name] = counted.call_count
        costs = compare({
            name: lambda runtime=runtime, queue=queue, batch=batch:
                dispatch(runtime, queue, batch)
            for name, (runtime, queue, batch, _, _) in variants.items()
        }, samples=samples)
        if any(
            runtime.physical_disabled or runtime.physical_ledger.pending_count or actions
            or reads["session_snapshot"]
            or getattr(runtime._native_cache, "beliefkv_join_pressure_candidates", ())
            for runtime, _, _, reads, actions in variants.values()
        ):
            raise AssertionError("an empty PREPARE source changed native state")
        return {
            "case": "post_forward_prepare_without_wait_sources",
            "workflows": workflows, "running_requests": running,
            "forced_existing_prepare_deadlines_due": True,
            "capacity_reads_per_dispatch": capacity_reads,
            "costs": costs, "native_actions_and_candidate_publication_equal": True,
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def completion_reconcile_case(baseline, *, samples, source, commands, missing):
    variants = {}
    target = tuple(f"burst-{index}" for index in range(commands))
    requested = ("missing", *target[1:]) if missing else target
    key = PrefillCandidateKey("r", "w", "i", "c", 0, 0, "s", 1)
    try:
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
            runtime = cls()
            runtime.completed_physical_actions.extend(
                NS(command_id=f"old-{index}", action=(
                    "PREFETCH_GPU" if index % 2 else "PREPARE_HOST"
                )) for index in range(128 - commands)
            )
            runtime.completed_physical_actions.extend(
                NS(command_id=command, action="PREFETCH_GPU") for command in target
            )
            ticket = NS(
                key=key, join_id="join", phase="confirmed",
                command_id=requested[-1], command_ids=requested,
            )
            variants[name] = runtime, ticket

        def reconcile(runtime, ticket):
            ticket.command_id, ticket.command_ids = requested[-1], requested
            return getattr(runtime, f"_reconcile_{source}_prefetch_ticket")(ticket)

        outputs = {
            name: reconcile(runtime, ticket)
            for name, (runtime, ticket) in variants.items()
        }
        if set(outputs.values()) != {not missing}:
            raise AssertionError("ACK reconciliation changed completion credit")
        costs = compare({
            name: lambda runtime=runtime, ticket=ticket: reconcile(runtime, ticket)
            for name, (runtime, ticket) in variants.items()
        }, samples=samples)
        if any(runtime.physical_disabled or runtime.physical_ledger.pending_count
               for runtime, _ in variants.values()):
            raise AssertionError("ACK lookup issued or invalidated a transfer")
        return {
            "case": "wait_prefetch_ack_reconciliation", "source": source,
            "history_receipts": 128, "burst_commands": commands,
            "one_command_missing": missing, "costs": costs,
            "completion_credit_equal": True,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def registration_case(
    baseline, *, samples, commands, children, pools="full", published_nodes=1,
):
    baseline_ledger = baseline._roll_tool_prefetch.__globals__["PhysicalTransactionLedger"]
    pool_bytes = {
        "full": (("kv", 20),), "mamba": (("mamba", 30),),
        "hybrid": (("kv", 20), ("mamba", 30)),
    }[pools]
    pool_sizes = tuple((pool, 10) for pool, _ in pool_bytes)
    num_bytes = sum(amount for _, amount in pool_bytes)
    planned = tuple(
        PhysicalActionExpectation(
            f"load-{index}", "PREFETCH_GPU", f"context-{index % 2}", 0,
            tuple(PhysicalChildExpectation(
                index * 16 + child * 2,
                tuple(range(
                    index * 16 + child * 2,
                    index * 16 + child * 2 + published_nodes,
                )),
                pool_bytes, num_bytes,
            ) for child in range(children)),
            pool_sizes, session_id=f"session-{index % 2}", session_generation=1,
        ) for index in range(commands)
    )

    def register(cls):
        ledger = cls()
        for expected in planned:
            ledger.register(expected)
        return ledger

    variants = {"baseline": baseline_ledger, "candidate": PhysicalTransactionLedger}
    states = {}
    for name, cls in variants.items():
        ledger = register(cls)
        states[name] = (
            ledger.pending_count, ledger.pending_action_bytes("PREFETCH_GPU"),
            tuple(ledger.pending_h2d_contexts),
            tuple(asdict(ledger.pending_expectation(item.command_id)) for item in planned),
            sorted(ledger._pending_nodes),
        )
    if states["baseline"] != states["candidate"]:
        raise AssertionError("registration changed bytes, ownership or node conflicts")
    return {
        "case": "register_native_restore_commands", "commands": commands,
        "children_per_command": children, "pools": pools,
        "published_nodes_per_child": published_nodes,
        "pending_bytes_contexts_and_node_ownership_equal": True,
        "costs": compare({
            name: lambda cls=cls: register(cls) for name, cls in variants.items()
        }, samples=samples, calls=10),
    }


def native_ack_case(baseline, *, samples, pending, node_count):
    baseline_ledger = baseline._roll_tool_prefetch.__globals__["PhysicalTransactionLedger"]
    variants = {}
    commit = NS(node_ids=tuple(range(10_000, 10_000 + node_count)), child_commits=())
    for name, cls in (("baseline", baseline_ledger), ("candidate", PhysicalTransactionLedger)):
        ledger = cls(max_age_s=3600.)
        for index in range(pending):
            ledger.register(PhysicalActionExpectation(
                f"cmd-{index}", "PREFETCH_GPU", f"context-{index % 2}", 0,
                (PhysicalChildExpectation(index, (index,), (("kv", 20),), 20),),
                (("kv", 10),),
            ))
        variants[name] = ledger
        if ledger.observe(commit, live_context_epochs={}) != ():
            raise AssertionError("an unrelated native ACK received predictive credit")
    costs = compare({
        name: lambda ledger=ledger: ledger.observe(commit, live_context_epochs={})
        for name, ledger in variants.items()
    }, samples=samples, calls=10)
    if any(ledger.pending_count != pending for ledger in variants.values()):
        raise AssertionError("an unrelated ACK changed pending ownership")
    return {
        "case": "unrelated_native_ack", "pending_commands": pending,
        "native_ack_nodes": node_count, "costs": costs,
        "pending_ownership_unchanged_and_no_credit": True,
    }


def parallel_tool_conversion_case(baseline):
    from types import MethodType
    from beliefkv.runtime.native_transfer_policy import TransferStartWindow
    from tests.test_wait_prefetch_batch import parallel_tool_batch_fixture

    results = {}
    for name in ("baseline_selector", "candidate_selector"):
        runtime, _, _, plans, _, _ = parallel_tool_batch_fixture()
        if name == "baseline_selector":
            runtime._roll_tool_prefetch = MethodType(baseline._roll_tool_prefetch, runtime)
        try:
            with patch.object(runtime, "inspect_context_h2d_opportunity",
                              side_effect=lambda **kwargs: plans[kwargs["context_id"]]), \
                 patch.object(runtime, "_h2d_start_window",
                              return_value=TransferStartWindow(200., 0., 1000.)):
                runtime.dispatch_tool_prefetch()
                runtime.dispatch_tool_prefetch()
                runtime.dispatch_tool_prefetch()
            results[name] = {
                "pending_contexts": len(runtime.physical_ledger.pending_h2d_contexts),
                "pending_commands": runtime.physical_ledger.pending_count,
                "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
                "acked_commands": runtime.counts["tool_prefetch_acked"],
                "completed_actions": len(runtime.completed_physical_actions),
                "physical_disabled": runtime.physical_disabled,
            }
        finally:
            runtime.close()
    if results["baseline_selector"]["pending_contexts"] not in (1, 2) or (
        results["candidate_selector"]["pending_contexts"] != 2
    ):
        raise AssertionError("two fitting tool contexts did not respect expected selector capacity")
    return {
        "scope": (
            "Selector-only differential: both versions use current CPU dispatcher and native "
            "stand-ins with identical two-context budgets. No DMA, ACK or reuse is fabricated."
        ),
        "results": results,
    }


def parallel_confirmed_join_conversion_case(baseline):
    from types import MethodType
    from tests.test_join_confirmed_reentry import parallel_join_fixture

    results = {}
    for name in ("baseline_selector", "candidate_selector"):
        runtime, _, _, plans, _, _ = parallel_join_fixture()
        if name == "baseline_selector":
            runtime._select_confirmed_join_ticket = MethodType(
                baseline._select_confirmed_join_ticket, runtime,
            )
        try:
            with patch.object(
                runtime, "inspect_context_h2d_opportunity",
                side_effect=lambda **kwargs: plans[kwargs["context_id"]],
            ):
                runtime.dispatch_join_prefetch()
                runtime.dispatch_join_prefetch()
                runtime.dispatch_join_prefetch()
            results[name] = {
                "pending_contexts": len(runtime.physical_ledger.pending_h2d_contexts),
                "pending_commands": runtime.physical_ledger.pending_count,
                "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
                "acked_commands": runtime.counts["join_prefetch_acked"],
                "completed_actions": len(runtime.completed_physical_actions),
                "physical_disabled": runtime.physical_disabled,
            }
        finally:
            runtime.close()
    if results["baseline_selector"]["pending_contexts"] not in (1, 2) or (
        results["candidate_selector"]["pending_contexts"] != 2
    ):
        raise AssertionError("two fitting confirmed JOINs did not respect expected selector capacity")
    return {
        "scope": (
            "Confirmed-JOIN selector differential on shared current CPU dispatcher/native "
            "stand-ins and identical live budgets. Both JOINs are already satisfied; "
            "no pre-RETURN prediction, DMA, ACK, reuse or throughput is fabricated."
        ),
        "results": results,
    }


def saturated_join_case(baseline, *, samples):
    from types import MethodType
    from tests.test_join_confirmed_reentry import parallel_join_fixture

    variants = {}
    for name in ("baseline", "candidate"):
        runtime, _, _, plans, _, _ = parallel_join_fixture()
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        if runtime.physical_ledger.pending_count != 8:
            raise AssertionError("saturated JOIN timing requires the same two pending bursts")
        if name == "baseline":
            runtime._select_confirmed_join_ticket = MethodType(
                baseline._select_confirmed_join_ticket, runtime,
            )
        variants[name] = runtime
    try:
        costs = compare({
            name: runtime._select_confirmed_join_ticket
            for name, runtime in variants.items()
        }, samples=samples)
        if any(runtime.physical_ledger.pending_count != 8
               or runtime._join_ticket.join_id != "join2"
               or runtime.counts["join_prefetch_acked"]
               or runtime.completed_physical_actions
               for runtime in variants.values()):
            raise AssertionError("saturated JOIN selection changed physical state")
        return {
            "case": "saturated_confirmed_join_selector", "costs": costs,
            "pending_contexts": 2, "pending_commands": 8,
            "physical_state_unchanged": True,
            "scope": "Selector only on identical already-full CPU frontiers; not decode CPU or GPU time",
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def provisional_join_conversion_case(baseline, *, signal):
    from types import MethodType
    from tests.test_join_parallel_prefetch import provisional_fixture

    results = {}
    for name in ("baseline_dispatch", "candidate_dispatch"):
        runtime, _, _, plans, _, _ = provisional_fixture(signal=signal)
        if name == "baseline_dispatch":
            runtime.dispatch_join_prefetch = MethodType(
                baseline.dispatch_join_prefetch, runtime,
            )
        try:
            with patch.object(
                runtime, "inspect_context_h2d_opportunity",
                side_effect=lambda **kwargs: plans[kwargs["context_id"]],
            ):
                for _ in range(3):
                    runtime.dispatch_join_prefetch()
            results[name] = {
                "pending_contexts": len(runtime.physical_ledger.pending_h2d_contexts),
                "pending_commands": runtime.physical_ledger.pending_count,
                "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
                "provisional_issued_nodes": runtime.counts["join_prefetch_provisional_issued"],
                "confirmed_issued_nodes": runtime.counts["join_prefetch_confirmed_issued"],
                "acked_commands": runtime.counts["join_prefetch_acked"],
                "completed_actions": len(runtime.completed_physical_actions),
                "satisfied_joins": sum(join.satisfied for join in runtime.graph.joins.values()),
                "physical_disabled": runtime.physical_disabled,
            }
        finally:
            runtime.close()
    if results["baseline_dispatch"]["pending_contexts"] not in (1, 2) or (
        results["candidate_dispatch"]["pending_contexts"] != 2
    ):
        raise AssertionError("two provisional JOINs did not respect expected selector capacity")
    return {
        "signal": signal,
        "scope": (
            "Dispatch-method differential on shared current CPU fixtures and legal budgets. "
            "Both JOINs remain unsatisfied. Controlled observed-EOS/work signals do not "
            "measure prediction accuracy, online opportunity frequency, DMA, ACK or reuse."
        ),
        "results": results,
    }


def saturated_provisional_join_case(baseline, *, samples):
    from types import MethodType
    from tests.test_join_parallel_prefetch import provisional_fixture

    variants = {}
    for name in ("baseline", "candidate"):
        runtime, _, _, plans, _, _ = provisional_fixture()
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ):
            runtime.dispatch_join_prefetch()
            runtime.dispatch_join_prefetch()
        if runtime.physical_ledger.pending_count != 8:
            raise AssertionError("timing requires the same two provisional bursts")
        if name == "baseline":
            runtime.dispatch_join_prefetch = MethodType(
                baseline.dispatch_join_prefetch, runtime,
            )
        variants[name] = runtime
    try:
        # Keep the same live semantic clock in both controlled pending states.
        now = time.monotonic()
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
            costs = compare({
                name: runtime.dispatch_join_prefetch for name, runtime in variants.items()
            }, samples=samples)
        if any(
            runtime.physical_ledger.pending_count != 8
            or runtime.counts["join_prefetch_acked"]
            or runtime.completed_physical_actions
            or any(join.satisfied for join in runtime.graph.joins.values())
            for runtime in variants.values()
        ):
            raise AssertionError("pending provisional dispatch changed physical state")
        return {
            "case": "saturated_provisional_join_dispatch", "costs": costs,
            "pending_contexts": 2, "pending_commands": 8,
            "physical_state_unchanged": True,
            "scope": "CPU dispatch on identical full frontiers; no service or online frequency evidence",
        }
    finally:
        for runtime in variants.values():
            runtime.close()


def inflight_prefill_conversion_case(baseline):
    from types import MethodType
    from beliefkv.runtime.native_transfer_policy import TransferStartWindow
    from tests.test_wait_prefetch_batch import parallel_tool_batch_fixture

    results = {}
    for name in ("baseline_budget", "candidate_budget"):
        runtime, _, _, plans, cache, _ = parallel_tool_batch_fixture()
        pool = NS(size=48, available_size=lambda: 0, mamba_pool=NS(size=8))
        cache.req_to_token_pool = pool
        cache.host_pool_group = cache.cache_controller.mem_pool_host
        cache.token_to_kv_pool_allocator = NS(size=1024)
        cache.full_evictable_size = cache.mamba_evictable_size = lambda: 0
        runtime._observe_native_admission_capacity(
            running_batch=NS(reqs=[NS(rid=f"running-{index}") for index in range(47)]),
            adder=NS(max_running_requests=48, max_prefill_bs=4,
                     page_size=1, rem_input_tokens=16),
        )
        if name == "baseline_budget":
            runtime._current_residency_budget = MethodType(
                baseline._current_residency_budget, runtime,
            )
        try:
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                return_value=next(iter(plans.values())).headroom,
            ), patch.object(
                runtime, "inspect_context_h2d_opportunity",
                side_effect=lambda **kwargs: plans[kwargs["context_id"]],
            ), patch.object(
                runtime, "_h2d_start_window",
                return_value=TransferStartWindow(200., 0., 1000.),
            ):
                runtime.dispatch_tool_prefetch()
                runtime.dispatch_tool_prefetch()
                runtime.dispatch_tool_prefetch()
            results[name] = {
                "running_requests": 47, "occupied_request_rows": pool.size,
                "free_request_rows": pool.available_size(),
                "pending_contexts": len(runtime.physical_ledger.pending_h2d_contexts),
                "pending_commands": runtime.physical_ledger.pending_count,
                "pending_bytes": runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU"),
                "residency_budget": asdict(runtime._residency_budget),
                "completed_actions": len(runtime.completed_physical_actions),
                "physical_disabled": runtime.physical_disabled,
            }
        finally:
            runtime.close()
    if results["baseline_budget"]["pending_contexts"] not in (0, 2) or (
        results["candidate_budget"]["pending_contexts"] != 2
    ):
        raise AssertionError("occupied prefill rows did not preserve the bounded cache frontier")
    return {
        "scope": (
            "Budget-method differential on one CPU fixture: 47 running plus one occupied "
            "prefill row. The old method supplies no occupied-row count, preserving its "
            "original budget behavior. Current selector/native stand-ins are shared; "
            "no request row, DMA completion, ACK, reuse or throughput is fabricated."
        ),
        "results": results,
    }


def native_budget_case(baseline, *, samples, running, free_rows):
    from types import MethodType
    from tests.test_wait_prefetch_batch import parallel_tool_batch_fixture

    variants = {}
    for name in ("baseline", "candidate"):
        runtime, _, _, plans, cache, _ = parallel_tool_batch_fixture()
        cache.req_to_token_pool = NS(
            size=48, available_size=lambda: free_rows, mamba_pool=NS(size=8),
        )
        cache.host_pool_group = cache.cache_controller.mem_pool_host
        cache.token_to_kv_pool_allocator = NS(size=1024)
        runtime._observe_native_admission_capacity(
            running_batch=NS(reqs=[NS(rid=f"running-{index}") for index in range(running)]),
            adder=NS(max_running_requests=48, max_prefill_bs=4,
                     page_size=1, rem_input_tokens=16),
        )
        if name == "baseline":
            runtime._current_residency_budget = MethodType(
                baseline._current_residency_budget, runtime,
            )
        variants[name] = (runtime, next(iter(plans.values())).headroom)
    try:
        budgets = {
            name: asdict(runtime._current_residency_budget(headroom=headroom))
            for name, (runtime, headroom) in variants.items()
        }
        return {
            "case": "native_restore_budget",
            "running_requests": running, "free_request_rows": free_rows,
            "occupied_request_rows": 48 - free_rows,
            "budgets": budgets,
            "costs": compare({
                name: lambda runtime=runtime, headroom=headroom: (
                    runtime._current_residency_budget(headroom=headroom)
                ) for name, (runtime, headroom) in variants.items()
            }, samples=samples),
            "scope": "CPU budget methods with shared native pool stand-ins; no request admission",
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def repeated_plan_case(baseline, *, samples, depth):
    variants = {}
    clock = [1000.]
    try:
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
            runtime, key, opportunity, _, _ = batch_fixture("tool_wait", node_count=depth)
            runtime.enable_h2d_opportunity_sampling = False
            rows = []
            runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
            variants[name] = runtime, cls._record_wait_prefetch_plan, key, opportunity, rows
        costs = {name: [] for name in variants}
        calls = 10
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", lambda: clock[0]):
            for sample in range(samples + 20):
                order = list(variants)
                if sample % 2:
                    order.reverse()
                for name in order:
                    runtime, method, key, opportunity, _ = variants[name]
                    started = time.thread_time_ns()
                    for _ in range(calls):
                        method(runtime, "tool_wait", key, opportunity, reason="no_cold_capacity")
                    elapsed = (time.thread_time_ns() - started) / calls / 1000.
                    if sample >= 20:
                        costs[name].append(elapsed)
                clock[0] += .6
        evidence = {
            name: {key: value for key, value in variant[4][0].items() if key != "ts_ms"}
            for name, variant in variants.items()
        }
        if evidence["baseline"] != evidence["candidate"]:
            raise AssertionError("initial diagnostic snapshot changed")
        if len(variants["candidate"][4]) != 1:
            raise AssertionError("unchanged candidate plan was periodically emitted")
        for runtime, _, key, opportunity, _ in variants.values():
            runtime._record_wait_prefetch_plan(
                "tool_wait", key, replace(opportunity, required_full_tokens=1),
                reason="no_cold_capacity",
            )
        return {
            "case": "unchanged_plan_without_diagnostic_sampling", "closure_nodes": depth + 1,
            "initial_evidence_equal": True, "changes_still_recorded": True,
            "periodic_rows_before_changed_plan": {
                name: len(value[4]) - 1 for name, value in variants.items()
            },
            "costs": {name: distribution(observed) for name, observed in costs.items()},
        }
    finally:
        for runtime, *_ in variants.values():
            runtime.close()


def join_scan_fixture(cls, *, workflows):
    runtime, queue = synthetic_runtime(cls, CausalFrontierScheduler, workflows, 4)
    runtime.enable_final_stage_prefetch = True
    runtime._h2d_samples.extend(((100, 10.),) * 3)
    reads = [0]

    class RunningRequest:
        def __init__(self, request_id):
            self.request_id = request_id

        @property
        def rid(self):
            reads[0] += 1
            return self.request_id

    runtime._native_running_batch = NS(reqs=[
        RunningRequest(request.rid) for request in queue[:48]
    ])
    for number in range(workflows):
        workflow = f"wf-{number}"
        parent, child_id = f"{workflow}-root", f"{workflow}-child-12"
        join_id = f"{workflow}-join-3"
        key = PrefillCandidateKey(
            f"root-{number}", workflow, parent, f"ctx-{parent}", 0, 0,
            f"root-session-{number}", 1,
        )
        runtime.context_sessions[key.context_id] = key
        runtime.graph.invocations[parent].join_id = join_id
        runtime._final_stages[join_id] = _ChildFinalStage(
            key, join_id, child_id, 0, 1000, time.monotonic() + 3600.,
            request_id=f"req-{child_id}", generated_tokens=100, tokens_per_second=40.,
        )
    if not all(runtime._live_final_stage(stage) for stage in runtime._final_stages.values()):
        raise AssertionError("JOIN CPU fixture has invalid dependencies")
    return runtime, reads


def join_scan_case(baseline, *, samples, workflows, running_requests=48):
    variants = {
        name: join_scan_fixture(cls, workflows=workflows)
        for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime))
    }
    try:
        membership_reads, graph_checks = {}, {}
        for name, (runtime, reads) in variants.items():
            runtime._native_running_batch.reqs = (
                runtime._native_running_batch.reqs[:running_requests]
            )
            with patch.object(runtime, "_live_final_stage",
                              wraps=runtime._live_final_stage) as checked:
                if runtime._roll_final_stage() is not None:
                    raise AssertionError("long report phase unexpectedly produced a restore plan")
            graph_checks[name] = checked.call_count
            membership_reads[name] = reads[0]
            runtime._native_running_batch = NS(reqs=[
                NS(rid=request.request_id) for request in runtime._native_running_batch.reqs
            ])
        costs = compare({
            name: runtime._roll_final_stage for name, (runtime, _) in variants.items()
        }, samples=samples, calls=10)
        if any(runtime._join_ticket is not None or runtime.physical_ledger.pending_count
               for runtime, _ in variants.values()):
            raise AssertionError("JOIN candidate scan issued a physical action")
        return {
            "case": "live_join_final_stage_scan", "workflows": workflows,
            "running_requests": min(running_requests, 48, workflows), "costs": costs,
            "running_membership_reads_per_scan": membership_reads,
            "full_graph_checks_per_scan": graph_checks,
            "membership_counts_measured_separately": True,
            "live_graph_dependencies_checked": True, "physical_actions": 0,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="31d6849")
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--scheduler-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output")
    paths = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py",
        "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/native_transfer_policy.py",
        "beliefkv/runtime/hotpath_timing.py",
        "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py",
        "scripts/benchmark_native_shared_path.py",
        "tests/test_wait_prefetch_batch.py",
        "tests/test_join_confirmed_reentry.py",
        "tests/test_join_parallel_prefetch.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    baseline, baseline_sources = baseline_runtime(args.baseline)
    cases = [
        *(empty_prepare_case(
            baseline, samples=args.samples, workflows=workflows, running=running,
        ) for workflows, running in ((24, 0), (24, 8), (156, 48))),
        *(completion_reconcile_case(
            baseline, samples=args.samples, source=source, commands=commands,
            missing=missing,
        ) for source in ("join", "tool") for commands in (1, 4, 16)
          for missing in (False, True)),
        *(registration_case(
            baseline, samples=args.samples, commands=commands, children=children,
        ) for commands, children in ((1, 1), (16, 1), (64, 1), (16, 4))),
    ] if args.scheduler_only else [
        *(idle_case(baseline, samples=args.samples, profiling=enabled)
          for enabled in (False, True)),
        *(pending_case(baseline, samples=args.samples, pending=pending)
          for pending in (0, 16, 64)),
        *(native_budget_case(
            baseline, samples=args.samples, running=running, free_rows=free_rows,
        ) for running, free_rows in ((40, 8), (47, 0), (48, 0))),
        saturated_join_case(baseline, samples=args.samples),
        saturated_provisional_join_case(baseline, samples=args.samples),
        *(native_ack_case(baseline, samples=args.samples, pending=pending, node_count=nodes)
          for pending in (0, 16, 64) for nodes in (1, 32, 128)),
        *(repeated_plan_case(baseline, samples=args.samples, depth=depth)
          for depth in (16, 128)),
        *(join_scan_case(
            baseline, samples=args.samples, workflows=workflows, running_requests=running,
        ) for workflows, running in ((48, 48), (156, 48), (512, 48), (156, 0))),
    ]
    for case in cases:
        costs = case["costs"]
        case["mean_thread_cpu_reduction_fraction"] = (
            1. - costs["candidate"]["mean_us"] / costs["baseline"]["mean_us"]
        )
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("CPU benchmark sources changed")
    report = {
        "scope": "CPU fixtures; no SGLang server, CUDA, DMA, throughput or JCT measurement",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "candidate_source_sha256": sources,
        "samples": args.samples, "warmup_samples": 20,
        "scheduler_only": args.scheduler_only,
        "cases": cases,
        "parallel_tool_conversion": (
            None if args.scheduler_only else parallel_tool_conversion_case(baseline)
        ),
        "parallel_confirmed_join_conversion": (
            None if args.scheduler_only else parallel_confirmed_join_conversion_case(baseline)
        ),
        "parallel_provisional_join_conversion": [] if args.scheduler_only else [
            provisional_join_conversion_case(baseline, signal=signal)
            for signal in ("eos", "work")
        ],
        "inflight_prefill_conversion": (
            None if args.scheduler_only else inflight_prefill_conversion_case(baseline)
        ),
        "limitations": [
            "Fixture frequency and pending populations are controlled, not replayed online frequencies.",
            "Both runtimes use the same profiling setting in each timing case.",
            "Removing repeated unchanged diagnostic rows intentionally changes observation density.",
            "Required final causal drain, prefix recapture, native capacity checks and ACK credit remain.",
            "Forced due PREPARE calls and bounded ACK histories are controlled scenes, not measured online frequencies.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(cases, indent=2), flush=True)


if __name__ == "__main__":
    main()
