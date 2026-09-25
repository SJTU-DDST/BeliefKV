"""Audit fully observed child terminal hints without issuing H2D."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
import math
from typing import Any


def _lead_summary(values: list[float]) -> dict[str, float | int | None]:
    values.sort()
    return {
        "count": len(values),
        "p50_ms": values[(len(values) - 1) // 2] if values else None,
        "p90_ms": values[math.ceil(.9 * len(values)) - 1] if values else None,
        "max_ms": values[-1] if values else None,
        "at_least_250ms": sum(item >= 250 for item in values),
        "at_least_500ms": sum(item >= 500 for item in values),
        "at_least_1000ms": sum(item >= 1000 for item in values),
    }


def summarize_child_terminal_signals(
    reentries: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    events = tuple(events)
    last_members: dict[tuple[str, str], float] = {}
    eligible_children: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for join in reentries:
        if (
            join.get("reentry_kind") != "join"
            or join.get("terminal_status") != "satisfied"
            or join.get("training_eligible") is not True
        ):
            continue
        members = join.get("member_outcomes") or ()
        if not members or any(
            member.get("invocation_id") is None
            or member.get("return_ts_ms") is None for member in members
        ):
            continue
        workflow = str(join.get("workflow_id") or "")
        if not workflow:
            continue
        last = max(members, key=lambda item: float(item["return_ts_ms"]))
        key = (workflow, str(last["invocation_id"]))
        last_members[key] = float(join["reentry_ts_ms"])
        eligible_children.update(
            (workflow, str(member["invocation_id"])) for member in members
        )
        counts["complete_joins"] += 1
    all_children = {
        (str(event.get("workflow_id") or ""), str(event.get("invocation_id") or ""))
        for event in events
        if event.get("kind") == "invocation_create"
        and event.get("relation_type") == "spawn"
    }
    by_child: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        key = (
            str(event.get("workflow_id") or ""),
            str(event.get("invocation_id") or ""),
        )
        if key in all_children or key in eligible_children:
            by_child[key].append(event)
    leads: dict[str, list[float]] = {
        "natural_final_to_last_child_return": [],
        "explicit_child_completion_to_last_child_return": [],
    }
    signaled_joins: set[tuple[str, str]] = set()
    for key, child_events in by_child.items():
        child_events.sort(key=lambda item: float(item.get("ts_ms") or 0))
        for index, event in enumerate(child_events):
            if event.get("kind") != "llm_result":
                continue
            attrs = event.get("attributes") or {}
            if attrs.get("runtime_internal") is True:
                continue
            if attrs.get("invalid_tool_call_count", 0) != 0:
                continue
            if attrs.get("finish_reason") not in (None, "stop"):
                continue
            names = attrs.get("structured_action_names")
            if names == ["ChildCompletion"]:
                category = "explicit_child_completion"
            elif (
                attrs.get("tool_call_count") == 0
                and not names
                and isinstance(attrs.get("output_chars"), int)
                and attrs["output_chars"] > 0
            ):
                category = "natural_final"
            else:
                continue
            if key in all_children:
                counts[f"all_spawn_{category}_candidate"] += 1
            if key in eligible_children:
                counts[f"{category}_candidate"] += 1
            subsequent = next((
                later
                for later in child_events[index + 1:]
                if later.get("kind") in {
                    "llm_submit", "tool_start", "return", "invocation_cancel"
                }
            ), None)
            if subsequent is None or subsequent.get("kind") != "return":
                if key in all_children:
                    counts[f"all_spawn_{category}_not_next_return"] += 1
                if key in eligible_children:
                    counts[f"{category}_not_next_return"] += 1
                continue
            if key in all_children:
                counts[f"all_spawn_{category}_followed_by_return"] += 1
            if key in eligible_children:
                counts[f"{category}_followed_by_return"] += 1
            if key not in last_members:
                continue
            signal_ts = float(event["ts_ms"])
            return_ts = float(subsequent["ts_ms"])
            if (
                not math.isfinite(signal_ts)
                or return_ts < signal_ts
                or not math.isclose(return_ts, last_members[key], abs_tol=1.0)
            ):
                counts["mismatched_join_return"] += 1
                continue
            leads[f"{category}_to_last_child_return"].append(return_ts - signal_ts)
            signaled_joins.add(key)
    return {
        "semantics": (
            "offline last-child LLM_RESULT to RETURN; next-event success is a "
            "retrospective check, not an online proof of terminal intent; older "
            "traces cannot audit invalid_tool_calls or finish_reason"
        ),
        "counts": dict(sorted(counts.items())),
        "all_spawn_child_count": len(all_children),
        "last_child_join_groups": len(last_members),
        "last_child_join_groups_with_signal": len(signaled_joins),
        "lead_distributions": {
            name: _lead_summary(values) for name, values in leads.items()
        },
    }
