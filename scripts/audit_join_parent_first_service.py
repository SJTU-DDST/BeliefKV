#!/usr/bin/env python3
"""Audit JOIN notices against the parent's first GPU service, without clock mixing."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_join_group_notice import collect


WINDOWS_MS = (500, 1000, 2000, 5000)


def candidate_parent_requests(
    workflows: Path, groups: list[dict],
) -> tuple[list[dict], dict]:
    by_task = defaultdict(list)
    for row in groups:
        if row["label"] == "natural" and row["parent_reentry_lead_ms"] is not None:
            by_task[row["task_id"]].append(row)
    matched = []
    counts = Counter()
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        targets = by_task[path.parent.name]
        if not targets:
            continue
        submits = []
        waiters = defaultdict(set)
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = orjson.loads(line)
                if event.get("kind") == "join_wait" and event.get("join_id"):
                    waiters[event["join_id"]].add(event.get("invocation_id"))
                if event.get("kind") != "llm_submit":
                    continue
                attrs = event.get("attributes") or {}
                if not attrs.get("runtime_internal") and attrs.get("request_id"):
                    submits.append(event)
        for row in targets:
            parents = waiters[row["join_id"]] - {None}
            if len(parents) != 1:
                counts["missing_or_ambiguous_parent_invocation"] += 1
                continue
            parent = next(iter(parents))
            target = row["trigger_ts_ms"] + row["parent_reentry_lead_ms"]
            candidates = [
                event for event in submits
                if event.get("invocation_id") == parent
                and abs(float(event["ts_ms"]) - target) < .5
            ]
            if len(candidates) != 1:
                counts["missing_or_ambiguous_parent_submit"] += 1
                continue
            matched.append({
                **row,
                "parent_request_id": candidates[0]["attributes"]["request_id"],
            })
    return matched, dict(counts)


def server_service_offsets(
    server_events: Path, service_audit: Path, request_ids: set[str],
) -> tuple[dict[str, float], dict]:
    boundaries = defaultdict(dict)
    with server_events.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = orjson.loads(line)
            rid = (event.get("attributes") or {}).get("request_id")
            kind = event.get("kind")
            if rid not in request_ids or kind not in ("llm_submit", "llm_result"):
                continue
            if kind in boundaries[rid]:
                raise ValueError(f"duplicate server {kind} for {rid}")
            boundaries[rid][kind] = float(event["ts_ms"])
    first = {}
    with service_audit.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = orjson.loads(line)
            if event.get("event") != "gpu_service_sample":
                continue
            ts = float(event["ts_ms"])
            for sample in event.get("request_samples") or ():
                rid = sample.get("request_id")
                if rid in request_ids:
                    first[rid] = min(first.get(rid, ts), ts)
    offsets = {}
    skipped = Counter()
    for rid in request_ids:
        boundary = boundaries[rid]
        if not all(key in boundary for key in ("llm_submit", "llm_result")):
            skipped["missing_server_boundary"] += 1
            continue
        if rid not in first:
            skipped["missing_service_sample"] += 1
            continue
        submit, result = boundary["llm_submit"], boundary["llm_result"]
        if not submit <= first[rid] <= result:
            skipped["service_outside_request"] += 1
            continue
        offsets[rid] = first[rid] - submit
    return offsets, dict(skipped)


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"groups": 0}
    service = [row["parent_service_lead_ms"] for row in rows]
    submit = [row["parent_reentry_lead_ms"] for row in rows]
    return {
        "groups": len(rows),
        "tasks": len({row["task_id"] for row in rows}),
        "join_lead_p50_ms": _quantile([row["lead_ms"] for row in rows], .5),
        "parent_submit_lead_p50_ms": _quantile(submit, .5),
        "parent_service_lead_p50_ms": _quantile(service, .5),
        "parent_service_lead_p90_ms": _quantile(service, .9),
        "submit_to_service_p50_ms": _quantile([
            row["submit_to_service_ms"] for row in rows
        ], .5),
        "service_window_count": {
            str(window): sum(value >= window for value in service)
            for window in WINDOWS_MS
        },
        "join_window_count": {
            str(window): sum(row["lead_ms"] >= window for row in rows)
            for window in WINDOWS_MS
        },
    }


def audit_batch(workloads: Path) -> dict:
    frozen, errors = require_complete_batch(workloads / "workflows")
    groups = {}
    candidates = {}
    request_ids = set()
    for source in ("shadow", "llm_result"):
        rows, counts = collect(workloads / "workflows", notice_source=source)
        matched, excluded = candidate_parent_requests(
            workloads / "workflows", rows,
        )
        groups[source] = counts
        candidates[source] = matched
        request_ids.update(row["parent_request_id"] for row in matched)
        groups[source]["parent_request_excluded"] = excluded
    offsets, skipped = server_service_offsets(
        workloads.parent / "server/runtime_events.sglang.jsonl",
        workloads.parent / "server/runtime_audit.jsonl",
        request_ids,
    )
    indexed = {}
    reports = {}
    for source, rows in candidates.items():
        eligible = []
        for row in rows:
            rid = row["parent_request_id"]
            if rid not in offsets:
                continue
            matched = {
                **row,
                "submit_to_service_ms": offsets[rid],
                "parent_service_lead_ms": row["parent_reentry_lead_ms"] + offsets[rid],
            }
            key = row["task_id"], row["join_id"]
            if key in indexed.get(source, {}):
                raise ValueError(f"duplicate whole-JOIN candidate {key}")
            indexed.setdefault(source, {})[key] = matched
            eligible.append(matched)
        reports[source] = {
            "candidate_groups": len(rows),
            "matched": summarize(eligible),
            "matched_evidence": [{
                key: row[key] for key in (
                    "project", "task_id", "join_id", "trigger_ts_ms",
                    "lead_ms", "parent_reentry_lead_ms",
                    "parent_service_lead_ms", "submit_to_service_ms",
                )
            } for row in eligible],
            "by_project": {
                project: summarize([
                    item for item in eligible if item["project"] == project
                ])
                for project in sorted({task.split("__", 1)[0] for task in frozen})
            },
            "notice_counts": groups[source],
        }
    common = set(indexed.get("shadow", {})) & set(indexed.get("llm_result", {}))
    paired = []
    for key in sorted(common):
        early = indexed["shadow"][key]
        terminal = indexed["llm_result"][key]
        if early["parent_request_id"] != terminal["parent_request_id"]:
            raise ValueError(f"JOIN signals matched different parent requests: {key}")
        if early["trigger_ts_ms"] > terminal["trigger_ts_ms"]:
            raise ValueError(f"JOIN terminal signal precedes early notice: {key}")
        paired.append({
            "early": early,
            "terminal": terminal,
            "delta_ms": terminal["trigger_ts_ms"] - early["trigger_ts_ms"],
        })
    return {
        "status": "posthoc_parent_first_service_not_action_eligible",
        "workflows": len(frozen),
        "runner_errors": errors,
        "sources": reports,
        "server_service_excluded": skipped,
        "paired_groups": len(paired),
        "paired_early_service_ge_500ms": sum(
            row["early"]["parent_service_lead_ms"] >= 500 for row in paired
        ),
        "paired_terminal_service_ge_500ms": sum(
            row["terminal"]["parent_service_lead_ms"] >= 500 for row in paired
        ),
        "scope": (
            "Only naturally completed whole-JOIN groups with unique first "
            "parent submit and measured GPU service are scored. Client "
            "trigger-to-submit and server submit-to-service are separate "
            "same-clock intervals; only their durations are added. Service "
            "is a posthoc label, not a signal available at the notice. "
            "Window existence does not prove H2D can fit, execute, or help."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workloads", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit_batch(args.workloads)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
