#!/usr/bin/env python3
"""Audit opt-in hidden-state snapshots against actual child RETURN/JOIN events."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np


def index_workflow(events: list[dict]) -> tuple[dict, dict]:
    children = {
        event["target_invocation_id"]
        for event in events
        if event["kind"] == "spawn" and event.get("target_invocation_id")
    }
    returns = {
        event["invocation_id"]: event["ts_ms"]
        for event in events
        if event["kind"] == "return" and event.get("invocation_id") in children
    }
    results = defaultdict(list)
    for event in events:
        if event["kind"] == "llm_result" and event.get("invocation_id") in children:
            results[event["invocation_id"]].append(event)
    terminal_rids = {}
    for invocation_id, returned_at in returns.items():
        prior = [
            event for event in results[invocation_id]
            if event["ts_ms"] <= returned_at
        ]
        if not prior:
            continue
        terminal = max(prior, key=lambda event: event["ts_ms"])
        attrs = terminal.get("attributes") or {}
        if (
            attrs.get("finish_reason") == "stop"
            and not attrs.get("tool_call_count")
            and attrs.get("output_chars", 0) > 0
            and attrs.get("request_id")
        ):
            terminal_rids[attrs["request_id"]] = (
                invocation_id, returned_at
            )
    join_last_children = set()
    groups = {
        event["join_id"]: event["member_invocation_ids"]
        for event in events
        if event["kind"] == "join_create" and event.get("join_id")
    }
    for event in events:
        if event["kind"] != "join_satisfied" or event.get("join_id") not in groups:
            continue
        members = groups[event["join_id"]]
        if not members or not all(member in returns for member in members):
            continue
        last = max(members, key=lambda member: returns[member])
        if abs(returns[last] - event["ts_ms"]) < 1:
            join_last_children.add(last)
    return terminal_rids, join_last_children


def summarize(
    traces: Path, workflow_root: Path | list[Path],
) -> dict:
    indexed = {}
    all_child_results = set()
    result_times = {}
    final_chunk_times = {}
    callback_entry_times = {}
    final_chunk_candidate_true = []
    final_chunk_candidate_false = 0
    final_chunk_candidate_censored = 0
    abnormal_reasons = defaultdict(int)
    natural_returns = 0
    roots = [workflow_root] if isinstance(workflow_root, Path) else workflow_root
    for event_file in (
        event_file
        for root in roots
        for event_file in root.glob("*/runtime_events.deepagents.jsonl")
    ):
        with event_file.open() as stream:
            events = [json.loads(line) for line in stream]
        terminal, join_last = index_workflow(events)
        terminal_by_child = {
            invocation_id: (rid, return_ts)
            for rid, (invocation_id, return_ts) in terminal.items()
        }
        child_results = defaultdict(list)
        first_tool_chunk_rids = set()
        for event in events:
            if event["kind"] == "llm_result" and str(
                event.get("invocation_id") or ""
            ).startswith("deepagents-invocation:"):
                child_results[event["invocation_id"]].append(event)
            if (
                event["kind"] == "structured_action"
                and (event.get("attributes") or {}).get(
                    "beliefkv_child_first_tool_chunk_shadow"
                )
            ):
                first_tool_chunk_rids.add(
                    (event.get("attributes") or {}).get("request_id")
                )
        for invocation_id, results in child_results.items():
            for index, event in enumerate(results):
                attrs = event.get("attributes") or {}
                rid = attrs.get("request_id")
                chunk_ts = attrs.get("stream_final_chunk_ts_ms")
                if (
                    not isinstance(chunk_ts, (int, float))
                    or attrs.get("finish_reason") != "stop"
                    or not (attrs.get("stream_content_counted_chars") or 0)
                    or rid in first_tool_chunk_rids
                ):
                    continue
                confirmed = terminal_by_child.get(invocation_id)
                if confirmed is not None and confirmed[0] == rid:
                    final_chunk_candidate_true.append(confirmed[1] - chunk_ts)
                elif index < len(results) - 1 or confirmed is not None:
                    final_chunk_candidate_false += 1
                else:
                    final_chunk_candidate_censored += 1
        natural_returns += sum(
            event["kind"] == "return"
            and str(event.get("invocation_id") or "").startswith(
                "deepagents-invocation:"
            )
            for event in events
        )
        for event in events:
            if event["kind"] != "llm_result":
                continue
            rid = (event.get("attributes") or {}).get("request_id")
            if rid and event.get("invocation_id", "").startswith(
                "deepagents-invocation:"
            ):
                all_child_results.add(rid)
                reason = (event.get("attributes") or {}).get("finish_reason")
                if reason in {"stop", "tool_calls", "length"}:
                    result_times[rid] = event["ts_ms"]
                    chunk_ts = (event.get("attributes") or {}).get(
                        "stream_final_chunk_ts_ms"
                    )
                    if isinstance(chunk_ts, (int, float)):
                        final_chunk_times[rid] = float(chunk_ts)
                        entry_ts = (event.get("attributes") or {}).get(
                            "llm_end_callback_entry_ts_ms"
                        )
                        if isinstance(entry_ts, (int, float)):
                            callback_entry_times[rid] = float(entry_ts)
                else:
                    abnormal_reasons[str(reason)] += 1
        for rid, (invocation_id, return_ts) in terminal.items():
            indexed[rid] = (invocation_id, return_ts, invocation_id in join_last)

    first_leads = []
    last_leads = []
    last_join_leads = []
    all_matched = 0
    terminal_matched = 0
    invalid_finished = 0
    finish_to_runtime = []
    finish_to_done = []
    done_to_runtime = []
    finish_to_client_final = []
    client_final_to_runtime = []
    done_to_client_final = []
    client_final_to_callback_entry = []
    callback_entry_to_runtime = []
    invalid_client_final_order = 0
    invalid_callback_entry_order = 0
    invalid_result_order = 0
    invalid_done_order = 0
    matched_normal = 0
    for path in traces.glob("*.npz"):
        with np.load(path, allow_pickle=False) as trace:
            rid = str(trace["rid"])
            if rid not in all_child_results:
                continue
            all_matched += 1
            arrival = trace["arrival_ns"].astype(np.float64) / 1e6
            finish_ms = float(trace["finish_ns"]) / 1e6
            if rid in result_times:
                matched_normal += 1
                result_lag = result_times[rid] - finish_ms
                if result_lag < 0:
                    invalid_result_order += 1
                else:
                    finish_to_runtime.append(result_lag)
                if rid in final_chunk_times:
                    chunk_ms = final_chunk_times[rid]
                    if not finish_ms <= chunk_ms <= result_times[rid]:
                        invalid_client_final_order += 1
                    else:
                        finish_to_client_final.append(chunk_ms - finish_ms)
                        client_final_to_runtime.append(result_times[rid] - chunk_ms)
                        if "done_ns" in trace:
                            done_to_client_final.append(
                                chunk_ms - float(trace["done_ns"]) / 1e6
                            )
                        if rid in callback_entry_times:
                            entry_ms = callback_entry_times[rid]
                            if not chunk_ms <= entry_ms <= result_times[rid]:
                                invalid_callback_entry_order += 1
                            else:
                                client_final_to_callback_entry.append(
                                    entry_ms - chunk_ms
                                )
                                callback_entry_to_runtime.append(
                                    result_times[rid] - entry_ms
                                )
                if "done_ns" in trace:
                    done_ms = float(trace["done_ns"]) / 1e6
                    if not finish_ms <= done_ms <= result_times[rid]:
                        invalid_done_order += 1
                    else:
                        finish_to_done.append(done_ms - finish_ms)
                        done_to_runtime.append(result_times[rid] - done_ms)
            if rid not in indexed:
                continue
            _, return_ts, join_last = indexed[rid]
            if not len(arrival) or finish_ms > return_ts:
                invalid_finished += 1
                continue
            terminal_matched += 1
            first_leads.append(return_ts - arrival[0])
            last_leads.append(return_ts - arrival[-1])
            if join_last:
                last_join_leads.append(return_ts - arrival[-1])

    def median(values: list[float]) -> float | None:
        return round(float(statistics.median(values)), 2) if values else None

    def p95(values: list[float]) -> float | None:
        return round(float(np.percentile(values, 95)), 2) if values else None

    return {
        "child_llm_results": len(all_child_results),
        "matched_hidden_trace_rounds": all_matched,
        "normal_child_results": len(result_times),
        "matched_normal_child_results": matched_normal,
        "abnormal_result_reasons": dict(sorted(abnormal_reasons.items())),
        "finish_to_runtime_result_count": len(finish_to_runtime),
        "finish_to_runtime_result_p50_ms": median(finish_to_runtime),
        "finish_to_runtime_result_p95_ms": p95(finish_to_runtime),
        "finish_to_runtime_result_max_ms": (
            round(max(finish_to_runtime), 2) if finish_to_runtime else None
        ),
        "invalid_result_order": invalid_result_order,
        "finish_to_done_count": len(finish_to_done),
        "finish_to_done_p95_ms": p95(finish_to_done),
        "done_to_runtime_result_p95_ms": p95(done_to_runtime),
        "invalid_done_order": invalid_done_order,
        "client_final_chunk_count": len(finish_to_client_final),
        "invalid_client_final_order": invalid_client_final_order,
        "finish_to_client_final_p50_ms": median(finish_to_client_final),
        "finish_to_client_final_p95_ms": p95(finish_to_client_final),
        "client_final_to_runtime_p50_ms": median(client_final_to_runtime),
        "client_final_to_runtime_p95_ms": p95(client_final_to_runtime),
        "done_to_client_final_p50_ms": median(done_to_client_final),
        "done_to_client_final_p95_ms": p95(done_to_client_final),
        "client_final_before_done_count": sum(
            lag < 0 for lag in done_to_client_final
        ),
        "callback_entry_count": len(client_final_to_callback_entry),
        "invalid_callback_entry_order": invalid_callback_entry_order,
        "client_final_to_callback_entry_p50_ms": median(
            client_final_to_callback_entry
        ),
        "client_final_to_callback_entry_p95_ms": p95(
            client_final_to_callback_entry
        ),
        "callback_entry_to_runtime_p50_ms": median(
            callback_entry_to_runtime
        ),
        "callback_entry_to_runtime_p95_ms": p95(
            callback_entry_to_runtime
        ),
        "final_chunk_candidate_true": len(final_chunk_candidate_true),
        "final_chunk_candidate_false": final_chunk_candidate_false,
        "final_chunk_candidate_censored": final_chunk_candidate_censored,
        "final_chunk_candidate_true_lead_p50_ms": median(
            final_chunk_candidate_true
        ),
        "natural_child_returns": natural_returns,
        "eligible_terminal_rids": len(indexed),
        "matched_terminal_rounds": terminal_matched,
        "invalid_finish_order": invalid_finished,
        "first_hidden_to_return_p50_ms": median(first_leads),
        "last_hidden_to_return_p50_ms": median(last_leads),
        "last_hidden_500ms_before_return": sum(
            bool(lead >= 500) for lead in last_leads
        ),
        "join_last_child_count": len(last_join_leads),
        "join_last_hidden_to_return_p50_ms": median(last_join_leads),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument(
        "--workflows", type=Path, action="append", required=True,
    )
    args = parser.parse_args()
    if not args.traces.is_dir() or any(
        not root.is_dir() for root in args.workflows
    ):
        parser.error("traces and workflow root must exist")
    print(json.dumps(summarize(args.traces, args.workflows), indent=2))


if __name__ == "__main__":
    main()
