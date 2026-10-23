#!/usr/bin/env python3
"""Pair sampled prefill phases with native service evidence in sealed prefixes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median


PHASES = (
    "selection", "run_batch", "launch_telemetry", "war_barrier_and_queue_publish",
    "post_launch_physical", "result_processing",
)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean_ms": mean(ordered) if ordered else None,
        "p50_ms": median(ordered) if ordered else None,
        "p90_ms": ordered[math.ceil(.9 * len(ordered)) - 1] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
        "sum_ms": sum(ordered) if ordered else None,
    }


def key(row):
    domain, forward = row.get("monotonic_clock_domain"), row.get("forward_iter")
    return (domain, forward) if (
        isinstance(domain, str) and domain and type(forward) is int and forward > 0
    ) else None


def request_ids(row, *, service=False):
    values = (
        [item.get("request_id") for item in row.get("request_samples", [])]
        if service else row.get("request_ids", [])
    )
    if (
        not values or not all(isinstance(value, str) and value for value in values)
        or len(set(values)) != len(values) or len(values) != row.get("batch_size")
    ):
        return None
    return tuple(values)


def validate_phase(phase):
    fields = (
        "start_monotonic_ms", "end_monotonic_ms", "wall_ms", "thread_cpu_ms",
    )
    if not isinstance(phase, dict) or not all(number(phase.get(name)) for name in fields):
        return False
    start, end, wall, cpu = (phase[name] for name in fields)
    return end >= start and wall >= 0 and cpu >= 0 and math.isclose(
        end - start, wall, rel_tol=1e-5, abs_tol=1e-4,
    )


def summarize(samples):
    metrics = {
        name: distribution([row["metrics"][name] for row in samples if name in row["metrics"]])
        for name in (
            "gpu_run_batch_span_ms", "launch_to_processed_completion_ms",
            "run_start_to_result_end_ms", "selection_end_to_run_start_ms",
            "run_return_to_result_start_ms", "gpu_layer_dependency_wait_ms",
        )
    }
    return {
        "sample_count": len(samples),
        "phase_coverage": {
            phase: sum(phase in row["phases"] for row in samples) for phase in PHASES
        },
        "phases": {
            phase: {
                metric: distribution([
                    row["phases"][phase][metric] for row in samples if phase in row["phases"]
                ])
                for metric in ("wall_ms", "thread_cpu_ms", "wall_minus_thread_cpu_ms")
            }
            for phase in PHASES
        },
        "metrics": metrics,
        "gpu_span_status": dict(Counter(row["gpu_span_status"] for row in samples)),
        "restore_wait_status": dict(Counter(row["restore_wait_status"] for row in samples)),
    }


def analyze_samples(probes, services, waits, *, begin, end):
    indexed = defaultdict(list)
    for row in services:
        if row.get("phase") == "prefill" and key(row) is not None:
            indexed[key(row)].append(row)
    probe_keys = Counter(key(row) for row in probes if key(row) is not None)
    wait_index = defaultdict(list)
    for row in waits:
        if type(row.get("forward_iter")) is int:
            wait_index[row["forward_iter"]].append(row)
    samples, unknown = [], Counter()
    for probe in probes:
        identity = key(probe)
        if identity is None:
            unknown["missing_clock_domain_or_forward_iter"] += 1
            continue
        if probe_keys[identity] != 1:
            unknown["ambiguous_probe_identity"] += 1
            continue
        candidates = indexed.get(identity, [])
        if len(candidates) != 1:
            unknown["missing_service" if not candidates else "ambiguous_service"] += 1
            continue
        service = candidates[0]
        if not number(service.get("complete_ts_ms")):
            unknown["missing_window_timestamp"] += 1
            continue
        if not begin <= service["complete_ts_ms"] < end:
            continue
        ids = request_ids(probe)
        if ids is None or ids != request_ids(service, service=True):
            unknown["request_identity_disagreement"] += 1
            continue
        if probe.get("result_processed") is not True:
            unknown["result_not_processed"] += 1
            continue
        native = service["request_samples"]
        if not all(
            item.get("prefill_geometry_source") in ("native_batch", "native_request_range")
            and type(item.get("token_delta")) is int and item["token_delta"] >= 0
            and type(item.get("sequence_tokens_before")) is int
            and item["sequence_tokens_before"] >= 0
            for item in native
        ):
            unknown["missing_native_geometry"] += 1
            continue
        extend = sum(item["token_delta"] for item in native)
        prefix = sum(item["sequence_tokens_before"] for item in native)
        if probe.get("extend_tokens") != extend or probe.get("prefix_tokens") != prefix:
            unknown["native_geometry_disagreement"] += 1
            continue
        phases = {}
        for name, phase in probe.get("phases", {}).items():
            if name not in PHASES:
                continue
            if not validate_phase(phase):
                unknown[f"invalid_phase/{name}"] += 1
                continue
            phases[name] = {
                **phase,
                "wall_minus_thread_cpu_ms": phase["wall_ms"] - phase["thread_cpu_ms"],
            }
        metrics = {}
        status, span = probe.get("gpu_span_status", "unknown"), probe.get("gpu_run_batch_span_ms")
        if status == "observed" and number(span) and span >= 0:
            metrics["gpu_run_batch_span_ms"] = span
        elif status == "observed":
            status = "invalid"
        launch, complete = (
            service.get("launch_monotonic_ms"), service.get("complete_monotonic_ms"),
        )
        if number(launch) and number(complete) and complete >= launch:
            if probe.get("launch_monotonic_ms") == launch:
                metrics["launch_to_processed_completion_ms"] = complete - launch
            else:
                unknown["launch_timestamp_disagreement"] += 1
        selection, run, result = (phases.get(name) for name in (
            "selection", "run_batch", "result_processing",
        ))
        for name, left, right in (
            ("selection_end_to_run_start_ms", selection, run),
            ("run_return_to_result_start_ms", run, result),
        ):
            if left is not None and right is not None:
                gap = right["start_monotonic_ms"] - left["end_monotonic_ms"]
                if gap >= 0:
                    metrics[name] = gap
                else:
                    unknown[f"invalid_chronology/{name}"] += 1
        if run is not None and result is not None:
            elapsed = result["end_monotonic_ms"] - run["start_monotonic_ms"]
            if elapsed >= 0:
                metrics["run_start_to_result_end_ms"] = elapsed
        matching_waits = [
            wait for wait in wait_index[identity[1]]
            if request_ids(wait) == ids
        ]
        wait_status = "not_observed"
        if len(matching_waits) > 1:
            wait_status = "ambiguous"
        elif matching_waits:
            wait = matching_waits[0]
            duration = wait.get("gpu_layer_dependency_wait_ms")
            wait_wall = wait.get("ts_ms")
            launch_wall = (
                service["complete_ts_ms"] - (complete - launch)
                if number(complete) and number(launch) else None
            )
            if (
                number(duration) and duration >= 0 and number(wait_wall)
                and number(launch_wall) and launch_wall <= wait_wall <= service["complete_ts_ms"]
            ):
                metrics["gpu_layer_dependency_wait_ms"] = duration
                wait_status = "observed"
            else:
                wait_status = "invalid_or_outside_service_interval"
        graph = service.get("cuda_graph_replayed")
        samples.append({
            "forward_iter": identity[1], "monotonic_clock_domain": identity[0],
            "request_ids": ids, "service_sample_id": service.get("sample_id"),
            "batch_size": service["batch_size"], "extend_tokens": extend,
            "prefix_tokens": prefix, "loop": probe.get("loop", "unknown"),
            "graph": str(graph).lower() if type(graph) is bool else "unknown",
            "observation_event": probe["event"],
            "sampling_interval": probe.get("sampling_interval"),
            "gpu_span_status": status, "restore_wait_status": wait_status,
            "phases": phases, "metrics": metrics,
        })
    short = [row for row in samples if row["batch_size"] == 1 and row["extend_tokens"] < 512]
    groups = defaultdict(list)
    for row in short:
        group = (
            f"extend/{row['extend_tokens'] // 16 * 16}"
            f"/prefix/{row['prefix_tokens'] // 2048 * 2048}"
            f"/loop/{row['loop']}/graph/{row['graph']}"
        )
        groups[group].append(row)
    return {
        "available": bool(samples), "probes_in_sealed_prefix": len(probes),
        "unknown_counts": dict(unknown), "all_paired": summarize(samples),
        "short_single_request": summarize(short),
        "short_cohorts": {name: summarize(rows) for name, rows in sorted(groups.items())},
        "samples": samples,
    }


def read_sealed(metadata):
    path = Path(metadata["source"])
    limit = metadata["read_prefix_bytes"]
    digest, read = hashlib.sha256(), 0
    probes, services, waits = [], [], []
    markers = (
        b'"prefill_turnover_sample"', b'"prefill_turnover_incomplete"',
        b'"gpu_service_sample"', b'"gpu_restore_dependency_wait"',
    )
    with path.open("rb") as stream:
        while read < limit:
            line = stream.readline(limit - read)
            if not line.endswith(b"\n"):
                raise ValueError("sealed audit prefix ends in an incomplete line")
            read += len(line)
            digest.update(line)
            if not any(marker in line for marker in markers):
                continue
            row = json.loads(line)
            event = row.get("event")
            if event in ("prefill_turnover_sample", "prefill_turnover_incomplete"):
                probes.append(row)
            elif event == "gpu_service_sample" and row.get("phase") == "prefill":
                services.append(row)
            elif event == "gpu_restore_dependency_wait":
                waits.append(row)
    if digest.hexdigest() != metadata["read_prefix_sha256"]:
        raise ValueError("sealed audit prefix hash changed")
    window = metadata["monitor_window"]
    return analyze_samples(
        probes, services, waits,
        begin=window["begin_ts_ms"], end=window["end_ts_ms"],
    )


def read_lifecycle_prefix(path, *, begin, end):
    """Reuse an existing seal without observing a later live-file suffix."""
    if not (number(begin) and number(end) and 0 <= begin < end):
        raise ValueError("lifecycle analysis requires a finite, nonempty epoch-ms window")
    raw = path.read_bytes()
    lifecycle = json.loads(gzip.decompress(raw))
    arm = Path(lifecycle["arm"]).resolve()
    audit = arm / "server/runtime_audit.jsonl"
    matches = [
        value for name, value in lifecycle["input_jsonl_prefixes"].items()
        if Path(name).resolve() == audit
    ]
    if len(matches) != 1:
        raise ValueError("lifecycle report requires one sealed native audit prefix")
    prefix = matches[0]
    if (
        type(prefix.get("complete_prefix_bytes")) is not int
        or prefix["complete_prefix_bytes"] < 0
        or type(prefix.get("complete_prefix_sha256")) is not str
        or len(prefix["complete_prefix_sha256"]) != 64
        or not isinstance(lifecycle.get("collection_code_commit"), str)
        or not lifecycle["collection_code_commit"]
    ):
        raise ValueError("lifecycle audit seal or collection identity is invalid")
    metadata = {
        "source": str(audit),
        "read_prefix_bytes": prefix["complete_prefix_bytes"],
        "read_prefix_sha256": prefix["complete_prefix_sha256"],
        "monitor_window": {"begin_ts_ms": begin, "end_ts_ms": end},
    }
    return {
        "lifecycle_report": str(path.resolve()),
        "lifecycle_report_sha256": hashlib.sha256(raw).hexdigest(),
        "collection_code_commit": lifecycle["collection_code_commit"],
        "source_prefix": metadata,
        "analysis": read_sealed(metadata),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--busy-report", type=Path)
    inputs.add_argument("--lifecycle-report", type=Path)
    parser.add_argument("--begin-ts-ms", type=float)
    parser.add_argument("--end-ts-ms", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing turnover evidence")
    if args.lifecycle_report is not None:
        if not (
            number(args.begin_ts_ms) and number(args.end_ts_ms)
            and 0 <= args.begin_ts_ms < args.end_ts_ms
        ):
            parser.error("lifecycle analysis requires a finite, nonempty epoch-ms window")
        evidence = read_lifecycle_prefix(
            args.lifecycle_report, begin=args.begin_ts_ms, end=args.end_ts_ms,
        )
        result = {
            "schema_version": 1,
            "observed_at_local": datetime.now().astimezone().isoformat(),
            **evidence,
            "phase_nesting": {"launch_telemetry": "run_batch"},
            "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": (
                "One collecting arm's sealed native phase observations. "
                "The window is diagnostic, not a workflow JCT truncation, "
                "same-work comparison or performance acceptance. "
                "CPU/GPU phases overlap; missing observations remain unknown. "
                "One-in-16 samples are not extrapolated to cumulative cost. "
                "The lifecycle input files were independently sealed, not atomic."
            ),
            "performance_accepted": False,
        }
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({
            "output": str(args.output),
            "collection_code_commit": result["collection_code_commit"],
            "probes_in_sealed_prefix": evidence["analysis"]["probes_in_sealed_prefix"],
            "short_single_request": evidence["analysis"]["short_single_request"],
        }, indent=2))
        return
    if args.begin_ts_ms is not None or args.end_ts_ms is not None:
        parser.error("epoch-ms window options require --lifecycle-report")
    raw = args.busy_report.read_bytes()
    report = json.loads(raw)
    result = {
        "schema_version": 1,
        "started_at_local": datetime.now().astimezone().isoformat(),
        "busy_report": str(args.busy_report.resolve()),
        "busy_report_sha256": hashlib.sha256(raw).hexdigest(),
        "same_version": report.get("same_version"),
        "phase_nesting": {"launch_telemetry": "run_batch"},
        "arms": {name: read_sealed(metadata) for name, metadata in report["arms"].items()},
        "semantics": [
            "Read exact hashed complete-line audit prefixes; append-only live growth is excluded.",
            "Window membership uses native service completion, not delayed probe publication.",
            "Pair by clock domain, forward_iter, ordered request identities and native geometry.",
            "Short prefill means one request and fewer than 512 actual native extend tokens.",
            "Missing, ambiguous, invalid and shutdown observations remain explicit; absent costs are null.",
            "Timing-event GPU spans include dispatch gaps and dependencies; they are not pure kernels.",
            "CPU and GPU intervals overlap; do not add them or subtract them from workflow JCT.",
            "launch_telemetry is nested inside run_batch and must not be added again to CPU cost.",
            "Wall minus own-thread CPU is not a measurement of GIL interference.",
            "Restore waits require exact forward/request identity and containment in the service interval.",
            "One-in-16 samples are not extrapolated; old native traces cannot supply missing phase evidence.",
            "Cohort geometry and historical-version comparisons do not establish causal acceleration.",
        ],
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    result["finished_at_local"] = datetime.now().astimezone().isoformat()
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {field: values[field] for field in (
            "available", "probes_in_sealed_prefix", "unknown_counts", "short_single_request",
        )}
        for name, values in result["arms"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
