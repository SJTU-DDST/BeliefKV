from copy import deepcopy

import pytest

from scripts.audit_agent_restore_churn import inspect


def native():
    def row(kind, request, when, host=0):
        return {
            "kind": kind, "ts_ms": when, "workflow_id": "w", "invocation_id": "i",
            "context_id": "c", "context_epoch": 0,
            "attributes": {
                "request_id": request, "cached_tokens_host": host,
                "native_host_hit_match_path": {"observed_ts_ms": 2000.},
            },
        }
    return [
        row("llm_submit", "previous", 0.), row("llm_result", "previous", 500.),
        row("llm_submit", "current", 1000., 3), row("llm_result", "current", 2500.),
    ]


def transfer():
    return {
        "status": "completed", "direction": "h2d", "actual_bytes": 500,
        "tagged_child_commits": [{"command_id": "controlled", "num_bytes": 200}],
    }


def state(when, active):
    return {
        "ts_ms": when, "agent_admission": {
            "active_invocation_ids": active, "logical_active_tokens": 50, "capacity_tokens": 100,
        },
    }


@pytest.mark.parametrize("duration,bucket", ((200., "at_most_200ms"), (201., "over_200ms"), (None, "unknown")))
def test_native_and_controlled_bytes_reconcile_without_double_counting_tool_duration(duration, bucket):
    result = inspect(native(), [transfer()], {
        "current": {"kind": "tool", "tool_round_duration_ms": duration},
    }, bytes_per_token=100, states=[state(0., ["i"]), state(1000., ["i"])])
    assert result["native_demand_h2d_bytes"] == result["request_host_hit_bytes"] == 300
    assert result["transfers"]["h2d_tagged_bytes"] == 200
    assert result["by_request_kind"]["tool"]["host_hit_bytes"] == 300
    assert result["tool_host_hits_by_tool_duration"][bucket] == {"requests": 1, "host_hit_bytes": 300}
    assert result["host_hit_requests"][0]["eligibility_cohort"] == "all_sampled_active"


@pytest.mark.parametrize("fault", ("mismatched_bytes", "bad_tag", "negative_tag", "not_complete", "duplicate_request"))
def test_inconsistent_transfer_or_native_request_work_is_rejected(fault):
    events, operation = native(), transfer()
    if fault == "mismatched_bytes":
        operation["actual_bytes"] = 499
    elif fault == "bad_tag":
        operation["tagged_child_commits"][0]["num_bytes"] = 501
    elif fault == "negative_tag":
        operation["tagged_child_commits"][0]["num_bytes"] = -1
    elif fault == "not_complete":
        operation["status"] = "pending"
    else:
        events.append(deepcopy(events[-2]))
    with pytest.raises(ValueError, match="native|transfer"):
        inspect(events, [operation], {}, bytes_per_token=100)


@pytest.mark.parametrize("states,expected", (
    ([], "unobserved_eligibility_or_first_service"),
    ([state(0., ["i"])], "active_or_unknown"),
    ([state(0., ["i"]), state(1000., [])], "observed_inactive"),
))
def test_missing_or_stale_samples_do_not_prove_continuous_eligibility(states, expected):
    result = inspect(native(), [transfer()], {}, bytes_per_token=100, states=states)
    assert result["host_hit_requests"][0]["eligibility_cohort"] == expected


def test_first_model_request_does_not_inherit_another_agents_service_history():
    events = native()
    for event in events[2:]:
        event["invocation_id"] = "child"
    result = inspect(events, [transfer()], {}, bytes_per_token=100)
    assert result["host_hit_requests"][0]["eligibility_cohort"] == "no_prior_same_invocation"


def test_reference_program_counts_do_not_establish_eligibility_or_logical_overcommit():
    snapshots = [{
        "ts_ms": when, "agent_admission": {
            "strategy": "thunderagent_reference", "capacity_tokens": 100,
            "active_programs": 1, "pending_requests": 1,
        },
    } for when in (0., 1000.)]
    result = inspect(native(), [transfer()], {}, bytes_per_token=100, states=snapshots)
    row = result["host_hit_requests"][0]
    assert row["eligibility_cohort"] == "active_or_unknown"
    assert row["eligibility_partition"] == {
        "active_ms": 0., "inactive_ms": 0., "unknown_ms": 1500.,
    }
    assert row["queue_snapshot_logical_overcommit_tokens"] is None
    assert result["native_demand_bytes_reconciled"]
    assert result["request_host_hit_bytes"] == 300
