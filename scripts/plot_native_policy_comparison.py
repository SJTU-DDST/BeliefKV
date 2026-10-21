#!/usr/bin/env python3
"""Render descriptive publication figures for a completed historical comparison."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import html
import io
import json
import math
from pathlib import Path
import platform
import shlex
import subprocess
import sys
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np


ARMS = ("native", "predictive_h2d")
LABELS = {"native": "Native (V103)", "predictive_h2d": "Predictive (V108)"}
COLORS = {"native": "#0072B2", "predictive_h2d": "#D55E00"}
STYLES = {"native": "--", "predictive_h2d": "-"}
CAPTIONS = {
    "jct_distribution": (
        "Workflow completion latency. (a) Empirical CDF of complete client-summary "
        "JCT; every one of the 36 workflows in each run is included, without "
        "eligibility filtering or clipping at the busy-window boundary. "
        "(b) The same tasks paired by identity; the diagonal denotes equal JCT, "
        "with identical axis limits and aspect ratio. Each point is one task pair. "
        "There is one experimental run per policy, so no run-level confidence "
        "interval or statistical significance is asserted."
    ),
    "workflow_progress": (
        "Observed workflow progress, aligned to each run's first GPU-monitor "
        "sample using the validated wall-clock mapping. (a) Cumulative actual "
        "workflow-end events; these timestamps are not reconstructed from JCT. "
        "(b) Completion throughput in complete, non-overlapping 300-second bins. "
        "Incomplete final bins are exported but omitted from the rate plot. "
        "All 36 completions remain in panel (a) and the JCT distribution. "
        "The gray regions mark warmup and the common late-period exclusion, respectively."
    ),
    "system_timeseries": (
        "System time series in complete, non-overlapping 60-second bins, with "
        "no smoothing. Decode throughput assigns each observed request token "
        "delta to its completed GPU-service batch timestamp; it is distinct "
        "from assigning an entire response to its completion bin. GPU utilization "
        "and running/waiting request counts are sample means from the original "
        "monitors. Each trace ends with its own observation period; absent "
        "post-termination observations are not plotted as zeros. The common "
        "busy window is 600 to 3494.819 seconds; all workloads contribute."
        " Exact-window totals are checked separately against raw timestamps; "
        "display bins are not trimmed at the vertical boundary."
    ),
    "request_latency": (
        "Server-observed request latency from native llm_submit to llm_result, "
        "including server-side queueing and model service, but excluding earlier "
        "client prompt construction and transport. (a) Exact empirical CDF on a "
        "logarithmic latency axis; the complete positive latency range is shown. "
        "(b) Median and P95 of requests completed in complete 300-second bins. "
        "Server lifecycle-pair counts are reported separately from logical "
        "client-call counts; identical request populations are not assumed. "
        "These correlated requests are not independent experimental repetitions. No confidence "
        "bands are drawn."
    ),
    "paired_workload": (
        "Realized work and completion latency. (a) Logical client model-call counts "
        "for the same 36 tasks, on equal axes. (b) Per-task relative changes in "
        "model calls and JCT, both relative to that task's native value. All tasks "
        "are shown; there is no fitted causal or regression model. Request counts "
        "do not capture context-dependent service cost and are not used to "
        "normalize JCT. All task identities and point coordinates are provided "
        "in the exported T01-T36 workflow table."
    ),
}


def read_json(path: Path, sources: dict) -> dict:
    raw = path.read_bytes()
    stat = path.stat()
    sources[str(path.resolve())] = {
        "bytes": len(raw), "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return json.loads(raw)


def records(path: Path, sources: dict, needle: bytes | None = None):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            if not line.endswith(b"\n"):
                raise ValueError(f"Incomplete input line in completed run: {path}")
            if line.strip() and (needle is None or needle in line):
                yield json.loads(line)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Input changed while reading: {path}")
    sources[str(path.resolve())] = {
        "bytes": after.st_size, "mtime_ns": after.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def average(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def quantile(values: list[float], q: float) -> float | None:
    return float(np.quantile(values, q)) if values else None


def extract_arm(name: str, comparison: dict, sources: dict, bin_s: int) -> dict:
    run = comparison["runs"][name]
    root = Path(run["run"])
    summary_paths = list(root.glob("client_*/summary.json"))
    if len(summary_paths) != 1:
        raise ValueError(f"Expected one completed client summary: {root}")
    summary = read_json(summary_paths[0], sources)
    if summary["completed_workflows"] != summary["workflow_count"]:
        raise ValueError("This completed-pair figure requires all workflows terminal/completed")
    if summary["llm_request_count"] != run["llm_requests"]:
        raise ValueError("Logical request counts disagree with the comparison")
    info = read_json(root / "server/server_info.json", sources)
    plan = read_json(root.parent / "ab_plan.json", sources)
    clock = run["clock_alignment"]
    if clock.get("schema_version") != 2 or not clock.get("native_client_clock_validated"):
        raise ValueError("A validated explicit GPU/client clock mapping is required")
    anchor = clock["anchor_unix_ms"]
    zone = ZoneInfo(clock["gpu_timezone"])
    horizon = run["timeline_duration_seconds"]
    count = math.ceil(horizon / bin_s)
    bins = [{
        "start_s": i * bin_s, "end_s": min((i + 1) * bin_s, horizon),
        "decode_tokens": 0, "gpu": [], "running": [], "waiting": [],
        "request_latencies_s": [],
    } for i in range(count)]

    def bucket(t: float):
        index = math.floor(t / bin_s)
        return bins[index] if 0 <= t < horizon and 0 <= index < count else None

    window = comparison["tail_excluded_primary_comparison"]["policy"]
    begin, end = window["warmup_seconds"], window["common_cutoff_seconds"]
    exact_tokens = 0
    clock_offset = None
    previous_complete = -math.inf
    sample_count = 0
    print(f"Reading service records: {name}", flush=True)
    for row in records(root / "server/runtime_audit.jsonl", sources, b"gpu_service_sample"):
        if row.get("event") != "gpu_service_sample":
            continue
        complete = row["complete_ts_ms"]
        if complete < previous_complete:
            raise ValueError("GPU service completion records are not ordered")
        previous_complete = complete
        if clock_offset is None:
            clock_offset = complete - row["complete_monotonic_ms"]
        if row["phase"] != "decode":
            continue
        delta = sum(sample["token_delta"] for sample in row["request_samples"])
        if delta < 0:
            raise ValueError("Negative decode token delta")
        t = (complete - anchor) / 1000
        item = bucket(t)
        if item is not None:
            item["decode_tokens"] += delta
        if begin <= t < end:
            exact_tokens += delta
        sample_count += 1
    if clock_offset is None:
        raise ValueError("Missing service clock")
    expected = comparison["tail_excluded_primary_comparison"]["runs"][name][
        "sustained_load"]["windows"][0]["decode_output_tokens"]
    if exact_tokens != expected:
        raise ValueError(f"Decode cross-check failed: {exact_tokens} != {expected}")

    client = summary_paths[0].parent
    gpu_path = client / "gpu_samples.csv"
    raw = gpu_path.read_bytes()
    sources[str(gpu_path.resolve())] = {
        "bytes": len(raw), "mtime_ns": gpu_path.stat().st_mtime_ns,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    for row in csv.DictReader(io.StringIO(raw.decode())):
        timestamp = datetime.strptime(
            row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f"
        ).replace(tzinfo=zone).timestamp() * 1000
        item = bucket((timestamp - anchor) / 1000)
        if item is not None:
            item["gpu"].append(float(row["gpu_utilization_percent"]))
    for row in records(client / "sglang_metrics.jsonl", sources):
        if row.get("error") or row.get("monotonic_ts_ms") is None:
            continue
        item = bucket((row["monotonic_ts_ms"] + clock_offset - anchor) / 1000)
        if item is not None:
            item["running"].append(row["num_running_reqs"])
            item["waiting"].append(row["num_queue_reqs"])

    pending = {}
    requests = []
    unmatched_results = duplicate_submits = 0
    for row in records(root / "server/runtime_events.sglang.jsonl", sources):
        attrs = row.get("attributes") or {}
        rid = attrs.get("request_id")
        if row["kind"] == "llm_submit":
            duplicate_submits += rid in pending
            pending[rid] = row
        elif row["kind"] == "llm_result":
            submitted = pending.pop(rid, None)
            if submitted is None:
                unmatched_results += 1
                continue
            latency = (row["ts_ms"] - submitted["ts_ms"]) / 1000
            if not math.isfinite(latency) or latency <= 0:
                raise ValueError("Request latency must be finite and positive")
            t = (row["ts_ms"] - anchor) / 1000
            item = bucket(t)
            if item is not None:
                item["request_latencies_s"].append(latency)
            requests.append({
                "arm": name, "request_id": rid, "workflow_id": row.get("workflow_id"),
                "submit_s": (submitted["ts_ms"] - anchor) / 1000,
                "complete_s": t, "latency_s": latency,
                "prompt_tokens": submitted["attributes"].get("prompt_tokens"),
                "uncached_prompt_tokens": submitted["attributes"].get("uncached_prompt_tokens"),
                "output_tokens": attrs.get("output_tokens"),
            })
    latencies = [row["latency_s"] for row in requests]
    for q, key in ((.5, "llm_result_duration_p50_ms"), (.95, "llm_result_duration_p95_ms")):
        if not math.isclose(quantile(latencies, q) * 1000, run[key], abs_tol=1e-5):
            raise ValueError(f"Server latency cross-check failed: {name}/{key}")

    workflows = comparison["tail_excluded_primary_comparison"]["runs"][name][
        "restricted_jct"]["rows"]
    by_id = {row["instance_id"]: row for row in summary["workflows"]}
    if {row["instance_id"] for row in workflows} != set(by_id):
        raise ValueError("Workflow timing population mismatch")
    workflows = [{
        "arm": name, "instance_id": row["instance_id"],
        "start_s": row["start_seconds"], "complete_s": row["end_seconds"],
        "jct_s": by_id[row["instance_id"]]["duration_seconds"],
        "outcome": by_id[row["instance_id"]]["outcome"],
    } for row in workflows]
    series = [{
        "arm": name, "start_s": row["start_s"], "end_s": row["end_s"],
        "complete_bin": math.isclose(row["end_s"] - row["start_s"], bin_s),
        "decode_tokens": row["decode_tokens"],
        "decode_tokens_per_s": row["decode_tokens"] / (row["end_s"] - row["start_s"]),
        "gpu_utilization_sample_mean": average(row["gpu"]),
        "running_requests_sample_mean": average(row["running"]),
        "waiting_requests_sample_mean": average(row["waiting"]),
        "gpu_samples": len(row["gpu"]), "queue_samples": len(row["waiting"]),
        "completed_server_requests": len(row["request_latencies_s"]),
        "server_latency_p50_s": quantile(row["request_latencies_s"], .5),
        "server_latency_p95_s": quantile(row["request_latencies_s"], .95),
    } for row in bins]
    return {
        "run": run, "series": series, "requests": requests, "workflows": workflows,
        "horizon_s": horizon, "clock_offset_ms": clock_offset,
        "audit": {
            "completed_decode_batches": sample_count,
            "common_window_decode_tokens": exact_tokens,
            "common_window_decode_tokens_crosscheck": "passed",
            "server_latency_quantiles_crosscheck": "passed",
            "server_request_pairs": len(requests),
            "unmatched_server_results": unmatched_results,
            "unmatched_server_submits": len(pending),
            "duplicate_pending_submits": duplicate_submits,
            "server_random_seed": info.get("random_seed"),
            "request_sampling_seed": plan.get("sampling_seed"),
            "max_running_requests": info.get("max_running_requests"),
            "code_commit": plan.get("code_commit"),
            "sglang_patch_sha256": plan.get("sglang_patch_sha256"),
        },
    }


def save_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No data for {path.name}")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9,
        "axes.titlesize": 9, "axes.labelsize": 9, "legend.fontsize": 8,
        "xtick.labelsize": 8, "ytick.labelsize": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": .7, "axes.axisbelow": True, "lines.linewidth": 1.4,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "svg.hashsalt": "beliefkv-policy-comparison-v1",
        "savefig.facecolor": "white",
    })


def ecdf(ax, values, arm, *, scale=1., label=None):
    x = np.sort(np.asarray(values) / scale)
    y = np.arange(1, len(x) + 1) / len(x)
    ax.step(
        np.r_[x[0], x], np.r_[0., y], where="post",
        color=COLORS[arm], linestyle=STYLES[arm], label=label or LABELS[arm],
    )
    ax.set_ylim(0, 1.02)
    ax.grid(axis="y", color=".90", linewidth=.5)


def shade_time(ax, data):
    cutoff = data["window"]["common_cutoff_seconds"] / 60
    warmup = data["window"]["warmup_seconds"] / 60
    limit = math.ceil(max(data["arms"][a]["horizon_s"] for a in ARMS) / 300) * 5
    ax.axvspan(0, warmup, color=".95", zorder=-2)
    ax.axvspan(cutoff, limit, color=".95", zorder=-2)
    ax.axvline(cutoff, color=".45", linestyle=":", linewidth=.8)
    ax.set_xlim(0, limit)
    ax.set_xlabel("Elapsed time (min)")
    ax.grid(axis="y", color=".90", linewidth=.5)


def stairs(ax, rows, field, arm):
    selected = [row for row in rows if row["complete_bin"]]
    edges = [row["start_s"] / 60 for row in selected] + [selected[-1]["end_s"] / 60]
    values = [row[field] if row[field] is not None else np.nan for row in selected]
    ax.stairs(values, edges, baseline=None, color=COLORS[arm],
              linestyle=STYLES[arm], label=LABELS[arm])


def throughput_bins(data, seconds: int) -> list[dict]:
    rows = []
    for arm in ARMS:
        current = data["arms"][arm]
        for begin in range(0, math.ceil(current["horizon_s"]), seconds):
            end = min(begin + seconds, current["horizon_s"])
            completed = sum(begin <= row["complete_s"] < end for row in current["workflows"])
            latencies = [
                row["latency_s"] for row in current["requests"]
                if begin <= row["complete_s"] < end
            ]
            rows.append({
                "arm": arm, "start_s": begin, "end_s": end,
                "complete_bin": math.isclose(end - begin, seconds),
                "workflow_completions": completed,
                "workflow_completions_per_min": completed / ((end - begin) / 60),
                "server_requests": len(latencies),
                "server_latency_p50_s": quantile(latencies, .5),
                "server_latency_p95_s": quantile(latencies, .95),
            })
    for arm in ARMS:
        if sum(row["workflow_completions"] for row in rows if row["arm"] == arm) != len(
            data["arms"][arm]["workflows"]
        ):
            raise ValueError("Completion-rate bins lost a workflow")
    return rows


def render_figures(data: dict, output: Path, dpi: int) -> list[dict]:
    configure_style()
    figures = []

    def save(fig, name):
        for extension in ("pdf", "svg", "png"):
            metadata = {"Creator": "BeliefKV comparison plotting"}
            if extension == "pdf":
                metadata.update({"CreationDate": None, "ModDate": None})
            elif extension == "svg":
                metadata["Date"] = None
            fig.savefig(output / f"{name}.{extension}", dpi=dpi, metadata=metadata)
        plt.close(fig)
        figures.append({"name": name, "caption": CAPTIONS[name]})

    pairs = data["pairs"]
    native_jct = np.array([row["native_jct_s"] for row in pairs]) / 60
    pred_jct = np.array([row["predictive_jct_s"] for row in pairs]) / 60
    fig, axes = plt.subplots(1, 2, figsize=(7, 2.8), layout="constrained")
    for arm in ARMS:
        values = [row["jct_s"] for row in data["arms"][arm]["workflows"]]
        ecdf(axes[0], values, arm, scale=60, label=f"{LABELS[arm]} (n={len(values)})")
    axes[0].set(xlabel="Workflow JCT (min)", ylabel="Empirical cumulative fraction",
                title="(a) Complete JCT distribution", xlim=(0, 75))
    axes[0].legend(loc="lower right")
    upper = math.ceil(max(native_jct.max(), pred_jct.max()) / 5) * 5
    axes[1].plot([0, upper], [0, upper], color=".45", linestyle=":", linewidth=1)
    axes[1].scatter(native_jct, pred_jct, s=21, color="#444444", alpha=.8)
    axes[1].set(xlabel="Native JCT (min)", ylabel="Predictive JCT (min)",
                title="(b) Same-task pairs (n=36)", xlim=(0, upper), ylim=(0, upper))
    axes[1].set_aspect("equal", adjustable="box")
    save(fig, "jct_distribution")

    coarse = data["coarse"]
    fig, axes = plt.subplots(1, 2, figsize=(7, 2.8), layout="constrained")
    for arm in ARMS:
        ends = sorted(row["complete_s"] / 60 for row in data["arms"][arm]["workflows"])
        limit = max(data["arms"][a]["horizon_s"] for a in ARMS) / 60
        axes[0].step([0, *ends, limit], [0, *range(1, len(ends) + 1), len(ends)],
                     where="post", color=COLORS[arm], linestyle=STYLES[arm],
                     label=LABELS[arm])
        stairs(axes[1], [row for row in coarse if row["arm"] == arm],
               "workflow_completions_per_min", arm)
    for ax in axes:
        shade_time(ax, data)
        ax.set_ylim(bottom=0)
    axes[0].set(title="(a) Cumulative completions", ylabel="Completed workflows", ylim=(0, 37))
    axes[0].yaxis.set_major_locator(MaxNLocator(integer=True))
    axes[1].set(title="(b) Completion throughput", ylabel="Completed workflows / min")
    axes[0].legend()
    save(fig, "workflow_progress")

    fig, axes = plt.subplots(2, 2, figsize=(7, 4.8), layout="constrained")
    fields = (
        ("decode_tokens_per_s", "(a) Decode throughput", "Generated decode tokens / s"),
        ("waiting_requests_sample_mean", "(b) Waiting requests", "Mean waiting requests"),
        ("gpu_utilization_sample_mean", "(c) GPU utilization", "GPU utilization (%)"),
        ("running_requests_sample_mean", "(d) Running requests", "Mean running requests"),
    )
    for ax, (field, title, ylabel) in zip(axes.flat, fields):
        for arm in ARMS:
            stairs(ax, data["arms"][arm]["series"], field, arm)
        shade_time(ax, data)
        ax.set(title=title, ylabel=ylabel, ylim=(0, None))
    axes[1, 0].set_ylim(0, 102)
    axes[1, 1].set_ylim(0, 8.3)
    axes[0, 0].legend(loc="lower left")
    save(fig, "system_timeseries")

    fig, axes = plt.subplots(1, 2, figsize=(7, 2.8), layout="constrained")
    for arm in ARMS:
        latencies = [row["latency_s"] for row in data["arms"][arm]["requests"]]
        ecdf(axes[0], latencies, arm, label=f"{LABELS[arm]} (n={len(latencies):,})")
        rows = [row for row in coarse if row["arm"] == arm]
        for field, alpha in (("server_latency_p50_s", 1.), ("server_latency_p95_s", .55)):
            selected = [row for row in rows if row["complete_bin"]]
            x = [(row["start_s"] + row["end_s"]) / 120 for row in selected]
            y = [row[field] if row[field] is not None else np.nan for row in selected]
            axes[1].plot(x, y, color=COLORS[arm], linestyle=STYLES[arm],
                         marker="o" if "p50" in field else "^", markersize=3,
                         alpha=alpha, label=f"{LABELS[arm]} {'P50' if 'p50' in field else 'P95'}")
    axes[0].set_xscale("log")
    axes[0].set(title="(a) Server request latency", xlabel="Request E2E latency (s, log scale)",
                ylabel="Empirical cumulative fraction")
    axes[0].legend(loc="lower right", fontsize=7)
    shade_time(axes[1], data)
    axes[1].set(title="(b) Completed-request latency", ylabel="Request E2E latency (s)",
                ylim=(0, None))
    axes[1].legend(fontsize=6.5, loc="upper right")
    save(fig, "request_latency")

    fig, axes = plt.subplots(1, 2, figsize=(7, 3.1), layout="constrained")
    x = np.array([row["native_model_calls"] for row in pairs])
    y = np.array([row["predictive_model_calls"] for row in pairs])
    upper = math.ceil(max(x.max(), y.max()) / 100) * 100
    axes[0].plot([0, upper], [0, upper], color=".45", linestyle=":", linewidth=1)
    axes[0].scatter(x, y, color="#444444", s=17, alpha=.8)
    axes[0].set(title="(a) Logical model-call counts", xlabel="Native model calls",
                ylabel="Predictive model calls", xlim=(0, upper), ylim=(0, upper))
    axes[0].set_aspect("equal", adjustable="box")
    for row in pairs:
        dx, dy = row["model_calls_change_pct"], row["jct_change_pct"]
        axes[1].scatter(dx, dy, s=12, color="#444444")
    axes[1].axhline(0, color=".45", linestyle=":", linewidth=.8)
    axes[1].axvline(0, color=".45", linestyle=":", linewidth=.8)
    axes[1].set(title="(b) Realized work versus JCT", xlabel="Model-call change (%)",
                ylabel="JCT change (%)")
    axes[1].margins(.14)
    save(fig, "paired_workload")
    return figures


def write_report(data: dict, figures: list[dict], output: Path, command: str) -> None:
    save_csv(output / "workflow_pairs.csv", data["pairs"])
    save_csv(output / "workflow_events.csv", [
        row for arm in ARMS for row in data["arms"][arm]["workflows"]
    ])
    save_csv(output / "server_request_latencies.csv", [
        row for arm in ARMS for row in data["arms"][arm]["requests"]
    ])
    save_csv(output / "timeseries_60s.csv", [
        row for arm in ARMS for row in data["arms"][arm]["series"]
    ])
    save_csv(output / "workflow_and_request_bins_300s.csv", data["coarse"])
    metrics = []
    for key in ("jct_mean_seconds", "jct_p50_seconds", "jct_p95_seconds",
                "llm_requests", "tool_calls", "output_tokens", "input_tokens"):
        native = data["arms"]["native"]["run"][key]
        pred = data["arms"]["predictive_h2d"]["run"][key]
        metrics.append({
            "metric": key, "native": native, "predictive": pred,
            "predictive_change_pct": 100 * (pred / native - 1),
        })
    for label, field in (
        ("common_window_decode_tokens_per_s", "decode_output_tokens_per_second"),
        ("common_window_gpu_utilization_sample_mean_pct", "gpu_utilization_mean"),
        ("common_window_decode_time_weighted_batch_size", "decode_time_weighted_batch_size"),
    ):
        native = data["common_metrics"]["native"][field]
        pred = data["common_metrics"]["predictive_h2d"][field]
        metrics.append({
            "metric": label, "native": native, "predictive": pred,
            "predictive_change_pct": 100 * (pred / native - 1),
        })
    for q, label in ((.5, "server_request_latency_p50_s"), (.95, "server_request_latency_p95_s")):
        native = quantile([r["latency_s"] for r in data["arms"]["native"]["requests"]], q)
        pred = quantile([r["latency_s"] for r in data["arms"]["predictive_h2d"]["requests"]], q)
        metrics.append({
            "metric": label, "native": native, "predictive": pred,
            "predictive_change_pct": 100 * (pred / native - 1),
        })
    save_csv(output / "summary_metrics.csv", metrics)
    audits = {arm: data["arms"][arm]["audit"] for arm in ARMS}
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[1],
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        revision = None
    extracted = output / "plot_data.json.gz"
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": command, "python": platform.python_version(),
        "matplotlib": matplotlib.__version__, "numpy": np.__version__,
        "git_revision": revision,
        "plot_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "extraction_script_sha256": data["extraction_script_sha256"],
        "input_sources": data["sources"], "validation": audits,
        "render_origin": data.get("render_origin", {"mode": "raw_extraction"}),
        "extracted_data": {
            "filename": extracted.name,
            "sha256": hashlib.sha256(extracted.read_bytes()).hexdigest(),
        },
        "comparison_scope": "One completed predictive run versus one historical native run",
        "statistical_unit": "Run; no run-level confidence interval from one run per policy",
        "workflow_population": "All 36 tasks in each run; no eligibility filtering",
        "common_window_policy": data["window"],
        "decode_bin_seconds": data["bin_seconds"], "workflow_bin_seconds": 300,
        "quantiles": "Linear interpolation at (n-1)*q, equivalent to numpy method=linear",
        "scope_of_context_diagnostic": data.get("geometry_diagnostic"),
        "captions": CAPTIONS,
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    notes = (
        "These are descriptive figures from one completed run per policy, not a "
        "load-sweep throughput-latency curve or a trajectory-matched causal speedup. "
        "No task is removed because of eligibility flags. No smoothing, KDE, "
        "run-level confidence intervals, or request-count normalization of JCT is used. "
        "Native/predictive server random seeds are "
        f"{audits['native']['server_random_seed']}/"
        f"{audits['predictive_h2d']['server_random_seed']}; code and engine patches "
        "also differ. Equal task inputs do not imply equal realized work."
    )
    geometry = data.get("geometry_diagnostic")
    if geometry:
        notes += (
            f" A separate {geometry['start_seconds']:g}-"
            f"{geometry['end_seconds']:g} s geometry audit reports mean visible "
            f"context {geometry['native_context_tokens_mean']:.0f}/"
            f"{geometry['predictive_context_tokens_mean']:.0f} tokens "
            "(native/predictive); this is not a full-run context measurement."
        )
    methods = (
        "PDF and SVG outputs are vector figures; PDF uses embedded TrueType fonts. "
        "PNG exports use the requested resolution. Color and line pattern both "
        "identify the policies. Shaded temporal regions are [0,600) s warmup "
        "and the interval after the common cutoff, selected when either run "
        "first has fewer than four active workflows. JCT uses full client "
        "summary durations; workflow progress uses actual event timestamps. "
        "Sample means are not scheduler-time-weighted batch sizes. Server-request "
        "E2E latency is neither TTFT nor isolated H2D stall."
    )
    sections = []
    tex = []
    for figure in figures:
        name, caption = figure["name"], figure["caption"]
        sections.append(
            f'<section><h2>{html.escape(name.replace("_", " ").title())}</h2>'
            f'<a href="{name}.pdf">PDF</a> | <a href="{name}.svg">SVG</a>'
            f' | <a href="{name}.png">PNG</a>'
            f'<img src="{name}.png" alt="{html.escape(caption)}">'
            f'<p>{html.escape(caption)}</p></section>'
        )
        tex.append(
            "\\begin{figure*}[t]\n\\centering\n"
            f"\\includegraphics[width=\\textwidth]{{{name}.pdf}}\n"
            f"\\caption{{{caption}}}\n\\label{{fig:{name.replace('_', '-')}}}\n"
            "\\end{figure*}\n"
        )
    task_rows = "".join(
        f'<tr><td>{row["case"]}</td><td>{html.escape(row["instance_id"])}</td>'
        f'<td>{row["native_jct_s"]:.2f}</td><td>{row["predictive_jct_s"]:.2f}</td>'
        f'<td>{row["jct_change_pct"]:+.2f}%</td></tr>' for row in data["pairs"]
    )
    metric_rows = "".join(
        f'<tr><td>{row["metric"]}</td><td>{row["native"]:,.2f}</td>'
        f'<td>{row["predictive"]:,.2f}</td><td>{row["predictive_change_pct"]:+.2f}%</td>'
        "</tr>" for row in metrics
    )
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>V108 predictive versus V103 native</title>
<style>
body{{font:15px/1.55 system-ui,sans-serif;color:#202124;margin:32px auto;padding:0 20px;max-width:1100px}}
h1{{font-size:26px}}h2{{font-size:20px;margin-top:32px}}a{{color:#00649c}}
section{{border-top:1px solid #ddd;margin-top:28px}}img{{display:block;width:100%;height:auto;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:7px;border-bottom:1px solid #ddd;text-align:right}}
th:first-child,td:first-child,td:nth-child(2){{text-align:left}}td{{overflow-wrap:anywhere}}
code{{overflow-wrap:anywhere}}@media print{{section{{break-inside:avoid}}body{{max-width:none}}}}
</style><h1>V108 predictive versus V103 native</h1>
<p>{html.escape(notes)}</p><p>{html.escape(methods)}</p>
<p><a href="provenance.json">Provenance and checks</a> |
<a href="workflow_pairs.csv">All workflow pairs (CSV)</a> |
<a href="timeseries_60s.csv">60-second series (CSV)</a> |
<a href="server_request_latencies.csv">Server request latencies (CSV)</a></p>
<table><thead><tr><th>Full-run metric</th><th>Native</th><th>Predictive</th><th>Change</th></tr></thead>
<tbody>{metric_rows}</tbody></table>
{''.join(sections)}
<h2>All paired workflows</h2><table><thead><tr><th>Case</th><th>Task</th>
<th>Native JCT (s)</th><th>Predictive JCT (s)</th><th>Change</th></tr></thead>
<tbody>{task_rows}</tbody></table></html>
"""
    (output / "index.html").write_text(document)
    (output / "figures.tex").write_text("\n".join(tex))
    (output / "plotting_requirements.txt").write_text(
        f"matplotlib=={matplotlib.__version__}\nnumpy=={np.__version__}\n"
    )
    (output / "plot_native_policy_comparison.py").write_bytes(Path(__file__).read_bytes())
    (output / "README.md").write_text(
        "# V108 predictive versus V103 native\n\n" + notes + "\n\n" + methods
        + "\n\n## Reproduce\n\n```bash\n" + command + "\n```\n\n"
        + "Install matplotlib and numpy in an analysis environment. The script "
        "never starts an experiment or modifies raw inputs. --reuse-data verifies "
        "the saved input sizes/mtimes before rendering the sealed extraction.\n\n"
        "The exported data also support portable rendering after raw archival:\n\n"
        "```bash\npython plot_native_policy_comparison.py "
        "--render-data plot_data.json.gz --output-dir regenerated\n```\n\n"
        "This verifies the extracted-data SHA256 against the accompanying "
        "provenance.json and records the rendering origin without claiming a "
        "fresh read of archived raw inputs.\n\n"
        + "\n\n".join(f"## {f['name']}\n\n{f['caption']}" for f in figures)
        + "\n\nUse figures.tex with graphicx for a two-column manuscript. "
        "These captions must retain the single-run and historical-reference limitations.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--comparison", type=Path)
    source.add_argument("--render-data", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=450)
    parser.add_argument("--geometry-report", type=Path)
    parser.add_argument("--reuse-data", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "plot_data.json.gz"
    if args.render_data:
        if args.reuse_data or args.geometry_report:
            parser.error("--render-data cannot be combined with --reuse-data or --geometry-report")
        raw = args.render_data.read_bytes()
        manifest = json.loads(args.render_data.with_name("provenance.json").read_bytes())
        digest = hashlib.sha256(raw).hexdigest()
        if digest != manifest["extracted_data"]["sha256"]:
            raise ValueError("Extracted-data SHA256 does not match provenance")
        data = json.loads(gzip.decompress(raw))
        data["render_origin"] = {
            "mode": "sealed_extracted_data", "source": str(args.render_data.resolve()),
            "sha256": digest, "raw_inputs_revalidated": False,
        }
    elif args.reuse_data:
        with gzip.open(cache, "rt") as stream:
            data = json.load(stream)
        for source, snapshot in data["sources"].items():
            stat = Path(source).stat()
            if (stat.st_size, stat.st_mtime_ns) != (snapshot["bytes"], snapshot["mtime_ns"]):
                raise ValueError(f"Sealed source changed; extract again: {source}")
        if str(args.comparison.resolve()) != data["comparison_path"]:
            raise ValueError("Cached comparison path mismatch")
    else:
        sources = {}
        comparison = read_json(args.comparison, sources)
        paired = comparison["paired_completed_jct"]["predictive_h2d_vs_native"]
        if paired["paired_completed_workflows"] != 36:
            raise ValueError("This V108/V103 figure set expects all 36 paired workflows")
        pairs = [{
            "case": f"T{i:02d}", "instance_id": row["instance_id"],
            "native_jct_s": row["right_seconds"], "predictive_jct_s": row["left_seconds"],
            "jct_delta_s": row["delta_seconds"],
            "jct_change_pct": 100 * (row["left_seconds"] / row["right_seconds"] - 1),
            "native_model_calls": row["right_llm_request_count"],
            "predictive_model_calls": row["left_llm_request_count"],
            "model_calls_change_pct": 100 * (
                row["left_llm_request_count"] / row["right_llm_request_count"] - 1
            ),
        } for i, row in enumerate(paired["rows"], 1)]
        data = {
            "comparison_path": str(args.comparison.resolve()), "bin_seconds": 60,
            "window": comparison["tail_excluded_primary_comparison"]["policy"],
            "pairs": pairs, "arms": {},
            "extraction_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        for arm in ARMS:
            data["arms"][arm] = extract_arm(arm, comparison, sources, 60)
        data["sources"] = sources
        data["coarse"] = throughput_bins(data, 300)
        for arm in ARMS:
            values = [row["jct_s"] for row in data["arms"][arm]["workflows"]]
            if {r["instance_id"] for r in data["arms"][arm]["workflows"]} != {
                r["instance_id"] for r in pairs
            }:
                raise ValueError("Paired scatter and workflow distribution populations differ")
            run = data["arms"][arm]["run"]
            for actual, expected in (
                (average(values), run["jct_mean_seconds"]),
                (quantile(values, .5), run["jct_p50_seconds"]),
                (quantile(values, .95), run["jct_p95_seconds"]),
            ):
                if not math.isclose(actual, expected, abs_tol=1e-6):
                    raise ValueError("JCT population/quantile cross-check failed")
    if not args.render_data:
        data["render_origin"] = {
            "mode": "cached_extraction" if args.reuse_data else "raw_extraction",
            "raw_inputs_revalidated": not args.reuse_data,
        }
        comparison = read_json(args.comparison, data["sources"])
        data["common_metrics"] = {
            arm: comparison["tail_excluded_primary_comparison"]["runs"][arm][
                "sustained_load"]["windows"][0] for arm in ARMS
        }
    if args.geometry_report:
        geometry = read_json(args.geometry_report, data["sources"])
        data["geometry_diagnostic"] = {
            "source": str(args.geometry_report.resolve()),
            "start_seconds": geometry["start_seconds"], "end_seconds": geometry["end_seconds"],
            "native_context_tokens_mean": geometry["arms"]["native"][
                "sequence_tokens_per_request_mean"],
            "predictive_context_tokens_mean": geometry["arms"]["predictive_h2d"][
                "sequence_tokens_per_request_mean"],
        }
    with gzip.open(cache, "wt") as stream:
        json.dump(data, stream, allow_nan=False)
    figures = render_figures(data, output, args.dpi)
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    write_report(data, figures, output, command)
    print(json.dumps({
        "output": str(output), "figures": len(figures),
        "workflows_per_arm": {a: len(data["arms"][a]["workflows"]) for a in ARMS},
        "server_request_pairs": {a: len(data["arms"][a]["requests"]) for a in ARMS},
        "crosschecks": "JCT, request-latency quantiles, exact busy-window decode tokens passed",
    }, indent=2))


if __name__ == "__main__":
    main()
