#!/usr/bin/env python3
"""Evaluate causal child/JOIN timing at early, late, and completed stages."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_native_stream_shadow import _rows, _satisfied_last_children


STAGES = (
    "content_1024", "content_1700", "content_2400", "content_3200",
    "content_4200", "content_5600", "content_7000", "result",
)
DELAYS_MS = {
    "content_1024": 2000, "content_1700": 250,
    "content_2400": 0, "content_3200": 0, "content_4200": 0,
    "content_5600": 0, "content_7000": 0, "result": 0,
}


def candidates(workflows: Path) -> dict[str, list[dict]]:
    output = {stage: [] for stage in STAGES}
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        events = sorted(
            _rows(path), key=lambda event: (
                float(event["ts_ms"]), int(event.get("sequence") or 0)
            )
        )
        groups = {
            event["join_id"]: (
                float(event["ts_ms"]), set(event["member_invocation_ids"])
            )
            for event in events
            if event["kind"] == "join_create"
            and (event.get("attributes") or {}).get("mode") == "all"
            and event.get("member_invocation_ids")
        }
        child_groups = {
            child: join for join, (_, members) in groups.items()
            for child in members
        }
        terminals = {
            event["join_id"]: (event["kind"], float(event["ts_ms"]))
            for event in events
            if event["kind"] in {"join_satisfied", "join_timeout"}
            and event.get("join_id")
        }
        returns = {
            event["invocation_id"]: float(event["ts_ms"])
            for event in events if event["kind"] == "return"
            and event.get("invocation_id")
        }
        canceled = {
            event["invocation_id"]: float(event["ts_ms"])
            for event in events if event["kind"] == "invocation_cancel"
            and event.get("invocation_id")
        }
        last_children = _satisfied_last_children(events)
        per_child = defaultdict(list)
        for event in events:
            if event.get("invocation_id"):
                per_child[event["invocation_id"]].append(event)
        for child, child_events in per_child.items():
            join = child_groups.get(child)
            if join is None:
                continue
            created, members = groups[join]
            terminal = terminals.get(join)
            chunks = {
                (event.get("attributes") or {}).get("request_id"): float(
                    event["ts_ms"]
                )
                for event in child_events
                if event["kind"] == "structured_action"
                and (event.get("attributes") or {}).get(
                    "beliefkv_child_first_tool_chunk_shadow"
                )
            }
            results = {
                (event.get("attributes") or {}).get("request_id"): event
                for event in child_events if event["kind"] == "llm_result"
            }
            for event in child_events:
                attrs = event.get("attributes") or {}
                stage = None
                if event["kind"] == "structured_action" and attrs.get(
                    "beliefkv_child_substantial_content_shadow"
                ):
                    chars = attrs.get("content_threshold_chars")
                    if chars in (1024, 1700, 2400, 3200, 4200, 5600, 7000):
                        stage = f"content_{chars}"
                elif event["kind"] == "llm_result":
                    stage = "result"
                    if (
                        attrs.get("runtime_internal")
                        or int(attrs.get("output_chars") or 0) == 0
                        or attrs.get("tool_call_count") != 0
                        or attrs.get("invalid_tool_call_count", 0) != 0
                        or attrs.get("finish_reason") not in (None, "stop")
                    ):
                        continue
                if stage is None:
                    continue
                request = attrs.get("request_id")
                result = results.get(request)
                if not request:
                    continue
                trigger = float(event["ts_ms"]) + DELAYS_MS[stage]
                if stage != "result" and (
                    result is not None and trigger >= float(result["ts_ms"])
                    or chunks.get(request, math.inf) <= trigger
                ):
                    continue
                if (
                    trigger < created
                    or terminal is not None and trigger >= terminal[1]
                    or returns.get(child, math.inf) <= trigger
                    or any(
                        (member != child and returns.get(member, math.inf) >= trigger)
                        or canceled.get(member, math.inf) <= trigger
                        for member in members
                    )
                ):
                    continue
                successor = next((
                    later for later in child_events
                    if result is not None
                    and float(later["ts_ms"]) > float(result["ts_ms"])
                    and later["kind"] in {
                        "return", "invocation_cancel", "llm_submit", "tool_start"
                    }
                ), None)
                row = {
                    "join": (str(path), join),
                    "child": child,
                    "trigger_ms": trigger,
                    "eligible_last_child": (
                        (event["workflow_id"], child) in last_children
                    ),
                }
                if successor is None:
                    canceled_after_trigger = result is None and any(
                        later["kind"] == "invocation_cancel"
                        and float(later["ts_ms"]) > trigger
                        for later in child_events
                    )
                    output[stage].append({
                        **row,
                        "final": False if canceled_after_trigger else None,
                        "lead_ms": None,
                    })
                    continue
                result_attrs = result.get("attributes") or {}
                final = (
                    successor["kind"] == "return"
                    and not result_attrs.get("runtime_internal")
                    and int(result_attrs.get("output_chars") or 0) > 0
                    and result_attrs.get("tool_call_count") == 0
                    and result_attrs.get("invalid_tool_call_count", 0) == 0
                    and result_attrs.get("finish_reason") in (None, "stop")
                    and terminal is not None
                    and terminal[0] == "join_satisfied"
                    and float(successor["ts_ms"]) <= terminal[1]
                )
                output[stage].append({
                    **row,
                    "final": final,
                    "lead_ms": (
                        float(successor["ts_ms"]) - trigger if final else None
                    ),
                })
    return {
        stage: list({
            row["join"]: row
            for row in sorted(rows, key=lambda item: item["trigger_ms"],
                              reverse=True)
        }.values())
        for stage, rows in output.items()
    }


def _quality(rows: list[dict], prior: float | None) -> dict:
    determined = [row for row in rows if row["final"] is not None]
    true = [row for row in rows if row["final"]]
    leads = [row["lead_ms"] for row in true]
    return {
        "join_candidates": len(rows),
        "determined_join_candidates": len(determined),
        "censored_join_candidates": len(rows) - len(determined),
        "true_next_return_join": len(true),
        "precision": len(true) / len(determined) if determined else None,
        "lead_p50_ms": median(leads) if leads else None,
        "lead_at_least_500ms": sum(lead >= 500 for lead in leads),
        "lead_at_least_2000ms": sum(lead >= 2000 for lead in leads),
        "eta_prior_ms": prior,
        "eta_error_p50_ms": (
            median(abs(lead - prior) for lead in leads)
            if leads and prior is not None else None
        ),
        "eta_within_500ms": (
            sum(abs(lead - prior) <= 500 for lead in leads)
            if prior is not None else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train = {stage: [] for stage in STAGES}
    for directory in args.train_workflows:
        for stage, rows in candidates(directory).items():
            train[stage].extend(rows)
    evaluation = candidates(args.evaluate_workflows)
    priors = {
        stage: median(row["lead_ms"] for row in rows if row["final"])
        if any(row["final"] for row in rows) else None
        for stage, rows in train.items()
    }
    report = {
        "status": "read_only_stage_eta_diagnostic_no_physical_h2d",
        "training": {
            stage: _quality(rows, priors[stage])
            for stage, rows in train.items()
        },
        "project_holdout": {
            stage: _quality(rows, priors[stage])
            for stage, rows in evaluation.items()
        },
        "limitation": (
            "First qualifying candidate per JOIN per stage. Completed-response "
            "ETA may be precise but can be too late to hide an H2D transfer."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
