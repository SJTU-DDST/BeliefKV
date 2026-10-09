#!/usr/bin/env python3
"""Diagnose recorded timing/residency costs; never simulate a new live trajectory."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_transfer_policy import transfer_start_window
from beliefkv.runtime.native_transfer_service import NativeServiceEstimate, percentile
from scripts.summarize_semantic_h2d_ab import records


def distribution(values: list[float]) -> dict:
    return {
        "count": len(values),
        "p50": median(values) if values else None,
        "p90": percentile(values, .9) if values else None,
        "median_absolute": median(map(abs, values)) if values else None,
    }


def audit(arm: Path) -> dict:
    labels_path = arm / "join_candidate_windows.json"
    labels = json.loads(labels_path.read_text())["rows"] if labels_path.exists() else []
    by_request = {row["final_request_id"]: row for row in labels}
    joins, lifecycle, forecasts = [], Counter(), {}
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        kind = row.get("event")
        if kind == "semantic_child_forecast":
            forecasts[row["request_id"]] = row
        elif kind == "prefetch_residency_released":
            lifecycle[f"released:{row.get('reason')}"] += 1
        elif kind == "prefetch_residency_registered":
            lifecycle["registered"] += 1
            lifecycle["native_locked"] += row.get("native_locked") is True
        elif kind == "parent_pressure_demoted":
            lifecycle[f"pressure_demoted:{row.get('source')}"] += 1
        elif kind == "final_stage_latest_start":
            target = by_request.get(row["child_request_id"])
            if target is None:
                lifecycle["trigger_without_return_label"] += 1
                continue
            # These service estimates were recorded at the decision. Future
            # RETURN/EOS labels are used only for error reporting below.
            estimate = NativeServiceEstimate(
                row["h2d_ms"], row["h2d_ms"],
                row.get("enqueue_to_submit_p90_ms"),
                row["service_sample_count"], row["service_support"],
            )
            window = transfer_start_window(
                estimate, max_lead_ms=row["prefetch_lead_ms"], observation_spacing_ms=100.,
            )
            return_lead = target["child_return_ts_ms"] - row["ts_ms"]
            eos_lead = target["native_eos_ts_ms"] - row["ts_ms"]
            forecast = forecasts.get(row["child_request_id"])
            advanced = (
                max(0, row["generated_tokens"] - forecast["observed_output_tokens"])
                if forecast is not None else None
            )
            joins.append({
                "join_id": row["join_id"],
                "request_id": row["child_request_id"],
                "trigger_kind": row.get("trigger_kind", "estimated_work"),
                "decision_ts_ms": row["ts_ms"],
                "estimated_remaining_ms": row["remaining_ms"],
                "lead_to_eos_ms": eos_lead, "lead_to_child_return_ms": return_lead,
                "error_against_eos_ms": row["remaining_ms"] - eos_lead,
                "error_against_return_ms": row["remaining_ms"] - return_lead,
                "recorded_h2d_p90_ms": row["h2d_ms"],
                "center_forecast_overtaken": (
                    forecast["remaining_tokens"] <= advanced if forecast is not None else None
                ),
                "upper_forecast_overtaken": (
                    forecast["upper_tokens"] <= advanced if forecast is not None else None
                ),
                "adaptive_start_window_ms": window.horizon_ms if window else None,
                "adaptive_policy_would_wait_at_this_snapshot": (
                    row["remaining_ms"] > window.horizon_ms if window else None
                ),
                "submit_queue_estimate_recorded": (
                    row.get("enqueue_to_submit_p90_ms") is not None
                ),
            })
    service = {"h2d": [], "d2h": []}
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        if (
            row.get("status") == "completed" and row.get("direction") in service
            and isinstance(row.get("submit_to_ack_ms"), (float, int))
        ):
            service[row["direction"]].append(row)
    uses = [
        row for row in records(arm / "server/physical_action_use.jsonl")
        if row.get("event") == "beliefkv_prefetch_first_service"
    ]
    predicted = [row for row in joins if row["trigger_kind"] == "estimated_work"]
    return {
        "scope": (
            "Existing live-trace diagnosis. The adaptive comparison checks the same "
            "recorded snapshot only; it does not predict a postponed trigger, "
            "new service timing, cache reuse or throughput."
        ),
        "limitations": [
            "Historical triggers without an enqueue estimate use zero extra submit delay.",
            "JOIN trigger snapshots are clustered by request and are not independent samples.",
            "The runtime phase/work model is unchanged; future EOS/RETURN labels are evaluation only.",
            "Transfer ACK and first service intervals are not exposed GPU stalls or an oracle speedup.",
        ],
        "source_arm": str(arm.resolve()),
        "join_trigger_count": len(joins),
        "join_trigger_request_count": len({row["request_id"] for row in joins}),
        "work_trigger_count": len(predicted),
        "work_trigger_overtaken_center_count": sum(
            row["center_forecast_overtaken"] is True for row in predicted
        ),
        "work_trigger_overtaken_upper_count": sum(
            row["upper_forecast_overtaken"] is True for row in predicted
        ),
        "work_error_against_eos_ms": distribution([
            row["error_against_eos_ms"] for row in predicted
        ]),
        "work_error_against_child_return_ms": distribution([
            row["error_against_return_ms"] for row in predicted
        ]),
        "work_trigger_lead_to_return_ms": distribution([
            row["lead_to_child_return_ms"] for row in predicted
        ]),
        "adaptive_window_would_wait_snapshot_count": sum(
            row["adaptive_policy_would_wait_at_this_snapshot"] is True for row in predicted
        ),
        "prefetch_ack_to_first_service_ms": distribution([
            row["first_service_ts_ms"] - row["ack_ts_ms"] for row in uses
            if isinstance(row.get("first_service_ts_ms"), (int, float))
            and isinstance(row.get("ack_ts_ms"), (int, float))
        ]),
        "confirmed_full_reuse_count": sum(
            row.get("full_node_reused") is True for row in uses
        ),
        "lifecycle_counts": dict(lifecycle),
        "native_service_ms": {
            direction: {
                "submit_to_ack": distribution([row["submit_to_ack_ms"] for row in rows]),
                "enqueue_to_submit": distribution([
                    row["enqueue_to_submit_ms"] for row in rows
                    if isinstance(row.get("enqueue_to_submit_ms"), (int, float))
                ]),
            } for direction, rows in service.items()
        },
        "join_trigger_rows": joins,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(
        {key: value for key, value in result.items() if key != "join_trigger_rows"},
        indent=2, allow_nan=False,
    ))


if __name__ == "__main__":
    main()
