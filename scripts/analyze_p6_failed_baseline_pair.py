#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta
from html import escape
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping


def records(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def resource_records(run_dir: Path) -> list[dict[str, Any]]:
    values = [
        item
        for item in records(run_dir / "server/runtime_audit.jsonl")
        if item.get("event") == "resource_snapshot"
    ]
    values.sort(key=lambda item: float(item.get("ts_ms") or 0.0))
    return values


def resource_window(values: list[Mapping[str, Any]]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    first = float(values[0]["ts_ms"])
    last = float(values[-1]["ts_ms"])
    return first, last


def integrated_pressure(
    values: Iterable[Mapping[str, Any]], cutoff_ms: float | None = None
) -> dict[str, float]:
    accepted = [
        item
        for item in values
        if cutoff_ms is None or float(item.get("ts_ms") or 0.0) <= cutoff_ms
    ]
    duration = 0.0
    pressure_duration = 0.0
    above_80 = 0.0
    above_90 = 0.0
    peak = 0.0
    pressures: list[float] = []
    for current, following in zip(accepted, accepted[1:]):
        capacity = float(current.get("hbm_capacity_bytes") or 0.0)
        used = float(current.get("hbm_used_bytes") or 0.0)
        delta_s = max(
            0.0,
            (
                float(following.get("ts_ms") or 0.0)
                - float(current.get("ts_ms") or 0.0)
            )
            / 1000.0,
        )
        duration += delta_s
        if capacity <= 0:
            continue
        pressure = used / capacity
        pressures.append(pressure)
        peak = max(peak, pressure)
        pressure_duration += delta_s * pressure
        above_80 += delta_s if pressure >= 0.80 else 0.0
        above_90 += delta_s if pressure >= 0.90 else 0.0
    return {
        "integrated_seconds": duration,
        "mean_hbm_pressure": pressure_duration / duration if duration else 0.0,
        "peak_hbm_pressure": peak,
        "above_80_seconds": above_80,
        "above_90_seconds": above_90,
    }


def last_service_summary(run_dir: Path) -> dict[str, Any]:
    for item in reversed(list(records(run_dir / "server/runtime_audit.jsonl"))):
        if item.get("event") == "gpu_service_observer_summary":
            summary = item.get("performance_aggregates")
            return summary if isinstance(summary, dict) else {}
    return {}


def runtime_event_counts(
    run_dir: Path, cutoff_ms: float | None = None
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in records(run_dir / "server/runtime_events.sglang.jsonl"):
        if cutoff_ms is not None and float(item.get("ts_ms") or 0.0) > cutoff_ms:
            continue
        counts[str(item.get("kind") or "")] += 1
    return counts


def baseline_results(
    run_dir: Path, cutoff_wall: datetime | None = None
) -> dict[str, Any]:
    outcomes: Counter[str] = Counter()
    valid = 0
    active_outcomes: Counter[str] = Counter()
    active_valid = 0
    for path in (run_dir / "workloads/workflows").glob("*/result.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        outcome = str(item.get("outcome") or "unknown")
        outcomes[outcome] += 1
        valid += int(bool(item.get("measurement_valid")))
        if cutoff_wall is None or datetime.fromtimestamp(path.stat().st_mtime) <= cutoff_wall:
            active_outcomes[outcome] += 1
            active_valid += int(bool(item.get("measurement_valid")))
    return {
        "result_workflows": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "measurement_valid_workflows": valid,
        "active_result_workflows": sum(active_outcomes.values()),
        "active_outcomes": dict(sorted(active_outcomes.items())),
        "active_measurement_valid_workflows": active_valid,
    }


def predictive_results(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "workloads/summary.json"
    if not path.exists():
        return {}
    summary = json.loads(path.read_text(encoding="utf-8"))
    outcomes = Counter(
        str(item.get("outcome") or "unknown") for item in summary.get("workflows", ())
    )
    return {
        **{
            key: summary.get(key)
            for key in (
                "workflow_count",
                "completed_workflows",
                "successful_workflows",
                "measurement_valid_workflows",
                "llm_request_count",
                "tool_call_count",
                "duration_seconds",
            )
        },
        "outcomes": dict(sorted(outcomes.items())),
    }


def transfer_summary(
    run_dir: Path, cutoff_ms: float | None = None
) -> dict[str, Any]:
    by_class: Counter[str] = Counter()
    completed: Counter[str] = Counter()
    bytes_by_class: Counter[str] = Counter()
    duration_by_class: Counter[str] = Counter()
    predictive_bytes = 0
    predictive_count = 0
    for item in records(run_dir / "server/transfer_telemetry.jsonl"):
        if item.get("event") != "transfer_telemetry":
            continue
        submit_ms = float(item.get("submit_ts_ms") or 0.0)
        if cutoff_ms is not None and submit_ms > cutoff_ms:
            continue
        direction = str(item.get("direction") or "unknown")
        predictive = bool(item.get("predictive_intent_id"))
        key = f"predictive_{direction}" if predictive else f"native_{direction}"
        by_class[key] += 1
        bytes_by_class[key] += int(item.get("actual_bytes") or 0)
        duration_by_class[key] += max(
            0.0,
            float(item.get("complete_ts_ms") or submit_ms) - submit_ms,
        ) / 1000.0
        if item.get("status") == "completed":
            completed[key] += 1
        if predictive:
            predictive_count += 1
            predictive_bytes += int(item.get("actual_bytes") or 0)
    return {
        "command_count": sum(by_class.values()),
        "completed_count": sum(completed.values()),
        "actual_bytes": sum(bytes_by_class.values()),
        "transfer_duration_seconds": sum(duration_by_class.values()),
        "by_class": {
            key: {
                "count": by_class[key],
                "completed_count": completed[key],
                "actual_bytes": bytes_by_class[key],
                "transfer_duration_seconds": duration_by_class[key],
            }
            for key in sorted(set(by_class) | set(completed))
        },
        "predictive_command_count": predictive_count,
        "predictive_actual_bytes": predictive_bytes,
    }


def timeline_gpu_active(timeline_path: Path) -> tuple[float, float, datetime]:
    payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    resources = payload.get("resources") or []
    last_running = max(
        (
            float(item.get("t_ms") or 0.0)
            for item in resources
            if int(item.get("running") or 0) > 0
        ),
        default=0.0,
    )
    start = datetime.strptime(str(payload["start_anchor"]), "%Y-%m-%dT%H:%M:%S")
    samples = payload.get("gpu_samples") or []
    accepted = [
        float(item.get("util") or 0.0)
        for item in samples
        if float(item.get("t_ms") or 0.0) <= last_running
    ]
    busy = sum(value >= 10.0 for value in accepted) / len(accepted) if accepted else 0.0
    return (mean(accepted) if accepted else 0.0, busy, start + timedelta(milliseconds=last_running))


def queue_summary(
    run_dir: Path, cutoff_ms: float | None = None
) -> dict[str, float]:
    running: list[float] = []
    waiting: list[float] = []
    used_tokens: list[float] = []
    pressure: list[float] = []
    for item in records(run_dir / "workloads/sglang_metrics.jsonl"):
        ts_ms = float(item.get("monotonic_ts_ms") or 0.0)
        if cutoff_ms is not None and ts_ms > cutoff_ms:
            continue
        running.append(float(item.get("num_running_reqs") or 0.0))
        waiting.append(float(item.get("num_queue_reqs") or 0.0))
        used_tokens.append(float(item.get("num_used_tokens") or 0.0))
        pressure.append(float(item.get("resident_pressure") or 0.0))
    count = len(running)
    return {
        "sample_count": count,
        "mean_running": mean(running) if running else 0.0,
        "mean_waiting": mean(waiting) if waiting else 0.0,
        "mean_used_tokens": mean(used_tokens) if used_tokens else 0.0,
        "mean_resident_pressure": mean(pressure) if pressure else 0.0,
    }


def service_metrics(run_dir: Path, duration_s: float) -> dict[str, float]:
    service = last_service_summary(run_dir)
    prefill = service.get("prefill") or {}
    decode = service.get("decode") or {}
    prefill_tokens = int(prefill.get("tokens") or 0)
    decode_tokens = int(decode.get("tokens") or 0)
    return {
        "prefill_tokens": prefill_tokens,
        "decode_tokens": decode_tokens,
        "service_tokens": prefill_tokens + decode_tokens,
        "prefill_tokens_per_second": prefill_tokens / duration_s if duration_s else 0.0,
        "decode_tokens_per_second": decode_tokens / duration_s if duration_s else 0.0,
        "gpu_service_tokens_per_second": (
            (prefill_tokens + decode_tokens) / duration_s if duration_s else 0.0
        ),
    }


def rate(count: int, duration_s: float, period: float) -> float:
    return count * period / duration_s if duration_s else 0.0


def gain(baseline: float, predictive: float) -> float | None:
    return predictive / baseline - 1.0 if baseline > 0 else None


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if abs(number) < 1024.0 or unit == units[-1]:
            return f"{number:.2f} {unit}"
        number /= 1024.0
    return f"{number:.2f} TiB"


def row(label: str, baseline: Any, predictive: Any, note: str = "") -> str:
    relative = ""
    if isinstance(baseline, (int, float)) and isinstance(predictive, (int, float)):
        change = gain(float(baseline), float(predictive))
        relative = f"{change * 100:.2f}%" if change is not None else "n/a"
    return (
        f"<tr><th>{escape(label)}</th><td>{escape(str(baseline))}</td>"
        f"<td>{escape(str(predictive))}</td><td>{escape(relative)}</td>"
        f"<td>{escape(note)}</td></tr>"
    )


def metric_row(
    label: str,
    baseline: float,
    predictive: float,
    note: str = "",
    formatter: str = "{:.2f}",
) -> str:
    change = gain(baseline, predictive)
    relative = f"{change * 100:.2f}%" if change is not None else "n/a"
    return (
        f"<tr><th>{escape(label)}</th>"
        f"<td>{formatter.format(baseline)}</td>"
        f"<td>{formatter.format(predictive)}</td>"
        f"<td>{escape(relative)}</td><td>{escape(note)}</td></tr>"
    )


def render_html(payload: Mapping[str, Any]) -> str:
    baseline = payload["arms"]["baseline_pre_starvation"]
    predictive = payload["arms"]["predictive"]
    full = payload["arms"]["baseline_full_failed_window"]
    rows = [
        metric_row("Resource window seconds", baseline["duration_seconds"], predictive["duration_seconds"]),
        metric_row("GPU service tokens/s", baseline["gpu_service_tokens_per_second"], predictive["gpu_service_tokens_per_second"], "Primary throughput proxy"),
        metric_row("Prefill tokens/s", baseline["prefill_tokens_per_second"], predictive["prefill_tokens_per_second"]),
        metric_row("Decode tokens/s", baseline["decode_tokens_per_second"], predictive["decode_tokens_per_second"]),
        metric_row("LLM requests/min", baseline["llm_requests_per_minute"], predictive["llm_requests_per_minute"]),
        metric_row("Tool calls/min", baseline["tool_calls_per_minute"], predictive["tool_calls_per_minute"]),
        metric_row("Result workflows/hour", baseline["result_workflows_per_hour"], predictive["completed_workflows_per_hour"], "Reference only; arms are not contract-complete"),
        metric_row("Valid workflows/hour", baseline["measurement_valid_workflows_per_hour"], predictive["measurement_valid_workflows_per_hour"], "Reference only"),
        metric_row("Mean GPU utilization", baseline["gpu_utilization_mean_percent"], predictive["gpu_utilization_mean_percent"]),
        metric_row("GPU busy fraction", baseline["gpu_busy_fraction"] * 100.0, predictive["gpu_busy_fraction"] * 100.0, "GPU util >= 10%"),
        metric_row("Mean running", baseline["queue"]["mean_running"], predictive["queue"]["mean_running"]),
        metric_row("Mean waiting", baseline["queue"]["mean_waiting"], predictive["queue"]["mean_waiting"]),
        metric_row("Mean HBM pressure", baseline["pressure"]["mean_hbm_pressure"] * 100.0, predictive["pressure"]["mean_hbm_pressure"] * 100.0),
        metric_row("Seconds above 80% HBM", baseline["pressure"]["above_80_seconds"], predictive["pressure"]["above_80_seconds"], formatter="{:.1f}"),
        row("Transfer commands", baseline["transfers"]["command_count"], predictive["transfers"]["command_count"]),
        row("Transfer actual bytes", format_bytes(baseline["transfers"]["actual_bytes"]), format_bytes(predictive["transfers"]["actual_bytes"])),
        row("Predictive transfer commands", baseline["transfers"]["predictive_command_count"], predictive["transfers"]["predictive_command_count"]),
        row("Predictive transfer bytes", format_bytes(baseline["transfers"]["predictive_actual_bytes"]), format_bytes(predictive["transfers"]["predictive_actual_bytes"])),
    ]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>v58 failed baseline vs predictive</title><style>
body{{margin:0;background:#f6f7f8;color:#171717;font:14px system-ui,sans-serif}}main{{max-width:1240px;margin:auto;padding:28px}}
h1{{font-size:28px;margin:0 0 10px}}h2{{margin-top:28px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:right}}
th:first-child,td:first-child{{text-align:left}}tr:nth-child(even){{background:#fafafa}}.note{{color:#555}}.warning{{border-left:4px solid #b42318;padding:12px;background:#fff}}
a{{color:#175cd3}}code{{background:#ececec;padding:2px 4px}}.links a{{margin-right:18px}}
</style></head><body><main><h1>v58 baseline attempt2 vs predictive</h1>
<p>Baseline attempt is incomplete and failed with waiting-only admission starvation. This report compares predictive against the baseline <b>pre-starvation window</b>: from the first resource snapshot through the last snapshot with running&gt;0.</p>
<div class="warning"><b>Not a formal A/B result.</b> Baseline has only {baseline['active_result_workflows']} result files before starvation and {full['result_workflows']} result files overall. The current rerun must complete before making a throughput claim.</div>
<h2>Paired metrics</h2><table><thead><tr><th>Metric</th><th>Baseline pre-starvation</th><th>Predictive</th><th>Relative change</th><th>Note</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Baseline failure boundary</h2><p class="note">Last running request timestamp: <code>{payload['pairing_gate']['baseline_cutoff_ts_ms']}</code>. Full failed window was {full['duration_seconds']:.2f}s; pre-starvation window was {baseline['duration_seconds']:.2f}s. Full-window GPU service throughput was {full['gpu_service_tokens_per_second']:.2f} tokens/s.</p>
<h2>Timelines</h2><p class="links"><a href="baseline_attempt2_execution_timeline.html">Baseline timeline</a><a href="predictive_execution_timeline.html">Predictive timeline</a></p>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a failed/interrupted P6 baseline against a predictive run."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--predictive", type=Path, required=True)
    parser.add_argument("--baseline-timeline", type=Path, required=True)
    parser.add_argument("--predictive-timeline", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()

    baseline_resources = resource_records(args.baseline)
    predictive_resources = resource_records(args.predictive)
    baseline_cutoff = max(
        (
            float(item.get("ts_ms") or 0.0)
            for item in baseline_resources
            if int(item.get("running_request_count") or 0) > 0
        ),
        default=float(baseline_resources[-1]["ts_ms"]) if baseline_resources else 0.0,
    )
    baseline_first, baseline_last = resource_window(baseline_resources)
    predictive_first, predictive_last = resource_window(predictive_resources)
    baseline_duration = max(0.0, (baseline_cutoff - baseline_first) / 1000.0)
    full_duration = max(0.0, (baseline_last - baseline_first) / 1000.0)
    predictive_duration = max(0.0, (predictive_last - predictive_first) / 1000.0)
    baseline_gpu, baseline_busy, cutoff_wall = timeline_gpu_active(args.baseline_timeline)
    predictive_timeline = json.loads(args.predictive_timeline.read_text(encoding="utf-8"))
    predictive_summary = predictive_timeline.get("summary") or {}
    predictive_results_payload = predictive_results(args.predictive)
    baseline_result_payload = baseline_results(args.baseline, cutoff_wall)
    baseline_events = runtime_event_counts(args.baseline, baseline_cutoff)
    predictive_events = runtime_event_counts(args.predictive)
    baseline_service = service_metrics(args.baseline, baseline_duration)
    predictive_service = service_metrics(args.predictive, predictive_duration)
    full_service = service_metrics(args.baseline, full_duration)
    baseline_transfer = transfer_summary(args.baseline, baseline_cutoff)
    predictive_transfer = transfer_summary(args.predictive)
    baseline_outcomes = baseline_result_payload
    predictive_count = int(predictive_results_payload.get("workflow_count") or 0)
    predictive_completed = int(predictive_results_payload.get("completed_workflows") or 0)
    predictive_valid = int(
        predictive_results_payload.get("measurement_valid_workflows") or 0
    )

    baseline_arm = {
        "duration_seconds": baseline_duration,
        **baseline_service,
        "llm_requests_per_minute": rate(baseline_events["llm_submit"], baseline_duration, 60.0),
        "tool_calls_per_minute": rate(baseline_events["tool_start"], baseline_duration, 60.0),
        "result_workflows_per_hour": rate(
            baseline_outcomes["active_result_workflows"], baseline_duration, 3600.0
        ),
        "measurement_valid_workflows_per_hour": rate(
            baseline_outcomes["active_measurement_valid_workflows"],
            baseline_duration,
            3600.0,
        ),
        "gpu_utilization_mean_percent": baseline_gpu,
        "gpu_busy_fraction": baseline_busy,
        "pressure": integrated_pressure(baseline_resources, baseline_cutoff),
        "queue": queue_summary(args.baseline, baseline_cutoff),
        "transfers": baseline_transfer,
        **baseline_outcomes,
    }
    full_arm = {
        "duration_seconds": full_duration,
        **full_service,
        **baseline_result_payload,
    }
    predictive_arm = {
        "duration_seconds": predictive_duration,
        "workload_summary_duration_seconds": predictive_results_payload.get("duration_seconds"),
        **predictive_service,
        "llm_requests_per_minute": rate(
            int(predictive_results_payload.get("llm_request_count") or predictive_events["llm_submit"]),
            predictive_duration,
            60.0,
        ),
        "tool_calls_per_minute": rate(
            int(predictive_results_payload.get("tool_call_count") or predictive_events["tool_start"]),
            predictive_duration,
            60.0,
        ),
        "completed_workflows_per_hour": rate(predictive_completed, predictive_duration, 3600.0),
        "measurement_valid_workflows_per_hour": rate(predictive_valid, predictive_duration, 3600.0),
        "gpu_utilization_mean_percent": float(
            predictive_summary.get("gpu_utilization_mean") or 0.0
        ),
        "gpu_busy_fraction": float(
            predictive_summary.get("gpu_busy_sample_fraction") or 0.0
        ),
        "pressure": integrated_pressure(predictive_resources),
        "queue": queue_summary(args.predictive),
        "transfers": predictive_transfer,
        **predictive_results_payload,
        "duration_seconds": predictive_duration,
    }
    payload = {
        "schema_version": 1,
        "pairing_gate": {
            "formal_ab_valid": False,
            "reason": "baseline_attempt2 failed with waiting-only admission starvation",
            "baseline_window": "first_resource_snapshot_to_last_running_gt_zero",
            "baseline_cutoff_ts_ms": baseline_cutoff,
            "baseline_cutoff_wall": cutoff_wall.isoformat(),
        },
        "arms": {
            "baseline_pre_starvation": baseline_arm,
            "baseline_full_failed_window": full_arm,
            "predictive": predictive_arm,
        },
        "relative_gain": {
            name: gain(float(baseline_arm[name]), float(predictive_arm[name]))
            for name in (
                "gpu_service_tokens_per_second",
                "prefill_tokens_per_second",
                "decode_tokens_per_second",
                "llm_requests_per_minute",
                "tool_calls_per_minute",
                "gpu_utilization_mean_percent",
            )
        },
        "source_runs": {
            "baseline": str(args.baseline.resolve()),
            "predictive": str(args.predictive.resolve()),
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.output_html.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps(payload["relative_gain"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
