"""Compare one fully published common throughput window while full JCT tails continue."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import _empty_inflight_tail, validate_cohort
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.plot_causal_decode_diagnostics import weighted_cdf
from scripts.plot_workflow_cohort_comparison import COLORS
from scripts.review_predictive_thunder_cohort import matching_serving_traffic
from scripts.stop_after_measured_cohort import boundaries
from scripts.train_workflow_work import write


def extract_window(cohort, arrivals, events, samples, *, bin_seconds=30.):
    validate_cohort(cohort, arrivals)
    if type(bin_seconds) not in (int, float) or not math.isfinite(bin_seconds) or bin_seconds <= 0:
        raise ValueError("closed-window bin width must be finite and positive")
    starts, ends = {}, {}
    schedule = arrivals["arrival_schedule"]
    ids = [row["workflow_id"] for row in schedule]
    boundaries(events, starts, ends, set(ids))
    if not starts or set(starts) != set(ids[:len(starts)]) or not ends.keys() <= starts.keys():
        raise ValueError("closed window requires a legal observed arrival prefix")
    origins = [starts[row["workflow_id"]] - row["offset_seconds"] * 1000.
               for row in schedule if row["workflow_id"] in starts]
    if max(origins) - min(origins) > 1e-5 or any(ends[key] < starts[key] for key in ends):
        raise ValueError("closed window changed the original arrival clock")
    origin = origins[0]
    start, end = cohort["throughput_window_offset_seconds"]
    if any(row["offset_seconds"] <= end and row["workflow_id"] not in starts for row in schedule):
        raise ValueError("a scheduled arrival inside the common window is missing")
    bins = [{
        "start_s": start + index * bin_seconds,
        "end_s": min(start + (index + 1) * bin_seconds, end),
        "output_tokens": 0, "decode_tokens": 0, "decode_batches": 0,
    } for index in range(math.ceil((end - start) / bin_seconds))]
    seen, clocks, widths = set(), set(), Counter()
    previous, latest = -math.inf, -math.inf
    for sample in samples:
        if sample.get("event") != "gpu_service_sample":
            continue
        forward, when = sample["forward_iter"], sample["complete_monotonic_ms"]
        if (
            type(forward) is not int or forward < 0 or forward in seen
            or type(when) not in (int, float) or not math.isfinite(when) or when < previous
            or sample["phase"] not in ("prefill", "decode")
        ):
            raise ValueError("closed-window native samples have invalid order, identity or phase")
        seen.add(forward)
        clocks.add(sample["monotonic_clock_domain"])
        previous = when
        latest = max(latest, (when - origin) / 1000.)
        offset = (when - origin) / 1000.
        if not start <= offset < end:
            continue
        if any(
            member["workflow_id"] not in starts
            or type(member.get("output_token_delta")) is not int
            or member["output_token_delta"] < 0
            or when < starts[member["workflow_id"]]
            or member["workflow_id"] in ends
            and when > ends[member["workflow_id"]] and not _empty_inflight_tail(
                sample, member, start=starts[member["workflow_id"]], end=ends[member["workflow_id"]],
            )
            for member in sample["request_samples"]
        ):
            raise ValueError("closed window contains foreign, invalid or post-completion output")
        output = sum(member["output_token_delta"] for member in sample["request_samples"])
        index = min(int((offset - start) / bin_seconds), len(bins) - 1)
        bins[index]["output_tokens"] += output
        if sample["phase"] == "decode":
            bins[index]["decode_tokens"] += output
            bins[index]["decode_batches"] += 1
            widths[sample["batch_size"]] += 1
    if not seen or len(clocks) != 1 or latest < end:
        raise ValueError("the whole fixed common window has not been published in one native clock")
    for row in bins:
        duration = row["end_s"] - row["start_s"]
        row["output_tokens_per_second"] = row["output_tokens"] / duration
        row["decode_tokens_per_second"] = row["decode_tokens"] / duration
    output, decode = (sum(row[name] for row in bins) for name in ("output_tokens", "decode_tokens"))
    return {
        "window_s": [start, end], "bins": bins, "output_tokens": output, "decode_tokens": decode,
        "output_tokens_per_second": output / (end - start),
        "decode_tokens_per_second": decode / (end - start),
        "decode_batch_counts": dict(sorted(widths.items())),
        "last_published_native_completion_offset_s": latest,
        "observed_arrivals": len(starts), "observed_ends": len(ends),
        "observed_measured_ends": len(set(cohort["measured_workflow_ids"]) & ends.keys()),
        "JCT_values_reported": False,
    }


def match_completed_report(result, report, cohort):
    if report["cohort"] != cohort or report.get("all_measured_completed") is not True:
        raise ValueError("reference lacks the same complete fixed cohort")
    if (
        result["bins"] != report["common_window_throughput_bins"]
        or result["output_tokens_per_second"] != report["common_window_output_tokens_per_second"]
        or result["decode_tokens_per_second"] != report["common_window_decode_tokens_per_second"]
    ):
        raise ValueError("closed-window extraction differs from the complete reference report")


def render(methods, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(exist_ok=False)
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
    for label, result in methods.items():
        bins = result["bins"]
        edges = [bins[0]["start_s"], *(row["end_s"] for row in bins)]
        axes[0].stairs(
            [row["decode_tokens_per_second"] for row in bins], edges, color=COLORS[label],
            label=f"{label} ({result['decode_tokens_per_second']:.2f})", baseline=None,
        )
        x, y = weighted_cdf([
            (row["decode_tokens_per_second"], row["end_s"] - row["start_s"]) for row in bins
        ])
        axes[1].step(x, y, where="post", color=COLORS[label], label=label)
    axes[0].set(xlabel="Seconds Since First Arrival", ylabel="Decode Output Token/s",
                title="Common-Window Decode Throughput")
    axes[1].set(xlabel="Decode Output Token/s", ylabel="Fraction of Common-Window Time",
                title="Time-Weighted Throughput Distribution")
    for axis in axes:
        axis.grid(alpha=.2)
        axis.legend(fontsize=8)
    start, end = next(iter(methods.values()))["window_s"]
    figure.suptitle(
        f"All workflows including background; fixed [{start:.2f}, {end:.2f}) seconds\n"
        "Complete measured JCT pending; one run per method, no confidence intervals",
        fontsize=10,
    )
    for extension in ("png", "pdf"):
        figure.savefig(output / ("decode_common_window." + extension), dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {0}
    ):
        raise ValueError("preserve window evidence and use CPU0, nice19, hidden CUDA")
    execution = causal.load(args.execution_plan)
    reg_path, cohort_path = Path(execution["registration"]), Path(execution["cohort"])
    registration, cohort = causal.load(reg_path), causal.load(cohort_path)
    state = causal.load(reg_path.with_suffix(".state.json"))
    if state["status"] != "running_full" or not all(
        causal.reservation.alive(state[name]) for name in ("watcher", "server", "driver")
    ):
        raise ValueError("collect this prefix only from the existing live execution")
    reference_names = {
        "full": "Nonranking Full", "learned": "Learned R5", "binned_truth": "Model-work Oracle",
    }
    sources = {label: (
        Path(execution["reference"][name]["registration"]),
        Path(execution["reference"][name]["review"]) / "cohort.json",
    ) for name, label in reference_names.items()}
    sources["Continuous-work Oracle"] = reg_path, None
    reader, methods, pins = SealedRecords(), {}, {}
    paths = [args.execution_plan, reg_path, cohort_path, Path(__file__)]
    for label, (source_reg, report_path) in sources.items():
        plan = causal.load(source_reg)
        causal.comparison.verify_sources(plan)
        matching_serving_traffic(plan, registration)
        arrivals_path = Path(plan["workflow_arrivals_path"])
        arm = Path(plan["run_root"]) / "full"
        result = extract_window(
            cohort, causal.load(arrivals_path),
            reader(arm / "replay/runtime_events.replay.jsonl"),
            reader(arm / "server/runtime_audit.jsonl"),
        )
        paths.extend((source_reg, arrivals_path))
        if report_path is not None:
            match_completed_report(result, causal.load(report_path), cohort)
            result["all_bins_and_rates_match_completed_report"] = True
            paths.append(report_path)
        methods[label] = result
    for path in paths:
        pins[str(path.resolve())] = sha256_file(path)
    if not all(causal.reservation.alive(state[name]) for name in ("watcher", "server", "driver")):
        raise ValueError("serving ownership changed while collecting the window prefix")
    current = methods["Continuous-work Oracle"]
    comparisons = {
        label: {
            "output_throughput_gain_percent": (
                current["output_tokens_per_second"] / result["output_tokens_per_second"] - 1.
            ) * 100.,
            "decode_throughput_gain_percent": (
                current["decode_tokens_per_second"] / result["decode_tokens_per_second"] - 1.
            ) * 100.,
        } for label, result in methods.items() if label != "Continuous-work Oracle"
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "window.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "methods": methods,
        "comparisons": comparisons, "input_jsonl_prefixes": reader.prefixes, "inputs_sha256": pins,
        "GPU_action": False, "JCT_values_reported": False, "candidate_final_idle_drain_verified": False,
        "whole_run_performance_or_repeatability_proven": False,
    })
    render(methods, args.output_dir / "figures")
    print(json.dumps({"comparisons": comparisons, "current": {
        name: current[name] for name in (
            "output_tokens_per_second", "decode_tokens_per_second",
            "last_published_native_completion_offset_s", "observed_measured_ends",
        )
    }}), flush=True)


if __name__ == "__main__":
    main()
