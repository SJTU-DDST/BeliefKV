#!/usr/bin/env python3
"""Plot complete matched causal runs using observed decode token deltas."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_causal_jct_paths import first_launches
from scripts.audit_decode_batch_efficiency import (
    OutputConservation, accumulate, decode_view, summarize,
)
from scripts.audit_outstanding_request_throughput import audit_arm
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


MATCH_FIELDS = (
    "source_plan", "token_source_sha256", "source_capacity",
    "source_server_parameters", "engine_tree", "sampling_mode",
    "stream_response_metrics", "workflow_ids", "request_timeout_s",
    "workflow_arrivals",
)
CONTEXT_BOUNDS = (32768, 65536, 131072)
CONTEXT_LABELS = ("<32K", "32K-64K", "64K-128K", ">=128K")
COLORS = ("#D55E00", "#0072B2", "#009E73")


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def make_bins(duration_s, bin_s):
    if (
        type(duration_s) not in (int, float) or not math.isfinite(duration_s)
        or duration_s <= 0 or type(bin_s) is not int or bin_s <= 0
    ):
        raise ValueError("positive finite duration and integer bin width required")
    return [
        {"start_s": index * bin_s, "end_s": min((index + 1) * bin_s, duration_s),
         "decode_tokens": 0, "decode_batches": 0, "worker_ms": 0.,
         "request_step_ms": 0.}
        for index in range(math.ceil(duration_s / bin_s))
    ]


def add_batch(bins, row, *, anchor_ms, bin_s):
    complete = row["complete_monotonic_ms"]
    elapsed = row["service_elapsed_ms"]
    if not all(
        type(value) in (int, float) and math.isfinite(value)
        for value in (complete, elapsed)
    ) or elapsed < 0:
        raise ValueError("invalid decode timing")
    seconds = (complete - anchor_ms) / 1000.
    if not 0 <= seconds <= bins[-1]["end_s"] + 1e-6:
        raise ValueError("decode completion outside measured workflow lifetime")
    tokens = sum(item["token_delta"] for item in row["request_samples"])
    if tokens > 0 and elapsed == 0:
        raise ValueError("positive output has no observed worker duration")
    index = min(int(seconds // bin_s), len(bins) - 1)
    bucket = bins[index]
    bucket["decode_tokens"] += tokens
    bucket["decode_batches"] += 1
    bucket["worker_ms"] += elapsed
    bucket["request_step_ms"] += row["batch_size"] * elapsed
    return tokens, elapsed


def finish_bins(bins, bin_s):
    return [
        {**row, "duration_s": row["end_s"] - row["start_s"],
         "partial_bin": row["end_s"] - row["start_s"] < bin_s,
         "decode_wall_tokens_per_s": row["decode_tokens"] / (row["end_s"] - row["start_s"]),
         "decode_worker_tokens_per_s": (
             row["decode_tokens"] * 1000. / row["worker_ms"] if row["worker_ms"] else None
         ),
         "worker_weighted_batch": (
             row["request_step_ms"] / row["worker_ms"] if row["worker_ms"] else None
         )}
        for row in bins
    ]


def weighted_cdf(pairs):
    weights = defaultdict(float)
    for value, weight in pairs:
        if (
            not all(type(item) in (int, float) and math.isfinite(item)
                    for item in (value, weight))
            or value < 0 or weight < 0
        ):
            raise ValueError("invalid weighted observation")
        if weight:
            weights[value] += weight
    total = sum(weights.values())
    if not total:
        return [], []
    xs, ys, cumulative = [], [], 0.
    for value, weight in sorted(weights.items()):
        cumulative += weight
        xs.append(value)
        ys.append(cumulative / total)
    return xs, ys


def match_contract(plan, work):
    common = plan["reference_environment"]
    return {
        **{name: plan.get(name) for name in MATCH_FIELDS},
        "tool_timing_overlay_sha256": (plan.get("tool_timing_overlay") or {}).get("sha256"),
        "seeds": {name: common.get(name) for name in (
            "SERVER_RANDOM_SEED", "SAMPLING_SEED", "PYTHONHASHSEED",
        )},
        "free_page_h2d_fence": common.get(
            "BELIEFKV_FREE_PAGE_H2D_FENCE", common.get("FREE_PAGE_H2D_FENCE", "0"),
        ),
        "exact_receipts_sha256": fingerprint(work),
    }


def require_matching(arms):
    contracts = [arm["match_contract"] for arm in arms.values()]
    if not contracts:
        raise ValueError("at least one complete arm is required")
    for contract in contracts[1:]:
        changed = sorted(name for name in contract if contract[name] != contracts[0][name])
        if changed:
            raise ValueError("unmatched comparison conditions: " + ", ".join(changed))


def require_complete_prefixes(prefixes):
    for name, prefix in prefixes.items():
        path = Path(name)
        if (
            path.stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed figure input changed or has a partial tail: " + name)


def extract(label, registration, arm, *, bin_s):
    plan = causal.load(registration)
    causal.comparison.verify_sources(plan)
    if (
        arm.resolve().parent != Path(plan["run_root"]).resolve()
        or arm.name not in plan["order"] or plan["quality_exclusions"]
    ):
        raise ValueError("arm differs from registered complete population")
    summary_path = arm / "replay/summary.json"
    result = causal.load(summary_path)
    work = causal.validate_result(plan, result, arm, live_control=True)
    metrics = causal.replay_event_metrics(result, arm, plan["workflow_ids"])
    native = audit_arm(arm)
    reader = SealedRecords()
    status_path = arm / "server/native_telemetry_status.json"
    status = causal.load(status_path)
    if status["writer_queue_depth"] != 0:
        raise ValueError("native telemetry has not drained")
    census_path = arm / "server/native_capacity_census.json"
    if causal.load(census_path)["capacity"] != plan["source_capacity"]:
        raise ValueError("actual KV capacity differs from the registered common capacity")
    state_path = arm / "opportunities/admission_opportunities.jsonl"
    causal.validate_free_page_fence(plan, causal.qualified.last_runtime_state(state_path))
    info_path = arm / "server/server_info.json"
    info = causal.load(info_path)
    expected = {**plan["source_server_parameters"], "enable_beliefkv_admission": True}
    if (
        any(info.get(name) != value for name, value in expected.items())
        or info.get("tp_size") != 1 or info.get("dp_size") != 1
        or info.get("speculative_algorithm") is not None
    ):
        raise ValueError("unsupported or changed observed serving configuration")
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    anchor = min(row["ts_ms"] for row in events if row["kind"] == "workflow_start")
    ends = sorted(row["ts_ms"] for row in events if row["kind"] == "workflow_end")
    duration = metrics["workflow_event_makespan_ms"] / 1000.
    bins = make_bins(duration, bin_s)
    requests = {
        row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")
    }
    totals, geometry, worker_rates = Counter(), defaultdict(Counter), []
    forwards, last_complete = set(), -math.inf
    domains, offsets = set(), []
    conservation = OutputConservation() if info["enable_mixed_chunk"] else None

    def samples():
        nonlocal last_complete
        for row in reader(arm / "server/runtime_audit.jsonl"):
            if row.get("event") != "gpu_service_sample":
                continue
            complete = row["complete_monotonic_ms"]
            if (
                row["forward_iter"] in forwards or row["phase"] not in ("prefill", "decode")
                or not math.isfinite(complete) or complete < last_complete
                or any(item["request_id"] not in requests for item in row["request_samples"])
            ):
                raise ValueError("duplicate, unordered or foreign native batch")
            forwards.add(row["forward_iter"])
            last_complete = complete
            domains.add(row["monotonic_clock_domain"])
            offsets.append(row["complete_ts_ms"] - complete)
            if conservation is not None:
                conservation.observe(row)
            decoded = decode_view(row, mixed_enabled=info["enable_mixed_chunk"])
            if decoded is not None:
                options = {"disable_padding": info["disable_cuda_graph_padding"]}
                accumulate(totals, decoded, info["cuda_graph_config"]["decode"]["bs"], **options)
                tokens, elapsed = add_batch(bins, decoded, anchor_ms=anchor, bin_s=bin_s)
                if elapsed:
                    worker_rates.append((tokens * 1000. / elapsed, elapsed))
                context = sum(item["sequence_tokens_before"] for item in decoded["request_samples"]) / decoded["batch_size"]
                key = decoded["batch_size"], bisect_right(CONTEXT_BOUNDS, context)
                accumulate(geometry[key], decoded, info["cuda_graph_config"]["decode"]["bs"], **options)
            yield row

    first_launches(
        samples(), requests,
        expected_records=status["background_writer_timing"]["categories"][
            "audit:gpu_service_sample"
        ]["records_seen"],
    )
    if len(domains) != 1 or max(offsets) - min(offsets) > 5.:
        raise ValueError("incompatible native observation clocks")
    if sum(row["decode_tokens"] for row in bins) != totals["output_tokens"]:
        raise ValueError("binned decode output did not reconcile")
    bins = finish_bins(bins, bin_s)
    for row in bins:
        row["remaining_workflows_at_bin_start"] = len(ends) - bisect_right(
            ends, anchor + row["start_s"] * 1000.,
        )
    require_complete_prefixes(reader.prefixes)
    return {
        "label": label, "registration": str(registration.resolve()),
        "arm": str(arm.resolve()), "code_commit": plan["code_commit"],
        "match_contract": match_contract(plan, work),
        "mean_jct_s": metrics["mean_jct_ms"] / 1000.,
        "makespan_s": duration, "complete_output_tokens": plan["source_plan"]["output_tokens"],
        "end_to_end_output_tokens_per_s": plan["source_plan"]["output_tokens"] / duration,
        "decode": summarize(totals), "bins": bins,
        "workflow_completions_s": [(when - anchor) / 1000. for when in ends],
        "worker_rate_cdf": dict(zip(("x", "y"), weighted_cdf(worker_rates))),
        "wall_rate_cdf": dict(zip(("x", "y"), weighted_cdf(
            (row["decode_wall_tokens_per_s"], row["duration_s"]) for row in bins
        ))),
        "geometry": [
            {"batch": size, "context_band": band, "context_label": CONTEXT_LABELS[band],
             **summarize(values)}
            for (size, band), values in sorted(geometry.items())
        ],
        "input_jsonl_prefixes": reader.prefixes,
        "evidence_sha256": {str(path): sha256_file(path) for path in (
            registration, summary_path, info_path, status_path, census_path, state_path,
        )},
        "native_request_work": native,
        "all_mode_output_accounting": conservation.verify(requests) if conservation else None,
    }


def write_csv(path, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render(arms, output, *, title, bin_s):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    from matplotlib.ticker import MaxNLocator
    import numpy as np

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": .18,
        "pdf.fonttype": 42, "svg.fonttype": "none",
    })
    figures = []

    def save(figure, name):
        for suffix in ("png", "pdf"):
            figure.savefig(output / f"{name}.{suffix}", dpi=180, bbox_inches="tight")
        plt.close(figure)
        figures.append(name)

    figure, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    figure.suptitle(title, fontsize=14)
    for index, (label, arm) in enumerate(arms.items()):
        color = COLORS[index % len(COLORS)]
        bins = arm["bins"]
        edges = [row["start_s"] / 60. for row in bins] + [bins[-1]["end_s"] / 60.]
        for axis, field in (
            (axes[0, 0], "decode_wall_tokens_per_s"),
            (axes[0, 1], "decode_worker_tokens_per_s"),
            (axes[1, 0], "worker_weighted_batch"),
        ):
            values = [row[field] if row[field] is not None else np.nan for row in bins]
            axis.stairs(values, edges, baseline=None, label=label, color=color, linewidth=1.6)
            if bins[-1]["partial_bin"] and values[-1] == values[-1]:
                axis.plot(
                    (edges[-2] + edges[-1]) / 2., values[-1],
                    marker="o", markerfacecolor="white", color=color, markersize=5,
                )
        completed = arm["workflow_completions_s"]
        axes[1, 1].step(
            [0.] + [value / 60. for value in completed],
            list(range(len(completed) + 1)), where="post", color=color, label=label,
        )
    settings = (
        ("(a) Decode output over wall time", "Decode tokens / wall second"),
        ("(b) Decode worker output rate", "Decode tokens / worker second"),
        ("(c) Decode batch occupancy", "Worker-time-weighted batch size"),
        ("(d) Complete workflow progress", "Completed workflows"),
    )
    for axis, (heading, ylabel) in zip(axes.flat, settings):
        axis.set(title=heading, xlabel="Minutes from workflow arrival", ylabel=ylabel)
        axis.set_xlim(left=0)
        axis.set_ylim(bottom=0)
        axis.legend(frameon=False, fontsize=9)
    axes[1, 1].yaxis.set_major_locator(MaxNLocator(integer=True, nbins=7))
    save(figure, "decode_timeline")

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), layout="constrained")
    figure.suptitle(title, fontsize=13)
    for index, (label, arm) in enumerate(arms.items()):
        for axis, field in zip(axes, ("wall_rate_cdf", "worker_rate_cdf")):
            curve = arm[field]
            axis.step(
                [0.] + curve["x"], [0.] + curve["y"], where="post",
                label=label, color=COLORS[index % len(COLORS)], linewidth=1.6,
            )
    axes[0].set(
        title=f"(a) {bin_s}s wall-rate distribution",
        xlabel="Decode tokens / wall second", ylabel="Fraction of observed wall time",
    )
    axes[1].set(
        title="(b) Per-batch worker-rate distribution",
        xlabel="Decode tokens / worker second (symlog)", ylabel="Fraction of decode worker time",
    )
    axes[1].set_xscale("symlog", linthresh=20.)
    for axis in axes:
        axis.set(xlim=(0, None), ylim=(0, 1))
        axis.legend(frameon=False, fontsize=9)
    save(figure, "decode_distributions")

    if len(arms) == 2:
        labels = list(arms)
        tables = [
            {(row["batch"], row["context_band"]): row for row in arms[label]["geometry"]}
            for label in labels
        ]
        maximum = max(size for table in tables for size, _ in table)
        values = np.full((4, maximum), np.nan)
        for key in tables[0].keys() & tables[1].keys():
            left, right = (table[key] for table in tables)
            if min(left["batches"], right["batches"]) >= 20:
                denominator = right["output_tokens_per_worker_interval_second"]
                if denominator:
                    values[key[1], key[0] - 1] = left["output_tokens_per_worker_interval_second"] / denominator
        figure, axis = plt.subplots(figsize=(13, 4), layout="constrained")
        finite = values[np.isfinite(values)]
        spread = max(.1, max((abs(value - 1.) for value in finite), default=.1))
        image = axis.imshow(
            values, origin="lower", aspect="auto", cmap="RdBu",
            norm=TwoSlopeNorm(vmin=max(0., 1. - spread), vcenter=1., vmax=1. + spread),
        )
        axis.set(
            title=f"Shared geometry: {labels[0]} / {labels[1]} worker output rate",
            xlabel="Exact decode batch size", ylabel="Mean request context per batch",
            xticks=range(maximum), xticklabels=range(1, maximum + 1),
            yticks=range(4), yticklabels=CONTEXT_LABELS,
        )
        axis.grid(False)
        for band, column in zip(*np.where(np.isfinite(values))):
            value = values[band, column]
            axis.text(
                column, band, f"{value:.2f}", ha="center", va="center",
                fontsize=7, color="white" if abs(value - 1.) > .7 * spread else "black",
            )
        figure.colorbar(image, ax=axis, label="Rate ratio; minimum 20 batches per arm")
        save(figure, "decode_geometry")
    return figures


def named_paths(values):
    result = {}
    for value in values:
        label, separator, path = value.partition("=")
        if not separator or not label or not path or label in result:
            raise ValueError("paths require unique LABEL=PATH entries")
        result[label] = Path(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="LABEL=ARM_PATH")
    parser.add_argument("--registration", action="append", required=True, help="LABEL=JSON_PATH")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bin-seconds", type=int, default=60)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    if args.bin_seconds <= 0:
        parser.error("--bin-seconds must be positive")
    arms, registrations = named_paths(args.arm), named_paths(args.registration)
    if arms.keys() != registrations.keys() or not 1 <= len(arms) <= len(COLORS):
        parser.error("one to three identically named arm/registration pairs are required")
    if args.output_dir.exists():
        parser.error("preserve existing figures; select a new output directory")
    extracted = {
        label: extract(label, registrations[label], arm, bin_s=args.bin_seconds)
        for label, arm in arms.items()
    }
    require_matching(extracted)
    args.output_dir.mkdir(parents=True)
    figures = render(extracted, args.output_dir, title=args.title, bin_s=args.bin_seconds)
    write_csv(args.output_dir / "time_bins.csv", [
        {"arm": label, **row} for label, arm in extracted.items() for row in arm["bins"]
    ])
    write_csv(args.output_dir / "geometry.csv", [
        {"arm": label, **row} for label, arm in extracted.items() for row in arm["geometry"]
    ])
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "matched_complete_runs" if len(extracted) > 1 else "single_complete_run",
        "bin_seconds": args.bin_seconds, "arms": extracted, "figures": figures,
        "analysis_source_sha256": {name: sha256_file(ROOT / name) for name in (
            "scripts/plot_causal_decode_diagnostics.py",
            "scripts/audit_causal_jct_paths.py", "scripts/audit_decode_batch_efficiency.py",
            "scripts/audit_outstanding_request_throughput.py", "scripts/run_causal_comparison.py",
        )},
        "figure_sha256": {
            path.name: sha256_file(path) for path in args.output_dir.iterdir()
            if path.suffix in (".png", ".pdf", ".csv")
        },
        "limitations": [
            "All observed wall time and normal tails remain. No warmup or tail selection.",
            "Bins align to workflow arrival, not normalized progress. No smoothing or post-end zero padding.",
            "Token deltas and worker intervals are assigned to the batch completion bin. Partial last bins use their actual duration and are marked by an open circle.",
            "An empty wall bin has rate zero; a bin without decode has undefined worker rate.",
            "CDFs are weighted by observed time, not by request count or equally weighted partial bins.",
            "The worker-rate CDF uses a symlog x-axis with a 20 token/s linear region, retaining zeros and the full positive range.",
            "Worker intervals include runtime and waits; they are not pure CUDA kernel time.",
            "Shared geometry is descriptive: exact batch size and broad context bands do not prove causal equality. Missing/insufficient cells are not speedups.",
            "Each method has one run here. No confidence interval, repeated-run or all-baseline acceptance.",
        ],
        "performance_accepted": False,
    }
    with (args.output_dir / "evidence.json").open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        label: {"decode": arm["decode"], "makespan_s": arm["makespan_s"],
                "mean_jct_s": arm["mean_jct_s"]}
        for label, arm in extracted.items()
    }, indent=2))


if __name__ == "__main__":
    main()
