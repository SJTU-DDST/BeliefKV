"""The dispatch funnel counts nonempty partial bursts without crediting reuse."""

from collections import defaultdict

import pytest

from scripts.audit_prefetch_lifecycle import wait_event_identity
from scripts.audit_sealed_wait_prefetch import dispatch_funnel, join_dispatch_gaps


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("observations,completion,submitted,partial,ordinary,unsubmitted", (
    (((90., "start_window"), (95., "submitted_partial")), 100., 1, 1, 0, 0),
    (((90., "start_window"), (95., "submitted")), 100., 1, 0, 1, 0),
    (
        ((90., "start_window"), (93., "submitted_partial"),
         (94., "submitted_partial"), (95., "submitted")),
        100., 1, 1, 1, 0,
    ),
    (((90., "start_window"), (100., "submitted_partial")), 100., 0, 0, 0, 1),
    (((90., "start_window"), (101., "submitted_partial")), 100., 0, 0, 0, 1),
    (((90., "start_window"), (95., "submitted_partial")), None, 0, 0, 0, 0),
    (((95., "submitted_partial"),), 100., 1, 1, 0, 0),
    (((90., "start_window"),), 100., 0, 0, 0, 1),
    ((), 100., 0, 0, 0, 0),
))
def test_partial_funnel_preserves_wait_boundaries_and_independent_event_counts(
    source, observations, completion, submitted, partial, ordinary, unsubmitted,
):
    event = {
        "source": source, "workflow_id": "workflow", "invocation_id": "parent",
        "context_id": "context", "context_epoch": 2, "join_id": "join",
        "active_tool_ids": ["tool"],
    }
    identity = wait_event_identity(event)
    assert identity is not None
    plans = defaultdict(list)
    plans[identity] = [
        {**event, "ts_ms": when, "reason": reason, "planned_full_tokens": 8}
        for when, reason in observations
    ]
    sources = dispatch_funnel(
        {identity: {**event, "completion_ts_ms": completion}}, plans,
    )
    counts = sources[source]["event_counts"]
    assert counts["observed_events"] == 1
    assert counts.get("completion_unknown", 0) == int(completion is None)
    assert counts.get("completion_observed", 0) == int(completion is not None)
    assert counts.get("submitted_before_completion", 0) == submitted
    assert counts.get("events_with_submitted_partial_reason_before_completion", 0) == partial
    assert counts.get("events_with_submitted_reason_before_completion", 0) == ordinary
    assert counts.get("start_without_submit_before_completion", 0) == unsubmitted
    assert not any("reused" in name or "bytes" in name for name in counts)
    other = "tool_wait" if source == "join_ticket" else "join_ticket"
    assert not sources[other]["event_counts"]


@pytest.mark.parametrize("submit,expected", (
    (99., "native_submitted_before_completion"),
    (100., "native_submitted_at_or_after_completion"),
    (101., "native_submitted_at_or_after_completion"),
    (None, "native_submission_unobserved"),
    (float("nan"), "native_submission_unobserved"),
    (float("inf"), "native_submission_unobserved"),
    (True, "native_submission_unobserved"),
))
@pytest.mark.parametrize("capacity_blocked", (False, True))
def test_join_gap_uses_native_submit_even_when_plan_record_crosses_completion(
    submit, expected, capacity_blocked,
):
    wait = {
        "source": "join_ticket", "workflow_id": "workflow",
        "context_id": "parent", "context_epoch": 2, "join_id": "join",
        "completion_ts_ms": 100.,
    }
    identity = wait_event_identity(wait)
    plans = {identity: [
        {**wait, "ts_ms": 90., "reason": "start_window"},
        {**wait, "ts_ms": 105., "reason": "submitted"},
    ]}
    if capacity_blocked:
        plans[identity].append({
            **wait, "ts_ms": 95., "reason": "no_restore_plan",
            "no_step_reason": "prefetch_capacity_budget",
        })
    actions = [
        {**wait, "context_epoch": 3, "command_id": "native", "submit_ts_ms": submit},
        {**wait, "join_id": "other-join", "command_id": "other", "submit_ts_ms": 98.},
        {**wait, "workflow_id": "other-workflow", "command_id": "foreign", "submit_ts_ms": 98.},
    ]
    result = join_dispatch_gaps({identity: wait}, plans, actions)
    assert result["event_counts"] == {expected: 1}
    assert result["events_with_pre_completion_capacity_budget"] == int(capacity_blocked)
    [row] = result["rows"]
    assert row["associated_command_ids"] == ["native"]
    assert row["first_start_relative_to_completion_ms"] == -10.
    assert row["first_submitted_record_relative_to_completion_ms"] == 5.
    valid = type(submit) in (int, float) and submit in (99., 100., 101.)
    assert row["first_native_submit_relative_to_completion_ms"] == (
        submit - 100. if valid else None
    )
    assert row["commands_without_native_submit_time"] == int(not valid)
    assert row["early_started_and_reused_full_bytes"] is None
    assert row["remaining_native_full_host_hit_bytes"] is None


@pytest.mark.parametrize("source,completion,observations", (
    ("tool_wait", 100., ((90., "start_window"),)),
    ("join_ticket", None, ((90., "start_window"),)),
    ("join_ticket", 100., ((90., "start_window"), (95., "submitted"))),
    ("join_ticket", 100., ((90., "start_window"), (95., "submitted_partial"))),
    ("join_ticket", 100., ((100., "start_window"),)),
    ("join_ticket", 100., ()),
))
def test_join_gap_keeps_non_gap_and_unknown_waits_out_of_its_population(
    source, completion, observations,
):
    wait = {
        "source": source, "workflow_id": "workflow", "context_id": "parent",
        "context_epoch": 2, "join_id": "join", "completion_ts_ms": completion,
    }
    identity = wait_event_identity(wait)
    plans = {identity: [
        {**wait, "ts_ms": when, "reason": reason} for when, reason in observations
    ]}
    result = join_dispatch_gaps({identity: wait}, plans, [])
    assert not result["event_counts"]
    assert not result["rows"]
