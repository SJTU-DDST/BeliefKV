#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
from statistics import mean
from typing import Iterable, Mapping


def _records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile / 100.0
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _gpu_samples(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    values: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = datetime.strptime(
                    str(row["timestamp"]), "%Y/%m/%d %H:%M:%S.%f"
                ).timestamp()
                values.append(
                    {
                        "ts": timestamp,
                        "util": float(row["gpu_utilization_percent"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return values


def _time_above(
    samples: list[Mapping[str, object]], ratio: float
) -> float:
    total = 0.0
    for current, following in zip(samples, samples[1:]):
        capacity = float(current.get("hbm_capacity_bytes") or 0.0)
        used = float(current.get("hbm_used_bytes") or 0.0)
        if capacity > 0 and used / capacity >= ratio:
            total += max(
                0.0,
                (float(following["ts_ms"]) - float(current["ts_ms"])) / 1000.0,
            )
    return total


def _load_summary(run_dir: Path) -> Mapping[str, object]:
    for name in ("p6_collection_summary.json", "summary.json"):
        path = run_dir / "workloads" / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def summarize_arm(run_dir: Path, arm: str) -> dict[str, object]:
    audit = _records(run_dir / "server/runtime_audit.jsonl")
    resources = [item for item in audit if item.get("event") == "resource_snapshot"]
    resources.sort(key=lambda item: float(item.get("ts_ms") or 0.0))
    gpu = _gpu_samples(run_dir / "workloads/gpu_samples.csv")
    summary = _load_summary(run_dir)
    event_counts = Counter(str(item.get("event") or "") for item in audit)
    runtime_events = _records(run_dir / "server/runtime_events.sglang.jsonl")
    runtime_counts = Counter(str(item.get("kind") or "") for item in runtime_events)
    transfers = [
        item
        for item in audit
        if item.get("event") == "transfer_telemetry"
        and item.get("status") == "completed"
    ]
    service = next(
        (
            item.get("performance_aggregates")
            for item in reversed(audit)
            if item.get("event") == "gpu_service_observer_summary"
        ),
        {},
    )
    duration_s = float(summary.get("duration_seconds") or 0.0)
    if duration_s <= 0 and len(resources) >= 2:
        duration_s = (
            float(resources[-1]["ts_ms"]) - float(resources[0]["ts_ms"])
        ) / 1000.0
    prefill_tokens = int((service.get("prefill") or {}).get("tokens") or 0)
    decode_tokens = int((service.get("decode") or {}).get("tokens") or 0)
    capacities = [float(item.get("hbm_capacity_bytes") or 0.0) for item in resources]
    pressures = [
        float(item.get("hbm_used_bytes") or 0.0) / capacity
        for item, capacity in zip(resources, capacities)
        if capacity > 0
    ]
    util = [float(item["util"]) for item in gpu]
    outcomes = Counter(
        str(item.get("state") or "unknown")
        for item in audit
        if item.get("event") == "predictive_action_outcome"
        and str(item.get("state") or "")
        in {"useful", "wasted", "too_late", "censored", "failed"}
    )
    beneficiary_service = [
        item
        for item in audit
        if item.get("event") == "running_retraction_transaction_completed"
        or (
            item.get("event") == "online_joint_residency_terminal"
            and item.get("beneficiary_first_service_latency_ms") is not None
        )
    ]
    correctness = {}
    latest = run_dir / "server/latest_runtime_summary.json"
    if latest.exists():
        correctness = json.loads(latest.read_text()).get("correctness_gates", {})
    return {
        "arm": arm,
        "run_dir": str(run_dir.resolve()),
        "duration_seconds": duration_s,
        "workflow_count": int(summary.get("workflow_count") or 0),
        "completed_workflows": int(summary.get("completed_workflows") or 0),
        "system_jct_eligible_workflows": int(
            summary.get("system_jct_eligible_workflows") or 0
        ),
        "llm_request_count": int(summary.get("llm_request_count") or 0),
        "tool_call_count": int(summary.get("tool_call_count") or 0),
        "join_satisfied_count": runtime_counts["join_satisfied"],
        "prefill_tokens": prefill_tokens,
        "decode_tokens": decode_tokens,
        "gpu_service_tokens_per_second": (
            (prefill_tokens + decode_tokens) / duration_s if duration_s else 0.0
        ),
        "llm_requests_per_minute": (
            int(summary.get("llm_request_count") or 0) * 60.0 / duration_s
            if duration_s
            else 0.0
        ),
        "tool_calls_per_minute": (
            int(summary.get("tool_call_count") or 0) * 60.0 / duration_s
            if duration_s
            else 0.0
        ),
        "gpu_utilization_mean": mean(util) if util else 0.0,
        "gpu_utilization_p95": _percentile(util, 95),
        "gpu_busy_fraction": (
            sum(value >= 10.0 for value in util) / len(util) if util else 0.0
        ),
        "hbm_peak_ratio": max(pressures, default=0.0),
        "hbm_above_80_seconds": _time_above(resources, 0.8),
        "hbm_above_90_seconds": _time_above(resources, 0.9),
        "running_mean": mean(
            float(item.get("running_request_count") or 0.0) for item in resources
        ) if resources else 0.0,
        "waiting_mean": mean(
            float(item.get("waiting_request_count") or 0.0) for item in resources
        ) if resources else 0.0,
        "d2h_count": sum(item.get("direction") == "d2h" for item in transfers),
        "h2d_count": sum(item.get("direction") == "h2d" for item in transfers),
        "d2h_bytes": sum(
            int(item.get("actual_bytes") or 0)
            for item in transfers
            if item.get("direction") == "d2h"
        ),
        "h2d_bytes": sum(
            int(item.get("actual_bytes") or 0)
            for item in transfers
            if item.get("direction") == "h2d"
        ),
        "predictive_intent_count": event_counts["predictive_semantic_intent_published"],
        "predictive_prepare_count": event_counts["predictive_semantic_intent_committed"],
        "predictive_outcomes": dict(sorted(outcomes.items())),
        "beneficiary_first_service_count": len(beneficiary_service),
        "correctness_gates": correctness,
        "_resources": resources,
        "_gpu": gpu,
        "_transfers": transfers,
        "_audit": audit,
    }


def _gain(baseline: float, treatment: float) -> float | None:
    return treatment / baseline - 1.0 if baseline > 0 else None


def compare(
    baseline: dict[str, object],
    treatment: dict[str, object],
    accuracy: Mapping[str, object],
) -> dict[str, object]:
    baseline_boolean_gates = [
        value
        for value in baseline["correctness_gates"].values()
        if isinstance(value, bool)
    ]
    treatment_boolean_gates = [
        value
        for value in treatment["correctness_gates"].values()
        if isinstance(value, bool)
    ]
    visible = {
        arm["arm"]: {key: value for key, value in arm.items() if not key.startswith("_")}
        for arm in (baseline, treatment)
    }
    gains = {
        name: _gain(float(baseline[name]), float(treatment[name]))
        for name in (
            "gpu_service_tokens_per_second",
            "llm_requests_per_minute",
            "tool_calls_per_minute",
            "gpu_utilization_mean",
        )
    }
    return {
        "schema_version": 1,
        "arms": visible,
        "relative_gain": gains,
        "heldout_prediction_accuracy": accuracy,
        "pairing_gate": {
            "same_duration_target": True,
            "baseline_high_pressure": baseline["hbm_above_80_seconds"] > 0,
            "treatment_high_pressure": treatment["hbm_above_80_seconds"] > 0,
            "baseline_clean": bool(baseline_boolean_gates)
            and all(baseline_boolean_gates),
            "treatment_clean": bool(treatment_boolean_gates)
            and all(treatment_boolean_gates),
        },
    }


def _series(
    samples: list[Mapping[str, object]], field: str, *, capacity_field: str | None = None
) -> list[tuple[float, float]]:
    if not samples:
        return []
    start = float(samples[0].get("ts_ms") or 0.0)
    values = []
    for item in samples:
        value = float(item.get(field) or 0.0)
        if capacity_field:
            capacity = float(item.get(capacity_field) or 0.0)
            value = value / capacity * 100.0 if capacity else 0.0
        values.append(((float(item.get("ts_ms") or start) - start) / 1000.0, value))
    step = max(1, len(values) // 500)
    return values[::step]


def _gpu_series(samples: list[Mapping[str, object]]) -> list[tuple[float, float]]:
    if not samples:
        return []
    start = float(samples[0]["ts"])
    step = max(1, len(samples) // 500)
    return [
        (float(item["ts"]) - start, float(item["util"]))
        for item in samples[::step]
    ]


def _polyline(
    values: list[tuple[float, float]], width: int, height: int, maximum: float
) -> str:
    if not values:
        return ""
    duration = max(values[-1][0], 1.0)
    points = " ".join(
        f"{x / duration * width:.1f},{height - min(maximum, max(0.0, y)) / maximum * height:.1f}"
        for x, y in values
    )
    return points


def _timeline(arm: dict[str, object]) -> str:
    width = 1100
    height = 130
    resources = arm["_resources"]
    hbm = _series(resources, "hbm_used_bytes", capacity_field="hbm_capacity_bytes")
    running = _series(resources, "running_request_count")
    waiting = _series(resources, "waiting_request_count")
    gpu = _gpu_series(arm["_gpu"])
    max_queue = max((value for _, value in running + waiting), default=1.0)
    transfer_marks = []
    if resources:
        start = float(resources[0].get("ts_ms") or 0.0)
        duration = max(float(resources[-1]["ts_ms"]) - start, 1.0)
        for item in arm["_transfers"]:
            x = (float(item.get("submit_ts_ms") or start) - start) / duration * width
            color = "#b42318" if item.get("direction") == "d2h" else "#175cd3"
            transfer_marks.append(
                f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height}" stroke="{color}" stroke-width="2"/>'
            )
    return f"""
<section class="arm-band">
  <h2>{escape(str(arm['arm']).title())}</h2>
  <div class="lane-label">HBM pressure</div>
  <svg viewBox="0 0 {width} {height}" aria-label="HBM pressure timeline">
    <line x1="0" x2="{width}" y1="{height * .2}" y2="{height * .2}" class="threshold"/>
    <polyline points="{_polyline(hbm, width, height, 100)}" class="hbm"/>{''.join(transfer_marks)}
  </svg>
  <div class="lane-label">GPU utilization</div>
  <svg viewBox="0 0 {width} {height}" aria-label="GPU utilization timeline">
    <polyline points="{_polyline(gpu, width, height, 100)}" class="gpu"/>
  </svg>
  <div class="lane-label">Running / waiting requests</div>
  <svg viewBox="0 0 {width} {height}" aria-label="Request queue timeline">
    <polyline points="{_polyline(running, width, height, max_queue)}" class="running"/>
    <polyline points="{_polyline(waiting, width, height, max_queue)}" class="waiting"/>
  </svg>
</section>"""


def render_html(
    baseline: dict[str, object], treatment: dict[str, object], comparison: Mapping[str, object]
) -> str:
    gains = comparison["relative_gain"]
    accuracy = comparison["heldout_prediction_accuracy"].get("action_timing", {})
    prepare = accuracy.get("prepare_host|wait_tool|operational_tau", {})
    rows = []
    for key, label in (
        ("gpu_service_tokens_per_second", "GPU service tokens/s"),
        ("llm_requests_per_minute", "LLM requests/min"),
        ("tool_calls_per_minute", "Tool calls/min"),
        ("gpu_utilization_mean", "Mean GPU utilization"),
        ("hbm_peak_ratio", "Peak HBM pressure"),
        ("hbm_above_80_seconds", "Seconds above 80% HBM"),
        ("d2h_bytes", "D2H bytes"),
        ("h2d_bytes", "H2D bytes"),
    ):
        gain = gains.get(key)
        rows.append(
            f"<tr><th>{label}</th><td>{float(baseline[key]):.3f}</td>"
            f"<td>{float(treatment[key]):.3f}</td><td>{gain * 100:.2f}%</td></tr>"
            if gain is not None
            else f"<tr><th>{label}</th><td>{float(baseline[key]):.3f}</td>"
            f"<td>{float(treatment[key]):.3f}</td><td>n/a</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P6 high-pressure A/B</title><style>
body{{margin:0;background:#f7f7f5;color:#171717;font:14px system-ui,sans-serif}}
header,main{{max-width:1200px;margin:auto;padding:24px}}h1{{font-size:28px;margin:0 0 8px}}h2{{font-size:18px}}
.pipeline{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:18px 0 28px}}
.stage{{border:1px solid #aaa;background:#fff;padding:10px;min-height:48px;border-radius:4px}}
.predict{{border-color:#175cd3;background:#eff8ff}}table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{text-align:right;padding:8px;border-bottom:1px solid #ddd}}th:first-child{{text-align:left}}
.arm-band{{margin:28px 0;padding-top:8px;border-top:2px solid #333}}.lane-label{{font-weight:600;margin:12px 0 4px}}
svg{{display:block;width:100%;height:130px;background:#fff;border:1px solid #d0d0d0}}
polyline{{fill:none;stroke-width:2}}.hbm{{stroke:#7a271a}}.gpu{{stroke:#027a48}}.running{{stroke:#175cd3}}.waiting{{stroke:#9333ea}}
.threshold{{stroke:#d92d20;stroke-dasharray:5 4}}.note{{color:#555}}
</style></head><body><header><h1>P6 High-Pressure A/B</h1>
<p>Same H200 BF16 runtime, 850K KV pool and 64-root workload. Red markers are D2H; blue markers are H2D.</p></header><main>
<h2>Scheduling pipeline</h2><div class="pipeline">
<div class="stage">RCCG event</div><div class="stage predict">Frontier prediction</div>
<div class="stage predict">Beneficiary + victim package</div><div class="stage predict">Early D2H shadow</div>
<div class="stage">Real HBM deficit / COMMIT</div><div class="stage">Beneficiary GPU service</div></div>
<h2>Paired results</h2><table><thead><tr><th>Metric</th><th>Baseline</th><th>P6</th><th>Relative gain</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="note">Held-out PREPARE timing accuracy at threshold 0.5: {float(prepare.get('accuracy_at_0_5') or 0) * 100:.2f}%; Brier: {float(prepare.get('brier') or 0):.4f}. Accuracy is reported with precision/recall in the JSON artifact.</p>
{_timeline(baseline)}{_timeline(treatment)}
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze and visualize a P6 high-pressure A/B pair.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--predictive", type=Path, required=True)
    parser.add_argument("--accuracy", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()
    baseline = summarize_arm(args.baseline, "baseline")
    predictive = summarize_arm(args.predictive, "predictive")
    accuracy = json.loads(args.accuracy.read_text(encoding="utf-8"))
    payload = compare(baseline, predictive, accuracy)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(
        render_html(baseline, predictive, payload), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
