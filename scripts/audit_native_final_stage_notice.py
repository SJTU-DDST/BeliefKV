#!/usr/bin/env python3
"""Audit native child completion notices against natural child returns."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "p50_ms": median(values),
        "p90_ms": _percentile(values, 0.9),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values),
    }


def _interval_union(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    current_end = float("-inf")
    for start, end in sorted(intervals):
        if start >= current_end:
            total += end - start
        elif end > current_end:
            total += end - current_end
        current_end = max(current_end, end)
    return total


def _service_clock(
    targets: dict[str, dict[str, float]],
    server_events: Path,
    service_audit: Path,
) -> dict:
    boundaries: dict[str, dict[str, float]] = {rid: {} for rid in targets}
    with server_events.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            rid = (row.get("attributes") or {}).get("request_id")
            if rid in boundaries and row.get("kind") in ("llm_submit", "llm_result"):
                boundaries[rid].setdefault(row["kind"], float(row["ts_ms"]))
    first_service: dict[str, float] = {}
    intervals: dict[str, list[tuple[float, float]]] = {}
    incomplete_intervals: set[str] = set()
    with service_audit.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") != "gpu_service_sample":
                continue
            start = row.get("service_start_ts_ms")
            if type(start) not in (int, float) or not math.isfinite(start):
                continue
            end = row.get("complete_ts_ms")
            for sample in row.get("request_samples") or ():
                rid = sample.get("request_id")
                if rid in boundaries:
                    first_service[rid] = min(
                        float(start), first_service.get(rid, float("inf"))
                    )
                    if (
                        type(end) not in (int, float)
                        or not math.isfinite(end) or end < start
                    ):
                        incomplete_intervals.add(rid)
                    else:
                        intervals.setdefault(rid, []).append((float(start), float(end)))

    excluded: Counter[str] = Counter()
    notice_to_first_lower, submit_to_first, first_to_result, first_to_return_lower = [], [], [], []
    active_service, without_service = [], []
    records = []
    for rid, target in targets.items():
        clocks = boundaries[rid]
        if "llm_submit" not in clocks or "llm_result" not in clocks:
            excluded["missing_server_boundary"] += 1
            continue
        first = first_service.get(rid)
        if first is None:
            excluded["missing_gpu_service"] += 1
            continue
        submit, result = clocks["llm_submit"], clocks["llm_result"]
        if first < submit or result < first:
            excluded["non_monotonic_server_clock"] += 1
            continue
        submit_to_first.append(first - submit)
        notice_to_first_lower.append(
            target["client_notice_to_submit_ms"] + first - submit
        )
        first_to_result.append(result - first)
        first_to_return_lower.append(
            result - first + target["client_result_to_return_ms"]
        )
        record = {
            "final_request_id": rid,
            "workflow_id": target["workflow_id"],
            "child_id": target["child_id"],
            "server_first_service_ts_ms": first,
            "server_result_ts_ms": result,
            "server_submit_to_first_service_ms": first - submit,
            "notice_to_first_service_lower_bound_ms": (
                target["client_notice_to_submit_ms"] + first - submit
            ),
            "first_service_to_server_result_ms": result - first,
            "client_result_to_return_ms": target["client_result_to_return_ms"],
            "first_service_to_return_lower_bound_ms": (
                result - first + target["client_result_to_return_ms"]
            ),
        }
        records.append(record)
        if rid in incomplete_intervals or not intervals.get(rid):
            excluded["incomplete_service_intervals"] += 1
            continue
        clipped = [
            (max(first, start), min(result, end))
            for start, end in intervals[rid]
            if end > first and start < result
        ]
        active = _interval_union(clipped)
        active_service.append(active)
        without_service.append(max(0.0, result - first - active))
        record["scheduler_service_interval_ms"] = active
        record["interleaved_without_service_ms"] = max(
            0.0, result - first - active
        )
    return {
        "matched_requests": len(first_to_result),
        "excluded": dict(excluded),
        "server_submit_to_first_service": _distribution(submit_to_first),
        "notice_to_first_service_lower_bound": _distribution(notice_to_first_lower),
        "first_service_to_server_result": _distribution(first_to_result),
        "first_service_to_child_return_lower_bound": _distribution(
            first_to_return_lower
        ),
        "scheduler_service_intervals": _distribution(active_service),
        "interleaved_without_service": _distribution(without_service),
        "records": records,
        "clock_note": (
            "First service includes prefill, not necessarily decode. Server "
            "submit/service/result share a wall clock; client result/RETURN "
            "share a monotonic clock. The lower bound omits server-result to "
            "client-result delivery and cannot be used as an exact timestamp. "
            "Notice-to-first-service also omits client-submit to server-submit "
            "delivery and is only a lower bound. "
            "Service intervals are a union of scheduler/worker batch intervals, "
            "not per-request CUDA kernel time. Later no-service gaps remain "
            "after first service. This label is only observable after the "
            "final request's first GPU service; notice-to-first-service delay "
            "needs a separate predictor."
        ),
    }


def audit(
    client_dir: Path,
    *,
    server_events: Path | None = None,
    service_audit: Path | None = None,
) -> dict:
    if (server_events is None) != (service_audit is None):
        raise ValueError("server events and GPU service audit must be supplied together")
    counts: Counter[str] = Counter()
    lead, submit_wait, service, return_gap = [], [], [], []
    targets: dict[str, dict[str, float]] = {}
    paths = sorted(client_dir.glob("workflows/*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no workflow events in {client_dir}")
    for path in paths:
        counts["workflow_traces"] += 1
        with path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        children = {
            row.get("target_invocation_id") for row in events
            if row.get("kind") == "spawn" and row.get("target_invocation_id")
        }
        counts["spawned_children"] += len(children)
        for child in children:
            child_events = [
                row for row in events if row.get("invocation_id") == child
            ]
            natural_terminals = [
                row for row in child_events
                if row.get("kind") == "return"
                and row.get("attributes", {}).get("outcome") == "completed"
                and row.get("attributes", {}).get("child_report_status") != "blocked"
            ]
            notices = [
                row for row in child_events
                if row.get("kind") == "tool_end"
                and row.get("attributes", {}).get("tool_name")
                == "announce_completion_intent"
                and row["attributes"].get("status") == "success"
            ]
            if any(
                row.get("kind") == "structured_action"
                and row.get("attributes", {}).get("child_completion_signal_kind")
                == "stage"
                and row.get("attributes", {}).get("beliefkv_child_completion_intent")
                is True
                for row in child_events
            ):
                counts["stage_published_children"] += 1
            if any(
                row.get("kind") == "structured_action"
                and row.get("attributes", {}).get("child_completion_signal_kind")
                == "terminal_fallback"
                for row in child_events
            ):
                counts["terminal_fallback_children"] += 1
            if not notices:
                counts["children_without_notice"] += 1
                counts[
                    "natural_returns_without_notice"
                    if natural_terminals else "unreturned_children_without_notice"
                ] += 1
                continue
            counts["announced_children"] += 1
            counts["extra_notices"] += max(0, len(notices) - 1)
            notice = notices[-1]
            starts = [
                row for row in child_events
                if row.get("kind") == "tool_start"
                and row.get("attributes", {}).get("tool_name")
                == "announce_completion_intent"
                and row["ts_ms"] <= notice["ts_ms"]
            ]
            if starts:
                counts["notice_tool_calls"] += 1
            later_tools = [
                row for row in child_events
                if row.get("kind") == "tool_start"
                and row["ts_ms"] > notice["ts_ms"]
                and row.get("attributes", {}).get("tool_name")
                != "announce_completion_intent"
            ]
            if later_tools:
                counts["notices_invalidated_by_later_tool"] += 1
                continue
            terminals = [
                row for row in natural_terminals
                if row["ts_ms"] > notice["ts_ms"]
            ]
            if not terminals:
                counts["notices_without_natural_return"] += 1
                continue
            end = terminals[0]["ts_ms"]
            counts["paired_natural_returns"] += 1
            lead.append(end - notice["ts_ms"])
            submits = [
                row for row in child_events
                if row.get("kind") == "llm_submit"
                and notice["ts_ms"] < row["ts_ms"] < end
                and not row.get("attributes", {}).get("runtime_internal")
            ]
            results = [
                row for row in child_events
                if row.get("kind") == "llm_result"
                and notice["ts_ms"] < row["ts_ms"] < end
                and not row.get("attributes", {}).get("runtime_internal")
            ]
            if len(submits) == len(results) == 1 and submits[0]["ts_ms"] <= results[0]["ts_ms"]:
                counts["single_final_request"] += 1
                submit_wait.append(submits[0]["ts_ms"] - notice["ts_ms"])
                service.append(results[0]["ts_ms"] - submits[0]["ts_ms"])
                return_gap.append(end - results[0]["ts_ms"])
                rid = (results[0].get("attributes") or {}).get("request_id")
                if rid and server_events is not None:
                    if rid in targets:
                        raise ValueError(f"duplicate final request ID: {rid}")
                    targets[rid] = {
                        "client_notice_to_submit_ms": (
                            submits[0]["ts_ms"] - notice["ts_ms"]
                        ),
                        "client_result_to_return_ms": end - results[0]["ts_ms"],
                        "workflow_id": results[0].get("workflow_id"),
                        "child_id": child,
                    }
            else:
                counts["other_final_request_shape"] += 1
    report = {
        "counts": dict(counts),
        "notice_to_return": _distribution(lead),
        "notice_to_final_submit": _distribution(submit_wait),
        "final_submit_to_result": _distribution(service),
        "final_result_to_return": _distribution(return_gap),
    }
    if server_events is not None and service_audit is not None:
        report["service_clock"] = _service_clock(
            targets, server_events, service_audit
        )
        report["service_clock"]["identified_final_requests"] = len(targets)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_dir", type=Path)
    parser.add_argument("--server-events", type=Path)
    parser.add_argument("--service-audit", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rows-output", type=Path)
    args = parser.parse_args()
    report = audit(
        args.client_dir,
        server_events=args.server_events,
        service_audit=args.service_audit,
    )
    if args.rows_output is not None:
        if "service_clock" not in report:
            parser.error("--rows-output requires both server data files")
        args.rows_output.parent.mkdir(parents=True, exist_ok=True)
        with args.rows_output.open("w", encoding="utf-8") as stream:
            for record in report["service_clock"].pop("records"):
                stream.write(json.dumps(record) + "\n")
    elif "service_clock" in report:
        report["service_clock"].pop("records")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
