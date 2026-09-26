#!/usr/bin/env python3
"""Audit first child completion notice and first live final chunk as one chain."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
        "p50_ms": float(np.median(values)),
        "p90_ms": float(np.percentile(values, 90)),
        "at_least_500ms": sum(value >= 500 for value in values),
    }


def audit(workflows: Path) -> dict:
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise FileNotFoundError(f"missing workflow events in {workflows}")
    counts = Counter(workflows=len(paths))
    notice_leads, chunk_leads, join_notice, join_chunk = [], [], [], []
    for path in paths:
        workflow = path.parent
        events = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        audit_path = workflow / "sandbox_audit.jsonl"
        if not audit_path.is_file():
            raise FileNotFoundError(f"missing sandbox audit for {workflow}")
        notices = defaultdict(list)
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "child_return_intent_shadow":
                notices[row["invocation_id"]].append(row)
        blocked = blocked_child_invocations(workflow)
        terminals, join_last = index_workflow(
            events, blocked_invocations=blocked,
        )
        terminal_by_child = {
            child: (rid, float(ts)) for rid, (child, ts) in terminals.items()
        }
        children = {
            row["target_invocation_id"]
            for row in events
            if row["kind"] == "spawn" and row.get("target_invocation_id")
        }
        counts["natural_returns"] += len(terminal_by_child)
        counts["natural_join_last_children"] += len(join_last)
        by_child = defaultdict(list)
        for row in events:
            if row.get("invocation_id") in children:
                by_child[row["invocation_id"]].append(row)
        for child, entries in notices.items():
            if child not in children:
                raise ValueError(f"unbound child notice in {workflow}")
            counts["children_with_notice"] += 1
            counts["repeat_notices"] += len(entries) - 1
            first_notice = min(entries, key=lambda row: float(row["ts_ms"]))
            first = float(first_notice["ts_ms"])
            terminal = terminal_by_child.get(child)
            finish = terminal[1] if terminal else float("inf")
            observed = by_child[child]
            if any(
                row["kind"] == "tool_start"
                and (row.get("attributes") or {}).get("tool_name")
                != "announce_completion_intent"
                and first < float(row["ts_ms"]) < finish
                for row in observed
            ):
                counts["notice_revoked_by_later_tool"] += 1
                continue
            if terminal is None or terminal[1] <= first:
                if child in blocked or any(
                    row["kind"] == "invocation_cancel"
                    and float(row["ts_ms"]) > first for row in observed
                ):
                    counts["notice_nonterminal_or_blocked"] += 1
                else:
                    counts["notice_censored"] += 1
                continue
            prior_identity = sorted((
                float(row["ts_ms"]), row["context_id"], row["context_epoch"],
            )
                for row in observed
                if float(row["ts_ms"]) <= first
                and row.get("context_id") is not None
                and row.get("context_epoch") is not None
            )
            if not prior_identity:
                counts["notice_missing_source_epoch"] += 1
                continue
            expected = prior_identity[-1][1:]
            if first_notice.get("context_id") is not None and (
                first_notice.get("context_id"), first_notice.get("context_epoch")
            ) != expected:
                counts["notice_source_identity_mismatch"] += 1
                continue
            counts["valid_natural_notices"] += 1
            notice_leads.append(terminal[1] - first)
            if child in join_last:
                join_notice.append(terminal[1] - first)
            candidates = sorted(
                (row for row in observed
                 if row["kind"] == "structured_action"
                 and (row.get("attributes") or {}).get(
                     "beliefkv_child_final_chunk_shadow"
                 )
                 and first < float(row["ts_ms"]) < terminal[1]),
                key=lambda row: float(row["ts_ms"]),
            )
            if not candidates:
                counts["valid_notice_without_final_chunk"] += 1
                continue
            chunk = candidates[0]
            rid = (chunk.get("attributes") or {}).get("request_id")
            chunk_ts = float(chunk["ts_ms"])
            successors = [
                row for row in observed
                if row["kind"] == "llm_submit"
                and not (row.get("attributes") or {}).get("runtime_internal")
                and first < float(row["ts_ms"]) <= chunk_ts
            ]
            if (
                len(successors) != 1
                or successors[0].get("context_id") != expected[0]
                or successors[0].get("context_epoch") != expected[1] + 1
                or (successors[0].get("attributes") or {}).get("request_id") != rid
                or (chunk.get("context_id"), chunk.get("context_epoch"))
                != (successors[0].get("context_id"), successors[0].get("context_epoch"))
            ):
                counts["first_chunk_successor_identity_mismatch"] += 1
                continue
            matches = [
                row for row in observed
                if row["kind"] == "llm_result"
                and (row.get("attributes") or {}).get("request_id") == rid
                and not (row.get("attributes") or {}).get("runtime_internal")
            ]
            if len(matches) != 1:
                counts["first_chunk_missing_or_ambiguous_result"] += 1
                continue
            result = matches[0]
            attrs = result.get("attributes") or {}
            if (
                rid is None
                or (result.get("context_id"), result.get("context_epoch"))
                != (successors[0].get("context_id"), successors[0].get("context_epoch"))
                or not isinstance(attrs.get("stream_final_chunk_ts_ms"), (int, float))
                or abs(attrs["stream_final_chunk_ts_ms"] - chunk_ts) > 1
                or chunk_ts > float(result["ts_ms"])
                or any(
                    row["kind"] == "structured_action"
                    and (row.get("attributes") or {}).get(
                        "beliefkv_child_first_tool_chunk_shadow"
                    )
                    and (row.get("attributes") or {}).get("request_id") == rid
                    and float(row["ts_ms"]) <= float(result["ts_ms"])
                    for row in observed
                )
            ):
                counts["first_chunk_invalid_signal"] += 1
                continue
            if rid != terminal[0]:
                counts["first_chunk_not_natural_terminal"] += 1
                continue
            lead = terminal[1] - chunk_ts
            counts["first_chunk_confirmed_return"] += 1
            chunk_leads.append(lead)
            if child in join_last:
                join_chunk.append(lead)
            if lead <= 500:
                counts["first_chunk_zero_eta_within_500ms"] += 1
    return {
        "diagnostic_only": True,
        "counts": dict(counts),
        "notice_to_natural_return": _stats(notice_leads),
        "first_chunk_to_natural_return": _stats(chunk_leads),
        "join_last_notice_to_return": _stats(join_notice),
        "join_last_first_chunk_to_return": _stats(join_chunk),
        "scope": (
            "First notice and first subsequent final-chunk signal per child; "
            "later non-intent tool revokes notice. Future final result is only "
            "a label. No physical H2D or claim of a sealed test."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(audit(args.workflows), indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
