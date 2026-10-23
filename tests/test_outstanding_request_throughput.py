from copy import deepcopy
import json

import pytest

from scripts.audit_outstanding_request_throughput import analyze, audit_arm


def request(rid, start, end, *, output=10):
    identity = {
        "workflow_id": "workflow", "invocation_id": rid,
        "context_id": "context-" + rid, "context_epoch": 0,
    }
    return [
        {
            **identity, "kind": "llm_submit", "ts_ms": start,
            "attributes": {
                "request_id": rid, "prompt_tokens": 100,
                "native_ingress_timing_ms": {
                    "api_received_ts_ms": start - 2,
                    "scheduler_received_ts_ms": start - 1,
                    "native_queue_registered_ts_ms": start,
                },
            },
        },
        {
            **identity, "kind": "llm_result", "ts_ms": end,
            "attributes": {
                "request_id": rid, "output_tokens": output,
                "complete_monotonic_ms": end + 1000,
                "monotonic_clock_domain": "clock",
            },
        },
    ]


def test_overlapping_requests_count_once_but_keep_queue_and_processing_wait():
    result = analyze(request("a", 2, 12) + request("b", 7, 17))
    queue = result["by_arrival_boundary"]["queue_registered"]
    assert queue["outstanding_union_ms"] == 15
    assert queue["no_outstanding_request_ms_within_span"] == 0
    assert queue["time_weighted_outstanding_requests"] == pytest.approx(20 / 15)
    assert queue["output_tokens_per_outstanding_second"] == pytest.approx(20000 / 15)
    assert result["by_arrival_boundary"]["api_received"]["outstanding_union_ms"] == 17


def test_only_zero_outstanding_gaps_are_excluded_and_long_requests_remain():
    result = analyze(request("a", 2, 12) + request("b", 112, 1112))
    queue = result["by_arrival_boundary"]["queue_registered"]
    assert queue["outstanding_union_ms"] == 1010
    assert queue["first_arrival_to_last_completion_ms"] == 1110
    assert queue["no_outstanding_request_ms_within_span"] == 100


def test_work_fingerprint_keeps_identity_and_token_counts_but_ignores_timing():
    first = analyze(request("a", 2, 12) + request("b", 7, 17))
    second = analyze(request("b", 20, 30) + request("a", 35, 45))
    changed = analyze(request("a", 2, 12, output=11) + request("b", 7, 17))
    field = "request_identity_and_token_count_sha256"
    assert first[field] == second[field]
    assert first[field] != changed[field]


@pytest.mark.parametrize("change", (
    "duplicate_submit", "duplicate_result", "missing_result", "foreign_result",
    "wrong_identity", "mixed_clock", "clock_jump", "missing_clock", "reverse_time",
    "missing_api", "unordered_ingress", "wrong_queue_boundary", "invalid_tokens",
))
def test_incomplete_or_inconsistent_evidence_cannot_improve_busy_throughput(change):
    rows = request("a", 2, 12) + request("b", 7, 17)
    if change == "duplicate_submit":
        rows.append(deepcopy(rows[0]))
    elif change == "duplicate_result":
        rows.append(deepcopy(rows[1]))
    elif change == "missing_result":
        rows.pop()
    elif change == "foreign_result":
        rows[-1]["attributes"]["request_id"] = "foreign"
    elif change == "wrong_identity":
        rows[-1]["context_epoch"] = 1
    elif change == "mixed_clock":
        rows[-1]["attributes"]["monotonic_clock_domain"] = "other"
    elif change == "clock_jump":
        rows[-1]["attributes"]["complete_monotonic_ms"] += 10
    elif change == "missing_clock":
        del rows[-1]["attributes"]["complete_monotonic_ms"]
    elif change == "reverse_time":
        rows[-1]["ts_ms"] = 6
    elif change == "missing_api":
        del rows[0]["attributes"]["native_ingress_timing_ms"]["api_received_ts_ms"]
    elif change == "unordered_ingress":
        rows[0]["attributes"]["native_ingress_timing_ms"]["api_received_ts_ms"] = 3
    elif change == "wrong_queue_boundary":
        rows[0]["attributes"]["native_ingress_timing_ms"]["native_queue_registered_ts_ms"] = 1
    else:
        rows[-1]["attributes"]["output_tokens"] = True
    with pytest.raises(ValueError):
        analyze(rows)


@pytest.mark.parametrize("change", (
    None, "partial_tail", "unsettled_count", "dropped_record", "pending_request",
    "incomplete_summary", "missing_request",
))
def test_arm_requires_complete_summary_population_and_settled_native_capture(tmp_path, change):
    server, replay = tmp_path / "server", tmp_path / "replay"
    server.mkdir()
    replay.mkdir()
    events = request("a", 2, 12)
    status = {
        "pending_request_count": 0, "pending_batch_count": 0,
        "dropped_records": 0, "failed_records": 0, "writer_error": None,
        "record_counts": {"events": 2},
    }
    summary = {
        "causal_execution_completed": True, "exact_tokens_verified": True,
        "original_native_stopping_verified": True, "completed_requests": 1,
    }
    if change == "unsettled_count":
        status["record_counts"]["events"] = 1
    elif change == "dropped_record":
        status["dropped_records"] = 1
    elif change == "pending_request":
        status["pending_request_count"] = 1
    elif change == "incomplete_summary":
        summary["causal_execution_completed"] = False
    elif change == "missing_request":
        summary["completed_requests"] = 2
    payload = "".join(json.dumps(row) + "\n" for row in events)
    (server / "runtime_events.sglang.jsonl").write_text(
        payload + ('{"kind":' if change == "partial_tail" else ""),
    )
    (server / "native_telemetry_status.json").write_text(json.dumps(status))
    (replay / "summary.json").write_text(json.dumps(summary))
    if change is None:
        assert audit_arm(tmp_path)["requests"] == 1
    else:
        with pytest.raises(ValueError):
            audit_arm(tmp_path)
