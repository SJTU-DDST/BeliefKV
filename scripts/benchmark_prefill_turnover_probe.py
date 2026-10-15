#!/usr/bin/env python3
"""Measure incremental probe CPU cost using actual scheduler-loop source."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.prefill_turnover_probe import PrefillTurnoverProbe


def hook(engine: Path, name: str):
    path = engine / "test/srt/test_beliefkv_scheduler_hook.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def historical_probe(revision: str):
    path = "beliefkv/runtime/prefill_turnover_probe.py"
    source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
    module = ModuleType("_beliefkv_turnover_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{revision}:{path}", "exec"), vars(module))
    return module.PrefillTurnoverProbe, hashlib.sha256(source).hexdigest()


def measure(
    module, *, iterations: int, prefill_every: int, sampled: bool,
    probe_class=PrefillTurnoverProbe,
):
    scheduler, running, _ = module.idle_scheduler(overlap=True)
    calls, records = Counter(), Counter()
    probe = (
        probe_class(lambda _domain, row: records.update([row["event"]]))
        if sampled else None
    )
    scheduler._beliefkv_prefill_turnover_probe = probe
    scheduler.running_batch = running
    scheduler.last_batch = None
    scheduler.device_module = scheduler.forward_stream = None
    scheduler.gracefully_exit = scheduler._engine_paused = False
    scheduler.is_generation = False
    scheduler.ingest_requests = lambda: None
    scheduler.is_disable_overlap_for_batch = lambda *args, **kwargs: False
    scheduler._apply_war_barrier = lambda: calls.update(["war"])
    scheduler.process_batch_result = lambda *_args: calls.update(["result"])
    scheduler.beliefkv_runtime = NS(
        dispatch_after_forward=lambda *_args, **_kwargs: calls.update(["physical"])
    )
    result = NS(can_run_cuda_graph=True)
    decode_requests = [NS(rid=f"decode-{i}") for i in range(48)]
    sequence = 0

    def select(**_kwargs):
        nonlocal sequence
        scheduler._beliefkv_physical_planning_deferred = True
        if sequence == iterations:
            scheduler.gracefully_exit = True
            return module.PLAN(None, running)
        prefill = sequence % prefill_every == 0
        requests = [NS(rid=f"prefill-{sequence}")] if prefill else decode_requests
        selected = NS(
            reqs=requests, forward_iter=0, launch_ts=0.,
            forward_mode=NS(is_extend=lambda: prefill),
            extend_num_tokens=80 if prefill else 0,
            prefix_lens=[16384] if prefill else None,
        )
        selected.copy = lambda: NS(**{
            key: value for key, value in vars(selected).items() if key != "copy"
        })
        sequence += 1
        return module.PLAN(selected, running)

    def forward(selected):
        calls.update(["forward"])
        selected.forward_iter = sequence
        selected.launch_ts = time.monotonic()
        if probe is not None:
            probe.on_launch(selected)
        return result

    scheduler.get_next_batch_to_run, scheduler.run_batch = select, forward
    cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
    scheduler.event_loop_overlap()
    elapsed_cpu, elapsed_wall = time.thread_time_ns() - cpu, time.perf_counter_ns() - wall
    if probe is not None:
        assert probe.snapshot()["pending_samples"] == 0
        assert probe.counts["selected_without_launch"] == 0
        assert probe.counts["completed_samples"] == sum(records.values())
    assert calls == Counter(
        forward=iterations, result=iterations, war=iterations, physical=iterations + 1,
    )
    return {
        "cpu_ns_per_iteration": elapsed_cpu / iterations,
        "wall_ns_per_iteration": elapsed_wall / iterations,
        "dispatch_counts": dict(calls), "sample_counts": dict(records),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-engine", type=Path, required=True)
    parser.add_argument("--candidate-engine", type=Path, required=True)
    parser.add_argument("--baseline-probe-revision",
                        help="Compare the pinned old probe with the current probe on the same loop.")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.iterations, args.repetitions) < 1 or args.output.exists():
        raise ValueError("positive work and a new output path required")
    modules = {
        "baseline": hook(args.baseline_engine, "turnover_baseline"),
        "candidate": hook(args.candidate_engine, "turnover_candidate"),
    }
    baseline_probe, baseline_digest = (
        historical_probe(args.baseline_probe_revision)
        if args.baseline_probe_revision else (None, None)
    )
    cases = []
    for prefill_every in (1, 5, 16):
        results = {name: [] for name in modules}
        for repetition in range(args.repetitions + 1):
            names = list(modules)
            if repetition % 2:
                names.reverse()
            for name in names:
                observed = measure(
                    modules[name], iterations=args.iterations,
                    prefill_every=prefill_every,
                    sampled=name == "candidate" or baseline_probe is not None,
                    probe_class=baseline_probe if name == "baseline" else PrefillTurnoverProbe,
                )
                if repetition:
                    results[name].append(observed)
        costs = {
            name: {
                field: {"mean": mean(row[field] for row in rows),
                        "p50": median(row[field] for row in rows)}
                for field in ("cpu_ns_per_iteration", "wall_ns_per_iteration")
            }
            for name, rows in results.items()
        }
        cases.append({
            "prefill_every": prefill_every, "costs": costs, "samples": results,
            "dispatch_counts_equal": (
                results["baseline"][0]["dispatch_counts"]
                == results["candidate"][0]["dispatch_counts"]
            ),
            "incremental_cpu_ns_per_iteration": (
                costs["candidate"]["cpu_ns_per_iteration"]["mean"]
                - costs["baseline"]["cpu_ns_per_iteration"]["mean"]
            ),
        })
    report = {
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "iterations": args.iterations, "repetitions": args.repetitions,
        "sampling_interval": 16, "cases": cases,
        "baseline_probe_revision": args.baseline_probe_revision,
        "baseline_probe_sha256": baseline_digest,
        "baseline_probe_enabled": baseline_probe is not None,
        "engine_sources": {
            name: {
                "engine": str(engine.resolve()),
                "scheduler_sha256": hashlib.sha256(
                    (engine / "python/sglang/srt/managers/scheduler.py").read_bytes()
                ).hexdigest(),
            }
            for name, engine in (
                ("baseline", args.baseline_engine), ("candidate", args.candidate_engine),
            )
        },
        "probe_sha256": hashlib.sha256(
            (ROOT / "beliefkv/runtime/prefill_turnover_probe.py").read_bytes()
        ).hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": (
            "Actual overlap-loop AST with stubbed forward/selection/physical work. "
            "Measures incremental CPU observation cost and preserves dispatch counts; "
            "no CUDA events, writer JSON cost, GPU overhead, native-relative throughput "
            "or JCT proof. A supplied baseline revision keeps the old probe enabled."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        key: case[key] for key in (
            "prefill_every", "dispatch_counts_equal", "incremental_cpu_ns_per_iteration",
        )
    } for case in cases], indent=2))


if __name__ == "__main__":
    main()
