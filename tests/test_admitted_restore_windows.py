from scripts.audit_admitted_restore_windows import (
    decisions, granted_free_window, join_populations, join_window, retained_grant_lead,
)


def test_pressure_mark_is_not_a_periodic_pause_or_a_new_grant():
    key = {"invocation_id": "a"}
    row = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 1.,
        "programs": [{
            "key": key, "active_before": True, "active_after_pressure": True,
            "marked_after_pressure": True,
        }],
    }
    assert list(decisions(row)) == []
    row["programs"][0]["active_after_pressure"] = False
    assert list(decisions(row)) == [(key, False, 1.)]


def test_grant_lead_rejects_an_intervening_pause_and_another_request_identity():
    key = ("r", "wf", "owner", "ctx", 1, 0, "session", 1)
    grants = {key: [(10., 2, "free_grant")]}
    assert retained_grant_lead(key, 5., (100., 9), grants, {}) == {
        "status": "exact_grant_without_later_observed_pause",
        "grant_event": "free_grant", "recorded_grant_to_staging_ms": 90.,
        "submitted_and_granted_to_staging_ms": 90.,
    }
    assert retained_grant_lead(
        key, 5., (100., 9), grants, {"owner": [(20., 3)]},
    )["status"] == "pause_observed_after_last_exact_grant"
    assert retained_grant_lead(
        (*key[:-1], 2), 5., (100., 9), grants, {},
    )["status"] == "no_exact_request_grant_record"


def test_retained_agent_grant_lead_starts_at_actual_request_submission():
    key = ("r", "wf", "a")
    result = retained_grant_lead(
        key, 50., (100., 9), {key: [(10., 2, "free_grant")]}, {},
    )
    assert result["recorded_grant_to_staging_ms"] == 90.
    assert result["submitted_and_granted_to_staging_ms"] == 50.


def test_join_presence_removes_terminal_and_ended_waiters():
    events = [
        {"ts_ms": 0, "kind": "join_wait", "workflow_id": "wf",
         "join_id": "j", "invocation_id": "parent"},
        {"ts_ms": 10, "kind": "return", "workflow_id": "wf", "invocation_id": "parent"},
    ]
    states = [{"monotonic_ms": now} for now in (5, 15)]
    assert join_populations(events, states) == {5: 1, 15: 0}
    events[1] = {"ts_ms": 10, "kind": "workflow_end", "workflow_id": "wf"}
    assert join_populations(events, states) == {5: 1, 15: 0}


def test_join_window_requires_no_ready_and_does_not_call_presence_safe_reclaim():
    target = {
        "invocation_id": "tool", "host_tokens": 100,
        "tool_intervals_ms": [(0, 100)], "tool_return_ms": 100,
    }
    states = [
        {"monotonic_ms": now, "ts_ms": now + 1000,
         "agent_admission": {"paused_invocation_ids": ["tool"]}}
        for now in (0, 50, 100)
    ]
    aligned = {now: {"ready_paused": int(now == 50)} for now in (0, 50, 100)}
    pools = [{
        "ts_ms": 1000, "device_pools": {
            "full": {"observable": True, "free_units": 0, "evictable_units": 100000},
        },
    }]
    result = join_window(target, states, aligned, pools, {0: 1, 50: 1, 100: 1})
    assert result == {
        "strict_free_ms": 0, "insufficient_free_join_present_ms": 50,
        "insufficient_free_no_join_ms": 0,
    }


def test_unknown_pool_cannot_become_an_idle_join_window():
    target = {
        "invocation_id": "tool", "host_tokens": 100,
        "tool_intervals_ms": [(0, 100)], "tool_return_ms": 100,
    }
    states = [
        {"monotonic_ms": now, "ts_ms": now,
         "agent_admission": {"paused_invocation_ids": ["tool"]}}
        for now in (0, 100)
    ]
    assert not any(join_window(
        target, states, {0: {"ready_paused": 0}}, [], {0: 1},
    ).values())


def test_granted_capacity_checks_whole_batch_and_observed_growth_reserve():
    states = [
        {"monotonic_ms": now, "ts_ms": now, "reserve_full_tokens": 3000}
        for now in (0, 50, 100)
    ]
    pools = [{
        "ts_ms": 0, "device_pools": {"full": {"observable": True, "free_units": 3100}},
    }]
    result = granted_free_window(10, 90, 101, states, pools)
    assert result["fresh_pool_coverage_ms"] == 80
    assert result["sampled_free_window_ms"] == 0
    assert result["pool_unknown_ms"] == 0
    result = granted_free_window(10, 90, 100, states, pools)
    assert result["sampled_free_window_ms"] == 80


def test_granted_capacity_keeps_long_gaps_unknown():
    states = [
        {"monotonic_ms": now, "ts_ms": now, "reserve_full_tokens": 0}
        for now in (0, 2000)
    ]
    result = granted_free_window(10, 90, 100, states, [])
    assert result["pool_unknown_ms"] == 80
    assert result["sampled_free_window_ms"] == 0
    assert result["maximum_observed_free_tokens"] is None
