import hashlib
import json

import pytest

from scripts.audit_native_hybrid_reuse import (
    interval_overlap, prefill_costs, read_requests, records, transfer_costs,
)


def sample(request, start, count):
    return {
        "request_id": request, "sequence_tokens_before": start, "token_delta": count,
        "token_delta_semantics": "prefill_extend_input_len",
    }


def service(identity, *samples):
    return {
        "event": "gpu_service_sample", "phase": "prefill",
        "sample_id": identity, "request_samples": list(samples),
        "service_elapsed_ms": 10,
    }


def transfer(command, direction, *, submit=0, complete=1, node=7, full=1, state=0):
    return {
        "event": "transfer_telemetry", "status": "completed", "command_id": command,
        "direction": direction, "node_ids": [node],
        "num_tokens_by_pool": {"kv": full, "mamba": state},
        "actual_bytes": full * 2 + state * 20,
        "submit_ts_ms": submit, "complete_ts_ms": complete,
        "transfer_stream_elapsed_ms": complete - submit,
        "submit_to_ack_ms": complete - submit,
    }


def test_chunk_geometry_counts_only_the_gap_positions():
    requests = {"r": {"effective_prefix": 100, "full_hit": 600, "role": "child"}}
    report, by_request = prefill_costs([
        service("a", sample("r", 100, 400)),
        service("b", sample("r", 500, 300)),
    ], requests)
    assert report["totals"]["extend_tokens"] == 700
    assert report["totals"]["full_match_gap_extend_tokens"] == 500
    assert by_request["r"]["full_match_gap_extend_tokens"] == 500
    assert interval_overlap(600, 100, 100, 600) == 0


def test_mixed_batch_time_is_not_assigned_to_gap_tokens_or_requests():
    requests = {
        "gap": {"effective_prefix": 0, "full_hit": 10, "role": "root"},
        "new": {"effective_prefix": 0, "full_hit": 0, "role": "summary"},
    }
    report, rows = prefill_costs([
        service("one", sample("gap", 0, 20), sample("new", 0, 100)),
    ], requests)
    group = report["batch_groups"]["with_full_match_gap"]
    assert group["worker_interval_ms"]["sum"] == 10
    assert group["single_request_worker_interval_ms"]["count"] == 0
    assert all("worker_interval_ms" not in row for row in rows.values())


def test_missing_geometry_and_duplicate_samples_are_not_silent_zero_work():
    request = {"r": {"effective_prefix": 0, "full_hit": 10}}
    bad = sample("r", 0, 20)
    bad.pop("token_delta_semantics")
    report, _ = prefill_costs([service("a", bad)], request)
    assert report["totals"]["samples_without_extend_geometry"] == 1
    assert report["batch_groups"]["unknown_geometry_or_identity"]["batches"] == 1
    with pytest.raises(ValueError, match="duplicate"):
        prefill_costs([service("a"), service("a")], {})


def test_transfer_pool_partition_requires_actual_byte_equality():
    row = transfer("a", "d2h", full=5, state=1)
    report = transfer_costs([row], {"kv": 2, "mamba": 20})
    group = report["by_direction_and_shape"]["d2h:full_and_mamba"]
    assert group["bytes"] == 30 and group["mamba_slots"]["sum"] == 1
    row["actual_bytes"] += 1
    with pytest.raises(ValueError, match="pool bytes"):
        transfer_costs([row], {"kv": 2, "mamba": 20})


def test_anchor_association_requires_past_completion_even_with_reordered_log_rows():
    rows = [
        transfer("h2d", "h2d", submit=5, complete=6),
        transfer("future", "d2h", submit=10, complete=11),
        transfer("past", "d2h", submit=0, complete=1),
        transfer("inflight", "d2h", submit=2, complete=8),
    ]
    result = transfer_costs(rows, {"kv": 2, "mamba": 20})
    assert result["anchors_with_multiple_d2h"] == 1
    assert result["d2h_commands_followed_by_same_anchor_h2d_ack"] == 1


def test_client_compaction_and_first_request_are_labeled_independently(tmp_path):
    workflow = tmp_path / "workflows/one"
    workflow.mkdir(parents=True)
    events = [
        {"kind": "invocation_create", "invocation_id": "root"},
        {"kind": "llm_submit", "invocation_id": "root", "attributes": {"request_id": "a"}},
        {"kind": "context_compact", "invocation_id": "root"},
        {"kind": "join_satisfied", "invocation_id": "root"},
        {"kind": "llm_submit", "invocation_id": "root", "attributes": {"request_id": "b"}},
    ]
    for index, event in enumerate(events):
        event.update(ts_ms=index, sequence=index)
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    result = read_requests(tmp_path, [])
    assert result["a"]["first_observed_invocation_request"]
    assert not result["a"]["first_after_compaction"]
    assert result["b"]["first_after_compaction"]
    assert result["b"]["follows_observed_join"]
    assert result["b"]["previous_client_request"] == "a"
    assert not result["b"]["first_observed_invocation_request"]


def test_filtered_source_hash_covers_nonselected_complete_rows(tmp_path):
    path = tmp_path / "rows.jsonl"
    complete = b'{"kind":"ignore"}\n{"kind":"keep"}\n'
    path.write_bytes(complete + b'{"kind":')
    bindings = []
    assert list(records(path, bindings, contains=lambda line: b'"keep"' in line)) == [
        {"kind": "keep"},
    ]
    assert bindings[0]["sha256"] == hashlib.sha256(complete).hexdigest()
    assert bindings[0]["analyzed_bytes"] == len(complete)
