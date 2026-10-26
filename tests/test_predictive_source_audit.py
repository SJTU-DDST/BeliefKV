import json

import pytest

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from scripts.audit_predictive_source import audit, recovery_observations


@pytest.fixture
def source(tmp_path):
    client = tmp_path / "client_1"
    events_path = client / "workflows/task/runtime_events.deepagents.jsonl"
    events_path.parent.mkdir(parents=True)
    events = []

    def add(kind, timestamp, invocation=None, **fields):
        events.append(RuntimeEvent(
            event_id=f"event-{len(events)}", workflow_id="w", kind=kind,
            ts_ms=timestamp, invocation_id=invocation,
            context_id=f"context-{invocation}" if invocation else None,
            context_epoch=0, **fields,
        ).to_dict())

    add(Kind.WORKFLOW_START, 0)
    add(Kind.INVOCATION_CREATE, 0, "root", relation_type=RelationType.ROOT)
    for child in ("a", "b"):
        add(Kind.INVOCATION_CREATE, 10, child, parent_invocation_id="root")
    add(Kind.JOIN_CREATE, 10, "root", join_id="join", member_invocation_ids=("a", "b"))
    add(Kind.JOIN_WAIT, 11, "root", join_id="join")
    for child, finish in (("a", 30), ("b", 35)):
        add(Kind.LLM_SUBMIT, 12, child, attributes={"request_id": child})
        add(Kind.LLM_RESULT, finish, child, attributes={"request_id": child})
        add(Kind.RETURN, finish + 1, child)
    add(Kind.JOIN_SATISFIED, 36, "root", join_id="join")
    add(Kind.TOOL_START, 37, "root", attributes={
        "tool_run_id": "tool", "tool_name": "execute",
    })
    add(Kind.TOOL_END, 47, "root", attributes={
        "tool_run_id": "tool", "status": "error",
    })
    add(Kind.LLM_SUBMIT, 48, "root", attributes={"request_id": "r"})
    add(Kind.LLM_RESULT, 60, "root", attributes={"request_id": "r"})
    add(Kind.RETURN, 61, "root")
    add(Kind.WORKFLOW_END, 62)
    events_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    (client / "summary.json").write_text(json.dumps({
        "workflow_count": 1, "completed_workflows": 1,
        "workflows": [{"workflow_id": "w", "native_agent_jct_eligible": True}],
    }))
    server = tmp_path / "server"
    server.mkdir()
    (server / "native_capacity_census.json").write_text(json.dumps({
        "capacity": {
            "device_full_tokens": 100, "device_full_bytes": 101 * 98304,
            "host_full_tokens": 200, "host_full_bytes": 200 * 98304,
        },
    }))
    native = [{
        "kind": "llm_submit", "workflow_id": "w", "invocation_id": identity,
        "attributes": {
            "request_id": identity, "prompt_tokens": 20,
            "cached_tokens_device": 0, "cached_tokens_host": 10 if identity == "r" else 0,
        },
    } for identity in ("a", "b", "r")]
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in native)
    )
    wait = {
        "event": "gpu_restore_dependency_wait", "request_ids": ["r"],
        "batch_size": 1, "sampling_interval": 16,
        "layers": [{"layer": 0, "gpu_wait_ms": 2.}, {"layer": 1, "gpu_wait_ms": 1.}],
        "gpu_layer_dependency_wait_ms": 3., "gpu_observed_layer_span_ms": 10.,
    }
    (server / "runtime_audit.jsonl").write_text(json.dumps(wait) + "\n")
    (server / "native_telemetry_status.json").write_text(json.dumps({
        "collection_mode": "native_reactive", "host_tier_enabled": True,
        "writer_error": None, "dropped_records": 0, "failed_records": 0,
        "pending_request_count": 0, "pending_batch_count": 0,
        "gpu_restore_wait_probe": {"counts": {"completed_samples": 1}},
        "host_pool_evidence": {"full": {"bytes_per_unit": 98304, "capacity_units": 200}},
    }))
    return tmp_path


def test_source_review_counts_actual_recovery_and_wait_without_inventing_cold_pages(source):
    result = audit(source, expected_workflows=1)
    assert result["accepted_for_full_probe"]
    assert result["native_recovery"]["served_requests"] == 3
    assert result["native_recovery"]["requests_with_host_prefix"] == 1
    assert result["native_recovery"]["host_prefix_bytes"] == 10 * 98304
    assert result["full_pool_geometry"]["device_allocated_bytes"] == 101 * 98304
    assert not result["full_pool_geometry"]["host_geometry_derived_from_device_capacity"]
    assert result["blocking"]["observed_join_member_histogram"] == {2: 1}
    assert result["blocking"]["ordinary_tool_status_counts"] == {"error": 1}
    assert result["native_sampled_restore_waits"]["summary"]["total_wait_ms"]["sum"] == 3.
    assert result["cold_exclusive_page_availability"] == "requires_actual_full_native_donor_checks"
    assert not result["sampled_waits_prove_removable_jct"]
    assert not result["sampled_waits_expanded_to_all_batches"]
    assert not result["tool_test_pass_required"]
    assert not result["performance_accepted"]
    assert str(source / "server/runtime_events.sglang.jsonl") in result["input_jsonl_prefixes"]


@pytest.mark.parametrize("prefix_loss", [0, 10])
def test_no_memory_recovery_opportunity_is_reported_without_using_policy_performance(
    source, prefix_loss,
):
    path = source / "server/runtime_events.sglang.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["attributes"]["cached_tokens_host"] = 0
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    status_path = source / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    status["context_prefix_reuse_evidence"] = {
        "counts": {"previously_served_prefix_recompute_proxy_tokens": prefix_loss},
    }
    status_path.write_text(json.dumps(status))
    result = audit(source, expected_workflows=1)
    assert result["accepted_for_full_probe"] is bool(prefix_loss)
    assert bool(result["review_reasons"]) is not bool(prefix_loss)
    assert not result["policy_performance_used_for_source_selection"]


@pytest.mark.parametrize("change", [
    "incomplete", "guarded", "wrong_workflow", "pending_native", "dropped",
    "missing_wait", "incomplete_tool", "host_geometry_missing", "host_geometry_mismatch",
])
def test_unfinished_or_unreliable_source_cannot_start_a_full_probe(source, change):
    if change in ("incomplete", "guarded", "wrong_workflow"):
        path = source / "client_1/summary.json"
        row = json.loads(path.read_bytes())
        if change == "incomplete":
            row["completed_workflows"] = 0
        elif change == "guarded":
            row["workflows"][0]["native_agent_jct_eligible"] = False
        else:
            row["workflows"][0]["workflow_id"] = "other"
    elif change == "incomplete_tool":
        path = source / "client_1/workflows/task/runtime_events.deepagents.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text("".join(
            json.dumps(row) + "\n" for row in events if row["kind"] != "tool_end"
        ))
        with pytest.raises(ValueError, match="incomplete"):
            audit(source, expected_workflows=1)
        return
    else:
        path = source / "server/native_telemetry_status.json"
        row = json.loads(path.read_bytes())
        if change == "pending_native":
            row["pending_request_count"] = 1
        elif change == "dropped":
            row["dropped_records"] = 1
        elif change == "host_geometry_missing":
            row.pop("host_pool_evidence")
        elif change == "host_geometry_mismatch":
            row["host_pool_evidence"]["full"]["capacity_units"] += 1
        else:
            row["gpu_restore_wait_probe"]["counts"]["completed_samples"] = 2
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        audit(source, expected_workflows=1)


@pytest.mark.parametrize("change", ["duplicate", "negative", "overcount"])
def test_native_recovery_accounting_rejects_invalid_request_or_cache_counters(change):
    row = {"kind": "llm_submit", "attributes": {
        "request_id": "r", "prompt_tokens": 20,
        "cached_tokens_device": 5, "cached_tokens_host": 10,
    }}
    rows = [row]
    if change == "duplicate":
        rows.append(row)
    elif change == "negative":
        row["attributes"]["cached_tokens_host"] = -1
    else:
        row["attributes"]["cached_tokens_device"] = 15
    with pytest.raises(ValueError):
        recovery_observations(rows, bytes_per_token=98304)
