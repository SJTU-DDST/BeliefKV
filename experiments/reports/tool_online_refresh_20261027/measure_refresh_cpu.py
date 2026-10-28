"""Measure the frozen refresh callback on CPU without touching the GPU run."""

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
REGISTRATION = ROOT.parent / "ops/tool_online_refresh_20261027_r1.registration.json"
PLAN = json.loads(REGISTRATION.read_bytes())
FROZEN = Path(PLAN["execution_checkout"])
sys.path.insert(0, str(FROZEN))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.online_workload_timing import OnlineWorkloadToolTiming
from beliefkv.predictor.tool_timing_prior import ToolTimingPrior
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from tests.test_sglang_v0520_runtime import req


def summary(values):
    return {
        "median": statistics.median(values),
        "p99": statistics.quantiles(values, n=100, method="inclusive")[98],
        "max": max(values),
    }


def fixture(prior):
    runtime = NativeAdmissionRuntime(enable_tool_prefetch=True)
    runtime._online_tool_timing = OnlineWorkloadToolTiming(prior=prior)
    runtime.predictor_sha256 = prior.sha256
    runtime.capture_shadow_candidate = Mock(
        side_effect=AssertionError("no physical ancestry in advisory refresh"),
    )
    sequence = 0
    attrs = {
        "tool_name": "execute", "timing_domain": "tracelab_network_categories_v2",
        "observed_timing_class": "WebSearch",
    }

    def send(kind, invocation=None, **extra):
        nonlocal sequence
        sequence += 1
        runtime.on_events((RuntimeEvent(
            f"bench-{sequence}", 100_000., kind, "wf",
            invocation_id=invocation,
            context_id=f"ctx-{invocation}" if invocation else None,
            attributes=extra,
        ),))

    # A full 64-completion history exercises blending and cached empirical CDFs.
    for index in range(64):
        identity = {**attrs, "tool_run_id": str(index)}
        for kind in (Kind.TOOL_START, Kind.TOOL_END):
            runtime._online_tool_timing.observe(RuntimeEvent(
                f"history-{kind.value}-{index}", 0., kind, "wf", invocation_id="history",
                attributes={**identity, "duration_ms": 10_000. + index * 1000.},
            ))
    send(Kind.WORKFLOW_START)
    for index in range(110):
        name = f"agent-{index}"
        send(Kind.INVOCATION_CREATE, name)
        request = req(name)
        request.session_id, request.session_generation = name, 1
        if not runtime.register_visible_request(request):
            raise ValueError("benchmark request did not bind")
        if index < 8:
            send(Kind.TOOL_START, name, **attrs, tool_run_id="active")
    if runtime._tool_timing_only or runtime._model_worker is not None:
        raise ValueError("benchmark is not the Full online empirical path")
    return runtime


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("run CPU-only at nice19")
    names = (
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/predictor/online_workload_timing.py",
        "beliefkv/runtime/sglang_v0520_prediction.py",
        "beliefkv/runtime/v0520_opportunity_telemetry.py",
    )
    sources = {name: sha256_file(FROZEN / name) for name in names}
    if any(PLAN["source_sha256"][name] != digest for name, digest in sources.items()):
        raise ValueError("measured callback differs from the frozen Full")
    prior = ToolTimingPrior.load(
        PLAN["reference_environment"]["BELIEFKV_TOOL_TIMING_PRIOR"],
        PLAN["reference_environment"]["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"],
    )
    results = {}
    with tempfile.TemporaryDirectory(prefix="beliefkv-refresh-cpu-") as directory:
        for mode in ("not_due", "due_but_per_context_backoff", "eight_recompute", "eight_recompute_uncached_index", "eight_recompute_with_async_journal"):
            with patch("time.monotonic", return_value=100.):
                runtime = fixture(prior)
                writer = None
                if mode.endswith("async_journal"):
                    writer = NativeOpportunityTelemetry(Path(directory) / mode)
                    runtime._opportunity_writer = writer
                now = 100_000.
                runtime._maintain_wait_predictions(now)
                runtime._prediction_maintenance_next_ms = now + 1000.
                before = Counter(runtime.counts)
                cpu, wall = [], []
                try:
                    for index in range(325):
                        runtime._tool_refresh_next_ms = now + 1000. if mode == "not_due" else 0.
                        if mode.startswith("eight_recompute"):
                            runtime._online_tool_queries.clear()
                        if mode == "eight_recompute_uncached_index":
                            runtime._wait_context_cache = None
                        begin_cpu, begin_wall = time.thread_time_ns(), time.perf_counter_ns()
                        runtime._maintain_wait_predictions(now)
                        elapsed_wall = (time.perf_counter_ns() - begin_wall) / 1000.
                        elapsed_cpu = (time.thread_time_ns() - begin_cpu) / 1000.
                        if index >= 25:
                            cpu.append(elapsed_cpu)
                            wall.append(elapsed_wall)
                    forecasts = runtime.counts["online_tool_supported"] - before["online_tool_supported"]
                    if forecasts != (325 * 8 if mode.startswith("eight_recompute") else 0):
                        raise ValueError("benchmark did not exercise its declared refresh work")
                    runtime.capture_shadow_candidate.assert_not_called()
                    results[mode] = {
                        "measured_callbacks": len(cpu), "actual_forecasts_including_warmup": forecasts,
                        "scheduler_thread_cpu_us": summary(cpu), "callback_wall_us": summary(wall),
                    }
                finally:
                    runtime.close()
                if writer is not None:
                    status = json.loads(writer.status_path.read_bytes())
                    if status["complete"] is not True or status["error"] is not None:
                        raise ValueError("benchmark async journal did not drain")
                    results[mode]["journal_status"] = status
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "frozen callback CPU fixture: 110 contexts, 8 active tools, 64 completed same-class samples",
        "measured_checkout": str(FROZEN), "application_commit": PLAN["code_commit"],
        "sources_sha256": sources, "scenarios": results,
        "limitations": [
            "Not an instrumentation of the historical GPU run or a whole-scheduler cost measurement.",
            "Monotonic policy time is frozen; CPU and wall measurement clocks are real.",
            "No native cache, DMA, physical offload, nonempty restore leases or concurrent event drain.",
            "The journal case includes scheduler-thread enqueue and real writer contention; writer CPU is separate.",
            "History, deadline resets and fixture setup are outside the timed callback.",
            "No fixture cost is extrapolated into removable JCT.",
        ],
        "operator_sha256": sha256_file(Path(__file__)),
        "gpu_or_jct_performance_verified": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({mode: {
        key: values[key] for key in ("scheduler_thread_cpu_us", "callback_wall_us")
    } for mode, values in results.items()}, indent=2))


if __name__ == "__main__":
    main()
