#!/usr/bin/env python3
"""Compare recurring runtime control with pinned code and real empty Unix sockets."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import InvocationState
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.hotpath_timing import HotpathTiming
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalTransactionLedger,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_native_shared_path import synthetic_runtime
from scripts.benchmark_prefetch_control_cpu import compare, registration_case


REMOVED_POLL_COUNTERS = frozenset((
    "physical_planning_after_forward", "tool_prefetch_scan_deferred",
    "prediction_maintenance_deferred", "prefetch_lease_maintenance_deferred",
))


def observed_poll_counts(path):
    if path is None:
        return None
    digest, final = hashlib.sha256(), None
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            row = json.loads(line)
            if row.get("event") == "admission_runtime_state" and row.get("final") is True:
                final = row
    if final is None or type(final.get("counts")) is not dict:
        raise ValueError("observed runtime lacks a complete final counter snapshot")
    return {
        "path": str(path.resolve()), "sha256": digest.hexdigest(),
        "final_poll_counters": {
            name: final["counts"].get(name, 0) for name in sorted(REMOVED_POLL_COUNTERS)
        },
        "scope": (
            "Historical diagnostic increments, not forward/completion rates or "
            "a workload-weighted timing, GPU or JCT saving."
        ),
    }


def physical_state(ledger):
    return {
        "pending": [asdict(value[0]) for value in ledger._pending.values()],
        "nodes": sorted(ledger._pending_nodes),
        "bytes": dict(ledger._pending_action_bytes),
        "counts": dict(ledger._pending_action_counts),
        "owners": sorted(ledger.pending_h2d_contexts),
    }


def command(index=0, *, children=1):
    return PhysicalActionExpectation(
        f"load-{index}", "PREFETCH_GPU", f"context-{index}", 0,
        tuple(PhysicalChildExpectation(
            index * 16 + child * 2, (index * 16 + child * 2,),
            (("kv", 20),), 20,
        ) for child in range(children)),
        (("kv", 10), ("mamba", 5)), session_id=f"session-{index}", session_generation=1,
    )


def validation_cases(baseline):
    baseline_ledger = baseline._roll_tool_prefetch.__globals__["PhysicalTransactionLedger"]
    variants = {"baseline": baseline_ledger, "candidate": PhysicalTransactionLedger}
    records = []
    amounts = (
        (("kv", 20),), (("mamba", 15),), (("kv", 20), ("mamba", 15)),
        (("mamba", 0), ("kv", 20)), (["kv", 20],), (),
        (("kv", True),), (("kv", -10),), (("kv", 20.0),), (("kv", 11),),
        (("mamba", 6),), (("kv", 20), ("kv", 0)),
        (("mamba", 5), ("mamba", 5)), (("swa", 20),), (("", 0),),
        (([], 10),), (("kv",),), ("kv",),
    )
    for children in (1, 2):
        for pools in amounts:
            original = command(children=children)
            part = replace(original.children[-1], pool_bytes=pools, num_bytes=35)
            expected = replace(original, children=(*original.children[:-1], part))
            outcomes = {}
            for name, cls in variants.items():
                ledger = cls()
                try:
                    ledger.register(expected)
                    accepted = True
                except Exception as error:
                    if type(error).__name__ != "PhysicalReceiptError":
                        raise
                    accepted = False
                outcomes[name] = {"accepted": accepted, "state": physical_state(ledger)}
            if outcomes["baseline"] != outcomes["candidate"]:
                raise AssertionError(f"registration validation changed: {expected}")
            records.append({
                "children": children, "pool_bytes": pools,
                "accepted": outcomes["candidate"]["accepted"],
                "state_and_validation_equal": True,
            })
    return records


def socket_case(baseline, *, samples, profiling):
    servers = {}
    with tempfile.TemporaryDirectory(prefix="bkv-empty-control-") as temporary:
        try:
            for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
                server_cls = cls._roll_tool_prefetch.__globals__["RuntimeEventDatagramServer"]
                servers[name] = server_cls(
                    Path(temporary) / f"{name}.sock", lambda events: None,
                    timing=HotpathTiming(enabled=profiling),
                )
            costs = compare(
                {name: server.drain for name, server in servers.items()}, samples=samples,
            )
            if any(server.drain() != () or server._acks for server in servers.values()):
                raise AssertionError("empty receive created a delivery or ACK")
            return {
                "case": "real_empty_socket_drain", "profiling": profiling,
                "costs": costs, "no_delivery_or_ack": True,
            }
        finally:
            for server in servers.values():
                server.close()


def predictor_hook(engine):
    path = engine / "python/sglang/srt/managers/scheduler.py"
    source = path.read_text()
    scheduler = next(node for node in ast.parse(source).body
                     if isinstance(node, ast.ClassDef) and node.name == "Scheduler")
    method = next(node for node in scheduler.body if isinstance(node, ast.FunctionDef)
                  and node.name == "get_next_batch_to_run")
    runtime_block = next(
        node for node in method.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.NamedExpr)
        and isinstance(node.test.left.target, ast.Name)
        and node.test.left.target.id == "beliefkv_runtime"
    )
    idle_block = next(
        node for node in runtime_block.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and node.test.left.attr == "idle_sleeper"
    )
    function = ast.FunctionDef(
        name="refresh_predictor_fds",
        args=ast.arguments(
            posonlyargs=[], args=[
                ast.arg(arg="self"), ast.arg(arg="beliefkv_runtime"),
            ], kwonlyargs=[], kw_defaults=[], defaults=[],
        ),
        body=[idle_block], decorator_list=[],
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[function], type_ignores=[],
    )), str(path), "exec"), namespace)
    return namespace[function.name], {
        "path": str(path.resolve()), "sha256": hashlib.sha256(source.encode()).hexdigest(),
        "scope": "actual idle_sleeper block from get_next_batch_to_run",
    }


def recurring_case(
    baseline, *, samples, workflows, running, pending, profiling, due,
    prepare=False, active_hints=False, fd_hooks=None, workers=0, idle_sleeper=True,
):
    variants = {}
    pipes = [os.pipe() for _ in range(workers)]
    with tempfile.TemporaryDirectory(prefix="bkv-recurring-control-") as temporary:
        try:
            for name, cls in (("baseline", baseline), ("candidate", NativeAdmissionRuntime)):
                runtime, queue = synthetic_runtime(
                    cls, CausalFrontierScheduler, workflows, 16, profiling=profiling,
                )
                scheduler = NS(
                    idle_sleeper=NS(poller=select.poll()) if idle_sleeper else None,
                    _beliefkv_predictor_fds=set(), _beliefkv_predictor_fd=None,
                )
                variants[name] = runtime, NS(reqs=queue[:running]), scheduler
                runtime._tool_timing_only = True
                runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
                runtime.enable_prepare_host = prepare
                runtime._join_prepare_next_ms = runtime._tool_prepare_next_ms = 1e100
                runtime._tool_prefetch_next_ms = 1e100
                runtime._prediction_maintenance_interval_ms = 100.
                runtime._prediction_maintenance_next_ms = 1e100
                runtime._tool_refresh_next_ms = 1e100
                for invocation in runtime.graph.invocations.values():
                    if invocation.state is InvocationState.WAIT_JOIN:
                        invocation.state = InvocationState.READY
                if active_hints:
                    for request in queue[running:running + 8]:
                        key = runtime.visible[request.rid]
                        invocation = runtime.graph.invocations[key.invocation_id]
                        invocation.state = InvocationState.WAIT_TOOL
                        runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
                            key, 10_000., 20_000., 30_000., 0., 1e100,
                            runtime.predictor_sha256, invocation.updated_ts_ms,
                        )
                runtime._prediction_maintenance_graph_version = runtime.graph.graph_version
                for index, (fd, _) in enumerate(pipes):
                    worker = NS(
                        disabled=False, fileno=lambda fd=fd: fd,
                        poll=lambda: (), close=lambda: None,
                    )
                    if index == 0:
                        runtime._model_worker = worker
                    else:
                        runtime._semantic_worker = worker
                names = cls._roll_tool_prefetch.__globals__
                runtime.event_server = names["RuntimeEventDatagramServer"](
                    Path(temporary) / f"{name}.sock", runtime.on_events,
                    timing=runtime._hotpath_timing,
                )
                for index in range(pending):
                    runtime.physical_ledger.register(command(index))
            before = {
                name: physical_state(runtime.physical_ledger)
                for name, (runtime, _, _) in variants.items()
            }

            def cycle(name, runtime, batch, scheduler):
                if due:
                    runtime._prediction_maintenance_next_ms = 0.
                runtime.scheduler_step(defer_observations=True)
                if fd_hooks is not None:
                    fd_hooks[name](scheduler, runtime)
                runtime.dispatch_after_forward(running_batch=batch)
                runtime.on_batch_completed(batch)

            costs = compare({
                name: lambda name=name, runtime=runtime, batch=batch, scheduler=scheduler:
                    cycle(name, runtime, batch, scheduler)
                for name, (runtime, batch, scheduler) in variants.items()
            }, samples=samples)
            states, counters = {}, {}
            for name, (runtime, batch, scheduler) in variants.items():
                current = physical_state(runtime.physical_ledger)
                if current != before[name] or runtime.physical_disabled or (
                    runtime.completed_physical_actions
                ):
                    raise AssertionError("recurring control changed a physical reservation")
                states[name] = {
                    "physical": current, "visible": sorted(runtime.visible),
                    "tokens": dict(runtime._context_tokens),
                    "semantic_revision": runtime.semantic_revision,
                    "tool_hints": {
                        key: asdict(hint) for key, hint in runtime.tool_wait_hints.items()
                    },
                    "registered_fds": sorted(scheduler._beliefkv_predictor_fds),
                    "legacy_fd": scheduler._beliefkv_predictor_fd,
                    "running_batch_identity_retained": runtime._native_running_batch is batch,
                    "positive_action_counts": {
                        key: value for key, value in runtime.counts.items()
                        if value and key not in REMOVED_POLL_COUNTERS
                        and not key.startswith("prediction_maintenance_")
                    },
                }
                counters[name] = {
                    key: runtime.counts[key] for key in (
                        "prediction_maintenance_sweeps", "prediction_maintenance_deferred",
                        "physical_expired", "physical_planning_after_forward",
                        "tool_prefetch_scan_deferred",
                        "prefetch_lease_maintenance_deferred",
                    )
                }
            if states["baseline"] != states["candidate"]:
                raise AssertionError("recurring control changed visible or physical state")
            return {
                "case": "scheduler_dispatch_and_batch_completion", "workflows": workflows,
                "running_requests": running, "pending_commands": pending,
                "profiling": profiling, "maintenance_forced_due": due,
                "prepare_enabled_with_original_deadlines_deferred": prepare,
                "active_tool_hints": len(next(iter(variants.values()))[0].tool_wait_hints),
                "synthetic_live_workers": workers,
                "native_idle_sleeper_enabled": idle_sleeper,
                "includes_native_predictor_registration": fd_hooks is not None,
                "configured_maintenance_interval_ms": 100.,
                "costs": costs, "state_and_actions_equal": True,
                "maintenance_diagnostic_counts": counters,
            }
        finally:
            for runtime, _, _ in variants.values():
                runtime.close()
            for reader, writer in pipes:
                os.close(reader)
                os.close(writer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="fc9fea7")
    parser.add_argument("--samples", type=int, default=151)
    parser.add_argument("--registration-only", action="store_true")
    parser.add_argument("--recurring-only", action="store_true")
    parser.add_argument("--active-hints", action="store_true")
    parser.add_argument("--prepare-enabled", action="store_true")
    parser.add_argument("--baseline-engine", type=Path)
    parser.add_argument("--candidate-engine", type=Path)
    parser.add_argument("--predictor-workers", type=int, nargs="+", default=[0], choices=(0, 1, 2))
    parser.add_argument("--idle-sleeper", type=int, nargs="+", default=[0, 1], choices=(0, 1))
    parser.add_argument("--observed-runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("use positive samples and a fresh output")
    if args.recurring_only and args.registration_only:
        parser.error("select recurring or registration cases")
    if bool(args.baseline_engine) != bool(args.candidate_engine):
        parser.error("both engine sources are required for hook comparisons")
    paths = (
        "beliefkv/runtime/event_channel.py", "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/runtime/sglang_v0520_physical.py", "beliefkv/runtime/sglang_v0520_observer.py",
        "beliefkv/runtime/hotpath_timing.py", "scripts/benchmark_recurring_control_cpu.py",
        "scripts/benchmark_native_prepare_path.py", "scripts/benchmark_prefetch_control_cpu.py",
        "scripts/benchmark_native_shared_path.py",
    )
    sources = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    observed = observed_poll_counts(args.observed_runtime)
    baseline, baseline_sources = baseline_runtime(args.baseline)
    hooks, hook_sources = None, {}
    if args.baseline_engine:
        hooks = {}
        for name, engine in (
            ("baseline", args.baseline_engine), ("candidate", args.candidate_engine),
        ):
            hooks[name], hook_sources[name] = predictor_hook(engine)
    validation = validation_cases(baseline)
    cases = [] if args.registration_only else [
        *(socket_case(baseline, samples=args.samples, profiling=profiling)
          for profiling in (False, True)),
        *(recurring_case(
            baseline, samples=args.samples, workflows=workflows, running=running,
            pending=pending, profiling=profiling, due=due,
            prepare=args.prepare_enabled, active_hints=active,
            fd_hooks=hooks, workers=workers, idle_sleeper=bool(sleeper),
        ) for workflows, running in ((12, 8), (24, 8), (156, 48))
          for pending in (0, 16) for profiling in (False, True) for due in (False, True)
          for active in ((False, True) if args.active_hints else (False,))
          for workers in args.predictor_workers
          for sleeper in (args.idle_sleeper if hooks is not None else (0,))),
    ]
    if not args.recurring_only:
        cases.extend(
            registration_case(
                baseline, samples=args.samples, commands=commands, children=children,
                pools=pools, published_nodes=nodes,
            ) for commands, children, nodes in ((1, 1, 1), (64, 1, 1), (16, 4, 1), (16, 1, 2))
              for pools in ("full", "mamba", "hybrid")
        )
    for case in cases:
        case["p50_thread_cpu_reduction_fraction"] = (
            1. - case["costs"]["candidate"]["p50_us"] / case["costs"]["baseline"]["p50_us"]
        )
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest
           for path, digest in sources.items()):
        raise RuntimeError("benchmark sources changed")
    if any(hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest() != info["sha256"]
           for info in hook_sources.values()):
        raise RuntimeError("engine hook source changed")
    report = {
        "status": "complete_cpu_comparison_not_gpu_or_jct_gain",
        "scope": "CPU runtime/ledger with real Unix sockets; no serving, CUDA, DMA or JCT",
        "at": datetime.now().astimezone().isoformat(),
        "baseline_revision": args.baseline, "baseline_source_sha256": baseline_sources,
        "candidate_source_sha256": sources, "samples": args.samples,
        "registration_only": args.registration_only,
        "recurring_only": args.recurring_only, "engine_hook_sources": hook_sources,
        "removed_poll_counters": sorted(REMOVED_POLL_COUNTERS),
        "observed_runtime": observed,
        "warmup_samples": 20, "cases": cases, "validation_cases": validation,
        "limitations": [
            "Prediction inventories and physical pending populations are controlled scenes.",
            "Forced-due and deferred scenes are measured separately, not their online mixture.",
            "Unexpired physical reservations do not represent native DMA or active service leases.",
            "PREPARE-enabled scenes defer its existing deadlines; they do not measure backups.",
            "Synthetic workers expose real idle pipe fds; no predictor process or model is run.",
            "The native block is included, not the complete get_next_batch_to_run method.",
            "Empty sweeps/counters are intentionally removed; action/state comparisons exclude those diagnostics.",
            "Microseconds and controlled fractions are not throughput, JCT or prediction-accuracy gains.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "timing_cases": len(cases),
        "validation_cases": len(validation),
        "p50_reduction_range": (
            min(case["p50_thread_cpu_reduction_fraction"] for case in cases),
            max(case["p50_thread_cpu_reduction_fraction"] for case in cases),
        ),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
