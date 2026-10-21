#!/usr/bin/env python3
"""Measure scoped forecast invalidation using sealed current tool histories."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.benchmark_native_prepare_path import baseline_runtime
from scripts.benchmark_online_tool_lookup_cpu import baseline_class, tool_start
from scripts.benchmark_prefetch_control_cpu import distribution
from tests.test_tool_predictive_transfers import tool_runtime


def end(workflow, invocation, call, duration, completed=True):
    return NS(
        kind=RuntimeEventKind.TOOL_END, workflow_id=workflow, invocation_id=invocation,
        ts_ms=duration, attributes={
            "tool_run_id": call, "duration_ms": duration, "censored": not completed,
        },
    )


def start(signature, invocation, *, workflow, call, at=0.):
    event = tool_start(signature, invocation, call=call, start=at)
    event.workflow_id = workflow
    return event


def build(cls, model_cls, signature, history, now):
    with patch("tests.test_tool_predictive_transfers.NativeAdmissionRuntime", cls):
        runtime, hint = tool_runtime()
    model = runtime._online_tool_timing = model_cls()
    runtime._tool_timing_only = True
    workflow, invocation = hint.key.root_workflow_id, hint.key.invocation_id
    for index, (duration, completed) in enumerate(history):
        call = str(index)
        model.observe(start(signature, "past", workflow=workflow, call=call))
        model.observe(end(workflow, "past", call, duration, completed))
    model.observe(start(signature, invocation, workflow=workflow, call="active", at=now * 1000.))
    runtime._submit_online_tool_wait(hint.key.context_id)
    return runtime, hint.key


def hint_value(runtime, key):
    hint = runtime.tool_wait_hints.get(key.context_id)
    return None if hint is None else (
        hint.wait_p10_ms, hint.wait_p50_ms, hint.wait_p90_ms, hint.release_cdf,
        hint.invocation_revision_ts_ms, hint.expires_monotonic_ms,
    )


def measure(
    classes, model_classes, *, signature, history, case, samples,
    baseline_scoped_history_retries=False,
):
    now = 1_000_000.
    with patch("time.monotonic", lambda: now):
        variants = {
            name: build(cls, model_classes[name], signature, history, now)
            for name, cls in classes.items()
        }
    values = {name: [] for name in variants}
    initial_counts = {
        name: runtime.counts["online_tool_supported"] for name, (runtime, _) in variants.items()
    }
    try:
        for iteration in range(samples + 10):
            for name in ("baseline", "candidate")[::1 if iteration % 2 else -1]:
                runtime, key = variants[name]
                model = runtime._online_tool_timing
                if case in ("related_completion", "unrelated_completion"):
                    updated_signature = (
                        signature if case == "related_completion"
                        else ("cpu-unrelated", "independent-command")
                    )
                    call = f"update-{iteration}"
                    model.observe(start(
                        updated_signature, "updated", workflow=key.root_workflow_id, call=call,
                    ))
                    model.observe(end(
                        key.root_workflow_id, "updated", call, history[-1][0], history[-1][1],
                    ))
                if case == "retry_due":
                    previous = runtime._online_tool_queries[key]
                    runtime._online_tool_queries[key] = (*previous[:2], 0.)
                with patch("time.monotonic", lambda: now):
                    before = time.thread_time_ns()
                    runtime._submit_online_tool_wait(key.context_id)
                    elapsed = (time.thread_time_ns() - before) / 1000.
                if iteration >= 10:
                    values[name].append(elapsed)
                assert not runtime.physical_disabled and not runtime.physical_ledger.pending_count
            if case != "unrelated_completion":
                assert hint_value(*variants["baseline"]) == hint_value(*variants["candidate"])
        supported = {
            name: runtime.counts["online_tool_supported"] - initial_counts[name]
            for name, (runtime, _) in variants.items()
        }
        if case == "unrelated_completion":
            assert supported["baseline"] == (
                0 if baseline_scoped_history_retries else samples + 10
            )
            assert supported["candidate"] == 0
            assert hint_value(*variants["baseline"]) == hint_value(*variants["candidate"])
        return {
            "case": case, "signature": signature, "history_samples": len(history),
            "supported_recomputations_including_warmup": supported,
            "costs": {
                name: {**distribution(costs), "min_us": min(costs),
                       "max_us": max(costs), "samples_us": costs}
                for name, costs in values.items()
            },
            "relevant_or_due_hints_equal": True, "physical_actions": 0,
        }
    finally:
        for runtime, _ in variants.values():
            runtime.close()


def observed_history(arm, baseline_model):
    sealed, events = SealedRecords(), []
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        events.extend(NS(
            kind=RuntimeEventKind(row["kind"]), workflow_id=row["workflow_id"],
            invocation_id=row.get("invocation_id"), ts_ms=row["ts_ms"],
            attributes=row.get("attributes") or {},
        ) for row in sealed(path))
    if not events:
        raise ValueError("no live tool lifecycle input")
    models = {"baseline": baseline_model(), "candidate": OnlineWorkloadToolTiming()}
    checks = Counter()
    observe_costs = {name: [] for name in models}
    end_costs = {name: [] for name in models}
    for event in sorted(events, key=lambda item: item.ts_ms):
        before = {
            owner: models["candidate"].estimate_revision(*owner)
            for owner, active in models["candidate"]._open_by_invocation.items()
            if len(active) == 1
        } if event.kind is RuntimeEventKind.TOOL_END else {}
        revision = models["candidate"].revision
        for name, model in models.items():
            cpu = time.thread_time_ns()
            model.observe(event)
            elapsed = (time.thread_time_ns() - cpu) / 1000.
            observe_costs[name].append(elapsed)
            if event.kind is RuntimeEventKind.TOOL_END:
                end_costs[name].append(elapsed)
        if models["candidate"].revision == revision:
            continue
        for owner, witness in before.items():
            current = models["candidate"].estimate_revision(*owner)
            if current is None:
                continue
            checks["live_single_wait_history_changes_checked"] += 1
            checks[
                "relevant_history_changed" if current != witness
                else "unrelated_global_history_changes"
            ] += 1
            estimates = [
                model.estimate(*owner, now_ms=event.ts_ms) for model in models.values()
            ]
            values = [asdict(value) if value is not None else None for value in estimates]
            assert values[0] == values[1]
            checks["exact_estimate_controls"] += 1
    assert models["baseline"].samples == models["candidate"].samples
    assert models["baseline"].open == models["candidate"].open
    assert models["candidate"].samples.keys() == models["candidate"]._history_revisions.keys()
    supported = [
        (key[1:], list(history))
        for key, history in models["candidate"].samples.items()
        if key[0] == "*" and sum(completed for _, completed in history) >= 8
    ]
    supported.sort(key=lambda item: (-len(item[1]), item[0]))
    return supported[:3], {
        "input_jsonl_prefixes": sealed.prefixes, "events": len(events),
        "dependency_checks": dict(checks),
        "observe_cost_per_event_us": {
            name: {**distribution(values), "samples_us": values}
            for name, values in observe_costs.items()
        },
        "observe_cost_per_tool_end_us": {
            name: {**distribution(values), "samples_us": values}
            for name, values in end_costs.items()
        },
        "complete_model_histories_and_open_calls_equal": True,
        "semantics": (
            "Independent complete-line prefixes, not an atomic snapshot. Unrelated "
            "history changes are dependency checks, not actual saved runtime queries."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--baseline", default="ed06f53")
    parser.add_argument("--baseline-scoped-history-retries", action="store_true")
    parser.add_argument("--samples", type=int, default=101)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.samples < 1:
        parser.error("positive samples and a new output are required")
    paths = (
        "beliefkv/predictor/online_workload_timing.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "scripts/benchmark_online_tool_dependency_cpu.py",
        "tests/test_tool_predictive_transfers.py",
    )
    sources = {
        str(ROOT / path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
    }
    before, bindings = baseline_runtime(args.baseline)
    before_model, model_bindings = baseline_class(args.baseline)
    histories, replay = observed_history(args.arm, before_model)
    if not histories:
        raise ValueError("no supported current observed history")
    cases = [
        measure(
            {"baseline": before, "candidate": NativeAdmissionRuntime},
            {"baseline": before_model, "candidate": OnlineWorkloadToolTiming},
            signature=signature, history=history, case=case, samples=args.samples,
            baseline_scoped_history_retries=args.baseline_scoped_history_retries,
        )
        for signature, history in histories
        for case in ("unchanged", "unrelated_completion", "related_completion", "retry_due")
    ]
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
               for path, value in sources.items())
    report = {
        "status": "cpu_forecast_dependency_measurement_not_gpu_acceptance",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_revision": args.baseline, "baseline_sources": bindings | model_bindings,
        "baseline_scoped_history_retries": args.baseline_scoped_history_retries,
        "source_sha256": sources, "gc_enabled": gc.isenabled(),
        "samples_per_variant": args.samples, "observed_trace_replay": replay,
        "cases": cases,
        "scope": (
            "Complete advisory forecast submission CPU and current sealed history "
            "replay. No transfer or scheduler execution, no aggregate CPU/JCT inference."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "dependency_checks": replay["dependency_checks"],
        "cases": [{
            "case": case["case"], "signature": case["signature"],
            "p50_us": {name: value["p50_us"] for name, value in case["costs"].items()},
        } for case in cases],
    }, indent=2))


if __name__ == "__main__":
    main()
