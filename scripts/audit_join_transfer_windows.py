#!/usr/bin/env python3
"""JOIN H2D windows and native completion-to-parent service timing."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.summarize_semantic_h2d_ab import records


def _latencies(rows: list[dict], field: str) -> dict:
    values = sorted(row[field] for row in rows if row[field] is not None)
    return {
        "count": len(values),
        "negative_count": sum(value < 0 for value in values),
        "p50_ms": median(values) if values else None,
        "p90_ms": values[min(len(values) - 1, int(len(values) * .9))]
        if values else None,
        "max_ms": values[-1] if values else None,
    }


def _difference(later: float | None, earlier: float | None) -> float | None:
    return later - earlier if later is not None and earlier is not None else None


def audit(arm: Path) -> dict:
    clock = None
    issued = {}
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if clock is None and row["event"] == "safe_point_census":
            clock = row
        if row["event"] == "prefetch_native_issued" and row.get("source") == "join_ticket":
            issued[row["command_id"]] = row
    if clock is None:
        raise ValueError("missing paired native wall/monotonic clock")
    offset = clock["ts_ms"] - clock["monotonic_ms"]
    child_requests = {
        issue["child_request_id"] for issue in issued.values()
        if issue.get("child_request_id")
    }
    returns, joins = {}, {}
    client_results, native_results = {}, {}
    parent_submits = defaultdict(list)
    native_submits, identities = {}, {}
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        for row in records(path):
            attrs = row.get("attributes") or {}
            request_id = attrs.get("request_id")
            if row["kind"] == "llm_result" and request_id in child_requests:
                client_results[request_id] = row
                identities[request_id] = (
                    row.get("invocation_id"), row.get("context_id"),
                    row.get("context_epoch"),
                )
            elif row["kind"] == "llm_submit" and row.get("context_id"):
                parent_submits[row["context_id"]].append(row)
            if row["kind"] == "return" and attrs.get("source") == "deepagents_task":
                if attrs.get("outcome") == "completed":
                    returns[row["invocation_id"]] = row["ts_ms"] + offset
            elif row["kind"] == "join_satisfied":
                joins[row["join_id"]] = row["ts_ms"] + offset
    transports = {}
    for path in arm.glob("client_*/workflows/*/child_stream_content.jsonl"):
        for row in records(path):
            if (
                row.get("event") == "llm_stream_http_transport"
                and row.get("request_id") in child_requests
            ):
                transports[row["request_id"]] = row
    session_closes = {}
    for path in arm.glob("client_*/workflows/*/sandbox_audit.jsonl"):
        for row in records(path):
            if row.get("event") not in {
                "native_session_retire_start", "native_session_retire_complete",
            }:
                continue
            key = (
                row.get("workflow_id"), row.get("context_id"), row.get("context_epoch"),
            )
            if any(value is None for value in key):
                continue
            if key not in session_closes or row["event"] == "native_session_retire_complete":
                session_closes[key] = row
    for submits in parent_submits.values():
        submits.sort(key=lambda row: (row["ts_ms"], row.get("sequence", 0)))
    parent_contexts = {issue["context_id"] for issue in issued.values()}
    parent_requests = {
        row["attributes"]["request_id"]
        for context_id, submits in parent_submits.items()
        if context_id in parent_contexts
        for row in submits
        if row["attributes"].get("request_id")
    }
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        request_id = (row.get("attributes") or {}).get("request_id")
        if row["kind"] == "llm_result" and request_id in child_requests:
            native_results[request_id] = row
        elif row["kind"] == "llm_submit" and request_id in parent_requests:
            native_submits[request_id] = row
    native_services = {}
    for row in records(arm / "server/runtime_audit.jsonl"):
        start = row.get("service_start_ts_ms")
        if row.get("event") != "gpu_service_sample" or start is None:
            continue
        for sample in row.get("request_samples") or ():
            request_id = sample.get("request_id")
            if request_id not in parent_requests:
                continue
            identity = (
                request_id, sample.get("workflow_id"), sample.get("invocation_id"),
                sample.get("context_id"), sample.get("context_epoch"),
            )
            previous = native_services.get(identity)
            if previous is None or start < previous:
                native_services[identity] = start
    acks = {
        row["command_id"]: row for row in records(arm / "server/physical_action_ack.jsonl")
        if row["action"] == "PREFETCH_GPU"
    }
    uses = {
        row["command_id"]: row for row in records(arm / "server/physical_action_use.jsonl")
        if row["event"] == "beliefkv_prefetch_first_service"
    }
    rows = []
    for transfer in records(arm / "server/transfer_telemetry.jsonl"):
        if transfer.get("direction") != "h2d":
            continue
        for child in transfer.get("tagged_child_commits") or []:
            command = child["command_id"]
            if command not in issued:
                continue
            issue = issued[command]
            start = transfer.get("submit_ts_ms")
            return_ts = returns.get(issue.get("child_invocation_id"))
            join_ts = joins.get(issue.get("join_id"))
            ack = acks.get(command)
            use = uses.get(command)
            child_request = issue.get("child_request_id")
            client_result = client_results.get(child_request)
            native_result = native_results.get(child_request)
            native_identity = (
                native_result.get("invocation_id"),
                native_result.get("context_id"),
                native_result.get("context_epoch"),
            ) if native_result else None
            identity_matches = (
                native_identity == identities[child_request]
                and native_identity[0] == issue.get("child_invocation_id")
            ) if native_result and client_result else None
            native_done = native_result["ts_ms"] if identity_matches else None
            client_done = client_result["ts_ms"] + offset if identity_matches else None
            # Match the first parent request after this complete JOIN. Do not
            # attach an earlier tool reentry or a different child model turn.
            parent_submit = next((
                row for row in parent_submits.get(issue["context_id"], ())
                if join_ts is not None and row["ts_ms"] + offset >= join_ts
            ), None)
            parent_id = (parent_submit.get("attributes") or {}).get("request_id") if parent_submit else None
            parent_arrival = native_submits.get(parent_id)
            if parent_arrival and (
                parent_arrival.get("context_id") != issue["context_id"]
                or parent_arrival.get("context_epoch") != parent_submit.get("context_epoch")
                or parent_arrival.get("invocation_id") != parent_submit.get("invocation_id")
                or parent_arrival.get("workflow_id") != parent_submit.get("workflow_id")
            ):
                parent_arrival = None
            parent_ts = parent_submit["ts_ms"] + offset if parent_submit else None
            parent_arrival_ts = parent_arrival["ts_ms"] if parent_arrival else None
            parent_service_ts = native_services.get((
                parent_id, parent_submit.get("workflow_id"), parent_submit.get("invocation_id"),
                parent_submit.get("context_id"), parent_submit.get("context_epoch"),
            )) if parent_submit else None
            parent_launch_ts = (
                use.get("first_service_ts_ms")
                if use is not None and parent_submit is not None
                and use.get("request_id") == parent_id
                and use.get("context_id") == parent_submit.get("context_id")
                and use.get("service_context_epoch") == parent_submit.get("context_epoch")
                else None
            )
            client_attrs = client_result.get("attributes") or {} if client_result else {}
            finish_chunk = client_attrs.get("stream_final_chunk_ts_ms")
            callback_entry = client_attrs.get("llm_end_callback_entry_ts_ms")
            finish_ts = (
                finish_chunk + offset
                if identity_matches and finish_chunk is not None else None
            )
            callback_ts = (
                callback_entry + offset
                if identity_matches and callback_entry is not None else None
            )
            transport = transports.get(child_request, {})
            last_raw = transport.get("last_raw_at_ms")
            last_raw_ts = (
                last_raw + offset
                if identity_matches and last_raw is not None else None
            )
            close = session_closes.get((
                client_result.get("workflow_id"), *native_identity[1:],
            ), {}) if identity_matches else {}
            close_start = close.get("close_start_monotonic_ms")
            close_elapsed = (
                close.get("close_elapsed_ms")
                if close.get("event") == "native_session_retire_complete" else None
            )
            close_start_ts = close_start + offset if close_start is not None else None
            close_complete_ts = (
                close_start_ts + close_elapsed
                if close_start_ts is not None and close_elapsed is not None else None
            )
            close_overlap = (
                max(0., min(parent_ts, close_complete_ts) - max(join_ts, close_start_ts))
                if None not in (parent_ts, join_ts, close_start_ts, close_complete_ts)
                else None
            )
            rows.append({
                "command_id": command, "context_id": issue["context_id"],
                "join_id": issue.get("join_id"), "node_id": issue["node_id"],
                "child_request_id": child_request,
                "child_result_identity_matches": identity_matches,
                "child_native_done_ts_ms": native_done,
                "child_client_result_ts_ms": client_done,
                "child_client_finish_chunk_ts_ms": finish_ts,
                "child_llm_end_callback_entry_ts_ms": callback_ts,
                "child_http_last_raw_ts_ms": last_raw_ts,
                "child_http_stream_consumed": transport.get("stream_consumed"),
                "child_http_raw_chunks": transport.get("raw_chunks"),
                "child_http_raw_bytes": transport.get("raw_bytes"),
                "child_http_raw_pull_total_ms": transport.get("raw_pull_total_ms"),
                "child_http_raw_pull_max_ms": transport.get("raw_pull_max_ms"),
                "child_http_consumer_pause_total_ms": transport.get("consumer_pause_total_ms"),
                "child_http_consumer_pause_max_ms": transport.get("consumer_pause_max_ms"),
                "child_client_finish_reason": client_attrs.get("finish_reason"),
                "child_client_tool_call_count": client_attrs.get("tool_call_count"),
                "child_client_invalid_tool_call_count": client_attrs.get("invalid_tool_call_count"),
                "child_session_close_start_ts_ms": close_start_ts,
                "child_session_http_complete_ts_ms": close_complete_ts,
                "child_session_close_elapsed_ms": close_elapsed,
                "child_session_close_queue_wait_ms": close.get("enqueue_to_close_start_ms"),
                "native_submit_ts_ms": start,
                "child_return_ts_ms": return_ts, "join_satisfied_ts_ms": join_ts,
                "parent_next_request_id": parent_id,
                "parent_client_submit_ts_ms": parent_ts,
                "parent_native_arrival_ts_ms": parent_arrival_ts,
                "parent_prefetch_first_launch_ts_ms": parent_launch_ts,
                "parent_native_first_service_ts_ms": parent_service_ts,
                "parent_first_service_evidence": (
                    "same_identity_gpu_service_interval_start"
                    if parent_service_ts is not None else None
                ),
                "native_done_to_client_result_ms": _difference(client_done, native_done),
                "native_done_to_client_finish_chunk_ms": _difference(finish_ts, native_done),
                "client_finish_chunk_to_callback_entry_ms": _difference(callback_ts, finish_ts),
                "callback_entry_to_client_result_ms": _difference(client_done, callback_ts),
                "native_done_to_last_http_read_ms": _difference(last_raw_ts, native_done),
                "last_http_read_to_callback_entry_ms": _difference(callback_ts, last_raw_ts),
                "client_result_to_child_return_ms": _difference(return_ts, client_done),
                "child_return_to_join_ms": _difference(join_ts, return_ts),
                "join_to_parent_submit_ms": _difference(parent_ts, join_ts),
                "parent_submit_to_native_arrival_ms": _difference(parent_arrival_ts, parent_ts),
                "parent_native_arrival_to_first_service_ms": _difference(
                    parent_service_ts, parent_arrival_ts,
                ),
                "parent_submit_to_first_service_ms": _difference(parent_service_ts, parent_ts),
                "child_return_to_session_close_start_ms": _difference(close_start_ts, return_ts),
                "child_return_to_session_http_complete_ms": _difference(close_complete_ts, return_ts),
                "session_http_complete_to_parent_submit_ms": _difference(parent_ts, close_complete_ts),
                "session_http_overlap_with_join_to_submit_ms": close_overlap,
                "native_submit_lead_to_child_native_done_ms": _difference(native_done, start),
                "submit_lead_to_child_return_ms": (
                    return_ts - start if return_ts is not None and start is not None else None
                ),
                "submit_lead_to_join_ms": (
                    join_ts - start if join_ts is not None and start is not None else None
                ),
                "ack_lead_to_join_ms": (
                    join_ts - ack["ts_ms"] if ack is not None and join_ts is not None else None
                ),
                "full_first_service_reused": use["full_node_reused"] if use else None,
                "actual_bytes": child["num_bytes"],
            })
    leads = [r["submit_lead_to_child_return_ms"] for r in rows
             if r["submit_lead_to_child_return_ms"] is not None]
    completion_fields = (
        "native_done_to_client_result_ms",
        "native_done_to_client_finish_chunk_ms",
        "client_finish_chunk_to_callback_entry_ms",
        "callback_entry_to_client_result_ms",
        "native_done_to_last_http_read_ms",
        "last_http_read_to_callback_entry_ms",
        "client_result_to_child_return_ms",
        "child_return_to_join_ms",
        "join_to_parent_submit_ms",
        "parent_submit_to_native_arrival_ms",
        "parent_native_arrival_to_first_service_ms",
        "parent_submit_to_first_service_ms",
        "child_return_to_session_close_start_ms",
        "child_return_to_session_http_complete_ms",
        "session_http_complete_to_parent_submit_ms",
        "session_http_overlap_with_join_to_submit_ms",
    )
    unique_children = {
        row["child_request_id"]: row for row in rows
        if row["child_request_id"] is not None
    }
    return {
        "schema_version": 2,
        "scope": "actual controller submission, not unanchored DMA-start or forecast ETA",
        "clock": "paired scheduler wall/monotonic clock; client/server on same host",
        "completion_timing_scope": (
            "same native/client child request, invocation/context/epoch identity. "
            "Native completion precedes HTTP consumption, framework callbacks "
            "and task return. Optional HTTP timings split raw reads from consumer "
            "pauses; pauses include parsing, callbacks and CPU scheduling, while "
            "raw pulls include waiting for server data. Neither is a pure network "
            "or function CPU measurement. Missing legacy timings remain unknown. "
            "Parent first service uses the earliest matching gpu_service_sample "
            "service_start_ts_ms for the next same-context request after JOIN. "
            "Native LLM_SUBMIT is request arrival, not first GPU service. Service "
            "samples use scheduler/worker intervals, not isolated CUDA kernel "
            "time; missing or unsampled service stays unknown. Prefetch launch "
            "receipts remain separate from worker service intervals."
            " Terminal session HTTP completion confirms dispatch acceptance, not "
            "scheduler reference release. HTTP/JOIN-to-submit overlap is observed "
            "interval overlap, not a causally isolated or additive JCT saving."
        ),
        "command_count": len(rows), "child_return_observed_count": len(leads),
        "started_before_child_return_count": sum(x > 0 for x in leads),
        "started_0_to_500ms_before_child_return_count": sum(0 < x <= 500 for x in leads),
        "started_100_to_500ms_before_child_return_count": sum(100 <= x <= 500 for x in leads),
        "started_at_or_after_child_return_count": sum(x <= 0 for x in leads),
        "median_submit_lead_to_child_return_ms": median(leads) if leads else None,
        "acked_before_join_count": sum(
            r["ack_lead_to_join_ms"] is not None and r["ack_lead_to_join_ms"] > 0 for r in rows
        ),
        "completion_intervals": {
            field: _latencies(rows, field)
            for field in (
                "native_submit_lead_to_child_native_done_ms",
                *completion_fields,
            )
        },
        "unique_child_request_count": len(unique_children),
        "unique_child_completion_intervals": {
            field: _latencies(list(unique_children.values()), field)
            for field in completion_fields
        },
        "unique_child_http_intervals": {
            field: _latencies(list(unique_children.values()), field)
            for field in (
                "child_http_raw_pull_total_ms",
                "child_http_raw_pull_max_ms",
                "child_http_consumer_pause_total_ms",
                "child_http_consumer_pause_max_ms",
            )
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
