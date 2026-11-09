import pytest

from scripts.audit_h2d_agent_origins import (
    request_metadata, staged_groups, union_duration, window_diagnostics,
)


def test_joint_staging_batch_keeps_full_bytes_joint_and_validates_identity():
    members = [
        {"request_id": rid, "workflow_id": rid, "invocation_id": rid,
         "context_id": rid, "context_epoch": 0}
        for rid in ("a", "b")
    ]
    metadata = {
        rid: {"identity": (rid, rid, rid, 0), "kind": kind, "role": role,
              "first_invocation_request": False}
        for rid, kind, role in (("a", "post_tool", "root"), ("b", "post_join", "child"))
    }
    episode = {"requests": members, "native_h2d_bytes": 1000}
    result = staged_groups([episode], metadata)
    assert result["by_kind"]["mixed:post_join+post_tool"]["actual_bytes"] == 1000
    assert result["distinct_requests"] == 2
    assert result["single_member_batches_by_invocation"] == []
    metadata["a"]["identity"] = ("other", "a", "a", 0)
    with pytest.raises(ValueError, match="identity"):
        staged_groups([episode], metadata)


def test_repeated_demand_copies_keep_bytes_but_deduplicate_request_population():
    member = {"request_id": "r", "workflow_id": "w", "invocation_id": "a",
              "context_id": "c", "context_epoch": 0}
    metadata = {"r": {"identity": ("w", "a", "c", 0), "kind": "post_tool",
                      "role": "child", "first_invocation_request": False}}
    episodes = [{"requests": [member], "native_h2d_bytes": size} for size in (100, 200)]
    result = staged_groups(episodes, metadata)
    assert result["by_kind"]["post_tool"]["actual_bytes"] == 300
    assert result["member_associations_by_kind"] == {"post_tool": 2}
    assert result["distinct_requests_by_kind"] == {"post_tool": 1}
    assert result["distinct_requests"] == 1


def test_parallel_tool_window_durations_are_unions_and_competition_is_not_capacity():
    target = {
        "request_id": "r", "invocation_id": "a", "host_tokens": 100,
        "tool_return_ms": 600, "tool_intervals_ms": [(0, 600), (100, 600)],
    }
    states = [
        {"monotonic_ms": when, "ts_ms": when,
         "agent_admission": {"paused_invocation_ids": ["a"]}}
        for when in (0, 200, 400, 600)
    ]
    pools = [{"ts_ms": 0, "device_pools": {"full": {"observable": True, "free_units": 5000}}}]
    aligned = {row["monotonic_ms"]: {"ready_paused": 1} for row in states}
    result = window_diagnostics(target, states, aligned, pools)
    assert result["category"] == "sufficient_free_observed_but_ready_competitors"
    assert result["free_but_ready_competition_ms"] == 600
    assert result["strict_window_ms"] == 0
    assert union_duration([(0, 10), (2, 9), (10, 20)]) == 20
    aligned[400]["ready_paused"] = 0
    result = window_diagnostics(target, states, aligned, pools)
    assert result["category"] == "strict_ge_100ms"
    assert result["strict_window_ms"] == 200


def test_window_unknown_pool_is_not_capacity_refusal_and_short_windows_are_kept():
    target = {
        "request_id": "r", "invocation_id": "a", "host_tokens": 100,
        "tool_return_ms": 50, "tool_intervals_ms": [(0, 50)],
    }
    states = [
        {"monotonic_ms": when, "ts_ms": when,
         "agent_admission": {"paused_invocation_ids": ["a"]}}
        for when in (0, 50)
    ]
    aligned = {0: {"ready_paused": 0}}
    assert window_diagnostics(target, states, aligned, [])["category"] == (
        "no_fresh_pool_evidence_during_observed_pause"
    )
    pools = [{"ts_ms": 0, "device_pools": {"full": {"observable": True, "free_units": 5000}}}]
    assert window_diagnostics(target, states, aligned, pools)["category"] == "strict_under_100ms"
    pools[0]["device_pools"]["full"]["free_units"] = 100
    assert window_diagnostics(target, states, aligned, pools)["category"] == (
        "no_sufficient_free_in_observed_paused_intervals"
    )


def test_first_child_is_distinct_from_first_root_and_repeated_submission_rejected():
    def event(kind, when, invocation, **values):
        return {
            "kind": kind, "ts_ms": when, "workflow_id": "w", "invocation_id": invocation,
            "context_id": invocation, "context_epoch": 0, "attributes": {}, **values,
        }
    events = [
        event("workflow_start", 0, None),
        event("invocation_create", 0, "root", relation_type="root"),
        event("llm_submit", 1, "root", attributes={"request_id": "r"}),
        event("invocation_create", 2, "child", parent_invocation_id="root"),
        event("llm_submit", 3, "child", attributes={"request_id": "c"}),
    ]
    metadata = request_metadata(events)
    assert metadata["r"]["kind"] == "first_root"
    assert metadata["c"]["kind"] == "first_child"
    with pytest.raises(ValueError, match="duplicate"):
        request_metadata(events + [events[-1]])
