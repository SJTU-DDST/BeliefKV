#!/usr/bin/env python3
"""Audit streamed first-content cues against the next completed child response."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1]


def _satisfied_last_children(events: list[dict]) -> set[tuple[str, str]]:
    returns = {
        event["invocation_id"]: float(event["ts_ms"])
        for event in events
        if event.get("kind") == "return" and event.get("invocation_id")
    }
    groups = {
        event["join_id"]: event
        for event in events
        if event.get("kind") == "join_create"
        and event.get("join_id")
        and (event.get("attributes") or {}).get("mode") == "all"
        and event.get("member_invocation_ids")
    }
    result = set()
    for event in events:
        if event.get("kind") != "join_satisfied":
            continue
        group = groups.get(event.get("join_id"))
        if group is None:
            continue
        members = group["member_invocation_ids"]
        if any(member not in returns for member in members):
            continue
        last = max(members, key=returns.__getitem__)
        if abs(returns[last] - float(event["ts_ms"])) <= 1:
            result.add((event["workflow_id"], last))
    return result


def audit(
    workflows: Path,
    dataset: Path | None = None,
    *,
    cue: str = "first_content",
    content_threshold_chars: int = 64,
    project_prefix: str | None = None,
) -> dict:
    if cue not in {"first_content", "substantial_content", "final_marker"}:
        raise ValueError("unsupported stream cue")
    if content_threshold_chars < 1:
        raise ValueError("content threshold must be positive")
    cue_attribute = {
        "first_content": "beliefkv_child_first_content_shadow",
        "substantial_content": "beliefkv_child_substantial_content_shadow",
        "final_marker": "beliefkv_child_final_marker_shadow",
    }[cue]
    last_children = set()
    if dataset is not None:
        for row in _rows(dataset / "reentries.jsonl"):
            if (
                row.get("reentry_kind") != "join"
                or row.get("terminal_status") != "satisfied"
                or row.get("training_eligible") is not True
            ):
                continue
            members = row.get("member_outcomes") or ()
            if not members or any(
                member.get("return_ts_ms") is None for member in members
            ):
                continue
            last = max(members, key=lambda member: float(member["return_ts_ms"]))
            last_children.add((row["workflow_id"], last["invocation_id"]))

    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if project_prefix:
        paths = [path for path in paths if path.parent.name.startswith(project_prefix)]
    if not paths:
        raise ValueError(f"no workflow event traces in {workflows}")
    by_child = defaultdict(list)
    for path in paths:
        workflow_events = list(_rows(path))
        if dataset is None:
            last_children.update(_satisfied_last_children(workflow_events))
        for event in workflow_events:
            if event.get("invocation_id"):
                by_child[(event["workflow_id"], event["invocation_id"])].append(event)
    cues = positives = negatives = unknown = joined = 0
    returned_children = set()
    marked_children = set()
    timer_thresholds = (250, 500, 750, 1000, 1250, 1500, 2000, 2500)
    timers = {
        delay: {"triggered": 0, "true_returns": 0, "false_triggers": 0,
                "return_leads_ms": [], "first_by_child": {}}
        for delay in timer_thresholds
    }
    leads, final_result_leads, joined_leads = [], [], []
    for child, events in by_child.items():
        events.sort(key=lambda event: float(event["ts_ms"]))
        if any(event.get("kind") == "invocation_create"
               and event.get("relation_type") == "spawn" for event in events) and any(
            event.get("kind") == "return" for event in events
        ):
            returned_children.add(child)
        for index, event in enumerate(events):
            cue_attrs = event.get("attributes") or {}
            if not cue_attrs.get(cue_attribute) or (
                cue == "substantial_content"
                and cue_attrs.get("content_threshold_chars")
                != content_threshold_chars
            ):
                continue
            cues += 1
            request_id = event["attributes"].get("request_id")
            result = next((
                later for later in events[index + 1:]
                if later.get("kind") == "llm_result"
                and (later.get("attributes") or {}).get("request_id") == request_id
            ), None)
            if result is None:
                unknown += 1
                continue
            tool_chunk = next((
                later for later in events
                if later.get("kind") == "structured_action"
                and (later.get("attributes") or {}).get(
                    "beliefkv_child_first_tool_chunk_shadow"
                )
                and (later.get("attributes") or {}).get("request_id") == request_id
                and float(later["ts_ms"]) <= float(result["ts_ms"])
            ), None)
            attrs = result.get("attributes") or {}
            successor = next((
                later for later in events
                if float(later["ts_ms"]) > float(result["ts_ms"])
                and later.get("kind") in {
                    "return", "invocation_cancel", "llm_submit", "tool_start"
                }
            ), None)
            if successor is None:
                unknown += 1
                continue
            is_final = (
                successor.get("kind") == "return"
                and attrs.get("runtime_internal") is not True
                and int(attrs.get("output_chars") or 0) > 0
                and attrs.get("tool_call_count") == 0
                and attrs.get("invalid_tool_call_count", 0) == 0
                and attrs.get("finish_reason") in (None, "stop")
            )
            if cue == "first_content":
                for delay, timer in timers.items():
                    trigger_ts = float(event["ts_ms"]) + delay
                    if (
                        trigger_ts >= float(result["ts_ms"])
                        or tool_chunk is not None
                        and float(tool_chunk["ts_ms"]) <= trigger_ts
                    ):
                        continue
                    timer["triggered"] += 1
                    timer["first_by_child"].setdefault(
                        child,
                        (
                            is_final,
                            float(successor["ts_ms"]) - trigger_ts
                            if is_final else None,
                        ),
                    )
                    if is_final:
                        timer["true_returns"] += 1
                        timer["return_leads_ms"].append(
                            float(successor["ts_ms"]) - trigger_ts
                        )
                    else:
                        timer["false_triggers"] += 1
            if is_final:
                positives += 1
                marked_children.add(child)
                lead = float(successor["ts_ms"]) - float(event["ts_ms"])
                leads.append(lead)
                final_result_leads.append(
                    float(successor["ts_ms"]) - float(result["ts_ms"])
                )
                if child in last_children:
                    joined += 1
                    joined_leads.append(lead)
            else:
                negatives += 1
    timer_results = {
        str(delay): {
            "triggered": values["triggered"],
            "true_returns": values["true_returns"],
            "false_triggers": values["false_triggers"],
            "precision": (
                values["true_returns"] / values["triggered"]
                if values["triggered"] else None
            ),
            "true_return_lead_p50_ms": _quantile(
                values["return_leads_ms"], .5
            ),
            "first_triggered_children": len(values["first_by_child"]),
            "first_trigger_true": sum(
                predicted for predicted, _ in values["first_by_child"].values()
            ),
            "first_trigger_precision": (
                sum(predicted for predicted, _ in values["first_by_child"].values())
                / len(values["first_by_child"])
                if values["first_by_child"] else None
            ),
            "first_trigger_true_lead_p50_ms": _quantile(
                [lead for predicted, lead in values["first_by_child"].values()
                 if predicted and lead is not None],
                .5,
            ),
        }
        for delay, values in timers.items()
    }
    return {
        "project_prefix": project_prefix,
        "cue": cue,
        "content_threshold_chars": (
            content_threshold_chars if cue == "substantial_content" else None
        ),
        "cues": cues,
        "confirmed_final_return": positives,
        "confirmed_not_final": negatives,
        "unknown": unknown,
        "precision_on_known_outcomes": (
            positives / (positives + negatives)
            if positives + negatives else None
        ),
        "joined_last_child_positive": joined,
        "returned_spawned_children": len(returned_children),
        "returned_child_coverage": (
            len(marked_children & returned_children) / len(returned_children)
            if returned_children else None
        ),
        "lead_ms": {
            "p10": _quantile(leads, .1), "p50": _quantile(leads, .5),
            "p90": _quantile(leads, .9),
        },
        "join_last_child_lead_ms": {
            "p10": _quantile(joined_leads, .1),
            "p50": _quantile(joined_leads, .5),
            "p90": _quantile(joined_leads, .9),
        },
        "complete_response_lead_p50_ms": _quantile(final_result_leads, .5),
        "first_content_timer_shadow": (
            timer_results if cue == "first_content" else None
        ),
        "join_cohort_available": dataset is not None,
        "status": "offline_stream_shadow_diagnostic_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--cue", choices=(
        "first_content", "substantial_content", "final_marker",
    ),
                        default="first_content")
    parser.add_argument("--project-prefix")
    parser.add_argument("--content-threshold-chars", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(
        args.workflows, args.dataset, cue=args.cue,
        content_threshold_chars=args.content_threshold_chars,
        project_prefix=args.project_prefix,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
