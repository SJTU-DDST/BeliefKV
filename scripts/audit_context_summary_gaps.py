#!/usr/bin/env python3
"""Attribute client completion-to-submit gaps to internal summary request spans."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics


KINDS = frozenset({
    "invocation_create", "llm_submit", "llm_result", "tool_start", "tool_end",
    "join_create", "join_wait", "join_satisfied", "join_timeout", "context_compact",
    "return", "invocation_cancel",
})


def distribution(values) -> dict:
    values = list(values)
    if not values:
        return {"count": 0}
    return {
        "count": len(values), "sum": sum(values),
        "p50": float(statistics.median(values)),
        "p90": float(
            statistics.quantiles(values, n=10, method="inclusive")[8]
            if len(values) > 1 else values[0]
        ),
        "min": min(values), "max": max(values),
    }


def interval_union_ms(intervals, left: float, right: float) -> float:
    """Measure intersections once, including overlapping retry/request spans."""
    clipped = sorted(
        (max(left, start), min(right, end))
        for start, end in intervals if end > left and start < right
    )
    total, cursor = 0., left
    for start, end in clipped:
        if end > max(cursor, start):
            total += end - max(cursor, start)
        cursor = max(cursor, end)
    return total


def _unique(events: list[dict], field: str) -> dict:
    result = {}
    for event in events:
        key = event.get(field)
        if key is None or key in result:
            raise ValueError(f"missing or duplicate {field}: {key!r}")
        result[key] = event
    return result


def analyze_workflow(events: list[dict], *, workflow_path: str) -> dict:
    events = sorted(events, key=lambda row: (row["ts_ms"], row["sequence"]))
    workflow_ids = {event["workflow_id"] for event in events}
    if len(workflow_ids) != 1:
        raise ValueError("a workflow source must contain exactly one workflow identity")
    workflow = next(iter(workflow_ids))
    creates = _unique(
        [event for event in events if event["kind"] == "invocation_create"],
        "invocation_id",
    )
    submissions, results, tool_ends = {}, {}, {}
    ordinary = defaultdict(list)
    compactions = defaultdict(list)
    tool_starts = defaultdict(list)
    join_waits = _unique(
        [event for event in events if event["kind"] == "join_wait"], "join_id",
    )
    join_ends = _unique(
        [event for event in events if event["kind"] in ("join_satisfied", "join_timeout")],
        "join_id",
    )
    joins_by_parent = defaultdict(list)
    for join_id, wait in join_waits.items():
        joins_by_parent[wait.get("invocation_id")].append((wait, join_ends.get(join_id)))
    for event in events:
        attrs = event.get("attributes") or {}
        kind, invocation = event["kind"], event.get("invocation_id")
        if kind in ("llm_submit", "llm_result"):
            request = attrs.get("request_id")
            target = submissions if kind == "llm_submit" else results
            if not request or request in target:
                raise ValueError(f"missing or duplicate {kind} request: {request!r}")
            target[request] = event
            if kind == "llm_submit" and not attrs.get("runtime_internal"):
                ordinary[invocation].append(event)
        elif kind == "tool_start":
            tool_starts[invocation].append(event)
        elif kind == "tool_end":
            tool_id = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            key = invocation, tool_id
            if not tool_id or key in tool_ends:
                raise ValueError(f"missing or duplicate tool completion: {key!r}")
            tool_ends[key] = event
        elif kind == "context_compact":
            compactions[invocation].append(event)

    summaries = []
    summary_invocations_with_request = set()
    for request, submit in submissions.items():
        attrs = submit.get("attributes") or {}
        if not attrs.get("runtime_internal"):
            continue
        invocation = submit.get("invocation_id")
        create = creates.get(invocation, {})
        summary_invocations_with_request.add(invocation)
        parent = create.get("parent_invocation_id")
        result = results.get(request)
        row = {
            "workflow_id": workflow, "workflow_path": workflow_path,
            "invocation_id": invocation, "parent_invocation_id": parent,
            "request_id": request, "create_ts_ms": create.get("ts_ms"),
            "submit_ts_ms": submit["ts_ms"],
            "result_ts_ms": result["ts_ms"] if result else None,
            "prompt_chars": attrs.get("prompt_chars"),
            "status": "complete" if result else "missing_result",
        }
        if create.get("agent_definition_id") != "context-summarizer" or parent is None:
            row["status"] = "unproven_summary_parent"
        elif result is not None and (
            result["ts_ms"] < submit["ts_ms"]
            or result.get("invocation_id") != invocation
        ):
            row["status"] = "invalid_result_identity_or_order"
        result_attrs = (result or {}).get("attributes") or {}
        row.update(
            finish_reason=result_attrs.get("finish_reason"),
            output_chars=result_attrs.get("output_chars"),
            empty_length=(
                result_attrs.get("finish_reason") == "length"
                and result_attrs.get("output_chars") == 0
            ),
            model_request_span_ms=(
                result["ts_ms"] - submit["ts_ms"] if row["status"] == "complete" else None
            ),
        )
        summaries.append(row)
    for invocation, create in creates.items():
        if (
            create.get("agent_definition_id") == "context-summarizer"
            and invocation not in summary_invocations_with_request
        ):
            summaries.append({
                "workflow_id": workflow, "workflow_path": workflow_path,
                "invocation_id": invocation,
                "parent_invocation_id": create.get("parent_invocation_id"),
                "request_id": None, "create_ts_ms": create["ts_ms"],
                "submit_ts_ms": None, "result_ts_ms": None,
                "status": "missing_submit", "empty_length": False,
                "model_request_span_ms": None,
            })
    by_parent = defaultdict(list)
    for summary in summaries:
        by_parent[summary["parent_invocation_id"]].append(summary)
    times = {
        invocation: [event["ts_ms"] for event in submits]
        for invocation, submits in ordinary.items()
    }

    def gap_row(source, invocation, completion, following, **extra):
        row = {
            "workflow_id": workflow, "workflow_path": workflow_path, "source": source,
            "invocation_id": invocation, "completion_ts_ms": completion,
            "next_request_id": (
                following["attributes"]["request_id"] if following is not None else None
            ),
            "client_submit_ts_ms": following["ts_ms"] if following is not None else None,
            **extra,
        }
        if following is None:
            return {**row, "status": "no_next_ordinary_submit"}
        if completion is None or completion > following["ts_ms"]:
            return {**row, "status": "unproven_completion"}
        right = following["ts_ms"]
        candidates = [
            summary for summary in by_parent.get(invocation, ())
            if (
                summary["submit_ts_ms"]
                if summary["submit_ts_ms"] is not None else summary["create_ts_ms"]
            ) < right
            and (summary["result_ts_ms"] is None or summary["result_ts_ms"] > completion)
        ]
        known = [summary for summary in candidates if summary["status"] == "complete"]
        overlap = interval_union_ms(
            [(summary["submit_ts_ms"], summary["result_ts_ms"]) for summary in known],
            completion, right,
        )
        unknown = len(candidates) - len(known)
        duration = right - completion
        join_intervals, unknown_joins = [], 0
        for wait, end in joins_by_parent.get(invocation, ()):
            if wait["ts_ms"] < right and (end is None or end["ts_ms"] > completion):
                if end is not None and end["ts_ms"] >= wait["ts_ms"]:
                    join_intervals.append((wait["ts_ms"], end["ts_ms"]))
                else:
                    unknown_joins += 1
        compacts = [
            event for event in compactions.get(invocation, ())
            if completion <= event["ts_ms"] <= right
        ]
        row.update({
            "status": "measured",
            "gap_ms": duration, "summary_request_ids": [
                summary["request_id"] for summary in candidates
            ],
            "summary_request_count": len(candidates),
            "summary_requests_with_unknown_span": unknown,
            "empty_length_summary_requests": sum(
                summary["empty_length"] for summary in candidates
            ),
            "summary_span_overlap_lower_bound_ms": overlap,
            "summary_span_overlap_ms": overlap if unknown == 0 else None,
            "outside_summary_request_spans_ms": duration - overlap if unknown == 0 else None,
            "blocking_join_overlap_ms": (
                interval_union_ms(join_intervals, completion, right)
                if unknown_joins == 0 else None
            ),
            "outside_summary_and_join_spans_ms": (
                duration - interval_union_ms(
                    join_intervals + [
                        (summary["submit_ts_ms"], summary["result_ts_ms"])
                        for summary in known
                    ], completion, right,
                ) if unknown == 0 and unknown_joins == 0 else None
            ),
            "context_compactions_in_gap": [
                {"ts_ms": event["ts_ms"], "attributes": event["attributes"]}
                for event in compacts
            ],
        })
        if known and unknown == 0:
            first = max(completion, min(summary["submit_ts_ms"] for summary in known))
            last = min(right, max(summary["result_ts_ms"] for summary in known))
            row.update(
                completion_to_first_summary_submit_ms=first - completion,
                between_summary_request_spans_ms=max(0., last - first - overlap),
                last_summary_result_to_next_submit_ms=right - last,
            )
            compact = next((event for event in compacts if event["ts_ms"] >= last), None)
            if compact is not None:
                row.update(
                    last_summary_result_to_compaction_ms=compact["ts_ms"] - last,
                    compaction_to_next_submit_ms=right - compact["ts_ms"],
                )
        return row

    gaps = []
    for invocation, starts in tool_starts.items():
        submits = ordinary.get(invocation, ())
        submit_times = times.get(invocation, ())
        groups = defaultdict(list)
        for start in starts:
            groups[bisect_right(submit_times, start["ts_ms"])].append(start)
        for index, tools in sorted(groups.items()):
            tool_ids = [
                event["attributes"].get("tool_run_id")
                or event["attributes"].get("tool_call_id") for event in tools
            ]
            ends = [tool_ends.get((invocation, tool_id)) for tool_id in tool_ids]
            complete = (
                index > 0 and all(end is not None for end in ends)
                and len(set(tool_ids)) == len(tool_ids)
                and all(end["ts_ms"] >= start["ts_ms"] for start, end in zip(tools, ends))
            )
            completion = max(end["ts_ms"] for end in ends) if complete else None
            gaps.append(gap_row(
                "tool_round", invocation, completion,
                submits[index] if index < len(submits) else None,
                origin_request_id=(
                    submits[index - 1]["attributes"]["request_id"] if index > 0 else None
                ),
                tool_run_ids=tool_ids,
                tool_names=[event["attributes"].get("tool_name") for event in tools],
            ))
    for event in events:
        if event["kind"] != "join_satisfied":
            continue
        wait = join_waits.get(event["join_id"], {})
        invocation = wait.get("invocation_id")
        # Equality at this boundary represents a zero-length handoff.
        submits = ordinary.get(invocation, ())
        following = next(
            (row for row in submits if row["ts_ms"] >= event["ts_ms"]), None,
        )
        row = gap_row(
            "join", invocation, event["ts_ms"] if invocation else None,
            following, join_id=event["join_id"],
        )
        if invocation is None:
            row["status"] = "unproven_join_parent"
        gaps.append(row)
    start_keys = {
        (invocation, start["attributes"].get("tool_run_id")
         or start["attributes"].get("tool_call_id"))
        for invocation, starts in tool_starts.items() for start in starts
    }
    return {
        "summary_rows": summaries, "gap_rows": gaps,
        "orphan_internal_results": [
            request for request, event in results.items()
            if event["attributes"].get("runtime_internal") and request not in submissions
        ],
        "tool_ends_without_starts": sum(
            key not in start_keys for key in tool_ends
        ),
    }


def summarize(summary_rows: list[dict], gap_rows: list[dict]) -> dict:
    summary = {
        "summary_requests": len(summary_rows),
        "workflows_with_summary": len({row["workflow_id"] for row in summary_rows}),
        "summary_status": dict(Counter(row["status"] for row in summary_rows)),
        "finish_reasons": dict(Counter(
            row.get("finish_reason") for row in summary_rows if row["status"] == "complete"
        )),
        "empty_length_summary_requests": sum(row["empty_length"] for row in summary_rows),
        "model_request_span_ms": distribution(
            row["model_request_span_ms"]
            for row in summary_rows if row["model_request_span_ms"] is not None
        ),
        "sources": {},
    }
    for source in ("tool_round", "join"):
        selected = [row for row in gap_rows if row["source"] == source]
        measured = [row for row in selected if row["status"] == "measured"]
        cohorts = {
            "all_measured": measured,
            "with_summary": [row for row in measured if row["summary_request_count"] > 0],
            "without_summary": [row for row in measured if row["summary_request_count"] == 0],
        }
        summary["sources"][source] = {
            "statuses": dict(Counter(row["status"] for row in selected)),
            "cohorts": {
                name: {
                    "events": len(rows),
                    "gap_ms": distribution(row["gap_ms"] for row in rows),
                    "summary_span_overlap_ms": distribution(
                        row["summary_span_overlap_ms"]
                        for row in rows if row["summary_span_overlap_ms"] is not None
                    ),
                    "outside_summary_request_spans_ms": distribution(
                        row["outside_summary_request_spans_ms"]
                        for row in rows if row["outside_summary_request_spans_ms"] is not None
                    ),
                    "additional_phase_ms": {
                        field: distribution(row[field] for row in rows if row.get(field) is not None)
                        for field in (
                            "blocking_join_overlap_ms", "outside_summary_and_join_spans_ms",
                            "completion_to_first_summary_submit_ms",
                            "between_summary_request_spans_ms",
                            "last_summary_result_to_next_submit_ms",
                            "last_summary_result_to_compaction_ms", "compaction_to_next_submit_ms",
                        )
                    },
                    "events_with_unknown_summary_span": sum(
                        row["summary_requests_with_unknown_span"] > 0 for row in rows
                    ),
                } for name, rows in cohorts.items()
            },
        }
    return summary


def audit(client: Path) -> dict:
    paths = sorted(client.glob("workflows/*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no workflow runtime sources under {client}")
    bindings, summary_rows, gap_rows = [], [], []
    orphans, tool_orphans = 0, 0
    for path in paths:
        digest, events, count, size = hashlib.sha256(), [], 0, 0
        with path.open("rb") as stream:
            for count, line in enumerate(stream, 1):
                digest.update(line)
                size += len(line)
                event = json.loads(line)
                timestamp = event.get("ts_ms")
                if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
                    raise ValueError(f"invalid timestamp: {path}:{count}")
                if event["kind"] in KINDS:
                    events.append(event)
        bindings.append({
            "path": str(path.resolve()), "sha256": digest.hexdigest(),
            "bytes": size, "events": count,
        })
        result = analyze_workflow(events, workflow_path=path.parent.name)
        summary_rows.extend(result["summary_rows"])
        gap_rows.extend(result["gap_rows"])
        orphans += len(result["orphan_internal_results"])
        tool_orphans += result["tool_ends_without_starts"]
    return {
        "scope": (
            "Within each workflow JSONL, use monotonic timestamps and explicit "
            "request/invocation/parent identities. Tool rounds contain all observed "
            "tool starts between adjacent ordinary submissions; completion requires "
            "every tool end and uses their maximum. Internal summary requests are "
            "excluded from ordinary submissions. Summary spans include model queueing, "
            "service and transport, not pure GPU computation or client idle. Intersect "
            "their interval union with each completion-to-submit gap. Unknown spans "
            "remain unknown; unclosed tool/submit boundaries are retained. Summed spans "
            "may overlap across workflows and source cohorts, so they are neither "
            "elapsed run time, exposed stall nor counterfactual JCT savings. No "
            "quality, causal performance or DMA overlap claim is made."
        ),
        "inputs": bindings, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "workflow_sources": len(paths), "orphan_internal_results": orphans,
        "tool_ends_without_starts": tool_orphans,
        "summary": summarize(summary_rows, gap_rows),
        "summary_rows": summary_rows, "gap_rows": gap_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.output, args.summary_output):
        if path.exists():
            raise FileExistsError(path)
    result = audit(args.client)
    compact = {key: value for key, value in result.items() if key not in ("summary_rows", "gap_rows")}
    for path, payload in ((args.output, result), (args.summary_output, compact)):
        path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "xt", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "workflow_sources": result["workflow_sources"],
        "orphan_internal_results": result["orphan_internal_results"],
        "tool_ends_without_starts": result["tool_ends_without_starts"],
        "summary": result["summary"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
