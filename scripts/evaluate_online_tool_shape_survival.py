#!/usr/bin/env python3
"""Replay causal command-shape duration history against sealed tool forecasts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_tool_forecast_windows import (
    distribution,
    identity,
    remaining_at_acceptance,
)
from scripts.audit_prefetch_lifecycle import wait_event_identity


def verified_records(path: Path, binding: dict, *, markers: tuple[bytes, ...]):
    consumed, digest = 0, hashlib.sha256()
    limit = binding["complete_prefix_bytes"]
    with path.open("rb") as stream:
        while consumed < limit:
            line = stream.readline(limit - consumed)
            if not line.endswith(b"\n"):
                raise ValueError(f"sealed complete-line prefix changed: {path}")
            consumed += len(line)
            digest.update(line)
            if any(marker in line for marker in markers):
                yield orjson.loads(line)
    if digest.hexdigest() != binding["complete_prefix_sha256"]:
        raise ValueError(f"sealed prefix checksum changed: {path}")


def tool_calls(events: list[dict], *, project: str) -> list[dict]:
    epochs, open_calls, result = {}, {}, []
    ended_without_start = set()
    for event in sorted(events, key=lambda row: (row["ts_ms"], row["sequence"])):
        scope = event["workflow_id"], event.get("invocation_id")
        kind, attrs = event["kind"], event.get("attributes") or {}
        if kind == "llm_result":
            if event.get("context_id") and type(event.get("context_epoch")) is int:
                epochs[scope] = event["context_id"], event["context_epoch"]
            continue
        run_id = attrs.get("tool_run_id")
        if not run_id:
            continue
        if kind == "tool_start":
            if run_id in ended_without_start:
                raise ValueError("tool completion precedes start")
            if run_id in open_calls:
                raise ValueError("ambiguous tool start identity")
            context, epoch = epochs.get(scope, (None, None))
            open_calls[run_id] = {
                "workflow_id": scope[0], "invocation_id": scope[1],
                "context_id": context, "context_epoch": epoch,
                "project": project, "tool_run_id": run_id,
                "tool_name": attrs.get("tool_name"),
                "command_class": attrs.get("observed_command_class") or "unknown",
                "shape": attrs.get("observed_command_shape") or "unknown",
                "is_child": attrs.get("is_child"),
                "start_ms": event["ts_ms"],
            }
        elif kind == "tool_end" and run_id in open_calls:
            call = open_calls.pop(run_id)
            end = event["ts_ms"]
            if end < call["start_ms"]:
                raise ValueError("tool completion precedes start")
            result.append({
                **call, "end_ms": end, "duration_ms": end - call["start_ms"],
                "status": attrs.get("status"),
                "exception_type": attrs.get("exception_type"),
                "tool_error_class": attrs.get("tool_error_class"),
            })
        elif kind == "tool_end":
            ended_without_start.add(run_id)
    return result


def history_key(call: dict) -> tuple:
    return call["project"], call["command_class"], call["shape"], call["is_child"]


class CausalShapeHistory:
    def __init__(self, calls: list[dict], *, window: int, include_errors: bool):
        self.calls = sorted(calls, key=lambda row: row["end_ms"])
        self.window = window
        self.include_errors = include_errors
        self.cursor = 0
        self.history: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=window))

    def remaining(self, call: dict, *, now_ms: float, minimum_support: int):
        while self.cursor < len(self.calls) and self.calls[self.cursor]["end_ms"] < now_ms:
            prior = self.calls[self.cursor]
            self.cursor += 1
            if (
                prior["tool_name"] != "execute"
                or prior["shape"] == "unknown"
                or prior["command_class"] == "unknown"
                or prior["status"] not in (
                    ("success", "error") if self.include_errors else ("success",)
                )
                or prior["exception_type"] in (
                    "CancelledError", "ActivationDeadlineExceeded",
                )
                or prior["tool_error_class"] in ("cancelled", "administrative_cancel")
            ):
                continue
            self.history[history_key(prior)].append(prior)
        past = self.history.get(history_key(call), ())
        age = now_ms - call["start_ms"]
        if age < 0 or len(past) < minimum_support:
            return None, len(past), 0
        surviving = [row["duration_ms"] for row in past if row["duration_ms"] > age]
        return (
            median(surviving) - age if surviving else None,
            len(past), len(surviving),
        )


def eligible_call(call: dict, label: dict, *, now_ms: float, offset_ms: float,
                  include_roots: bool = False) -> bool:
    completion = label.get("completion_ts_ms")
    return bool(
        label.get("completion_evidence") == "whole_tool_request_round"
        and label.get("observed_tool_call_count") == 1
        and completion is not None
        and call["tool_name"] == "execute"
        and (
            call["is_child"] is True
            or include_roots and call["is_child"] is False
        )
        and call["status"] in ("success", "error")
        and call["exception_type"] not in ("CancelledError", "ActivationDeadlineExceeded")
        and call["start_ms"] <= now_ms < call["end_ms"]
        and abs(call["end_ms"] + offset_ms - completion) <= 1.
    )


def summarize(rows: list[dict]) -> dict:
    def timing(variant):
        near = [row[f"{variant}_last_second_absolute_error_ms"] for row in rows]
        leads = [row[f"{variant}_first_1s_window_actual_lead_ms"] for row in rows]
        leads = [value for value in leads if value is not None]
        return {
            "tool_weighted_last_second_absolute_error_ms": distribution(near),
            "first_1s_window_actual_lead_ms": distribution(leads),
            "first_1s_window_too_early_over_10s": sum(value > 10_000 for value in leads),
            "first_1s_window_within_actual_last_second": sum(value <= 1_000 for value in leads),
            "no_pre_completion_1s_window": len(rows) - len(leads),
        }
    return {
        "independent_single_execute_waits": len(rows),
        "independent_single_execute_child_waits": sum(
            row["is_child"] is True for row in rows
        ),
        "independent_single_execute_root_waits": sum(
            row["is_child"] is False for row in rows
        ),
        "waits_with_empirical_support": sum(row["empirical_frames"] > 0 for row in rows),
        "waits_with_last_second_empirical_support": sum(
            row["last_second_empirical_frames"] > 0 for row in rows
        ),
        "recorded": timing("recorded"), "candidate": timing("candidate"),
        "recorded_early_full_bytes": sum(row["early_full_bytes"] for row in rows),
        "semantics": (
            "Same recorded forecast acceptance times and completed tool boundaries; "
            "unsupported empirical queries fall back to the recorded forecast. "
            "A timing window is not a simulated H2D action, feasible capacity or causal benefit."
        ),
    }


def evaluate(calls: list[dict], labels: list[dict], forecasts: list[dict], *,
             offset_ms: float, window: int = 64, minimum_support: int = 4,
             include_errors: bool = True, after_initial_p90: bool = False,
             include_roots: bool = False) -> dict:
    if window < 1 or not 1 <= minimum_support <= window:
        raise ValueError("history support must be positive and bounded by the window")
    waits = {identity(row): row for row in labels if row["source"] == "tool_wait"}
    grouped_calls = defaultdict(list)
    for call in calls:
        grouped_calls[identity(call)].append(call)
    history = CausalShapeHistory(calls, window=window, include_errors=include_errors)
    frames, counts, original_upper = defaultdict(list), Counter(), {}
    for forecast in sorted(forecasts, key=lambda row: row["ts_ms"]):
        wait_id = identity(forecast)
        label = waits.get(wait_id)
        candidates = grouped_calls.get(wait_id, ())
        if label is None or len(candidates) != 1:
            counts["unlinked_or_multi_call"] += 1
            continue
        call = candidates[0]
        now = forecast["ts_ms"] - offset_ms
        if not eligible_call(
            call, label, now_ms=now, offset_ms=offset_ms, include_roots=include_roots,
        ):
            counts["unproven_or_outside_single_execute_child_call"] += 1
            continue
        issued = forecast.get("observed_monotonic_ms")
        if issued is None or not 0 <= now - issued < 5_000:
            counts["expired_or_clock_reversed"] += 1
            continue
        recorded = remaining_at_acceptance(forecast, now)
        if recorded is None or not math.isfinite(recorded):
            counts["recorded_timing_unsupported"] += 1
            continue
        upper = forecast.get("p90_ms")
        if (
            wait_id not in original_upper
            and type(upper) in (int, float) and math.isfinite(upper) and upper > 0
        ):
            original_upper[wait_id] = issued + upper
        empirical, support, survivors = history.remaining(
            call, now_ms=now, minimum_support=minimum_support,
        )
        overrun = wait_id in original_upper and now >= original_upper[wait_id]
        if after_initial_p90 and not overrun:
            empirical = None
        candidate = recorded if empirical is None else empirical
        actual = call["end_ms"] - now
        frames[wait_id].append({
            "ts_ms": forecast["ts_ms"], "actual_ms": actual,
            "recorded_ms": recorded, "candidate_ms": candidate,
            "empirical": empirical is not None, "support": support,
            "survivors": survivors, "original_p90_exceeded": overrun,
        })
    rows = []
    for wait_id, values in frames.items():
        label, call = waits[wait_id], grouped_calls[wait_id][0]
        near = [row for row in values if row["actual_ms"] <= 1_000]
        row = {
            "workflow_id": call["workflow_id"], "invocation_id": call["invocation_id"],
            "context_id": call["context_id"], "context_epoch": call["context_epoch"],
            "project": call["project"], "shape": call["shape"],
            "is_child": call["is_child"],
            "command_class": call["command_class"], "status": call["status"],
            "actual_duration_ms": call["duration_ms"], "forecast_frames": len(values),
            "empirical_frames": sum(item["empirical"] for item in values),
            "last_second_empirical_frames": sum(item["empirical"] for item in near),
            "early_full_bytes": label.get("early_started_full_bytes", 0) or 0,
            "early_full_reused_bytes": label.get("early_started_and_reused_full_bytes", 0) or 0,
            "positive_full_plan": (label.get("max_pre_completion_planned_full_bytes") or 0) > 0,
            "last_pre_completion_frame": values[-1],
            "original_p90_deadline_mono_ms": original_upper.get(wait_id),
        }
        for variant in ("recorded", "candidate"):
            row[f"{variant}_last_second_absolute_error_ms"] = (
                median(abs(item[f"{variant}_ms"] - item["actual_ms"]) for item in near)
                if near else None
            )
            first = next((item for item in values if item[f"{variant}_ms"] <= 1_000), None)
            row[f"{variant}_first_1s_window_actual_lead_ms"] = (
                first["actual_ms"] if first is not None else None
            )
        rows.append(row)
    return {
        "counts": dict(counts), "summary": summarize(rows),
        "positive_full_plan_summary": summarize([row for row in rows if row["positive_full_plan"]]),
        "by_role": {
            role: summarize([row for row in rows if row["is_child"] is is_child])
            for role, is_child in (("child", True), ("root", False))
        },
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-support", type=int, default=4)
    parser.add_argument("--window", type=int, default=64)
    parser.add_argument("--successful-history-only", action="store_true")
    parser.add_argument("--after-initial-p90", action="store_true")
    parser.add_argument(
        "--include-roots", action="store_true",
        help="Score root calls too, retaining separate same-role causal histories.",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing pilot evidence")
    payload = args.sealed.read_bytes()
    sealed = json.loads(gzip.decompress(payload))
    prefixes = sealed["input_jsonl_prefixes"]
    calls, forecasts, offset, used = [], [], None, {}
    for name, binding in prefixes.items():
        path = Path(name)
        if name.endswith("runtime_events.deepagents.jsonl"):
            events = list(verified_records(
                path, binding, markers=(b'"tool_start"', b'"tool_end"', b'"llm_result"'),
            ))
            calls.extend(tool_calls(events, project=path.parent.name.split("__", 1)[0]))
            used[name] = binding
        elif name.endswith("admission_opportunities.jsonl"):
            for row in verified_records(
                path, binding, markers=(b'"tool_wait_forecast"', b'"safe_point_census"'),
            ):
                if row.get("event") == "safe_point_census" and offset is None:
                    offset = row["ts_ms"] - row["monotonic_ms"]
                elif row.get("event") == "tool_wait_forecast":
                    forecasts.append(row)
            used[name] = binding
    if offset is None:
        raise ValueError("sealed client/server clock bridge is missing")
    labels = sealed["wait_event_attribution"]["rows"]
    boundaries = {
        wait_event_identity(row): row.get("completion_ts_ms")
        for row in labels if row["source"] == "tool_wait"
    }
    early_bytes = Counter()
    for action in sealed["rows"]:
        if action.get("source") != "tool_wait":
            continue
        key = wait_event_identity(action)
        end = boundaries.get(key)
        submitted = action.get("submit_ts_ms")
        if end is not None and submitted is not None and submitted < end:
            early_bytes[key] += (action.get("pool_bytes") or {}).get("kv", 0)
    for label in labels:
        label["early_started_full_bytes"] = early_bytes[wait_event_identity(label)]
    result = evaluate(
        calls, labels, forecasts, offset_ms=offset,
        window=args.window, minimum_support=args.minimum_support,
        include_errors=not args.successful_history_only,
        after_initial_p90=args.after_initial_p90,
        include_roots=args.include_roots,
    )
    result.update({
        "status": "offline_causal_shape_survival_pilot_not_online_or_calibrated",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "sealed_report": str(args.sealed.resolve()),
        "sealed_report_sha256": hashlib.sha256(payload).hexdigest(),
        "source_prefixes": used, "client_monotonic_to_wall_offset_ms": offset,
        "history_window": args.window, "minimum_history_support": args.minimum_support,
        "history_statuses": ["success"] if args.successful_history_only else ["success", "error"],
        "evaluated_roles": ["child", "root"] if args.include_roots else ["child"],
        "history_activation": (
            "after_the_first_accepted_forecast_p90_deadline"
            if args.after_initial_p90 else "every_supported_forecast"
        ),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "Only previously ended calls with end strictly before the prediction enter history.",
            "Past calls of the same project/class/shape/child role are runtime-observable inputs; the current call's end is only a label.",
            "Every query conditions duration history on the current tool still running; no survivor means unknown and recorded fallback.",
            "Actual tool error results are completion labels; cancellation and activation-deadline exceptions are excluded.",
            "History is updated at prediction time, an offline candidate that requires online plumbing and calibrated support before deployment.",
            "No thresholds are chosen using this evaluation; the one-second diagnostic window is not a new runtime policy.",
            "Candidate design uses this development prefix; a second independent prefix is required before online qualification.",
            "Project-local past history is causal but does not establish unseen-project calibration or robustness.",
            "No physical actions, eviction, priority, leases, budgets, raw timestamps or running code are changed.",
        ],
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(args.output), "summary": result["summary"],
        "positive_full_plan_summary": result["positive_full_plan_summary"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
