from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.audit_workflow_idle_supply import gpu_suffix, replay_states, weighted_summary


def observation(when, *, gpu=0., pending=0, ready=0, active=0, paused=0, age=.1):
    return {
        "offset_s": when, "gpu_percent": gpu, "submitted_pending_agents": pending,
        "application_ready_agents": ready, "active_ready_agents": active,
        "ready_paused_agents": paused, "snapshot_age_s": age, "inflight": 2,
        "tool_blocked_agents": 2, "join_blocked_agents": 0,
    }


def test_weighting_clips_window_and_does_not_extrapolate_across_sampling_holes():
    rows = [observation(0.), observation(.2, gpu=100.), observation(2.), observation(2.2)]
    result = weighted_summary(rows, .1, 2.1)
    assert result["covered_sample_s"] == pytest.approx(.2)
    assert result["zero_sample_s"] == pytest.approx(.2)
    assert result["coverage_fraction"] == pytest.approx(.1)


def test_unobserved_native_membership_is_not_counted_as_zero_active_work():
    rows = [
        observation(0., active=4),
        observation(.2, active=None),
        observation(.4),
    ]
    result = weighted_summary(rows, 0., .4)
    assert result["time_weighted_mean"]["active_ready_agents"] == 4
    assert result["native_eligibility_coverage_fraction"] == .5


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({}, "no_client_model_request_pending"),
        ({"pending": 1}, "pending_but_application_blocked"),
        ({"pending": 1, "ready": 1, "age": 2}, "ready_native_eligibility_unknown"),
        ({"pending": 1, "ready": 1, "active": 1}, "client_ready_and_active"),
        ({"pending": 1, "ready": 1, "paused": 1}, "client_ready_and_paused"),
        ({"pending": 1, "ready": 1}, "client_ready_native_membership_unobserved"),
    ],
)
def test_zero_sample_classification_does_not_call_all_ready_work_native_feasible(kwargs, reason):
    result = weighted_summary([observation(0., **kwargs), observation(.2)], 0., .2)
    assert result["zero_sample_attribution_s"] == {reason: .2}


def test_parallel_tools_and_join_are_distinct_from_pending_model_work():
    def event(kind, when, *, invocation="a", **attrs):
        return {
            "kind": kind, "ts_ms": when, "workflow_id": "w",
            "invocation_id": invocation, "join_id": "join", "attributes": attrs,
        }
    events = [
        event("workflow_start", 0),
        event("tool_start", 1, tool_call_id="t1"),
        event("tool_start", 2, tool_call_id="t2"),
        event("tool_end", 3, tool_call_id="t1"),
        event("llm_submit", 4, invocation="b", request_id="r"),
        event("join_wait", 5),
        event("tool_end", 6, tool_call_id="t2"),
        event("llm_result", 7, invocation="b", request_id="r"),
        event("join_satisfied", 8),
    ]
    rows = replay_states(events, [3., 5., 6., 8.])
    assert rows[0]["tool_blocked_agents"] == 1
    assert rows[1]["ready_ids"] == {"b"}
    assert rows[1]["join_blocked_agents"] == 1
    assert rows[2]["tool_blocked_agents"] == 0
    assert rows[-1]["submitted_pending_agents"] == rows[-1]["join_blocked_agents"] == 0


def test_sampler_suffix_has_explicit_clock_zone_and_seals_only_complete_lines(tmp_path):
    path = tmp_path / "gpu.csv"
    path.write_bytes(
        b"timestamp,gpu_utilization_percent\n"
        b"2026/10/31 12:00:00.000, 0\n"
        b"2026/10/31 12:00:00.200, 100\n"
        b"2026/10/31 12:"
    )
    rows, seal = gpu_suffix(path, ZoneInfo("Asia/Shanghai"), 1024)
    expected = datetime(2026, 10, 31, 4, tzinfo=ZoneInfo("UTC")).timestamp() * 1000.
    assert rows[0]["wall_ms"] == expected
    assert rows[1]["gpu_percent"] == 100
    assert seal["complete_bytes"] < path.stat().st_size - seal["start_byte"]


def test_duplicate_serial_submission_is_rejected():
    event = {
        "ts_ms": 0., "kind": "llm_submit", "workflow_id": "w",
        "invocation_id": "a", "attributes": {"request_id": "r"},
    }
    with pytest.raises(ValueError, match="overlapping"):
        replay_states([event, event], [1.])


@pytest.mark.parametrize("kind", ["join_satisfied", "join_timeout"])
def test_join_completion_without_invocation_clears_only_its_workflow_wait(kind):
    events = [
        {"ts_ms": 0., "kind": "join_wait", "workflow_id": workflow,
         "invocation_id": invocation, "join_id": "same-local-id", "attributes": {}}
        for workflow, invocation in (("w1", "a"), ("w2", "b"))
    ]
    events.extend([
        {"ts_ms": 1., "kind": kind, "workflow_id": "w1",
         "invocation_id": None, "join_id": "same-local-id", "attributes": {}},
        {"ts_ms": 2., "kind": "llm_submit", "workflow_id": "w1",
         "invocation_id": "a", "attributes": {"request_id": "r"}},
    ])
    row = replay_states(events, [3.])[0]
    assert row["join_blocked_agents"] == 1
    assert row["ready_ids"] == {"a"}
