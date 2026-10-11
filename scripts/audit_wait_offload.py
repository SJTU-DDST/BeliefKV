#!/usr/bin/env python3
"""Separate waiting-agent Host backups from HBM demotion and audit tool timing."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from scripts.audit_prefetch_lifecycle import distribution


def sealed_records(path: Path, seals: list[dict]):
    digest, size, count = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            size += len(line)
            if line.strip():
                count += 1
                yield json.loads(line)
    seals.append({
        "path": str(path), "bytes": size, "records": count,
        "sha256": digest.hexdigest(),
    })


def read_tools(arm: Path, seals: list[dict]):
    windows, joins = defaultdict(list), []
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        contexts, active = {}, {}
        for row in sealed_records(path, seals):
            scope = row["workflow_id"], row["invocation_id"]
            attrs = row.get("attributes") or {}
            if row["kind"] == "llm_submit" and row.get("context_id"):
                contexts[scope] = row["context_id"], row["context_epoch"]
            elif row["kind"] == "join_create":
                joins.append(row)
            elif row["kind"] in ("tool_start", "tool_end"):
                tool_id = attrs.get("tool_run_id") or attrs.get("tool_call_id")
                if tool_id is None:
                    continue
                identity = (*scope, tool_id)
                if row["kind"] == "tool_start":
                    context = contexts.get(scope)
                    if context is None:
                        continue
                    window = {
                        "workflow_id": scope[0], "invocation_id": scope[1],
                        "context_id": context[0], "context_epoch": context[1],
                        "tool_run_id": tool_id, "tool_name": attrs.get("tool_name"),
                        "command_class": attrs.get("observed_command_class"),
                        "is_child": attrs.get("is_child"),
                        "start_monotonic_ms": row["ts_ms"],
                        "end_monotonic_ms": None,
                    }
                    active[identity] = window
                    windows[context].append(window)
                elif identity in active:
                    window = active.pop(identity)
                    window["end_monotonic_ms"] = row["ts_ms"]
                    window["status"] = attrs.get("status")
    for group in windows.values():
        group.sort(key=lambda row: row["start_monotonic_ms"])
    return windows, joins


def hint_from_forecast(row: dict, hint_type=NativeToolWaitHint) -> NativeToolWaitHint:
    key = PrefillCandidateKey(
        "audit", row["workflow_id"], row["invocation_id"],
        row["context_id"], row["context_epoch"], 0,
    )
    observed = row["observed_monotonic_ms"]
    return hint_type(
        key, row["p10_ms"], row["p50_ms"], row["p90_ms"],
        observed, observed + 5000., "audit",
        release_cdf=tuple(tuple(point) for point in row.get("release_cdf", ())),
        residual_duration_mass=tuple(
            tuple(point) for point in row.get("residual_duration_mass", ())
        ),
        residual_support_detail=row.get("residual_support_detail"),
    )


def tool_decision(
    row, forecast, offset, windows, starts, *, hint_type=NativeToolWaitHint,
) -> dict:
    result = {
        "event": row["event"], "ts_ms": row["ts_ms"],
        "context_id": row["context_id"], "context_epoch": row["context_epoch"],
        "command_id": row.get("command_id"), "node_id": row.get("node_id"),
    }
    when = row["ts_ms"] - offset
    result["decision_monotonic_ms"] = when
    scope = row["context_id"], row["context_epoch"]
    group = windows.get(scope, ())
    index = bisect_right(starts.get(scope, ()), when)
    active = [
        window for window in group[:index]
        if window["end_monotonic_ms"] is None or when < window["end_monotonic_ms"]
    ]
    result["active_tools"] = len(active)
    result["tool_windows_in_context_epoch"] = len(group)
    if (
        not active and group and index == len(group)
        and all(window["end_monotonic_ms"] is not None for window in group)
    ):
        active = [max(group, key=lambda window: window["end_monotonic_ms"])]
        result["tool_already_ended_on_harness"] = True
    if len(active) == 1:
        window = active[0]
        result.update(window)
        end = window["end_monotonic_ms"]
        result["actual_remaining_ms"] = end - when if end is not None else None
        result["actual_remaining_ge_2000ms"] = (
            end - when >= 2000. if end is not None else None
        )
    else:
        result["boundary_unknown_reason"] = "no_single_active_tool_at_decision"
    if forecast is not None:
        hint = hint_from_forecast(forecast, hint_type)
        result["forecast_age_ms"] = when - hint.issued_monotonic_ms
        result["forecast_live"] = hint.live(hint.key, now_ms=when)
        result["forecast_invocation_matches"] = (
            result.get("invocation_id") == forecast["invocation_id"]
        )
        if result["forecast_live"] and result["forecast_invocation_matches"]:
            probability = hint.release_probability_within(2000., now_ms=when)
            p10 = hint.remaining_quantile(.1, now_ms=when)
            p50 = hint.remaining_quantile(.5, now_ms=when)
            curve_hint = replace(hint, residual_duration_mass=())
            curve_probability = curve_hint.release_probability_within(2000., now_ms=when)
            curve_p10 = curve_hint.remaining_quantile(.1, now_ms=when)
            age = when - hint.issued_monotonic_ms
            result.update({
                "predicted_return_within_2000ms_probability": probability,
                "remaining_p10_ms": p10, "remaining_p50_ms": p50,
                "runtime_long_wait_predicate": (
                    probability is not None and probability <= .1
                    if hint.release_cdf else p10 is not None and p10 >= 2000.
                ),
                "timing_policy": forecast.get("timing_policy"),
                "residual_support_detail": hint.residual_support_detail,
                "surviving_history_points": sum(
                    value > age for value, _ in hint.residual_duration_mass
                ),
                "cdf_return_within_2000ms_probability": curve_probability,
                "cdf_long_wait_predicate": (
                    curve_probability is not None and curve_probability <= .1
                    if hint.release_cdf
                    else curve_p10 is not None and curve_p10 >= 2000.
                ),
            })
            actual = result.get("actual_remaining_ms")
            if actual is not None and p50 is not None:
                result["p50_signed_error_ms"] = p50 - actual
                result["p50_absolute_error_ms"] = abs(p50 - actual)
    return result


def decision_summary(rows: list[dict]) -> dict:
    known = [row for row in rows if row.get("actual_remaining_ms") is not None]
    groups, support_groups = {}, defaultdict(list)
    for row in known:
        identity = row["workflow_id"], row["invocation_id"], row["tool_run_id"]
        groups.setdefault(identity, row)
        support_groups[
            row.get("residual_support_detail"), row.get("surviving_history_points")
        ].append(row)
    first = list(groups.values())
    return {
        "decision_records": len(rows), "known_tool_boundary_records": len(known),
        "unknown_tool_boundary_records": len(rows) - len(known),
        "decisions_after_observed_tool_end": sum(
            row.get("tool_already_ended_on_harness") is True for row in rows
        ),
        "unique_known_tools": len(first),
        "records_actual_remaining_ge_2000ms": sum(
            row["actual_remaining_ge_2000ms"] for row in known
        ),
        "record_long_wait_precision": (
            sum(row["actual_remaining_ge_2000ms"] for row in known) / len(known)
            if known else None
        ),
        "tools_first_decision_remaining_ge_2000ms": sum(
            row["actual_remaining_ge_2000ms"] for row in first
        ),
        "first_decision_per_tool_long_wait_precision": (
            sum(row["actual_remaining_ge_2000ms"] for row in first) / len(first)
            if first else None
        ),
        "live_same_invocation_forecast_records": sum(
            row.get("forecast_live") is True
            and row.get("forecast_invocation_matches") is True for row in rows
        ),
        "runtime_long_wait_predicate_true": sum(
            row.get("runtime_long_wait_predicate") is True for row in rows
        ),
        "actual_remaining_ms": distribution(
            row.get("actual_remaining_ms") for row in rows
        ),
        "remaining_p50_ms": distribution(row.get("remaining_p50_ms") for row in rows),
        "p50_absolute_error_ms": distribution(
            row.get("p50_absolute_error_ms") for row in rows
        ),
        "tool_names": dict(Counter(row["tool_name"] for row in first)),
        "history_survival_groups": [
            {
                "support_detail": support, "surviving_points": points,
                "records": len(group),
                "records_actual_remaining_ge_2000ms": sum(
                    row["actual_remaining_ge_2000ms"] for row in group
                ),
            }
            for (support, points), group in sorted(
                support_groups.items(),
                key=lambda item: (str(item[0][0]), item[0][1] or 0),
            )
        ],
        "cdf_replay_on_selected_decisions": {
            "records_selected": sum(
                row.get("cdf_long_wait_predicate") is True for row in known
            ),
            "records_selected_actual_remaining_ge_2000ms": sum(
                row.get("cdf_long_wait_predicate") is True
                and row["actual_remaining_ge_2000ms"] for row in known
            ),
            "scope": "Subset of recorded decisions, not policy replay or opportunity recall.",
        },
    }


def audit(arm: Path) -> dict:
    seals = []
    windows, joins = read_tools(arm, seals)
    starts = {
        scope: [row["start_monotonic_ms"] for row in group]
        for scope, group in windows.items()
    }
    forecasts, prepared, decisions, demotions = {}, {}, defaultdict(list), []
    bursts, offset = set(), None
    forecast_count = 0
    opportunity_path = arm / "opportunities/admission_opportunities.jsonl"
    for row in sealed_records(opportunity_path, seals):
        kind = row["event"]
        if kind == "safe_point_census":
            offset = row["ts_ms"] - row["monotonic_ms"]
        elif kind == "tool_wait_forecast":
            forecasts[row["context_id"], row["context_epoch"]] = row
            forecast_count += 1
        elif kind == "prepare_native_issued":
            prepared[row["command_id"]] = row
            identity = tuple(row.get("burst_command_ids") or (row["command_id"],))
            if identity not in bursts:
                bursts.add(identity)
                if row["source"] == "tool_wait":
                    if offset is None:
                        raise ValueError("tool decision has no paired scheduler clock")
                    decisions["prepare"].append(tool_decision(
                        row, forecasts.get((row["context_id"], row["context_epoch"])),
                        offset, windows, starts,
                    ))
        elif kind == "parent_pressure_demoted":
            demotions.append(row)
            if row["source"] == "tool_wait":
                if offset is None:
                    raise ValueError("tool demotion has no paired scheduler clock")
                decision = tool_decision(
                    row, forecasts.get((row["context_id"], row["context_epoch"])),
                    offset, windows, starts,
                )
                decision["freed_units"] = row["freed_units"]
                decisions["demotion"].append(decision)

    acknowledgments = {
        row["command_id"]: row
        for row in sealed_records(arm / "server/physical_action_ack.jsonl", seals)
        if row["action"] == "PREPARE_HOST"
    }
    capacity_path = arm / "server/native_capacity_census.json"
    capacity_bytes = capacity_path.read_bytes()
    capacity = json.loads(capacity_bytes)["capacity"]
    seals.append({
        "path": str(capacity_path), "bytes": len(capacity_bytes),
        "sha256": hashlib.sha256(capacity_bytes).hexdigest(),
    })
    # Device Mamba allocation includes a reserved slot absent from usable size.
    full_unit = capacity["host_full_bytes"] // capacity["host_full_tokens"]
    mamba_unit = capacity["host_mamba_bytes"] // capacity["host_mamba_slots"]
    backups = {}
    for source in sorted({row["source"] for row in prepared.values()}):
        selected = [row for row in prepared.values() if row["source"] == source]
        acked = [acknowledgments[row["command_id"]] for row in selected
                 if row["command_id"] in acknowledgments]
        backups[source] = {
            "node_commands": len(selected), "acknowledged_commands": len(acked),
            "burst_decisions": len({
                tuple(row.get("burst_command_ids") or (row["command_id"],))
                for row in selected
            }),
            "unique_contexts": len({row["context_id"] for row in selected}),
            "context_epochs": len({
                (row["context_id"], row["context_epoch"]) for row in selected
            }),
            "actual_pool_bytes": {
                pool: sum(row["pool_bytes"].get(pool, 0) for row in acked)
                for pool in ("kv", "mamba")
            },
        }
    releases = {}
    for source in sorted({row["source"] for row in demotions}):
        selected = [row for row in demotions if row["source"] == source]
        full = sum(row["freed_units"].get("0", 0) for row in selected)
        mamba = sum(row["freed_units"].get("2", 0) for row in selected)
        releases[source] = {
            "node_demotion_events": len(selected),
            "events_releasing_positive_full": sum(
                row["freed_units"].get("0", 0) > 0 for row in selected
            ),
            "unique_contexts": len({row["context_id"] for row in selected}),
            "context_epochs": len({
                (row["context_id"], row["context_epoch"]) for row in selected
            }),
            "released_full_tokens": full, "released_full_bytes": full * full_unit,
            "released_mamba_slots": mamba, "released_mamba_bytes": mamba * mamba_unit,
        }
    observed_tools = [row for group in windows.values() for row in group]
    return {
        "arm": str(arm), "source_seals": seals, "units": {
            "full_bytes_per_token": full_unit, "mamba_bytes_per_slot": mamba_unit,
        },
        "summary": {
            "prepare_backups_by_source": backups, "hbm_demotion_by_source": releases,
            "tool_prepare_timing": decision_summary(decisions["prepare"]),
            "tool_demotion_timing": decision_summary(decisions["demotion"]),
            "observed_tool_starts_with_context": len(observed_tools),
            "observed_complete_tools_with_context": sum(
                row["end_monotonic_ms"] is not None for row in observed_tools
            ),
            "observed_tools_total_duration_ge_2000ms": sum(
                row["end_monotonic_ms"] is not None
                and row["end_monotonic_ms"] - row["start_monotonic_ms"] >= 2000.
                for row in observed_tools
            ),
            "observed_join_creates": len(joins),
            "observed_tool_forecasts": forecast_count,
        },
        "tool_prepare_decisions": decisions["prepare"],
        "tool_demotion_decisions": decisions["demotion"],
        "demotion_events": demotions,
        "semantics": (
            "PREPARE_HOST ACK is a Host shadow backup, not an HBM offload. "
            "Demotion records are per native node/pool release; repeated events "
            "and shared prefixes are not whole-agent offloads or unique bytes. "
            "Tool matching uses workflow/invocation plus the latest submitted "
            "context/epoch and exact tool_run_id. A decision requires exactly one "
            "tool active on the harness monotonic clock. When all tools in the "
            "same bounded context/epoch already ended, the latest end is retained "
            "as a nonpositive remaining-time label and a false long-wait decision, "
            "rather than dropping it from precision. Unclosed or ambiguous "
            "boundaries stay unknown. Event wall time is converted using the "
            "latest preceding paired scheduler census, so millisecond clock "
            "pairing uncertainty remains. Only preceding accepted forecasts enter "
            "reconstructed predictions; subsequent tool ends are labels only. "
            "The 2000ms precision measures whether selected waits remain open long "
            "enough, not RETURN timestamp accuracy, recall, benefit or performance. "
            "Independent-tool precision uses its first observed action in that "
            "category; per-node decision counts do not constitute independent "
            "samples. Long-tool total duration is only descriptive; it is not an "
            "opportunity denominator because pressure, residency, references, "
            "Host capacity and forecasting coverage are not continuously observed. "
            "Native untagged eviction and terminal child discard are separate. "
            "No unconditional post-spawn COMMIT_CPU is inferred from these events."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.arm.resolve())
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
