from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_LOG_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_GPU_TS_FORMAT = "%Y/%m/%d %H:%M:%S.%f"
_BATCH_RE = re.compile(
    r"^\[(?P<timestamp>[^]]+)\] (?P<phase>Prefill|Decode) batch\.(?P<body>.*)$"
)
_NUMBER_FIELD_RE = re.compile(
    r"(?P<name>#new-seq|#new-token|#cached-token|#running-req|#queue-req|"
    r"token usage|gen throughput \(token/s\)):\s*(?P<value>-?[0-9.]+)"
)
_CUDA_GRAPH_RE = re.compile(r"cuda graph:\s*(?P<value>True|False)")
_DECODE_LOG_INTERVAL_RE = re.compile(r"decode_log_interval=(?P<value>[0-9]+)")


@dataclass(frozen=True)
class ExecutionTimeline:
    arm: str
    run_dir: str
    start_anchor: str
    duration_ms: float
    gpu_samples: tuple[dict[str, object], ...]
    service_observations: tuple[dict[str, object], ...]
    queue_samples: tuple[dict[str, object], ...]
    decode_windows: tuple[dict[str, object], ...]
    transfers: tuple[dict[str, object], ...]
    resources: tuple[dict[str, object], ...]
    external_waits: tuple[dict[str, object], ...]
    event_bins: tuple[dict[str, object], ...]
    summary: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_execution_timeline(
    run_dir: Path,
    *,
    arm: str,
    gpu_busy_threshold: float = 10.0,
) -> ExecutionTimeline:
    run_dir = run_dir.resolve()
    audit_path = run_dir / "server/runtime_audit.jsonl"
    transfer_path = run_dir / "server/transfer_telemetry.jsonl"
    runtime_events_path = run_dir / "server/runtime_events.sglang.jsonl"
    server_log_path = run_dir / "server/server.log"
    gpu_path = run_dir / "workloads/gpu_samples.csv"
    for path in (audit_path, server_log_path):
        if not path.exists():
            raise FileNotFoundError(path)

    runtime_start_ms, resources, audit_bins = _load_audit(audit_path)
    wall_anchor = _server_wall_anchor(server_log_path)
    services, decode_windows = _load_service_observations(
        server_log_path,
        wall_anchor=wall_anchor,
    )
    queue_samples = [
        {
            "t_ms": item["t_ms"],
            "running": item["running"],
            "waiting": item["waiting"],
        }
        for item in services
    ]
    gpu_samples = _load_gpu_samples(gpu_path, wall_anchor=wall_anchor)
    transfers = _load_transfers(transfer_path, runtime_start_ms=runtime_start_ms)
    external_waits, runtime_bins = _load_runtime_events(
        runtime_events_path,
        runtime_start_ms=runtime_start_ms,
    )
    event_bins = _merge_event_bins(audit_bins, runtime_bins)

    duration_ms = max(
        [0.0]
        + [float(item["t_ms"]) for item in gpu_samples]
        + [float(item["t_ms"]) for item in services]
        + [float(item["end_ms"]) for item in decode_windows]
        + [float(item["end_ms"]) for item in transfers]
        + [float(item["t_ms"]) for item in resources]
        + [float(item["t_ms"]) for item in external_waits]
        + [float(item["t_ms"]) for item in event_bins]
    )
    busy_intervals = _gpu_busy_intervals(
        gpu_samples,
        threshold=gpu_busy_threshold,
    )
    decode_intervals = _merge_intervals(
        (float(item["start_ms"]), float(item["end_ms"]))
        for item in decode_windows
    )
    busy_starts = [item[0] for item in busy_intervals]
    decode_starts = [item[0] for item in decode_intervals]
    enriched_transfers: list[dict[str, object]] = []
    for transfer in transfers:
        start_ms = float(transfer["start_ms"])
        end_ms = float(transfer["end_ms"])
        duration = max(0.0, end_ms - start_ms)
        busy_overlap = _interval_overlap_ms(
            start_ms,
            end_ms,
            busy_intervals,
            busy_starts,
        )
        decode_overlap = _interval_overlap_ms(
            start_ms,
            end_ms,
            decode_intervals,
            decode_starts,
        )
        enriched_transfers.append(
            {
                **transfer,
                "duration_ms": duration,
                "gpu_busy_overlap_ms": busy_overlap,
                "decode_overlap_ms": decode_overlap,
                "potentially_hidden_fraction": (
                    min(1.0, busy_overlap / duration) if duration > 0 else 0.0
                ),
            }
        )

    summary = _summarize(
        duration_ms=duration_ms,
        gpu_samples=gpu_samples,
        services=services,
        decode_windows=decode_windows,
        transfers=enriched_transfers,
        resources=resources,
        queue_samples=queue_samples,
        external_waits=external_waits,
        event_bins=event_bins,
        gpu_busy_threshold=gpu_busy_threshold,
    )
    return ExecutionTimeline(
        arm=arm,
        run_dir=str(run_dir),
        start_anchor=wall_anchor.isoformat(),
        duration_ms=duration_ms,
        gpu_samples=tuple(_downsample(gpu_samples, 5000)),
        service_observations=tuple(services),
        queue_samples=tuple(_downsample(queue_samples, 5000)),
        decode_windows=tuple(decode_windows),
        transfers=tuple(enriched_transfers),
        resources=tuple(_downsample(resources, 5000)),
        external_waits=tuple(_downsample(external_waits, 5000)),
        event_bins=tuple(event_bins),
        summary=summary,
    )


def render_execution_timeline(
    timeline: ExecutionTimeline,
    output_html: Path,
    *,
    title: str | None = None,
) -> tuple[Path, Path]:
    output_html = output_html.resolve()
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json = output_html.with_suffix(".json")
    payload = timeline.to_dict()
    output_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    output_html.write_text(
        _render_html(
            payload,
            title=title or f"BeliefKV {timeline.arm.title()} Execution Timeline",
        ),
        encoding="utf-8",
    )
    return output_html, output_json


def _load_audit(
    path: Path,
) -> tuple[float, list[dict[str, object]], dict[int, dict[str, int]]]:
    runtime_start_ms: float | None = None
    resources: list[dict[str, object]] = []
    bins: dict[int, dict[str, int]] = {}
    important = {
        "predictive_risk_enqueued": "risk",
        "predictive_semantic_intent_published": "intent",
        "predictive_semantic_intent_committed": "commit",
        "predictive_semantic_intent_rejected": "reject",
        "running_retraction_residency_queued": "retraction",
        "running_retraction_residency_ack": "retraction",
        "restore_obligation_source_terminal": "restore",
        "restore_obligation_terminal": "restore",
        "restore_service_grace_terminal": "restore",
    }
    pending: list[tuple[float, Mapping[str, object]]] = []
    for record in _read_jsonl(path):
        event = str(record.get("event") or "")
        ts_ms = _finite_float(record.get("ts_ms"))
        if event == "runtime_initialized" and ts_ms is not None:
            runtime_start_ms = ts_ms
        if ts_ms is None:
            continue
        pending.append((ts_ms, record))
    if runtime_start_ms is None:
        if not pending:
            raise ValueError(f"no timestamped audit records in {path}")
        runtime_start_ms = min(item[0] for item in pending)
    for ts_ms, record in pending:
        elapsed = max(0.0, ts_ms - runtime_start_ms)
        event = str(record.get("event") or "")
        if event == "resource_snapshot":
            capacity = _finite_float(record.get("hbm_capacity_bytes"))
            used = _finite_float(record.get("hbm_used_bytes"))
            resources.append(
                {
                    "t_ms": elapsed,
                    "hbm_ratio": used / capacity if used is not None and capacity else 0.0,
                    "host_ratio": _ratio(
                        record.get("host_used_bytes"),
                        record.get("host_capacity_bytes"),
                    ),
                    "running": int(record.get("running_request_count") or 0),
                    "waiting": int(record.get("waiting_request_count") or 0),
                    "migratable_ratio": (
                        (_finite_float(record.get("migratable_gpu_bytes")) or 0.0)
                        / capacity
                        if capacity
                        else 0.0
                    ),
                }
            )
        category = important.get(event)
        if category:
            second = max(0, int(elapsed // 1000.0))
            slot = bins.setdefault(second, {})
            slot[category] = slot.get(category, 0) + 1
    resources.sort(key=lambda item: float(item["t_ms"]))
    return runtime_start_ms, resources, bins


def _server_wall_anchor(path: Path) -> datetime:
    fallback: datetime | None = None
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = re.match(r"^\[([^]]+)\]", line)
            if match:
                try:
                    parsed = datetime.strptime(match.group(1), _LOG_TS_FORMAT)
                except ValueError:
                    continue
                fallback = fallback or parsed
                if "Application startup complete" in line:
                    return parsed
    if fallback is None:
        raise ValueError(f"no wall-clock timestamps in {path}")
    return fallback


def _load_service_observations(
    path: Path,
    *,
    wall_anchor: datetime,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    observations: list[dict[str, object]] = []
    decode_log_interval = 40
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            interval_match = _DECODE_LOG_INTERVAL_RE.search(line)
            if interval_match:
                decode_log_interval = int(interval_match.group("value"))
            match = _BATCH_RE.match(line.strip())
            if not match:
                continue
            timestamp = datetime.strptime(match.group("timestamp"), _LOG_TS_FORMAT)
            elapsed_ms = max(0.0, (timestamp - wall_anchor).total_seconds() * 1000.0)
            fields = {
                item.group("name"): float(item.group("value"))
                for item in _NUMBER_FIELD_RE.finditer(match.group("body"))
            }
            graph_match = _CUDA_GRAPH_RE.search(match.group("body"))
            phase = match.group("phase").lower()
            observations.append(
                {
                    "t_ms": elapsed_ms,
                    "phase": phase,
                    "new_sequences": int(fields.get("#new-seq", 0.0)),
                    "new_tokens": int(fields.get("#new-token", 0.0)),
                    "cached_tokens": int(fields.get("#cached-token", 0.0)),
                    "running": int(fields.get("#running-req", 0.0)),
                    "waiting": int(fields.get("#queue-req", 0.0)),
                    "hbm_ratio": fields.get("token usage", 0.0),
                    "throughput": fields.get("gen throughput (token/s)", 0.0),
                    "cuda_graph": (
                        graph_match.group("value") == "True" if graph_match else None
                    ),
                }
            )
    observations.sort(key=lambda item: float(item["t_ms"]))
    decode_windows: list[dict[str, object]] = []
    for item in observations:
        if item["phase"] != "decode":
            continue
        throughput = float(item["throughput"])
        running = int(item["running"])
        if throughput <= 0 or running <= 0:
            continue
        estimated_ms = min(
            30_000.0,
            max(1.0, running * decode_log_interval / throughput * 1000.0),
        )
        end_ms = float(item["t_ms"])
        decode_windows.append(
            {
                "start_ms": max(0.0, end_ms - estimated_ms),
                "end_ms": end_ms,
                "running": running,
                "throughput": throughput,
                "cuda_graph": item["cuda_graph"],
                "semantics": "inferred_from_decode_log_interval_and_throughput",
            }
        )
    return observations, decode_windows


def _load_gpu_samples(path: Path, *, wall_anchor: datetime) -> list[dict[str, object]]:
    if not path.exists():
        return []
    samples: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                timestamp = datetime.strptime(row["timestamp"].strip(), _GPU_TS_FORMAT)
                samples.append(
                    {
                        "t_ms": max(
                            0.0,
                            (timestamp - wall_anchor).total_seconds() * 1000.0,
                        ),
                        "util": float(row["gpu_utilization_percent"]),
                        "memory_util": float(row["memory_utilization_percent"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return samples


def _load_transfers(path: Path, *, runtime_start_ms: float) -> list[dict[str, object]]:
    if not path.exists():
        return []
    transfers: list[dict[str, object]] = []
    for record in _read_jsonl(path):
        if record.get("event") != "transfer_telemetry":
            continue
        submit = _finite_float(record.get("submit_ts_ms"))
        complete = _finite_float(record.get("complete_ts_ms"))
        actual_bytes = int(record.get("actual_bytes") or 0)
        if submit is None or complete is None or complete < submit or actual_bytes <= 0:
            continue
        transfers.append(
            {
                "start_ms": max(0.0, submit - runtime_start_ms),
                "end_ms": max(0.0, complete - runtime_start_ms),
                "direction": str(record.get("direction") or "unknown"),
                "bytes": actual_bytes,
                "extent_count": int(record.get("extent_count") or 0),
                "kind": str(record.get("command_kind") or ""),
                "command_id": str(record.get("command_id") or ""),
                "predictive": bool(record.get("predictive_intent_id")),
                "status": str(record.get("status") or "unknown"),
            }
        )
    transfers.sort(key=lambda item: (float(item["start_ms"]), str(item["command_id"])))
    return transfers


def _load_runtime_events(
    path: Path,
    *,
    runtime_start_ms: float,
) -> tuple[list[dict[str, object]], dict[int, dict[str, int]]]:
    if not path.exists():
        return [], {}
    active_tools = 0
    active_joins = 0
    waits: list[dict[str, object]] = []
    bins: dict[int, dict[str, int]] = {}
    events = sorted(
        (
            record
            for record in _read_jsonl(path)
            if _finite_float(record.get("ts_ms")) is not None
        ),
        key=lambda item: float(item["ts_ms"]),
    )
    for record in events:
        elapsed = max(0.0, float(record["ts_ms"]) - runtime_start_ms)
        kind = str(record.get("kind") or "")
        if kind == "tool_start":
            active_tools += 1
        elif kind == "tool_end":
            active_tools = max(0, active_tools - 1)
        elif kind == "join_wait":
            active_joins += 1
        elif kind == "join_satisfied":
            active_joins = max(0, active_joins - 1)
        else:
            continue
        waits.append(
            {
                "t_ms": elapsed,
                "active_tools": active_tools,
                "active_joins": active_joins,
            }
        )
        second = int(elapsed // 1000.0)
        slot = bins.setdefault(second, {})
        slot[kind] = slot.get(kind, 0) + 1
    return waits, bins


def _merge_event_bins(
    *sources: Mapping[int, Mapping[str, int]],
) -> list[dict[str, object]]:
    merged: dict[int, dict[str, int]] = {}
    for source in sources:
        for second, counts in source.items():
            target = merged.setdefault(second, {})
            for key, value in counts.items():
                target[key] = target.get(key, 0) + int(value)
    return [
        {"t_ms": second * 1000.0, **counts}
        for second, counts in sorted(merged.items())
    ]


def _gpu_busy_intervals(
    samples: list[dict[str, object]],
    *,
    threshold: float,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for current, following in zip(samples, samples[1:]):
        if float(current["util"]) < threshold:
            continue
        start = float(current["t_ms"])
        end = min(float(following["t_ms"]), start + 1000.0)
        if end > start:
            intervals.append((start, end))
    return _merge_intervals(intervals)


def _merge_intervals(
    intervals: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(item[0], item[1]) for item in merged]


def _interval_overlap_ms(
    start_ms: float,
    end_ms: float,
    intervals: list[tuple[float, float]],
    starts: list[float],
) -> float:
    if end_ms <= start_ms or not intervals:
        return 0.0
    index = max(0, bisect_left(starts, start_ms) - 1)
    total = 0.0
    while index < len(intervals):
        other_start, other_end = intervals[index]
        if other_start >= end_ms:
            break
        total += max(0.0, min(end_ms, other_end) - max(start_ms, other_start))
        index += 1
    return min(end_ms - start_ms, total)


def _summarize(
    *,
    duration_ms: float,
    gpu_samples: list[dict[str, object]],
    services: list[dict[str, object]],
    decode_windows: list[dict[str, object]],
    transfers: list[dict[str, object]],
    resources: list[dict[str, object]],
    queue_samples: list[dict[str, object]],
    external_waits: list[dict[str, object]],
    event_bins: list[dict[str, object]],
    gpu_busy_threshold: float,
) -> dict[str, object]:
    transfer_duration = sum(float(item["duration_ms"]) for item in transfers)
    hidden_duration = sum(float(item["gpu_busy_overlap_ms"]) for item in transfers)
    decode_overlap = sum(float(item["decode_overlap_ms"]) for item in transfers)
    by_direction: dict[str, dict[str, float | int]] = {}
    for item in transfers:
        direction = str(item["direction"])
        entry = by_direction.setdefault(
            direction,
            {"count": 0, "bytes": 0, "duration_ms": 0.0, "gpu_busy_overlap_ms": 0.0},
        )
        entry["count"] = int(entry["count"]) + 1
        entry["bytes"] = int(entry["bytes"]) + int(item["bytes"])
        entry["duration_ms"] = float(entry["duration_ms"]) + float(item["duration_ms"])
        entry["gpu_busy_overlap_ms"] = float(entry["gpu_busy_overlap_ms"]) + float(
            item["gpu_busy_overlap_ms"]
        )
    return {
        "duration_ms": duration_ms,
        "gpu_busy_threshold_percent": gpu_busy_threshold,
        "gpu_sample_count": len(gpu_samples),
        "gpu_utilization_mean": _mean(float(item["util"]) for item in gpu_samples),
        "gpu_busy_sample_fraction": _mean(
            1.0 if float(item["util"]) >= gpu_busy_threshold else 0.0
            for item in gpu_samples
        ),
        "prefill_observation_count": sum(item["phase"] == "prefill" for item in services),
        "decode_observation_count": sum(item["phase"] == "decode" for item in services),
        "decode_inferred_window_count": len(decode_windows),
        "transfer_count": len(transfers),
        "transfer_duration_ms": transfer_duration,
        "transfer_gpu_busy_overlap_ms": hidden_duration,
        "transfer_decode_overlap_ms": decode_overlap,
        "potentially_hidden_transfer_fraction": (
            hidden_duration / transfer_duration if transfer_duration > 0 else 0.0
        ),
        "decode_overlap_transfer_fraction": (
            decode_overlap / transfer_duration if transfer_duration > 0 else 0.0
        ),
        "transfers_by_direction": by_direction,
        "peak_hbm_ratio": max((float(item["hbm_ratio"]) for item in resources), default=0.0),
        "mean_running": _mean(float(item["running"]) for item in queue_samples),
        "mean_waiting": _mean(float(item["waiting"]) for item in queue_samples),
        "peak_active_tools": max((int(item["active_tools"]) for item in external_waits), default=0),
        "peak_active_joins": max((int(item["active_joins"]) for item in external_waits), default=0),
        "predictive_intent_count": sum(int(item.get("intent", 0)) for item in event_bins),
        "predictive_commit_count": sum(int(item.get("commit", 0)) for item in event_bins),
    }


def _render_html(payload: Mapping[str, object], *, title: str) -> str:
    summary = payload["summary"]
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    hidden = float(summary["potentially_hidden_transfer_fraction"]) * 100.0
    decode_overlap = float(summary["decode_overlap_transfer_fraction"]) * 100.0
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--ink:#171717;--muted:#62686f;--line:#d9dde1;--panel:#f7f8f8;--decode:#2867b2;--prefill:#9b5f16;--d2h:#16877d;--h2d:#c65f2b;--predict:#744fc6;--restore:#a1374b;}}
*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:var(--ink);font:13px/1.45 system-ui,sans-serif;letter-spacing:0}}
header,main{{max-width:1500px;margin:auto;padding:20px 24px}}header{{border-bottom:1px solid var(--line)}}h1{{margin:0;font-size:23px}}h2{{font-size:16px;margin:24px 0 8px}}
.meta,.note{{color:var(--muted)}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));border:1px solid var(--line);margin-top:18px}}
.stat{{padding:10px 12px;border-right:1px solid var(--line)}}.stat:last-child{{border-right:0}}.stat b{{display:block;font-size:17px;margin-top:3px}}
.tools{{display:grid;grid-template-columns:auto minmax(160px,1fr) auto minmax(160px,1fr) auto;gap:10px;align-items:center;margin:14px 0}}
input[type=range]{{width:100%}}canvas{{display:block;width:100%;height:720px;border:1px solid var(--line);background:#fff}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0;color:var(--muted)}}.key{{display:inline-flex;align-items:center;gap:6px}}.swatch{{width:18px;height:4px;background:var(--c)}}
.tooltip{{position:fixed;display:none;pointer-events:none;max-width:390px;padding:8px 10px;background:#171717;color:#fff;border-radius:4px;font-size:12px;z-index:5}}
@media(max-width:760px){{header,main{{padding:14px}}.tools{{grid-template-columns:auto 1fr}}canvas{{height:620px}}}}
</style></head><body><header><h1>{escape(title)}</h1><div class="meta">{escape(str(payload['run_dir']))}</div></header><main>
<section class="stats">
<div class="stat">Elapsed<b>{_duration(float(summary['duration_ms']))}</b></div>
<div class="stat">Mean GPU util<b>{float(summary['gpu_utilization_mean']):.2f}%</b></div>
<div class="stat">Physical DMA<b>{int(summary['transfer_count']):,}</b></div>
<div class="stat">DMA while GPU busy<b>{hidden:.2f}%</b></div>
<div class="stat">DMA in inferred decode<b>{decode_overlap:.2f}%</b></div>
<div class="stat">Peak HBM<b>{float(summary['peak_hbm_ratio'])*100:.2f}%</b></div>
<div class="stat">Predictive commits<b>{int(summary['predictive_commit_count'])}</b></div>
</section>
<p class="note">DMA bars use measured submit-to-complete telemetry. Decode windows are inferred from SGLang's decode log interval, batch size and reported throughput. Prefill is shown as timestamped observations because this trace has no CUDA-event duration.</p>
<div class="tools"><label for="zoom">Zoom</label><input id="zoom" type="range" min="1" max="80" step="1" value="1"><output id="zoomOut">1x</output><input id="pan" type="range" min="0" max="1000" value="0"><output id="windowOut"></output></div>
<div class="legend"><span class="key"><i class="swatch" style="--c:var(--decode)"></i>Decode inferred</span><span class="key"><i class="swatch" style="--c:var(--prefill)"></i>Prefill observed</span><span class="key"><i class="swatch" style="--c:var(--d2h)"></i>D2H</span><span class="key"><i class="swatch" style="--c:var(--h2d)"></i>H2D</span><span class="key"><i class="swatch" style="--c:var(--predict)"></i>Predictive planning</span><span class="key"><i class="swatch" style="--c:var(--restore)"></i>Restore/retraction</span></div>
<canvas id="timeline"></canvas><div id="tooltip" class="tooltip"></div>
<h2>Interpretation</h2><p class="note">Transfer overlap with non-zero GPU utilization is potentially hideable, not proof of zero interference. The exposed remainder and transfers occurring before HBM-blocked work are the primary pipeline targets.</p>
</main><script id="timelineData" type="application/json">{data}</script><script>{_timeline_javascript()}</script></body></html>"""


def _timeline_javascript() -> str:
    return r"""
const data=JSON.parse(document.getElementById('timelineData').textContent);const canvas=document.getElementById('timeline');const ctx=canvas.getContext('2d');
const zoom=document.getElementById('zoom'),pan=document.getElementById('pan'),zoomOut=document.getElementById('zoomOut'),windowOut=document.getElementById('windowOut'),tip=document.getElementById('tooltip');
const lanes=[['GPU utilization',42,96],['GPU service',112,164],['D2H transfer',180,226],['H2D transfer',242,288],['Planner / lifecycle',304,354],['Tool / JOIN waits',370,440],['HBM pressure',456,526],['Running / waiting',542,628]];let hit=[];
function size(){const r=canvas.getBoundingClientRect();const ratio=devicePixelRatio||1;canvas.width=Math.max(800,Math.floor(r.width*ratio));canvas.height=Math.floor(r.height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0)}
function view(){const z=Number(zoom.value),total=data.duration_ms,start=(Number(pan.value)/1000)*Math.max(0,total-total/z);return [start,start+total/z]}
function x(t,start,end,w){return 116+(t-start)/(end-start)*(w-132)}function line(points,field,start,end,w,y0,y1,max,color){ctx.beginPath();let first=true;for(const p of points){if(p.t_ms<start||p.t_ms>end)continue;const px=x(p.t_ms,start,end,w),py=y1-Math.max(0,Math.min(max,Number(p[field]||0)))/max*(y1-y0);if(first){ctx.moveTo(px,py);first=false}else ctx.lineTo(px,py)}ctx.strokeStyle=color;ctx.lineWidth=1.4;ctx.stroke()}
function draw(){size();const w=canvas.getBoundingClientRect().width,[start,end]=view();hit=[];ctx.clearRect(0,0,w,720);ctx.font='12px system-ui';ctx.fillStyle='#62686f';for(const [name,y0,y1] of lanes){ctx.fillText(name,8,(y0+y1)/2+4);ctx.fillStyle='#f7f8f8';ctx.fillRect(116,y0,w-132,y1-y0);ctx.strokeStyle='#e2e5e8';ctx.strokeRect(116,y0,w-132,y1-y0);ctx.fillStyle='#62686f'}
for(let i=0;i<=8;i++){const px=116+i*(w-132)/8;ctx.strokeStyle='#eceeef';ctx.beginPath();ctx.moveTo(px,42);ctx.lineTo(px,628);ctx.stroke();ctx.fillStyle='#62686f';ctx.fillText(fmt(start+(end-start)*i/8),px-18,650)}
line(data.gpu_samples,'util',start,end,w,42,96,100,'#26734d');
ctx.fillStyle='#2867b2';for(const q of data.decode_windows){if(q.end_ms<start||q.start_ms>end)continue;const a=x(Math.max(start,q.start_ms),start,end,w),b=x(Math.min(end,q.end_ms),start,end,w);ctx.globalAlpha=.72;ctx.fillRect(a,119,Math.max(1,b-a),38);ctx.globalAlpha=1;hit.push([a,119,Math.max(3,b-a),38,`Decode ${q.running} req | ${q.throughput.toFixed(1)} tok/s | ${q.cuda_graph?'CUDA graph':'eager'}`])}
ctx.fillStyle='#9b5f16';for(const p of data.service_observations){if(p.phase!=='prefill'||p.t_ms<start||p.t_ms>end)continue;const px=x(p.t_ms,start,end,w),h=Math.min(38,5+Math.log2(1+p.new_tokens)*2);ctx.fillRect(px-1,157-h,2,h);hit.push([px-3,119,6,38,`Prefill ${p.new_tokens} new + ${p.cached_tokens} cached tokens | ${p.new_sequences} seq`])}
for(const t of data.transfers){if(t.end_ms<start||t.start_ms>end)continue;const y=t.direction==='d2h'?190:252,a=x(Math.max(start,t.start_ms),start,end,w),b=x(Math.min(end,t.end_ms),start,end,w);ctx.fillStyle=t.direction==='d2h'?'#16877d':'#c65f2b';ctx.globalAlpha=t.predictive?1:.68;ctx.fillRect(a,y,Math.max(1,b-a),26);ctx.globalAlpha=1;hit.push([a,y,Math.max(3,b-a),26,`${t.direction.toUpperCase()} ${bytes(t.bytes)} | ${t.duration_ms.toFixed(2)} ms | GPU-busy overlap ${(t.potentially_hidden_fraction*100).toFixed(1)}% | ${t.kind}`])}
for(const b of data.event_bins){if(b.t_ms<start||b.t_ms>end)continue;const px=x(b.t_ms,start,end,w);if(b.risk||b.intent||b.commit||b.reject){ctx.fillStyle='#744fc6';ctx.fillRect(px,309,Math.max(1,Math.log2(2+(b.risk||0))*2),17)}if(b.restore||b.retraction){ctx.fillStyle='#a1374b';ctx.fillRect(px,333,Math.max(1,Math.log2(2+(b.restore||0)+(b.retraction||0))*2),15)}}
line(data.external_waits,'active_tools',start,end,w,370,440,Math.max(1,data.summary.peak_active_tools),'#9b5f16');line(data.external_waits,'active_joins',start,end,w,370,440,Math.max(1,data.summary.peak_active_joins),'#744fc6');
line(data.resources,'hbm_ratio',start,end,w,456,526,1,'#a1374b');line(data.queue_samples,'running',start,end,w,542,628,32,'#2867b2');line(data.queue_samples,'waiting',start,end,w,542,628,Math.max(32,data.summary.mean_waiting*2),'#744fc6');
zoomOut.value=`${zoom.value}x`;windowOut.value=`${fmt(start)} - ${fmt(end)}`}
function fmt(ms){const s=Math.max(0,ms/1000),h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return `${h}h${String(m).padStart(2,'0')}m`}function bytes(v){const u=['B','KiB','MiB','GiB'];let n=v,i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return `${n.toFixed(i?1:0)} ${u[i]}`}
zoom.addEventListener('input',draw);pan.addEventListener('input',draw);window.addEventListener('resize',draw);canvas.addEventListener('mousemove',e=>{const r=canvas.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,found=hit.find(h=>mx>=h[0]&&mx<=h[0]+h[2]&&my>=h[1]&&my<=h[1]+h[3]);if(!found){tip.style.display='none';return}tip.textContent=found[4];tip.style.display='block';tip.style.left=`${e.clientX+12}px`;tip.style.top=`${e.clientY+12}px`});canvas.addEventListener('mouseleave',()=>tip.style.display='none');draw();
"""


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                continue
            yield value


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _ratio(numerator: object, denominator: object) -> float:
    num = _finite_float(numerator)
    den = _finite_float(denominator)
    return num / den if num is not None and den else 0.0


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _downsample(
    values: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    if len(values) <= limit:
        return values
    step = math.ceil(len(values) / limit)
    result = values[::step]
    if result[-1] is not values[-1]:
        result.append(values[-1])
    return result


def _duration(ms: float) -> str:
    seconds = max(0, int(ms / 1000.0))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
