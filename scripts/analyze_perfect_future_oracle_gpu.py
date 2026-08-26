#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Iterable, Mapping


def _read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _records(path: Path) -> Iterable[Mapping[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = fraction * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def _correctness_passed(summary: Mapping[str, object]) -> bool:
    gates = summary.get("correctness_gates")
    return bool(
        isinstance(gates, Mapping)
        and gates
        and all(value is True for value in gates.values() if isinstance(value, bool))
        and summary.get("shutdown_state") == "acknowledged"
    )


def _analyze_audit(path: Path, makespan_s: float) -> dict[str, object]:
    event_counts: Counter[str] = Counter()
    hbm_ratios: list[float] = []
    host_bytes: list[int] = []
    prefix_tokens = 0
    prompt_tokens = 0
    service_elapsed_ms = 0.0
    service_by_phase: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"samples": 0, "tokens": 0, "elapsed_ms": 0.0}
    )
    decode_batch_sizes: list[float] = []
    residency: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "bytes": 0, "statuses": Counter()}
    )
    reversals: list[float] = []
    short_reversals = 0

    for record in _records(path):
        event = str(record.get("event") or "unknown")
        event_counts[event] += 1
        if event == "resource_snapshot":
            capacity = int(record.get("hbm_capacity_bytes") or 0)
            used = int(record.get("hbm_used_bytes") or 0)
            if capacity > 0:
                hbm_ratios.append(used / capacity)
            host_bytes.append(int(record.get("host_used_bytes") or 0))
        elif event == "request_started":
            prefix_tokens += int(record.get("cache_hit_tokens") or 0)
            prompt_tokens += int(record.get("prompt_tokens") or 0)
        elif event == "gpu_service_sample":
            phase = str(record.get("phase") or "unknown")
            elapsed_ms = float(record.get("service_elapsed_ms") or 0.0)
            tokens = int(record.get("tokens") or 0)
            service_elapsed_ms += elapsed_ms
            service_by_phase[phase]["samples"] += 1
            service_by_phase[phase]["tokens"] += tokens
            service_by_phase[phase]["elapsed_ms"] += elapsed_ms
            if phase == "decode":
                decode_batch_sizes.append(float(record.get("batch_size") or 0))
        elif event == "online_joint_residency_terminal":
            action = str(record.get("action") or "unknown")
            residency[action]["count"] += 1
            residency[action]["bytes"] += int(record.get("actual_bytes") or 0)
            statuses = residency[action]["statuses"]
            assert isinstance(statuses, Counter)
            statuses[str(record.get("status") or "unknown")] += 1
        elif event == "online_joint_residency_direction_reversed":
            reversals.append(float(record.get("reversal_age_ms") or 0.0))
            short_reversals += int(record.get("short_reverse") is True)

    serialized_residency = {
        action: {
            "count": int(values["count"]),
            "bytes": int(values["bytes"]),
            "statuses": dict(values["statuses"]),
        }
        for action, values in sorted(residency.items())
    }
    return {
        "event_counts": dict(event_counts),
        "hbm": {
            "sample_count": len(hbm_ratios),
            "peak_ratio": max(hbm_ratios) if hbm_ratios else None,
            "fraction_at_or_above_80_percent": _ratio(
                sum(value >= 0.80 for value in hbm_ratios), len(hbm_ratios)
            ),
            "fraction_at_or_above_98_percent": _ratio(
                sum(value >= 0.98 for value in hbm_ratios), len(hbm_ratios)
            ),
        },
        "host": {"peak_used_bytes": max(host_bytes) if host_bytes else 0},
        "prefix": {
            "cache_hit_tokens": prefix_tokens,
            "prompt_tokens": prompt_tokens,
            "aggregate_hit_ratio": _ratio(prefix_tokens, prompt_tokens),
        },
        "gpu_service": {
            "sample_count": sum(
                int(values["samples"]) for values in service_by_phase.values()
            ),
            "observed_interval_ms": service_elapsed_ms,
            "observed_interval_fraction_of_makespan": _ratio(
                service_elapsed_ms, makespan_s * 1000.0
            ),
            "by_phase": dict(service_by_phase),
            "decode_batch_size": _distribution(decode_batch_sizes),
            "decode_batch_at_least_16_fraction": _ratio(
                sum(value >= 16 for value in decode_batch_sizes),
                len(decode_batch_sizes),
            ),
        },
        "explicit_residency": serialized_residency,
        "direction_reversal": {
            "count": len(reversals),
            "short_count": short_reversals,
            "age_ms": _distribution(reversals),
        },
    }


def _analyze_transfers(path: Path) -> dict[str, object]:
    groups: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {"count": 0, "bytes": 0, "duration_ms": []}
    )
    status_counts: Counter[str] = Counter()
    for record in _records(path):
        direction = str(record.get("direction") or "unknown")
        command_kind = str(record.get("command_kind") or "unknown")
        key = (command_kind, direction)
        groups[key]["count"] += 1
        groups[key]["bytes"] += int(record.get("actual_bytes") or 0)
        status_counts[str(record.get("status") or "unknown")] += 1
        submit = record.get("submit_ts_ms")
        complete = record.get("complete_ts_ms")
        if submit is not None and complete is not None:
            durations = groups[key]["duration_ms"]
            assert isinstance(durations, list)
            durations.append(max(0.0, float(complete) - float(submit)))
    return {
        "status_counts": dict(status_counts),
        "by_command_kind_direction": [
            {
                "command_kind": command_kind,
                "direction": direction,
                "count": int(values["count"]),
                "bytes": int(values["bytes"]),
                "submit_to_complete_ms": _distribution(values["duration_ms"]),
            }
            for (command_kind, direction), values in sorted(groups.items())
        ],
    }


def analyze_run(run_dir: Path) -> dict[str, object]:
    replay = _read_json(run_dir / "replay" / "replay_summary.json")
    runtime = _read_json(run_dir / "server" / "latest_runtime_summary.json")
    makespan_s = float(replay["makespan_s"])
    return {
        "run_dir": str(run_dir.resolve()),
        "replay": replay,
        "correctness": {
            "passed": _correctness_passed(runtime),
            "gates": runtime.get("correctness_gates"),
            "shutdown_state": runtime.get("shutdown_state"),
            "transactions": runtime.get("transactions"),
        },
        "joint_control": runtime.get("joint_control"),
        "audit": _analyze_audit(
            run_dir / "server" / "runtime_audit.jsonl", makespan_s
        ),
        "transfers": _analyze_transfers(
            run_dir / "server" / "transfer_telemetry.jsonl"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze frozen-demand GPU O0/O3 Oracle replay arms."
    )
    parser.add_argument("--o0-run", type=Path, required=True)
    parser.add_argument("--o3-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    o0 = analyze_run(args.o0_run)
    o3 = analyze_run(args.o3_run)
    o0_replay = o0["replay"]
    o3_replay = o3["replay"]
    assert isinstance(o0_replay, Mapping)
    assert isinstance(o3_replay, Mapping)
    same_demand = bool(
        o0_replay.get("truth_digest") == o3_replay.get("truth_digest")
        and o0_replay.get("completed_requests")
        == o3_replay.get("completed_requests")
        and o0_replay.get("prompt_tokens") == o3_replay.get("prompt_tokens")
        and o0_replay.get("output_tokens") == o3_replay.get("output_tokens")
    )
    o0_throughput = float(o0_replay["workflows_per_hour"])
    o3_throughput = float(o3_replay["workflows_per_hour"])
    o0_makespan = float(o0_replay["makespan_s"])
    o3_makespan = float(o3_replay["makespan_s"])
    candidate_gain = o3_throughput / o0_throughput - 1.0
    result = {
        "schema_version": 1,
        "comparison": {
            "same_frozen_demand": same_demand,
            "both_correctness_gates_passed": bool(
                o0["correctness"]["passed"] and o3["correctness"]["passed"]
            ),
            "o3_candidate_throughput_gain": candidate_gain,
            "o3_candidate_makespan_change": o3_makespan / o0_makespan - 1.0,
            "finite_candidate_oracle_gain_with_o0_noop": max(0.0, candidate_gain),
            "decision": (
                "positive_joint_gap"
                if candidate_gain >= 0.10
                else "small_joint_gap"
                if candidate_gain >= 0.03
                else "no_positive_joint_gap"
            ),
            "run_count_caveat": "one paired frozen-demand run",
        },
        "o0": o0,
        "o3": o3,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result["comparison"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
