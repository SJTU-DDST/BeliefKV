"""CPU-only grant observation cost with the real asynchronous evidence writer."""

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write
from tests.test_free_capacity_choices import waiting


def measure(directory, *, enabled, iterations, capacity, same_workflow):
    with pytest.MonkeyPatch.context() as patch:
        directory.mkdir()
        live, adapter, _requests, _records = waiting(
            patch, directory, count=8, tokens=100,
            capacity=capacity, same_workflow=same_workflow,
        )
        writer = NativeOpportunityTelemetry(directory / "journal")
        live._opportunity_writer = writer
        if not enabled:
            patch.setattr(adapter, "_observe_free_capacity_choice", None)
        keys = tuple(adapter._dispatch_pending.values())
        selected = adapter.policy.programs["a0"]
        samples = []
        process_started, wall_started = time.process_time_ns(), time.perf_counter_ns()
        try:
            for index in range(iterations + 16):
                started = time.thread_time_ns()
                granted = adapter._admit_from_free_capacity(
                    (selected,), event="agent_dispatch_free_capacity_admitted", join_id=None,
                    choice_keys=keys, choice_sequence=index + 1,
                )
                elapsed = time.thread_time_ns() - started
                if not granted:
                    raise ValueError("CPU observation fixture unexpectedly denied its original grant")
                selected.active = False
                if index >= 16:
                    samples.append(elapsed / 1000.)
        finally:
            live.close()
        process_ns = time.process_time_ns() - process_started
        wall_ns = time.perf_counter_ns() - wall_started
        status = json.loads(writer.status_path.read_bytes())
        if not status["complete"] or status["error"] is not None:
            raise ValueError("real asynchronous observation writer did not drain")
        return {
            "observer_enabled": enabled,
            "main_thread_grant_including_enqueue_us": distribution(samples),
            "process_cpu_including_writer_drain_ms": process_ns / 1e6,
            "wall_including_writer_drain_ms": wall_ns / 1e6,
            "grants_including_warmup": iterations + 16,
            "writer": status,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous observation cost evidence")
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
        or not 10 <= args.iterations <= 1000 or not 1 <= args.rounds <= 10
    ):
        raise ValueError("CPU fixture requires nice19, hidden CUDA and bounded iterations/rounds")
    results = {}
    with TemporaryDirectory(prefix="beliefkv-free-choice-cost-") as temporary:
        for name, capacity, same in (
            ("same_workflow_fast_path", 1000, True),
            ("cross_workflow_all_fit", 2000, False),
            ("cross_workflow_conflict", 1000, False),
        ):
            rounds = []
            for number in range(args.rounds):
                runs = {}
                for enabled in ((False, True) if number % 2 == 0 else (True, False)):
                    runs[str(enabled)] = measure(
                        Path(temporary) / f"{name}-{number}-{enabled}",
                        enabled=enabled, iterations=args.iterations,
                        capacity=capacity, same_workflow=same,
                    )
                before, after = runs["False"], runs["True"]
                rounds.append({
                    "disabled": before, "enabled": after,
                    "main_thread_median_difference_us": (
                        after["main_thread_grant_including_enqueue_us"]["p50"]
                        - before["main_thread_grant_including_enqueue_us"]["p50"]
                    ),
                    "process_cpu_difference_us_per_grant": (
                        (after["process_cpu_including_writer_drain_ms"]
                         - before["process_cpu_including_writer_drain_ms"])
                        * 1000. / (args.iterations + 16)
                    ),
                })
            results[name] = rounds
    write(args.output, {
        "iterations": args.iterations, "rounds": args.rounds, "results": results,
        "sources_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "beliefkv/runtime/reference_thunder_admission.py",
                "beliefkv/runtime/reference_free_continuation.py",
                "beliefkv/runtime/v0520_opportunity_telemetry.py",
                "tests/test_free_capacity_choices.py",
                str(Path(__file__).resolve().relative_to(ROOT)),
            )
        },
        "scope": {
            "real_asynchronous_writer_and_drain": True,
            "real_reference_grant_and_candidate_checks": True,
            "native_cache_is_CPU_standin": True,
            "fixed_eight_candidates_reset_between_grants": True,
            "warmup_included_in_process_totals_only": True,
            "microbenchmark_not_end_to_end_control_cost_or_GPU_performance": True,
            "GPU_action": False,
        },
    })
    print(json.dumps({
        name: [row["main_thread_median_difference_us"] for row in rounds]
        for name, rounds in results.items()
    }))


if __name__ == "__main__":
    main()
