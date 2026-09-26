#!/usr/bin/env python3
"""Measure opt-in child completion intent against natural RETURN and JOIN."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

try:
    from scripts.audit_child_hidden_trace import (
        blocked_child_invocations, index_workflow,
    )
except ModuleNotFoundError:
    from audit_child_hidden_trace import blocked_child_invocations, index_workflow


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
    }


def summarize(workflows: Path) -> dict:
    natural = announced = valid = invalidated = incomplete = 0
    repeat = nonterminal = 0
    early = on_time = late = 0
    natural_join_last = observed_join_last = 0
    leads = []
    join_leads = []
    projects = defaultdict(lambda: {"natural": 0, "valid_intents": 0})
    files = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not files:
        raise FileNotFoundError(f"missing workflow events in {workflows}")
    for event_file in files:
        root = event_file.parent
        project = root.name.split("__", 1)[0]
        with event_file.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream]
        blocked = blocked_child_invocations(root)
        terminals, join_last = index_workflow(
            events, blocked_invocations=blocked,
        )
        terminal_by_child = {
            invocation: (rid, returned_ms)
            for rid, (invocation, returned_ms) in terminals.items()
        }
        natural += len(terminal_by_child)
        natural_join_last += len(join_last)
        projects[project]["natural"] += len(terminal_by_child)
        if not (root / "sandbox_audit.jsonl").is_file():
            raise FileNotFoundError(f"missing sandbox audit for {root}")
        with (root / "sandbox_audit.jsonl").open(encoding="utf-8") as stream:
            audit = [json.loads(line) for line in stream]
        children = {
            row["target_invocation_id"]
            for row in events
            if row["kind"] == "spawn" and row.get("target_invocation_id")
        }
        signals = defaultdict(list)
        for row in audit:
            if row.get("event") == "child_return_intent_shadow":
                child = row.get("invocation_id")
                if child not in children:
                    raise ValueError(f"unbound child intent in {root}")
                signals[child].append(float(row["ts_ms"]))
        for child, times in signals.items():
            announced += 1
            repeat += len(times) - 1
            first = min(times)
            terminal = terminal_by_child.get(child)
            end = terminal[1] if terminal is not None else float("inf")
            later_tools = [
                row for row in events
                if row["kind"] == "tool_start"
                and row.get("invocation_id") == child
                and first < float(row["ts_ms"]) < end
                and (row.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
            ]
            if later_tools:
                invalidated += 1
                continue
            if terminal is None:
                if child in blocked or any(
                    row["kind"] == "invocation_cancel"
                    and row.get("invocation_id") == child
                    for row in events
                ):
                    nonterminal += 1
                else:
                    incomplete += 1
                continue
            lead = end - first
            if lead <= 0:
                invalidated += 1
                continue
            valid += 1
            projects[project]["valid_intents"] += 1
            leads.append(lead)
            if child in join_last:
                observed_join_last += 1
                join_leads.append(lead)
            if lead > 3000:
                early += 1
            elif lead < 500:
                late += 1
            else:
                on_time += 1
    return {
        "diagnostic_only": True,
        "workflow_count": len(files),
        "natural_child_returns": natural,
        "natural_join_last_children": natural_join_last,
        "children_with_intent": announced,
        "repeat_intents": repeat,
        "valid_natural_intents": valid,
        "natural_returns_without_valid_intent": natural - valid,
        "intent_invalidated_by_later_tool": invalidated,
        "intent_nonterminal_or_blocked": nonterminal,
        "intent_without_observed_terminal": incomplete,
        "return_lead_ms": _stats(leads),
        "join_last_intents": observed_join_last,
        "join_last_lead_ms": _stats(join_leads),
        "intents_over_3000ms": early,
        "intents_in_500_to_3000ms": on_time,
        "intents_under_500ms": late,
        "window_precision_conservative": (
            round(on_time / announced, 4) if announced else None
        ),
        "window_precision_observed": (
            round(on_time / (announced - incomplete), 4)
            if announced > incomplete else None
        ),
        "window_recall_of_natural_returns": (
            round(on_time / natural, 4) if natural else None
        ),
        "projects": dict(sorted(projects.items())),
        "scope": (
            "Opt-in shadow tool changes agent trajectory; first intent per child "
            "is revoked by later non-intent tool use. No physical migration."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.workflows)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
