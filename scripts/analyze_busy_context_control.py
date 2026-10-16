#!/usr/bin/env python3
"""Compare visible decode geometry and runtime control cost in a matched window."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from statistics import mean, median
from zoneinfo import ZoneInfo


_LOG_TIMESTAMP = re.compile(
    rb"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
)
_DECODE_BATCH = re.compile(rb"Decode batch(?: \[\d+\])?[,\.].*?#running-req:\s*(\d+)")
_GRAPH_FLAG = re.compile(rb"cuda graph:\s*(True|False)")
_DECODE_LOG_INTERVAL = re.compile(rb"decode_log_interval=(\d+)")


class WriterTimingWindow:
    """Difference processing samples on the writer clock, not producer timestamps."""

    def __init__(self, begin: float, end: float) -> None:
        self.begin, self.end = begin, end
        self.boundaries: list[tuple[dict, bytes] | None] = [None, None]
        self.collected = False
        self.covered = False

    def observe(self, timing: dict | None, line: bytes) -> None:
        if timing is None:
            return
        self.collected = True
        timestamp = timing["snapshot_ts_ms"]
        if timestamp > self.end:
            self.covered = True
            return
        if timestamp <= self.begin:
            self.boundaries[0] = timing, line
        self.boundaries[1] = timing, line

    def summary(self) -> dict:
        if not self.collected:
            return {"available": False, "reason": "writer_timing_not_collected"}
        if not self.covered or any(boundary is None for boundary in self.boundaries):
            return {"available": False, "reason": "writer_window_not_fully_observed"}
        lower, upper = (boundary[0] for boundary in self.boundaries)
        elapsed = upper["snapshot_ts_ms"] - lower["snapshot_ts_ms"]
        if elapsed <= 0:
            return {"available": False, "reason": "no_writer_snapshot_inside_window"}
        for field in ("schema_version", "sample_every", "cpu_clock", "wall_clock"):
            if lower[field] != upper[field]:
                raise ValueError(f"writer timing changed within the window: {field}")
        categories = {}
        for name in lower["categories"].keys() | upper["categories"].keys():
            before = lower["categories"].get(name, {})
            after = upper["categories"].get(name, {})
            delta = {
                metric: after.get(metric, 0) - before.get(metric, 0)
                for metric in (
                    "records_seen", "samples", "sampled_cpu_ms", "sampled_wall_ms",
                )
            }
            if any(value < 0 for value in delta.values()):
                raise ValueError(f"writer timing counters reset within the window: {name}")
            samples = delta["samples"]
            categories[name] = {
                **delta,
                "mean_sample_cpu_ms": delta["sampled_cpu_ms"] / samples if samples else None,
                "mean_sample_wall_ms": delta["sampled_wall_ms"] / samples if samples else None,
            }
        return {
            "available": True,
            "sample_every": upper["sample_every"],
            "cpu_clock": upper["cpu_clock"], "wall_clock": upper["wall_clock"],
            "boundary_snapshot_ts_ms": [
                boundary[0]["snapshot_ts_ms"] for boundary in self.boundaries
            ],
            "raw_boundary_line_sha256": [
                hashlib.sha256(boundary[1]).hexdigest() for boundary in self.boundaries
            ],
            "observed_duration_ms": elapsed,
            "begin_snapshot_lag_ms": self.begin - lower["snapshot_ts_ms"],
            "end_snapshot_lag_ms": self.end - upper["snapshot_ts_ms"],
            "sampled_cpu_ms": sum(values["sampled_cpu_ms"] for values in categories.values()),
            "sampled_wall_ms": sum(values["sampled_wall_ms"] for values in categories.values()),
            "categories": dict(sorted(
                categories.items(), key=lambda item: (-item[1]["sampled_cpu_ms"], item[0]),
            )),
            "semantics": (
                "Measured processing samples completed between writer snapshots. "
                "Producer timestamps, queue waiting and status publication are excluded. "
                "Samples may span a boundary; cumulative maxima are not window maxima. "
                "No multiplication by the sampling interval, full-window cost, "
                "GIL attribution, GPU stall or JCT saving is inferred."
            ),
        }


def monitor_window(arm: Path, start: float, end: float, timezone: ZoneInfo) -> dict:
    paths = list(arm.glob("client_*/gpu_samples.csv"))
    if len(paths) != 1:
        raise ValueError(f"expected one GPU monitor in {arm}")
    path = paths[0]
    with path.open() as stream:
        rows = csv.DictReader(stream)
        first = next(rows)

        def timestamp(row):
            return datetime.strptime(
                row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f",
            ).replace(tzinfo=timezone).timestamp() * 1000.

        origin = timestamp(first)
        coverage = origin
        for row in rows:
            coverage = timestamp(row)
            if coverage >= origin + end * 1000.:
                break
    if coverage < origin + end * 1000.:
        raise ValueError(f"GPU monitor has not covered the requested window: {path}")
    return {
        "source": str(path), "first_row": first, "origin_ts_ms": origin,
        "begin_ts_ms": origin + start * 1000., "end_ts_ms": origin + end * 1000.,
        "observed_through_ts_ms": coverage, "timezone": str(timezone),
    }


def periodic_decode_graph_log(
    arm: Path, begin: float, end: float, timezone: ZoneInfo,
) -> dict:
    path = arm / "server.log"
    if not path.exists():
        return {"available": False, "reason": "server_log_not_collected", "source": str(path)}
    groups = defaultdict(lambda: {"true": 0, "false": 0, "unknown": 0})
    boundaries = {"begin": 0, "end": 0}
    prefix, bytes_read = hashlib.sha256(), 0
    observed_through = None
    interval = None
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            interval_match = _DECODE_LOG_INTERVAL.search(line)
            if interval_match:
                interval = int(interval_match[1])
            if b"] server_args=" in line:
                server_args = ast.literal_eval(line.split(b"] server_args=", 1)[1].decode("utf-8"))
                interval = server_args.get("decode_log_interval", interval)
            timestamp_match = _LOG_TIMESTAMP.match(line)
            if timestamp_match is None:
                continue
            timestamp = datetime.strptime(
                timestamp_match[1].decode("ascii"), "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=timezone).timestamp() * 1000.
            observed_through = (
                timestamp if observed_through is None else max(timestamp, observed_through)
            )
            if timestamp >= end:
                break
            batch_match = _DECODE_BATCH.search(line)
            if batch_match is None or timestamp + 1000. <= begin:
                continue
            # Second-resolution logs that straddle a window boundary stay separate.
            if timestamp < begin:
                boundaries["begin"] += 1
                continue
            if timestamp + 1000. > end:
                boundaries["end"] += 1
                continue
            graph_match = _GRAPH_FLAG.search(line)
            flag = graph_match[1].decode("ascii").lower() if graph_match else "unknown"
            groups[int(batch_match[1])][flag] += 1
    return {
        "available": True, "source": str(path),
        "read_prefix_bytes": bytes_read, "read_prefix_sha256": prefix.hexdigest(),
        "timezone": str(timezone), "timestamp_resolution_ms": 1000,
        "begin_ts_ms": begin, "end_ts_ms": end,
        "observed_through_ts_ms": observed_through,
        "window_fully_observed": observed_through is not None and observed_through >= end,
        "decode_log_interval_observed": interval,
        "samples": sum(sum(values.values()) for values in groups.values()),
        "batch_sizes": {str(size): values for size, values in sorted(groups.items())},
        "boundary_samples_excluded": boundaries,
        "flag_source": (
            "GenerationBatchResult.can_run_cuda_graph -> process_batch_result_decode "
            "-> metrics_reporter.report_decode_stats for that completed batch."
        ),
        "semantics": (
            "Periodic decode log samples, not all batches or kernel timings. "
            "Second-resolution timestamps represent a one-second bucket; samples "
            "straddling the requested half-open window are excluded. "
            "No flag is backfilled into runtime_audit batches or extrapolated "
            "to unsampled batches. Missing logs and observed zero samples differ."
        ),
    }


def host_match_path_geometry(arm: Path, begin: float, end: float) -> dict:
    path = arm / "server/runtime_events.sglang.jsonl"
    counts, lengths = Counter(), Counter()
    prefix, bytes_read = hashlib.sha256(), 0
    if not path.exists():
        return {"available": False, "reason": "native_events_not_collected"}
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"native_host_hit_match_path"' not in line:
                continue
            observed = json.loads(line)["attributes"]["native_host_hit_match_path"]
            timestamp = observed["observed_ts_ms"]
            if timestamp >= end:
                break
            if timestamp < begin:
                continue
            values = [
                observed[name] for name in (
                    "last_device_path", "last_host_path", "best_match_path",
                )
            ]
            valid = [value for value in values if value is not None]
            unique = {tuple(map(tuple, value)) for value in valid}
            counts["host_hit_records"] += 1
            counts["valid_paths"] += len(valid)
            counts["duplicate_valid_paths"] += len(valid) - len(unique)
            counts["records_with_three_identical_valid_paths"] += int(
                len(valid) == 3 and len(unique) == 1
            )
            counts["recorded_path_nodes"] += sum(len(value) for value in valid)
            counts["unique_path_nodes_by_identical_anchor"] += sum(
                len(value) for value in unique
            )
            lengths.update(len(value) for value in valid)
    return {
        "available": True, "source": str(path),
        "read_prefix_bytes": bytes_read, "read_prefix_sha256": prefix.hexdigest(),
        "begin_ts_ms": begin, "end_ts_ms": end,
        "counts": dict(counts),
        "path_length_histogram": dict(sorted(lengths.items())),
        "semantics": (
            "Host-hit identity paths recorded at first service, selected by their "
            "observation timestamps, not earlier request submission timestamps. "
            "Identical recorded paths expose repeat observation; no FULL/Mamba "
            "reuse, CPU time or native-relative latency saving is inferred."
        ),
    }


def worker_interval_neighbors(markers: list[dict]) -> dict:
    selected = [row for row in markers if row["in_window"]]
    valid = [
        row for row in markers
        if type(row["start_ms"]) in (int, float)
        and type(row["complete_ms"]) in (int, float)
    ]
    domains = {row["clock_domain"] for row in valid if row["clock_domain"] is not None}
    if len(domains) > 1:
        return {"available": False, "reason": "mixed_monotonic_clock_domains"}
    ordered = sorted(valid, key=lambda row: row["complete_ms"])
    raw_launch_rows = [
        row for row in valid
        if row["in_window"] and type(row.get("launch_ms")) in (int, float)
    ]
    cohorts = defaultdict(list)
    examples = []
    paired = 0
    for current, following in zip(ordered, ordered[1:]):
        if not current["in_window"]:
            continue
        boundary_gap = following["start_ms"] - current["complete_ms"]
        elapsed = current["complete_ms"] - current["start_ms"]
        key = (
            current["phase"], current["batch_size"],
            following["phase"], following["batch_size"],
            current["geometry_cohort"],
        )
        cohorts[key].append((elapsed, boundary_gap))
        paired += 1
        if current["phase"] == "prefill" and len(examples) < 3:
            examples.append({
                "sample_id": current["sample_id"],
                "next_sample_id": following["sample_id"],
                "service_start_monotonic_ms": current["start_ms"],
                "next_service_start_monotonic_ms": following["start_ms"],
                "complete_monotonic_ms": current["complete_ms"],
                "interval_ms": elapsed,
                "next_interval_boundary_gap_ms": boundary_gap,
            })
    return {
        "available": bool(cohorts),
        "window_samples": len(selected),
        "missing_monotonic_window_samples": sum(
            type(row["start_ms"]) not in (int, float)
            or type(row["complete_ms"]) not in (int, float)
            for row in selected
        ),
        "paired_window_samples": paired,
        "cohorts": {
            f"{phase}/{size}/{next_phase}/{next_size}/{geometry}": {
                "batches": len(values),
                "mean_ms": mean(row[0] for row in values),
                "median_ms": median(row[0] for row in values),
                "next_interval_boundary_gap_mean_ms": mean(row[1] for row in values),
                "contiguous_interval_count": sum(row[1] == 0 for row in values),
            }
            for (phase, size, next_phase, next_size, geometry), values in sorted(cohorts.items())
        },
        "examples": examples,
        "raw_launch_timing_available": bool(raw_launch_rows),
        "raw_launch_window_samples": len(raw_launch_rows),
        "missing_raw_launch_window_samples": len(selected) - len(raw_launch_rows),
        "launch_to_processed_completion_mean_ms": (
            mean(row["complete_ms"] - row["launch_ms"] for row in raw_launch_rows)
            if raw_launch_rows else None
        ),
        "clipped_start_minus_launch_mean_ms": (
            mean(row["start_ms"] - row["launch_ms"] for row in raw_launch_rows)
            if raw_launch_rows else None
        ),
        "launch_timestamp_semantics": (
            "scheduler.run_batch entry before telemetry and worker dispatch, "
            "not CUDA submission or kernel start. Only explicitly recorded raw "
            "timestamps are used; legacy service starts are not substituted."
        ),
        "start_boundary": "max(batch.launch_ts, previous_processed_completion)",
        "semantics": (
            "Consecutive observed worker intervals in processed-completion order. "
            "The exported service start is clipped to the previous processed completion, "
            "so it is not a raw batch launch. A zero neighboring boundary gap follows "
            "from that clipping; it does not prove zero result-processing cost. "
            "These fields cannot split kernel, scheduling or overlap time. Cohorts "
            "match current native work/context geometry and the following observed "
            "phase/batch size, not identical work. Missing geometry or timestamps "
            "remain unknown; unobserved native batches may exist."
        ),
    }


def decode_geometry(arm: Path, begin: float, end: float) -> dict:
    path = arm / "server/runtime_audit.jsonl"
    contexts, sums, durations, batches = [], [], [], []
    groups = defaultdict(list)
    graph_groups = defaultdict(list)
    prefill_durations, prefill_work = [], []
    prefill_sources = Counter()
    prefill_groups = defaultdict(list)
    interval_markers = []
    prefix, bytes_read = hashlib.sha256(), 0
    covered = False
    writer = WriterTimingWindow(begin, end)
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"background_writer_timing"' in line:
                writer.observe(json.loads(line)["timing"], line)
                if covered and writer.covered:
                    break
                continue
            if b'"gpu_service_sample"' not in line:
                continue
            row = json.loads(line)
            if row["complete_ts_ms"] >= begin and row.get("phase") in ("prefill", "decode"):
                interval_markers.append({
                    "sample_id": row.get("sample_id"),
                    "phase": row["phase"],
                    "batch_size": row["batch_size"],
                    "launch_ms": row.get("launch_monotonic_ms"),
                    "start_ms": row.get("service_start_monotonic_ms"),
                    "complete_ms": row.get("complete_monotonic_ms"),
                    "clock_domain": row.get("monotonic_clock_domain"),
                    "in_window": row["complete_ts_ms"] < end,
                    "geometry_cohort": "unknown",
                })
            if row["complete_ts_ms"] >= end:
                covered = True
                if not writer.collected or writer.covered:
                    break
                continue
            if row["complete_ts_ms"] < begin:
                continue
            if row["phase"] == "prefill":
                duration = row["service_elapsed_ms"]
                prefill_durations.append(duration)
                samples = row["request_samples"]
                prefill_sources.update(
                    item.get("prefill_geometry_source", "unverified_legacy")
                    for item in samples
                )
                native = [
                    item for item in samples
                    if item.get("prefill_geometry_source") in (
                        "native_batch", "native_request_range",
                    )
                ]
                if len(native) == row["batch_size"] and native:
                    work = sum(item["token_delta"] for item in native)
                    context = mean(item["sequence_tokens_before"] for item in native)
                    prefill_work.append(work)
                    interval_markers[-1]["geometry_cohort"] = (
                        f"{work // 512 * 512}/{int(context) // 2048 * 2048}"
                    )
                    prefill_groups[
                        row["batch_size"], work // 512, int(context) // 2048,
                    ].append(duration)
                continue
            if row["phase"] != "decode":
                continue
            observed = [
                item["sequence_tokens_before"] for item in row["request_samples"]
                if type(item.get("sequence_tokens_before")) is int
            ]
            if len(observed) != row["batch_size"]:
                raise ValueError("incomplete visible decode geometry")
            duration = row["service_elapsed_ms"]
            contexts.extend(observed)
            sums.append(sum(observed))
            durations.append(duration)
            average = mean(observed)
            groups[(row["batch_size"], int(average) // 2048)].append(duration)
            replayed = row.get("cuda_graph_replayed")
            replay_label = "unknown" if replayed is None else str(replayed).lower()
            interval_markers[-1]["geometry_cohort"] = (
                f"{int(average) // 2048 * 2048}/{replay_label}"
            )
            graph_groups[(row["batch_size"], int(average) // 2048, replay_label)].append(duration)
            batches.append((average, duration))
    if not covered or not batches:
        raise ValueError(f"runtime samples have not covered the requested window: {path}")
    return {
        "source": str(path), "read_prefix_bytes": bytes_read,
        "read_prefix_sha256": prefix.hexdigest(),
        "decode_batches": len(batches), "observed_request_steps": len(contexts),
        "sequence_tokens_per_request_mean": mean(contexts),
        "sequence_tokens_per_request_median": median(contexts),
        "sequence_tokens_per_batch_sum_mean": mean(sums),
        "worker_decode_duration_mean_ms": mean(durations),
        "worker_decode_duration_median_ms": median(durations),
        "worker_interval_neighbors": worker_interval_neighbors(interval_markers),
        "background_writer_timing": writer.summary(),
        "time_weighted_mean_request_sequence_tokens": (
            sum(average * duration for average, duration in batches) / sum(durations)
        ),
        "cohorts": {
            f"{size}/{bucket * 2048}": {
                "batches": len(values), "mean_ms": mean(values), "median_ms": median(values),
            } for (size, bucket), values in sorted(groups.items())
        },
        "cuda_graph_cohorts": {
            f"{size}/{bucket * 2048}/{replay}": {
                "batches": len(values), "mean_ms": mean(values), "median_ms": median(values),
            } for (size, bucket, replay), values in sorted(graph_groups.items())
        },
        "cuda_graph_semantics": (
            "Engine completion result can_run_cuda_graph, paired with this batch. "
            "Absent legacy flags remain unknown; batch size alone does not prove replay. "
            "Worker intervals include scheduler/result processing and are not kernel timings."
        ),
        "prefill_geometry": {
            "batches": len(prefill_durations),
            "worker_interval_ms": sum(prefill_durations),
            "known_work_batches": len(prefill_work),
            "unknown_work_batches": len(prefill_durations) - len(prefill_work),
            "known_extend_tokens": sum(prefill_work) if prefill_work else None,
            "request_geometry_sources": dict(sorted(prefill_sources.items())),
            "cohorts": {
                f"{size}/{work * 512}/{context * 2048}": {
                    "batches": len(values), "mean_ms": mean(values),
                    "median_ms": median(values),
                }
                for (size, work, context), values in sorted(prefill_groups.items())
            },
            "semantics": (
                "Completed worker intervals, not CUDA kernel timings. Cohorts require "
                "native prepared prefix/extend lengths for every batch row; they match "
                "batch size, 512-token total-work bins and 2048-token mean-prefix bins. "
                "Legacy missing fields or zero defaults do not establish zero prefill "
                "work. Partial attributed batches retain unknown total work."
            ),
        },
    }


def control_profile(arm: Path, begin: float, end: float) -> dict:
    path = arm / "opportunities/admission_opportunities.jsonl"
    boundary, selected_lines = [None, None], [None, None]
    prefix, bytes_read = hashlib.sha256(), 0
    covered = False
    writer = WriterTimingWindow(begin, end)
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"admission_runtime_state"' not in line:
                continue
            state = json.loads(line)
            writer.observe(state.get("opportunity_writer_timing"), line)
            if state["ts_ms"] > end:
                covered = True
                if not writer.collected or writer.covered:
                    break
                continue
            index = 0 if state["ts_ms"] <= begin else 1
            boundary[index], selected_lines[index] = state, line
    if not covered or any(state is None for state in boundary):
        raise ValueError(f"control profile has not covered the requested window: {path}")
    phases = {}
    for phase, upper in boundary[1]["shared_path_timing"]["phases"].items():
        lower = boundary[0]["shared_path_timing"]["phases"].get(phase, {})
        phases[phase] = {
            metric: upper[metric] - lower.get(metric, 0)
            for metric in ("count", "self_ms", "total_ms")
        }
    return {
        "source": str(path), "read_prefix_bytes": bytes_read,
        "read_prefix_sha256": prefix.hexdigest(),
        "boundary_state_ts_ms": [state["ts_ms"] for state in boundary],
        "raw_boundary_line_sha256": [hashlib.sha256(line).hexdigest() for line in selected_lines],
        "observed_duration_ms": boundary[1]["ts_ms"] - boundary[0]["ts_ms"],
        "summed_python_self_ms": sum(item["self_ms"] for item in phases.values()),
        "background_writer_timing": writer.summary(),
        "phases": dict(sorted(phases.items(), key=lambda item: item[1]["self_ms"], reverse=True)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--native-run-root", type=Path,
        help="Use a completed historical native arm for cross-version development diagnosis.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, default=600.)
    parser.add_argument("--end-seconds", type=float, default=900.)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument(
        "--predictive-timezone",
        help="Override the predictive GPU/server-log wall timezone.",
    )
    parser.add_argument(
        "--native-timezone",
        help="Override the native GPU/server-log wall timezone.",
    )
    args = parser.parse_args()
    if not 0 <= args.start_seconds < args.end_seconds:
        parser.error("window must satisfy 0 <= start < end")
    plan = json.loads((args.run_root / "ab_plan.json").read_text())
    native_root = args.native_run_root or args.run_root
    native_plan = (
        plan if native_root == args.run_root
        else json.loads((native_root / "ab_plan.json").read_text())
    )
    same_version = (
        plan["code_commit"] == native_plan["code_commit"]
        and plan["sglang_patch_sha256"] == native_plan["sglang_patch_sha256"]
    )
    timezones = {
        "predictive_h2d": ZoneInfo(args.predictive_timezone or args.timezone),
        "native": ZoneInfo(args.native_timezone or args.timezone),
    }
    report = {
        "scope": (
            "Complete observed window; not identical-demand causality or completed-pair JCT"
            if same_version else
            "Cross-version complete observed window; development diagnosis, not causal speedup"
        ),
        "code_commit": plan["code_commit"], "patch_sha256": plan["sglang_patch_sha256"],
        "native_code_commit": native_plan["code_commit"],
        "native_patch_sha256": native_plan["sglang_patch_sha256"],
        "same_version": same_version,
        "native_run_root": str(native_root),
        "arm_wall_timezones": {name: str(zone) for name, zone in timezones.items()},
        "frozen_plan_changed_keys": [
            key for key in sorted(plan.keys() | native_plan.keys())
            if plan.get(key) != native_plan.get(key)
        ],
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds, "arms": {},
        "semantics": [
            "Context is input plus generated tokens visible before each decode batch.",
            "Only batches completed in the half-open window are counted; intervals are not clipped.",
            "Cohorts match batch size and a 2048-token bin of batch-mean context, not exact demand.",
            "Worker intervals include scheduler processing and are not CUDA kernel timings.",
            "Control counters use preceding snapshots; exclusive Python wall time is not removable JCT.",
            "Writer CPU/wall differences use their own snapshot clocks and measured samples only.",
            "Periodic graph logs are separate samples; legacy per-batch graph flags stay unknown.",
            "Prefill work cohorts require native prefix/extend lengths; old request-field zero defaults remain unknown.",
        ],
    }
    for name in ("predictive_h2d", "native"):
        arm = (args.run_root if name == "predictive_h2d" else native_root) / name
        window = monitor_window(
            arm, args.start_seconds, args.end_seconds, timezones[name],
        )
        values = decode_geometry(arm, window["begin_ts_ms"], window["end_ts_ms"])
        values["monitor_window"] = window
        values["host_match_path_geometry"] = host_match_path_geometry(
            arm, window["begin_ts_ms"], window["end_ts_ms"],
        )
        values["periodic_decode_graph_log"] = periodic_decode_graph_log(
            arm, window["begin_ts_ms"], window["end_ts_ms"], timezones[name],
        )
        if name == "predictive_h2d":
            values["control_profile"] = control_profile(
                arm, window["begin_ts_ms"], window["end_ts_ms"],
            )
        report["arms"][name] = values
    predictive, native = (
        report["arms"][name] for name in ("predictive_h2d", "native")
    )
    for name, left, right in (
        ("matched_cohorts", predictive["cohorts"], native["cohorts"]),
        (
            "matched_cuda_graph_cohorts",
            predictive["cuda_graph_cohorts"], native["cuda_graph_cohorts"],
        ),
        (
            "matched_prefill_cohorts",
            predictive["prefill_geometry"]["cohorts"],
            native["prefill_geometry"]["cohorts"],
        ),
        (
            "matched_worker_neighbor_cohorts",
            predictive["worker_interval_neighbors"].get("cohorts", {}),
            native["worker_interval_neighbors"].get("cohorts", {}),
        ),
    ):
        report[name] = {
            key: {
                "predictive": left[key], "native": right[key],
                "predictive_mean_ratio": left[key]["mean_ms"] / right[key]["mean_ms"],
            } for key in sorted(left.keys() & right.keys())
            if left[key]["batches"] >= 32 and right[key]["batches"] >= 32
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {
            "decode_batches": values["decode_batches"],
            "mean_visible_context": values["sequence_tokens_per_request_mean"],
            "mean_worker_interval_ms": values["worker_decode_duration_mean_ms"],
        } for name, values in report["arms"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
