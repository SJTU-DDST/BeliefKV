"""The dispatch funnel counts nonempty partial bursts without crediting reuse."""

from collections import defaultdict

import pytest

from scripts.audit_prefetch_lifecycle import wait_event_identity
from scripts.audit_sealed_wait_prefetch import dispatch_funnel


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
